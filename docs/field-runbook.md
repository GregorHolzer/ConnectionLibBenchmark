# Field runbook

What to plug in, in what order, and what good output looks like. The *why* is in
[field-campaign.md](field-campaign.md) — this file does not restate it.

Two hosts are involved and they do very different jobs. **The laptop is the host**: it carries the
node and the PPK2, drives the campaign, and holds the results. **Whatever the gateway is plugged
into is a power supply and nothing else** — no software on it takes part in a run. That asymmetry
is the whole of field mode.

---

## 1. Wiring

```
        laptop (the host)                            gateway's power source
   ┌─────────────────────┐                        ┌──────────────────────┐
   │ USB ─── PPK2 ───────┼── 5V  → T-Beam pin 10  │  USB ──┐             │
   │         source mode │   GND → T-Beam pin  9  │        │ power only  │
   │         5000 mV     │                        │        ▼             │
   │                     │        NODE            │     GATEWAY          │
   │ USB ────────────────┼── micro-USB,           │    5748001588        │
   │   data only         │   red VBUS conductor   │   ordinary cable     │
   └─────────────────────┘   cut — 573C000618     └──────────────────────┘
                                   │                       │
                              SX1278 + XBee           SX1278 + XBee
                                   │                  (the coordinator)
                                   └────── over the air ───┘
                                     LoRa 433.2 MHz SF12 — "home"
```

Both USB plugs — the PPK2 and the node's data cable — go into the laptop. The node draws around
140 mA through the PPK2, well inside one port's budget.

For the **simulated outing at home**, the gateway's power source is the desktop: an ordinary
micro-USB cable into any port, and nothing on the desktop runs. For a **real outing** it is a USB
power bank, and the laptop walks away from it.

Four things to get right, each of which otherwise looks like a different fault entirely:

- **Source mode must be 5000 mV.** Below about 4 V the AXP2101 rejects its VBUS input and gates
  every ESP32 rail. The CH9102 still enumerates, so it presents as a dead UART rather than as a
  power problem. 5000 mV is the default in `bench/power.py`; don't override it.
- **Only the red VBUS conductor of the node's cable is cut**, so the laptop carries data but
  cannot power the board. The consequence runs through all the tooling: the node has **no serial
  port at all** until the PPK2 raises the rail, which is why bring-up is power → enumerate → open
  and never the other way round.
- **Antennas fitted** on both SX1278s and both XBees. A transmit status of "success" means *sent*,
  not *received* — two modules with no antennas once exchanged nothing at all while reporting
  `status=0x00`.
- **Nothing else may hold the PPK2.** Its port is exclusive, and the nRF Connect Power Profiler
  GUI is the usual culprit; the failure surfaces as "device reports readiness to read but returned
  no data". `run_field.py` checks this and names the process.

---

## 2. Once per laptop, at home, with network

```bash
./tools/field_laptop_setup.sh
```

Clones or updates the repo with its submodule, installs `ppk2-api`/`pyserial`/`matplotlib` into
PlatformIO's venv, and prebuilds `field_node` and `field_gateway`. That last step is the point:
a PlatformIO project downloads its library dependencies on first build, and the field has no
network. Re-running it is safe.

---

## 3. Before leaving — the gateway

With the gateway on USB, on either machine:

```bash
tools/field_gateway.py flash      # prints the MAC; ESP-NOW needs it
tools/field_gateway.py status     # role=field-gateway, and the log size
tools/field_gateway.py clear --yes   # only once every earlier campaign has been fetched
```

Then **power-cycle it** and move its cable to a power-only port. Two reasons, both of which cost a
run once:

- Its airtime ledger lives in RAM and spans its uptime, so it still holds whatever LoRa airtime
  the previous run spent. A gateway that remembers an hour it cannot afford drops pongs silently
  and counts them in the lease record rather than answering — 34 of 40 replies, in
  [findings.md](findings.md) §7.
- It boots into the home config by itself, and the reboot is recorded in its log, so a clean
  power-cycle before a campaign makes the log unambiguous.

Its flash log survives unplugging, which is exactly why the log is in LittleFS and not RAM.

---

## 4. The run

```bash
./run_field.py --site "desk, 0 m" --techs lora --budget-hours 0.3 --dry-run   # the plan only
./run_field.py --site "desk, 0 m" --techs lora --budget-hours 0.3 --gateway-mac <mac>
```

`--site` is mandatory and is the only record of where the node was. `--gateway-mac` is cached
after the first use, so later sites need only `--site`. Everything else is passed through to
`run_campaign.py`, including `--preset`, `--budget-hours`, `--rssi` and `--no-power`.

**One campaign per site.** Field data is never pooled with bench data, and the two desk runs of
2026-09-23 are not comparable with a 1 km run either.

Watch it from a second shell:

