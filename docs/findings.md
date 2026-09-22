# Findings

Measured state of the benchmark as of 2026-09-22. Every number here comes from a recorded campaign
under `results/`; nothing is estimated unless it says so. Where a figure contradicts an earlier
claim in this repo, the contradiction is stated rather than quietly corrected — a benchmark whose
history is edited is worth less than one whose mistakes are legible.

Two campaigns are referenced throughout:

| | `campaign-20260921-083956` | `campaign-20260922-084620` |
|---|---|---|
| Points | 220 / 220, 0 failures | 66 of 232 (stopped early) |
| Rotations | 4 (3 for LoRa) | 1 |
| Duration | 1 h 54 m | partial |
| Raw arm | absent (predates it) | ESP-NOW ×4, LoRa ×4 |
| `rssi_capture` | false | false |

## 1. Cross-technology comparison

Best configuration of each technology, selected by lowest energy per delivered payload among
configurations clearing a 95 % delivery ratio (`plot_comparison.py --best-by energy`). From
`campaign-20260922-084620`:

| | ESP-NOW | LoRa | Zigbee |
|---|---|---|---|
| Configuration | `combination/encrypt=1+payload=16` | `stack/raw+payload=8` | `payload/8` |
| Energy per delivered payload | 70.7 mJ | 153.2 mJ | 71.9 mJ |
| — radio's own share | 27.2 mJ | 25.9 mJ | 5.6 mJ |
| Median round-trip latency | 2.44 ms | 294.5 ms | 125.2 ms |
| Delivery ratio | 100 % | 100 % | 100 % |
| Goodput | 1267 bit/s | 217 bit/s | 416 bit/s |
| Standing current above floor | 55.2 mA | 9.8 mA | 9.9 mA |

Board floor (cold boot, every radio down): **85.7 mA** at the 5 V header, confirmed independently
by a bench measurement of 85.34 mA on an idle node.

**Standing current is the number that decides battery life here.** At a 15-minute reporting
interval — FORTE's low-rate periodic profile — the radio's daily budget is 99.96–99.99 % standing
draw and 0.01–0.04 % messages:

| | standing | 96 messages/day | total |
|---|---|---|---|
| ESP-NOW | 1589 mAh | 0.148 mAh | 1589 mAh |
| LoRa | 240.4 mAh | 0.094 mAh | 240.5 mAh |
| Zigbee | 241.5 mAh | 0.025 mAh | 241.5 mAh |

Per-packet efficiency is therefore close to irrelevant for this deployment, which is what
`rq1-methodology.tex` predicts from `pfister2025`'s months-on-battery requirement but nothing had
measured.

## 2. What the library costs — ConnectionLib against the raw radio API

`stack=raw` drives the vendor API directly with ConnectionLib out of the path, measured by the same
loop with the same timing points, so the difference is attributable to the library. See
`docs/metrics.md` for exactly what each arm bypasses.

| | payload | latency delta | bytes on air (lib vs raw) | energy delta |
|---|---|---|---|---|
| **ESP-NOW** | 8 B | +0.10 ms | 8 vs 8 | +0.1 mJ |
| | 16 B | +0.00 ms | 16 vs 16 | −0.9 mJ |
| | 32 B | +0.10 ms | 32 vs 32 | −0.8 mJ |
| | 84 B | +0.10 ms | 84 vs 84 | −0.5 mJ |
| **LoRa** | 8 B | +114.76 ms | 14 vs 8 | +60.1 mJ |
| | 16 B | +114.95 ms | 22 vs 16 | +60.1 mJ |
| | 32 B | +57.69 ms | 38 vs 32 | +28.1 mJ |
| | 84 B | +114.77 ms | 90 vs 84 | +61.3 mJ |

**ConnectionLib is free on ESP-NOW** — every delta is inside the noise — and costs real airtime on
LoRa, where it must add a 6-byte header because a raw SX1278 has no addressing of its own.

