# Field campaign

How the benchmark runs when the boards are up to 2 km apart and only one of them has a computer.

**No transfer has been made with this harness yet.** As of 2026-10-07 the firmware builds, the
host code is unit-tested, and the node alone has been flashed, started and metered on the bench:
idle holds, and an ESP-NOW run that correctly stopped at `HOME,noack` with no gateway present. The
gateway image, the announcement and the transfer itself have not run on hardware. Do a desk run
(`site --distance 0`) before the first trip, and read "Before trusting a result" below.

## Running one

1. **At the desk**, both boards on USB: `./bench.py prepare --node-port … --gateway-port …
   --zigbee-peer …`. It reads both MACs into `boards.json`, flashes the gateway (which needs the
   node's MAC to answer over ESP-NOW), and builds the node firmware once so the field needs no
   network. The gateway must be the board with the Zigbee coordinator.
2. **Once**: `./bench.py baseline` — the board floor and each radio's standing current. These do
   not depend on distance.
3. **Place the gateway** on battery. It boots into LoRa home and waits.
4. **At each spot**: `./bench.py site --distance <m>`. Zigbee, ESP-NOW, the LoRa baseline, then
   the other 15 LoRa settings in an order shuffled per distance. Watch the first line of each
   run: `HOME,noack` means the gateway was not reached on home at all.

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
2. The node, freshly flashed, brings LoRa up on home and sends a **config frame**: which radio the
   run uses, the LoRa parameters, and a lease. This is the same for ESP-NOW, Zigbee and LoRa runs.
3. The library's ack confirms it. The gateway takes LoRa home down and brings up only the radio
   that was asked for; the node does the same on its side. For an ESP-NOW or Zigbee run both
   boards cut power to their SX1278, so it is not receiving under the measurement.
4. The node runs the loop. The gateway serves that one radio and nothing else.
5. The node sends an **end frame** on the run's radio. The gateway answers with its report
   (payloads counted, mean RSSI/SNR on LoRa) and goes back to WAIT.

What can go wrong, and what happens then:

- **The config's ack is lost.** The node cannot tell a lost frame from a lost ack, so after every
  unacknowledged attempt it sends a probe on the run's radio; an ack there means the gateway did
  switch. Three attempts, then `HOME,noack` and the run is not made.
- **The end frame never arrives** (node died, or the run's radio does not reach). The gateway goes
  back to WAIT when the **lease** runs out: 32 s for ESP-NOW, 60 s for Zigbee, 44–140 s for LoRa
  (`configs.lease_seconds`). `bench.py` knows the end frame was not acknowledged and waits that
  long before the next run, because until then the gateway is deaf to home.

## Duty cycle

433 MHz allows 10 % per hour, 360 s of airtime per transmitter. One site costs the node about
200 s (16 points, config frames included), so **two sites inside one hour will make the second one
wait**; `bench.py` prints how long.

- The firmware's own guard starts empty at every flash, so the host ledger
  (`results/lora_airtime_ledger.txt`, kept across runs) is what enforces the limit on the node. It
  paces at 9 %.
- The gateway sends only the library's header-only acks and one report per run, far less airtime
  than the node. The library's own guard covers it; that guard starts empty whenever the gateway
  switches radio, which at this volume does not matter.
- Every run, ESP-NOW and Zigbee included, costs one 1.7 s config frame on 433 MHz. The ledger
  charges it.

## Before trusting a result

1. **The LoRa transmit chain was broken in September** ([findings.md](findings.md) §4): the PA
   drew no current and the desk link sat at −130 dBm. Unrepaired, every LoRa distance reads about
   100 dB short. Check the node's current during an SF12 point on the PPK2 before going out.
2. **Which XBee is the coordinator is unverified** since the boards swapped roles (findings §8).
3. **One visit is one sample.** There are no rotations. `--rounds 2` and the shuffled LoRa order
   spread drift within a visit, but a link that changes with weather or with where you stand
   (findings §8, `liu2022`) needs the site revisited to show it. `--note` and the home RSSI
   recorded with every LoRa point are there to make such differences explicable afterwards.
4. **Energy is the sender's**, and includes waiting for acks that never come. It is not
   comparable with September's responder energy.
5. **Nothing runs unless LoRa home reaches the gateway**, ESP-NOW and Zigbee runs included. With
   the transmit chain of item 1 unrepaired, that is every run.
6. **ESP-NOW and Zigbee stop within a few hundred metres.** Beyond that the config still
   arrives over LoRa, the loop ends after 10 losses in a row, and the point is recorded with zero
   delivery. That is the result at that distance, not a failure. It costs the lease, since the
   end frame cannot reach the gateway either.
7. **The raw stack is a separate pass** with its own gateway image, and covers ESP-NOW and Zigbee
   only.
8. Distance campaigns are **not poolable** with the September bench campaigns.
