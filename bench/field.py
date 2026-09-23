"""Field mode: the laptop on one board, and a gateway in the field with no host at all.

See docs/field-campaign.md. What changes relative to the bench runner:

* **Only the node is on USB.** It runs the measured loop, as the bench gateway does, and the PPK2
  meters it. The field gateway is reached over the air: each point starts with a LEASE, which
  carries the point's configuration over the fixed home config, and ends with a RELEASE.
* **An unacknowledged lease is ambiguous.** If the request was lost the gateway is still on home;
  if only the ack was, it has switched and won't hear home until its lease runs out. The runner
  therefore tracks when the gateway is *certain* to be back on home, and never sends a lease
  before then.
* **Node->gateway arrivals are not seen live.** They are in the gateway's flash log, and
  tools/field_gateway.py turns them into node_rx.csv once the gateway has been collected.
"""

import math
import time

from . import technologies
from .campaign import CampaignRunner
from .device import LeaseNoAck
from .pacing import run_timeout
from .ports import DeviceError
from .records import parse_records
from .technologies import lora

# Mirrors src/FieldProtocol.hpp. Used only to charge control airtime against the duty cycle.
HOME = {"sf": 12, "bw_khz": 125.0, "coding_rate": 8, "preamble": 8}
LEASE_FRAME_BYTES = 35
TOKEN_FRAME_BYTES = 8
RELEASE_ATTEMPTS = 3   # FieldNode.cpp kReleaseAttempts

# Added to the run timeout to get the lease. Covers the settle time and the switch guard, which
# both fall inside the lease, with room to spare.
LEASE_MARGIN_S = 30

# Waited past the point where the gateway's lease must have run out, before trusting it is back
# on home: its clock starts at its ack, which is a little before the node sees it.
EXPIRY_GUARD_S = 5

# The wire carries the point id as 16 bits.
MAX_POINT_ID = 0xFFFF


def lease_request_airtime_s():
    """One lease request on home. The gateway's ack is shorter, so this also bounds its side."""
    return lora.time_on_air_ms(LEASE_FRAME_BYTES, **HOME) / 1000.0


def release_airtime_s(point):
    """The release frames for one point, when the point is LoRa - otherwise they cost no LoRa
    airtime. Charged at every attempt the node may make, since the host can't see how many it
    did; over-charging is the safe direction."""
    if point.tech != "lora":
        return 0.0
    p = point.params
    header = 0 if p.get("stack") == "raw" else lora.HEADER_BYTES
    one = lora.time_on_air_ms(TOKEN_FRAME_BYTES, sf=p["sf"], bw_khz=float(p["bw"]),
                              coding_rate=p["cr"], preamble=p["preamble"], header_bytes=header)
    return RELEASE_ATTEMPTS * one / 1000.0


