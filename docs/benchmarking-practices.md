# Benchmarking practices

Summary of **"Performance-Oriented Computing — Experimentation"**, Peter Thoman, University of
Innsbruck (`02_experimentation.pdf`, 28 slides, 2023). The slides are course material and are not
redistributed in this repository; slide numbers below refer to that deck.

Every practice is written as a **checkable statement with an ID**. The *Here:* remarks describe
the September 2026 bench harness and its audit script `verify_practices.py`, both of which were
removed in the flash-per-configuration refactor (git history, `117a3f8`). The practices themselves
still bind the distance benchmark; which of them it can no longer show by repetition is said in
[field-campaign.md](field-campaign.md).

---

## 1. Metrics

**Three categories** (slides 4–6), which differ in portability:

| Category | Characteristics | Examples |
|---|---|---|
| **General** | apply to many programs; easy to measure platform-independently | execution time, peak memory, CPU utilisation |
| **Domain-specific** | quantities of the application domain; not portable between domains | requests serviced, simulation steps per second |
| **Low-level** | how the underlying hardware is used; may not port across architectures | cache misses, branch mispredictions |

**Throughput vs. latency** (slides 7–9) is a second axis. Latency metrics concern one operation
start-to-end; throughput metrics concern operations completed per unit time. They are
interchangeable *only* in a model with no parallelism or pipelining — "latency to produce 3 meals"
equals "latency for one meal × 3" only when nothing overlaps. In any realistic pipelined system
they are distinct and must be reported separately.

> **P1 — Report latency and throughput as separate metrics.** Never derive one from the other by
> multiplication or division.
>
> *Here:* round-trip latency (`rtt_us`) and goodput are measured and reported independently.
> Latency is never inferred from throughput or vice versa.

---

## 2. Measurement challenges

Two fundamental ones (slide 11): **consistency/reproducibility**, harder the smaller the quantity,
and described as "essential to overcome"; and **access/portability**, particularly for low-level
metrics.

Four concrete sources of error (slide 12): imprecise measurements, impact of external load,
measuring in non-representative scenarios, and properties varying with input data.

### The distinction that governs everything else

> Random errors **can** be mitigated by statistical evaluation of many runs.
> Systematic errors **cannot** be mitigated by repetitions. (slide 12)

> **P2 — Distinguish random from systematic error, and never report repetition as a remedy for
> the latter.** Any analysis that reduces uncertainty by averaging must first show the error it is
> averaging away is random.
>
> *Here:* the audit runs a drift regression against campaign wall-clock time. A significant slope
> is evidence of *systematic* error, and it is reported as a FAIL that more repetitions will not
> fix — not folded into an error bar.

---

## 3. Precision

Precision depends on the type of quantity, the extent (e.g. duration) of the event, and the
hardware/software doing the measuring (slide 13). To improve it: use the best available method
(high-performance timers, CPU counters), and make the measured event as large as possible —
"one option: repetition → but be careful, might be non-representative!"

> **P3 — Use the best available timer.** Microsecond-resolution or better, and the timer's own
> resolution must be far below the smallest reported quantity.
>
> *Here:* `micros()` on the ESP32. This is a corrected defect rather than a given: an earlier
> harness polled with `delay(5)`, which put a hard 5 ms floor under every latency figure and made
> every ESP-NOW measurement read exactly 5.00 ms — the harness was being reported instead of the
> radio.

> **P4 — Bound the measurement overhead.** Quantify the instrumentation's own cost and show it is
> small relative to the smallest quantity measured.
>
> *Here:* `CFG tech=none` runs a null measurement through the identical code path with no radio.
> The audit requires the result to be under 1 % of the smallest reported latency.

---

## 4. External load

External load is system resource use by other processes; it is hard to avoid entirely on consumer
hardware, and can be reduced by benchmarking on dedicated systems and by core pinning (slide 14).
The slide's own plot shows relative runtime rising sharply from "none" to "desktop" to
"workstation" load.

> **P5 — Control external load, and record the evidence.** Run on dedicated hardware; make sure no
> unrelated activity competes with the measurement.
>
> *Here:* a Raspberry Pi doing nothing but driving the two boards. The subtle case is on-device:
> `~EspNowTransport` deinitialises ESP-NOW but leaves **WiFi running**, which would leave a 2.4 GHz
> transmitter active beside every subsequent Zigbee run. The benchmark firmware therefore forces
> `WiFi.mode(WIFI_OFF)` when the active transport is not ESP-NOW, and records the radio state in
> each run header so the audit can verify it rather than assume it.

---

## 5. Non-representative scenarios

Problems arise when the measured scenario differs from the real one (slide 15): measuring debug
builds, different hardware, individual components whose behaviour changes once integrated — and
"the act of measuring itself might modify the behaviour". The advice: reproduce the production
scenario as precisely as possible, and **integrate benchmarking features into release builds**.

> **P6 — Never benchmark a debug build.** Benchmarking must be compiled into the release build.
>
> *Here:* PlatformIO release mode, no `-Og`/`-DDEBUG`; the audit inspects the recorded build flags
> and fails on debug indicators.

