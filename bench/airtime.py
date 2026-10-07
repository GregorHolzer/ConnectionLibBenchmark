"""LoRa airtime: how much one frame costs, and how much has been spent.

The 433 MHz band allows 10 % duty cycle per hour (ETSI EN 300 220-2 / ERC-REC 70-03 h1.3, see
docs/regulations.md). The firmware's own guard starts empty on every flash, so on the node this
ledger is the only thing that enforces the limit across a campaign.
"""

import math
import time
from collections import deque
from pathlib import Path

# ConnectionLib prepends destNodeId/srcNodeId/seq/payloadLen/flags to every LoRa frame, because a
# raw SX1278 has no addressing; the flags byte is there because the benchmark builds the library
# with CONNECTIONLIB_LORA_ACK. An ack is that header alone.
LORA_HEADER_BYTES = 7

# Paced a little under the legal 10 %, as margin for the estimate. The gateway only sends the
# library's header-only acks, so its own budget is never the one that runs out first.
DUTY_CYCLE_FRACTION = 0.09
DUTY_CYCLE_WINDOW_S = 3600.0

# src/Config.hpp kHome and src/Frames.hpp: the config frame on home, and a header-only frame.
HOME = {"sf": 12, "bw_khz": 125, "coding_rate": 8, "preamble": 8}
SWITCH_FRAME_BYTES = 11
CONTROL_FRAME_BYTES = 8
CONTROL_ATTEMPTS = 3   # src/NodeControl.cpp kAttempts


def time_on_air_ms(payload_bytes, sf=9, bw_khz=125.0, coding_rate=7, preamble=8,
                   header_bytes=LORA_HEADER_BYTES):
    """Time-on-air of one transmission, from the SX127x symbol-count formula.

    Validated against hardware: predicts 197.6 ms at SF9 / 8 B where the boards measured 201.9 ms.
    """
    total = payload_bytes + header_bytes
    t_sym = (2 ** sf) / (bw_khz * 1000.0)
    # Low data rate optimisation is mandatory where a symbol exceeds 16 ms, which at 125 kHz is
    # SF11 and above. It costs throughput, so it changes the answer and must be modelled.
    low_rate_opt = 1 if t_sym > 0.016 else 0
    numerator = 8 * total - 4 * sf + 28 + 16
    denominator = 4 * (sf - 2 * low_rate_opt)
    payload_symbols = 8 + max(math.ceil(numerator / denominator) * coding_rate, 0)
    return (preamble + 4.25 + payload_symbols) * t_sym * 1000.0


def point_airtime_s(ping_ms, control_ms, pings, switch_attempts=CONTROL_ATTEMPTS, clean=False,
                    header_bytes=LORA_HEADER_BYTES):
    """LoRa airtime one run costs the node, whatever its technology.

    Every run announces itself with a config frame on LoRa home, so ESP-NOW and Zigbee runs cost
    that too; for them `ping_ms` and `control_ms` are 0. A LoRa run adds its payloads and the
    header-only frames on the point (probe, end message).

    With the defaults this is the upper bound, used to reserve airtime before a run and to charge
    one that did not end cleanly; over-charging is the safe side. A clean run is charged what it
    sent: `switch_attempts` config frames and one end message.
    """
    del header_bytes   # home always goes through the library, with its header
    switch_ms = time_on_air_ms(SWITCH_FRAME_BYTES, **HOME)
    control_frames = 1 if clean else 2 * CONTROL_ATTEMPTS
    return (pings * ping_ms + switch_attempts * switch_ms + control_frames * control_ms) / 1000.0


class AirtimeBudget:
    """Rolling one-hour ledger of the node's LoRa airtime, kept in a file.

    In a file because the radios do not forget between runs: a run started 16 minutes after an
    earlier one once began with an empty ledger, and the gateway - whose own ledger spans its
    uptime - refused 34 of 40 replies.
    """

    def __init__(self, path, fraction=DUTY_CYCLE_FRACTION, window_s=DUTY_CYCLE_WINDOW_S):
        self.window_s = window_s
        self.limit_s = fraction * window_s
        self._path = Path(path)
        self._entries = deque()   # (timestamp, seconds)
        now = time.time()
        if self._path.exists():
            for line in self._path.read_text().splitlines():
                try:
                    at, seconds = (float(x) for x in line.split())
                except ValueError:
                    continue
                if now - at <= window_s:
                    self._entries.append((at, seconds))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text("".join(f"{at:.3f} {s:.3f}\n" for at, s in self._entries))

    def used(self, now=None):
        now = now or time.time()
        while self._entries and now - self._entries[0][0] > self.window_s:
            self._entries.popleft()
        return sum(seconds for _, seconds in self._entries)

    def record(self, seconds):
        if seconds <= 0:
            return
        at = time.time()
        self._entries.append((at, seconds))
        with self._path.open("a") as handle:
            handle.write(f"{at:.3f} {seconds:.3f}\n")

    def wait_for(self, seconds, sleeper=time.sleep, log=print):
        """Block until `seconds` more airtime fits inside the window. Returns how long it waited."""
        waited = 0.0
        while seconds > 0 and self.used() + seconds > self.limit_s:
            if not self._entries:
                raise ValueError(f"{seconds:.0f} s of airtime can never fit in the duty cycle")
            pause = max(1.0, self._entries[0][0] + self.window_s - time.time() + 0.5)
            log(f"duty cycle: {self.used():.0f} s of {self.limit_s:.0f} s used, "
                f"waiting {pause:.0f} s")
            sleeper(pause)
            waited += pause
        return waited
