# Metrics

What this benchmark measures, how, and — equally important — what it cannot.

Categorised per [benchmarking-practices.md](benchmarking-practices.md) §1. For a transport library
the "general" metrics are timing ones; the domain-specific metrics are the wireless-sensor-network
ones; low-level PHY counters are largely unavailable, which is itself a finding.

## Measured

| Metric | Category | Axis | Definition |
|---|---|---|---|
| `latency_us` | general | latency | Time inside `Transport::send()`: from handing a payload to the library until the library has its ack. Reported for acknowledged payloads. |
| Goodput | domain | throughput | Delivered payload bits ÷ wall-clock seconds, over a whole configuration point. One way, back to back; each payload waits for its ack. |
| PDR | domain | — | Payloads the library reported acknowledged ÷ sent. Beside it, the gateway's own count ÷ sent. See below. |
| Payload efficiency | domain | — | `payloadBytes / totalBytes` — *library* framing only, see below. |
| Duty cycle | domain | — | Airtime consumed against the rolling-window budget. |
| RSSI / SNR | low-level | — | LoRa only, at both ends, at no cost. See below. |
| **Energy per delivered payload** | domain | — | Measured with a Nordic PPK2 source-metering the **node**'s battery rail at 3.9 V. See below. |
| **Standing current** | domain | — | Mean current with the radio configured but idle — very likely the figure that decides battery life. Measured once, by `bench.py baseline`. |

### The transfer, and delivery

A one-way transfer from node to gateway, as fast as the link allows (`src/node.cpp`). The loop is

    result = link.send(gateway, payload)

repeated, each payload sent **once**. `send()` returns when the library has its confirmation of
delivery, or has given up waiting for it:

| | what confirms delivery |
|---|---|
| ESP-NOW | the 802.11 MAC ack, reported by the send callback |
| Zigbee | the APS ack, reported in the module's TX status frame |
| LoRa | ConnectionLib's software ack (`CONNECTIONLIB_LORA_ACK`): the receiver's `listen()` answers with a bare header, and `send()` waits for it |

The benchmark adds no acknowledgement of its own. During a run the gateway has exactly one radio
up, the one under test, and its loop is `listen()` and a counter. There are no retries at the benchmark level, so nothing a retry would have repaired is
hidden (ESP-NOW and Zigbee retry inside the radio, which is part of what they are). A point stops
after 10 losses in a row and is recorded as out of range.

Nothing but the radio happens between `RUN,begin` and `RUN,end`: each payload's figures go into a
buffer in RAM and are printed once the run is over. The serial port is therefore in neither the
timings nor the energy window, and there is no pause between payloads.

**Two delivery figures.** *PDR* is acknowledged ÷ sent: what an application using the library
sees. After the run the node asks the gateway for a report, and *gateway PDR* is the gateway's own
count ÷ sent. The second can be higher, because a payload can arrive while its ack is lost; the
difference is the ack loss. If the report does not come back, the gateway figure is unknown, not
zero.

**Latency** is the time inside `send()`. On LoRa that is the payload's airtime, the gateway's
turnaround and the ack's airtime; on ESP-NOW and Zigbee it is whatever the radio needs to get its
own ack, retries included. An unacknowledged LoRa payload costs the full ack wait
(`configs.ack_wait_ms`, 1.0–2.7 s), which is why latency is reported for acknowledged payloads and
the loss shows up in goodput and energy instead.

The library polls for its acks in 1 ms steps (`delay(1)` in ESP-NOW's and LoRa's `send()`), so
ESP-NOW latency is quantised to about a millisecond. That is the library's behaviour and is
measured as such.

### Latency vs. throughput (practice P1)

Goodput is measured over the whole configuration point and is **not** derived from `latency_us`. The
exchange is a strict request/response with at most one packet in flight, so latency and throughput
happen to be closely coupled here — but deriving one from the other would bake that assumption
into the data, and it stops holding the moment any pipelining is introduced.

### Energy

Measured with a **Nordic PPK2 source-metering the node board's 5 V header pin at 5.0 V** (left
header pin 10, return on pin 9). The 18650 is removed — a cell in the holder buffers every current
spike, so you would measure the charger instead — and the node's USB carries data only, with the
cable's VBUS conductor cut: otherwise the host supplies the same net the PPK2 is sourcing and the
measurement is meaningless.

This tap is upstream of the AXP2101, so it includes the PMU's 5→3.3 V conversion loss and the
onboard USB-serial bridge in every reading, floor and per-radio alike. **The deltas between
technologies are therefore the trustworthy quantity; the absolute currents are inflated by a
constant.** A battery-terminal tap at a mid-discharge 3.9 V would model a deployed satellite's
rail more directly and exclude that overhead. Whether it is usable here is untested: it depends on
whether the CH9102F bridge draws from the board's 3.3 V rail or from USB VBUS, and if the latter,
a battery tap with VBUS cut leaves no serial link for the campaign to be driven over.

The SX1278's rail is cut whenever LoRa is not the radio in use, on both boards: the board floor
and every ESP-NOW and Zigbee figure are taken with the LoRa chip unpowered (69.5 mA floor against
75.9 mA with LoRa in receive, node on the bench, 2026-10-07).

