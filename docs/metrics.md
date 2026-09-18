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
| Goodput | domain | throughput | Delivered payload bits ÷ wall-clock seconds, over a whole configuration point. |
| PDR (uplink, downlink) | domain | — | Distinct sequence numbers received **at the receiver** ÷ sent. Counted per direction. |
| Retries / first-attempt success | domain | — | Exchanges completed without retransmission. |
| Ack rate | domain | — | Fraction of sends the backend reported acknowledged. |
| Payload efficiency | domain | — | `payloadBytes / totalBytes` — *library* framing only, see below. |
| Duty cycle | domain | — | Airtime consumed against the rolling-window budget. |
| RSSI / SNR | low-level | — | Where the radio exposes it: LoRa both, Zigbee neither via this API, ESP-NOW neither. |

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

## Not measured — and why

These are asked for by the thesis exposé's RQ1 metric set. They are listed here, and printed by
the analysis tooling, rather than silently omitted.

**One-way delay and one-way jitter.** Requires clock synchronisation between the two boards;
`becker2025` used Reference Broadcast Synchronization over the wireless link for exactly this.
Round-trip ÷ 2 is not a substitute: these links are strongly asymmetric — a Zigbee gateway
broadcast leg and the node's unicast reply differ by an order of magnitude. Round-trip latency and
its variation are reported instead, and labelled as such.

**Energy per delivered payload.** The binding requirement for FORTE (months of battery life), but
it needs an external power monitor on the board's supply rail. Nothing available over the serial
link approximates it.

**PHY-level payload efficiency.** `totalBytes` counts framing that **ConnectionLib** adds — LoRa's
6-byte software header, which exists because raw SX1278 has no addressing, versus zero added bytes
for ESP-NOW and Zigbee, which address natively. The 802.11 and 802.15.4 framing underneath is not
instrumented by any backend. Reported as "library framing efficiency"; reading it as on-air
efficiency would imply a fabricated 100 % for two of the three radios.

**Low-level PHY counters** (retry counts inside the radio, CCA failures, backoff). ESP-NOW and
Zigbee perform their own retries and expose only the outcome. This is the practical form of
practice §1's "low-level metrics may not be portable across hardware architectures": here they are
not available at all, so no comparison can rest on them.

## Aggregation (practice P11)

Single numbers are **medians**, never means, and always accompanied by a spread. Per-exchange
samples are kept in `raw.csv` so any other statistic can be recomputed without re-running.

Following the slides' guidance on choosing the function for the question:

- **Peak capability** of a configuration — minimum latency, which suppresses external-load effects.
- **Typical performance** — median, with IQR, plus box/violin plots of the distribution.
- **Worst-case / soft-realtime** — 95th and 99th percentiles, by nearest rank. Percentiles are
  deliberately **not interpolated**: interpolation invents a value the sample never contained,
  and the confidence machinery here is non-parametric order statistics.
