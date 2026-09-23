"""Pass B, first half: is the scatter random, or is something moving?

The distinction driving these four checks is the deck's central one - *random* error can be beaten
down with repetitions, *systematic* error cannot. So noise and reproducibility are reported as
warnings, while a drift against campaign wall-clock time is a FAIL: more measurement will not fix
it, and averaging it away hides it.
"""

import statistics
from collections import defaultdict

from ..analysis import describe, linear_regression
from .report import (DRIFT_P, FAIL, MAX_BETWEEN_REP_SPREAD, MAX_CV, PASS, SKIP, WARMUP_K, WARN)


def samples_by_point_rotation(raw):
    """{point_key: {rotation: [rtt_us]}} for successful exchanges of a real technology.

    Deliberately not bench.analysis.group_exchanges(): that one keeps tech="none" rows, which are
    the null measurement (practice P4). Letting harness-only timings into the reliability pass
    would compare the instrument against itself.
    """
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


def check_within_point_noise(report, section, grouped):
    """Judged on IQR/median, not stdev/mean.

    These latency distributions are strongly quantised, so the classical CV reports "noisy" for
    runs that repeat one value for three quarters of their exchanges - see analysis.describe().
    """
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


def check_reproducibility(report, section, grouped):
    """How far a point's median moves between repetitions."""
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


def check_drift(report, section, grouped, points):
    """P2: systematic drift. This is the one that repetitions cannot fix."""
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


def check_warmup(report, section, grouped):
    """P2: a systematic bias at the start of each run."""
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


def run(report, section, grouped, points):
    check_within_point_noise(report, section, grouped)
    check_reproducibility(report, section, grouped)
    check_drift(report, section, grouped, points)
    check_warmup(report, section, grouped)
