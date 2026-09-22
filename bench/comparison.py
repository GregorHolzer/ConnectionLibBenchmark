"""Cross-technology comparison: the RQ1 metric set, pooled per configuration point.

`analysis.py` answers "what do this radio's knobs do". This module answers the different question
the thesis asks in RQ2 - how the three compare to *each other* - and so it aggregates along a
different axis: one record per configuration point, pooled over every repetition, carrying the
metrics the exposé's RQ1 set commits to rather than latency alone.

Packet delivery ratio and goodput are computed here for the first time. `docs/metrics.md` defines
both, but nothing in the tree produced them: `analysis.summarise_points` yields `rtt_us` and
`send_us` only.

Two measurements need care, and both are documented at their call sites below: the board floor
(which is contaminated after the first rotation in campaigns run before the `Tech::None` teardown
is fixed) and the duty cycle (whose raw column is not comparable across bands).
"""

import statistics
from collections import defaultdict

from . import technologies
from .analysis import describe

# ETSI Annex 1 for the 433 MHz band - see docs/regulations.md. The cap applies per transmitter over
# a rolling hour, so it converts directly into a ceiling on messages per hour.
LORA_DUTY_CYCLE = 0.10

# Technologies whose band carries no duty-cycle limit. Their ceiling is set by airtime alone, which
# is a different kind of quantity, so they are reported as uncapped rather than as a large number.
UNCAPPED = ("espnow", "zigbee")


def board_floor_ua(points, mode="cold"):
    """Mean current of the board with every radio down, per rotation.

    `mode="cold"` returns the rotation-0 floor for every rotation. That is not a convenience: in a
    campaign recorded before `Tech::None` tears the radio down (src/main.cpp), only the first floor
    point is a genuine cold-boot measurement. Every later one is taken after some radio has been
    initialised and never deinitialised, and reads roughly 10 mA high - high enough that Zigbee's
    *active* current falls below the "all radios off" floor, which cannot be true.

    `mode="per-rotation"` uses each rotation's own floor. Correct once the firmware resets the
    radio state, wrong before it, so it is not the default.
    """
    floors = {p["rotation"]: p["energy"]["mean_ua"]
              for p in points
              if p["tech"] == "none" and p.get("energy")}
    if not floors or mode == "per-rotation":
        return floors
    cold = floors[min(floors)]
    return {rotation: cold for rotation in floors}


def standing_currents(points, floor):
    """{tech: {"absolute_ua", "above_floor_ua"}} from the per-technology idle points.

    Above-floor is deliberately *not* clamped at zero. `analysis.point_energy` clamps, which turns
    the negative values a contaminated floor produces into plausible-looking zeros and hides the
    very problem the reader needs to see.
    """
    per_tech = defaultdict(list)
    for point in points:
        if point["tech"] == "none" or not (point.get("summary") or {}).get("idle"):
            continue
        if not point.get("energy"):
            continue
        base = floor.get(point["rotation"])
        per_tech[point["tech"]].append((point["energy"]["mean_ua"], base))

    out = {}
    for tech, rows in per_tech.items():
        rows.sort()
        absolute = [value for value, _ in rows]
        above = [value - base for value, base in rows if base is not None]
        out[tech] = {
            "absolute_ua": statistics.median(absolute),
            "above_floor_ua": statistics.median(above) if above else None,
            # Every repetition kept, because the median hides the thing worth seeing: in a campaign
            # with the teardown bug, ESP-NOW's standing point reads 141 mA in rotation 0 and 152 mA
            # afterwards. A figure that plots only the median asserts a stability that is not there.
            "per_rotation_ua": absolute,
            "spread_ua": max(absolute) - min(absolute),
            "repetitions": len(rows),
        }
    return out


def _msgs_per_hour(tech, params):
    """Ceiling on offered load that the radio's regulatory band imposes.

    Not the `duty_pct` column. That column reports the guard's rolling-window usage, which is not
    comparable across bands - Zigbee reads a higher figure than LoRa purely because 2.4 GHz has no
    limit to pace against, so nothing throttles it. The comparable quantity is the ceiling itself:
    a technology that cannot legally transmit as often *is* worse for a periodic application, and
    the thesis asks for that to be reported as a result rather than corrected away.
    """
    if tech in UNCAPPED:
        return None                     # uncapped: annotated as such, never drawn as a finite bar
    module = technologies.get(tech)
    point = technologies.Point(tech, "baseline", "", dict(params))
    # airtime_seconds() charges a whole repetition; one message is that divided by the exchanges.
    per_message = module.airtime_seconds(point) / module.exchanges(point)
    if per_message <= 0:
        return None
    return LORA_DUTY_CYCLE * 3600.0 / per_message


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


