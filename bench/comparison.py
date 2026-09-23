"""Cross-technology comparison: the RQ1 metric set, pooled per configuration point.

`analysis.py` answers "what do this radio's knobs do". This module answers the different question
the thesis asks in RQ2 - how the three compare to *each other* - and so it aggregates along a
different axis: one record per configuration point, pooled over every repetition, carrying the
metrics the exposé's RQ1 set commits to rather than latency alone.

Packet delivery ratio and goodput are computed here for the first time. `docs/metrics.md` defines
both, but nothing in the tree produced them: `analysis.summarise_points` yields `rtt_us` and
`send_us` only.

The two measurements that need care live next door in standing.py (the board floor, and the
duty-cycle ceiling); picking which record to show lives in selection.py.
"""

import statistics
from collections import defaultdict

from .analysis import describe
from .standing import _msgs_per_hour, board_floor_ua


def aggregate(points, raw, node_rx, floor_mode="cold"):
    """One record per (tech, point_key), pooled over rotations.

    Pooling over rotations rather than reporting each separately is what makes the cross-technology
    comparison legible; the per-repetition view already exists in the reliability figures.
    """
    floor = board_floor_ua(points, floor_mode)

    # Distinct sequence numbers seen *at the receiver*. records.py calls this the only honest place
    # to count delivery: a sender-side figure means something different on each of the three radios.
    arrivals = defaultdict(set)
    for row in node_rx:
        arrivals[(row["point_key"], int(row["rotation"]))].add(row["seq"])

    rtts = defaultdict(list)
    sends = defaultdict(list)
    payload_bits = defaultdict(float)
    framing = defaultdict(list)
    # Signal quality from both ends: raw.csv carries the reply as the gateway heard it, node_rx.csv
    # the request as the node heard it. Kept separate rather than pooled, because these links are
    # asymmetric - a Zigbee gateway broadcast and the node's unicast reply are not the same path.
    rssi = defaultdict(list)
    snr = defaultdict(list)
    node_rssi = defaultdict(list)
    node_snr = defaultdict(list)
    for row in node_rx:
        key = row["point_key"]
        for column, sink in (("rssi_dbm", node_rssi), ("snr_db", node_snr)):
            if not row.get(column):
                continue
            try:
                value = float(row[column])
            except ValueError:
                continue
            # A received power at or above 0 dBm cannot come from a 7 dBm transmitter over the
            # air. records.py documents why such rows exist at all: the Zigbee path interleaves
            # raw XBee API frame bytes into the serial stream, so a line can parse into a number
            # that was never a measurement. Dropping them here keeps one artifact from setting the
            # scale of a whole figure.
            if column == "rssi_dbm" and value >= 0:
                continue
            sink[key].append(value)
    for row in raw:
        key = row["point_key"]
        try:
            if row.get("ok") == "1":
                rtts[key].append(float(row["rtt_us"]) / 1000.0)
                payload_bits[key] += float(row["payload_bytes"]) * 8
            sends[key].append(float(row["send_us"]) / 1000.0)
            total = float(row["total_bytes"])
            if total > 0:
                framing[key].append(float(row["payload_bytes"]) / total)
        except (KeyError, ValueError):
            continue
        # Empty is the normal case, not an error: only LoRa reports these without being asked,
        # and the other two only when the campaign ran with rssi capture enabled.
        for column, sink in (("rssi_dbm", rssi), ("snr_db", snr)):
            if row.get(column):
                try:
                    sink[key].append(float(row[column]))
                except ValueError:
                    pass

    pooled = defaultdict(lambda: {"attempts": 0, "successes": 0, "retries": 0, "delivered": 0,
                                   "elapsed_s": 0.0, "energy_mj": 0.0, "charge_mc": 0.0,
                                   "duration_s": 0.0, "floor_mj": 0.0, "rotations": set()})
    meta = {}
    for point in points:
        summary = point.get("summary") or {}
        if summary.get("idle") or point["tech"] == "none":
            continue
        key = point["point_key"]
        meta.setdefault(key, point)
        acc = pooled[key]
        acc["attempts"] += summary.get("attempts", 0)
        acc["successes"] += summary.get("successes", 0)
        acc["retries"] += summary.get("retries", 0)
        acc["delivered"] += len(arrivals.get((key, point["rotation"]), ()))
        acc["elapsed_s"] += summary.get("elapsed_ms", 0) / 1000.0
        acc["rotations"].add(point["rotation"])
        energy = point.get("energy")
        if energy:
            acc["energy_mj"] += energy["energy_mj"]
            acc["duration_s"] += energy["duration_s"]
            base = floor.get(point["rotation"])
            if base is not None:
                # What the same window would have cost with every radio down. Subtracting it leaves
                # the radio's own share, which is the quantity the absolute figure inflates: the
                # PPK2 taps upstream of the AXP2101, so the ESP32, the PMU loss and the USB bridge
                # sit under every reading alike.
                volts = energy.get("voltage_mv", 0) / 1000.0
                acc["floor_mj"] += base * energy["duration_s"] / 1000.0 * volts

    records = []
    for key, acc in sorted(pooled.items()):
        point = meta[key]
        attempts, successes = acc["attempts"], acc["successes"]
        record = {
            "tech": point["tech"],
            "point_key": key,
            "factor": point.get("factor", ""),
            "level": point.get("level", ""),
            "params": point.get("params", {}),
            "rotations": len(acc["rotations"]),
            "attempts": attempts,
            "successes": successes,
            "retries": acc["retries"],
            "delivered": acc["delivered"],
            "pdr": acc["delivered"] / attempts if attempts else None,
            "round_trip_ratio": successes / attempts if attempts else None,
            "rtt_ms": describe(rtts.get(key, [])),
            "send_ms": describe(sends.get(key, [])),
            # Over the whole point against wall clock, never derived from RTT - practice P1. The
            # exchange happens to be strictly serial here, but deriving one from the other would
            # bake that in and stop being true the moment anything pipelines.
            "goodput_bps": payload_bits[key] / acc["elapsed_s"] if acc["elapsed_s"] else None,
            "framing_efficiency": statistics.median(framing[key]) if framing.get(key) else None,
            "reply_rssi_dbm": describe(rssi.get(key, [])),
            "reply_snr_db": describe(snr.get(key, [])),
            "node_rssi_dbm": describe(node_rssi.get(key, [])),
            "node_snr_db": describe(node_snr.get(key, [])),
            "stack": (point.get("params") or {}).get("stack", "lib"),
            "max_msgs_per_hour": _msgs_per_hour(point["tech"], point.get("params", {})),
            "payload_bytes": point.get("params", {}).get("payload"),
        }
        if successes and acc["energy_mj"]:
            record["energy_per_delivered_mj"] = acc["energy_mj"] / successes
            record["radio_energy_per_delivered_mj"] = (acc["energy_mj"] - acc["floor_mj"]) / successes
            record["mean_current_ua"] = (acc["energy_mj"] / acc["duration_s"] * 1000.0
                                          / 5.0) if acc["duration_s"] else None
        records.append(record)
    return records

