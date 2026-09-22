"""LoRa benchmark: the full PHY, swept one factor at a time.

LoRa is the only one of the three that exposes its physical layer, and the only one whose cost
model is dominated by regulation rather than by software. Two consequences shape this module:

* **One-factor-at-a-time, not factorial.** 6 spreading factors x 2 bandwidths x 4 coding rates x
  3 power levels x 4 payloads is 576 points; at a mean time-on-air of several hundred milliseconds
  and a 10 % duty cycle that is measured in days. OFAT around a documented baseline gives 16
  points and still isolates each factor's effect.
* **Airtime is the budget.** `airtime_seconds()` is the real currency of the campaign - see
  docs/regulations.md. The scheduler paces against it.
"""

import math

from . import Point, raw_stack_points

NAME = "lora"

# The EU-legal operating point from docs/regulations.md, and ConnectionLib's own defaults.
BASELINE = {
    "freq": 433.2,
    "bw": 125,
    "sf": 9,
    "cr": 7,
    "pwr": 7,
    "preamble": 8,
    "payload": 32,
}

# Every level here is inside the envelope of docs/regulations.md; the firmware's conformance guard
# is the backstop, not the plan. Notably absent: bandwidth 500 kHz, which at a 433.2 MHz centre
# would span 432.95-433.45 MHz and fall out of the band, and transmit powers above 7 dBm, which
# would exceed the 10 mW e.r.p. ceiling.
FACTORS = {
    "sf": [7, 8, 9, 10, 11, 12],
    "bw": [125, 250],
    "cr": [5, 6, 7, 8],
    "pwr": [2, 5, 7],
    "preamble": [8, 16],
    "payload": [8, 16, 32, 84],
}

EXCHANGES = 20   # deliberately lower than the 2.4 GHz benchmarks; airtime is the constraint


# ConnectionLib prepends this to every LoRa frame (destNodeId/srcNodeId/seq/payloadLen), because a
# raw SX1278 has no addressing of its own. The raw arm sends none of it, which is the whole point
# of measuring that arm - so it is a parameter rather than a constant.
HEADER_BYTES = 6


def time_on_air_ms(payload_bytes, sf=9, bw_khz=125.0, coding_rate=7, preamble=8,
                    header_bytes=HEADER_BYTES):
    """Time-on-air for one transmission, from the SX127x symbol-count formula.

    `payload_bytes` is the application payload and `header_bytes` what the stack adds on top:
    HEADER_BYTES for ConnectionLib, 0 for the raw arm. It used to be hardcoded, which silently
    over-charged raw LoRa points by six bytes of airtime against the duty-cycle budget - and,
    less harmfully, made a prediction of the library's own cost come out at exactly half the
    measured value, because the header got counted on both sides of the comparison.

    Validated against hardware: predicts 197.6 ms at SF9/8 B where the boards measure 201.9 ms.
    That agreement is also what exposed the original harness measuring only one leg of the round
    trip - it reported 212 ms for what it called a round trip, i.e. a single leg plus processing.

    Validated again by the raw arm: the predicted round-trip cost of the header is 114.7 ms at
    8, 16 and 84 bytes and 57.3 ms at 32, against measured 114.76 / 114.95 / 114.77 / 57.69. The
    step is not constant because time-on-air is quantised to whole symbol blocks, so six bytes
    buys one block at some payloads and two at others.
    """
    total = payload_bytes + header_bytes
    t_sym = (2 ** sf) / (bw_khz * 1000.0)
    # Low data rate optimisation is mandatory where a symbol exceeds 16 ms, which at 125 kHz is
    # SF11 and above. It costs throughput, so it changes the answer and must be modelled.
    low_rate_opt = 1 if t_sym > 0.016 else 0
    cr = coding_rate - 4

    numerator = 8 * total - 4 * sf + 28 + 16
    denominator = 4 * (sf - 2 * low_rate_opt)
    payload_symbols = 8 + max(math.ceil(numerator / denominator) * (cr + 4), 0)
    return ((preamble + 4.25) * t_sym + payload_symbols * t_sym) * 1000.0


def points():
    """Baseline plus every single-factor deviation from it."""
    out = [Point(NAME, "baseline", "", dict(BASELINE))]
    for factor, levels in FACTORS.items():
        for level in levels:
            if level == BASELINE[factor]:
                continue   # already covered by the baseline point
            params = dict(BASELINE)
            params[factor] = level
            out.append(Point(NAME, factor, level, params))
    out.extend(raw_stack_points(NAME, BASELINE))
    return out


def exchanges(point):
    return EXCHANGES


def _toa(point):
    p = point.params
    # The raw arm puts the payload on the air untouched, so it must not be charged for a header
    # it never sends - this feeds the duty-cycle budget the whole campaign is paced against.
    header = 0 if p.get("stack") == "raw" else HEADER_BYTES
    return time_on_air_ms(p["payload"], sf=p["sf"], bw_khz=float(p["bw"]),
                           coding_rate=p["cr"], preamble=p["preamble"], header_bytes=header)


def reply_wait_ms(point):
    """Must exceed the reply's own time-on-air, which at SF12/84 B is 4.8 s - a fixed 2 s timeout
    would score those points as total loss and the failure would look like a dead link."""
    return int(max(2000, 3 * _toa(point) + 1000))


def cfg_params(point, peer):
    p = dict(point.params)
    p["tech"] = NAME
    p["n"] = exchanges(point)
    p["replywait"] = reply_wait_ms(point)
    p["spacing"] = 100
    return p


def airtime_seconds(point):
    """Airtime one repetition of this point consumes on one board. Both boards transmit, but the
    duty-cycle budget is per-transmitter, so this is per-board."""
    return _toa(point) * exchanges(point) / 1000.0


def estimated_seconds(point):
    """Wall clock for one repetition, ignoring duty-cycle pacing (the scheduler adds that)."""
    return (2 * _toa(point) + 100 + 30) * exchanges(point) / 1000.0
