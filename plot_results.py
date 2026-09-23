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

The figures themselves live in bench/figures/, one module per family; this file only decides which
of them to render for the campaign it was pointed at.
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# bench.plotting first: it selects the headless Agg backend, which has to happen before pyplot
# is imported anywhere.
from bench.plotting import TECH_ORDER
from bench.figures.energy import (plot_energy_per_delivered, plot_power_trace,
                                   plot_standing_current)
from bench.figures.hyperparameter import plot_factor
from bench.figures.overview import plot_overview
from bench.figures.reliability import (plot_cv_heatmap, plot_drift, plot_ecdf_per_repetition,
                                        plot_per_repetition)
from bench.results import load_meta, load_points, load_raw

REPO_ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True)
    parser.add_argument("--skip-factor", action="append", default=[], metavar="TECH:FACTOR",
                        help="do not render this factor figure; repeatable. For sweeps whose knob "
                             "moves neither latency nor energy, which plot_comparison.py's "
                             "--report-flat identifies. Example: --skip-factor espnow:channel")
    args = parser.parse_args()

    skipped = set()
    for entry in args.skip_factor:
        tech, _, factor = entry.partition(":")
        if not factor:
            sys.exit(f"--skip-factor wants TECH:FACTOR, got {entry!r}")
        skipped.add((tech, factor))

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
    plot_standing_current(points, out_dir)
    plot_energy_per_delivered(points, out_dir)
    plot_power_trace(results_dir, out_dir)
    for tech in techs:
        for factor in sorted(factors_seen.get(tech, ())):
            if (tech, factor) in skipped:
                print(f"  {tech}-factor-{factor}: skipped")
                continue
            plot_factor(raw, points, tech, factor, out_dir)
        plot_per_repetition(raw, tech, out_dir)
        plot_drift(raw, points, tech, out_dir)
        plot_ecdf_per_repetition(raw, tech, out_dir)
    plot_cv_heatmap(raw, out_dir)

    print(f"\ndone. Audit the campaign with:\n  ./verify_practices.py --results {results_dir}")


if __name__ == "__main__":
    main()
