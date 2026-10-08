"""Nordic Power Profiler Kit II capture, accumulated per configuration point.

Wraps the IRNAS `ppk2-api` package. The PPK2 source-meters the **node** board through its 5 V
header pin (left header pin 10, return on pin 9) at 5.0 V, while the campaign runs normally over a
USB cable whose VBUS conductor has been cut, so the host carries data but cannot also power the
board.

**Accumulate, do not store.** The PPK2 streams tens of thousands of samples a second; what the
analysis needs per window is a mean current, a charge and a peak. The polling thread folds samples
into running sums and discards them. A raw trace is kept only for each technology's baseline run.

**Windows are opened and closed by the runner**, on the `RUN,begin` / `RUN,end` and `IDLE` lines
the node prints, and shifted by the stream's measured latency (see FALLBACK_LATENCY_S). No GPIO
marker channel is needed; what is left is a few tens of milliseconds at each edge.

Everything here is optional. If the package is missing or no PPK2 is attached, `PowerMonitor.create`
returns None and the campaign runs exactly as before, without energy columns.

Getting the device to talk at all - picking the right interface, un-wedging one left streaming,
naming whoever else holds the port - is bench/ppk2_port.py.
"""

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .ppk2_port import find_port, port_holder, recover_streaming

# Must match the rail actually sourced: energy is charge times this voltage.
DEFAULT_VOLTAGE_MV = 5000

# The reader must drain the PPK2 faster than it fills, and one read returns at most 4095 bytes.
# At the 50 ms this used to be, four fifths of the stream queued up and every window held samples
# from about 2 s *before* it was opened.
POLL_INTERVAL_S = 0.001
MAX_RAW_SECONDS = 8.0   # cap on any single retained waveform

# The PPK2's stream reaches the host about half a second after the current it describes. A window
# therefore takes the samples that *arrive* between its two instants shifted by that latency,
# which create() measures from how long switching the board on takes to show up in the stream.
FALLBACK_LATENCY_S = 0.55
POWER_ON_UA = 20_000    # the board draws well above this the moment its rail is up


@dataclass
class Window:
    label: str
    started_at: float
    keep_raw: bool = False
    samples: int = 0
    sum_ua: float = 0.0
    min_ua: float = float("inf")
    max_ua: float = 0.0
    raw: list = field(default_factory=list)
    finished_at: float = 0.0
    arrive_until: float = float("inf")   # set by close(): when its last samples will be in


class PowerMonitor:
    """Streams from a PPK2 and folds samples into whichever window is currently open."""

    def __init__(self, ppk2, voltage_mv=DEFAULT_VOLTAGE_MV, raw_dir=None, log=print,
                 latency_s=FALLBACK_LATENCY_S):
        self._ppk2 = ppk2
        self.latency_s = latency_s
        self.voltage_mv = voltage_mv
        self._raw_dir = Path(raw_dir) if raw_dir else None
        self._log = log
        self._lock = threading.Lock()
        self._windows = []
        self._stop = threading.Event()
        self._thread = None

    @classmethod
    def create(cls, port=None, voltage_mv=DEFAULT_VOLTAGE_MV, raw_dir=None, log=print):
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
                port = find_port(log)
                if port is None:
                    return None

            recover_streaming(port)
            ppk2 = PPK2_API(port)
            ppk2.get_modifiers()
            ppk2.use_source_meter()
            ppk2.set_source_voltage(int(voltage_mv))
            ppk2.start_measuring()
            latency_s = cls._measure_latency(ppk2)

            monitor = cls(ppk2, voltage_mv, raw_dir, log, latency_s)
            monitor.start()
            log(f"power: PPK2 on {port}, source mode at {voltage_mv} mV, "
                f"stream latency {latency_s * 1000:.0f} ms")
            return monitor
        except Exception as exc:                       # noqa: BLE001 - see docstring
            holder = port_holder(port)
            if holder:
                # By far the most common cause, and the library's own error ("device reports
                # readiness to read but returned no data") gives no hint of it.
                log(f"power: {port} is held by {holder} — close it and re-run "
                    f"(the nRF Connect Power Profiler keeps the port open)")
            else:
                log(f"power: PPK2 setup failed ({exc}) — continuing without energy measurement")
            return None

    @staticmethod
    def _measure_latency(ppk2):
        """Switch the board on and time how long the stream takes to show it."""
        deadline = time.time() + 1.0
        while time.time() < deadline:   # with the rail still off: let the stream run empty
            ppk2.get_data()
            time.sleep(POLL_INTERVAL_S)
        switched_at = time.time()
        ppk2.toggle_DUT_power("ON")
        while time.time() - switched_at < 3.0:
            data = ppk2.get_data()
            if data:
                result = ppk2.get_samples(data)
                samples = result[0] if isinstance(result, tuple) else result
                if samples and max(samples) > POWER_ON_UA:
                    return time.time() - switched_at
            time.sleep(POLL_INTERVAL_S)
        return FALLBACK_LATENCY_S

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
            if not self._windows:
                return
            # Negative readings are the PPK2's noise floor straddling zero: clamp them.
            cleaned = [s if s > 0 else 0.0 for s in samples]
            batch_sum = sum(cleaned)
            batch_min, batch_max = min(cleaned), max(cleaned)
            now = time.time()   # these samples describe the current of latency_s ago
            for window in self._windows:
                if not window.started_at + self.latency_s <= now < window.arrive_until:
                    continue
                window.samples += len(cleaned)
                window.sum_ua += batch_sum
                window.min_ua = min(window.min_ua, batch_min)
                window.max_ua = max(window.max_ua, batch_max)
                if window.keep_raw and now - window.started_at - self.latency_s < MAX_RAW_SECONDS:
                    window.raw.extend(cleaned)

    def open(self, label, keep_raw=False):
        """Start folding samples into a new window. `keep_raw` also keeps its first seconds."""
        window = Window(label=label, started_at=time.time(),
                        keep_raw=keep_raw and self._raw_dir is not None)
        with self._lock:
            self._windows.append(window)
        return window

    def close(self, window):
        """Close a window and return its energy summary, or None if nothing was captured.

        Blocks for the stream latency: the window's last samples are still on their way."""
        window.finished_at = time.time()
        window.arrive_until = window.finished_at + self.latency_s
        time.sleep(self.latency_s + 0.02)
        with self._lock:
            if window in self._windows:
                self._windows.remove(window)

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
        path = self._raw_dir / (window.label.replace("/", "_") + ".csv")
        # Sample index, not a timestamp per row: index and duration reconstruct the time axis.
        with path.open("w") as handle:
            handle.write(f"# window={window.label}\n")
            handle.write(f"# duration_s={window.finished_at - window.started_at:.4f} "
                          f"samples={len(window.raw)} voltage_mv={self.voltage_mv}\n")
            handle.write("sample_index,current_ua\n")
            for index, value in enumerate(window.raw):
                handle.write(f"{index},{value:.3f}\n")
        self._log(f"power: kept raw trace {path.name} ({len(window.raw)} samples)")
