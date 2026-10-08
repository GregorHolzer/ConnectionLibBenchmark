# Recorded metrics

What the benchmark writes down, field by field, and what is computed from it. How to run it is in
[scripts.md](scripts.md); the design behind it is in [field-campaign.md](field-campaign.md).

## What one run is

The node sends payloads to the gateway one way, back to back. The measured loop is the library's
`send()` and nothing else (`src/node.cpp`):

- `send()` returns when the library has its confirmation of delivery, or has given up waiting:
  ESP-NOW's MAC ack, Zigbee's APS ack, ConnectionLib's software ack on LoRa.
- Each payload is sent once. The benchmark adds no retries and no acknowledgement of its own.
- A run stops after 10 unacknowledged payloads in a row.
- Results are kept in RAM and printed after the run, so the serial port is in neither the timings
  nor the energy.

The PPK2 meters the node for the whole run.

## Files

Everything of a campaign is in `results/<campaign>/`.

| File | One entry per | Content |
|---|---|---|
| `meta.json` | campaign | boards, firmware stack, start time, whether the duty cycle was enforced between runs |
| `sites.jsonl` | `site` invocation | distance, free-text note, time |
| `runs.jsonl` | flashed configuration | the run record, [below](#per-run-runsjsonl) |
| `exchanges.csv` | payload | the per-payload record, [below](#per-payload-exchangescsv) |
| `serial.log` | run | every record line the node printed, verbatim |
| `raw/<config>-<distance>m-r<round>.csv` | baseline run | the first 8 s of the current waveform, one sample per row |

## Per payload: `exchanges.csv`

| Column | Unit | Meaning |
|---|---|---|
| `distance_m`, `round`, `key` | | which run this payload belongs to; `key` is `<technology>/<configuration>` |
| `seq` | | position in the run, from 0 |
| `payload_bytes` | bytes | application payload handed to `send()` |
| `total_bytes` | bytes | what the library reports it put on the air: payload plus the library's own framing (7 bytes on LoRa, 0 on ESP-NOW and Zigbee). Radio-level framing is not included. |
| `latency_us` | µs | time inside `send()`: from handing over the payload until the library had its ack or gave up |
| `status`, `status_name` | | the library's `SendStatus`: `Ok`, `Timeout`, `Failed`, `DutyCycleExceeded`, … |
| `acked` | 0/1 | whether the library reported the payload acknowledged. This is the delivery signal. |
| `duty_pct` | % | the library's duty-cycle figure after this payload: airtime used in its one-hour window. It starts from zero at every flash. |

## Per run: `runs.jsonl`

| Field | Meaning |
|---|---|
| `distance_m`, `round`, `key`, `tech`, `name` | where and what; `distance_m` is null for baseline holds |
| `params` | the configuration: payload size, and for LoRa `sf`, `bw`, `cr`, `pwr`, `preamble` |
| `exchanges_planned` | payloads the run was built to send; 0 for a baseline hold |
| `started_at`, `run_id` | start time; the one-byte id the gateway sees |
| `home.acked`, `home.attempts` | whether the gateway acknowledged the announcement on LoRa home, and on which attempt (1–3) |
| `summary.sent`, `summary.acked` | payloads sent and acknowledged |
| `summary.elapsed_ms` | wall clock of the loop, first `send()` to last return |
| `summary.duty_pct` | the library's duty-cycle figure at the end of the run |
| `summary.aborted` | 1 if the run stopped after 10 losses in a row |
| `report.requested` | whether the gateway acknowledged the end message |
| `report.received` | payloads the gateway itself counted in this run |
| `report.rssi_dbm`, `report.snr_db` | mean signal strength and signal-to-noise ratio of those payloads at the gateway. LoRa only. |
| `delivery.pdr`, `delivery.gateway_pdr` | `acked / sent` and `received / sent` |
| `energy.run`, `energy.idle` | the two power windows, [below](#energy) |
| `idle_ms` | length of the idle hold the node reports, for baseline runs |
| `payload_airtime_ms` | calculated time-on-air of one data frame; 0 off LoRa |
| `duty_wait_s` | how long the script waited for airtime budget before this run |
| `error`, `complete` | `ERR` text or `timeout`; whether the node reached `END` |

## Derived metrics

Computed by `bench/summary.py`, one row per (distance, configuration), rounds pooled. These are
what `bench.py status` and `plot.py` show.

| Metric | Unit | Definition |
|---|---|---|
| Delivery (`pdr`) | fraction | acknowledged ÷ sent. What an application using the library sees. |
| Delivery at the gateway (`gateway_pdr`) | fraction | gateway's count ÷ sent, over runs whose report came back. Can exceed `pdr`: a payload can arrive while its ack is lost. |
| Delivery latency (`latency_us`) | µs | distribution of `latency_us` over acknowledged payloads: median, min, max, quartiles, IQR, 95th and 99th percentile, mean |
| Median confidence interval (`latency_median_ci_us`) | µs | non-parametric 95 % interval for the median; absent below 6 samples |
| Goodput (`goodput_bps`) | bit/s | acknowledged payload bits ÷ `elapsed_ms`. One way, of a back-to-back burst. |
| Framing efficiency (`framing_efficiency`) | fraction | payload bytes ÷ `total_bytes`. Library framing only. |
| Signal at the gateway (`gw_rssi_dbm`, `gw_snr_db`) | dBm, dB | mean of the runs' reports. LoRa only. |
| Duty cycle (`duty_pct`) | % | highest per-payload value in the run |
| Run current (`energy.mean_ua`) | µA | mean current of the node over the loop |
| Energy per sent payload (`energy_per_sent_mj`) | mJ | energy of the loop ÷ payloads sent |
| Energy per delivered payload (`energy_per_delivered_mj`) | mJ | energy of the loop ÷ payloads acknowledged. Rises as the link degrades, because waiting for lost acks is paid too. |
| … above standing current (`excess_energy_per_delivered_mj`) | mJ | (run current − that radio's standing current) × duration × voltage ÷ acknowledged. Needs the baseline. |
| Standing current | µA | mean current of each 30 s baseline hold: `none` (board floor), `zigbee`, `espnow`, `lora` |

Single numbers are medians, never means, and percentiles are nearest-rank, not interpolated.

## Energy

A Nordic PPK2 sources and meters the node at its 5 V header pin, 5.0 V, with the battery removed
and the USB cable's power wire cut. Each window record holds:

| Field | Unit | Meaning |
|---|---|---|
| `duration_s` | s | wall clock between the two marker lines |
| `samples` | | samples folded into the window (about 71,000 per second) |
| `mean_ua`, `min_ua`, `max_ua` | µA | of those samples |
| `charge_mc` | mC | mean current × duration |
| `energy_mj` | mJ | charge × voltage |
| `voltage_mv` | mV | the sourced voltage |

There are two windows:

- **`run`**: from `RUN,begin` to `RUN,end`, i.e. the `send()` loop only. The announcement before
  it and the report after it are outside.
- **`idle`**: from `IDLE,begin` to `IDLE,end`, a baseline hold with the radio able to receive and
  nothing sent.

The PPK2's samples reach the host about 0.47 s after the current they describe. The script
measures that latency at every start and shifts each window by it; what is left is a few tens of
milliseconds at each edge. Runs are sized to last seconds for that reason.

## What to keep in mind when reading the numbers

- **Absolute currents are high by a constant.** The 5 V tap includes the power converter's loss,
  the USB-serial chip, and the XBee, which is powered whatever firmware runs. Differences between
  configurations are the trustworthy quantity.
- **Zigbee's standing current is inside the board floor**, for the same reason. The `zigbee` hold
  reads the same as `none`; that does not mean the XBee is free.
- **"Above standing current" is not purely the radio.** In a hold the processor mostly sleeps;
  during a LoRa `send()` the radio driver keeps it busy. Part of the difference is the ESP32.
- **Goodput is burst goodput.** On LoRa the 10 % duty cycle limits the sustained rate to about a
  tenth of it.
- **Zigbee latency is mostly the serial link** between the ESP32 and the XBee at 9600 baud.
- **Energy is the sender's.** The gateway is not metered.
- **The LoRa SNR reading tops out** around +14 dB; above that it says only "strong".

## Not recorded

- **Signal quality for ESP-NOW and Zigbee.** Neither exposes it without perturbing the
  measurement, and the library version in use has no capture for it. Neither exposes SNR at all.
- **Per-payload signal quality.** The gateway reports one mean per run; the library's ack does
  not carry RSSI back to the node.
- **One-way delay and jitter.** They need synchronised clocks. Latency here includes the ack's
  way back.
- **Radio-internal retries and channel-access counters.** ESP-NOW and Zigbee retry inside the
  radio and expose only the outcome; their retries are part of the latency.
- **Bytes on the air below the library**, i.e. the 802.11 and 802.15.4 framing.
- **The gateway's energy.**
- **Environment**: temperature, interference, exact antenna placement. Use `--note`.

## The no-library comparison

[ConnectionLibBenchmarkRaw](https://github.com/GregorHolzer/ConnectionLibBenchmarkRaw) runs the
same node and gateway on the vendor APIs and records exactly the same fields, with
`meta.json` → `firmware.stack` set to `raw`. It covers ESP-NOW and Zigbee only: on LoRa the
delivery signal is a ConnectionLib feature with no raw counterpart.