class FieldCampaignRunner(CampaignRunner):
    """Executes a schedule with the node on USB and the gateway reached over the air.

    `peer` is the field gateway's addressing (its ESP-NOW MAC and XBee address): the address the
    node sends to, which is what the bench runner calls `gateway_peer` from the other side.
    """

    def __init__(self, node, peer, campaign_id, out_dir, writer, status_path, **kwargs):
        super().__init__(None, node, peer, None, out_dir, writer, status_path, **kwargs)
        self.campaign_id = campaign_id
        self._gateway_home_at = 0.0
        self.expiry_wait_total_s = 0.0
        self.lease_retries = 0
        self.unreleased = 0

    def _charge(self, seconds):
        """Wait for, then record, airtime the node is about to spend on control."""
        waited = self.airtime.wait_for(seconds)
        if waited:
            self.duty_wait_total_s += waited
            self.log(f"  duty cycle: waited {waited:.0f}s for airtime budget")
        self.airtime.record(seconds)

    def _wait_for_gateway_home(self):
        remaining = self._gateway_home_at - time.time()
        if remaining > 0:
            self.log(f"  waiting {remaining:.0f}s for the gateway's lease to run out")
            time.sleep(remaining)
            self.expiry_wait_total_s += remaining

    def _lease(self, params, point_id, lease_s):
        """Lease the gateway for one point. Returns (header, home signal quality, attempts).

        Retries once at once, which covers a lost request. If that fails too, the ack may have
        been lost instead, so it waits out the lease that might be running and tries a last time.
        """
        attempts = 0
        requested_at = None
        for wait_out_lease in (False, False, True):
            if wait_out_lease:
                self._gateway_home_at = max(self._gateway_home_at,
                                            requested_at + lease_s + EXPIRY_GUARD_S)
            self._wait_for_gateway_home()
            self._charge(lease_request_airtime_s())
            attempts += 1
            requested_at = time.time()
            try:
                header, home = self.node.lease(params, self.campaign_id, point_id, lease_s)
            except LeaseNoAck:
                self.lease_retries += 1
                continue
            # The gateway's lease runs from its ack. Until a release is acknowledged, assume it
            # runs in full.
            self._gateway_home_at = time.time() + lease_s + EXPIRY_GUARD_S
            return header, home, attempts

        # The last request may have started a lease whose ack was lost like the others.
        self._gateway_home_at = requested_at + lease_s + EXPIRY_GUARD_S
        raise DeviceError(f"the field gateway never acked a lease over home ({attempts} attempts): "
                          "out of LoRa range at SF12, or not running")

    def _run_idle(self, run):
        """Standing cost, on the node alone - it is the instrumented board, and the gateway has
        nothing to do with what the node draws while it waits."""
        tech = run.point.tech
        if tech == "none":
            params = {"tech": "none", "payload": 32, "n": 1}
        else:
            module = technologies.get(tech)
            params = self._with_rssi(module.cfg_params(run.point, self.gateway_peer))

        self.node.configure(params)
        self._settle(tech)

        started_at = time.time()
        window = self.power.open(run.point.key, run.rotation) if self.power else None
        records = self.node.idle(technologies.IDLE_SECONDS * 1000)
        energy = self.power.close(window) if window else None

        _, _, summary, _, _ = parse_records(records)
        self.writer.write_point(run, {}, [], summary, [], None, started_at, energy=energy)
        return summary is not None, [], summary

    def run_one(self, run, exchanges_override=None):
        if run.point.factor == "idle":
            return self._run_idle(run)
        if run.index > MAX_POINT_ID:
            raise DeviceError(f"point index {run.index} does not fit the 16-bit point id; "
                              "split the field campaign")

        module = technologies.get(run.tech)
        params = self._with_rssi(module.cfg_params(run.point, self.gateway_peer))
        airtime = module.airtime_seconds(run.point)
        if exchanges_override:
            airtime *= exchanges_override / module.exchanges(run.point)
            params["n"] = exchanges_override
        airtime += release_airtime_s(run.point)

        # The point's own airtime first, so the lease is never taken only to sit waiting on the
        # duty cycle while the gateway's lease clock runs.
        waited = self.airtime.wait_for(airtime + lease_request_airtime_s())
        if waited:
            self.duty_wait_total_s += waited
            self.log(f"  duty cycle: waited {waited:.0f}s for airtime budget")

        timeout = run_timeout(module, run.point, params)
        lease_s = math.ceil(timeout + LEASE_MARGIN_S)
        header, home, attempts = self._lease(params, run.index, lease_s)
        self._settle(run.tech)

        started_at = time.time()
        window = self.power.open(run.point.key, run.rotation) if self.power else None
        records = self.node.run(timeout_s=timeout)
        energy = self.power.close(window) if window else None

        released = self.node.release()
        if released:
            self._gateway_home_at = time.time()
        else:
            self.unreleased += 1
        self.airtime.record(airtime)

        _, exchanges, summary, _, _ = parse_records(records)
        field = {
            "campaign_id": f"{self.campaign_id:08x}",
            "point_id": run.index,
            "lease_s": lease_s,
            "lease_attempts": attempts,
            "released": released,
            "home_rssi_dbm": home.get("rssi_dbm"),
            "home_snr_db": home.get("snr_db"),
        }
        # node_rx stays empty: arrivals at the gateway come from its flash log, afterwards.
        self.writer.write_point(run, header, exchanges, summary, [], None, started_at,
                                energy=energy, extra={"field": field})

        expected = exchanges_override or module.exchanges(run.point)
        ok = summary is not None and summary["successes"] >= expected
        if not ok:
            got = summary["successes"] if summary else 0
            self.failures.append({"index": run.index, "point": run.point.key,
                                  "successes": got, "expected": expected})
        return ok, exchanges, summary
