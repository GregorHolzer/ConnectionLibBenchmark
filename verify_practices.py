#!/usr/bin/env python3
"""Audit a campaign: does it follow good benchmarking practice, and are its numbers reliable?

    ./verify_practices.py --results results/campaign-20260918-1200
    ./verify_practices.py --strict        # warnings are failures too

Two passes:

**A. Methodology** - checks the benchmark against the practices in
docs/benchmarking-practices.md, each of which comes from `02_experimentation.pdf`. Every check
cites its practice ID, so a failure points at the rule it broke.

**B. Reliability** - checks the data the campaign produced. The distinction that drives this pass
is the deck's central one: *random* error can be beaten down with repetitions, *systematic* error
cannot. So a drift across the campaign is not reported as a wider error bar - it is reported as a
failure that more measurement will not fix.

The checks themselves live in bench/audit/, one module per group; this file only decides what to
load, in what order to run them, and what exit code to leave behind.

Writes `verification.md` next to the data and exits non-zero if anything FAILs.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.audit import claims, design, environment, stability
from bench.audit.report import FAIL, Report, SECTION_METHODOLOGY, SECTION_RELIABILITY
from bench.results import load_meta, load_points, load_raw

REPO_ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", required=True)
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args()

    results_dir = Path(args.results)
    if not results_dir.is_absolute():
        results_dir = REPO_ROOT / results_dir
    if not results_dir.exists():
        sys.exit(f"no such results directory: {results_dir}")

    meta = load_meta(results_dir)
    points = load_points(results_dir)
    raw = load_raw(results_dir)

    print(f"auditing {results_dir.name}: {len(points)} configuration points, "
          f"{len(raw)} exchanges")

    report = Report()
    environment.run(report, SECTION_METHODOLOGY, meta, points, raw)
    design.run(report, SECTION_METHODOLOGY, results_dir, meta, points, raw)

    # Pass B needs grouped samples, and every one of its checks is meaningless without them, so
    # an empty grouping short-circuits the whole pass rather than skipping check by check.
    grouped = stability.samples_by_point_rotation(raw)
    if not grouped:
        report.add(SECTION_RELIABILITY, "", "Data present", FAIL,
                   "no successful exchanges to analyse")
    else:
        stability.run(report, SECTION_RELIABILITY, grouped, points)
        claims.run(report, SECTION_RELIABILITY, grouped, meta, points)

    report.print_console()
    out_path = results_dir / "verification.md"
    report.write_markdown(out_path, results_dir)

    print(f"\n{len(report.rows) - len(report.failures) - len(report.warnings)} passed, "
          f"{len(report.warnings)} warnings, {len(report.failures)} failed")
    print(f"written to {out_path}")

    if report.failures:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
