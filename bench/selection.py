"""Choosing what to show, and reporting what the choice discarded.

Each technology is represented in the comparison by one configuration, so how that one is picked
is a result in its own right - `select_best` returns the rejects alongside the winners so the gate
is visible rather than silent. `flat_factors` answers the companion question: which swept knobs
turned out to move nothing, and so need not occupy a figure at all.
"""

from collections import defaultdict


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
