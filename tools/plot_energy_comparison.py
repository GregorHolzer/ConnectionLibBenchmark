#!/usr/bin/env python3
"""Compare energy across technologies for a set of single-technology runs.

    ./tools/energy_check.py --json results/idle.json ...        # measure the baselines
    ./tools/plot_energy_comparison.py --idle results/idle.json results/espnow-* results/lora-*

`plot_results.py` covers this for a *campaign*, where every rotation leads with idle points and
`idle_baselines()` can separate what the radio costs from what the ESP32 costs. A `run_benchmark.py
--quick` run has no idle points, so the baselines have to come from `energy_check --json` instead.

**Absolute energy per delivered message is not comparable across technologies**, and that is the
whole reason this script prefers the above-idle figure. The measured window is radio-active ~3 % of
the time for ESP-NOW (a 3 ms exchange inside 100 ms of spacing) but ~99 % for LoRa, whose airtime
fills it. Dividing the absolute window energy by the message count therefore charges ESP-NOW almost
entirely for standing current and LoRa almost entirely for airtime - two different quantities
wearing the same unit. Both panels are drawn so the disagreement is visible rather than hidden.

Points whose mean current falls *below* their technology's idle are dropped: a radio cannot
transmit while drawing less than it does sitting still, so such a window contains time with the
radio not yet up. On this bench the first ESP-NOW point of every run does exactly that.

The point after it is still converging without going below idle, so a second, weaker test hatches
any point drawing more than SETTLE_TOLERANCE below its technology's median. Both tests are
positional artifacts of a radio warming up at the start of a run - they are reproducible, so
treating them as payload or spreading-factor effects would be a mistake.
"""

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# bench.plotting selects the headless Agg backend, so it has to be imported before pyplot.
from bench.plotting import COLOR, GRID, INK, INK_2, LABEL, SURFACE, TECH_ORDER, save, style_axes

import matplotlib.pyplot as plt

# A settled point sits within this fraction of its technology's median draw. 5 % clears the
# spread actually seen within a settled technology (Zigbee 0.5 %, LoRa 3.1 %) while catching the
# ESP-NOW point that is still coming up (11.9 % low).
SETTLE_TOLERANCE = 0.05


def load_points(dirs, idle_ma):
    """Measured points with an energy record, split into usable and warm-up-contaminated."""
    good, dropped = [], []
    for d in dirs:
        path = Path(d) / "points.jsonl"
        if not path.exists():
            print(f"  skipping {d}: no points.jsonl")
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            point = json.loads(line)
            energy = point.get("energy")
            delivered = (point.get("summary") or {}).get("successes") or 0
            if not energy or not delivered:
                continue
            idle = idle_ma.get(point["tech"])
            if idle is None:
                sys.exit(f"no idle baseline for {point['tech']} — run energy_check --json first")
            mean_ma = energy["mean_ua"] / 1000.0
            volts = energy["voltage_mv"] / 1000.0
            point["absolute_mj"] = energy["energy_mj"] / delivered
            point["excess_mj"] = (mean_ma - idle) * energy["duration_s"] * volts / delivered
            point["mean_ma"] = mean_ma
            (dropped if mean_ma < idle else good).append(point)

    for tech in {p["tech"] for p in good}:
        peers = [p for p in good if p["tech"] == tech]
        median = statistics.median(p["mean_ma"] for p in peers)
        for point in peers:
            point["settling"] = point["mean_ma"] < median * (1 - SETTLE_TOLERANCE)
    return good, dropped


def short_label(point):
    """'espnow/payload/8' -> 'ESP-NOW · payload 8'.

    The technology is named rather than left to the colour: 'baseline', 'payload 8' and
    'payload 16' each occur under more than one technology, so without the prefix those rows are
    distinguishable only by hue - and the palette's green sits below 3:1 against this surface.
    """
    factor, level = point.get("factor", ""), point.get("level", "")
    tech = LABEL.get(point["tech"], point["tech"])
    if factor == "baseline":
        detail = "baseline"
    elif factor == "combination":
        detail = str(level).replace("+", " + ").replace("=", " ")
    else:
        detail = f"{factor} {level}".strip()
    return f"{tech} · {detail}"


