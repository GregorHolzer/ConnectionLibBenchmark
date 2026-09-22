#!/usr/bin/env python3
"""Cross-technology comparison figures.

    ./plot_comparison.py --results results/campaign-20260921-083956

`plot_results.py` asks what each radio's knobs do. This asks the question RQ2 asks - how the three
compare against each other - on the metric set RQ1 commits to, with each technology represented by
its *best* configuration rather than an arbitrary baseline.

Two rules the figures never break:

* **No normalised, relative or ratio axes.** Every panel carries one metric in its own real unit.
  Metrics with different units get different panels rather than a shared 0-1 scale, because a
  normalised axis silently encodes whichever technology happened to define the maximum.
* **Linear axes start at zero; log axes start at a decade.** A bar chart whose axis starts at 95
  makes a 0.3 % difference look like a rout.

Written for print, light mode only, PNG and vector PDF, like the rest of the suite.
"""

import argparse
import math
import sys
import textwrap
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")   # headless
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.comparison import (aggregate, board_floor_ua, flat_factors, select_best,
                               stack_pairs, standing_currents)
from bench.results import load_meta, load_node_rx, load_points, load_raw
# The palette, the axis styling and the always-visible median marker are shared with the
# per-technology figures on purpose: the two scripts must produce one visual system, and a
# technology must keep its colour across every figure in the thesis.
from plot_results import COLOR, INK, INK_2, LABEL, SURFACE, TECH_ORDER, lighten, save, style_axes

REPO_ROOT = Path(__file__).resolve().parent

# Stated on every figure. This campaign is two boards on a bench; RQ2 asks about a forest. These
# figures answer RQ1 and fix the reference values a field campaign gets compared against.
BENCH_CAVEAT = ("Bench measurement, two boards at desk range - not a forest result; "
                "RQ2's environment question needs the field campaign")

VOLTAGE_V = 5.0          # the PPK2 source voltage the campaign ran at
SECONDS_PER_DAY = 86400


# ------------------------------------------------------------------------------------------
# Axis discipline
# ------------------------------------------------------------------------------------------

def apply_floor(ax, values, log=False, headroom=1.30):
    """Force the axis to start at zero, or at a decade when the data spans orders of magnitude.

    One helper for every panel so the rule cannot drift between them. Matplotlib's autoscaling
    would happily start a bar axis at 95 mA and turn a 0.3 % difference into a visual landslide.
    """
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return
    if log:
        positive = [v for v in finite if v > 0]
        if not positive:
            return
        ax.set_yscale("log")
        low = 10 ** math.floor(math.log10(min(positive)))
        high = 10 ** math.ceil(math.log10(max(positive)))
        ax.set_ylim(low, high)
    else:
        ax.set_ylim(0, max(finite) * headroom)


def bars(ax, techs, values, *, log=False, fmt="{:.1f}", unit="", headroom=1.30):
    """One bar per technology with its value printed inside it.

    Inside, not above: two technologies landing within a percent of each other - which happens on
    delivery ratio and on framing efficiency - put their labels on top of one another otherwise.
    """
    drawn = [(i, t, v) for i, (t, v) in enumerate(zip(techs, values))
             if v is not None and math.isfinite(v)]
    for i, tech, value in drawn:
        ax.bar(i, value, color=COLOR[tech], width=0.58, zorder=3)
    apply_floor(ax, [v for _, _, v in drawn], log=log, headroom=headroom)

    for i, tech, value in drawn:
        ax.text(i, value, fmt.format(value) + unit, ha="center", va="top", color=SURFACE,
                 fontsize=8.5, fontweight="bold", zorder=6,
                 bbox=dict(boxstyle="round,pad=0.22", facecolor=COLOR[tech], edgecolor="none"))
    # A technology with no value for this metric is named and left blank rather than dropped, so
    # the reader sees that the metric does not exist for it instead of silently missing a bar.
    for i, (tech, value) in enumerate(zip(techs, values)):
        if value is None or not math.isfinite(value):
            ax.text(i, 0, "  n/a  ", ha="center", va="bottom", color=INK_2, fontsize=8.5,
                     style="italic")
    ax.set_xticks(range(len(techs)))
    ax.set_xticklabels([LABEL[t] for t in techs], fontsize=9)


