"""Nordic Power Profiler Kit II capture, accumulated per configuration point.

Wraps the IRNAS `ppk2-api` package. The PPK2 source-meters the **node** board through its 5 V
header pin (left header pin 10, return on pin 9) at 5.0 V, while the campaign runs normally over a
USB cable whose VBUS conductor has been cut, so the host carries data but cannot also power the
board.

Two design decisions worth knowing:

**Accumulate, do not store.** The PPK2 streams at ~100 kSa/s. A ten-hour campaign is ~14 GB of raw
samples, which is both unwieldy and pointless: what the analysis needs per configuration point is a
mean current, a charge and a peak, not three billion individual readings. The polling thread folds
samples into running sums and discards them. Raw traces are kept only where a waveform is actually
worth looking at - the first repetition of each technology's baseline - because a few seconds
showing the TX bursts makes a genuinely useful figure.

**Windows are opened and closed by the campaign runner**, around the same instants it already
records as `started_at`/`finished_at`. Correlation is therefore exact by construction rather than a
post-hoc timestamp alignment between two independent clocks, and no GPIO marker channel is needed:
host-side windowing jitter is tens of milliseconds against points lasting seconds to minutes.

Everything here is optional. If the package is missing or no PPK2 is attached, `PowerMonitor.create`
returns None and the campaign runs exactly as before, without energy columns.
"""

import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# The 5 V header pin feeds the same net as USB VBUS, so the board sees what a USB supply would
# give it. This must match the rail actually being sourced: energy_mj is charge times this
# voltage, so a mismatch scales every energy figure silently.
DEFAULT_VOLTAGE_MV = 5000

POLL_INTERVAL_S = 0.05
MAX_RAW_SECONDS = 8.0   # cap on any single retained waveform


@dataclass
class Window:
    point_key: str
    rotation: int
    started_at: float
    keep_raw: bool = False
    samples: int = 0
    sum_ua: float = 0.0
    min_ua: float = float("inf")
    max_ua: float = 0.0
    raw: list = field(default_factory=list)
    finished_at: float = 0.0


