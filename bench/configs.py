"""The configurations the node is flashed with.

One `Config` is one firmware build. There are two sets:

* `baseline_configs()` - measured once, at any distance: the board floor (every radio down) and one
  standing-current hold per technology (radio up and listening, nothing sent).
* `site_configs()` - measured at every distance: each technology swept one factor at a time
  around its baseline.

All three are swept over the same payload sizes; LoRa also over every radio knob, because it is
the only radio that exposes its physical layer through the library. Up to 84 B, Zigbee's
single-packet limit, is the comparison: every radio sends one bare packet. 256 and 512 B are a
capability result of the library: a payload above its radio's limit goes through ConnectionLib's
FragmentingTransport.

The gateway has no host: before every run the node tells it over LoRa home which radio and
parameters come next (src/gateway.cpp). The sweep is one-factor-at-a-time rather than factorial
because airtime is the budget: a factorial would be several hundred points against a 10 % duty
cycle.
"""

from dataclasses import dataclass, field, replace

from .airtime import CONTROL_FRAME_BYTES, LORA_HEADER_BYTES, time_on_air_ms

IDLE_MS = 30_000   # long enough for the PPK2 to average over a stack's periodic housekeeping

# The EU-legal operating point (docs/regulations.md), and ConnectionLib's own defaults.
LORA_BASELINE = {"bw": 125, "sf": 9, "cr": 7, "pwr": 7, "preamble": 8, "payload": 32}

# The comparison: up to Zigbee's single-packet limit, the largest payload all three carry
# unfragmented, which is the range the thesis methodology confines it to.
PAYLOADS = [8, 16, 32, 84]
# The library's capability beyond that: sizes a radio can only carry fragmented. Reported apart.
CAPABILITY_PAYLOADS = [256, 512]

# Every level is inside the envelope src/Config.hpp asserts. Absent on purpose: 500 kHz, which at
# a 433.2 MHz centre leaves the band, and anything above 7 dBm, which exceeds 10 mW e.r.p.
LORA_FACTORS = {
    "sf": [7, 8, 9, 10, 11, 12],
    "bw": [125, 250],
    "cr": [5, 6, 7, 8],
    "pwr": [2, 5, 7],
    "preamble": [8, 16],
    "payload": PAYLOADS + CAPABILITY_PAYLOADS,
}

# The channel is not swept: it changes the interference at a site, not the radio.
ESPNOW_BASELINE = {"channel": 1, "payload": 32}
ZIGBEE_BASELINE = {"payload": 32}
ESPNOW_FACTORS = {"payload": PAYLOADS + CAPABILITY_PAYLOADS}
# Zigbee stops at 32 B. In the field on 2026-10-10 every run of 84 B frames (84, 256, 512 B)
# lost the link about 5 s in, at 10 m, and aborted; the cause is open (docs/field-campaign.md).
ZIGBEE_FACTORS = {"payload": [8, 16, 32]}

# src/Config.hpp kNativeLimitBytes: each radio's single-packet limit. A larger payload is wrapped
# in a FragmentingTransport, which cuts it into packets of that size, each with a 3-byte header.
NATIVE_LIMIT = {"zigbee": 84, "espnow": 250, "lora": 248}
FRAGMENT_HEADER_BYTES = 3

# Sized so that every run lasts seconds: the PPK2 window is only aligned to a few tens of
# milliseconds, and 100 ESP-NOW payloads were over in 0.17 s, too short to meter. LoRa gets the
# fewest because its airtime is what the duty cycle rations.
EXCHANGES = {"lora": 20, "espnow": 1500, "zigbee": 100}
WRAPPED_LORA_EXCHANGES = 5   # a 512 B message is 3.7 s on the air at the baseline


@dataclass(frozen=True)
class Config:
    tech: str     # "none" | "espnow" | "lora" | "zigbee"
    name: str     # "baseline", "sf=12", "floor", "standing"
    params: dict = field(default_factory=dict)
    exchanges: int = 0
    idle_ms: int = 0

    @property
    def key(self):
        return f"{self.tech}/{self.name}"

    @property
    def payload(self):
        return self.params.get("payload", 32)

    @property
    def wrapped(self):
        return self.payload > NATIVE_LIMIT.get(self.tech, self.payload)


