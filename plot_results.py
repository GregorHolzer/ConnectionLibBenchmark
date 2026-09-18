#!/usr/bin/env python3
"""Render figures from a campaign.

    ./plot_results.py --results results/campaign-20260918-1200

Two families:

* **Hyperparameter figures** — what each technology's knobs actually do, one figure per factor.
* **Reliability figures** — whether the numbers hold up. Box and violin plots per configuration
  point (slide 20 of `02_experimentation.pdf` calls for exactly these), per-repetition strip
  overlays, drift against campaign wall-clock time, a CV heatmap, and per-repetition ECDFs.

Everything is written as PNG *and* PDF, the latter vector for dropping into the thesis. Light mode
only: these are print figures, not a web page.
"""

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")   # headless: the Pi has no display
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.analysis import describe, linear_regression
from bench.results import load_meta, load_points, load_raw

REPO_ROOT = Path(__file__).resolve().parent

# Slots 1-3 of the validated reference categorical palette; the documented set that clears the
# all-pairs CVD and normal-vision floors on this surface (worst CVD ΔE 9.2, normal-vision 24.0).
# Colour follows the technology's identity, never its rank, so omitting one never repaints another.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e3e3df"

TECH_ORDER = ["espnow", "lora", "zigbee"]
COLOR = {"espnow": "#2a78d6", "lora": "#eb6834", "zigbee": "#1baf7a"}
MARKER = {"espnow": "o", "lora": "s", "zigbee": "^"}
LABEL = {"espnow": "ESP-NOW", "lora": "LoRa", "zigbee": "Zigbee"}

FACTOR_LABEL = {
    "sf": "spreading factor", "bw": "bandwidth (kHz)", "cr": "coding rate (4/N)",
    "pwr": "transmit power (dBm)", "preamble": "preamble symbols", "payload": "payload (bytes)",
    "channel": "WiFi channel", "encrypt": "encryption", "timeout": "send timeout (ms)",
    "broadcast": "broadcast addressing",
}


def wrap(text, width):
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def style_axes(ax, xlabel, ylabel, title, subtitle=None):
    ax.set_facecolor(SURFACE)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=3, color=GRID)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=10)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=10)
    sub_lines = wrap(subtitle, 84) if subtitle else []
    if title:
        ax.set_title(title, color=INK, fontsize=12, loc="left", pad=10 + 12 * len(sub_lines))
    if sub_lines:
        ax.text(0.0, 1.015, "\n".join(sub_lines), transform=ax.transAxes, color=INK_2,
                 fontsize=8.5, va="bottom", ha="left", linespacing=1.4)


