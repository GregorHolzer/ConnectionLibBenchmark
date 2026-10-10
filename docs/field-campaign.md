# Field campaign

How the benchmark runs when the boards are up to 2 km apart and only one of them has a computer.

**First field day: 2026-10-10** (`results/field-20261010`), sites at 10, 22, 84 and 145 m, the
last one incomplete; not yet written up. Before that it had only run on the desk. On 2026-10-07, boards about 1 m apart
(`results/desk-20261007`): Zigbee and ESP-NOW delivered 100/100, and the gateway went
WAIT → RUN → WAIT for every run. LoRa was at first as weak as in September (−135 dBm, −12.5 dB SNR
at one metre; SF7 and 2 dBm delivered nothing). That turned out to be the antennas: once they were
mounted correctly the SF9 baseline read **−70 dBm and +11.5 dB SNR**, 20/20 delivered
(`results/desk-20261007-antenna-check`). The LoRa rows of `desk-20261007` were recorded before
that and should be re-run; their RSSI column is also clamped at −127 dBm by a since-fixed bug.

**The whole set ran on the desk on 2026-10-09** (`results/desk-20261009`): every configuration
delivered 100 %, the gateway counting every payload, fragmented ones included. That campaign
used 250, 500 and 1000 B as its large sizes; a site now uses 256 and 512 B
([metrics.md](metrics.md#larger-payloads-a-library-capability)). Nothing fragmented has run in
the field.

## Running one

1. **At the desk**, both boards on USB: `./bench.py prepare --node-port … --gateway-port …
   --zigbee-peer …`. It reads both MACs into `boards.json`, flashes the gateway (which needs the
   node's MAC to answer over ESP-NOW), and builds the node firmware once so the field needs no
   network. The gateway must be the board with the Zigbee coordinator. **Repeat this after every
   change under `src/`**: the gateway is only ever flashed here, and it must carry the same
   firmware as the node.
2. **Once**: `./bench.py baseline` — the board floor and each radio's standing current. These do
   not depend on distance.
3. **Place the gateway** on battery. It boots into LoRa home and waits.
4. **At each spot**: `./bench.py site --distance <m>`. The three baselines first, then the other
   24 points (payload sizes on every radio, LoRa's knobs) in an order shuffled per distance.
   Watch the first line of each run: `HOME,noack` means the gateway was not reached on home at
   all.

A site is 27 flashes and takes roughly 20 minutes (an estimate: about 10 minutes of flashing,
the rest mostly LoRa). It uses three quarters of the hourly 433 MHz budget, so a
second site inside the same hour waits for most of it ([Duty cycle](#duty-cycle)).

**Where Zigbee or ESP-NOW no longer reaches**, `site` notices by itself. The baselines run
first, Zigbee, then ESP-NOW, then LoRa. If a Zigbee or ESP-NOW baseline delivers nothing, the
other points of that radio at this distance are skipped (printed as `skipped`), since each
would only fail and cost its lease; the zero-delivery baseline stays in the campaign as the
result there. LoRa's points always run. A baseline that failed with `no link` does not count:
that is the gateway not answering on LoRa home, not the radio being out of range.

**Do not restart `site` right after a run that came back without a report.** The gateway stays
on that run's radio until its lease runs out, and the script only remembers that while it is
running; a restart inside the lease gets `no link` until the gateway is home again.

## Topology and roles

- **Gateway**: fixed, battery-powered, no host. Exactly one of its radios is up at any time.
- **Node**: with the laptop and the PPK2, which meters it. It sends; the gateway receives.

The node firmware does nothing until the host sends it a byte after reset. A board that is merely
powered up, or reset by a serial port being opened, therefore never transmits unrecorded.

## One run, step by step

The gateway is in one of two states (`src/gateway.cpp`):

| | radio up | does |
|---|---|---|
| **WAIT** | LoRa on home only | waits for a config frame |
| **RUN** | only the radio the config named | `listen()`, count; nothing else |

1. The gateway is in WAIT. **Home** is SF12, 125 kHz, CR 4/8, 7 dBm, the most robust setting in
   the envelope: if home does not reach a spot, no swept LoRa setting would have.
2. The node, freshly flashed, brings LoRa up on home and sends a **config frame** (12 bytes):
   which radio the run uses, the LoRa parameters, whether the run is fragmented, and a lease.
   This is the same for ESP-NOW, Zigbee and LoRa runs.
3. The library's ack confirms it. The gateway takes LoRa home down and brings up only the radio
   that was asked for; the node does the same on its side. For an ESP-NOW or Zigbee run both
   boards cut power to their SX1278, so it is not receiving under the measurement. For a payload
   too large for one packet of that radio both wrap it in a `FragmentingTransport`; LoRa home is never wrapped.
4. The node runs the loop. The gateway serves that one radio and nothing else.
5. The node sends an **end frame** on the run's radio. The gateway answers with its report
   (payloads counted, mean RSSI/SNR on LoRa) and goes back to WAIT.

What can go wrong, and what happens then:

- **The config's ack is lost.** The node cannot tell a lost frame from a lost ack, so after every
  unacknowledged attempt it sends a probe on the run's radio; an ack there means the gateway did
  switch. Three attempts, then `HOME,noack` and the run is not made.
- **The end frame never arrives** (node died, or the run's radio does not reach). The gateway goes
  back to WAIT when the **lease** runs out (`configs.lease_seconds`, table below). `bench.py`
  knows the end frame was not acknowledged and waits that long before the next run, because
  until then the gateway is deaf to home.

| Lease | up to 84 B | 256 B | 512 B |
|---|---|---|---|
| Zigbee | 60 s | not measured | not measured |
| ESP-NOW | 32 s | 64 s | 96 s |
| LoRa | 44–140 s by setting | 52 s | 72 s |

For Zigbee and ESP-NOW the lease is the single-packet bound times the packets of one payload.

These are upper bounds, not run times: a Zigbee run of 100 payloads at 32 B takes about 8 s on
the desk, an ESP-NOW run of 1500 about 2.7 s.

A gateway flashed before 2026-10-09 expects an 11-byte config frame and the node now sends 12;
it has not been tried against the current node. Run `prepare` again if in doubt.

## Zigbee and full-size frames

Zigbee is measured at 8, 16 and 32 B only. On 2026-10-10 at 10 m (`results/field-20261010`),
every Zigbee run with 84 B frames lost the link about 5 s into the run and aborted: 84 B after
29 payloads, 256 B after 10, 512 B after 5, and 512 B again in a second round. Until then the
payloads arrived with desk-like latency; afterwards every packet failed, the end message
included. The 8, 16 and 32 B runs at the same spot delivered 100/100, and on the desk the large
sizes had delivered 100/100 too. The same happened to the 32 B baseline at 22 m (62 payloads,
then dead), while Zigbee delivered 100/100 at 84 m.

Seen at the gateway, the board went down during those runs and came back by itself before the
next one. Back on the desk the same afternoon (`results/zigbeecheck-20261010`), the baseline and
84, 256 and 512 B delivered 100/100 twice each, so it does not reproduce at 1 m. The cause is
open; the gateway's reset reason has not been read yet. Those recorded field runs are a gateway
that went down, not a delivery ratio.

## Duty cycle

433 MHz allows 10 % per hour, 360 s of airtime per transmitter. One site costs the node about
249 s (27 runs, config frames included) of the 324 s the ledger paces to, so **a second site
inside the same hour will wait**; `bench.py` prints how long. `--rounds 2` at one spot waits
the same way.

| Per site | Runs | Airtime on 433 MHz |
|---|---|---|
| LoRa, the 16 points up to 84 B | 16 | about 200 s |
| LoRa, 256 and 512 B at 5 payloads each | 2 | about 30 s |
| Zigbee and ESP-NOW, one config frame each | 9 | about 15 s |

More samples of Zigbee and ESP-NOW are nearly free: `--tech zigbee --tech espnow --rounds 3`
costs only the config frames.

- The firmware's own guard starts empty at every flash, so the host ledger
  (`results/lora_airtime_ledger.txt`, kept across runs) is what enforces the limit on the node. It
  paces at 9 %.
- The gateway sends only the library's header-only acks and one report per run, far less airtime
  than the node. The library's own guard covers it; that guard starts empty whenever the gateway
  switches radio, which at this volume does not matter.
- Every run, ESP-NOW and Zigbee included, costs one 1.7 s config frame on 433 MHz. The ledger
  charges it.

## Before trusting a result

1. **Check the LoRa antennas before every trip.** The "broken transmit chain" of September
   ([findings.md](findings.md) §4) was wrongly mounted antennas: about 70 dB of loss with
   everything else working. A LoRa baseline run on the desk should report around −70 dBm at the
   gateway; around −135 dBm means an antenna is not connected to the radio.
2. **The gateway's XBee is `0013A20042035B39`**, confirmed by the desk run.
3. **One visit is one sample.** There are no rotations. `--rounds 2` and the shuffled order
   spread drift within a visit, but a link that changes with weather or with where you stand
   (findings §8, `liu2022`) needs the site revisited to show it. `--note` and the home RSSI
   recorded with every LoRa point are there to make such differences explicable afterwards.
4. **Energy is the sender's**, and includes waiting for acks that never come. It is not
   comparable with September's responder energy.
5. **Nothing runs unless LoRa home reaches the gateway**, ESP-NOW and Zigbee runs included.
6. **ESP-NOW and Zigbee stop within a few hundred metres.** Beyond that the config still
   arrives over LoRa, the loop ends after 10 losses in a row, and the point is recorded with zero
   delivery. That is the result at that distance, not a failure. It costs the lease, since the
   end frame cannot reach the gateway either; from there on use `--tech lora`
   ([Running one](#running-one)).
7. **The raw stack is a separate pass** with its own gateway image, and covers ESP-NOW and Zigbee
   only.
8. Distance campaigns are **not poolable** with the September bench campaigns.
