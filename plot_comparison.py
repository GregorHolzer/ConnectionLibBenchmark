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

The figures live in bench/figures/comparison_*.py, one module per family; this file selects the
best configuration of each technology, renders them, and prints the numbers behind them.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# bench.plotting selects the headless Agg backend, so it has to be imported before pyplot.
from bench.plotting import LABEL, TECH_ORDER
from bench.comparison import (aggregate, board_floor_ua, flat_factors, select_best,
                               standing_currents)
from bench.figures.comparison_best import plot_best_config
from bench.figures.comparison_energy import plot_energy_budget
from bench.figures.comparison_signal import plot_signal_quality
from bench.figures.comparison_sweeps import plot_payload_sweep, plot_stack_overhead
from bench.results import load_meta, load_node_rx, load_points, load_raw

REPO_ROOT = Path(__file__).resolve().parent


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
    plot_energy_budget(best, standing, out_dir, args.interval_s)
    plot_stack_overhead(records, out_dir)
    plot_signal_quality(records, out_dir)
    report(records, best, rejected, standing, floor_ua,
           args.best_by, args.pdr_floor, args.report_flat)


if __name__ == "__main__":
    main()
