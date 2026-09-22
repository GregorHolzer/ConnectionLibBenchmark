"""Talking to a board that the PPK2 is powering, and reading its rail back.

Split out of tools/bench_board.py, which is now just the sequence these are called in.

Deliberately not using bench.device.Device for the capture: its constructor sleeps past the reset
and calls reset_input_buffer(), which discards exactly the boot log this exists to show. Boards
are addressed by /dev/serial/by-id path rather than ttyACM number because the numbering has
already swapped between sessions.
"""

import time
from pathlib import Path

from .ppk2_port import recover_streaming

BY_ID = Path("/dev/serial/by-id")
BOARD_BAUD = 115200

# CH9102 USB serial -> role, from docs/hardware.md. Addressing boards by by-id path rather than
# ttyACM number matters: the numbering has already swapped between sessions.
KNOWN_BOARDS = {
    "573C000618": "coordinator",
    "5748001588": "node",
}

DIM, RED, GREEN, RESET = "\033[2m", "\033[31m", "\033[32m", "\033[0m"


def log(msg=""):
    print(msg, flush=True)


def board_ports():
    """by-id paths that are plausibly a board, i.e. everything that is not the PPK2."""
    if not BY_ID.is_dir():
        return []
    return sorted(p for p in BY_ID.iterdir() if "PPK2" not in p.name)


def describe_board(path):
    for serial_no, role in KNOWN_BOARDS.items():
        if serial_no in path.name:
            return f"{role} ({serial_no})"
    return f"unrecognised board ({path.name})"


def wait_for_board(timeout_s, log=log):
    """Poll until a non-PPK2 serial device shows up. Returns the by-id path, or None."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        found = board_ports()
        if found:
            return found[0]
        time.sleep(0.25)
    return None


def power_off(port):
    """Drop DUT power. The teardown path, and the recovery path after a crashed hold."""
    from ppk2_api.ppk2_api import PPK2_API

    recover_streaming(port)
    ppk2 = PPK2_API(port)
    ppk2.get_modifiers()
    ppk2.toggle_DUT_power("OFF")
    return ppk2


def capture(ser, seconds):
    """Print whatever the board says for `seconds`. Returns the lines seen."""
    deadline = time.time() + seconds
    lines = []
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode(errors="replace").rstrip()
        if line:
            log(f"  {DIM}│{RESET} {line}")
            lines.append(line)
    return lines


def send(ser, command, terminators, timeout_s=3.0):
    """Send one line, read until a terminator appears. Returns (matched_line, all_lines)."""
    ser.reset_input_buffer()
    ser.write((command + "\n").encode())
    ser.flush()
    deadline = time.time() + timeout_s
    seen = []
    while time.time() < deadline:
        raw = ser.readline()
        if not raw:
            continue
        line = raw.decode(errors="replace").strip()
        if not line:
            continue
        seen.append(line)
        for term in terminators:
            if term in line:
                return line, seen
    return None, seen


def identify(ser):
    """Ask the board what it is. No answer is a result, not an error."""
    log("\n3. Firmware")
    pong, seen = send(ser, "PING", ("PONG,", "ERR,"))
    if not pong:
        log(f"  {DIM}no reply to PING — not the benchmark firmware"
            f"{' (board is saying nothing at all)' if not seen else ''}{RESET}")
        return None
    log(f"  {GREEN}PONG{RESET}  {pong}")
    info, _ = send(ser, "INFO", ("INFO,", "ERR,"))
    if info:
        log(f"  {GREEN}INFO{RESET}  {info}")
    return pong


def report_current(power, seconds, label):
    """Mean/min/max over a window. Near-zero means the rail is not reaching the board."""
    window = power.open(label, 0)
    time.sleep(seconds)
    result = power.close(window)
    if not result:
        log(f"  {RED}no samples{RESET} — the PPK2 is not streaming")
        return None
    mean_ma = result["mean_ua"] / 1000.0
    log(f"  mean {mean_ma:.2f} mA   "
        f"min {result['min_ua'] / 1000.0:.2f}   max {result['max_ua'] / 1000.0:.2f}   "
        f"({result['samples']} samples at {result['voltage_mv']} mV)")
    if mean_ma < 1.0:
        log(f"  {RED}under 1 mA — the PPK2 is probably not actually powering the board{RESET}")
    return mean_ma