def panel_note(ax, text, width=52):
    """A caveat pinned under the panel, for metrics that mislead without one.

    Wrapped here rather than by hand-placed newlines in the caller: a note written to look right
    at one figure width runs into the neighbouring panel at any other, and these panels sit four
    to a row with very little gutter between them.
    """
    ax.text(0.0, -0.30, textwrap.fill(" ".join(text.split()), width), transform=ax.transAxes,
             color=INK_2, fontsize=7.4, va="top", ha="left", linespacing=1.35, style="italic")


# ------------------------------------------------------------------------------------------
# Figure A - the headline comparison
# ------------------------------------------------------------------------------------------

def plot_best_config(best, standing, floor_ua, out_dir, *, best_by, pdr_floor):
    """One panel per metric, all three technologies on each panel's own real-unit axis."""
    techs = [t for t in TECH_ORDER if t in best]
    if not techs:
        return
    get = lambda field: [best[t].get(field) for t in techs]   # noqa: E731

    fig, axes = plt.subplots(2, 4, figsize=(16.0, 9.0))
    fig.patch.set_facecolor(SURFACE)
    flat = axes.flatten()

    # -- energy, the binding requirement -----------------------------------------------------
    bars(flat[0], techs, get("energy_per_delivered_mj"), fmt="{:.0f}", unit=" mJ")
    style_axes(flat[0], "", "mJ per delivered payload", "Energy per delivered payload")
    panel_note(flat[0], "Absolute, at the 5 V header tap upstream of the AXP2101: the ESP32, the "
                        "PMU conversion loss and the USB bridge sit under all three alike. The "
                        "deltas are the trustworthy quantity, not the absolute height.")

    bars(flat[1], techs, get("radio_energy_per_delivered_mj"), fmt="{:.1f}", unit=" mJ")
    style_axes(flat[1], "", "mJ per delivered payload", "Radio's own share of that energy")
    panel_note(flat[1], f"Board floor ({floor_ua / 1000:.1f} mA, cold boot) subtracted over each\n"
                        "measurement window. This is what the radio itself costs to move\n"
                        "one payload, with the board's constant draw removed.")

    # -- standing current, which dominates a low-rate periodic deployment ---------------------
    standing_ma = [(standing.get(t, {}).get("above_floor_ua") or 0) / 1000 for t in techs]
    bars(flat[2], techs, standing_ma, fmt="{:.1f}", unit=" mA", headroom=1.45)
    for i, tech in enumerate(techs):
        for value in standing.get(tech, {}).get("per_rotation_ua", []):
            flat[2].plot(i, (value - floor_ua) / 1000, marker="_", markersize=16,
                          color=INK_2, alpha=0.75, zorder=5)
    style_axes(flat[2], "", "mA above board floor", "Standing current, radio idle")
    panel_note(flat[2], "What the radio costs while merely able to receive. Each tick is one "
                        "repetition. ESP-NOW's ticks spread 11 mA because the idle reference "
                        "is contaminated after rotation 0 - see the floor note in the report.")

    # -- delivery and latency ------------------------------------------------------------------
    bars(flat[3], techs, [(v or 0) * 100 for v in get("pdr")], fmt="{:.1f}", unit=" %",
          headroom=1.18)
    flat[3].set_ylim(0, 100)
    style_axes(flat[3], "", "delivered / sent (%)", "Delivery ratio, counted at the receiver")
    panel_note(flat[3], "Distinct sequence numbers arriving at the node, over attempts. A "
                        "sender-side figure would mean a different thing on each radio.")

    rtt = [(best[t].get("rtt_ms") or {}).get("median") for t in techs]
    bars(flat[4], techs, rtt, log=True, fmt="{:.1f}", unit=" ms")
    style_axes(flat[4], "", "median round-trip latency (ms)", "Round-trip latency")
    panel_note(flat[4], "Log axis, floored at a decade: the three span 2.9 ms to 700 ms. Round "
                        "trip, not one-way - one-way delay needs clock synchronisation this "
                        "bench does not have, so it is not reported at all.")

    # -- goodput -------------------------------------------------------------------------------
    bars(flat[5], techs, get("goodput_bps"), fmt="{:.0f}", unit=" bit/s")
    style_axes(flat[5], "", "delivered payload bits / s", "Goodput")
    panel_note(flat[5], "Over the whole configuration point against wall clock, never derived "
                        "from latency. FORTE's traffic is low-rate periodic, so this matters "
                        "less here than energy does.")

    # -- framing efficiency, the one that lies without its caveat --------------------------------
    bars(flat[6], techs, [(v or 0) * 100 for v in get("framing_efficiency")], fmt="{:.1f}",
          unit=" %", headroom=1.18)
    flat[6].set_ylim(0, 100)
    style_axes(flat[6], "", "payload / total bytes (%)", "Library framing efficiency")
    panel_note(flat[6], "ConnectionLib's own framing only. LoRa pays 6 bytes because raw SX1278 "
                        "has no addressing; ESP-NOW and Zigbee address natively and so read a "
                        "FABRICATED 100 %. The 802.11 / 802.15.4 framing beneath is not "
                        "instrumented on any backend. Not an on-air efficiency.")

    # -- the regulatory ceiling ------------------------------------------------------------------
    caps = get("max_msgs_per_hour")
    bars(flat[7], techs, caps, log=True, fmt="{:.0f}", unit=" /h")
    style_axes(flat[7], "", "max messages per hour", "Ceiling imposed by regulation")
    for i, (tech, cap) in enumerate(zip(techs, caps)):
        if cap is None:
            # Axes fraction, not data coordinates: on a log axis there is no sensible data y for
            # "no limit", and anchoring to the top of the range collides with the decade label.
            flat[7].text((i + 0.5) / len(techs), 0.5, "no duty-cycle\nlimit\nin band",
                          transform=flat[7].transAxes, ha="center", va="center", color=INK_2,
                          fontsize=8.5, linespacing=1.4, style="italic")
    panel_note(flat[7], "LoRa at 433 MHz is capped at 10 % duty cycle by ETSI Annex 1; 2.4 GHz "
                        "carries no such limit. A radio that may not legally transmit as often "
                        "IS worse for a periodic application - reported, not corrected away.")

    rule = {"energy": "lowest energy per delivered payload", "latency": "lowest median latency",
            "pdr": "highest delivery ratio", "baseline": "documented baseline"}[best_by]
    chosen = ", ".join(f"{LABEL[t]}: {best[t]['point_key'].split('/', 1)[1] or 'baseline'}"
                        for t in techs)
    fig.suptitle("Best configuration of each technology, compared", color=INK, fontsize=14,
                  x=0.012, ha="left", y=1.005)
    fig.text(0.012, 0.975, f"selected by {rule}"
                            + (f", among configurations delivering ≥ {pdr_floor:.0%}"
                               if best_by != "baseline" else "")
                            + f" — {chosen}\n{BENCH_CAVEAT}",
              color=INK_2, fontsize=9, va="top", ha="left", linespacing=1.5)

    fig.subplots_adjust(hspace=0.78, wspace=0.30, top=0.88)
    save(fig, out_dir, "comparison-best-config")


