"""Serial client for one benchmark board.

The device speaks the line protocol described at the top of src/main.cpp. One flashed image serves
an entire campaign: every hyperparameter, including the peer address, arrives in a CFG command.

Finding and flashing the boards is bench/ports.py, which this module depends on rather than the
other way round - see the note there.
"""

import re
import subprocess
import time
from pathlib import Path

import serial

from .ports import DeviceError

REPO_ROOT = Path(__file__).resolve().parent.parent

# Records the firmware emits. Matched on prefix because the Zigbee log interleaves raw XBee API
# frame bytes with the text, so a line is not guaranteed to start where the record does.
RECORD_PREFIXES = ("PPH,", "PPD,", "PPS,", "PPN,", "PPM,", "PPI,", "INFO,")


class Device:
    """One board. Blocking, line-oriented, one command in flight at a time."""

    def __init__(self, port, baud=115200, name="device", log=None):
        self.port = port
        self.name = name
        self._log = log
        self._serial = serial.Serial(port, baud, timeout=0.2)
        # Opening the port toggles DTR/RTS and resets the ESP32; wait for it to come back up
        # rather than racing its boot messages.
        time.sleep(1.5)
        self._serial.reset_input_buffer()

    def close(self):
        try:
            self._serial.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- low level ------------------------------------------------------------------------

    def _write(self, line):
        self._serial.write((line + "\n").encode())
        self._serial.flush()

    def _read_until(self, terminators, timeout_s, collect=True):
        """Read lines until one starts with a terminator. Returns (terminator_line, records).

        Junk is skipped rather than fatal: a board that has just reset emits ROM boot messages,
        and the Zigbee path mixes binary into the stream.
        """
        deadline = time.time() + timeout_s
        records = []
        while time.time() < deadline:
            raw = self._serial.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            if self._log:
                self._log(f"[{self.name}] {line}")
            for term in terminators:
                idx = line.find(term)
                if idx >= 0:
                    return line[idx:], records
            if collect:
                for prefix in RECORD_PREFIXES:
                    idx = line.find(prefix)
                    if idx >= 0:
                        records.append(line[idx:])
                        break
        raise DeviceError(f"{self.name}: timed out after {timeout_s:.0f}s "
                           f"waiting for one of {terminators}")

    # -- commands -------------------------------------------------------------------------

    def ping(self, timeout_s=5.0):
        self._serial.reset_input_buffer()
        self._write("PING")
        line, _ = self._read_until(("PONG,", "ERR,"), timeout_s, collect=False)
        if line.startswith("ERR"):
            raise DeviceError(f"{self.name}: {line}")
        return line.split(",", 1)[1]

    def info(self, timeout_s=5.0):
        """Build and environment facts, for the practice audit (P3, P6)."""
        self._serial.reset_input_buffer()
        self._write("INFO")
        _, records = self._read_until(("INFO,end,",), timeout_s)
        out = {}
        for record in records:
            parts = record.split(",", 2)
            if len(parts) == 3 and parts[0] == "INFO":
                out[parts[1]] = parts[2]
        return out

    def configure(self, params, timeout_s=20.0):
        """Send one CFG. Returns the PPH header dict. Raises DeviceError on ERR (e.g. a
        configuration the firmware's conformance guard rejected as outside the EU envelope)."""
        args = " ".join(f"{k}={v}" for k, v in params.items())
        self._serial.reset_input_buffer()
        self._write(f"CFG {args}")
        line, records = self._read_until(("ACK", "ERR,"), timeout_s)
        if line.startswith("ERR"):
            raise DeviceError(f"{self.name}: {line}")
        header = {}
        for record in records:
            parts = record.split(",", 2)
            if len(parts) == 3 and parts[0] == "PPH":
                header[parts[1]] = parts[2]
        return header

    def run(self, timeout_s):
        """Gateway only: run the configured point to completion. Returns the raw records."""
        self._write("RUN")
        _, records = self._read_until(("RDY",), timeout_s)
        return records

    def idle(self, duration_ms, timeout_s=None):
        """Hold the current configuration without exchanging anything - the standing-cost window.

        The board stays in exactly the receive state it uses during a real run, so this measures an
        idle radio rather than an artificially quiescent one.
        """
        self._write(f"IDLE {int(duration_ms)}")
        _, records = self._read_until(("RDY", "ERR,"), timeout_s or (duration_ms / 1000.0 + 30))
        return records

    def resync(self, timeout_s=300.0):
        """Wait for a board that is still working to go quiet, after a command gave up on it.

        A run that overruns its timeout has not stopped - the firmware retries every exchange up to
        five times, so the board can still be grinding for minutes after the host stopped listening.
        Sending the next CFG into that stream gets no ACK, and the point after it fails for a reason
        that has nothing to do with its own configuration. That is how one marginal point used to
        take the next six down with it.

        Returns True if the board reported RDY (it finished on its own), False if it simply fell
        silent. Either way the stream is clean enough for the next CFG.
        """
        deadline = time.time() + timeout_s
        quiet_needed = 1.5
        last_seen = time.time()
        finished = False
        while time.time() < deadline:
            raw = self._serial.readline()
            if raw:
                last_seen = time.time()
                if "RDY" in raw.decode(errors="replace"):
                    finished = True
                    break
                continue
            if time.time() - last_seen > quiet_needed:
                break
        self._serial.reset_input_buffer()
        return finished

    def drain(self, seconds=0.3):
        """Collect whatever the board has emitted since the last read - the node's PPN/PPM
        records accumulate while the gateway is driving the exchange."""
        deadline = time.time() + seconds
        records = []
        while time.time() < deadline:
            raw = self._serial.readline()
            if not raw:
                continue
            line = raw.decode(errors="replace").strip()
            for prefix in RECORD_PREFIXES:
                idx = line.find(prefix)
                if idx >= 0:
                    records.append(line[idx:])
                    break
        return records

    def read_mac(self, pio="pio"):
        """Read the board's MAC without flashing anything - esptool talks to the ROM loader."""
        self.close()
        try:
            result = subprocess.run(
                [pio, "pkg", "exec", "--", "esptool.py", "--port", self.port, "read_mac"],
                capture_output=True, text=True, check=True, cwd=REPO_ROOT)
            macs = re.findall(r"MAC: ([0-9A-Fa-f:]{17})", result.stdout)
            if not macs:
                raise DeviceError(f"no MAC in esptool output for {self.port}")
            return macs[-1]
        finally:
            self._serial.open()
            time.sleep(1.5)
            self._serial.reset_input_buffer()
