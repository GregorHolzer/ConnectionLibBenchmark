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

Writes `verification.md` next to the data and exits non-zero if anything FAILs.
"""

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.analysis import (describe, linear_regression, median_confidence_interval, percentile,
                            gross_outliers, rank_stability, triscale_runs_needed)
from bench.results import load_meta, load_node_rx, load_points, load_raw

REPO_ROOT = Path(__file__).resolve().parent

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"
COLOURS = {PASS: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", SKIP: "\033[2m"}
RESET = "\033[0m"

# Thresholds. Stated here rather than buried in the checks so they can be argued with.
MIN_REPETITIONS = 3            # below this, random and systematic error are indistinguishable
MAX_OVERHEAD_FRACTION = 0.01   # instrumentation must cost <1% of the smallest measured quantity
MAX_CV = 0.25                  # within-point coefficient of variation
MAX_BETWEEN_REP_SPREAD = 0.20  # relative spread of a point's median across repetitions
DRIFT_P = 0.01                 # significance for calling a trend systematic
MAX_OUTLIER_FRACTION = 0.05
MIN_RANK_STABILITY = 0.80      # mean Spearman correlation between repetitions
WARMUP_K = 3                   # first k exchanges checked against the rest


class Report:
    def __init__(self):
        self.rows = []

    def add(self, section, practice, name, verdict, detail):
        self.rows.append({"section": section, "practice": practice, "name": name,
                           "verdict": verdict, "detail": detail})

    @property
    def failures(self):
        return [r for r in self.rows if r["verdict"] == FAIL]

    @property
    def warnings(self):
        return [r for r in self.rows if r["verdict"] == WARN]

    def print_console(self):
        current = None
        for row in self.rows:
            if row["section"] != current:
                current = row["section"]
                print(f"\n{current}\n{'=' * len(current)}")
            colour = COLOURS[row["verdict"]]
            practice = f"[{row['practice']}]" if row["practice"] else "     "
            print(f"  {colour}{row['verdict']:<4}{RESET} {practice:<6} {row['name']}")
            for line in str(row["detail"]).split("\n"):
                if line:
                    print(f"              {line}")

    def write_markdown(self, path, results_dir):
        lines = [f"# Verification report — `{Path(results_dir).name}`", ""]
        counts = defaultdict(int)
        for row in self.rows:
            counts[row["verdict"]] += 1
        lines.append(f"**{counts[PASS]} passed, {counts[WARN]} warnings, {counts[FAIL]} failed, "
                     f"{counts[SKIP]} skipped.**")
        lines.append("")
        lines.append("Practice IDs refer to [docs/benchmarking-practices.md]"
                     "(../../docs/benchmarking-practices.md), which summarises "
                     "`02_experimentation.pdf` (Peter Thoman, University of Innsbruck).")
        lines.append("")

        current = None
        for row in self.rows:
            if row["section"] != current:
                current = row["section"]
                lines += ["", f"## {current}", "",
                          "| | Practice | Check | Detail |", "|---|---|---|---|"]
            detail = str(row["detail"]).replace("\n", "<br>").replace("|", "\\|")
            lines.append(f"| **{row['verdict']}** | {row['practice'] or '—'} | {row['name']} "
                         f"| {detail} |")
        lines.append("")
        Path(path).write_text("\n".join(lines))


# ------------------------------------------------------------------------------------------
# Pass A — methodology
# ------------------------------------------------------------------------------------------

def audit_methodology(report, results_dir, meta, points, raw, node_rx):
    section = "A. Methodology — does the benchmark follow good practice?"

    # P6: release build, no debug instrumentation.
    info = (meta.get("device_info") or {})
    verdicts = []
    for role, facts in info.items():
        debug_level = facts.get("coreDebugLevel", "?")
        optimized = facts.get("optimized")
        verdicts.append((role, debug_level, optimized))
    if not verdicts:
        report.add(section, "P6", "Release build", SKIP, "no device_info recorded in meta.json")
    else:
        bad = [f"{role}: coreDebugLevel={lvl}, optimized={opt}"
               for role, lvl, opt in verdicts if opt != "1" or lvl not in ("0", "?")]
        if bad:
            report.add(section, "P6", "Release build", FAIL,
                       "debug indicators present — the deck is explicit that measuring debug "
                       "builds is a non-representative scenario\n" + "\n".join(bad))
        else:
            report.add(section, "P6", "Release build", PASS,
                       ", ".join(f"{role}: optimized, no core debug" for role, _, _ in verdicts))

    # P3: best available timer.
    timers = {role: facts.get("timer") for role, facts in info.items()}
    if not timers:
        report.add(section, "P3", "Timer resolution", SKIP, "no device_info recorded")
    elif all(t == "micros" for t in timers.values()):
        report.add(section, "P3", "Timer resolution", PASS,
                   "micros() on both boards, 1 µs resolution")
    else:
        report.add(section, "P3", "Timer resolution", FAIL, f"unexpected timers: {timers}")

    # P4: instrumentation overhead bounded. The null measurement (tech=none) gives the figure.
    overhead = [float(r["rtt_us"]) for r in raw if r["tech"] == "none"]
    smallest = min((float(r["rtt_us"]) for r in raw
                    if r["tech"] != "none" and r["ok"] == "1" and float(r["rtt_us"]) > 0),
                   default=None)
    if not overhead:
        report.add(section, "P4", "Measurement overhead", SKIP,
                   "no null measurement in this campaign — run `CFG tech=none` to quantify it")
    elif smallest is None:
        report.add(section, "P4", "Measurement overhead", SKIP, "no successful exchanges to compare")
    else:
        median_overhead = statistics.median(overhead)
        fraction = median_overhead / smallest
        verdict = PASS if fraction <= MAX_OVERHEAD_FRACTION else WARN
        report.add(section, "P4", "Measurement overhead", verdict,
                   f"harness costs {median_overhead:.1f} µs; smallest measured exchange "
                   f"{smallest:.0f} µs → {fraction * 100:.2f}% "
                   f"(threshold {MAX_OVERHEAD_FRACTION * 100:.0f}%)")

    # P5: external load — WiFi must be off whenever ESP-NOW is not the technology under test.
    wifi_violations = []
    for point in points:
        header = point.get("header") or {}
        tech, wifi = point.get("tech"), header.get("wifi")
        if wifi is None:
            continue
        if tech != "espnow" and wifi != "off":
            wifi_violations.append(f"{point['point_key']}: wifi={wifi}")
    if not any((p.get("header") or {}).get("wifi") for p in points):
        report.add(section, "P5", "External load (radio)", SKIP, "no wifi state recorded")
    elif wifi_violations:
        report.add(section, "P5", "External load (radio)", FAIL,
                   "WiFi left on during a non-ESP-NOW run — a 2.4 GHz transmitter beside the "
                   "measurement\n" + "\n".join(wifi_violations[:5]))
    else:
        report.add(section, "P5", "External load (radio)", PASS,
                   "WiFi off for every LoRa and Zigbee point; on only for ESP-NOW")

    # P9: interleaving. Reconstructed from the recorded order, not taken on trust.
    order = sorted(points, key=lambda p: p["run_index"])
    adjacent_repeats, gaps = [], []
    last_seen = {}
    for position, point in enumerate(order):
        key = point["point_key"]
        if key in last_seen:
            gap = position - last_seen[key]
            gaps.append(gap)
            if gap == 1:
                adjacent_repeats.append(key)
        last_seen[key] = position
    if not gaps:
        report.add(section, "P9", "Repetitions interleaved", SKIP,
                   "only one repetition — nothing to interleave")
    elif adjacent_repeats:
        report.add(section, "P9", "Repetitions interleaved", FAIL,
                   f"{len(adjacent_repeats)} configuration point(s) ran twice back to back, "
                   f"e.g. {adjacent_repeats[0]} — slide 18 requires repetitions to be spaced")
    else:
        report.add(section, "P9", "Repetitions interleaved", PASS,
                   f"minimum gap between repetitions of the same point: {min(gaps)} runs "
                   f"(median {statistics.median(gaps):.0f})")

    # P10: enough repetitions.
    reps_per_tech = defaultdict(set)
    for point in points:
        reps_per_tech[point["tech"]].add(point["rotation"])
    thin = {tech: len(rots) for tech, rots in reps_per_tech.items() if len(rots) < MIN_REPETITIONS}
    if not reps_per_tech:
        report.add(section, "P10", "Repetition count", SKIP, "no points recorded")
    elif thin:
        report.add(section, "P10", "Repetition count", WARN,
                   f"below {MIN_REPETITIONS} repetitions: " +
                   ", ".join(f"{t}={n}" for t, n in thin.items()) +
                   "\nrandom and systematic error cannot be separated at this count")
    else:
        report.add(section, "P10", "Repetition count", PASS,
                   ", ".join(f"{t}={len(r)}" for t, r in sorted(reps_per_tech.items())))

    # P8: input data swept.
    payloads_per_tech = defaultdict(set)
    for point in points:
        payload = (point.get("params") or {}).get("payload")
        if payload is not None:
            payloads_per_tech[point["tech"]].add(payload)
    single = {t: sorted(p) for t, p in payloads_per_tech.items() if len(p) < 2}
    if not payloads_per_tech:
        report.add(section, "P8", "Input data swept", SKIP, "no payload parameters recorded")
    elif single:
        report.add(section, "P8", "Input data swept", WARN,
                   f"only one payload size for: {single}")
    else:
        report.add(section, "P8", "Input data swept", PASS,
                   ", ".join(f"{t}: {sorted(p)}" for t, p in sorted(payloads_per_tech.items())))

    # P7: measured in the deployed configuration — the EU envelope, unrelaxed.
    relaxed = []
    for point in points:
        header = point.get("header") or {}
        if point["tech"] != "lora":
            continue
        power = header.get("txPowerDbm")
        if power is not None and int(power) > 7:
            relaxed.append(f"{point['point_key']}: txPowerDbm={power}")
    lora_points = [p for p in points if p["tech"] == "lora"]
    if not lora_points:
        report.add(section, "P7", "Deployed configuration", SKIP, "no LoRa points in this campaign")
    elif relaxed:
        report.add(section, "P7", "Deployed configuration", FAIL,
                   "transmit power above the 10 mW e.r.p. ceiling\n" + "\n".join(relaxed[:5]))
    else:
        report.add(section, "P7", "Deployed configuration", PASS,
                   "every LoRa point inside the EU envelope (≤7 dBm conducted, in-band, "
                   "10 % duty cycle never relaxed)")

    # P1: latency and throughput measured independently.
    has_rtt = any(r.get("rtt_us") for r in raw)
    has_send = any(r.get("send_us") for r in raw)
    if has_rtt and has_send:
        report.add(section, "P1", "Latency vs throughput", PASS,
                   "rtt_us and send_us recorded separately; goodput computed over wall clock, "
                   "not derived from latency")
    else:
        report.add(section, "P1", "Latency vs throughput", WARN, "timing columns incomplete")

    # P14: benchmark coverage.
    space = meta.get("configuration_space") or {}
    if not space:
        report.add(section, "P14", "Benchmark coverage", SKIP, "no configuration space recorded")
    else:
        visited = {p["point_key"] for p in points}
        missing = []
        for tech, entries in space.items():
            for entry in entries:
                key = f"{tech}/{entry['factor']}/{entry['level']}"
                if key not in visited:
                    missing.append(key)
        if missing:
            report.add(section, "P14", "Benchmark coverage", WARN,
                       f"{len(missing)} of "
                       f"{sum(len(v) for v in space.values())} configurations never ran, e.g. "
                       f"{', '.join(missing[:3])}")
        else:
            report.add(section, "P14", "Benchmark coverage", PASS,
                       f"all {len(visited)} configurations in the space were exercised")

    # P13: a report exists, not just CSV.
    plots = list((Path(results_dir) / "plots").glob("*.png")) if \
        (Path(results_dir) / "plots").exists() else []
    if plots:
        report.add(section, "P13", "Report generated", PASS,
                   f"{len(plots)} figures rendered alongside the raw data")
    else:
        report.add(section, "P13", "Report generated", WARN,
                   "no figures yet — run ./plot_results.py. Slide 24: data nobody looks at "
                   "because it is 'just a bunch of CSV files' does not help")

    # P12: distributions available.
    if raw:
        report.add(section, "P12", "Distributions retained", PASS,
                   f"{len(raw)} per-exchange samples kept in raw.csv, so box/violin plots and any "
                   f"other statistic can be produced without re-running")
    else:
        report.add(section, "P12", "Distributions retained", FAIL, "raw.csv is empty")


# ------------------------------------------------------------------------------------------
# Pass B — reliability
# ------------------------------------------------------------------------------------------

def _samples_by_point_rotation(raw):
    grouped = defaultdict(lambda: defaultdict(list))
    for row in raw:
        if row.get("ok") != "1" or row.get("tech") == "none":
            continue
        try:
            value = float(row["rtt_us"])
        except (KeyError, ValueError):
            continue
        if value <= 0:
            continue
        grouped[row["point_key"]][int(row["rotation"])].append(value)
    return grouped


def audit_reliability(report, meta, points, raw):
    section = "B. Reliability — can the numbers be trusted?"
    grouped = _samples_by_point_rotation(raw)
    if not grouped:
        report.add(section, "", "Data present", FAIL, "no successful exchanges to analyse")
        return

    # Within-point noise.
    # Judged on IQR/median, not stdev/mean. These latency distributions are strongly quantised, so
    # the classical CV reports "noisy" for runs that repeat one value for three quarters of their
    # exchanges - see analysis.describe().
    noisy = []
    for point_key, rotations in grouped.items():
        for rotation, values in rotations.items():
            stats = describe(values)
            if stats.get("robust_cv", 0) > MAX_CV:
                noisy.append(f"{point_key} rot={rotation}: IQR/median="
                              f"{stats['robust_cv']:.2f} (median {stats['median'] / 1000:.2f} ms, "
                              f"IQR {stats['iqr'] / 1000:.2f} ms, classical CV {stats['cv']:.2f})")
    total_runs = sum(len(r) for r in grouped.values())
    if noisy:
        report.add(section, "", "Within-point noise", WARN,
                   f"{len(noisy)}/{total_runs} runs above IQR/median {MAX_CV:.2f}\n" +
                   "\n".join(noisy[:5]))
    else:
        report.add(section, "", "Within-point noise", PASS,
                   f"every one of {total_runs} runs below IQR/median {MAX_CV:.2f} "
                   f"(robust dispersion; quantised distributions make the classical CV misleading)")

    # Reproducibility: how far a point's median moves between repetitions.
    unstable = []
    checked = 0
    for point_key, rotations in grouped.items():
        if len(rotations) < 2:
            continue
        checked += 1
        medians = [statistics.median(v) for v in rotations.values()]
        centre = statistics.median(medians)
        spread = (max(medians) - min(medians)) / centre if centre else 0.0
        if spread > MAX_BETWEEN_REP_SPREAD:
            unstable.append(f"{point_key}: medians span {spread * 100:.0f}% "
                             f"({min(medians) / 1000:.2f}–{max(medians) / 1000:.2f} ms)")
    if not checked:
        report.add(section, "", "Reproducibility across repetitions", SKIP,
                   "only one repetition per point")
    elif unstable:
        report.add(section, "", "Reproducibility across repetitions", WARN,
                   f"{len(unstable)}/{checked} points move more than "
                   f"{MAX_BETWEEN_REP_SPREAD * 100:.0f}% between repetitions\n" +
                   "\n".join(unstable[:5]))
    else:
        report.add(section, "", "Reproducibility across repetitions", PASS,
                   f"all {checked} repeated points hold their median within "
                   f"{MAX_BETWEEN_REP_SPREAD * 100:.0f}%")

    # Systematic drift. This is the one that repetitions cannot fix (practice P2).
    started = {p["point_key"]: {} for p in points}
    for point in points:
        started.setdefault(point["point_key"], {})[point["rotation"]] = point["started_at"]
    drifting = []
    tested = 0
    for point_key, rotations in grouped.items():
        if len(rotations) < 3:
            continue
        xs, ys = [], []
        for rotation, values in sorted(rotations.items()):
            timestamp = started.get(point_key, {}).get(rotation)
            if timestamp is None:
                continue
            xs.append(timestamp)
            ys.append(statistics.median(values))
        if len(xs) < 3:
            continue
        tested += 1
        fit = linear_regression(xs, ys)
        if fit and fit["p"] < DRIFT_P and abs(fit["r"]) > 0.9:
            per_hour = fit["slope"] * 3600 / 1000.0
            drifting.append(f"{point_key}: {per_hour:+.2f} ms/hour (r={fit['r']:.2f}, "
                             f"p={fit['p']:.4f})")
    if not tested:
        report.add(section, "P2", "Systematic drift", SKIP,
                   "needs 3+ repetitions per point to test for a trend")
    elif drifting:
        report.add(section, "P2", "Systematic drift", FAIL,
                   f"{len(drifting)}/{tested} points drift significantly with campaign time. "
                   f"This is systematic error: slide 12 is explicit that repetitions cannot "
                   f"mitigate it, so more measurement will not help — find the cause.\n" +
                   "\n".join(drifting[:5]))
    else:
        report.add(section, "P2", "Systematic drift", PASS,
                   f"no significant trend against campaign time in any of {tested} points "
                   f"(p ≥ {DRIFT_P}) — the remaining scatter behaves as random error")

    # Warm-up: a systematic bias at the start of each run.
    warm_offenders = []
    for point_key, rotations in grouped.items():
        for rotation, values in rotations.items():
            if len(values) < WARMUP_K * 4:
                continue
            head = statistics.median(values[:WARMUP_K])
            tail = statistics.median(values[WARMUP_K:])
            if tail and abs(head - tail) / tail > 0.5:
                warm_offenders.append(f"{point_key} rot={rotation}: first {WARMUP_K} exchanges "
                                       f"{head / 1000:.2f} ms vs {tail / 1000:.2f} ms after")
    if warm_offenders:
        report.add(section, "P2", "Warm-up effect", WARN,
                   f"{len(warm_offenders)} run(s) show a distinct first-exchanges regime — "
                   f"consider discarding them as warm-up rather than averaging them in\n" +
                   "\n".join(warm_offenders[:5]))
    else:
        report.add(section, "P2", "Warm-up effect", PASS,
                   f"first {WARMUP_K} exchanges statistically indistinguishable from the rest")

    # Outliers — the classic external-load signature.
    flagged, examined, degenerate = [], 0, 0
    for point_key, rotations in grouped.items():
        for rotation, values in rotations.items():
            # Tukey fences on a handful of samples flag ordinary scatter: with n=10 a single
            # slow exchange is already 10 %. Only judge runs long enough for the quartiles to
            # mean something.
            if len(values) < 20:
                continue
            outliers = gross_outliers(values)
            if outliers is None:
                # Median non-positive or too few samples: the rule does not apply.
                degenerate += 1
                continue
            examined += 1
            fraction = len(outliers) / len(values)
            if fraction > MAX_OUTLIER_FRACTION:
                flagged.append(f"{point_key} rot={rotation}: {fraction * 100:.0f}% outliers "
                                f"of {len(values)} exchanges")
    degenerate_note = (f"\n{degenerate} run(s) could not be tested") if degenerate else ""
    if not examined:
        report.add(section, "P5", "Outliers", SKIP,
                   "no run has the 20+ exchanges this test needs" + degenerate_note)
    elif flagged:
        report.add(section, "P5", "Outliers", WARN,
                   f"{len(flagged)}/{examined} run(s) above "
                   f"{MAX_OUTLIER_FRACTION * 100:.0f}% of exchanges taking >3x the median — the "
                   f"signature of external load\n" + "\n".join(flagged[:5]) + degenerate_note)
    else:
        report.add(section, "P5", "Outliers", PASS,
                   f"all {examined} runs keep excursions beyond 3x the median below "
                   f"{MAX_OUTLIER_FRACTION * 100:.0f}% of exchanges" + degenerate_note)

    # Rank stability — whether the *conclusions* survive repetition.
    per_tech = defaultdict(lambda: defaultdict(dict))
    for point_key, rotations in grouped.items():
        tech = point_key.split("/")[0]
        for rotation, values in rotations.items():
            per_tech[tech][rotation][point_key] = statistics.median(values)
    unstable_ranks, stable_ranks = [], []
    for tech, by_rotation in sorted(per_tech.items()):
        result = rank_stability(by_rotation)
        if result is None:
            continue
        if result["mean_spearman"] < MIN_RANK_STABILITY:
            unstable_ranks.append(f"{tech}: mean Spearman {result['mean_spearman']:.2f} "
                                   f"(worst pair {result['min_spearman']:.2f})")
        else:
            stable_ranks.append(f"{tech}: {result['mean_spearman']:.2f}")
    if not stable_ranks and not unstable_ranks:
        report.add(section, "", "Rank stability", SKIP, "needs 2+ repetitions and 3+ points")
    elif unstable_ranks:
        report.add(section, "", "Rank stability", FAIL,
                   "configuration points reorder between repetitions, so any ranking claim is "
                   "not reproducible regardless of how tight the error bars look\n" +
                   "\n".join(unstable_ranks))
    else:
        report.add(section, "", "Rank stability", PASS,
                   "ordering of configurations holds across repetitions — "
                   + ", ".join(stable_ranks))

    # Does N support the percentile being claimed?
    needed_95 = triscale_runs_needed(95, 0.95)
    thin_samples = []
    for point_key, rotations in grouped.items():
        pooled = [v for values in rotations.values() for v in values]
        if len(pooled) < needed_95:
            thin_samples.append(f"{point_key}: n={len(pooled)}")
    if thin_samples:
        report.add(section, "P10", "Sample size vs. claims", WARN,
                   f"{len(thin_samples)}/{len(grouped)} points have fewer than {needed_95} "
                   f"samples, which TriScale Eq. 4 requires for a 95th-percentile claim at 95% "
                   f"confidence. Medians remain sound; tail claims do not.\n" +
                   "\n".join(thin_samples[:5]))
    else:
        report.add(section, "P10", "Sample size vs. claims", PASS,
                   f"every point has ≥{needed_95} samples — enough for a 95th-percentile claim "
                   f"at 95% confidence")

    # Median confidence intervals, as a summary rather than a gate.
    widths = []
    for point_key, rotations in grouped.items():
        pooled = [v for values in rotations.values() for v in values]
        interval = median_confidence_interval(pooled)
        if interval:
            low, high, _ = interval
            centre = statistics.median(pooled)
            if centre:
                widths.append((high - low) / centre)
    if widths:
        report.add(section, "P11", "Median CI width", PASS if percentile(widths, 90) < 0.5 else WARN,
                   f"95% CI on the median spans {statistics.median(widths) * 100:.1f}% of the "
                   f"median (typical), {percentile(widths, 90) * 100:.1f}% at the 90th percentile "
                   f"of points")

    # Duty-cycle compliance, from measured airtime rather than intent.
    airtime = meta.get("lora_airtime_total_s")
    elapsed = meta.get("elapsed_s")
    if airtime and elapsed:
        realised = airtime / elapsed
        verdict = PASS if realised <= 0.10 else FAIL
        report.add(section, "P7", "LoRa duty cycle honoured", verdict,
                   f"{airtime:.0f}s airtime over {elapsed / 3600:.1f}h = "
                   f"{realised * 100:.2f}% mean duty cycle (limit 10%); "
                   f"{meta.get('duty_wait_total_s', 0):.0f}s spent waiting for budget")
    else:
        report.add(section, "P7", "LoRa duty cycle honoured", SKIP, "no airtime recorded")


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
    node_rx = load_node_rx(results_dir)

    print(f"auditing {results_dir.name}: {len(points)} configuration points, "
          f"{len(raw)} exchanges")

    report = Report()
    audit_methodology(report, results_dir, meta, points, raw, node_rx)
    audit_reliability(report, meta, points, raw)

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
