# ConnectionLibBenchmark

Distance benchmark for [ConnectionLib](https://github.com/GregorHolzer/ConnectionLib): LoRa, Zigbee
and ESP-NOW on two LilyGO T-Beams, measured at a series of distances (10 m … 2 km) for the FORTE
forest-sensor deployment.

- The **gateway** is flashed once, placed, and left alone. It has no host. It waits on a fixed
  LoRa setting until the node announces a run, then serves only that run's radio.
- The **node** stays with the laptop and the PPK2. `bench.py` re-flashes it for every
  configuration, starts it, and records what it prints.

## Quick start

```bash
# once, at the desk, both boards on USB
./bench.py prepare --node-port /dev/serial/by-id/<node> --gateway-port /dev/serial/by-id/<gateway> \
                   --zigbee-peer <64-bit address of the gateway's XBee>

# once, anywhere: board floor and the standing current of each radio
./bench.py baseline

# at every spot
./bench.py site --distance 10
./bench.py site --distance 100 --note "edge of the clearing, light rain"

./bench.py status                      # what has been recorded, as a table
./plot.py --campaign field-<date>      # figures/<campaign>/: technologies, lora_factors, latency_cdf
```

`site` runs Zigbee, ESP-NOW, then 16 LoRa settings: 18 flashes, about 6 s of build and 15 s of
upload each. Interrupt it and run the same command again to resume. `--rounds 2` repeats the set
in reverse order. All sites of one day land in `results/field-<date>/` unless `--campaign` says
otherwise.

Needs PlatformIO, `pyserial`, `matplotlib` and `ppk2-api`; run with the interpreter of a venv that
has them (`~/.venvs/platformio/bin/python bench.py …`). `--no-power` records without the PPK2.
Host-side tests need no hardware: `python -m unittest discover tests`.

## How it measures

A one-way transfer, node to gateway, as fast as the link allows. The measured loop is the
library's `send()` and nothing else: it returns once the library has its confirmation of delivery
— ESP-NOW's MAC ack, Zigbee's APS ack, ConnectionLib's software ack on LoRa — and the next payload
goes out at once. Time in `send()` is the delivery latency, `acknowledged` is the delivery. No
retries, no pause, and results are kept in RAM and printed after the run, so the serial port is in
neither the timings nor the energy.

The gateway application answers no data frame. It counts what arrives and, when the node asks
after its run, sends one report with that count and the mean RSSI/SNR. Details and every metric:
[docs/metrics.md](docs/metrics.md). How the gateway follows LoRa settings, what a site costs in
duty cycle, and what limits the result: [docs/field-campaign.md](docs/field-campaign.md).

## Layout

| | |
|---|---|
| `src/node.cpp` | the measured loop; one configuration per build |
| `src/NodeControl.cpp` | around the loop: announce the run to the gateway, end it, take the report |
| `src/gateway.cpp` | the receiver: WAIT on LoRa home, RUN on the one radio a config named |
| `src/Frames.hpp` | the application frames: data, config, end, report |
| `src/Config.hpp` | the configuration macros and the EU envelope as `static_assert`s |
| `src/Transports.cpp` | the only file that names a ConnectionLib backend |
| `bench.py` | the command line |
| `bench/configs.py` | which configurations exist |
| `bench/firmware.py`, `bench/board.py` | write the config header and flash; reset and read the node |
| `bench/runner.py` | one run: duty-cycle wait, flash, capture, PPK2 windows, record |
| `bench/airtime.py` | LoRa time-on-air and the persisted duty-cycle ledger |
| `bench/records.py`, `bench/results.py`, `bench/summary.py` | parse, store, turn into metrics |
| `tools/bench_board.py` | power the node from the PPK2 and watch its serial, outside a campaign |

The firmware is configured at build time: `bench/firmware.py` writes `include/bench_config.h`,
`src/Config.hpp` includes it, and a configuration outside the EU envelope does not compile.

The no-library comparison lives in
[ConnectionLibBenchmarkRaw](https://github.com/GregorHolzer/ConnectionLibBenchmarkRaw), the same
node and gateway on the raw vendor APIs. It is driven from here with `--firmware`.

## Documentation

- [docs/field-campaign.md](docs/field-campaign.md) — running a campaign, the LoRa handshake, duty
  cycle, and the limits of what a distance campaign shows.
- [docs/metrics.md](docs/metrics.md) — what is measured, how, and what cannot be.
- [docs/regulations.md](docs/regulations.md) — the EU radio envelope the configurations stay in.
- [docs/findings.md](docs/findings.md) — the September 2026 bench campaigns (previous harness) and
  the open hardware problems, which still apply.
- [docs/benchmarking-practices.md](docs/benchmarking-practices.md) — the practices the suite follows.