# ------------------------------------------------------------------------------------------
# Figure B - the one factor all three sweep
# ------------------------------------------------------------------------------------------

def plot_payload_sweep(records, out_dir):
    """Payload is the only hyperparameter all three technologies expose, so it is the only axis
    along which they can be drawn as lines on one shared scale without inventing a common x."""
    series = defaultdict(dict)
    for record in records:
        if record["factor"] not in ("payload", "baseline"):
            continue
        payload = record.get("payload_bytes")
        if payload is None:
            continue
        series[record["tech"]][int(payload)] = record

    techs = [t for t in TECH_ORDER if series.get(t)]
    if not techs:
        return

    panels = [
        ("rtt", "median round-trip latency (ms)", "Latency against payload", True,
         lambda r: (r.get("rtt_ms") or {}).get("median")),
        ("energy", "mJ per delivered payload", "Energy against payload", False,
         lambda r: r.get("energy_per_delivered_mj")),
        ("goodput", "delivered payload bits / s", "Goodput against payload", False,
         lambda r: r.get("goodput_bps")),
    ]

    # Categorical x, not the byte count itself: the sizes roughly double, so a linear axis crushes
    # 8, 16 and 32 into one another and spends half the panel on the gap between 84 and 250. Even
    # spacing also avoids implying that the lines interpolate between measured sizes.
    sizes = sorted({size for tech in techs for size in series[tech]})
    slot = {size: i for i, size in enumerate(sizes)}

    fig, axes = plt.subplots(1, 3, figsize=(15.0, 4.6))
    fig.patch.set_facecolor(SURFACE)

    for ax, (_, ylabel, title, log, extract) in zip(axes, panels):
        seen = []
        for tech in techs:
            pairs = [(slot[size], extract(series[tech][size])) for size in sorted(series[tech])]
            pairs = [(x, v) for x, v in pairs if v is not None]
            if not pairs:
                continue
            xs, ys = zip(*pairs)
            seen.extend(ys)
            ax.plot(xs, ys, marker="o", markersize=5, linewidth=1.8, color=COLOR[tech],
                     markeredgecolor=SURFACE, markeredgewidth=1.0, label=LABEL[tech], zorder=4)
        apply_floor(ax, seen, log=log)
        style_axes(ax, "payload (bytes)", ylabel, title)
        ax.set_xticks(list(slot.values()))
        ax.set_xticklabels([str(size) for size in sizes], fontsize=9)
        ax.set_xlim(-0.35, len(sizes) - 0.65)
        ax.legend(frameon=False, labelcolor=INK_2, fontsize=8.5, loc="best")

    fig.suptitle("Payload sweep, all three technologies on one axis", color=INK, fontsize=13,
                  x=0.008, ha="left", y=1.04)
    fig.text(0.008, 1.0, "payload is the only factor all three expose; real units throughout, "
                          f"no normalisation. {BENCH_CAVEAT}",
              color=INK_2, fontsize=8.5, va="top", ha="left")
    fig.subplots_adjust(wspace=0.28)
    save(fig, out_dir, "comparison-payload-sweep")


