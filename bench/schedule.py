"""What runs, how often, and in what order - the ordering that practice P9 asks for.

**Execution order (practice P9).** Repetitions of the same configuration are never run back to
back. The schedule is *repetition-major* - every configuration point once, then every point again -
and technologies rotate between repetitions, so a given point's repeats are separated by the whole
rest of the campaign. The point order is also rotated each time round, so the same configuration
does not always sit at the start of its block. This is exactly the ordering slide 18 of
`02_experimentation.pdf` argues for: a transient external event then damages every configuration a
little instead of destroying one completely.

The budget side is the other half: how many repetitions each technology's share of the wall clock
actually buys, given that for LoRa the duty cycle rather than the exchange time sets the floor.
"""

from dataclasses import dataclass
from typing import Any

from . import technologies
from .pacing import DUTY_CYCLE_FRACTION

# Roughly how long a CFG round trip plus bookkeeping costs, per point. Measured at ~2 s, dominated
# by the two transports being torn down and rebuilt.
POINT_OVERHEAD_S = 4.0

# Share of the campaign's wall clock each technology gets. LoRa takes the largest share because
# it is the only one whose cost is set by regulation rather than by software, and it still ends up
# with the fewest repetitions.
DEFAULT_SHARES = {"lora": 0.55, "zigbee": 0.25, "espnow": 0.20}

PRESETS = {"quick": 0.5, "standard": 3.0, "full": 10.0}


@dataclass(frozen=True)
class ScheduledRun:
    index: int
    rotation: int
    point: Any

    @property
    def tech(self):
        return self.point.tech


def cost_per_repetition(module):
    """Wall clock one repetition of a technology's whole point list needs.

    For LoRa the duty cycle, not the exchange time, sets the floor: transmitting A seconds of
    airtime legally takes at least A/0.10 seconds of wall clock.
    """
    points = module.points()
    measured = sum(module.estimated_seconds(p) + POINT_OVERHEAD_S for p in points)
    airtime = sum(module.airtime_seconds(p) for p in points)
    duty_floor = airtime / DUTY_CYCLE_FRACTION if airtime else 0.0
    return max(measured, duty_floor), airtime, len(points)


def allocate(budget_hours, techs, shares=None):
    """Repetitions per technology for a given wall-clock budget.

    Two passes, because the idle points are scheduled once per rotation and sit outside the
    per-technology point lists: a first pass does not know how many rotations there will be, so it
    cannot reserve their time, and would over-commit the budget.
    """
    first = _allocate_once(budget_hours * 3600.0, techs, shares)
    rotations = max(entry["repetitions"] for entry in first.values())
    idle_cost = rotations * (1 + len(techs)) * (technologies.IDLE_SECONDS + POINT_OVERHEAD_S)

    plan = _allocate_once(max(budget_hours * 3600.0 - idle_cost, 60.0), techs, shares)
    for entry in plan.values():
        entry["idle_reserved_s"] = round(idle_cost, 1)
    return plan


def _allocate_once(budget_s, techs, shares=None):
    shares = shares or DEFAULT_SHARES
    total_share = sum(shares.get(t, 0.0) for t in techs) or 1.0

    plan = {}
    for tech in techs:
        module = technologies.get(tech)
        per_rep, airtime, n_points = cost_per_repetition(module)
        tech_budget = budget_s * (shares.get(tech, 0.0) / total_share)
        reps = max(1, int(tech_budget // per_rep)) if per_rep else 1
        plan[tech] = {
            "repetitions": reps,
            "points": n_points,
            "seconds_per_repetition": round(per_rep, 1),
            "airtime_per_repetition_s": round(airtime, 1),
            "budget_s": round(tech_budget, 1),
            "estimated_total_s": round(reps * per_rep, 1),
        }
    return plan


def build_schedule(plan, techs):
    """Repetition-major, technology-rotating, point-order-rotated. See practice P9.

    The resulting order is what `verify_practices.py` audits - it reconstructs the real execution
    order from timestamps in the data rather than trusting this function.
    """
    point_lists = {tech: technologies.get(tech).points() for tech in techs}
    max_reps = max(plan[t]["repetitions"] for t in techs)
    idle = technologies.idle_points(techs)

    schedule, index = [], 0
    for rotation in range(max_reps):
        # Standing-cost measurements lead every rotation, so each one is repeated as often as the
        # rotations themselves and gets the same reproducibility treatment as the active points.
        for point in idle:
            schedule.append(ScheduledRun(index, rotation, point))
            index += 1
        for tech in techs:
            if rotation >= plan[tech]["repetitions"]:
                continue
            points = point_lists[tech]
            # Rotate the starting offset so a given point does not always lead its block.
            offset = rotation % len(points)
            for point in points[offset:] + points[:offset]:
                schedule.append(ScheduledRun(index, rotation, point))
                index += 1
    return schedule
