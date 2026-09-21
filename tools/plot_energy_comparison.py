#!/usr/bin/env python3
"""Compare energy across technologies for a set of single-technology runs.

    ./tools/plot_energy_comparison.py results/espnow-* results/zigbee-* results/lora-*

`plot_results.py` covers this for a *campaign*, where every rotation leads with idle points and
`idle_baselines()` can separate what the radio costs from what the ESP32 costs. A `run_benchmark.py
--quick` run has no idle points, so the above-idle decomposition is unavailable and only the
absolute figures can be drawn. This renders those, over any number of run directories.

Two panels rather than one chart with two y-scales: energy per delivered message and mean current
are different quantities on different scales, and overlaying them would invent a relationship
between them.
"""

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plot_results import COLOR, GRID, INK, INK_2, LABEL, SURFACE, TECH_ORDER, save, style_axes


def load_points(dirs):
    """Every measured point carrying an energy record, with its per-delivered cost."""
    out = []
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
            point["per_delivered_mj"] = energy["energy_mj"] / delivered
            out.append(point)
    return out


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


def plot(points, out_dir):
    points = sorted(points, key=lambda p: p["per_delivered_mj"])
    labels = [short_label(p) for p in points]
    colors = [COLOR.get(p["tech"], INK_2) for p in points]
    per_msg = [p["per_delivered_mj"] for p in points]
    current = [p["energy"]["mean_ua"] / 1000.0 for p in points]
    y = range(len(points))

    fig, (left, right) = plt.subplots(
        1, 2, figsize=(13.5, 6.2), sharey=True,
        gridspec_kw={"width_ratios": [1.55, 1], "wspace": 0.08})
    fig.patch.set_facecolor(SURFACE)

    # Rounded data-ends, anchored at the baseline; a surface-coloured edge keeps adjacent bars
    # from touching, which is what the 2px spacer rule asks for on a dense categorical axis.
    left.barh(y, per_msg, color=colors, height=0.68, zorder=3,
              edgecolor=SURFACE, linewidth=1.2)
    span = max(per_msg)
    for index, value in enumerate(per_msg):
        # Every bar is labelled deliberately: the palette's green sits below 3:1 against this
        # surface, and the validator makes visible labels the required relief for that.
        left.text(value + span * 0.012, index, f"{value:,.0f}", va="center", ha="left",
                  color=INK_2, fontsize=8.5, zorder=4)
    left.set_xlim(0, span * 1.12)
    left.set_yticks(list(y))
    left.set_yticklabels(labels, fontsize=9)
    style_axes(left, "energy per delivered message (mJ)", None,
               "Energy per delivered message",
               "Absolute cost of the measured window, divided by messages that actually arrived. "
               "10 exchanges per point, one repetition.")
    left.grid(True, axis="x", color=GRID, linewidth=0.8)
    left.grid(False, axis="y")

    right.barh(y, current, color=colors, height=0.68, zorder=3,
               edgecolor=SURFACE, linewidth=1.2)
    right.set_xlim(0, max(current) * 1.12)
    style_axes(right, "mean current during the point (mA)", None,
               "Mean current drawn",
               "The node is source-metered at 5.0 V. Its standing draw sets the floor every "
               "figure on the left is built on.")
    right.grid(True, axis="x", color=GRID, linewidth=0.8)
    right.grid(False, axis="y")

    handles = [plt.Rectangle((0, 0), 1, 1, color=COLOR[t]) for t in TECH_ORDER]
    fig.legend(handles, [LABEL[t] for t in TECH_ORDER], loc="lower center", ncol=3,
               frameon=False, fontsize=9.5, labelcolor=INK_2, bbox_to_anchor=(0.5, -0.02))

    fig.suptitle("Energy comparison — quick bench, 2026-09-21", color=INK, fontsize=13,
                 x=0.005, ha="left", y=1.02)
    save(fig, out_dir, "energy-comparison")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", nargs="+", help="run directories to compare")
    parser.add_argument("--out", default="figures", help="output directory (default: figures/)")
    args = parser.parse_args()

    points = load_points(args.results)
    if not points:
        sys.exit("no points with an energy record — was the PPK2 attached?")
    print(f"{len(points)} measured points across "
          f"{len({p['tech'] for p in points})} technologies")
    plot(points, Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
