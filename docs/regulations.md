# EU radio regulations and the fixed parameter envelope

Every configuration this benchmark runs is legal to transmit in the EU. Hyperparameter sweeps move
**within** the envelope below; they never move past it, and the firmware's `Conformance::check()`
rejects any configuration that would.

This matters for more than compliance. A benchmark that quietly raises transmit power or relaxes a
duty cycle to finish sooner is measuring a radio nobody can legally deploy — practice **P7** in
[benchmarking-practices.md](benchmarking-practices.md), measuring the deployed configuration.

## 433 MHz — LoRa

**ETSI EN 300 220-2**; **CEPT ERC-REC 70-03** Annex 1 band **h1.3**: 433.050–434.790 MHz, maximum
**10 mW e.r.p.**, duty cycle **≤ 10 %** across the whole band.

| Parameter | Fixed value / swept range | Reasoning |
|---|---|---|
| Centre frequency | **433.2 MHz** (fixed) | Must clear both band edges *including* the occupied bandwidth. A 125 kHz signal centred on 433.0 spans ~432.94–433.06 MHz — below the 433.050 MHz edge. |
| Bandwidth | 125 kHz, 250 kHz | 500 kHz is **rejected** at this centre: 433.2 ± 250 kHz = 432.95–433.45 MHz, outside the band. It would need a centre ≥ 433.3 MHz. |
| Spreading factor | 7–12 (swept) | No regulatory constraint; affects time-on-air enormously (see below). |
| Coding rate | 4/5 – 4/8 (swept) | No regulatory constraint. |
| TX power | 2, 5, **7 dBm** (swept, capped at 7) | The limit is 10 mW = 10 dBm **e.r.p.** With a ~2 dBi antenna, 7 dBm conducted ≈ 9 dBm e.r.p., leaving headroom. 17 dBm — the SX1278 default and ConnectionLib's former value — is 50 mW, five times the ceiling. |
| Duty cycle | **10 % / 1 h** (fixed, never relaxed) | = 360 s of airtime per rolling hour. |

### The duty cycle governs the whole campaign

Because transmission may occupy at most 10 % of any hour, **wall clock is at least ten times
airtime**. Time-on-air at BW125/CR4-7, computed from the SX127x symbol formula and confirmed
against hardware (201.9 ms measured vs. 197.6 ms predicted at SF9/8 B):

| SF | 8 B | 16 B | 32 B | 84 B |
|---|---|---|---|---|
| 7 | 57 ms | 71 ms | 107 ms | 214 ms |
| 8 | 99 ms | 127 ms | 185 ms | 371 ms |
| 9 | 198 ms | 255 ms | 341 ms | 685 ms |
| 10 | 338 ms | 453 ms | 625 ms | 1255 ms |
| 11 | 791 ms | 905 ms | 1364 ms | 2626 ms |
| 12 | 1352 ms | 1810 ms | 2499 ms | 4792 ms |

One transmission at SF12/84 B takes **4.8 seconds**. A single repetition of the 15-point LoRa
sweep at 20 exchanges per point costs ~322 s of airtime, so it needs **≥ 54 minutes** of wall clock
no matter how fast the host is. That is the entire reason the full campaign is budgeted at 10 h,
and why LoRa completes ~6 repetitions where ESP-NOW completes ~28.

Reported as a result, not normalised away: a technology that cannot legally transmit as often *is*
worse for a high-rate application.

## 2.4 GHz — ESP-NOW and Zigbee

**ETSI EN 300 328 V2.2.2**: 2400–2483.5 MHz, ≤ **100 mW (20 dBm) EIRP**, power spectral density
≤ 10 dBm/MHz for non-FHSS equipment.

**No duty-cycle limit applies.** This is not because the band is unregulated — the standard imposes
a 10 % *medium-utilisation* cap, but only on **non-adaptive** equipment. ESP-NOW inherits 802.11's
CSMA/CA and Zigbee uses 802.15.4 CSMA/CA, so both qualify as adaptive and are exempt. Hence
`dutyCycle{100.0f, …}` in `EspNowConfig` and `ZigbeeConfig` is correct rather than merely
permissive.

| Parameter | Value | Notes |
|---|---|---|
| ESP-NOW channel | 1 (fixed) | Not swept: every channel has the same modulation and power, so a channel differs only by the Wi-Fi around a site. |
| ESP-NOW TX power | ESP32 default (~20 dBm EIRP) | Not exposed by `EspNowConfig`; would need a library change. |
| Zigbee channel | set by PAN association | Both modules currently associate on channel 0x15. |
| Zigbee power level | XBee default | `PL` is settable only via AT frames, not through `ZigbeeConfig`. |

## The bands cannot be equalised

10 mW at 433 MHz versus 100 mW at 2.4 GHz is **law, not tuning**. There is no configuration in
which the two bands radiate the same power legally, so cross-band results are reported as
properties of each technology rather than normalised to a common transmit power. Within a band,
EIRP normalisation is meaningful and is applied.

## Sources

- [ETSI EN 300 220-2 V3.2.1](https://www.etsi.org/deliver/etsi_en/300200_300299/30022002/03.02.01_60/en_30022002v030201p.pdf)
- [CEPT ERC-REC 70-03](https://www.arcep.fr/fileadmin/reprise/dossiers/frequences/ERC-REC-70-03E-version02.PDF)
- [ETSI EN 300 328 V2.2.2](https://www.etsi.org/deliver/etsi_en/300300_300399/300328/02.02.02_60/en_300328v020202p.pdf)
