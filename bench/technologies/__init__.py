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


from . import espnow, lora, zigbee   # noqa: E402

REGISTRY = {"lora": lora, "espnow": espnow, "zigbee": zigbee}
ALL = ["espnow", "lora", "zigbee"]


def get(name):
    if name not in REGISTRY:
        raise KeyError(f"unknown technology {name!r}; known: {', '.join(REGISTRY)}")
    return REGISTRY[name]
