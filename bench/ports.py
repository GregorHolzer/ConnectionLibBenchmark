"""Finding the boards, flashing them, and making sure pyserial is importable.

Split from device.py, which is the serial client itself: this is everything that happens *before*
a Device object exists. Port discovery filters by USB vendor id rather than by device-name glob,
because the bench also carries a PPK2 whose own two interfaces enumerate as ttyACM nodes right
beside the boards.

Deliberately imports no pyserial, and device.py depends on this module rather than the reverse:
ensure_pyserial() exists to re-exec when pyserial is absent, so it cannot live behind an import
that needs pyserial to succeed.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class DeviceError(RuntimeError):
    pass


# USB vendor IDs that are emphatically not benchmark boards. The PPK2 is the one that matters:
# it presents *two* CDC-ACM interfaces, so an unfiltered scan on a bench with the power monitor
# attached sees four serial devices instead of two and either fails the "expected exactly 2" check
# or, worse, hands a PPK2 interface to the flasher.
NON_BOARD_VIDS = {
    "1915",   # Nordic Semiconductor — Power Profiler Kit II
}


def detect_ports(pio="pio", expect=None, timeout_s=0.0):
    """The real USB serial devices that could be benchmark boards.

    Ignores the virtual /dev/ttyS* that flood Linux, and anything whose vendor ID is known not to
    be a board (see NON_BOARD_VIDS).

    `expect` + `timeout_s` poll until that many boards are present. Needed whenever the PPK2 is
    source-metering a board: it *is* that board's supply, and the board's USB-serial chip runs off
    the board rail (the cable has VBUS cut), so for a second or two after the rail comes up the
    board has no serial port to find. A single scan in that window sees one board and fails.
    """
    if expect is None:
        return _scan_ports(pio)
    deadline = time.time() + timeout_s
    while True:
        ports = _scan_ports(pio)
        if len(ports) >= expect or time.time() >= deadline:
            return ports
        time.sleep(0.5)


def wait_for_port(port, timeout_s=20.0):
    """Block until `port` exists. Returns True if it showed up.

    Needed even when ports are named explicitly: a board the PPK2 source-meters has no serial port
    until the rail is up, and the CH9102 takes a second or two to enumerate after it is. Skipping
    this makes the first esptool or serial open fail on a board that is merely still booting.
    """
    deadline = time.time() + timeout_s
    while not os.path.exists(port) and time.time() < deadline:
        time.sleep(0.25)
    return os.path.exists(port)


def _scan_ports(pio):
    result = subprocess.run([pio, "device", "list"], capture_output=True, text=True, check=True)
    ports, current = [], None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("/dev/tty") and not line.startswith("/dev/ttyS"):
            current = line
        elif line.startswith("Hardware ID:") and "USB VID" in line and current:
            match = re.search(r"VID:PID=([0-9A-Fa-f]{4}):", line)
            if not match or match.group(1).lower() not in NON_BOARD_VIDS:
                ports.append(current)
            current = None
    return ports


def flash(pio, env, port):
    """Build and upload. Called at most twice per campaign - everything else is runtime CFG."""
    subprocess.run([pio, "run", "-e", env, "-t", "upload", "--upload-port", port],
                    check=True, cwd=REPO_ROOT)


def read_mac_for_port(port, pio="pio"):
    result = subprocess.run(
        [pio, "pkg", "exec", "--", "esptool.py", "--port", port, "read_mac"],
        capture_output=True, text=True, check=True, cwd=REPO_ROOT)
    macs = re.findall(r"MAC: ([0-9A-Fa-f:]{17})", result.stdout)
    if not macs:
        raise DeviceError(f"no MAC in esptool output for {port}")
    return macs[-1]


def ensure_pyserial(pio):
    """Re-exec under PlatformIO's own interpreter if this one lacks pyserial or ppk2-api.

    Debian's and Arch's system Pythons are both EXTERNALLY-MANAGED with no usable pip, but
    PlatformIO's venv always has pyserial as a hard dependency.

    `ppk2_api` is checked for the same reason even though energy measurement is optional. The
    system Python here *does* have pyserial and *does not* have ppk2-api, so the old check passed
    and `./run_campaign.py` then ran with no energy at all - while the same command under the
    PlatformIO venv measured normally. In source mode that difference is fatal rather than merely
    quiet, because the PPK2 is the node's only supply.
    """
    missing = [name for name in ("serial", "ppk2_api") if not _importable(name)]
    if not missing:
        return

    if os.environ.get("_BENCH_REEXECED"):
        if "serial" in missing:
            sys.exit(f"pyserial still missing after re-exec; install it for {sys.executable}")
        # Only ppk2-api absent after re-exec: genuinely optional, and PowerMonitor.create says so.
        return
    pio_bin = Path(shutil.which(pio) or pio).resolve()
    for name in ("python3", "python"):
        candidate = pio_bin.parent / name
        if candidate.exists():
            os.environ["_BENCH_REEXECED"] = "1"
            os.execv(str(candidate), [str(candidate), str(Path(sys.argv[0]).resolve())] + sys.argv[1:])
    sys.exit(f"{', '.join(missing)} not available and no interpreter found next to {pio_bin}")


def _importable(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False