The LoRa step is **not constant**, because time-on-air is quantised to whole symbol blocks: six
bytes buys one extra block at a 32-byte payload and two at 8, 16 and 84. Predicted round-trip cost
from the symbol formula is 114.69 / 114.69 / 57.34 / 114.69 ms against measured 114.76 / 114.95 /
57.69 / 114.77 — agreement within 0.35 ms, which is the strongest validation the airtime model has.

Zigbee's raw arm has not run yet; the campaign stopped before reaching it.

> **Correction.** An earlier draft of this comparison predicted a flat +28.7 ms. That was wrong
> twice over: it used `time_on_air_ms()`, which hardcoded the 6-byte header and so counted it on
> both sides of the subtraction, and it quoted the header's share of *bytes* (43 % at 8 B) as if it
> were its share of *airtime* (14.5 %). The hardcoding also over-charged raw LoRa points against the
> duty-cycle budget. `time_on_air_ms()` now takes `header_bytes`, and `_toa()` passes 0 for the raw
> arm.

## 3. Signal quality — and a 16 dB link regression

RSSI and SNR are **observed metrics, not comparison axes**, for the reasons in `baccour2012`
(see §6). Availability, measured rather than assumed:

| | RSSI | SNR |
|---|---|---|
| LoRa | yes, free | yes, free |
| ESP-NOW | only under `--rssi` (promiscuous sniffer) | none exists at any core version |
| Zigbee | only under `--rssi` (`DB` AT query) | none exists |

**The LoRa link lost about 16 dB between the two campaigns:**

| | 2026-09-21 | 2026-09-22 |
|---|---|---|
| RSSI at the node | −119 dBm | −135 dBm |
| SNR at the node | +1.5 dB | −13 dB |

That regression, not any property of the benchmark, is what broke the second campaign. SNR margin
against each spreading factor's demodulation floor predicts delivery almost exactly:

| point | SF | SNR | floor | margin | PDR |
|---|---|---|---|---|---|
| `sf/7` | 7 | — | −7.5 | ≈ −4.5 | 0 % (never decoded) |
| `sf/8` | 8 | −12.0 | −10.0 | −2.0 | 20.8 % |
| `baseline` | 9 | −10.8 | −12.5 | +1.7 | 100 % |
| `pwr/5` | 9 | −15.0 | −12.5 | −2.5 | 4.2 % |
| `cr/5` | 9 | −14.2 | −12.5 | −1.7 | 29.4 % |
| `sf/10` | 10 | −12.8 | −15.0 | +2.2 | 100 % |
| `sf/12` | 12 | −12.2 | −20.0 | +7.8 | 100 % |

Every point with positive margin delivers 100 %; every point below −1.7 dB fails. SF7 is about
4.5 dB short of closing, so it decodes nothing — which is why that point timed out and, before the
fix in §5, took the following six with it.

## 4. The transmit path draws no power

Investigated with the `RFCHECK` firmware command, which drives the SX1278 directly with no library
in the path, measured on the PPK2.

**Healthy:** `radio.begin()` finds the chip; time-on-air is exact (160 ms at SF7, 516 ms at SF9,
3.65 s at SF12 for 64 B); every transmit returns success. **Registers correct:** `PA_CONFIG 0xF5`
selects PA_BOOST at 7 dBm, `PA_DAC 0x84` normal, `OCP` on at a 60 mA trim. **Noise floor normal:**
−121.9 dBm, where LoRa at BW125 belongs.

**But the PA draws nothing.** At SF12, ten transmissions of 3.65 s each — 36.5 s of solid keying,
90 % of the measurement window:

| | mean current |
|---|---|
| Radio idle in receive | 85.8 mA |
| During 36.5 s of transmitting, PA_BOOST | 85.8 mA (+0.0) |
| During 36.5 s of transmitting, RFO | 86.0 mA (+0.4) |

An SX1278 at +7 dBm should draw about +18 mA on either path. Neither output pin draws, so this is
not a pin-selection bug. Combined with a received signal ~100 dB below the ≈ −18 dBm expected at a
metre, the transmit chain is not delivering RF power despite correct configuration.