def lighten(hex_color, t):
    src = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    dst = [int(SURFACE[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (s - c) * t):02x}" for c, s in zip(src, dst))


def save(fig, out_dir, name):
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(out_dir / f"{name}.{ext}", dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"  {name}.png / .pdf")


def ms_formatter(value, _):
    return f"{value:g}"


# ------------------------------------------------------------------------------------------
# Data shaping
# ------------------------------------------------------------------------------------------

def samples(raw, tech=None):
    """{point_key: {rotation: [rtt_ms]}} for successful exchanges."""
    grouped = defaultdict(lambda: defaultdict(list))
    for row in raw:
        if row.get("ok") != "1" or row.get("tech") == "none":
            continue
        if tech and row["tech"] != tech:
            continue
        try:
            value = float(row["rtt_us"]) / 1000.0
        except (KeyError, ValueError):
            continue
        if value <= 0:
            continue
        grouped[row["point_key"]][int(row["rotation"])].append(value)
    return grouped


def factor_series(raw, points, tech, factor):
    """{level: [all rtt_ms across repetitions]} for one hyperparameter, plus the baseline."""
    meta = {p["point_key"]: p for p in points}
    baseline_level = None
    for point in points:
        if point["tech"] == tech and point["factor"] == "baseline":
            baseline_level = (point.get("params") or {}).get(factor)
            break

    by_level = defaultdict(list)
    for row in raw:
        if row.get("ok") != "1" or row["tech"] != tech:
            continue
        record = meta.get(row["point_key"])
        if not record:
            continue
        if record["factor"] == factor:
            level = record["level"]
        elif record["factor"] == "baseline" and baseline_level is not None:
            level = baseline_level
        else:
            continue
        try:
            value = float(row["rtt_us"]) / 1000.0
        except ValueError:
            continue
        if value > 0:
            by_level[str(level)].append(value)
    return by_level


def sort_levels(levels):
    def key(level):
        try:
            return (0, float(level))
        except (TypeError, ValueError):
            return (1, str(level))
    return sorted(levels, key=key)


# ------------------------------------------------------------------------------------------
# Hyperparameter figures
# ------------------------------------------------------------------------------------------

def plot_factor(raw, points, tech, factor, out_dir):
    """Box + violin per level: the distribution, not just a median (practices P11, P12)."""
    by_level = factor_series(raw, points, tech, factor)
    if len(by_level) < 2:
        return
    levels = sort_levels(by_level)
    data = [by_level[level] for level in levels]

    fig, ax = plt.subplots(figsize=(max(6.0, 1.5 * len(levels) + 2.5), 4.4))
    fig.patch.set_facecolor(SURFACE)
    positions = range(1, len(levels) + 1)

    # Violin behind for the shape, box in front for the quartiles.
    violins = ax.violinplot(data, positions=list(positions), widths=0.72, showextrema=False)
    for i, body in enumerate(violins["bodies"]):
        body.set_facecolor(lighten(COLOR[tech], 0.55))
        body.set_edgecolor("none")
        body.set_alpha(1.0)

    box = ax.boxplot(data, positions=list(positions), widths=0.26, patch_artist=True,
                      showfliers=True, medianprops=dict(color=SURFACE, linewidth=1.6),
                      flierprops=dict(marker=".", markersize=3, markerfacecolor=INK_2,
                                      markeredgecolor="none", alpha=0.5))
    for patch in box["boxes"]:
        patch.set_facecolor(COLOR[tech])
        patch.set_edgecolor("none")
    for element in ("whiskers", "caps"):
        for line in box[element]:
            line.set_color(INK_2)
            line.set_linewidth(1.0)

    counts = ", ".join(f"{level}: n={len(by_level[level])}" for level in levels)
    style_axes(ax, FACTOR_LABEL.get(factor, factor), "round-trip latency (ms)",
                f"{LABEL[tech]} — effect of {FACTOR_LABEL.get(factor, factor)}",
                f"violin shows the distribution, box the quartiles; all repetitions pooled "
                f"({counts})")
    ax.set_xticks(list(positions))
    ax.set_xticklabels(levels)
    if max(max(d) for d in data) / max(min(min(d) for d in data), 1e-9) > 20:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(FuncFormatter(ms_formatter))
    save(fig, out_dir, f"{tech}-factor-{factor}")


# ------------------------------------------------------------------------------------------
# Reliability figures
# ------------------------------------------------------------------------------------------

def plot_per_repetition(raw, tech, out_dir):
    """Each repetition as its own marker, over the pooled box. Reproducibility at a glance:
    if the repetition markers scatter widely around their box, the point is not reproducible."""
    grouped = samples(raw, tech)
    if not grouped:
        return
    keys = sorted(grouped)
    if len(keys) > 20:
        keys = keys[:20]

    fig, ax = plt.subplots(figsize=(max(7.0, 0.55 * len(keys) + 3.0), 4.8))
    fig.patch.set_facecolor(SURFACE)
    positions = range(1, len(keys) + 1)
    pooled = [[v for values in grouped[k].values() for v in values] for k in keys]

    box = ax.boxplot(pooled, positions=list(positions), widths=0.55, patch_artist=True,
                      showfliers=False, medianprops=dict(color=SURFACE, linewidth=1.4))
    for patch in box["boxes"]:
        patch.set_facecolor(lighten(COLOR[tech], 0.45))
        patch.set_edgecolor("none")
    for element in ("whiskers", "caps"):
        for line in box[element]:
            line.set_color(GRID)

    rotations = sorted({r for k in keys for r in grouped[k]})
    for position, key in zip(positions, keys):
        for rotation in rotations:
            values = grouped[key].get(rotation)
            if not values:
                continue
            tint = 0.55 * (1 - rotation / max(len(rotations) - 1, 1))
            ax.plot(position, statistics.median(values), marker=MARKER[tech], markersize=5,
                     color=lighten(COLOR[tech], tint), markeredgecolor=SURFACE,
                     markeredgewidth=0.8, linestyle="none", zorder=3)

    style_axes(ax, "", "round-trip latency (ms)",
                f"{LABEL[tech]} — reproducibility across repetitions",
                "box pools every exchange; each marker is one repetition's median, darker = later "
                "in the campaign. Markers clustered inside the box means reproducible.")
    ax.set_xticks(list(positions))
    ax.set_xticklabels([k.split("/", 1)[1] for k in keys], rotation=45, ha="right", fontsize=8)
    save(fig, out_dir, f"reliability-{tech}-per-repetition")


def plot_drift(raw, points, tech, out_dir):
    """Median against campaign wall-clock time. A trend here is *systematic* error, which
    practice P2 (slide 12) says repetitions cannot fix — so it has to be visible."""
    grouped = samples(raw, tech)
    started = {}
    for point in points:
        started.setdefault(point["point_key"], {})[point["rotation"]] = point["started_at"]
    if not grouped:
        return

    origin = min((t for rots in started.values() for t in rots.values()), default=None)
    if origin is None:
        return

    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    fig.patch.set_facecolor(SURFACE)

    all_x, all_y, plotted = [], [], 0
    for key, rotations in sorted(grouped.items()):
        xs, ys = [], []
        for rotation, values in sorted(rotations.items()):
            timestamp = started.get(key, {}).get(rotation)
            if timestamp is None:
                continue
            xs.append((timestamp - origin) / 3600.0)
            ys.append(statistics.median(values))
        if len(xs) < 2:
            continue
        # Normalised to each point's own first repetition, so points three orders of magnitude
        # apart can share one axis — this asks "did it change?", not "how big is it?".
        base = ys[0]
        if not base:
            continue
        ax.plot(xs, [y / base for y in ys], color=lighten(COLOR[tech], 0.35), linewidth=1.2,
                 marker=MARKER[tech], markersize=4, markeredgecolor=SURFACE, markeredgewidth=0.6,
                 alpha=0.85)
        all_x.extend(xs)
        all_y.extend(y / base for y in ys)
        plotted += 1

    if not plotted:
        plt.close(fig)
        return

    ax.axhline(1.0, color=INK_2, linewidth=1.2, linestyle="--")
    fit = linear_regression(all_x, all_y)
    note = ""
    if fit:
        note = (f"pooled trend {(fit['slope']) * 100:+.2f}%/hour "
                f"(r={fit['r']:.2f}, p={fit['p']:.3f})")

    style_axes(ax, "campaign time (hours)", "median latency ÷ first repetition",
                f"{LABEL[tech]} — drift across the campaign",
                f"each line is one configuration point, normalised to its own first repetition. "
                f"A systematic slope here cannot be fixed by more repetitions. {note}")
    save(fig, out_dir, f"reliability-{tech}-drift")


def plot_cv_heatmap(raw, out_dir):
    """Coefficient of variation per point and repetition — where the noise actually lives."""
    grouped = samples(raw)
    if not grouped:
        return
    keys = sorted(grouped)
    rotations = sorted({r for rots in grouped.values() for r in rots})
    if len(rotations) < 2:
        return

    matrix = []
    for key in keys:
        row = []
        for rotation in rotations:
            values = grouped[key].get(rotation)
            row.append(describe(values).get("cv") if values else float("nan"))
        matrix.append(row)

    fig, ax = plt.subplots(figsize=(max(6.0, 0.45 * len(rotations) + 5.0),
                                     max(4.0, 0.22 * len(keys) + 2.0)))
    fig.patch.set_facecolor(SURFACE)
    # Sequential: one hue, light to dark, as magnitude requires.
    image = ax.imshow(matrix, aspect="auto", cmap="YlOrBr", vmin=0, vmax=0.3)
    ax.set_xticks(range(len(rotations)))
    ax.set_xticklabels(rotations)
    ax.set_yticks(range(len(keys)))
    ax.set_yticklabels([k.replace("/", " ") for k in keys], fontsize=7)
    ax.tick_params(colors=INK_2, length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xlabel("repetition", color=INK_2, fontsize=10)
    ax.set_title("Within-run coefficient of variation", color=INK, fontsize=12, loc="left",
                  pad=22)
    ax.text(0.0, 1.015, "darker = noisier. A whole dark column is a disturbed repetition; a whole "
                        "dark row is an inherently noisy configuration.",
             transform=ax.transAxes, color=INK_2, fontsize=8.5, va="bottom", ha="left")
    bar = fig.colorbar(image, ax=ax, fraction=0.025, pad=0.02)
    bar.ax.tick_params(colors=INK_2, labelsize=8)
    bar.outline.set_visible(False)
    save(fig, out_dir, "reliability-cv-heatmap")


def plot_ecdf_per_repetition(raw, tech, out_dir):
    """Per-repetition ECDFs of the baseline point: do whole distributions coincide, not just
    their medians? Two runs can share a median and differ completely in shape."""
    grouped = samples(raw, tech)
    baseline = next((k for k in sorted(grouped) if "/baseline/" in k), None)
    if not baseline or len(grouped[baseline]) < 2:
        return

    fig, ax = plt.subplots(figsize=(6.6, 4.2))
    fig.patch.set_facecolor(SURFACE)
    rotations = sorted(grouped[baseline])
    for rotation in rotations:
        values = sorted(grouped[baseline][rotation])
        ys = [(i + 1) / len(values) for i in range(len(values))]
        tint = 0.55 * (1 - rotation / max(len(rotations) - 1, 1))
        ax.step(values, ys, where="post", color=lighten(COLOR[tech], tint), linewidth=1.8,
                 label=f"rep {rotation}")

    style_axes(ax, "round-trip latency (ms)", "fraction of exchanges",
                f"{LABEL[tech]} — baseline distribution, one line per repetition",
                "overlapping lines mean the whole distribution reproduces, not merely its median")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v * 100:.0f}%"))
    ax.legend(frameon=False, labelcolor=INK_2, fontsize=8, loc="lower right", ncol=2)
    save(fig, out_dir, f"reliability-{tech}-ecdf-per-repetition")


def plot_overview(raw, points, out_dir):
    """Cross-technology baseline comparison, on a log axis — three orders of magnitude apart."""
    grouped = samples(raw)
    data, techs = [], []
    for tech in TECH_ORDER:
        key = next((k for k in grouped if k.startswith(f"{tech}/baseline/")), None)
        if not key:
            continue
        pooled = [v for values in grouped[key].values() for v in values]
        if pooled:
            data.append(pooled)
            techs.append(tech)
    if not data:
        return

    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    fig.patch.set_facecolor(SURFACE)
    positions = range(1, len(techs) + 1)

    violins = ax.violinplot(data, positions=list(positions), widths=0.7, showextrema=False)
    for body, tech in zip(violins["bodies"], techs):
        body.set_facecolor(lighten(COLOR[tech], 0.55))
        body.set_edgecolor("none")
        body.set_alpha(1.0)
    box = ax.boxplot(data, positions=list(positions), widths=0.22, patch_artist=True,
                      showfliers=False, medianprops=dict(color=SURFACE, linewidth=1.6))
    for patch, tech in zip(box["boxes"], techs):
        patch.set_facecolor(COLOR[tech])
        patch.set_edgecolor("none")
    for element in ("whiskers", "caps"):
        for line in box[element]:
            line.set_color(INK_2)
            line.set_linewidth(1.0)

    style_axes(ax, "", "round-trip latency (ms, log scale)",
                "Baseline configuration, all three technologies",
                "each technology at its own documented baseline; all repetitions pooled")
    ax.set_xticks(list(positions))
    ax.set_xticklabels([LABEL[t] for t in techs])
    ax.set_yscale("log")
    ax.yaxis.set_major_formatter(FuncFormatter(ms_formatter))
    save(fig, out_dir, "overview-baseline")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True)
    args = parser.parse_args()

    results_dir = Path(args.results)
    if not results_dir.is_absolute():
        results_dir = REPO_ROOT / results_dir
    raw = load_raw(results_dir)
    points = load_points(results_dir)
    load_meta(results_dir)
    if not raw:
        sys.exit(f"no raw.csv in {results_dir}")

    out_dir = results_dir / "plots"
    print(f"writing figures to {out_dir}")

    techs = [t for t in TECH_ORDER if any(r["tech"] == t for r in raw)]
    factors_seen = defaultdict(set)
    for point in points:
        if point["factor"] not in ("baseline", "combination"):
            factors_seen[point["tech"]].add(point["factor"])

    plot_overview(raw, points, out_dir)
    for tech in techs:
        for factor in sorted(factors_seen.get(tech, ())):
            plot_factor(raw, points, tech, factor, out_dir)
        plot_per_repetition(raw, tech, out_dir)
        plot_drift(raw, points, tech, out_dir)
        plot_ecdf_per_repetition(raw, tech, out_dir)
    plot_cv_heatmap(raw, out_dir)

    print(f"\ndone. Audit the campaign with:\n  ./verify_practices.py --results {results_dir}")


if __name__ == "__main__":
    main()