# ------------------------------------------------------------------------------------------
# Figure C - the FORTE question
# ------------------------------------------------------------------------------------------

def plot_energy_budget(best, standing, floor_ua, out_dir, interval_s):
    """Standing cost against per-message cost, at a realistic reporting interval.

    This is the panel that answers the deployment question rather than the benchmark one. FORTE's
    satellites report periodically and must last months, so the standing draw applies continuously
    while the per-message cost applies only during the seconds the node is actually transmitting -
    and a radio can lose outright on the former no matter how cheap its packets are.

    Two panels rather than one stacked bar. At a 15-minute interval the two terms differ by four
    orders of magnitude, and a stack cannot show a segment that is 0.01 % of its own bar: the
    message term would read as zero for all three radios, which is exactly the wrong conclusion to
    invite from a figure whose subject is per-message efficiency. Each term therefore gets its own
    axis, both linear from zero, and the ratio between them is stated in words instead of being
    left to a reader measuring pixels.
    """
    techs = [t for t in TECH_ORDER if t in best]
    if not techs:
        return
    messages_per_day = SECONDS_PER_DAY / interval_s

    standing_mah, message_mah = [], []
    for tech in techs:
        above_ua = standing.get(tech, {}).get("above_floor_ua") or 0.0
        standing_mah.append(above_ua / 1000.0 * 24.0)               # mA held for 24 h
        per_message_mj = best[tech].get("radio_energy_per_delivered_mj") or 0.0
        # mJ -> mC at the source voltage, mC -> mAh. Charge is what a battery is rated in, so the
        # per-message and the standing term only become addable once both are in mAh.
        message_mah.append(per_message_mj / VOLTAGE_V / 3600.0 * messages_per_day)

    totals = [s + m for s, m in zip(standing_mah, message_mah)]
    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.4))
    fig.patch.set_facecolor(SURFACE)

    bars(axes[0], techs, totals, fmt="{:.0f}", unit=" mAh", headroom=1.30)
    style_axes(axes[0], "", "charge per day (mAh, radio only)",
                "Total daily cost of keeping the radio up")
    panel_note(axes[0], "Standing draw held for 24 h plus every message sent in that time. "
                        "ESP-NOW's bar carries a 16 % uncertainty: its idle reference is "
                        "contaminated after rotation 0.", width=62)

    bars(axes[1], techs, message_mah, fmt="{:.3f}", unit=" mAh", headroom=1.30)
    style_axes(axes[1], "", "charge per day (mAh)",
                f"Of which the {messages_per_day:.0f} messages cost")
    shares = [100 * m / t if t else 0 for m, t in zip(message_mah, totals)]
    panel_note(axes[1], "Its own axis, because this term is "
                        + " / ".join(f"{s:.3f} %" for s in shares)
                        + " of the bar on the left. At this interval the radio's energy is "
                          "almost entirely the cost of being switched on, not the cost of "
                          "transmitting - which is why standing current, not per-packet "
                          "efficiency, decides battery life here.", width=62)

    fig.suptitle("Daily energy budget of the radio, at a realistic reporting interval",
                  color=INK, fontsize=13, x=0.008, ha="left", y=1.06)
    fig.text(0.008, 1.015,
              f"one message every {interval_s / 60:.0f} min ({messages_per_day:.0f} per day), "
              f"best configuration of each technology, board floor excluded. {BENCH_CAVEAT}",
              color=INK_2, fontsize=8.5, va="top", ha="left")
    fig.subplots_adjust(wspace=0.26)
    save(fig, out_dir, "comparison-energy-budget")


