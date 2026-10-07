"""Reading one run off the node: reset it, then follow its serial output until `END`.

The node firmware takes one command: after booting it prints `READY` and waits for a `G`. It then
measures its one configuration and prints as it goes. The wait is what keeps a board that was
merely powered up, or reset by a port being opened, from transmitting unrecorded.
"""

import time

import serial

BAUD = 115200
RECORD_PREFIXES = ("READY", "PPH,", "PPD,", "PPS,", "HOME,", "IDLE,", "RUN,", "REPORT,", "ERR,",
                   "END")


class BoardTimeout(RuntimeError):
    pass


def run_and_capture(port, timeout_s, on_line=None):
    """Reset the board, start it, and return every record line up to and including `END`.

    `on_line(line)` is called as each record arrives; the runner uses it to open and close the
    PPK2 windows on the `RUN,begin` / `IDLE,begin` lines, at the moment they happen.
    """
    link = serial.Serial()
    link.port = port
    link.baudrate = BAUD
    link.timeout = 0.2
    link.dtr = False   # DTR low + RTS pulse: reset into the application, not the bootloader
    link.rts = False
    link.open()
    try:
        link.rts = True
        time.sleep(0.1)
        link.reset_input_buffer()
        link.rts = False

        lines = []
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            # A Zigbee build shares nothing with this UART, but the ROM boot banner and a stray
            # reset do: anything that is not a record is skipped, never fatal.
            text = link.readline().decode("ascii", errors="replace").strip()
            start = min((text.find(p) for p in RECORD_PREFIXES if p in text), default=-1)
            if start < 0:
                continue
            line = text[start:]
            if line == "READY":
                link.write(b"G")
                continue
            lines.append(line)
            if on_line:
                on_line(line)
            if line == "END":
                return lines
        raise BoardTimeout(f"no END within {timeout_s:.0f} s; last lines: {lines[-3:]}")
    finally:
        link.close()