> **P7 — Measure the integrated system, in its deployed configuration.**
>
> *Here:* the real ConnectionLib `Transport` on the real radios at the EU-legal operating point of
> [regulations.md](regulations.md) — not a mock, and not a relaxed configuration chosen to make the
> benchmark finish sooner.

---

## 6. Variance with input data

Performance can vary with input data, and decisions based on a single data set can be wrong
(slide 16). Several data sets are needed, with the challenge being "represent all relevant
characteristics with as few runs as possible".

> **P8 — Sweep the input data, never characterise from a single input.**
>
> *Here:* payload size is swept in every technology's benchmark (8/16/32/84 B, plus 250 B where the
> radio allows). Payload is the input-data axis for a transport.

---

## 7. Statistical evaluation and execution order

Repeat runs and aggregate, for three distinct purposes: reproducing different scenarios, using
different input data, and reducing non-systemic errors. Two factors matter: **order of execution**
and the aggregation functions applied (slide 17).

Slide 18 is the sharpest practical point in the deck. It contrasts running all repetitions of
scenario 1, then all of scenario 2, and so on, against a **round-robin ordering** — and shows that
under the grouped ordering a transient external event corrupts one scenario entirely, while the
round-robin ordering spreads that damage across all scenarios:

> Repetitions with the purpose of reducing random errors should be spaced out as far as possible.

> **P9 — Interleave repetitions; never group all repetitions of one configuration together.**
>
> *Here:* the campaign scheduler is repetition-major — every configuration point once, then every
> point again — and technologies rotate in blocks so each one's repetitions spread over hours. The
> audit does not take this on trust: it reconstructs the real execution order from recorded
> timestamps and fails if repetitions of the same configuration were adjacent.

> **P10 — Repeat enough to say something.** At least 3 repetitions per configuration point, and
> enough samples to support whatever percentile is being claimed.
>
> *Here:* repetitions per technology are derived from the campaign's wall-clock budget. The audit
> additionally applies TriScale's `N ≥ log(1−C)/log(1−p)` to check the sample count actually
> supports the percentile reported.

---

## 8. Aggregation functions

The right function depends on the purpose (slide 19):

| Purpose | Function |
|---|---|
| Maximum throughput of hardware or an algorithm | **minimum** of times — minimises the impact of external load |
| Realistic "average" performance | **median** if one number is needed; *ideally, analyse the distribution* |
| Response times in a soft-realtime context | **maximum, or 99th percentile** |

> **P11 — Choose the aggregation function for the question, and never report a bare mean.** Where a
> single number is given, it is the median; the distribution is always available alongside it.
>
> *Here:* summaries carry median, IQR, min, max and percentiles, and the per-exchange samples are
> kept in the raw CSV. The audit fails any summary offering a mean without a median and a spread.

---

## 9. Data representation

"Representations such as box and violin plots allow for a better understanding of comparative
performance at a glance" (slide 20).

> **P12 — Show distributions, not just central tendencies.** Box or violin plots for every
> configuration point being compared.
>
> *Here:* `plot_results.py` produces box and violin plots per configuration point, per-repetition
> strip overlays, and per-repetition ECDFs.

---

## 10. Performance integration testing

Continuous monitoring of performance during development, to catch unintentional regressions —
"feature X was introduced, suddenly unrelated feature Y is 5 times slower" (slides 22–25). Its
important factors: define target metrics and experiments clearly and follow all the good practices
above; **track "benchmark coverage" like test coverage**; implement it as early as possible,
because design issues are cheaper to fix early; and generate reports, summaries and charts —

> Having the data is a good first step, but if no one looks at it since it's just a bunch of CSV
> files that doesn't help much.

Its challenges: more complex and costly than normal testing, needs dedicated hardware for
reproducibility, and should be integrated with continuous build/test/delivery, including long-term
storage and efficient comparison of results.

> **P13 — Produce a report, not just data files.** Every campaign yields figures and a written
> summary, not only CSV.
>
> *Here:* `plot_results.py` writes figures and `verify_practices.py` writes `verification.md`. The
> audit fails if a results directory contains data but no rendered report.

> **P14 — Track benchmark coverage.** Record which configurations were actually exercised, and
> which the hardware or the regulations put out of reach.
>
> *Here:* each campaign writes the full configuration space alongside the points completed, so
> coverage is explicit. Points rejected by the conformance guard are recorded with their reason
> rather than silently skipped.

---

## Practice index

| ID | Practice | Slide |
|---|---|---|
| P1 | Latency and throughput reported separately | 7–9 |
| P2 | Random vs. systematic error distinguished | 12 |
| P3 | Best available timer | 13 |
| P4 | Measurement overhead bounded | 13, 15 |
| P5 | External load controlled and evidenced | 14 |
| P6 | Release build, never debug | 15 |
| P7 | Integrated system in deployed configuration | 15 |
| P8 | Input data swept | 16 |
| P9 | Repetitions interleaved, not grouped | 18 |
| P10 | Enough repetitions for the claim | 17 |
| P11 | Aggregation chosen for the question; no bare mean | 19 |
| P12 | Distributions shown (box/violin) | 20 |
| P13 | Reports generated, not just CSV | 24 |
| P14 | Benchmark coverage tracked | 24 |
