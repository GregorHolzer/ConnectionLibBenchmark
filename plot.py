#!/usr/bin/env python3
"""Figures and the results table for a campaign.

    ./plot.py --campaign field-20261007

Writes into figures/<campaign>/ (what each one is for is in bench/plots.py):

    technologies.png   the three technologies at their baseline, one panel per metric
    lora_factors.png   what each LoRa knob does
    latency_cdf.png    the whole latency distribution per technology
"""

import argparse
from pathlib import Path

from bench import plots, results, summary

REPO_ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--campaign", required=True, help="directory under results/")
    args = parser.parse_args()

    results_dir = REPO_ROOT / "results" / args.campaign
    runs, exchanges = results.load_runs(results_dir), results.load_exchanges(results_dir)
    rows = summary.summarise(runs, exchanges)
    if not rows:
        raise SystemExit(f"no measured points in {results_dir}")
    out_dir = REPO_ROOT / "figures" / args.campaign
    out_dir.mkdir(parents=True, exist_ok=True)
    plots.plot_technologies(rows, summary.standing_currents(runs), out_dir / "technologies.png")
    plots.plot_lora_factors(rows, out_dir / "lora_factors.png")
    plots.plot_latency_cdf(rows, exchanges, out_dir / "latency_cdf.png")
    print(summary.table(rows))
    print(f"figures in {out_dir}")


if __name__ == "__main__":
    main()