This is unresolved and is the single biggest obstacle to trustworthy LoRa data. Distinguishing an
unterminated antenna port from a wrong-band antenna from a failed matching network needs a second
radio or a VNA; it cannot be settled from software. `RFCHECK` is the two-second check for whether a
repair worked.

## 5. Robustness fixes this exposed

**Run timeouts assumed no retries.** The firmware allows five attempts per sequence number
(`BenchRunner.cpp`), each costing a full reply-wait, so a point estimated at 7 s can legitimately
take 210 s. The host waited `estimated × 3 + 60` = 81 s, gave up while the board was still working,
and fired the next CFG into a stream nobody was reading — so the *following* six points failed for
reasons unrelated to their own configuration. `run_timeout()` now derives the bound from what the
firmware can actually do, capped at 300 s; `lora/sf/7` went from 81 s to 240 s.

**Failures cascaded.** `Device.resync()` now waits for an overrunning board to go quiet before the
next CFG, so one marginal point fails alone.

## 6. What the metric set rests on

The full derivation is in ConnectionLib's `docs/measurement-methodology.md`. In brief, per source:

| Source | What it contributes |
|---|---|
| `boano2018` (IoTBench) | The input / output / observed / experiment-parameter taxonomy, the fairness condition ("behaviour determined by the exact same set of input parameters"), and the low-rate environmental-monitoring profile |
| `pfister2025`, `pfister2024` (FORTE) | The measurement profile: star topology single hop, ≥ 50 m range, satellites months on battery, under canopy below 2 m, low-rate periodic traffic |
| `ojo2021` | PDR as a function of payload length (hence payload is swept, not fixed); EIRP normalisation within a band (9.9 / 10.4 dBm at 433 / 868 MHz) |
| `becker2025` | Receiver-side PDR, one-way delay and jitter; RBS clock synchronisation; non-monotonic range (dead at 59/61/64 m while working at 62/65 m), hence a position grid rather than a line |
| `baccour2012` | Why RSSI and SNR are demoted to observed metrics: read only from packets that arrived, so a failing link flatters itself; they resolve good links but not intermediate ones; the threshold is a property of the environment |
| `liu2022` | The closest published comparison (ZigBee vs LoRa, RTT and PLR only). Excludes energy by assumption, runs LoRa at an unequalised 30 dBm, and has no repetitions — the concrete case for deriving RQ1 rather than borrowing it |
| `jacob2021` (TriScale) | run / series / sequel; KPIs as percentiles with one-sided non-parametric CIs; `N ≥ log(1−C)/log(1−p)` |
| `jacob2019` | No mean-and-variance synthesis; report the conservative CI bound; store raw timestamps rather than differences; many short tests over few long ones |
| `boano2014` (TempLab) | Temperature as an observed metric: 35 °C of on-board swing in an hour is enough to take a link from 100 % to 0 % reception |
| `schuss2018` (D-Cube) | What a benchmarking facility controls, and why a forest study must replace each control with a recorded observation |
| `itu833`, `etsi300220` | Frequency-dependent vegetation attenuation; the 433 MHz duty-cycle envelope |

**Decision on record:** 25th percentile at 95 % confidence, N = 11 runs per point. The median was
rejected because `becker2025`'s non-monotonic failures put the interesting behaviour in the lower
tail.

## 7. Open

- **The LoRa transmit path (§4).** Blocks all LoRa results.
- **Observed metrics are not recorded at all** — no on-board temperature, external interference,
  vegetation state or operator position anywhere in the codebase. `boano2018` treats these as a
  precondition for comparison, and `liu2022` reports PLR swinging from 100 % to under 10 % purely
  from where a researcher stood. Defensible on a stable bench; a blocker for field data.
- **One-way delay and jitter** remain unmeasurable without clock synchronisation.
- **Zigbee's raw arm** has not run.
- **The gateway's USB link is unreliable** — three disconnects on 2026-09-22 (08:41, 09:01, 09:18),
  two with no campaign running.
- **`DutyCycleGuard` is per-transport-instance**, so its history resets on every CFG and it never
  accumulates across a campaign. Host-side pacing in `run_campaign.py` is what actually enforces
  the budget; the firmware guard is only a per-point backstop.
