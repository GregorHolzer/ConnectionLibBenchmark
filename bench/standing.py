"""The two measurements that need care before anything can be compared.

**The board floor.** What the ESP32 draws with every radio down. Subtracting it is what separates
the radio's own cost from the board's - and it is contaminated after the first rotation in
campaigns recorded before the `Tech::None` teardown was fixed, which is why the mode is a choice
rather than an assumption.

**The regulatory ceiling.** The duty-cycle column is not comparable across bands, so it is turned
into a ceiling on messages per hour, and technologies whose band carries no limit are reported as
uncapped rather than as a large number.
"""

import statistics
from collections import defaultdict

from . import technologies

# ETSI Annex 1 for the 433 MHz band - see docs/regulations.md. The cap applies per transmitter over
# a rolling hour, so it converts directly into a ceiling on messages per hour.
LORA_DUTY_CYCLE = 0.10

# Technologies whose band carries no duty-cycle limit. Their ceiling is set by airtime alone, which
# is a different kind of quantity, so they are reported as uncapped rather than as a large number.
UNCAPPED = ("espnow", "zigbee")


def board_floor_ua(points, mode="cold"):
    """Mean current of the board with every radio down, per rotation.

    `mode="cold"` returns the rotation-0 floor for every rotation. That is not a convenience: in a
    campaign recorded before `Tech::None` tears the radio down (src/main.cpp), only the first floor
    point is a genuine cold-boot measurement. Every later one is taken after some radio has been
    initialised and never deinitialised, and reads roughly 10 mA high - high enough that Zigbee's
    *active* current falls below the "all radios off" floor, which cannot be true.

    `mode="per-rotation"` uses each rotation's own floor. Correct once the firmware resets the
    radio state, wrong before it, so it is not the default.
    """
    floors = {p["rotation"]: p["energy"]["mean_ua"]
              for p in points
              if p["tech"] == "none" and p.get("energy")}
    if not floors or mode == "per-rotation":
        return floors
    cold = floors[min(floors)]
    return {rotation: cold for rotation in floors}


def standing_currents(points, floor):
    """{tech: {"absolute_ua", "above_floor_ua"}} from the per-technology idle points.

    Above-floor is deliberately *not* clamped at zero. `analysis.point_energy` clamps, which turns
    the negative values a contaminated floor produces into plausible-looking zeros and hides the
    very problem the reader needs to see.
    """
    per_tech = defaultdict(list)
    for point in points:
        if point["tech"] == "none" or not (point.get("summary") or {}).get("idle"):
            continue
        if not point.get("energy"):
            continue
        base = floor.get(point["rotation"])
        per_tech[point["tech"]].append((point["energy"]["mean_ua"], base))

    out = {}
    for tech, rows in per_tech.items():
        rows.sort()
        absolute = [value for value, _ in rows]
        above = [value - base for value, base in rows if base is not None]
        out[tech] = {
            "absolute_ua": statistics.median(absolute),
            "above_floor_ua": statistics.median(above) if above else None,
            # Every repetition kept, because the median hides the thing worth seeing: in a campaign
            # with the teardown bug, ESP-NOW's standing point reads 141 mA in rotation 0 and 152 mA
            # afterwards. A figure that plots only the median asserts a stability that is not there.
            "per_rotation_ua": absolute,
            "spread_ua": max(absolute) - min(absolute),
            "repetitions": len(rows),
        }
    return out


def _msgs_per_hour(tech, params):
    """Ceiling on offered load that the radio's regulatory band imposes.

    Not the `duty_pct` column. That column reports the guard's rolling-window usage, which is not
    comparable across bands - Zigbee reads a higher figure than LoRa purely because 2.4 GHz has no
    limit to pace against, so nothing throttles it. The comparable quantity is the ceiling itself:
    a technology that cannot legally transmit as often *is* worse for a periodic application, and
    the thesis asks for that to be reported as a result rather than corrected away.
    """
    if tech in UNCAPPED:
        return None                     # uncapped: annotated as such, never drawn as a finite bar
    module = technologies.get(tech)
    point = technologies.Point(tech, "baseline", "", dict(params))
    # airtime_seconds() charges a whole repetition; one message is that divided by the exchanges.
    per_message = module.airtime_seconds(point) / module.exchanges(point)
    if per_message <= 0:
        return None
    return LORA_DUTY_CYCLE * 3600.0 / per_message

