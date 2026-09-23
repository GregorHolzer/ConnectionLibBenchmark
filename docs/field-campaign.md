# Field campaign

How the benchmark runs when the two boards are up to 4 km apart and only one of them can have a
computer attached. Every serial `CFG`/`RUN` path the bench uses stays as it is. Field mode is added
alongside it, as two more builds (`field_node`, `field_gateway`) and `run_campaign.py --field`, and
the bench builds compile none of it.

**Run on the desk, not yet in the field.** The first two runs, and the three defects they turned
up, are in [findings.md](findings.md) §7.

## Running one

1. **Before deployment**, with the coordinator board on USB:
   `tools/field_gateway.py flash`. It prints the gateway's MAC, which ESP-NOW needs.
   `tools/field_gateway.py status` confirms the firmware and shows the log size.
2. **In the field**, with only the node on USB and the PPK2 metering it:
   `./run_campaign.py --field --site "1 km, ridge" --gateway-mac <mac>`. One campaign per site.
3. **After collection**, with the gateway on USB:
   `tools/field_gateway.py fetch --results results/<campaign>` for each campaign, then
   `tools/field_gateway.py clear --yes` once all of them are fetched.

## Topology

- **Gateway**: fixed in the field, battery-powered, no host. It is the Zigbee coordinator, as on
  the bench (see ConnectionLib's `docs/hardware.md`). It only answers.
- **Node**: carries the laptop and the PPK2. It runs the measured loop, `run_campaign.py` drives
  it over USB exactly as today, and it is moved between distances. The same gateway serves every
  distance.

**Roles are swapped relative to the bench.** On the bench the gateway runs the measured loop
([`BenchRunner.cpp`](../src/BenchRunner.cpp)) and the PPK2 meters the node, which only echoes. In
the field the metered board is the one that initiates. That keeps the RTT timing, live monitoring
and result collection on the laptop, and leaves the gateway a pure responder. What it costs:
**field energy is initiator energy and is not comparable with the bench's responder energy**,
even at the same configuration point.

## Control: a home configuration and a lease

The gateway can listen on all three technologies at once, but only with one parameter set each.
A LoRa radio receives on one SF/BW at a time, and ESP-NOW on one channel. So the gateway still has
to be told which point comes next, and a failed point must not be able to strand it on a
configuration the node can no longer reach.

- **Home configuration.** One fixed LoRa point: SF12, 125 kHz, CR 4/8, 7 dBm, the slowest point in
  the [regulations.md](regulations.md) envelope (`homeConfig()` in `src/FieldProtocol.cpp`). The
  gateway boots into it and always falls back to it. Home carries control for **every**
  technology, ESP-NOW and Zigbee points included: LoRa outranges both, so one home is enough, and
  the gateway only ever has one transport up at a time.
- **Lease.** For each point the host sends the node a `LEASE`, which transmits the point's radio
  settings, the point id and a lease length to the gateway over home as a 35-byte binary frame.
  The gateway acks over home, switches, and answers pings on the point's configuration. The lease
  is the host's run timeout plus 30 s.
- **Release.** Once `RUN` finishes, the node sends a release on the point's configuration, and the
  gateway acks it and goes straight back to home. Without it every point would cost its full
  worst-case lease.
- **Expiry.** If the release is lost, or the point failed, the gateway **goes back to home when the
  lease runs out, whether the point worked or not.** When only the release's ack went missing,
  though, the gateway is already home: the first desk run logged `released` at the gateway for a
  point whose ack the node never saw, then waited out 250 s of a lease that had already ended. So
  after an unacknowledged release the host sends one lease at once, and waits out the lease only
  if that gets no answer either.
- **Lost ack.** The node can't tell a lost request from a lost ack. The host retries at once, which
  covers a lost request, and if that fails too it waits out the lease that may be running and
  tries a third time. Three failures mark the point failed; it isn't recorded, so `--resume`
  retries it. A repeated request for the same point does no harm.
- **Reboot or hang.** The gateway boots into home. The task watchdog (60 s) turns a hang into a
  reboot, so a wedged gateway costs one point, not the campaign.

The command never travels over the configuration being swept, only over home. If home fails at a
given distance, no swept point would have worked either: that distance is out of range, which is
a result in itself.

The alternative considered was GPS-disciplined time slots, where both boards compute the current
point from UTC and exchange no packets. It was rejected because it needs GPS reception under
canopy, a shared schedule compiled into both firmwares, and GPS power on the gateway. The lease
needs none of these.

## What the gateway records, and where

**Node→gateway PDR is counted at the gateway**. [metrics.md](metrics.md) counts PDR at the
receiver, per direction, and no host sits at the gateway. Without a record kept there, the
round-trip loss can't be split into the two directions. RTT, `send_us` and gateway→node arrivals
are all measured on the node and reach the laptop as they do today.

The gateway therefore writes **one record per lease to LittleFS on its internal flash**: campaign
id, point id, how the lease ended (released, expired, superseded, setup failed), the counters, and a
bitmap of the sequence numbers that arrived. It also writes one record per boot, with the reset
reason. `tools/field_gateway.py fetch` reads the log out and rewrites `node_rx.csv` from it,
which is the file the bench fills live, so the analysis counts node→gateway delivery the same way
in both. It also writes `gateway_leases.csv` and flags any reboots during the campaign. A point
leased twice gets the union of its leases' arrivals.

- **Not RAM.** A reboot or brownout loses it, and those are exactly the events a field log has to
  survive.
- **Not an SD card.** A campaign is about 800 points at well under 100 B each, which fits easily in
  the ~1.4 MB data partition of the `ttgo-t-beam` default layout. An SD card adds a part that can
  fail in the field (contacts, moisture, corruption on brownout) and needs SPI pins next to the
  LoRa radio.
- **Written at lease end**, after the point and before going back to home, so a flash write can
  never fall inside a measured window.

## Signal quality

**The pong stays a plain echo.** The gateway doesn't send its received-signal readings back:
the link is reciprocal (same frequency, antennas and power in both directions), so they would
mostly repeat the node's own reading, at the cost of a payload change. RSSI and SNR aren't a
comparison axis in any case ([metrics.md](metrics.md), signal quality section). The node keeps
LoRa RSSI/SNR, which it records at no cost.

One reading comes for free from the control path: every point records the RSSI and SNR of the
gateway's lease ack in `points.jsonl` (`field.home_rssi_dbm`, `field.home_snr_db`). It is the same
SF12 link at every point, so across a campaign it shows how the site's link margin moved.

## Duty cycle

The gateway only transmits in reply to the node, so the host's pacing of the node also limits the
gateway's airtime. The host's ledger is kept in `results/lora_airtime_ledger.txt` and reloaded by
every run, so a campaign started within the hour of another one inherits its airtime. Before
that, a second desk run went 50 % over the limit and the gateway's own ledger refused its replies
(findings.md §7). Control traffic counts too, and it isn't small: a lease request is **3.0 s** of
SF12 airtime and its ack 1.45 s, on every point of every technology. The host charges each
request, and the release frames of LoRa points, against its ledger, and prints the extra
duty-cycle time in the plan, which the allocation itself doesn't include.

No host watches the gateway, and `LoRaTransport`'s own `DutyCycleGuard` is rebuilt with every
`Transport`, forgetting its ledger at each lease boundary. So the gateway keeps its own ledger in
the application, spanning its whole uptime: every LoRa transmission it makes, acks and pongs alike,
goes through it, and a pong it can't afford is dropped and counted in the lease record rather than
sent. ConnectionLib is unchanged.

## Scope

- **4 km is LoRa only.** ESP-NOW and Zigbee reach a few hundred metres at most. Their leases still
  succeed, because control travels over LoRa, so past their range those points are recorded with
  zero delivery, which is the result. If home itself is out of reach, every lease fails and nothing
  is recorded: that site is beyond range. The three-technology comparison is a short-range result.
- **Field data is a separate campaign.** `meta.json` records the control mode (serial or lease),
  and field data is never pooled with bench data: different environment, different roles, a
  different control path.
