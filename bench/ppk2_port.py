"""Getting a PPK2 to talk: finding the right interface, and un-wedging a stuck one.

Three traps, each of which produces an error message pointing nowhere near its cause, so each is
handled here rather than left to the caller:

* A PPK2 presents **two** CDC-ACM interfaces and only the lower one carries the measurement
  stream. Opening the other yields a trickle of nonsense - in testing, 24 samples in 3 s averaging
  81 A.
* A run that ended without stop() leaves the device **streaming**, and the next handshake decodes
  those leftover sample bytes as UTF-8: `invalid start byte`.
* Another process holding the port (the nRF Connect Power Profiler is the usual culprit) surfaces
  only as "device reports readiness to read but returned no data".
"""

import os
import re
import time
from pathlib import Path


def find_port(log):
    """The PPK2's *data* interface.

    A PPK2 presents two CDC-ACM interfaces and `list_devices()` returns both, unordered. Only
    the lower one carries the measurement stream; opening the other yields a trickle of
    nonsense - in testing, 24 samples in 3 s averaging 81 A. So resolve through
    /dev/serial/by-id and take the lowest interface number rather than trusting list order.
    """
    from ppk2_api.ppk2_api import PPK2_API

    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        candidates = sorted(
            (int(re.search(r"-if(\d+)", p.name).group(1)), p.resolve())
            for p in by_id.glob("*PPK2*") if re.search(r"-if(\d+)", p.name))
        if candidates:
            return str(candidates[0][1])

    devices = PPK2_API.list_devices()
    ports = sorted(d[0] if isinstance(d, (tuple, list)) else d for d in devices)
    if not ports:
        log("power: no PPK2 found — continuing without energy measurement")
        return None
    return ports[0]


def recover_streaming(port, timeout_s=5.0):
    """Quiet a PPK2 left mid-stream by a previous run, and drain its backlog.

    `get_modifiers()` decodes the device's reply as UTF-8, so leftover sample bytes surface as
    `UnicodeDecodeError: invalid start byte` — which points nowhere near the real cause. Any
    run that ended without `stop()` leaves the device streaming, so this has to happen before
    the handshake rather than as a special case in one tool.

    One read is not enough: the buffer caps at ~4 kB while the device keeps sending, so drain
    until the port stays quiet for three consecutive polls. Backlogs of 130–160 kB are normal
    after a killed run.
    """
    try:
        import serial
        from ppk2_api.ppk2_api import PPK2_Command
        with serial.Serial(port, timeout=0.1) as ser:
            ser.write(bytes((PPK2_Command.AVERAGE_STOP,)))
            ser.flush()
            total, quiet, deadline = 0, 0, time.time() + timeout_s
            while time.time() < deadline and quiet < 3:
                waiting = ser.in_waiting
                if waiting:
                    total += len(ser.read(waiting))
                    quiet = 0
                else:
                    quiet += 1
                time.sleep(0.1)
            return total
    except Exception:                              # noqa: BLE001 - purely opportunistic
        return 0


def port_holder(port):
    """Name *another* process holding `port`, by scanning /proc. None if nothing does.

    Skipping our own pid matters: by the time this runs from `create`'s error handler, PPK2_API
    has already opened the port, so an unfiltered scan finds our own descriptor and reports the
    caller as the culprit - which hides whatever actually failed.
    """
    if not port:
        return None
    own = str(os.getpid())
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit() or proc.name == own:
                continue
            try:
                for link in (proc / "fd").iterdir():
                    if str(link.resolve()) == port:
                        name = (proc / "comm").read_text().strip()
                        return f"{name} (pid {proc.name})"
            except (OSError, PermissionError):
                continue
    except OSError:
        pass
    return None
