# ConnectionLibBenchmark

Distance benchmark for [ConnectionLib](https://github.com/GregorHolzer/ConnectionLib): LoRa, Zigbee
and ESP-NOW on two LilyGO T-Beams, measured at a series of distances (10 m … 2 km) for the FORTE
forest-sensor deployment.

- The **gateway** is flashed once, placed, and left alone. It has no host and only answers.
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
./plot.py --campaign field-<date>      # figures/<campaign>/comparison.png, lora_sweep.png
```

`site` runs Zigbee, ESP-NOW, then 16 LoRa settings: 18 flashes, about 6 s of build and 15 s of
upload each. Interrupt it and run the same command again to resume. `--rounds 2` repeats the set
in reverse order. All sites of one day land in `results/field-<date>/` unless `--campaign` says
otherwise.

Needs PlatformIO, `pyserial`, `matplotlib` and `ppk2-api`; run with the interpreter of a venv that
has them (`~/.venvs/platformio/bin/python bench.py …`). `--no-power` records without the PPK2.
Host-side tests need no hardware: `python -m unittest discover tests`.

## How it measures

Stop-and-wait ping-pong, started by the node. Ping N is sent once; the node waits for the pong
with the same N, then sends N+1. No retries, so delivery is simply counted. Each pong carries the
gateway's running count of pings received, which is what splits a lost round trip into "ping
lost" and "pong lost" without a host at the gateway. Details and every metric:
[docs/metrics.md](docs/metrics.md). How the gateway follows LoRa settings, what a site costs in
duty cycle, and what limits the result: [docs/field-campaign.md](docs/field-campaign.md).

## Layout

| | |
|---|---|
| `src/node.cpp` | the measured loop; one configuration per build |
| `src/gateway.cpp` | the responder: all three radios up, LoRa on home until asked to move |
| `src/Frames.hpp` | the frames the two exchange |
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