# ------------------------------------------------------------------------------------------
# Figure D - the library's own cost
# ------------------------------------------------------------------------------------------

def plot_stack_overhead(records, out_dir):
    """What ConnectionLib charges, measured rather than assumed.

    The exposé asks for results that keep what the library contributes separable from what the
    radios contribute. Both arms run the same measurement loop with the same timing points, so
    every difference here is the library - on LoRa, chiefly the 6-byte header it must add because
    a raw SX1278 has no addressing of its own.

    Plotted as two absolute series per panel rather than as a percentage. A ratio would hide which
    of the two moved, and at 8 bytes against 84 the same percentage means very different numbers of
    milliseconds and millijoules.
    """
    pairs = stack_pairs(records)
    techs = [t for t in TECH_ORDER if pairs.get(t)]
    if not techs:
        return

    panels = [
        ("median round-trip latency (ms)", "Latency", False,
         lambda r: (r.get("rtt_ms") or {}).get("median")),
        ("mJ per delivered payload", "Energy per delivery", False,
         lambda r: r.get("energy_per_delivered_mj")),
        ("bytes on air per message", "Bytes on air", False,
         lambda r: (r.get("payload_bytes") or 0) / (r.get("framing_efficiency") or 1)),
    ]

    fig, axes = plt.subplots(len(techs), 3, figsize=(14.0, 3.7 * len(techs)), squeeze=False)
    fig.patch.set_facecolor(SURFACE)

    for row, tech in enumerate(techs):
        sizes = [size for size, _, _ in pairs[tech]]
        slots = list(range(len(sizes)))
        for column, (ylabel, title, log, extract) in enumerate(panels):
            ax = axes[row][column]
            lib = [extract(library) for _, library, _ in pairs[tech]]
            raw = [extract(rawrec) for _, _, rawrec in pairs[tech]]
            seen = [v for v in lib + raw if v is not None]
            width = 0.38
            ax.bar([i - width / 2 for i in slots], [v or 0 for v in lib], width=width,
                    color=COLOR[tech], label="ConnectionLib", zorder=3)
            ax.bar([i + width / 2 for i in slots], [v or 0 for v in raw], width=width,
                    color=lighten(COLOR[tech], 0.55), label="raw API", zorder=3)
            apply_floor(ax, seen, log=log)
            style_axes(ax, "payload (bytes)", ylabel, f"{LABEL[tech]} \u2014 {title}")
            ax.set_xticks(slots)
            ax.set_xticklabels([str(size) for size in sizes], fontsize=9)
            if row == 0 and column == 0:
                ax.legend(frameon=False, labelcolor=INK_2, fontsize=8.5, loc="upper left")

    fig.suptitle("What the library costs: ConnectionLib against the raw radio API",
                  color=INK, fontsize=13, x=0.008, ha="left", y=1.02)
    fig.text(0.008, 1.0, textwrap.fill(
                  "Same measurement loop, same timing points, same records - only send() and "
                  "poll() differ. Duty-cycle enforcement is kept in both arms (it is regulation, "
                  "not a library feature), so this is framing, addressing and queueing. "
                  f"{BENCH_CAVEAT}", 118),
              color=INK_2, fontsize=8.5, va="top", ha="left", linespacing=1.5)
    fig.subplots_adjust(hspace=0.75, wspace=0.28)
    save(fig, out_dir, "comparison-stack-overhead")


