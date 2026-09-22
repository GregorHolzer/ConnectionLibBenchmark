"""The three per-technology benchmarks.

Each module owns its own sweep, its own cost model, and its own constraints, because the three
radios are not alike in what they expose or what they cost to measure:

* **LoRa** exposes its whole PHY (spreading factor, bandwidth, coding rate, transmit power,
  preamble) and pays for it: one transmission at SF12/84 B takes 4.8 s, and the 10 % duty cycle
  means wall clock is at least ten times airtime. Its sweep is therefore **one-factor-at-a-time**
  around a documented baseline - a full factorial would be several hundred points and take days.
* **ESP-NOW** exposes channel, encryption and timeout, and an exchange costs ~3.5 ms. A **full
  factorial** over channel x encryption x payload is affordable and catches interactions an OFAT
  sweep would miss.
* **Zigbee** exposes almost nothing through ConnectionLib - the module manages its own PHY - so
  the sweep is payload x addressing, again **full factorial**.

That asymmetry is a result in its own right, not a gap in the benchmark: it is what "the radio
manages itself" looks like from an application's point of view.
"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class Point:
    """One configuration point: the CFG parameters plus what it is varying and why.

    `factor` and `level` are what make the analysis possible - they say which hyperparameter this
    point moves relative to the baseline, so figures can be grouped by factor and the reliability
    pass can check whether the ordering within a factor stays stable across repetitions.
    """

    tech: str
    factor: str            # hyperparameter varied, or "baseline"
    level: Any             # its value at this point
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def label(self):
        return "baseline" if self.factor == "baseline" else f"{self.factor}={self.level}"

    @property
    def key(self):
        return f"{self.tech}/{self.factor}/{self.level}"


# Payload sizes every technology can carry. Zigbee's 84-byte unfragmented limit binds the common
# range (docs/metrics.md), and staying inside it is what makes the raw arm comparable across all
# three rather than three separate measurements.
COMMON_PAYLOADS = [8, 16, 32, 84]


def raw_stack_points(tech, baseline, payloads=None):
    """The no-library arm: the same exchange, driven straight onto the vendor API.

    The exposé asks for results that keep what the library contributes separable from what the
    radios contribute, and nothing measured that. These points are the other half of that
    subtraction - identical in every respect to their library counterparts except for `stack=raw`,
    which makes the firmware build a RawTransport instead of a ConnectionLib backend.

    Swept across payload rather than taken at one size because the library's cost is not constant
    in relative terms: ConnectionLib's 6-byte LoRa header costs a near-fixed +28.7 ms of airtime at
    the SF9 baseline, which is 14.5 % of an 8-byte exchange but only 4.2 % of an 84-byte one. A
    single size would report whichever of those happened to be measured.
    """
    out = []
    for payload in payloads or COMMON_PAYLOADS:
        params = dict(baseline)
        params["payload"] = payload
        params["stack"] = "raw"
        # The level carries the payload because all of these share a factor; without it they would
        # collapse onto one point_key and the sweep would be invisible to the analysis.
        out.append(Point(tech, "stack", f"raw+payload={payload}", params))
    return out


from . import espnow, lora, zigbee   # noqa: E402

REGISTRY = {"lora": lora, "espnow": espnow, "zigbee": zigbee}
ALL = ["espnow", "lora", "zigbee"]

# How long to hold each idle measurement. Long enough for the PPK2 to average over any periodic
# housekeeping the stack does, short enough that ~4 of them per rotation cost minutes, not hours.
IDLE_SECONDS = 30


def get(name):
    if name not in REGISTRY:
        raise KeyError(f"unknown technology {name!r}; known: {', '.join(REGISTRY)}")
    return REGISTRY[name]


def idle_points(techs):
    """The standing-cost measurements: a board floor plus one per technology.

    These exist because for a node that must live months on a battery, what a radio costs while
    merely *able* to receive very likely dominates what it costs to send. The three are not close -
    ESP-NOW cannot receive without WiFi up, LoRa sits in continuous receive, the XBee idles on its
    own - so a technology can lose the energy comparison outright on idle current regardless of how
    efficient it is per packet. Active-only measurement would never reveal that.

    The floor point (`tech=none`, every radio down) is what makes the others interpretable:
    subtracting it separates the radio's standing cost from the ESP32's.
    """
    points = [Point("none", "idle", "floor", {"tech": "none", "payload": 32})]
    for tech in techs:
        module = get(tech)
        params = dict(module.BASELINE)
        params["tech"] = tech
        points.append(Point(tech, "idle", "standing", params))
    return points
