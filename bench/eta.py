"""How much campaign is left, from each technology's own cost model.

Kept out of progress.py because it is real arithmetic rather than formatting, and because the
reason it is not a running average is worth stating once, next to the code that acts on it.
"""

from collections import defaultdict

from bench import technologies
from bench.pacing import DUTY_CYCLE_FRACTION
from bench.schedule import POINT_OVERHEAD_S, build_schedule


def duration(seconds):
    if seconds is None:
        return "?"
    seconds = int(max(seconds, 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def remaining_estimate(meta, done):
    """Seconds left, from each technology's own cost model rather than a running average.

    A flat average over completed points is badly wrong here, because the points are not
    interchangeable: the schedule front-loads ESP-NOW at ~11 s a point, so an hour in the average
    predicts a 2 h campaign when LoRa alone - at a duty-cycle floor of 28 minutes per repetition -
    still has five hours to go. Re-deriving the schedule and summing what has not run yet is both
    simple and honest.
    """
    plan, techs = meta.get("plan"), meta.get("techs")
    if not plan or not techs:
        return None
    try:
        schedule = build_schedule(plan, techs)
    except (KeyError, ValueError):
        return None
    if done >= len(schedule):
        return 0.0

    # Accumulate per technology, then take the binding constraint for each one separately and add
    # them up. The technologies share the two boards so they run sequentially, and only LoRa has a
    # duty cycle - one global max() would let LoRa's five hours of mandatory waiting absorb the
    # other two technologies' real work and under-report the total by hours.
    measured, airtime = defaultdict(float), defaultdict(float)
    for run in schedule[done:]:
        if run.point.factor == "idle":
            # Fixed hold, and no transmission at all — an idle radio is receiving, not sending,
            # so idle points cost no airtime against the duty cycle.
            measured[run.tech] += technologies.IDLE_SECONDS + POINT_OVERHEAD_S
            continue
        module = technologies.get(run.tech)
        measured[run.tech] += module.estimated_seconds(run.point) + POINT_OVERHEAD_S
        airtime[run.tech] += module.airtime_seconds(run.point)

    return sum(max(measured[tech], airtime[tech] / DUTY_CYCLE_FRACTION) for tech in measured)
