"""Turning a PPK2 current record into the energy figures the comparison reports.

bench/power.py drives the instrument; this derives meaning from what it captured. The distinction
that shapes every function here is absolute versus above-idle: the absolute figure answers "what
does the node draw", the above-idle figure answers "what did the traffic cost", and for a node
that must last months those lead to different decisions.
"""

def idle_baselines(points):
    """{(tech, rotation): mean_ua} from the idle points, plus ("none", rotation) for the floor.

    Every active energy figure is interpreted against these. Without the floor there is no way to
    separate what the radio costs from what the ESP32 costs, and without the per-technology idle
    there is no way to tell a radio that is expensive to transmit from one that is merely expensive
    to leave switched on - which, for a node that must last months, is the more important question.
    """
    out = {}
    for point in points:
        if point.get("factor") != "idle":
            continue
        energy = point.get("energy")
        if not energy:
            continue
        out[(point["tech"], point["rotation"])] = energy["mean_ua"]
    return out


def point_energy(point, baselines):
    """Energy figures for one measured point, absolute and above idle.

    Returns None when the point carries no energy record, so the whole pipeline degrades cleanly
    to the no-PPK2 case rather than inventing zeros.
    """
    energy = point.get("energy")
    summary = point.get("summary") or {}
    if not energy:
        return None

    delivered = summary.get("successes") or 0
    voltage_v = energy.get("voltage_mv", 0) / 1000.0
    out = {
        "mean_ua": energy["mean_ua"],
        "max_ua": energy["max_ua"],
        "duration_s": energy["duration_s"],
        "energy_mj": energy["energy_mj"],
        "charge_mc": energy["charge_mc"],
    }

    if delivered:
        out["energy_per_delivered_mj"] = energy["energy_mj"] / delivered

    # Above-idle: what this configuration costs *beyond* simply having the radio powered. The
    # absolute figure answers "what does the node draw"; this one answers "what did the traffic
    # cost", and they lead to different design decisions.
    idle_ua = baselines.get((point["tech"], point["rotation"]))
    if idle_ua is not None:
        excess_ua = max(energy["mean_ua"] - idle_ua, 0.0)
        out["idle_mean_ua"] = idle_ua
        out["excess_mean_ua"] = round(excess_ua, 2)
        excess_mj = excess_ua * energy["duration_s"] / 1000.0 * voltage_v
        out["excess_energy_mj"] = round(excess_mj, 4)
        if delivered:
            out["excess_energy_per_delivered_mj"] = round(excess_mj / delivered, 6)

    floor_ua = baselines.get(("none", point["rotation"]))
    if floor_ua is not None:
        out["board_floor_ua"] = floor_ua
        if idle_ua is not None:
            # What the radio costs to keep listening, with the ESP32's own draw removed.
            out["radio_standing_ua"] = round(max(idle_ua - floor_ua, 0.0), 2)

    return out
