"""Zigbee benchmark: payload x addressing, full factorial.

The XBee S2C runs its own network stack, so ConnectionLib exposes essentially no PHY control -
`ZigbeeConfig` is pins, UART baud and a duty-cycle budget, and that is all. What is left to sweep
is payload size and the one genuinely consequential application-level choice: whether the gateway
unicasts or broadcasts.

**Addressing is worth a factor of eight.** A ZB broadcast blocks ~950-1020 ms inside `send()`
waiting for its APS status, and the node's reply arrives inside that window, so it gets buffered
and the measured round trip collapses to 0 ms. A unicast resolves in ~53 ms. Sweeping it turns
that from a trap into a measurement.

**UART baud is deliberately excluded.** Changing it requires the module's `BD` parameter and the
ESP32's serial port to change in lockstep; a desync leaves the XBee unreachable until it is
physically reset, which is not a risk worth running unattended for ten hours.
"""

import itertools

from . import Point, raw_stack_points

NAME = "zigbee"

BASELINE = {"payload": 32, "broadcast": 0}

FACTORIAL = {
    "payload": [8, 16, 32, 84],   # 84 B is the XBee S2C's unfragmented RF payload limit
    "broadcast": [0, 1],
}

EXCHANGES = 100


def points():
    out = []
    keys = list(FACTORIAL)
    for combination in itertools.product(*(FACTORIAL[k] for k in keys)):
        params = dict(BASELINE)
        params.update(dict(zip(keys, combination)))
        varied = [f"{k}={v}" for k, v in zip(keys, combination) if v != BASELINE[k]]
        if not varied:
            factor, level = "baseline", ""
        elif len(varied) == 1:
            factor, level = varied[0].split("=")
        else:
            factor, level = "combination", "+".join(varied)
        out.append(Point(NAME, factor, level, params))
    out.extend(raw_stack_points(NAME, BASELINE))
    return out


def exchanges(point):
    return EXCHANGES


def cfg_params(point, peer):
    p = dict(point.params)
    p["tech"] = NAME
    p["n"] = exchanges(point)
    # Generous enough for a broadcast's ~1 s APS status wait plus the reply.
    p["replywait"] = 4000
    p["spacing"] = 100
    p["peerxbee"] = peer["xbee64"]
    return p


def airtime_seconds(point):
    return 0.0   # no duty-cycle limit applies; see docs/regulations.md


def estimated_seconds(point):
    # Unicast ~125 ms round trip; broadcast ~1.05 s, almost all of it inside send().
    per_exchange = 1150 if point.params.get("broadcast") else 250
    return per_exchange * exchanges(point) / 1000.0
