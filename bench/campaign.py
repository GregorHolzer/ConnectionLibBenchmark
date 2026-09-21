"""Campaign scheduling and execution.

Two things here carry most of the methodological weight:

**Execution order (practice P9).** Repetitions of the same configuration are never run back to
back. The schedule is *repetition-major* - every configuration point once, then every point again -
and technologies rotate between repetitions, so a given point's repeats are separated by the whole
rest of the campaign. The point order is also rotated each time round, so the same configuration
does not always sit at the start of its block. This is exactly the ordering slide 18 of
`02_experimentation.pdf` argues for: a transient external event then damages every configuration a
little instead of destroying one completely.

**Airtime pacing.** LoRa's 10 % duty cycle is enforced *here*, on the host, and that is not
redundant with the on-device `DutyCycleGuard`: the firmware rebuilds its Transport on every CFG,
which constructs a fresh guard and discards its ledger, so the device cannot see across
configuration points. The host tracks a rolling one-hour window over the whole campaign and waits
when the next point would exceed it.
"""

import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from . import technologies
from .records import parse_records

# ETSI EN 300 220-2 / ERC-REC 70-03 h1.3 - see docs/regulations.md.
DUTY_CYCLE_FRACTION = 0.10
DUTY_CYCLE_WINDOW_S = 3600.0

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


class AirtimeBudget:
    """Rolling-window airtime ledger, in the host, spanning the whole campaign."""

    def __init__(self, fraction=DUTY_CYCLE_FRACTION, window_s=DUTY_CYCLE_WINDOW_S):
        self.fraction = fraction
        self.window_s = window_s
        self.limit_s = fraction * window_s
        self._entries = deque()   # (timestamp, seconds)
        self.total_s = 0.0

    def _prune(self, now):
        while self._entries and now - self._entries[0][0] > self.window_s:
            self._entries.popleft()

    def used(self, now=None):
        now = now or time.time()
        self._prune(now)
        return sum(seconds for _, seconds in self._entries)

    def record(self, seconds):
        if seconds <= 0:
            return
        self._entries.append((time.time(), seconds))
        self.total_s += seconds

    def wait_for(self, seconds, sleeper=time.sleep):
        """Block until `seconds` of additional airtime would fit inside the window.

        Returns how long it waited, which the campaign records - a large value is not a stall but
        the duty cycle being visibly enforced, and it belongs in the results.
        """
        if seconds <= 0:
            return 0.0
        waited = 0.0
        while True:
            now = time.time()
            self._prune(now)
            if self.used(now) + seconds <= self.limit_s:
                return waited
            # Wait until the oldest entry ages out of the window.
            oldest_at, _ = self._entries[0]
            pause = max(1.0, (oldest_at + self.window_s) - now + 0.5)
            sleeper(pause)
            waited += pause


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


# Settle time after switching to a different technology, before the energy window opens. CFG
# returns as soon as the firmware acknowledges it, which is before the radio has reached its
# steady state: measured on the bench, the first ESP-NOW point after another technology read
# 85.4 mA against an ESP-NOW idle of 117.8 mA - below its own idle, which is impossible while
# transmitting - and the point after it was still 12 % low. LoRa and Zigbee settle within the
# CFG round trip and show no such ramp.
#
# Charged only on a *change* of technology, not on every CFG: consecutive points within one
# technology's block rebuild the Transport too, and those showed no warm-up. That keeps the cost
# at a few seconds per rotation rather than per point.
SETTLE_SECONDS = 3.0


