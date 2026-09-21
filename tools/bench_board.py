#!/usr/bin/env python3
"""Power a board from the PPK2 and talk to it.

    ./tools/bench_board.py                 # power up, show the boot log, hold the rail
    ./tools/bench_board.py --once          # power up, capture, drop power, exit
    ./tools/bench_board.py --no-capture    # just bring the rail up and hold it
    ./tools/bench_board.py --off           # drop DUT power and exit

The ordering here is inverted from the usual bench script, and that is the whole reason this tool
exists. The board's CH9102 USB-serial chip is fed from the board rail, not from USB VBUS - the
VBUS conductor in the cable is cut, so the host carries data but cannot power anything. The board
therefore has **no serial port at all until the PPK2 powers it**: power first, then enumerate,
then open. `tools/energy_check.py` cannot be used for this because it opens its `Device`s before
touching the PPK2, and it wants two boards.

Two further traps, both of which produce silence rather than an error:

* **Closing the PPK2 API session drops DUT power.** Power exists only while a process holds the
  connection, which is why the default mode holds instead of exiting. The board's UART is released
  before holding, so `pio` and `esptool` can still use it.
* **Source mode must be 5.0 V.** Below ~4 V the AXP2101 rejects its VBUS input and gates every
  ESP32 rail, so the CH9102 still enumerates while the board never boots. That reads as a dead
  UART, not as a power problem.

Deliberately not using `bench.device.Device` for the capture: its constructor sleeps past the
reset and calls `reset_input_buffer()`, which discards exactly the boot log we want to see.
"""

import argparse
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.power import DEFAULT_VOLTAGE_MV, PowerMonitor

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

    PowerMonitor._recover_streaming(port)
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


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=20.0,
                        help="how long to listen to the board (default: 20)")
    parser.add_argument("--voltage", type=int, default=DEFAULT_VOLTAGE_MV,
                        help="source voltage in mV (default: 5000; below ~4000 the AXP2101 "
                             "gates every rail and the board silently never boots)")
    parser.add_argument("--once", action="store_true",
                        help="capture, then drop power and exit instead of holding")
    parser.add_argument("--no-capture", action="store_true",
                        help="bring the rail up and hold it, without reading the UART")
    parser.add_argument("--off", action="store_true",
                        help="drop DUT power and exit")
    parser.add_argument("--port", help="PPK2 control port (default: autodetect via by-id)")
    parser.add_argument("--board-port", help="board serial port (default: wait for it to appear)")
    parser.add_argument("--wait", type=float, default=20.0,
                        help="seconds to wait for the board to enumerate (default: 20)")
    args = parser.parse_args()

    ppk2_port = args.port or PowerMonitor._find_port(log)
    if not ppk2_port:
        return 1

    if args.off:
        log(f"dropping DUT power on {ppk2_port}")
        power_off(ppk2_port)
        log(f"{GREEN}power off{RESET} — the board's serial port should now disappear")
        return 0

    holder = PowerMonitor._port_holder(ppk2_port)
    if holder:
        log(f"{RED}{ppk2_port} is held by {holder}{RESET}")
        log("close it and re-run — the nRF Connect Power Profiler keeps the port open exclusively")
        return 1

    log(f"1. PPK2 on {ppk2_port}")
    drained = PowerMonitor._recover_streaming(ppk2_port)
    if drained:
        log(f"  {DIM}drained {drained} bytes of leftover stream from a previous run{RESET}")
    power = PowerMonitor.create(port=ppk2_port, voltage_mv=args.voltage, mode="source", log=log)
    if power is None:
        return 1

    released = False

    def release(*_):
        nonlocal released
        if not released:
            released = True
            log(f"\n{DIM}releasing — dropping DUT power{RESET}")
            power.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, release)
    signal.signal(signal.SIGTERM, release)

    try:
        log(f"\n2. Waiting for the board to enumerate (up to {args.wait:.0f}s)")
        board = Path(args.board_port) if args.board_port else wait_for_board(args.wait)
        if board is None:
            log(f"  {RED}no board appeared{RESET}")
            report_current(power, 2.0, "noboard")
            log("\n  A near-zero current means the rail is not reaching the board — check the")
            log("  5 V header pin (left header pin 10, return on pin 9).")
            log("  A healthy current with no serial port means the USB data path is at fault:")
            log("  the node board has a known intermittent USB fault that defeats a cable swap.")
            return 1
        log(f"  {GREEN}{describe_board(board)}{RESET}")
        log(f"  {DIM}{board}{RESET}")

        lines = []
        if not args.no_capture:
            import serial
            # Opening toggles DTR/RTS and resets the ESP32, so this is a boot log from reset
            # rather than whatever happened to be mid-stream.
            with serial.Serial(str(board), BOARD_BAUD, timeout=0.2) as ser:
                log(f"\n3. Listening for {args.seconds:.0f}s at {BOARD_BAUD} "
                    f"{DIM}(opening the port resets the ESP32){RESET}")
                lines = capture(ser, args.seconds)
                if not lines:
                    log(f"  {RED}silence{RESET} — powered and enumerated, but nothing on the UART")
                identify(ser)

        log("\n4. Current")
        report_current(power, 3.0, "hold")

        if args.once:
            log(f"\n{DIM}--once: dropping power{RESET}")
            released = True
            power.stop()
            log(f"{GREEN}done{RESET} — nothing left running, board unpowered")
            return 0

        log(f"\n{GREEN}holding power at {args.voltage} mV{RESET} (pid {os.getpid()})")
        log(f"{DIM}the board's UART is released, so pio/esptool can use it{RESET}")
        log(f"{DIM}Ctrl-C, or kill this pid, to drop power{RESET}")
        while True:
            time.sleep(1)
    finally:
        if not released:
            released = True
            power.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