def baseline_configs():
    return [
        Config("none", "floor", {}, idle_ms=IDLE_MS),
        Config("zigbee", "standing", dict(ZIGBEE_BASELINE), idle_ms=IDLE_MS),
        Config("espnow", "standing", dict(ESPNOW_BASELINE), idle_ms=IDLE_MS),
        Config("lora", "standing", dict(LORA_BASELINE), idle_ms=IDLE_MS),
    ]


def _sweep(tech, baseline, factors):
    """A baseline plus every single-factor deviation from it."""
    out = [Config(tech, "baseline", dict(baseline), EXCHANGES[tech])]
    for factor, levels in factors.items():
        for level in levels:
            if level != baseline[factor]:
                params = dict(baseline, **{factor: level})
                config = Config(tech, f"{factor}={level}", params, EXCHANGES[tech])
                if tech == "lora" and config.wrapped:
                    config = replace(config, exchanges=WRAPPED_LORA_EXCHANGES)
                out.append(config)
    return out


def lora_configs():
    return _sweep("lora", LORA_BASELINE, LORA_FACTORS)


def site_configs(techs=("zigbee", "espnow", "lora")):
    """What is measured at one distance, in the order it runs."""
    out = []
    if "zigbee" in techs:
        out.extend(_sweep("zigbee", ZIGBEE_BASELINE, ZIGBEE_FACTORS))
    if "espnow" in techs:
        out.extend(_sweep("espnow", ESPNOW_BASELINE, ESPNOW_FACTORS))
    if "lora" in techs:
        out.extend(lora_configs())
    return out


def packet_sizes(config):
    """Bytes handed to the backend for each packet of one message: one packet unless wrapped."""
    if not config.wrapped:
        return [config.payload]
    per_packet = NATIVE_LIMIT[config.tech] - FRAGMENT_HEADER_BYTES
    return [min(per_packet, config.payload - offset) + FRAGMENT_HEADER_BYTES
            for offset in range(0, config.payload, per_packet)]


def _lora_airtime_ms(config, payload_bytes, header_bytes):
    if config.tech != "lora":
        return 0.0
    p = config.params
    return time_on_air_ms(payload_bytes, sf=p["sf"], bw_khz=p["bw"], coding_rate=p["cr"],
                          preamble=p["preamble"], header_bytes=header_bytes)


def payload_airtime_ms(config, header_bytes=LORA_HEADER_BYTES):
    """Time-on-air of one message, over all its packets. 0 off LoRa."""
    return sum(_lora_airtime_ms(config, size, header_bytes) for size in packet_sizes(config))


def control_airtime_ms(config, header_bytes=LORA_HEADER_BYTES):
    """Time-on-air of a header-only benchmark frame (probe, report request). 0 off LoRa."""
    wrapper_bytes = FRAGMENT_HEADER_BYTES if config.wrapped else 0
    return _lora_airtime_ms(config, CONTROL_FRAME_BYTES + wrapper_bytes, header_bytes)


def ack_wait_ms(config):
    """How long LoRa's send() waits for the library's ack, which is a bare header on the air.

    Must cover that ack's time-on-air - over a second at SF12 - plus the gateway's turnaround.
    Kept short otherwise, because every lost payload costs the whole wait. ESP-NOW and Zigbee
    time their own acks inside the radio; for them this only bounds the wait for the report.
    """
    return int(max(1000, 2 * _lora_airtime_ms(config, 0, LORA_HEADER_BYTES) + 500))


def lease_seconds(config):
    """How long the gateway stays on a run's radio if it never hears the end message.

    An upper bound on the run. LoRa: every packet's airtime and a full ack wait, with 100 ms of
    slack each, then the report; rounded up to the 4 s step the config frame carries. ESP-NOW and
    Zigbee time their own acks and the loop stops after 10 losses in a row, so a fixed bound per
    packet of a message does.
    """
    if config.exchanges == 0:
        return 0
    packets = len(packet_sizes(config))
    if config.tech != "lora":
        seconds = {"espnow": 32, "zigbee": 60}[config.tech] * packets
    else:
        per_exchange_ms = payload_airtime_ms(config) + packets * (100 + ack_wait_ms(config))
        seconds = (config.exchanges + 3) * per_exchange_ms / 1000.0 + 15
    return min(1020, int(-(-seconds // 4) * 4))
