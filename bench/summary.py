"""From recorded runs to the reported metrics: one row per (distance, configuration).

Rounds at the same distance are pooled. Single numbers are medians with a spread (practice P11);
delivery is counted, never averaged. What each metric means is in docs/metrics.md.
"""

from collections import defaultdict

from .analysis import describe, median_confidence_interval


def standing_currents(runs):
    """{tech: mean µA} from the baseline holds; "none" is the board with every radio down."""
    out = {}
    for run in runs:
        idle = (run.get("energy") or {}).get("idle")
        if run["exchanges_planned"] == 0 and idle:
            out[run["tech"]] = idle["mean_ua"]
    return out


def _numbers(rows, field):
    return [float(row[field]) for row in rows if row.get(field) not in (None, "")]


def _ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def _energy(runs, answered, sent, standing_ua):
    """Energy of the active windows, absolute and above the radio's own standing current.

    This is the *initiator's* energy and it includes waiting for pongs that never came, so the
    per-delivered figure rises as the link degrades - which is what a deployed node would pay.
    """
    windows = [run["energy"]["run"] for run in runs if (run.get("energy") or {}).get("run")]
    if not windows:
        return {}
    energy_mj = sum(w["energy_mj"] for w in windows)
    duration_s = sum(w["duration_s"] for w in windows)
    mean_ua = sum(w["mean_ua"] * w["duration_s"] for w in windows) / duration_s
    out = {"mean_ua": mean_ua, "energy_per_ping_mj": _ratio(energy_mj, sent),
           "energy_per_delivered_mj": _ratio(energy_mj, answered)}
    if standing_ua is not None:
        excess_mj = max(mean_ua - standing_ua, 0.0) * duration_s / 1000.0 \
            * windows[0]["voltage_mv"] / 1000.0
        out["excess_energy_per_delivered_mj"] = _ratio(excess_mj, answered)
    return out


def summarise(runs, exchanges):
    """One dict per (distance_m, key), sorted by distance then key. Baseline holds are excluded."""
    standing = standing_currents(runs)
    by_point = defaultdict(list)
    for run in runs:
        if run["exchanges_planned"] and run["distance_m"] is not None:
            by_point[(run["distance_m"], run["key"])].append(run)
    rows_by_point = defaultdict(list)
    for row in exchanges:
        if row["distance_m"]:
            rows_by_point[(float(row["distance_m"]), row["key"])].append(row)

    out = []
    for (distance, key), point_runs in sorted(by_point.items()):
        rows = rows_by_point[(float(distance), key)]
        answered_rows = [row for row in rows if row["ok"] == "1"]
        summaries = [run["summary"] for run in point_runs if run["summary"]]
        sent = sum(s["sent"] for s in summaries)
        answered = sum(s["answered"] for s in summaries)
        # A run whose pongs all went missing never learnt the gateway's count: leave it out of
        # the per-direction figures rather than count it as zero uplink.
        heard = [s for s in summaries if s["answered"] or s["gw_rx"]]
        gateway_rx = sum(s["gw_rx"] for s in heard)
        elapsed_s = sum(s["elapsed_ms"] for s in summaries) / 1000.0
        first = point_runs[0]
        rtt = _numbers(answered_rows, "rtt_us")
        home = [run["home"]["rssi_dbm"] for run in point_runs
                if run["home"] and run["home"].get("rssi_dbm") is not None]
        out.append({
            "distance_m": distance, "key": key, "tech": first["tech"], "name": first["name"],
            "params": first["params"], "rounds": len(point_runs),
            "reached": any(run["complete"] and not run["error"] for run in point_runs),
            "sent": sent, "answered": answered,
            "round_trip_pdr": _ratio(answered, sent),
            "uplink_pdr": _ratio(min(gateway_rx, sum(s["sent"] for s in heard)),
                                 sum(s["sent"] for s in heard)),
            "downlink_pdr": _ratio(min(sum(s["answered"] for s in heard), gateway_rx), gateway_rx),
            "rtt_us": describe(rtt),
            "rtt_median_ci_us": median_confidence_interval(rtt),
            "send_us": describe(_numbers(rows, "send_us")),
            "ack_rate": _ratio(sum(1 for row in rows if row["acked"] == "1"), len(rows)),
            # One-way goodput of a back-to-back burst: confirmed payload bits over wall clock.
            "goodput_bps": _ratio(answered * first["params"].get("payload", 32) * 8, elapsed_s),
            "framing_efficiency": _ratio(sum(_numbers(rows, "payload_bytes")),
                                         sum(_numbers(rows, "total_bytes"))),
            "rssi_dbm": describe(_numbers(answered_rows, "rssi_dbm")),
            "snr_db": describe(_numbers(answered_rows, "snr_db")),
            "gw_rssi_dbm": describe(_numbers(answered_rows, "gw_rssi_dbm")),
            "gw_snr_db": describe(_numbers(answered_rows, "gw_snr_db")),
            "home_rssi_dbm": describe(home),
            "duty_pct": max(_numbers(rows, "duty_pct"), default=None),
            "energy": _energy(point_runs, answered, sent, standing.get(first["tech"])),
        })
    return out


def table(rows):
    """Plain-text table of the headline metrics, for the console."""
    def pct(value):
        return "   -" if value is None else f"{value:4.0%}"

    lines = [f"{'dist':>6} {'config':<22} {'sent':>5} {'rt':>4} {'up':>4} {'down':>4} "
             f"{'rtt ms':>8} {'mJ/msg':>8} {'rssi':>6}"]
    for row in rows:
        rtt = row["rtt_us"].get("median")
        energy = row["energy"].get("energy_per_delivered_mj")
        rssi = row["rssi_dbm"].get("median")
        lines.append(
            f"{row['distance_m']:>6g} {row['key']:<22} {row['sent']:>5} "
            f"{pct(row['round_trip_pdr'])} {pct(row['uplink_pdr'])} {pct(row['downlink_pdr'])} "
            f"{'-' if rtt is None else format(rtt / 1000, '.1f'):>8} "
            f"{'-' if energy is None else format(energy, '.2f'):>8} "
            f"{'-' if rssi is None else format(rssi, '.0f'):>6}")
    return "\n".join(lines)
