"""Running one configuration: flash the node, let it measure, record what it printed.

The gateway has no host, so everything known about it is inferred here. In particular, after a
LoRa point that did not end with an acknowledged release the gateway may still be sitting on that
point, deaf to home; the runner then waits out the lease before starting the next LoRa point.
"""

import random
import time
from datetime import datetime

from . import airtime, board, configs, firmware, records

NODE_ENV = "node"
BOOT_ALLOWANCE_S = 30
SEND_TIMEOUT_S = 2.0   # the longest any backend blocks in send(): ESP-NOW and Zigbee status waits


class Runner:
    def __init__(self, project, port, boards, writer, budget, power=None, pio="pio",
                 lora_header_bytes=airtime.LORA_HEADER_BYTES, log=print):
        self.project = project
        self.port = port
        self.boards = boards
        self.writer = writer
        self.budget = budget
        self.power = power
        self.pio = pio
        self.lora_header_bytes = lora_header_bytes
        self.log = log
        self._run_id = random.randrange(256)
        self._gateway_home_at = 0.0

    # -- timing bounds ---------------------------------------------------------------------

    def _airtimes_ms(self, config):
        """(ping, header-only control frame) time-on-air at this point; zeros off LoRa."""
        if config.tech != "lora":
            return 0.0, 0.0
        ping = configs.ping_airtime_ms(config, self.lora_header_bytes)
        p = config.params
        control = airtime.time_on_air_ms(
            airtime.CONTROL_FRAME_BYTES, sf=p["sf"], bw_khz=p["bw"], coding_rate=p["cr"],
            preamble=p["preamble"], header_bytes=self.lora_header_bytes)
        return ping, control

    def _timeout_s(self, config):
        """An upper bound on the run, so a hung board ends the point and not the day."""
        reply_s = configs.reply_wait_ms(config) / 1000.0
        bound = BOOT_ALLOWANCE_S + config.idle_ms / 1000.0
        bound += config.exchanges * (0.1 + SEND_TIMEOUT_S + reply_s)
        if config.tech == "lora" and config.exchanges:
            bound += airtime.CONTROL_ATTEMPTS * (10 + 4 * reply_s)   # handshake, probes, release
        return bound

    # -- one run ---------------------------------------------------------------------------

    def run(self, config, distance_m=None, round_index=0):
        """Flash `config`, capture its run, write it to the campaign. Returns the run record."""
        is_lora_point = config.tech == "lora" and config.exchanges > 0
        ping_ms, control_ms = self._airtimes_ms(config)
        duty_wait_s = 0.0
        if is_lora_point:
            self._wait_for_gateway_home()
            duty_wait_s = self.budget.wait_for(
                airtime.point_airtime_s(ping_ms, control_ms, config.exchanges,
                                        header_bytes=self.lora_header_bytes), log=self.log)

        self._run_id = (self._run_id + 1) % 256
        header = firmware.render_header(config, self.boards["gateway_mac"],
                                        self.boards["gateway_xbee64"], self._run_id)
        firmware.flash(self.project, NODE_ENV, self.port, header, self.pio)

        windows, energy = {}, {}

        def on_line(line):
            if not self.power:
                return
            for name, opens, closes in (("idle", "IDLE,begin", "IDLE,end"),
                                        ("run", "RUN,begin", "RUN,end")):
                if line.startswith(opens):
                    keep_raw = name == "run" and config.name == "baseline"
                    label = f"{config.key}-{distance_m}m-r{round_index}"
                    windows[name] = self.power.open(label, keep_raw=keep_raw)
                elif line.startswith(closes) and name in windows:
                    energy[name] = self.power.close(windows.pop(name))

        started_at = datetime.now().isoformat(timespec="seconds")
        lines, timed_out = [], False
        try:
            lines = board.run_and_capture(self.port, self._timeout_s(config), on_line)
        except board.BoardTimeout as exc:
            timed_out = True
            self.log(f"  {exc}")
        for window in windows.values():   # a run that died mid-window still has to let go of it
            self.power.close(window)

        run = records.parse(lines)
        released = run["release"] == "ok"   # the gateway said it is going back to home
        clean = bool(released and run["home"] and run["home"]["acked"])
        if is_lora_point:
            pings = run["summary"]["sent"] if run["summary"] else len(run["exchanges"])
            attempts = run["home"]["attempts"] if clean else airtime.CONTROL_ATTEMPTS
            charged = airtime.point_airtime_s(ping_ms, control_ms, pings, attempts, clean,
                                              self.lora_header_bytes)
            self.budget.record(charged)
            if not released:
                self._gateway_home_at = time.time() + configs.lease_seconds(config) + 5

        record = {
            "distance_m": distance_m, "round": round_index, "key": config.key,
            "tech": config.tech, "name": config.name, "params": config.params,
            "exchanges_planned": config.exchanges, "started_at": started_at,
            "run_id": self._run_id, "header": run["header"], "home": run["home"],
            "idle_ms": run["idle_ms"], "summary": run["summary"],
            "delivery": records.delivery(run), "release": run["release"],
            "energy": energy, "ping_airtime_ms": round(ping_ms, 2),
            "duty_wait_s": round(duty_wait_s, 1),
            "error": "timeout" if timed_out else run["error"], "complete": run["complete"],
        }
        self.writer.write_run(record, run["exchanges"], lines)
        return record

    def _wait_for_gateway_home(self):
        remaining = self._gateway_home_at - time.time()
        if remaining > 0:
            self.log(f"  last LoRa point was not released; the gateway's lease runs out in "
                     f"{remaining:.0f} s, waiting")
            time.sleep(remaining)


def describe(record):
    """One line for the console, so a bad point is visible while still standing at the site."""
    if record["error"]:
        return f"FAILED ({record['error']})"
    parts = []
    delivery = record["delivery"]
    if delivery["sent"]:
        up, down = delivery["uplink_pdr"], delivery["downlink_pdr"]
        parts.append(f"{delivery['answered']}/{delivery['sent']} answered")
        parts.append("up " + ("?" if up is None else f"{up:.0%}"))
        parts.append("down " + ("?" if down is None else f"{down:.0%}"))
        if record["summary"]["aborted"]:
            parts.append("stopped: 10 losses in a row")
    for name, window in record["energy"].items():
        if window:
            parts.append(f"{name} {window['mean_ua'] / 1000:.1f} mA")
    if record["home"] and record["home"]["acked"] and record["home"]["rssi_dbm"] is not None:
        parts.append(f"home {record['home']['rssi_dbm']:.0f} dBm")
    return ", ".join(parts) or "done"