The board is **stripped for these measurements**: the GPS rail (ALDO3) is disabled in firmware and
the OLED is not compiled in. Neither is used by the benchmark, so both were purely constant loads —
harmless for latency, but for energy they sit under every measurement and compress exactly the
per-technology differences the comparison exists to resolve.

The metered board is the **sender**. Its active window is the loop, first `send()` to last, so it
includes waiting for acks that never come: energy per delivered payload
rises as the link degrades, which is what a deployed node would pay. The September bench
campaigns metered the responder instead and are not comparable.

Three quantities, and the difference between them matters:

| | What it answers |
|---|---|
| **Board floor** (`none/floor`: every radio down) | what the ESP32 costs with every radio down |
| **Standing current** (radio configured, idle) | what this radio costs while merely *able* to receive |
| **Energy per delivered payload** (active) | what moving a message costs |

Reported both absolute and **above idle**, because they lead to different decisions. For a node
that must last months, standing current very likely dominates: it applies continuously while
per-packet energy applies only during the small fraction of time the node transmits. A radio can
therefore lose the energy comparison outright on idle current regardless of how efficient its
packets are — and ESP-NOW cannot receive at all without WiFi up, where LoRa sits in continuous
receive at an order of magnitude less.

**The caveat that bounds all of it:** this harness never sleeps. What is measured is energy during
*active operation*, which is the right basis for comparing the three technologies against each
other. Absolute battery life needs a duty-cycled sleep model layered on top, since a real satellite
spends almost all its time asleep and this benchmark spends none of it.

### Signal quality

Only LoRa reports it, and only at the gateway. RadioLib reads RSSI and SNR out of the chip for
every frame that arrives, at no cost; the gateway averages them over the run's payloads and
returns the two means in its report. There is no per-payload signal quality at the node: the only
thing the node receives during a run is the library's ack, and `SendResult` does not carry the
ack's RSSI.

ESP-NOW and Zigbee record none. Getting it costs a promiscuous-mode sniffer on ESP-NOW and a `DB`
AT query per packet on Zigbee, both of which move the latency and energy figures; the opt-in
capture that did this exists only on an unmerged ConnectionLib branch and is not in the library
version this benchmark builds against. Neither radio exposes SNR at all.

RSSI and SNR stay observed metrics, not comparison axes, for the reasons in ConnectionLib's
`docs/measurement-methodology.md` §4.

## The no-library baseline

[ConnectionLibBenchmarkRaw](https://github.com/GregorHolzer/ConnectionLibBenchmarkRaw) runs the
same node and gateway firmware on the vendor APIs — RadioLib's `SX1278`, `esp_now.h`,
xbee-arduino's `ZBTxRequest` — with ConnectionLib out of the path. `src/Transports.cpp` is the
only file that differs, so the measured loop, the timing points and the records are the same code.
It is a separate pass with its own gateway image.

**It covers ESP-NOW and Zigbee only.** Those radios acknowledge on their own, so the same loop
runs on the raw API. A raw SX1278 has no acknowledgement at all: the loop's delivery signal on
LoRa *is* a ConnectionLib feature, so there is nothing to compare it against without writing a
second ack protocol, which would then be measured instead of the radio.

## Not measured — and why

**Benchmark-level retries.** There are none, by design; see "The transfer, and delivery".

These are asked for by the thesis exposé's RQ1 metric set. They are listed here, and printed by
the analysis tooling, rather than silently omitted.

**One-way delay and one-way jitter.** Requires clock synchronisation between the two boards;
`becker2025` used Reference Broadcast Synchronization over the wireless link for exactly this.
What is reported is the time until delivery is *confirmed* at the sender, which contains the ack's
way back, and it is labelled as such.

**PHY-level payload efficiency.** `totalBytes` counts framing that **ConnectionLib** adds — LoRa's
6-byte software header, which exists because raw SX1278 has no addressing, versus zero added bytes
for ESP-NOW and Zigbee, which address natively. The 802.11 and 802.15.4 framing underneath is not
instrumented by any backend. Reported as "library framing efficiency"; reading it as on-air
efficiency would imply a fabricated 100 % for two of the three radios.

**Low-level PHY counters** (retry counts inside the radio, CCA failures, backoff). ESP-NOW and
Zigbee perform their own retries and expose only the outcome. This is the practical form of
practice §1's "low-level metrics may not be portable across hardware architectures": here they are
not available at all, so no comparison can rest on them.

## Where the numbers are

Measured values, the cross-technology comparison and the open problems live in
[findings.md](findings.md). This document says what is measured and why; that one says what came
out. The literature each metric rests on is tabulated there and derived in full in ConnectionLib's
`docs/measurement-methodology.md`.

## Aggregation (practice P11)

Single numbers are **medians**, never means, and always accompanied by a spread. Per-exchange
samples are kept in `exchanges.csv` so any other statistic can be recomputed without re-running.
Rounds at the same distance are pooled (`bench/summary.py`).

Following the slides' guidance on choosing the function for the question:

- **Peak capability** of a configuration — minimum latency, which suppresses external-load effects.
- **Typical performance** — median, with IQR, plus box/violin plots of the distribution.
- **Worst-case / soft-realtime** — 95th and 99th percentiles, by nearest rank. Percentiles are
  deliberately **not interpolated**: interpolation invents a value the sample never contained,
  and the confidence machinery here is non-parametric order statistics.
