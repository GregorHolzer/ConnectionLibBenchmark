#!/usr/bin/env python3
"""Figures for a distance campaign.

    ./plot.py --campaign field-20261007

Writes two PNGs into figures/<campaign>/:

    comparison.png   the three technologies at their baseline, every metric against distance
    lora_sweep.png   what each LoRa factor does to delivery and latency as distance grows

A point with no delivery has no latency or energy per message, so its line simply stops there;
delivery itself is drawn down to zero, because "out of range" is the result at that distance.
"""

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402

from bench import configs, results, summary   # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent
COLORS = {"zigbee": "#2a9d8f", "espnow": "#e76f51", "lora": "#264653"}
LABELS = {"zigbee": "Zigbee", "espnow": "ESP-NOW", "lora": "LoRa (SF9 baseline)"}


def _median(name, scale=1.0):
    return lambda row: None if row[name].get("median") is None else row[name]["median"] * scale


def _energy(name):
    return lambda row: row["energy"].get(name)


# (title, y label, value of a summary row, log y axis)
COMPARISON_PANELS = [
    ("Delivery, confirmed by the library's ack", "PDR", lambda row: row["pdr"], False),
    ("Delivery, counted at the gateway", "PDR", lambda row: row["gateway_pdr"], False),
    ("Delivery latency, median (time in send())", "ms", _median("latency_us", 1e-3), True),
    ("Goodput, one way", "bit/s", lambda row: row["goodput_bps"], True),
    ("Energy per delivered payload", "mJ", _energy("energy_per_delivered_mj"), True),
    ("… above the radio's standing current", "mJ", _energy("excess_energy_per_delivered_mj"), True),
    ("RSSI at the gateway (LoRa)", "dBm", lambda row: row["gw_rssi_dbm"], False),
    ("SNR at the gateway (LoRa)", "dB", lambda row: row["gw_snr_db"], False),
]


def _series(rows, value, log_y=False):
    """(distances, values) with the gaps left out, sorted by distance. A log axis cannot show
    zero, so there a zero is a gap too."""
    points = [(row["distance_m"], value(row)) for row in rows]
    points = sorted((d, v) for d, v in points if v is not None and (v > 0 or not log_y))
    return [d for d, _ in points], [v for _, v in points]


def _style_axis(axis, title, ylabel, log_y, distances):
    axis.set_title(title, fontsize=10, loc="left")
    axis.set_ylabel(ylabel)
    if distances and min(distances) > 0:   # a desk run at 0 m has no place on a log axis
        axis.set_xscale("log")
    axis.set_xticks(distances)
    axis.set_xticklabels([f"{d:g}" for d in distances])
    axis.minorticks_off()
    if log_y:
        axis.set_yscale("log")
    elif ylabel == "PDR":
        axis.set_ylim(-0.05, 1.05)
    axis.grid(True, alpha=0.3)


def plot_comparison(rows, out_path):
    baselines = [row for row in rows if row["name"] == "baseline"]
    distances = sorted({row["distance_m"] for row in baselines})
    figure, axes = plt.subplots(2, 4, figsize=(20, 8), sharex=True)
    for axis, (title, ylabel, value, log_y) in zip(axes.flat, COMPARISON_PANELS):
        for tech in ("zigbee", "espnow", "lora"):
            xs, ys = _series([row for row in baselines if row["tech"] == tech], value, log_y)
            if xs:
                axis.plot(xs, ys, marker="o", color=COLORS[tech], label=LABELS[tech])
        _style_axis(axis, title, ylabel, log_y, distances)
    for axis in axes[-1]:
        axis.set_xlabel("distance to the gateway (m)")
    axes[0][0].legend(loc="lower left", fontsize=9)
    figure.suptitle("LoRa, Zigbee and ESP-NOW against distance (baseline configurations)")
    figure.tight_layout()
    figure.savefig(out_path, dpi=150)
    plt.close(figure)


def plot_lora_sweep(rows, out_path):
    """One column per LoRa factor; each line is one level of that factor."""
    lora = [row for row in rows if row["tech"] == "lora"]
    distances = sorted({row["distance_m"] for row in lora})
    factors = list(configs.LORA_FACTORS)
    figure, axes = plt.subplots(2, len(factors), figsize=(4 * len(factors), 7.5), sharex=True,
                                squeeze=False)
    panels = [("Delivery", "PDR", lambda row: row["pdr"], False),
              ("Delivery latency, median", "ms", _median("latency_us", 1e-3), True)]
    for column, factor in enumerate(factors):
        levels = configs.LORA_FACTORS[factor]
        for index, level in enumerate(levels):
            # The baseline point is one level of every factor.
            name = "baseline" if level == configs.LORA_BASELINE[factor] else f"{factor}={level}"
            level_rows = [row for row in lora if row["name"] == name]
            color = plt.cm.viridis(index / max(len(levels) - 1, 1))
            for axis, (_, _, value, log_y) in zip(axes[:, column], panels):
                xs, ys = _series(level_rows, value, log_y)
                if xs:
                    axis.plot(xs, ys, marker="o", color=color, label=f"{factor}={level}")
        for axis, (title, ylabel, _, log_y) in zip(axes[:, column], panels):
            _style_axis(axis, f"{title} by {factor}", ylabel, log_y, distances)
        axes[0][column].legend(fontsize=8, loc="lower left")
        axes[1][column].set_xlabel("distance to the gateway (m)")
    figure.suptitle("LoRa, one factor at a time around SF9 / 125 kHz / 4:7 / 7 dBm / 32 B")
    figure.tight_layout()
    figure.savefig(out_path, dpi=150)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--campaign", required=True, help="directory under results/")
    args = parser.parse_args()

    results_dir = REPO_ROOT / "results" / args.campaign
    rows = summary.summarise(results.load_runs(results_dir), results.load_exchanges(results_dir))
    if not rows:
        raise SystemExit(f"no measured points in {results_dir}")
    out_dir = REPO_ROOT / "figures" / args.campaign
    out_dir.mkdir(parents=True, exist_ok=True)
    plot_comparison(rows, out_dir / "comparison.png")
    plot_lora_sweep(rows, out_dir / "lora_sweep.png")
    print(summary.table(rows))
    print(f"figures in {out_dir}")


if __name__ == "__main__":
    main()
