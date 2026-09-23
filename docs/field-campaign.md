# Field campaign

How the benchmark runs when the two boards are up to 4 km apart and only one of them can have a
computer attached. **This is the design; none of it is implemented yet.** Every serial
`CFG`/`RUN` path the bench uses stays as it is. Field mode is added alongside it, not in place
of it.

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

- **Home configuration.** A fixed, most-robust point per technology: for LoRa SF12, 125 kHz,
  10 mW, the slowest point in the [regulations.md](regulations.md) envelope. The gateway boots
  into it and always falls back to it.
- **Lease.** The node sends `CFG(point_id, params, lease_s)` over home. The gateway ACKs over
  home, switches, answers for the length of the lease, and **goes back to home when the lease
  expires, whether the point worked or not.**
- **Lost ACK.** The node can't tell a lost `CFG` from a lost ACK. Either way it waits
  `lease + guard` and retries: by then the gateway is back on home. `point_id` is idempotent, so
  a repeated `CFG` does no harm.
- **Reboot or hang.** The gateway boots into home. With the task watchdog enabled, a wedged
  gateway costs one point, not the campaign.

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

The gateway therefore writes **one record per point to LittleFS on its internal flash**:
`point_id`, a bitmap of received sequence numbers, and boot/lease events. The log is read out
over USB when the gateway is collected.

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

## Duty cycle

The gateway only transmits in reply to the node, so the host's pacing of the node also limits the
gateway's airtime. Control traffic counts toward the budget as well: a `CFG` and its ACK at SF12
take a few seconds of airtime per point. The on-device `DutyCycleGuard` is the gateway's only
safety net, because no host watches it. Today the firmware rebuilds the guard with every
`Transport`, which throws its ledger away on each `CFG`. Field mode needs the guard to survive
that rebuild.

## Scope

- **4 km is LoRa only.** ESP-NOW and Zigbee reach a few hundred metres at most. Past that the node
  gets no ACK on home, and the points are recorded as out of range. The three-technology
  comparison is a short-range result.
- **Field data is a separate campaign.** `meta.json` records the control mode (serial or lease),
  and field data is never pooled with bench data: different environment, different roles, a
  different control path.
