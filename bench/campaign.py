"""Executing a schedule against the two boards, streaming results to disk as it goes.

What to run and in what order is bench/schedule.py; how long to wait is bench/pacing.py. This
module is the part that talks to hardware: configure both boards, settle the radio, open the
energy window around the measured exchange only, run, and write the point out before moving on.

Two details here are load-bearing rather than incidental:

* **Each board's peer is the other board.** Handing both the same peer makes ESP-NOW talk to
  itself and every exchange time out.
* **The energy window brackets the exchange, not the CFG.** Including the teardown and rebuild
  would charge every point the cost of constructing a Transport.
"""

import json
import time
from collections import deque

from . import technologies
from .pacing import AirtimeBudget, run_timeout
from .records import parse_records

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
                 log=print, power=None, settle_s=SETTLE_SECONDS, capture_rssi=False):
        # Campaign-wide rather than per-point, because it is not a swept factor: capturing signal
        # quality costs a promiscuous sniffer on ESP-NOW and a UART round trip on Zigbee, so a run
        # with it on is a different experiment from one without, not another point in the same one.
        self.capture_rssi = capture_rssi
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

    def _with_rssi(self, params):
        """Stamp the campaign-wide RSSI setting onto a CFG.

        Only when enabled: a CFG is a complete specification and the firmware starts from defaults
        each time, so omitting the key is the same as sending rssi=0. Leaving it out keeps the
        emitted CFG lines byte-identical to those of campaigns recorded before this existed, which
        is what lets the two be compared.
        """
        if self.capture_rssi:
            params["rssi"] = 1
        return params

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
            params = self._with_rssi(module.cfg_params(run.point, self.gateway_peer))
            node_params = self._with_rssi(module.cfg_params(run.point, self.node_peer))
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
        params = self._with_rssi(module.cfg_params(run.point, self.gateway_peer))
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

        node_params = self._with_rssi(module.cfg_params(run.point, self.node_peer))
        node_params.pop("n", None)   # the node serves continuously; it has no exchange count
        self.node.configure(node_params)
        header = self.gateway.configure(params)
        self._settle(run.tech)

        started_at = time.time()
        timeout = run_timeout(module, run.point, params)
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