# ------------------------------------------------------------------------------------------
# Figure E - observed conditions, not a comparison axis
# ------------------------------------------------------------------------------------------

def plot_signal_quality(records, out_dir):
    """RSSI and SNR, presented as observed metrics.

    Deliberately not a ranking. `baccour2012` gives the reasons and they are on the figure: these
    are read only from packets that *arrived*, so a link losing most of its traffic reports the
    quality of its survivors and flatters itself exactly where it is worst; the good/bad threshold
    is a property of the environment rather than the radio; and neither quantity is available on
    all three radios, so neither can carry a comparison.
    """
    # A "median" of one or two samples is not a median, and on the Zigbee path a stray row is as
    # likely to be a mis-parsed API frame as a measurement (see records.py). Require a real sample.
    def series(record, field):
        summary = record.get(field) or {}
        return summary if summary.get("n", 0) >= 5 else None

    have = [r for r in records if series(r, "node_rssi_dbm") or series(r, "node_snr_db")]
    if not have:
        return
    # Every technology measured stays on the axis, including the ones that report nothing. Which
    # radios expose signal quality and which do not is the RQ1 availability result; dropping the
    # silent ones would turn "this radio cannot tell you" into "this radio was not tested".
    techs = [t for t in TECH_ORDER if any(r["tech"] == t for r in records)]

    fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.6))
    fig.patch.set_facecolor(SURFACE)

    for ax, (field, label, unit) in zip(axes, [("node_rssi_dbm", "RSSI at the node", "dBm"),
                                                ("node_snr_db", "SNR at the node", "dB")]):
        drawn = []
        for i, tech in enumerate(techs):
            values = [series(r, field)["median"] for r in have
                       if r["tech"] == tech and series(r, field)]
            if not values:
                continue
            ax.scatter([i] * len(values), values, s=26, color=COLOR[tech], alpha=0.75,
                        edgecolors=SURFACE, linewidths=0.7, zorder=4)
            drawn.extend(values)
        ax.set_xticks(range(len(techs)))
        ax.set_xticklabels([LABEL[t] for t in techs], fontsize=9)
        ax.set_xlim(-0.6, len(techs) - 0.4)
        # No zero floor here: these are negative dBm and a decibel axis has no meaningful origin,
        # so the rule that linear axes start at zero would make the panel unreadable rather than
        # honest. The axis is left to the data and labelled in absolute units.
        if drawn:
            span = max(drawn) - min(drawn) or 1.0
            ax.set_ylim(min(drawn) - span * 0.2, max(drawn) + span * 0.2)
        style_axes(ax, "", f"{label} ({unit})", label)
        for i, tech in enumerate(techs):
            if not any(r["tech"] == tech and series(r, field) for r in have):
                ax.text(i, ax.get_ylim()[0], " not exposed ", ha="center", va="bottom",
                         color=INK_2, fontsize=8.5, style="italic")

    fig.suptitle("Signal quality \u2014 observed conditions, not a comparison axis",
                  color=INK, fontsize=13, x=0.008, ha="left", y=1.04)
    fig.text(0.008, 1.005, textwrap.fill(
                  "One point per configuration. Read only from packets that arrived, so a failing "
                  "link reports the quality of its survivors; the good/bad threshold is a property "
                  f"of the environment, not the radio. {BENCH_CAVEAT}", 118),
              color=INK_2, fontsize=8.5, va="top", ha="left", linespacing=1.5)
    fig.subplots_adjust(wspace=0.26, top=0.80)
    save(fig, out_dir, "comparison-signal-quality")


# ------------------------------------------------------------------------------------------

