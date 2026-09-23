"""Pass A, first half: was the measurement environment itself sound?

These four checks ask whether the numbers were taken under conditions that make them mean
anything - a release build, a good timer, bounded instrumentation cost, and no second radio
transmitting beside the one under test. They are the checks that invalidate a campaign outright
rather than merely weakening a claim, so they run first.
"""

import statistics

from .report import FAIL, MAX_OVERHEAD_FRACTION, PASS, SKIP, WARN


def check_release_build(report, section, info):
    """P6: release build, no debug instrumentation."""
    verdicts = []
    for role, facts in info.items():
        debug_level = facts.get("coreDebugLevel", "?")
        optimized = facts.get("optimized")
        verdicts.append((role, debug_level, optimized))
    if not verdicts:
        report.add(section, "P6", "Release build", SKIP, "no device_info recorded in meta.json")
        return
    bad = [f"{role}: coreDebugLevel={lvl}, optimized={opt}"
           for role, lvl, opt in verdicts if opt != "1" or lvl not in ("0", "?")]
    if bad:
        report.add(section, "P6", "Release build", FAIL,
                   "debug indicators present — the deck is explicit that measuring debug "
                   "builds is a non-representative scenario\n" + "\n".join(bad))
    else:
        report.add(section, "P6", "Release build", PASS,
                   ", ".join(f"{role}: optimized, no core debug" for role, _, _ in verdicts))


def check_timer(report, section, info):
    """P3: best available timer."""
    timers = {role: facts.get("timer") for role, facts in info.items()}
    if not timers:
        report.add(section, "P3", "Timer resolution", SKIP, "no device_info recorded")
    elif all(t == "micros" for t in timers.values()):
        report.add(section, "P3", "Timer resolution", PASS,
                   "micros() on both boards, 1 µs resolution")
    else:
        report.add(section, "P3", "Timer resolution", FAIL, f"unexpected timers: {timers}")


def check_overhead(report, section, raw):
    """P4: instrumentation overhead bounded. The null measurement (tech=none) gives the figure."""
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


def check_external_load(report, section, points):
    """P5: WiFi must be off whenever ESP-NOW is not the technology under test."""
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


def run(report, section, meta, points, raw):
    info = (meta.get("device_info") or {})
    check_release_build(report, section, info)
    check_timer(report, section, info)
    check_overhead(report, section, raw)
    check_external_load(report, section, points)