# Ranking keys per --best-by mode. Each returns a tuple whose last element is the point key, so a
# tie resolves deterministically instead of by dict ordering - ESP-NOW genuinely has several
# configurations at the same energy per delivery, because the board dominates its energy budget.
_RANKERS = {
    "energy": lambda r: (r.get("energy_per_delivered_mj") or float("inf"),
                          -(r.get("goodput_bps") or 0.0), r["point_key"]),
    "latency": lambda r: ((r["rtt_ms"] or {}).get("median") or float("inf"),
                           -(r.get("pdr") or 0.0), r["point_key"]),
    "pdr": lambda r: (-(r.get("pdr") or 0.0),
                       r.get("energy_per_delivered_mj") or float("inf"), r["point_key"]),
}


def select_best(records, by="energy", pdr_floor=0.95):
    """The best configuration of each technology, and what the gate rejected.

    Returns `(best, rejected)`. `by="baseline"` skips the search and returns each technology's
    documented baseline, which is a defensible choice but is "default", not "best".

    The delivery gate is what stops the ranking being won by a configuration that is cheap only
    because it gives up: rq1-methodology.tex normalises energy per *delivered* payload for exactly
    this reason. On the reference campaign it rejects all four Zigbee broadcast configurations.
    """
    best, rejected = {}, defaultdict(list)

    if by == "baseline":
        for record in records:
            if record["factor"] == "baseline":
                best[record["tech"]] = record
        return best, rejected

    try:
        rank = _RANKERS[by]
    except KeyError:
        raise ValueError(f"unknown ranking {by!r}; known: {', '.join(_RANKERS)}") from None

    by_tech = defaultdict(list)
    for record in records:
        if not record["attempts"] or not record["successes"]:
            continue
        pdr = record.get("pdr")
        if pdr is not None and pdr < pdr_floor:
            rejected[record["tech"]].append(record)
            continue
        by_tech[record["tech"]].append(record)

    for tech, candidates in by_tech.items():
        best[tech] = min(candidates, key=rank)
    return best, rejected


def flat_factors(records, threshold=1.05):
    """(tech, factor, ratio) for sweeps whose levels barely move the median latency.

    A factor whose extreme levels differ by less than `threshold` is a knob that provably does
    nothing on this hardware - worth reporting as a finding, and worth not spending a figure on.
    """
    # The baseline sits at some level of every factor, so it counts as one of that factor's levels.
    # Without it a factor swept to a single alternative - ESP-NOW encryption, Zigbee broadcast -
    # looks like one lonely point and gets skipped, which is exactly backwards: those are the
    # sweeps where the comparison against the baseline *is* the whole result.
    baselines = {r["tech"]: r for r in records if r["factor"] == "baseline"}

    by_factor = defaultdict(list)
    for record in records:
        if record["factor"] in ("baseline", "combination", "idle"):
            continue
        median = (record["rtt_ms"] or {}).get("median")
        if median:
            by_factor[(record["tech"], record["factor"])].append(median)

    for (tech, factor), medians in by_factor.items():
        base = baselines.get(tech)
        if base and factor in (base.get("params") or {}):
            base_median = (base["rtt_ms"] or {}).get("median")
            if base_median:
                medians.append(base_median)

    out = []
    for (tech, factor), medians in sorted(by_factor.items()):
        if len(medians) < 2 or min(medians) <= 0:
            continue
        ratio = max(medians) / min(medians)
        out.append((tech, factor, ratio, ratio < threshold))
    return out


def stack_pairs(records):
    """{tech: [(payload, library_record, raw_record)]} for payloads measured on both stacks.

    The subtraction the exposé asks for. Pairing is by payload rather than by point name because
    the two arms are named differently - the library arm is a `payload` sweep point, the raw arm a
    `stack` one - and payload is the input parameter that has to match for the difference to mean
    anything.

    Only sizes present on both sides are returned. A payload measured on one stack alone yields no
    comparison, and silently dropping the other half of the pair would turn a missing measurement
    into an apparent result.
    """
    by_tech = defaultdict(lambda: {"lib": {}, "raw": {}})
    for record in records:
        payload = record.get("payload_bytes")
        if payload is None or not record.get("successes"):
            continue
        arm = "raw" if record.get("stack") == "raw" else "lib"
        # The library arm reaches a given payload through either its payload sweep or its
        # baseline; both are the same configuration as far as this comparison is concerned.
        if arm == "lib" and record["factor"] not in ("payload", "baseline"):
            continue
        by_tech[record["tech"]][arm][int(payload)] = record

    out = {}
    for tech, arms in by_tech.items():
        shared = sorted(set(arms["lib"]) & set(arms["raw"]))
        if shared:
            out[tech] = [(size, arms["lib"][size], arms["raw"][size]) for size in shared]
    return out
