"""The configurations the node is flashed with.

One `Config` is one firmware build. There are two sets:

* `baseline_configs()` - measured once, at any distance: the board floor (every radio down) and one
  standing-current hold per technology (radio up and listening, nothing sent).
* `site_configs()` - measured at every distance: one Zigbee point, one ESP-NOW point, and LoRa
  swept one factor at a time around its baseline.

ESP-NOW and Zigbee get a single point because the gateway is flashed once and has no host: it
listens on one WiFi channel and one XBee network for the whole campaign. LoRa is swept because it
is the only radio that exposes its physical layer, and the gateway can follow a LoRa setting over
the air (src/gateway.cpp). The sweep is one-factor-at-a-time rather than factorial because airtime
is the budget: a factorial would be several hundred points against a 10 % duty cycle.
"""

from dataclasses import dataclass, field

from .airtime import CONTROL_FRAME_BYTES, LORA_HEADER_BYTES, time_on_air_ms

IDLE_MS = 30_000   # long enough for the PPK2 to average over a stack's periodic housekeeping

# The EU-legal operating point (docs/regulations.md), and ConnectionLib's own defaults.
LORA_BASELINE = {"bw": 125, "sf": 9, "cr": 7, "pwr": 7, "preamble": 8, "payload": 32}

# Every level is inside the envelope src/Config.hpp asserts. Absent on purpose: 500 kHz, which at
# a 433.2 MHz centre leaves the band, and anything above 7 dBm, which exceeds 10 mW e.r.p.
LORA_FACTORS = {
    "sf": [7, 8, 9, 10, 11, 12],
    "bw": [125, 250],
    "cr": [5, 6, 7, 8],
    "pwr": [2, 5, 7],
    "preamble": [8, 16],
    "payload": [8, 16, 32, 84],
}

ESPNOW_BASELINE = {"channel": 1, "payload": 32}
ZIGBEE_BASELINE = {"payload": 32}

# LoRa gets fewer exchanges than the 2.4 GHz radios: its airtime is what the duty cycle rations.
EXCHANGES = {"lora": 20, "espnow": 100, "zigbee": 100}


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


def baseline_configs():
    return [
        Config("none", "floor", {}, idle_ms=IDLE_MS),
        Config("zigbee", "standing", dict(ZIGBEE_BASELINE), idle_ms=IDLE_MS),
        Config("espnow", "standing", dict(ESPNOW_BASELINE), idle_ms=IDLE_MS),
        Config("lora", "standing", dict(LORA_BASELINE), idle_ms=IDLE_MS),
    ]


def lora_configs():
    """The LoRa baseline plus every single-factor deviation from it."""
    out = [Config("lora", "baseline", dict(LORA_BASELINE), EXCHANGES["lora"])]
    for factor, levels in LORA_FACTORS.items():
        for level in levels:
            if level != LORA_BASELINE[factor]:
                params = dict(LORA_BASELINE, **{factor: level})
                out.append(Config("lora", f"{factor}={level}", params, EXCHANGES["lora"]))
    return out


def site_configs(techs=("zigbee", "espnow", "lora")):
    """What is measured at one distance, in the order it runs."""
    out = []
    if "zigbee" in techs:
        out.append(Config("zigbee", "baseline", dict(ZIGBEE_BASELINE), EXCHANGES["zigbee"]))
    if "espnow" in techs:
        out.append(Config("espnow", "baseline", dict(ESPNOW_BASELINE), EXCHANGES["espnow"]))
    if "lora" in techs:
        out.extend(lora_configs())
    return out


def ping_airtime_ms(config, header_bytes=LORA_HEADER_BYTES):
    """Time-on-air of one payload frame. 0 off LoRa."""
    if config.tech != "lora":
        return 0.0
    p = config.params
    return time_on_air_ms(config.payload, sf=p["sf"], bw_khz=p["bw"], coding_rate=p["cr"],
                          preamble=p["preamble"], header_bytes=header_bytes)


def ack_airtime_ms(config, header_bytes=LORA_HEADER_BYTES):
    """Time-on-air of the gateway's 8-byte ack, or of any header-only frame. 0 off LoRa."""
    if config.tech != "lora":
        return 0.0
    p = config.params
    return time_on_air_ms(CONTROL_FRAME_BYTES, sf=p["sf"], bw_khz=p["bw"], coding_rate=p["cr"],
                          preamble=p["preamble"], header_bytes=header_bytes)


def reply_wait_ms(config):
    """How long the node waits for an ack. Must exceed the ack's own time-on-air, which at SF12
    is over a second; short otherwise, because every lost payload costs the whole wait."""
    return int(max(1000, 3 * ack_airtime_ms(config) + 500))


def lease_seconds(config):
    """How long the gateway stays on a LoRa point if it never hears the release.

    An upper bound on the run: every exchange's ping and a full reply wait, with 100 ms of slack
    each, then the three count probes. Rounded up to the 4 s step the switch frame carries.
    """
    if config.tech != "lora" or config.exchanges == 0:
        return 0
    per_exchange_ms = 100 + ping_airtime_ms(config) + reply_wait_ms(config)
    seconds = (config.exchanges + 3) * per_exchange_ms / 1000.0 + 15
    return min(1020, int(-(-seconds // 4) * 4))
