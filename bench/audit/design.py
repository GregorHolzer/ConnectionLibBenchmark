"""Pass A, second half: was the experiment designed and reported properly?

Where environment.py asks whether the bench was sound, these checks ask whether the *campaign*
was: repetitions spaced rather than run back to back, enough of them, the input space actually
swept, the deployed configuration measured rather than a relaxed one, and the results kept in a
form somebody can look at. Each cites the practice it enforces.
"""

import statistics
from collections import defaultdict
from pathlib import Path

from .report import FAIL, MIN_REPETITIONS, PASS, SKIP, WARN


def check_interleaving(report, section, points):
    """P9: interleaving. Reconstructed from the recorded order, not taken on trust."""
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


def check_repetitions(report, section, points):
    """P10: enough repetitions."""
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


def check_input_sweep(report, section, points):
    """P8: input data swept."""
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


def check_deployed_configuration(report, section, points):
    """P7: measured in the deployed configuration — the EU envelope, unrelaxed."""
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


def check_latency_vs_throughput(report, section, raw):
    """P1: latency and throughput measured independently."""
    has_rtt = any(r.get("rtt_us") for r in raw)
    has_send = any(r.get("send_us") for r in raw)
    if has_rtt and has_send:
        report.add(section, "P1", "Latency vs throughput", PASS,
                   "rtt_us and send_us recorded separately; goodput computed over wall clock, "
                   "not derived from latency")
    else:
        report.add(section, "P1", "Latency vs throughput", WARN, "timing columns incomplete")


def check_coverage(report, section, meta, points):
    """P14: benchmark coverage."""
    space = meta.get("configuration_space") or {}
    if not space:
        report.add(section, "P14", "Benchmark coverage", SKIP, "no configuration space recorded")
        return
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


def check_report_generated(report, section, results_dir):
    """P13: a report exists, not just CSV."""
    plots = list((Path(results_dir) / "plots").glob("*.png")) if \
        (Path(results_dir) / "plots").exists() else []
    if plots:
        report.add(section, "P13", "Report generated", PASS,
                   f"{len(plots)} figures rendered alongside the raw data")
    else:
        report.add(section, "P13", "Report generated", WARN,
                   "no figures yet — run ./plot_results.py. Slide 24: data nobody looks at "
                   "because it is 'just a bunch of CSV files' does not help")


def check_distributions(report, section, raw):
    """P12: distributions available."""
    if raw:
        report.add(section, "P12", "Distributions retained", PASS,
                   f"{len(raw)} per-exchange samples kept in raw.csv, so box/violin plots and any "
                   f"other statistic can be produced without re-running")
    else:
        report.add(section, "P12", "Distributions retained", FAIL, "raw.csv is empty")


def run(report, section, results_dir, meta, points, raw):
    check_interleaving(report, section, points)
    check_repetitions(report, section, points)
    check_input_sweep(report, section, points)
    check_deployed_configuration(report, section, points)
    check_latency_vs_throughput(report, section, raw)
    check_coverage(report, section, meta, points)
    check_report_generated(report, section, results_dir)
    check_distributions(report, section, raw)