```bash
./progress.py --watch
```

### What good output looks like

Per point, in `points.jsonl` under `field`:

| field | good | what it means otherwise |
|---|---|---|
| `lease_attempts` | 1 | 2–3 means requests or acks are being lost; the link margin on home is thin |
| `released` | `true` | `false` costs the next point one probe, and the lease if the probe fails too |
| `home_rssi_dbm`, `home_snr_db` | present | absent means the ack carried no reading — investigate before trusting the point |

`home_rssi_dbm` is the same SF12 link at every point, so across a campaign it is the record of how
the site's margin moved. On the desk it read −128 to −132 dBm with SNR −5.8 to −9 dB; that is a
weak desk link, not a field-mode effect.

And the duty cycle will visibly stop the campaign for minutes at a time on LoRa. That is the
regulation being enforced, not a hang — `run_campaign.py` announces each wait before it starts.

---

## 5. Collection

Node→gateway delivery is counted **at the gateway**, and only the gateway has it. Plug the gateway
into the laptop and read it out:

```bash
tools/field_gateway.py fetch --results results/campaign-YYYYMMDD-HHMMSS
```

One `fetch` per campaign, before any `clear`. It keeps the raw log as `gateway_log.txt`, rewrites
`node_rx.csv` — the same file the bench fills live, so the analysis counts both the same way — and
writes `gateway_leases.csv`. Fetching twice gives the same files, and one log can hold several
campaigns because records are matched by the campaign id in `meta.json`.

Check, before trusting the data:

- `gateway_leases.csv` has a row for every recorded point, and its arrival counts match the node's
  successes.
- No reboot rows fall inside the campaign. One means a point's counters are suspect.

Then:

```bash
./verify_practices.py --results results/campaign-...    # the audit
./plot_results.py     --results results/campaign-...
./plot_comparison.py  --results results/campaign-...
```

---

## 6. When it goes wrong

**Resume.** `run_campaign.py` prints the exact command on the way out; `run_field.py` passes
`--resume` through.

```bash
./run_field.py --resume results/campaign-YYYYMMDD-HHMMSS
```

**Omit `--techs` and `--preset` when resuming.** A resume rebuilds the schedule from the campaign's
recorded plan, and a plan given on the command line that would put a recorded point at a different
index is refused outright — which is the right outcome, but it means the flags are not merely
redundant. `run_field.py` therefore passes `--techs` only when you ask for it explicitly.

**"the field gateway never acked a lease over home".** Three failed requests at SF12. Either the
site is out of range — which is a result, and the honest one — or the gateway is dead, has drifted
off home, or has lost power. Walk back toward it and retry: if home fails at a distance, no swept
point at that distance would have worked either.

**A point stalls.** The node stops the measured loop on any host input and `resync()` confirms
with a `PING`, so a timed-out point no longer keeps running behind the host's back. If a point
does fail it is simply not recorded, and `--resume` retries it.

**Point index past 65535.** The wire carries the point id as 16 bits. Split the campaign.

---

## 7. Traps that produce plausible numbers rather than errors

These do not fail. They return figures that pass every internal consistency check and are wrong.

- **Field energy is *initiator* energy.** Roles are swapped relative to the bench: here the metered
  board is the one that initiates. Field and bench energy are not comparable even at the same
  configuration point.
- **Discard the first one or two ESP-NOW points of a run.** The radio is still warming up; point #0
  has read *below ESP-NOW's own idle current*, which is impossible while transmitting, and gives a
  negative above-idle figure. Any "payload raises ESP-NOW energy" trend from a short run is this
  ramp. See [findings.md](findings.md).
- **`energy_mj / delivered` is not comparable across technologies.** The measured window is
  radio-active 2.7–3.9 % of the time for ESP-NOW and 99.4–99.9 % for LoRa. Use above-idle energy
  for any cross-technology claim.
- **Short runs carry no idle points**, so standing current has to come from `tools/energy_check.py`
  separately. Standing current probably matters more than per-packet energy for a node that must
  last months.
- **The Zigbee peer is unverified.** The gateway must carry the **coordinator** module, and
  `--field` defaults `--zigbee-peer` to `0013A20042035C03` on that assumption. There is no
  host-side way to read an XBee's address, and a wrong one makes every Zigbee `send()` time out —
  recorded as zero delivery, indistinguishable from out of range.

  The cheap check is empirical and belongs in the 0 m desk run: if LoRa delivers and Zigbee records
  zero everywhere, retry with `--zigbee-peer 0013A20042035B39`. The heavy check is the
  `xbee_config` diagnostic firmware (on the Pi, `~/dev/xbee_config`), which reads `SH`/`SL` and
  `CE` back over API frames — but note it also *writes* `AP=1`, where the bench has been running
  `AP=2`, so don't reach for it casually the day before a campaign.