def panel(ax, y, values, colors, title, subtitle, xlabel, settling=None):
    settling = settling or [False] * len(values)
    bars = ax.barh(y, values, color=colors, height=0.68, zorder=3,
                    edgecolor=SURFACE, linewidth=1.2)
    # Hatching, not a fourth colour: "still settling" is a caveat on a series, not a series.
    for bar, suspect in zip(bars, settling):
        if suspect:
            bar.set_hatch("///")
            bar.set_edgecolor(SURFACE)
    span = max(values)
    for index, value in enumerate(values):
        # Every bar is labelled deliberately: the palette's green sits below 3:1 against this
        # surface, and the validator makes visible labels the required relief for that.
        ax.text(value + span * 0.015, index, f"{value:,.1f}", va="center", ha="left",
                color=INK_2, fontsize=8.5, zorder=4)
    ax.set_xlim(0, span * 1.16)
    style_axes(ax, xlabel, None, title, subtitle)
    ax.grid(True, axis="x", color=GRID, linewidth=0.8)
    ax.grid(False, axis="y")


def plot(points, dropped, idle_ma, out_dir):
    points = sorted(points, key=lambda p: p["excess_mj"])
    labels = [short_label(p) for p in points]
    colors = [COLOR.get(p["tech"], INK_2) for p in points]
    y = range(len(points))

    fig, (left, right) = plt.subplots(1, 2, figsize=(14.5, 6.0), sharey=True,
                                       gridspec_kw={"width_ratios": [1, 1], "wspace": 0.07})
    fig.patch.set_facecolor(SURFACE)

    settling = [p.get("settling", False) for p in points]

    panel(left, y, [p["excess_mj"] for p in points], colors,
          "Above idle — what the traffic cost",
          "Window energy minus the same radio sitting idle, per delivered message. The "
          "comparable figure.",
          "energy above idle per delivered message (mJ)", settling)
    left.set_yticks(list(y))
    left.set_yticklabels(labels, fontsize=9)

    panel(right, y, [p["absolute_mj"] for p in points], colors,
          "Absolute — whole-node cost",
          "Total window energy per delivered message. Dominated by standing current, so it "
          "ranks differently.",
          "absolute energy per delivered message (mJ)", settling)

    handles = [plt.Rectangle((0, 0), 1, 1, color=COLOR[t]) for t in TECH_ORDER]
    fig.legend(handles, [LABEL[t] for t in TECH_ORDER], loc="lower center", ncol=3,
               frameon=False, fontsize=9.5, labelcolor=INK_2, bbox_to_anchor=(0.5, -0.03))

    idle_text = "  ".join(f"{LABEL.get(k, k)} {v:.1f}" for k, v in sorted(idle_ma.items())
                          if k != "none")
    note = (f"Node source-metered at 5.0 V. Idle baselines subtracted (mA): {idle_text}"
            f"  ·  floor {idle_ma.get('none', float('nan')):.1f}."
            "  10 exchanges per point, one repetition — no error bars.")
    if dropped:
        note += ("\nExcluded as radio-warm-up (mean current below that technology's own idle, so "
                 "the radio was not up for the whole window): "
                 + ", ".join(f"{short_label(p)} ({p['mean_ma']:.1f} mA)" for p in dropped))
    suspect = [p for p in points if p.get("settling")]
    if suspect:
        note += ("\nHatched — still settling, drawing >5 % below that technology's median, so the "
                 "value is an underestimate and not a payload effect: "
                 + ", ".join(f"{short_label(p)} ({p['mean_ma']:.1f} mA)" for p in suspect))
    fig.text(0.005, -0.075, note, color=INK_2, fontsize=8, ha="left", va="top", linespacing=1.5)

    fig.suptitle("Energy comparison — quick bench, 2026-09-21", color=INK, fontsize=13,
                 x=0.005, ha="left", y=1.03)
    save(fig, out_dir, "energy-comparison")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", nargs="+", help="run directories to compare")
    parser.add_argument("--idle", required=True,
                        help="JSON from energy_check --json, holding the standing currents")
    parser.add_argument("--out", default="figures", help="output directory (default: figures/)")
    args = parser.parse_args()

    idle_ma = json.loads(Path(args.idle).read_text())["idle_ma"]
    points, dropped = load_points(args.results, idle_ma)
    if not points:
        sys.exit("no usable points with an energy record — was the PPK2 attached?")
    for point in dropped:
        print(f"  dropped {point['point_key']}: {point['mean_ma']:.2f} mA is below "
              f"{point['tech']} idle {idle_ma[point['tech']]:.2f} mA — radio still warming up")
    print(f"{len(points)} usable points across {len({p['tech'] for p in points})} technologies")
    plot(points, dropped, idle_ma, Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