class PowerMonitor:
    """Streams from a PPK2 and folds samples into whichever window is currently open."""

    def __init__(self, ppk2, voltage_mv=DEFAULT_VOLTAGE_MV, raw_dir=None, log=print):
        self._ppk2 = ppk2
        self.voltage_mv = voltage_mv
        self._raw_dir = Path(raw_dir) if raw_dir else None
        self._log = log
        self._lock = threading.Lock()
        self._windows = []
        self._stop = threading.Event()
        self._thread = None
        self._seen_baselines = set()
        self.total_samples = 0

    # -- construction ---------------------------------------------------------------------

    @classmethod
    def create(cls, port=None, voltage_mv=DEFAULT_VOLTAGE_MV, mode="source", raw_dir=None,
               log=print):
        """Return a started monitor, or None if no PPK2 is usable.

        Never raises: a campaign must still run when the PPK2 is absent, unplugged mid-run, or the
        package is not installed. Energy columns simply do not appear.
        """
        try:
            from ppk2_api.ppk2_api import PPK2_API
        except ImportError:
            log("power: ppk2-api not installed — continuing without energy measurement")
            return None

        try:
            if port is None:
                port = cls._find_port(log)
                if port is None:
                    return None

            cls._recover_streaming(port)
            ppk2 = PPK2_API(port)
            ppk2.get_modifiers()
            if mode == "source":
                ppk2.use_source_meter()
                ppk2.set_source_voltage(int(voltage_mv))
                ppk2.toggle_DUT_power("ON")
            else:
                ppk2.use_ampere_meter()
            ppk2.start_measuring()

            monitor = cls(ppk2, voltage_mv=voltage_mv, raw_dir=raw_dir, log=log)
            monitor.start()
            log(f"power: PPK2 on {port}, {mode} mode"
                + (f" at {voltage_mv} mV" if mode == "source" else ""))
            return monitor
        except Exception as exc:                       # noqa: BLE001 - see docstring
            holder = cls._port_holder(port)
            if holder:
                # By far the most common cause, and the library's own error ("device reports
                # readiness to read but returned no data") gives no hint of it.
                log(f"power: {port} is held by {holder} — close it and re-run "
                    f"(the nRF Connect Power Profiler keeps the port open)")
            else:
                log(f"power: PPK2 setup failed ({exc}) — continuing without energy measurement")
            return None

    @staticmethod
    def _find_port(log):
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

    @staticmethod
    def _recover_streaming(port, timeout_s=5.0):
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

    @staticmethod
    def _port_holder(port):
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

    # -- lifecycle ------------------------------------------------------------------------

    def start(self):
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        try:
            self._ppk2.stop_measuring()
            self._ppk2.toggle_DUT_power("OFF")
        except Exception:                              # noqa: BLE001
            pass

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                data = self._ppk2.get_data()
                if data:
                    result = self._ppk2.get_samples(data)
                    # get_samples returns (samples, digital) on newer versions, samples on older.
                    samples = result[0] if isinstance(result, tuple) else result
                    if samples:
                        self._accumulate(samples)
            except Exception as exc:                   # noqa: BLE001 - a USB hiccup must not take
                self._log(f"power: read failed ({exc})")   # the campaign down with it
                time.sleep(0.5)
            time.sleep(POLL_INTERVAL_S)

    def _accumulate(self, samples):
        with self._lock:
            self.total_samples += len(samples)
            if not self._windows:
                return
            # Negative readings are the PPK2's noise floor straddling zero; clamp rather than let
            # them bias the sum downward.
            cleaned = [s if s > 0 else 0.0 for s in samples]
            batch_sum = sum(cleaned)
            batch_min, batch_max = min(cleaned), max(cleaned)
            for window in self._windows:
                window.samples += len(cleaned)
                window.sum_ua += batch_sum
                window.min_ua = min(window.min_ua, batch_min)
                window.max_ua = max(window.max_ua, batch_max)
                if window.keep_raw and (time.time() - window.started_at) < MAX_RAW_SECONDS:
                    window.raw.extend(cleaned)

    # -- windows --------------------------------------------------------------------------

    def open(self, point_key, rotation):
        keep_raw = False
        if self._raw_dir and "/baseline/" in point_key and point_key not in self._seen_baselines:
            self._seen_baselines.add(point_key)
            keep_raw = True   # one waveform per technology baseline, on its first repetition only
        window = Window(point_key=point_key, rotation=rotation, started_at=time.time(),
                         keep_raw=keep_raw)
        with self._lock:
            self._windows.append(window)
        return window

    def close(self, window):
        """Close a window and return its energy summary, or None if nothing was captured."""
        with self._lock:
            if window in self._windows:
                self._windows.remove(window)
        window.finished_at = time.time()

        duration_s = window.finished_at - window.started_at
        if window.samples == 0 or duration_s <= 0:
            return None

        mean_ua = window.sum_ua / window.samples
        voltage_v = self.voltage_mv / 1000.0
        charge_mc = mean_ua * duration_s / 1000.0          # µA·s = µC, /1000 -> mC
        energy_mj = charge_mc * voltage_v                  # mC × V = mJ

        if window.keep_raw and window.raw:
            self._write_raw(window)

        return {
            "duration_s": round(duration_s, 3),
            "samples": window.samples,
            "mean_ua": round(mean_ua, 2),
            "min_ua": round(window.min_ua, 2),
            "max_ua": round(window.max_ua, 2),
            "charge_mc": round(charge_mc, 4),
            "energy_mj": round(energy_mj, 4),
            "voltage_mv": self.voltage_mv,
        }

    def _write_raw(self, window):
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        safe = window.point_key.replace("/", "_") or "unnamed"
        path = self._raw_dir / f"{safe}-rot{window.rotation}.csv"
        # Sample index rather than a timestamp per row: the PPK2's rate is fixed, so the index and
        # the recorded duration reconstruct the time axis exactly and at a fraction of the size.
        with path.open("w") as handle:
            handle.write(f"# point={window.point_key} rotation={window.rotation}\n")
            handle.write(f"# duration_s={window.finished_at - window.started_at:.4f} "
                          f"samples={len(window.raw)} voltage_mv={self.voltage_mv}\n")
            handle.write("sample_index,current_ua\n")
            for index, value in enumerate(window.raw):
                handle.write(f"{index},{value:.3f}\n")
        self._log(f"power: kept raw trace {path.name} ({len(window.raw)} samples)")
