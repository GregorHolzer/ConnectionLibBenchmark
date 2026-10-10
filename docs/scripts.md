# Scripts and flags

Every script in this repository, what it does, and every flag it takes. What the results mean is
in [metrics.md](metrics.md); how a field day goes is in [field-campaign.md](field-campaign.md).

Run everything with the interpreter of the venv that has `pyserial`, `ppk2-api` and `matplotlib`:

```bash
~/.venvs/platformio/bin/python bench.py …
```

| Script | Purpose |
|---|---|
| [`bench.py`](#benchpy) | the benchmark: flash, measure, record |
| [`plot.py`](#plotpy) | figures and the results table of a campaign |
| [`tools/bench_board.py`](#toolsbench_boardpy) | power the node from the PPK2 and watch its serial, outside a campaign |
| [`tests/test_host.py`](#tests) | host-side tests, no hardware needed |
| [`pio run`](#building-the-firmware-by-hand) | build the firmware without the runner |

## bench.py

```
bench.py [global flags] <command> [command flags]
```

Global flags come **before** the command.

| Global flag | Default | Meaning |
|---|---|---|
| `--campaign NAME` | `field-<today>` | Directory under `results/` that the run writes to and reads from. All distances of one day belong in one campaign. |
| `--firmware DIR` | this repository | PlatformIO project to build and flash. Point it at `../ConnectionLibBenchmarkRaw` for the no-library stack. |
| `--pio PATH` | `pio` | PlatformIO executable. |
| `--no-power` | off | Run without the PPK2. Everything is recorded except energy. Without this flag a missing PPK2 stops the run. |
| `--ignore-duty-cycle` | off | Development only. Do not wait between runs for the hourly 433 MHz airtime budget. Airtime is still logged, and `meta.json` marks the campaign with `duty_cycle_enforced: false`. The library's own guard inside each run stays active. |

### `prepare`

Once, at the desk, with both boards on USB (the node powered by the PPK2, see
[`tools/bench_board.py`](#toolsbench_boardpy)). Reads both boards' MAC addresses, writes
`boards.json`, flashes the gateway, and builds the node firmware once so that the field needs no
network.

| Flag | Required | Meaning |
|---|---|---|
| `--node-port PATH` | yes | Serial port of the node, preferably its `/dev/serial/by-id/…` path, which is stable. |
| `--gateway-port PATH` | yes | Serial port of the gateway. |
| `--zigbee-peer HEX` | yes | 64-bit address of the gateway's XBee (the coordinator), e.g. `0013A20042035B39`. |

```bash
bench.py prepare --node-port /dev/serial/by-id/usb-1a86_USB_Single_Serial_573C000618-if00 \
                 --gateway-port /dev/serial/by-id/usb-1a86_USB_Single_Serial_5748001588-if00 \
                 --zigbee-peer 0013A20042035B39
```

### `baseline`

Once per campaign, at any distance; the gateway is not involved. Four 30-second holds with
nothing sent: the board floor (every radio down), then Zigbee, ESP-NOW and LoRa each merely able
to receive. No flags.

```bash
bench.py --campaign field-20261010 baseline
```

### `site`

At every spot. Flashes and measures the Zigbee, ESP-NOW and LoRa baselines, then the other 24
points in an order shuffled per distance: the payload sizes 8, 16, 32, 84, 256 and 512 B
on every radio, and LoRa's other knobs. Runs already recorded for this distance are skipped,
so the same command resumes an interrupted visit. Ctrl-C stops between runs.

If the Zigbee or ESP-NOW baseline delivers nothing, that radio's other points at this distance
are skipped; LoRa's always run. The latest baseline decides: to test a radio again at the same
distance, run its baseline as a further round, e.g.
`site --distance 22 --rounds 2 --config zigbee/baseline --config espnow/baseline`. If it
delivers this time, the next plain `site` at that distance runs that radio's other points.

| Flag | Default | Meaning |
|---|---|---|
| `--distance METRES` | required | Distance to the gateway. Recorded with every run; `0` for the desk. |
| `--rounds N` | `1` | Repeat the whole set N times. Even rounds run in reverse order, so drift during the visit lands on both sides of every configuration. |
| `--tech NAME` | all three | Limit to `zigbee`, `espnow` or `lora`. Repeat the flag for two of them. |
| `--config KEY` | all | Limit to single configurations by name, e.g. `zigbee/payload=512`. Repeat the flag for several. |
| `--note TEXT` | empty | Free text stored in `sites.jsonl`: weather, obstacles, antenna height. |

```bash
bench.py --campaign field-20261010 site --distance 100 --note "edge of the clearing"
bench.py --campaign field-20261010 site --distance 100 --tech lora --rounds 2
```

One line is printed per run, for example:

```
[3/27] lora/baseline
  20/20 delivered, gateway counted 20, run 104.8 mA, -68 dBm at the gateway
```

`FAILED (no link …)` means the gateway did not answer the announcement on LoRa home. After a run
whose end message was not acknowledged, the script waits for the gateway's lease to run out before
the next run.

### `status`

Prints the standing currents and one line per (distance, configuration) of the campaign. No
flags; reads only, needs no hardware.

```bash
bench.py --campaign field-20261010 status
```

### What bench.py writes

| Path | Content |
|---|---|
| `boards.json` | the two boards' addresses and the node's port, from `prepare` |
| `include/bench_config.h` | the configuration of the build in progress; rewritten for every flash |
| `results/<campaign>/` | the measurements, see [metrics.md](metrics.md#files) |
| `results/lora_airtime_ledger.txt` | the node's LoRa airtime of the last hour, shared by all campaigns |

None of these are tracked by git.

### What is measured, and how often

Defined in [`bench/configs.py`](../bench/configs.py); change them there.

| | Configurations | Payloads per run |
|---|---|---|
| Zigbee | 3: baseline 32 B unicast, plus each other payload size | 100 |
| ESP-NOW | 6: baseline 32 B on channel 1, plus each other payload size | 1500 |
| LoRa | 18: baseline SF9 / 125 kHz / CR 4/7 / 7 dBm / preamble 8 / 32 B, plus each other level of one knob at a time | 20; 5 at 256 and 512 B |

Zigbee is measured at 8, 16 and 32 B only: in the field its runs with 84 B frames lost the link
mid-run ([field-campaign.md](field-campaign.md#zigbee-and-full-size-frames)). ESP-NOW and LoRa
use the payload sizes below, in two groups:

- **The comparison** (`PAYLOADS`): 8, 16, 32, 84 bytes. 84 B is Zigbee's single-packet limit, so
  this is the range all three carry unfragmented, the one the thesis methodology confines the
  comparison to.
- **Library capability** (`CAPABILITY_PAYLOADS`): 256, 512 bytes, which every radio can only
  carry fragmented. Reported apart from the comparison, see
  [metrics.md](metrics.md#larger-payloads-a-library-capability).

Other LoRa levels (`LORA_FACTORS`): spreading factor 7–12; bandwidth 125, 250 kHz; coding rate
4/5–4/8; power 2, 5, 7 dBm; preamble 8, 16. Idle holds last 30 s (`IDLE_MS`).

Configurations are named `<technology>/<what differs from the baseline>`: `zigbee/baseline`,
`espnow/payload=84`, `lora/sf=12`. The ESP-NOW channel is fixed, not swept: it changes the
interference at a site, not the radio.

To change what is measured: edit `PAYLOADS`, `CAPABILITY_PAYLOADS`, `LORA_FACTORS` or
`EXCHANGES`. A payload above 1024 B also needs `kMaxMessageBytes` raised in `src/Config.hpp`. After any change under `src/`,
run `prepare` again so the gateway carries the same firmware as the node.

## plot.py

```
plot.py --campaign NAME
```

| Flag | Required | Meaning |
|---|---|---|
| `--campaign NAME` | yes | Directory under `results/` to read. |

Prints the same table as `bench.py status` and writes four figures to `figures/<campaign>/`:

| File | Shows |
|---|---|
| `technologies.png` | Zigbee, ESP-NOW and LoRa at their baseline, one panel per metric. Dots for a single distance, lines against distance for several. |
| `payloads.png` | Zigbee, ESP-NOW and LoRa against payload size, one row per metric and one column per distance. The shaded range is the fragmented capability sizes. |
| `lora_factors.png` | Each metric against the levels of each LoRa knob, with the airtime floor under latency. One line per distance. |
| `latency_cdf.png` | The whole distribution of delivery latency per technology, one panel per distance. |

`technologies.png` and `latency_cdf.png` show the three 32 B baselines only.

## tools/bench_board.py

Powers the node from the PPK2 and, by default, shows what it prints. The node has no serial port
until the PPK2 powers it, and power lasts only while a process holds the PPK2, so by default this
tool keeps running until Ctrl-C. `bench.py baseline` and `site` power the board themselves and
cannot start while this tool holds the PPK2.

| Flag | Default | Meaning |
|---|---|---|
| *(none)* | | Power up, show the boot log for `--seconds`, then hold the rail until Ctrl-C. |
| `--once` | off | Power up, capture, drop power, exit. |
| `--no-capture` | off | Bring the rail up and hold it without reading the serial port. Use this before `bench.py prepare`. |
| `--off` | off | Drop the board's power and exit. Also un-sticks a PPK2 left streaming by a killed run. |
| `--seconds N` | `20` | How long to listen to the board. |
| `--voltage MV` | `5000` | Source voltage. Below about 4000 the board's power chip gates every rail and the board never boots, while its serial port still appears. |
| `--port PATH` | autodetect | PPK2 control port. |
| `--board-port PATH` | wait for it | Board serial port. |
| `--wait N` | `20` | Seconds to wait for the board's port to appear. |

The benchmark firmware prints `READY` and then waits for the runner, so on its own there is little
to see here beyond that line.

## Tests

```bash
~/.venvs/platformio/bin/python -m unittest discover tests
```

Covers what needs no hardware: time-on-air, the airtime ledger, the configuration sets, the
generated header, record parsing, and the path from stored runs to metrics. There is no pytest in
the venv; the tests use the standard library.

## Building the firmware by hand

`bench.py` does this itself. To check that the code builds:

```bash
pio run -e node -e gateway
```

| Environment | Image |
|---|---|
| `node` | `src/node.cpp` + `src/NodeControl.cpp`: measures one configuration |
| `gateway` | `src/gateway.cpp`: waits on LoRa home, serves one radio per run |

Without `include/bench_config.h` the node builds as an idle board (the defaults in
[`src/Config.hpp`](../src/Config.hpp)). A configuration outside the EU limits does not compile.
