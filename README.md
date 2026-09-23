# ConnectionLibBenchmark

Benchmark suite for [ConnectionLib](https://github.com/GregorHolzer/ConnectionLib), comparing
LoRa, Zigbee and ESP-NOW on real hardware (two LilyGO T-Beams, one acting as gateway and one as
node) for the FORTE forest-sensor deployment.

Three separate per-technology benchmarks, each sweeping the hyperparameters its radio actually
exposes, plus a campaign runner that produces enough repeated measurements to judge whether the
results are *reliable* — and a script that audits the benchmark against documented good practice.

## Quick start

The boards are attached to the machine you run this on — everything is local.

```bash
pio run -t upload -e bench_node                       # flash, once per campaign
pio run -t upload -e bench_gateway
./tools/selftest.py                                   # prove the hardware path first
./run_benchmark.py --tech espnow --quick              # one technology, a few minutes
./run_campaign.py --preset full                       # the 10h campaign
./progress.py --watch                                 # live view of a running campaign
./verify_practices.py --results results/<campaign>    # the audit
./plot_results.py  --results results/<campaign>       # per-technology figures
./plot_comparison.py --results results/<campaign>     # cross-technology comparison
```

Two arms and two capture modes, both off by default so every campaign stays comparable with those
recorded before they existed:

```bash
./run_campaign.py --preset standard --rssi   # + per-packet RSSI/SNR (perturbs ESP-NOW & Zigbee)
```

`stack=raw` points are swept automatically alongside the library ones: the same exchange driven
straight onto RadioLib / `esp_now.h` / xbee-arduino, so the difference measures what ConnectionLib
costs. See [docs/metrics.md](docs/metrics.md) for what each arm does and does not bypass.

`plot_results.py` asks what each radio's knobs do; `plot_comparison.py` asks how the three compare
against each other, on the RQ1 metric set, with each technology represented by its best
configuration rather than its baseline:

```bash
./plot_comparison.py --results results/<campaign> --report-flat   # + which knobs do nothing
./plot_comparison.py --results results/<campaign> --best-by latency --pdr-floor 0.99
```

Sweeps whose knob moves neither latency nor energy can be left out of the per-technology set:

```bash
./plot_results.py --results results/<campaign> \
    --skip-factor espnow:channel --skip-factor espnow:timeout
```

Requires PlatformIO, `pyserial`, `matplotlib` and `ppk2-api` (the last only for energy
measurement). A venv containing all of them is the easiest route; symlink its `pio` onto your
`PATH`.

### Waking the bench remotely

The bench host is normally off. A Raspberry Pi on the same network acts as an always-on jump host
and power switch, so the working chain from outside is:

```
VPN → raspberrypi → start_desktop → ssh desktop → work
```

`start_desktop`, `stop_desktop` and `desktop_status` live in `~/.local/bin` on the Pi.
`stop_desktop` refuses to power the machine off while a campaign is running — a full campaign is
ten hours of bench time and is not reproducible on a whim.

> **Historical note.** The boards used to live on the Pi itself, driven over SSH with a `sync.sh`
> that pushed this repo to it. That is gone: the boards are now on the bench host directly, which
> removes a whole class of problems (a `--delete` sync that could destroy a results tree, the Pi's
> USB power budget, and serial latency over two hops). None of the benchmark code changed in the
> move — it always ran locally against local serial ports.

## Why one firmware for everything

All three backends are compiled into a single image and selected at run time over a serial
command protocol (`CFG …` / `RUN`). A campaign visits several hundred configuration points, and
rebuilding plus reflashing both boards costs ~80 s each time — build-time configuration would
spend the campaign flashing instead of measuring. Runtime configuration needs exactly two flashes
for the whole run. This required no changes to ConnectionLib.

## The three benchmarks

| | Swept | Points | Exchanges/point |
|---|---|---|---|
| **LoRa** | spreading factor, bandwidth, coding rate, TX power, preamble, payload | 15 | 20 |
| **ESP-NOW** | channel, encryption, payload, send timeout | 30 | 100 |
| **Zigbee** | payload, unicast vs. broadcast | 8 | 100 |

The asymmetry is a finding, not an oversight: ConnectionLib exposes LoRa's PHY parameters and
deliberately hides ESP-NOW's and Zigbee's, because those radios manage their own.

**LoRa's duty cycle governs the whole campaign.** The 10 % ETSI limit means wall clock is at least
ten times airtime, and one transmission at SF12/32 B costs 2.5 s. That is why the full campaign is
budgeted at 10 h and why LoRa completes ~6 repetitions where ESP-NOW completes ~28. The constraint
is reported as a result rather than relaxed — see [docs/regulations.md](docs/regulations.md).

## Documentation

- [docs/benchmarking-practices.md](docs/benchmarking-practices.md) — the good practices this suite
  is built to follow, written as checkable statements. `verify_practices.py` enforces them.
- [docs/regulations.md](docs/regulations.md) — the EU radio parameter envelope. Sweeps move within
  it; the firmware rejects configurations that would leave it.
- [docs/metrics.md](docs/metrics.md) — what is measured, how, and what this bench honestly cannot
  measure.
- [docs/findings.md](docs/findings.md) — the measured state: cross-technology comparison, what
  ConnectionLib costs against the raw radio API, signal quality, and the open problems. Every
  number traces to a recorded campaign.
- [docs/field-campaign.md](docs/field-campaign.md) — the design for running unattended across up to
  4 km with one host: a lease-based control channel and gateway-side logging. Not implemented yet.
