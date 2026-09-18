# ConnectionLibBenchmark

Benchmark suite for [ConnectionLib](https://github.com/GregorHolzer/ConnectionLib), comparing
LoRa, Zigbee and ESP-NOW on real hardware (two LilyGO T-Beams, one acting as gateway and one as
node) for the FORTE forest-sensor deployment.

Three separate per-technology benchmarks, each sweeping the hyperparameters its radio actually
exposes, plus a campaign runner that produces enough repeated measurements to judge whether the
results are *reliable* — and a script that audits the benchmark against documented good practice.

## Quick start

```bash
./sync.sh                                             # push to the Pi that holds the boards
ssh raspberrypi 'cd ~/dev/ConnectionLibBenchmark && ~/.venvs/pio/bin/pio run -t upload -e bench_node'
./run_benchmark.py --tech espnow --quick              # one technology, a few minutes
./run_campaign.py --preset full                       # the 10h campaign
./progress.py --watch                                 # live view of a running campaign
./verify_practices.py --results results/<campaign>    # the audit
./plot_results.py  --results results/<campaign>       # figures
```

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
