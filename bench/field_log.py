"""Turning the field gateway's flash log back into a campaign's results.

The gateway appends one `FLP` record per lease and one `FLB` per boot (src/FieldGateway.cpp). Its
log outlives campaigns, so records are matched to this one by campaign id, and to points by point
id, which the field runner sets to the run index.

The output is node_rx.csv - the same file the bench fills live from the node - so the analysis
counts node->gateway delivery in the field exactly as it counts it on the bench. Only sequence
numbers survive the trip through the bitmap: the size columns come from the point's parameters,
and signal quality is left empty (docs/field-campaign.md says why).
"""

import csv
import json
from collections import defaultdict
from pathlib import Path

from .records import NODE_COLUMNS

LEASE_COLUMNS = [
    "run_index", "point_key", "boot", "tech", "stack", "lease_ms", "held_ms", "reason",
    "rx_distinct", "rx_total", "replies_ok", "reply_failures", "duty_dropped",
]


def bitmap_seqs(hex_words):
    """Sequence numbers set in the bitmap: word i is 8 hex digits, bit b of it is 32 * i + b."""
    seqs = []
    for i in range(len(hex_words) // 8):
        word = int(hex_words[8 * i:8 * i + 8], 16)
        seqs.extend(32 * i + bit for bit in range(32) if word >> bit & 1)
    return seqs


def parse_log(lines):
    """(boots, leases) from the raw log lines. Anything that doesn't parse is skipped."""
    boots, leases = [], []
    for line in lines:
        fields = line.strip().split(",")
        try:
            if fields[0] == "FLB" and len(fields) >= 3:
                boots.append({"boot": int(fields[1]), "reset_reason": int(fields[2])})
            elif fields[0] == "FLP" and len(fields) >= 15:
                leases.append({
                    "boot": int(fields[1]),
                    "campaign_id": fields[2].lower(),
                    "point_id": int(fields[3]),
                    "tech": fields[4],
                    "stack": fields[5],
                    "lease_ms": int(fields[6]),
                    "held_ms": int(fields[7]),
                    "reason": fields[8],
                    "rx_distinct": int(fields[9]),
                    "rx_total": int(fields[10]),
                    "replies_ok": int(fields[11]),
                    "reply_failures": int(fields[12]),
                    "duty_dropped": int(fields[13]),
                    "seqs": bitmap_seqs(fields[14]),
                })
        except (ValueError, IndexError):
            continue
    return boots, leases


def _load_points(results_dir):
    points = {}
    with (Path(results_dir) / "points.jsonl").open() as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            points[record["run_index"]] = record
    return points


def rebuild_node_rx(results_dir, log_lines):
    """Rewrite node_rx.csv and write gateway_leases.csv from the gateway's log. Returns a summary.

    Rewrites rather than appends, so fetching the log twice gives the same files. A point leased
    more than once - an ack lost, the lease retried - gets the union of its leases' arrivals: a
    sequence number is unique within a point, so a retried lease can only add arrivals it really
    saw.
    """
    results_dir = Path(results_dir)
    meta = json.loads((results_dir / "meta.json").read_text())
    campaign_id = meta["field"]["campaign_id"].lower()
    points = _load_points(results_dir)
    boots, leases = parse_log(log_lines)

    ours = [lease for lease in leases if lease["campaign_id"] == campaign_id]
    arrivals = defaultdict(set)
    for lease in ours:
        arrivals[lease["point_id"]].update(lease["seqs"])

    with (results_dir / "node_rx.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(NODE_COLUMNS)
        for run_index in sorted(points):
            point = points[run_index]
            if point["factor"] == "idle" or run_index not in arrivals:
                continue
            prefix = [run_index, point["rotation"], point["tech"], point["factor"], point["level"],
                      point["point_key"]]
            payload = point["params"].get("payload", "")
            for seq in sorted(arrivals[run_index]):
                writer.writerow(prefix + [seq, payload, "", "", ""])

    with (results_dir / "gateway_leases.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(LEASE_COLUMNS)
        for lease in ours:
            point = points.get(lease["point_id"], {})
            writer.writerow([lease["point_id"], point.get("point_key", "")] +
                            [lease[column] for column in LEASE_COLUMNS[2:]])

    measured = {i for i, p in points.items() if p["factor"] != "idle"}
    campaign_boots = sorted({lease["boot"] for lease in ours})
    reboots = [b for b in boots
               if campaign_boots and campaign_boots[0] < b["boot"] <= campaign_boots[-1]]
    reasons = defaultdict(int)
    for lease in ours:
        reasons[lease["reason"]] += 1
    return {
        "leases": len(ours),
        "points_with_lease": len(measured & set(arrivals)),
        "points_without_lease": sorted(measured - set(arrivals)),
        "lease_endings": dict(reasons),
        "reboots": reboots,
        "arrivals": sum(len(seqs) for seqs in arrivals.values()),
    }
