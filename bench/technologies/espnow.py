"""ESP-NOW benchmark: full factorial over channel x encryption x payload.

An exchange costs ~3.5 ms and there is no duty-cycle limit in the 2.4 GHz band for adaptive
equipment, so unlike LoRa this benchmark can afford a **full factorial** rather than a
one-factor-at-a-time sweep. That matters: a factorial catches interactions - whether, say, the
cost of encryption depends on payload size - which OFAT cannot see by construction.

What is *not* here is as informative as what is. ESP-NOW exposes no spreading factor, no coding
rate, no bandwidth, and ConnectionLib exposes no transmit-power control for it (`esp_wifi_set_
max_tx_power` would need a library change). The PHY is managed by the radio, and an application
gets a channel, an encryption flag and a timeout. The sparse sweep is the honest picture.
"""

import itertools

from . import Point, raw_stack_points

NAME = "espnow"

BASELINE = {"channel": 1, "encrypt": 0, "payload": 32, "timeout": 2000}

# 1/6/11 are the three non-overlapping 2.4 GHz channels; 250 B is ESP-NOW's ceiling, well above
# Zigbee's 84 B, so it is reported as a capability rather than mixed into cross-technology
# comparisons.
FACTORIAL = {
    "channel": [1, 6, 11],
    "encrypt": [0, 1],
    "payload": [8, 16, 32, 84, 250],
}
# Swept one-factor-at-a-time on top of the factorial: a send timeout only affects the failure
# path, so crossing it with everything else would triple the run for no additional information.
OFAT = {"timeout": [500, 2000]}

EXCHANGES = 100


def points():
    out = []
    keys = list(FACTORIAL)
    for combination in itertools.product(*(FACTORIAL[k] for k in keys)):
        params = dict(BASELINE)
        params.update(dict(zip(keys, combination)))
        # Name the point after whichever factor is off-baseline; points differing in more than one
        # get a composite label so the analysis can still group them.
        varied = [f"{k}={v}" for k, v in zip(keys, combination) if v != BASELINE[k]]
        if not varied:
            factor, level = "baseline", ""
        elif len(varied) == 1:
            factor, level = varied[0].split("=")
        else:
            factor, level = "combination", "+".join(varied)
        out.append(Point(NAME, factor, level, params))

    for factor, levels in OFAT.items():
        for level in levels:
            if level == BASELINE[factor]:
                continue
            params = dict(BASELINE)
            params[factor] = level
            out.append(Point(NAME, factor, level, params))
    out.extend(raw_stack_points(NAME, BASELINE))
    return out


def exchanges(point):
    return EXCHANGES


def cfg_params(point, peer):
    p = dict(point.params)
    p["tech"] = NAME
    p["n"] = exchanges(point)
    p["replywait"] = 2000
    p["spacing"] = 100
    p["peermac"] = peer["mac"]
    return p


def airtime_seconds(point):
    return 0.0   # no duty-cycle limit applies; see docs/regulations.md


def estimated_seconds(point):
    # ~3.5 ms exchange plus the 100 ms pacing floor, which dominates entirely.
    return (5 + 100) * exchanges(point) / 1000.0
