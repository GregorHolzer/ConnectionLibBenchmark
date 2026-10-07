# Field campaign

How the benchmark runs when the boards are up to 2 km apart and only one of them has a computer.

**No exchange has been made with this harness yet.** As of 2026-10-07 the firmware builds, the
host code is unit-tested, and the node alone has been flashed, started and metered through an idle
hold on the bench. The gateway image, the handshake and the ping-pong itself have not run on
hardware. Do a desk run (`site --distance 0`) before the first trip, and read "Before trusting a
result" below.

## Running one

1. **At the desk**, both boards on USB: `./bench.py prepare --node-port … --gateway-port …
   --zigbee-peer …`. It reads both MACs into `boards.json`, flashes the gateway (which needs the
   node's MAC to answer over ESP-NOW), and builds the node firmware once so the field needs no
   network. The gateway must be the board with the Zigbee coordinator.
2. **Once**: `./bench.py baseline` — the board floor and each radio's standing current. These do
   not depend on distance.
3. **Place the gateway** on battery. It boots into ESP-NOW, Zigbee and LoRa home, all at once.
4. **At each spot**: `./bench.py site --distance <m>`. Zigbee, ESP-NOW, the LoRa baseline, then
   the other 15 LoRa settings in an order shuffled per distance.

## Topology and roles

- **Gateway**: fixed, battery-powered, no host, answers only. All three radios stay up.
- **Node**: with the laptop and the PPK2, which meters it. It initiates every exchange.

The node firmware does nothing until the host sends it a byte after reset. A board that is merely
powered up, or reset by a serial port being opened, therefore never transmits unrecorded.

## How the gateway follows LoRa

ESP-NOW and Zigbee have one parameter set each, so the gateway just listens. LoRa hears one SF/BW
at a time and is swept, so the gateway has to be told each setting — over the air, since nothing
else reaches it.

- **Home**: SF12, 125 kHz, CR 4/8, 7 dBm, the most robust setting in the envelope. The gateway
  waits there. If home does not reach a spot, no swept setting would have.
- **Switch**: a LoRa node build starts by sending, on home, a 10-byte frame with its setting and a
  lease length. The gateway acks on home and moves.
- **Release**: after its pings the node sends a release on the point; the gateway acks and goes
  home.
- **Lease**: if the release never arrives, the gateway goes home when the lease runs out. The
  lease is an upper bound on the run (`configs.lease_seconds`), 68–272 s depending on the setting.
- **Lost ack**: a missing switch ack means either the request or the ack was lost. The node
  therefore probes on the point after every unacknowledged attempt and carries on if answered.
- **Host side**: after a LoRa point without an acknowledged release, `bench.py` waits out that
  lease before starting the next LoRa point, because the gateway may still be deaf to home.

## Duty cycle

433 MHz allows 10 % per hour, 360 s of airtime per transmitter. One site costs the node about
205 s (16 points, handshakes included), so **two sites inside one hour will make the second one
wait**; `bench.py` prints how long.

- The firmware's own guard starts empty at every flash, so the host ledger
  (`results/lora_airtime_ledger.txt`, kept across runs) is what enforces the limit on the node. It
  paces at 9 %.
- The gateway answers every ping with a frame of the same size. It keeps its own ledger across
  its whole uptime and does not send a reply it cannot afford; the node sees that as a lost pong.
  The 1 % of margin on the host is what keeps this from happening.

## Before trusting a result

1. **The LoRa transmit chain was broken in September** ([findings.md](findings.md) §4): the PA
   drew no current and the desk link sat at −130 dBm. Unrepaired, every LoRa distance reads about
   100 dB short. Check the node's current during an SF12 point on the PPK2 before going out.
2. **Which XBee is the coordinator is unverified** since the boards swapped roles (findings §8).
3. **One visit is one sample.** There are no rotations. `--rounds 2` and the shuffled LoRa order
   spread drift within a visit, but a link that changes with weather or with where you stand
   (findings §8, `liu2022`) needs the site revisited to show it. `--note` and the home RSSI
   recorded with every LoRa point are there to make such differences explicable afterwards.
4. **Energy is the initiator's**, and includes waiting for pongs that never come. It is not
   comparable with September's responder energy.
5. **The gateway runs all three radios at once.** It is not metered, so no energy figure changes,
   but WiFi is receiving beside the XBee during Zigbee points, and the gateway draws on the order
   of 130 mA from its cell.
6. **ESP-NOW and Zigbee stop within a few hundred metres.** A point ends after 10 losses in a row
   and is recorded with zero delivery. That is the result at that distance, not a failure.
7. **The raw stack is a separate pass.** A raw LoRa frame has no header, so the gateway must be
   flashed from ConnectionLibBenchmarkRaw too.
8. Distance campaigns are **not poolable** with the September bench campaigns.
