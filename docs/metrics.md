# Metrics

What this benchmark measures, how, and — equally important — what it cannot.

Categorised per [benchmarking-practices.md](benchmarking-practices.md) §1. For a transport library
the "general" metrics are timing ones; the domain-specific metrics are the wireless-sensor-network
ones; low-level PHY counters are largely unavailable, which is itself a finding.

## Measured

| Metric | Category | Axis | Definition |
|---|---|---|---|
| `rtt_us` | general | latency | Application send instant → application receive instant, for one request/response exchange. |
| `send_us` | general | latency | Time blocked inside `Transport::send()`: the outbound transmission plus any backend status wait. |
| Goodput | domain | throughput | Delivered payload bits ÷ wall-clock seconds, over a whole configuration point. Stop-and-wait goodput, not link capacity. |
| PDR (uplink, downlink, round trip) | domain | — | Packets received **at the receiver** ÷ sent, per direction. Each ping is sent once. See "Delivery" below. |
| Ack rate | domain | — | Fraction of sends the backend reported acknowledged. |
| Payload efficiency | domain | — | `payloadBytes / totalBytes` — *library* framing only, see below. |
| Duty cycle | domain | — | Airtime consumed against the rolling-window budget. |
| RSSI / SNR | low-level | — | LoRa only, at both ends, at no cost. See below. |
| **Energy per delivered payload** | domain | — | Measured with a Nordic PPK2 source-metering the **node**'s battery rail at 3.9 V. See below. |
| **Standing current** | domain | — | Mean current with the radio configured but idle — very likely the figure that decides battery life. Measured once, by `bench.py baseline`. |

### The exchange, and delivery

Stop-and-wait ping-pong, started by the node (`src/node.cpp`). Ping N is sent **once**; the node
waits for the pong carrying the same N, then sends N+1. There are no retries, so nothing a retry
would have repaired is hidden, and a point costs the same airtime whether the link is good or bad.
A point stops after 10 losses in a row and is recorded as out of range.

The gateway has no host, so its side of the count travels in the pong: every pong carries how many
pings of this run the gateway has received so far (`gw_rx`), plus its own RSSI and SNR of that
ping. After the last ping the node asks for the final count up to three times, outside the timed
window, so a lost last pong does not understate it.

- **Uplink PDR** = `gw_rx` ÷ pings sent.
- **Downlink PDR** = pongs received ÷ `gw_rx`.
- **Round-trip PDR** = pongs received ÷ pings sent.

If no pong arrives at all, the gateway's count is unknown: uplink and downlink are then reported
as unknown, not as zero, and only the round trip is zero. If the final count request also goes
unanswered, `gw_rx` is the last value seen and uplink is a lower bound.

The reply wait polls `listen(0)` every 200 µs rather than calling `listen(timeout)`: the library's
blocking `listen()` sleeps in 1 ms steps, which would quantise ESP-NOW's ~3.5 ms round trip.

### Why `rtt_us` and `send_us` are separate

Reporting only one of these produced two wrong published numbers in an earlier harness, both of
which looked plausible:

- The timer started **after** `send()` returned, so the outbound transmission was excluded. LoRa
  was reported at 212 ms when the true round trip is 409 ms — the 212 ms was the reply leg alone,
  which matched the computed single-leg time-on-air almost exactly.
- On Zigbee the effect was total: a broadcast blocks ~1 s inside `send()` waiting for its APS
  status, the reply lands inside that window and is buffered, and the measured round trip was
  **0 ms**.

Splitting the two makes the cost structure visible instead of hiding it in a single number.

### Latency vs. throughput (practice P1)

Goodput is measured over the whole configuration point and is **not** derived from `rtt_us`. The
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

The board is **stripped for these measurements**: the GPS rail (ALDO3) is disabled in firmware and
the OLED is not compiled in. Neither is used by the benchmark, so both were purely constant loads —
harmless for latency, but for energy they sit under every measurement and compress exactly the
per-technology differences the comparison exists to resolve.

The metered board is the **initiator**. Its active window runs from the first ping to the last
pong or timeout, so it includes waiting for pongs that never come: energy per delivered payload
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

Only LoRa reports it. RadioLib reads RSSI and SNR out of the chip for a packet that already
arrived, at no cost, so every answered exchange records both ends: `rssi_dbm` / `snr_db` are the
node's reading of the pong, `gw_rssi_dbm` / `gw_snr_db` the gateway's reading of the ping. Every
LoRa point also records the RSSI and SNR of the gateway's switch ack on home, the same SF12 link
at every point, which shows how a site's link margin moved during the visit.

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
Its README lists what each raw transport bypasses. It is a separate pass with its own gateway
image, because a raw LoRa frame has no header for a library gateway to recognise.

On LoRa the library's 6-byte header costs airtime in whole symbols: a near-fixed +28.7 ms per
transmission at the SF9 / 125 kHz / 4:7 baseline, 14.5 % of an 8-byte exchange and 4.2 % of an
84-byte one (September bench measurement, [findings.md](findings.md)).

## Not measured — and why

**Retries.** There are none, by design; see "The exchange, and delivery".

These are asked for by the thesis exposé's RQ1 metric set. They are listed here, and printed by
the analysis tooling, rather than silently omitted.

**One-way delay and one-way jitter.** Requires clock synchronisation between the two boards;
`becker2025` used Reference Broadcast Synchronization over the wireless link for exactly this.
Round-trip ÷ 2 is not a substitute: these links are strongly asymmetric — a Zigbee gateway
broadcast leg and the node's unicast reply differ by an order of magnitude. Round-trip latency and
its variation are reported instead, and labelled as such.

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