class CampaignRunner:
    """Executes a schedule against the two boards, streaming results to disk as it goes."""

    def __init__(self, gateway, node, gateway_peer, node_peer, out_dir, writer, status_path,
                 log=print, power=None, settle_s=SETTLE_SECONDS):
        self.gateway = gateway
        self.node = node
        # Optional PPK2. None means the campaign runs exactly as before, without energy columns.
        self.power = power
        # Each board's peer is the *other* board: the gateway unicasts to the node's address and
        # the node replies to the gateway's. Handing both the same peer would make ESP-NOW talk
        # to itself and every exchange time out.
        self.gateway_peer = gateway_peer
        self.node_peer = node_peer
        self.out_dir = out_dir
        self.writer = writer
        self.status_path = status_path
        self.log = log
        self.airtime = AirtimeBudget()
        self.settle_s = settle_s
        self._last_tech = None
        self.completed = 0
        self.failures = []
        self.started_at = time.time()
        self.recent = deque(maxlen=8)
        self.duty_wait_total_s = 0.0

    def _settle(self, tech):
        """Let a newly-selected radio reach steady state before any window opens.

        Includes the very first point of a run, when `_last_tech` is still None: that is the
        coldest the radio ever is, and it is the case the warm-up was first measured on.
        """
        if self.settle_s and tech != self._last_tech:
            time.sleep(self.settle_s)
        self._last_tech = tech

    def _write_status(self, schedule, current=None):
        elapsed = time.time() - self.started_at
        done = self.completed
        remaining = len(schedule) - done
        rate = elapsed / done if done else 0.0
        status = {
            "started_at": self.started_at,
            "updated_at": time.time(),
            "elapsed_s": round(elapsed, 1),
            "points_done": done,
            "points_total": len(schedule),
            "points_remaining": remaining,
            "eta_s": round(remaining * rate, 1) if rate else None,
            "current": current,
            "failures": self.failures[-20:],
            "duty_wait_total_s": round(self.duty_wait_total_s, 1),
            "lora_airtime_used_s": round(self.airtime.used(), 1),
            "lora_airtime_limit_s": round(self.airtime.limit_s, 1),
            "lora_airtime_total_s": round(self.airtime.total_s, 1),
            "recent": list(self.recent),
        }
        per_tech = {}
        for run in schedule[:done]:
            per_tech.setdefault(run.tech, set()).add(run.rotation)
        status["rotations_done"] = {tech: len(rotations) for tech, rotations in per_tech.items()}

        self.status_path.write_text(json.dumps(status, indent=2))

    def _run_idle(self, run):
        """Standing-cost measurement: configure, then hold without exchanging.

        Both boards are configured so the *pair* is in the state a real run leaves it in - the node
        is what carries the PPK2, and its idle current is the number that matters.
        """
        tech = run.point.tech
        if tech == "none":
            params = {"tech": "none", "payload": 32, "n": 1}
            node_params = dict(params)
        else:
            module = technologies.get(tech)
            params = module.cfg_params(run.point, self.gateway_peer)
            node_params = module.cfg_params(run.point, self.node_peer)
            node_params.pop("n", None)

        self.node.configure(node_params)
        self.gateway.configure(params)
        self._settle(tech)

        started_at = time.time()
        duration_ms = technologies.IDLE_SECONDS * 1000
        window = self.power.open(run.point.key, run.rotation) if self.power else None
        # Only the node holds. It is the instrumented board, and a gateway that is configured but
        # not running never transmits - it just polls - so it is already idle and cannot perturb
        # the measurement. Idling it as well would double the cost of every idle point and stretch
        # the energy window across two phases in which the node was in different states.
        records = self.node.idle(duration_ms)
        energy = self.power.close(window) if window else None

        _, _, summary, _, _ = parse_records(records)
        self.writer.write_point(run, {}, [], summary, [], None, started_at, energy=energy)
        return summary is not None, [], summary

    def run_one(self, run, exchanges_override=None):
        """Execute one configuration point.

        `exchanges_override` shortens the run for smoke tests. It scales the airtime charged to
        the budget accordingly - charging the full amount for a 10-exchange run would make the
        pacing wrong in the safe direction, but wrong all the same.
        """
        if run.point.factor == "idle":
            return self._run_idle(run)

        module = technologies.get(run.tech)
        params = module.cfg_params(run.point, self.gateway_peer)
        airtime = module.airtime_seconds(run.point)
        if exchanges_override:
            scale = exchanges_override / module.exchanges(run.point)
            params["n"] = exchanges_override
            airtime *= scale

        # Wait for the duty cycle *before* configuring, so the boards are not left sitting in a
        # configured state for an hour.
        waited = self.airtime.wait_for(airtime)
        if waited:
            self.duty_wait_total_s += waited
            self.log(f"  duty cycle: waited {waited:.0f}s for airtime budget")

        node_params = module.cfg_params(run.point, self.node_peer)
        node_params.pop("n", None)   # the node serves continuously; it has no exchange count
        self.node.configure(node_params)
        header = self.gateway.configure(params)
        self._settle(run.tech)

        started_at = time.time()
        timeout = module.estimated_seconds(run.point) * 3 + 60
        # The energy window brackets only the measured exchange, not the CFG teardown and rebuild
        # that precedes it - otherwise every point would carry the cost of constructing a Transport.
        window = self.power.open(run.point.key, run.rotation) if self.power else None
        records = self.gateway.run(timeout_s=timeout)
        energy = self.power.close(window) if window else None
        node_records = self.node.drain(0.5)
        self.airtime.record(airtime)

        _, exchanges, summary, _, _ = parse_records(records)
        _, _, _, node_rx, node_summary = parse_records(node_records)

        self.writer.write_point(run, header, exchanges, summary, node_rx, node_summary, started_at,
                                 energy=energy)

        expected = exchanges_override or module.exchanges(run.point)
        ok = summary is not None and summary["successes"] >= expected
        if not ok:
            got = summary["successes"] if summary else 0
            self.failures.append({"index": run.index, "point": run.point.key,
                                   "successes": got, "expected": expected})
        return ok, exchanges, summary
