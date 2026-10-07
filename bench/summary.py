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


def _energy(runs, delivered, sent, standing_ua):
    """Energy of the active windows, absolute and above the radio's own standing current.

    This is the sender's energy and it includes waiting for acks that never came, so the
    per-delivered figure rises as the link degrades - which is what a deployed node would pay.
    """
    windows = [run["energy"]["run"] for run in runs if (run.get("energy") or {}).get("run")]
    if not windows:
        return {}
    energy_mj = sum(w["energy_mj"] for w in windows)
    duration_s = sum(w["duration_s"] for w in windows)
    mean_ua = sum(w["mean_ua"] * w["duration_s"] for w in windows) / duration_s
    out = {"mean_ua": mean_ua, "energy_per_sent_mj": _ratio(energy_mj, sent),
           "energy_per_delivered_mj": _ratio(energy_mj, delivered)}
    if standing_ua is not None:
        excess_mj = max(mean_ua - standing_ua, 0.0) * duration_s / 1000.0 \
            * windows[0]["voltage_mv"] / 1000.0
        out["excess_energy_per_delivered_mj"] = _ratio(excess_mj, delivered)
    return out


def _mean(values):
    return sum(values) / len(values) if values else None


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
        summaries = [run["summary"] for run in point_runs if run["summary"]]
        sent = sum(s["sent"] for s in summaries)
        acked = sum(s["acked"] for s in summaries)
        elapsed_s = sum(s["elapsed_ms"] for s in summaries) / 1000.0
        # The gateway's own count, only from runs whose report came back.
        reported = [run for run in point_runs if run["summary"] and run["report"]
                    and run["report"]["received"] is not None]
        reports = [run["report"] for run in reported]
        first = point_runs[0]
        latency = _numbers([row for row in rows if row["acked"] == "1"], "latency_us")
        out.append({
            "distance_m": distance, "key": key, "tech": first["tech"], "name": first["name"],
            "params": first["params"], "rounds": len(point_runs),
            "sent": sent, "acked": acked,
            "pdr": _ratio(acked, sent),
            "gateway_pdr": _ratio(min(sum(r["received"] for r in reports),
                                      sum(run["summary"]["sent"] for run in reported)),
                                  sum(run["summary"]["sent"] for run in reported)),
            "latency_us": describe(latency),
            "latency_median_ci_us": median_confidence_interval(latency),
            # One-way goodput of a back-to-back burst: confirmed payload bits over wall clock.
            "goodput_bps": _ratio(acked * first["params"].get("payload", 32) * 8, elapsed_s),
            "framing_efficiency": _ratio(sum(_numbers(rows, "payload_bytes")),
                                         sum(_numbers(rows, "total_bytes"))),
            "gw_rssi_dbm": _mean([r["rssi_dbm"] for r in reports if r["rssi_dbm"] is not None]),
            "gw_snr_db": _mean([r["snr_db"] for r in reports if r["snr_db"] is not None]),
            "duty_pct": max(_numbers(rows, "duty_pct"), default=None),
            "energy": _energy(point_runs, acked, sent, standing.get(first["tech"])),
        })
    return out


def table(rows):
    """Plain-text table of the headline metrics, for the console."""
    def cell(value, spec):
        return "-" if value is None else format(value, spec)

    lines = [f"{'dist':>6} {'config':<22} {'sent':>5} {'acked':>6} {'at gw':>6} "
             f"{'lat ms':>8} {'bit/s':>8} {'mJ/msg':>8} {'gw rssi':>8}"]
    for row in rows:
        latency = row["latency_us"].get("median")
        lines.append(
            f"{row['distance_m']:>6g} {row['key']:<22} {row['sent']:>5} "
            f"{cell(row['pdr'], '.0%'):>6} {cell(row['gateway_pdr'], '.0%'):>6} "
            f"{cell(None if latency is None else latency / 1000, '.1f'):>8} "
            f"{cell(row['goodput_bps'], '.0f'):>8} "
            f"{cell(row['energy'].get('energy_per_delivered_mj'), '.2f'):>8} "
            f"{cell(row['gw_rssi_dbm'], '.0f'):>8}")
    return "\n".join(lines)