def report(records, best, rejected, standing, floor_ua, best_by, pdr_floor, show_flat):
    """The numbers behind the figures, on stdout - a figure nobody can check is not a result."""
    print(f"\nboard floor (cold boot): {floor_ua / 1000:.2f} mA")
    print(f"selection: best-by={best_by}, pdr-floor={pdr_floor:.0%}\n")
    for tech in TECH_ORDER:
        if tech not in best:
            continue
        r = best[tech]
        print(f"  {LABEL[tech]:8s} {r['point_key']}")
        print(f"      energy {r.get('energy_per_delivered_mj', float('nan')):8.1f} mJ/delivered"
              f"   radio share {r.get('radio_energy_per_delivered_mj', float('nan')):7.1f} mJ")
        print(f"      rtt    {(r['rtt_ms'] or {}).get('median', float('nan')):8.2f} ms median"
              f"        p95 {(r['rtt_ms'] or {}).get('p95', float('nan')):8.2f} ms")
        print(f"      pdr    {(r.get('pdr') or 0) * 100:8.2f} %"
              f"              goodput {r.get('goodput_bps') or 0:8.1f} bit/s")
        stand = standing.get(tech, {})
        print(f"      standing {(stand.get('above_floor_ua') or 0) / 1000:6.2f} mA above floor"
              f"   (spread {stand.get('spread_ua', 0) / 1000:.2f} mA over "
              f"{stand.get('repetitions', 0)} repetitions)")
        if rejected.get(tech):
            print(f"      rejected by the delivery gate: "
                  f"{', '.join(x['point_key'].split('/', 1)[1] for x in rejected[tech])}")
        print()

    if show_flat:
        print("factor sweeps, by how much the extreme levels move median latency:")
        for tech, factor, ratio, is_flat in flat_factors(records):
            print(f"  {tech:8s} {factor:10s} {ratio:7.2f}x"
                  f"{'   <- flat, the knob does nothing' if is_flat else ''}")
        print()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True)
    parser.add_argument("--best-by", default="energy",
                        choices=["energy", "latency", "pdr", "baseline"],
                        help="how to pick each technology's best configuration")
    parser.add_argument("--pdr-floor", type=float, default=0.95,
                        help="delivery ratio a configuration must reach to be eligible")
    parser.add_argument("--floor-mode", default="cold", choices=["cold", "per-rotation"],
                        help="'cold' uses rotation 0's board floor throughout; see bench/comparison.py")
    parser.add_argument("--interval-s", type=float, default=900,
                        help="reporting interval for the daily energy budget")
    parser.add_argument("--report-flat", action="store_true",
                        help="also print which factor sweeps move nothing")
    args = parser.parse_args()

    results_dir = Path(args.results)
    if not results_dir.is_absolute():
        results_dir = REPO_ROOT / results_dir
    points = load_points(results_dir)
    raw = load_raw(results_dir)
    node_rx = load_node_rx(results_dir)
    load_meta(results_dir)
    if not raw or not points:
        sys.exit(f"no campaign data in {results_dir}")

    floor = board_floor_ua(points, args.floor_mode)
    if not floor:
        sys.exit("no idle floor point in this campaign - energy comparison needs one")
    floor_ua = floor[min(floor)]
    standing = standing_currents(points, floor)
    records = aggregate(points, raw, node_rx, floor_mode=args.floor_mode)
    best, rejected = select_best(records, by=args.best_by, pdr_floor=args.pdr_floor)
    if not best:
        sys.exit(f"no configuration reached a delivery ratio of {args.pdr_floor:.0%}")

    out_dir = results_dir / "plots"
    print(f"writing comparison figures to {out_dir}")
    plot_best_config(best, standing, floor_ua, out_dir,
                     best_by=args.best_by, pdr_floor=args.pdr_floor)
    plot_payload_sweep(records, out_dir)
    plot_energy_budget(best, standing, floor_ua, out_dir, args.interval_s)
    plot_stack_overhead(records, out_dir)
    plot_signal_quality(records, out_dir)
    report(records, best, rejected, standing, floor_ua,
           args.best_by, args.pdr_floor, args.report_flat)


if __name__ == "__main__":
    main()
