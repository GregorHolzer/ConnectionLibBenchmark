"""The four figures of a campaign. plot.py is the command line; this is the drawing.

    technologies.png   Zigbee, ESP-NOW and LoRa at their baseline, one panel per metric
    payloads.png       the three against payload size, one column per distance
    lora_factors.png   what each LoRa knob does: metric against the knob's levels
    latency_cdf.png    the whole latency distribution per technology, not just its median

Every panel answers one question and has one y axis. With a single distance (a desk run) the
technology panels are dot plots; with several, the same panels become lines against distance.
Colour identifies a technology and nothing else; distance, where it is a series, is one hue from
light to dark. Colours are the validated palette of the dataviz skill (CVD-safe as a set of
three); since one of them is below 3:1 against the surface, every series is also labelled.
"""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt   # noqa: E402
from matplotlib.ticker import FuncFormatter, NullFormatter   # noqa: E402

from . import airtime, configs   # noqa: E402

SURFACE, INK, INK_2, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7"
TECHS = ["zigbee", "espnow", "lora"]
TECH_COLOR = {"zigbee": "#2a78d6", "espnow": "#eb6834", "lora": "#1baf7a"}
TECH_LABEL = {"zigbee": "Zigbee", "espnow": "ESP-NOW", "lora": "LoRa SF9"}
DISTANCE_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281", "#0d366b"]   # near -> far

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "text.color": INK,
    "xtick.color": INK_2, "ytick.color": INK_2, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titlesize": 10,
    "axes.titleweight": "bold", "axes.titlelocation": "left", "font.size": 9,
    "lines.linewidth": 2, "lines.markersize": 7, "axes.axisbelow": True,
})


def _median(name, scale=1.0):
    return lambda row: None if row[name].get("median") is None else row[name]["median"] * scale


# (title, unit, value of a summary row, log axis, number format)
METRICS = [
    ("Delivered", "% of payloads acknowledged", lambda r: None if r["pdr"] is None else 100 * r["pdr"],
     False, "{:.0f} %"),
    ("Delivery latency", "ms, median", _median("latency_us", 1e-3), True, "{:.3g} ms"),
    ("Goodput", "bit/s, back-to-back burst", lambda r: r["goodput_bps"], True, "{:,.0f}"),
    ("Energy per delivered payload", "mJ, whole board",
     lambda r: r["energy"].get("energy_per_delivered_mj"), True, "{:.3g} mJ"),
    ("… of which above standing current", "mJ, the radio's share",
     lambda r: r["energy"].get("excess_energy_per_delivered_mj"), True, "{:.3g} mJ"),
]


def _scale(axis, which, log, percent=False):
    """Log axes with plain numbers (500, not 5 x 10^2); a percentage always runs 0..100."""
    setter = axis.set_xscale if which == "x" else axis.set_yscale
    target = axis.xaxis if which == "x" else axis.yaxis
    setter("log" if log else "linear")
    if log:
        target.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        target.set_minor_formatter(NullFormatter())
    if percent:
        (axis.set_xlim if which == "x" else axis.set_ylim)(-5, 112)


def _usable(value, log):
    return value is not None and (value > 0 or not log)


def _finish(figure, path, title):
    figure.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold")
    figure.tight_layout(rect=(0, 0, 1, 0.96))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def _distance_label(distance):
    return "desk" if distance == 0 else f"{distance:g} m"


def plot_technologies(rows, standing, path):
    baselines = [row for row in rows if row["name"] == "baseline"]
    distances = sorted({row["distance_m"] for row in baselines})
    figure, axes = plt.subplots(2, 3, figsize=(15, 7.5))
    for axis, (title, unit, value, log, fmt) in zip(axes.flat, METRICS):
        axis.set_title(title)
        axis.grid(True, axis="x" if len(distances) == 1 else "both")
        for index, tech in enumerate(TECHS):
            points = sorted((row["distance_m"], value(row)) for row in baselines
                            if row["tech"] == tech and _usable(value(row), log))
            if not points:
                if len(distances) == 1:   # say so, rather than leave a silent gap
                    axis.annotate("not measurable here", (0.03, -index), color=INK_2,
                                  xycoords=("axes fraction", "data"), va="center", fontsize=8)
                continue
            if len(distances) == 1:   # one dot per technology, labelled with its value
                axis.plot(points[0][1], -index, "o", color=TECH_COLOR[tech], markersize=10)
                axis.annotate(fmt.format(points[0][1]), (points[0][1], -index),
                              textcoords="offset points", xytext=(10, -3), color=INK)
            else:                     # a line against distance, labelled at its end
                xs, ys = zip(*points)
                axis.plot(xs, ys, "o-", color=TECH_COLOR[tech], label=TECH_LABEL[tech])
                axis.annotate(TECH_LABEL[tech], (xs[-1], ys[-1]), textcoords="offset points",
                              xytext=(6, 0), color=INK_2, va="center")
        if len(distances) == 1:
            axis.set_yticks([-i for i in range(len(TECHS))], [TECH_LABEL[t] for t in TECHS])
            axis.set_ylim(-len(TECHS) + 0.4, 0.6)
            axis.set_xlabel(unit)
            axis.margins(x=0.3)
            _scale(axis, "x", log, percent=title == "Delivered")
        else:
            axis.set_xlabel("distance to the gateway (m)")
            axis.set_ylabel(unit)
            _scale(axis, "y", log, percent=title == "Delivered")
            if min(distances) > 0:
                axis.set_xscale("log")
            axis.set_xticks(distances, [f"{d:g}" for d in distances])
            axis.minorticks_off()

    # Standing current is not a per-distance quantity: bars from zero, the board floor removed.
    axis = axes.flat[-1]
    axis.set_title("Standing current above the board floor")
    floor = standing.get("none")
    names = [t for t in TECHS if t in standing and floor is not None]
    values = [(standing[t] - floor) / 1000 for t in names]
    axis.barh([TECH_LABEL[t].replace(" SF9", "") for t in names], values, height=0.5,
              color=[TECH_COLOR[t] for t in names])
    for index, value in enumerate(values):
        axis.annotate(f"{value:+.1f} mA", (max(value, 0), index), textcoords="offset points",
                      xytext=(6, -3), color=INK)
    axis.invert_yaxis()
    axis.grid(True, axis="x")
    axis.margins(x=0.25)
    axis.set_xlabel("mA, radio able to receive, nothing sent" if names else "no baseline recorded")
    where = _distance_label(distances[0]) if len(distances) == 1 else "against distance"
    _finish(figure, path, f"Zigbee, ESP-NOW and LoRa at their baseline configuration, {where}")


def _predicted_latency_ms(params):
    """Airtime of every packet of the payload and of its ack: what LoRa latency cannot go below."""
    radio = {"sf": params["sf"], "bw_khz": params["bw"], "coding_rate": params["cr"],
             "preamble": params["preamble"]}
    return sum(airtime.time_on_air_ms(size, **radio) + airtime.time_on_air_ms(0, **radio)
               for size in configs.packet_sizes(configs.Config("lora", "", params)))


def plot_lora_factors(rows, path):
    """One column per LoRa knob, one row per metric. The x axis is the knob's levels."""
    lora = {(row["distance_m"], row["name"]): row for row in rows if row["tech"] == "lora"}
    distances = sorted({distance for distance, _ in lora})
    factors = list(configs.LORA_FACTORS)
    metrics = METRICS[:4]
    figure, axes = plt.subplots(len(metrics), len(factors), figsize=(3.1 * len(factors), 10),
                                sharey="row", squeeze=False)
    ramp = DISTANCE_RAMP[2:3] if len(distances) == 1 else DISTANCE_RAMP[-len(distances):]
    for column, factor in enumerate(factors):
        levels = configs.LORA_FACTORS[factor]
        names = ["baseline" if level == configs.LORA_BASELINE[factor] else f"{factor}={level}"
                 for level in levels]
        base_index = names.index("baseline")
        for line, (title, unit, value, log, _) in enumerate(metrics):
            axis = axes[line][column]
            for series, distance in enumerate(distances):
                points = [(i, value(lora[(distance, name)])) for i, name in enumerate(names)
                          if (distance, name) in lora and _usable(value(lora[(distance, name)]), log)]
                if points:
                    xs, ys = zip(*points)
                    axis.plot(xs, ys, "o-", color=ramp[series % len(ramp)],
                              label=_distance_label(distance))
            if title == "Delivery latency":   # the floor the airtime sets, for comparison
                floor = [_predicted_latency_ms(dict(configs.LORA_BASELINE, **{factor: level}))
                         for level in levels]
                axis.plot(range(len(levels)), floor, "--", color=INK_2, linewidth=1,
                          label="airtime of payload + ack")
            axis.axvline(base_index, color=AXIS, linewidth=6, alpha=0.35, zorder=0)
            _scale(axis, "y", log, percent=title == "Delivered")
            axis.set_xticks(range(len(levels)), [str(level) for level in levels])
            axis.grid(True, axis="y")
            if line == 0:
                axis.set_title({"sf": "Spreading factor", "bw": "Bandwidth (kHz)",
                                "cr": "Coding rate 4/x", "pwr": "TX power (dBm)",
                                "preamble": "Preamble (symbols)", "payload": "Payload (bytes)"}[factor])
            if column == 0:
                axis.set_ylabel(f"{title}\n{unit}")
    axes[1][0].legend(frameon=False, fontsize=8, loc="upper left")
    _finish(figure, path, "LoRa, one knob at a time. Grey band: the baseline "
                          "(SF9, 125 kHz, 4/7, 7 dBm, preamble 8, 32 B). Rows share their y axis.")


def plot_payloads(rows, path):
    """One row per metric, one column per distance; x is the payload size, one line per radio."""
    sweep = [row for row in rows if row["name"].split("=")[0] in ("baseline", "payload")]
    distances = sorted({row["distance_m"] for row in sweep})
    metrics, sizes = METRICS[:4], configs.PAYLOADS + configs.CAPABILITY_PAYLOADS
    figure, axes = plt.subplots(len(metrics), len(distances), sharey="row", squeeze=False,
                                figsize=(max(7, 4.5 * len(distances)), 11))
    for column, distance in enumerate(distances):
        for line, (title, unit, value, log, _) in enumerate(metrics):
            axis = axes[line][column]
            for tech in TECHS:
                points = sorted((row["params"].get("payload", 32), value(row)) for row in sweep
                                if row["tech"] == tech and row["distance_m"] == distance
                                and _usable(value(row), log))
                if points:
                    axis.plot(*zip(*points), "o-", color=TECH_COLOR[tech], label=TECH_LABEL[tech])
            # Shaded: the capability sizes, sent fragmented. Left of it is the comparison.
            axis.axvspan(configs.PAYLOADS[-1] * 1.5, configs.CAPABILITY_PAYLOADS[-1] * 1.5,
                         color=AXIS, alpha=0.2, zorder=0)
            _scale(axis, "x", True)
            _scale(axis, "y", log, percent=title == "Delivered")
            axis.set_xticks(sizes, [str(size) for size in sizes])
            axis.grid(True)
            if line == 0:
                axis.set_title(_distance_label(distance))
            if column == 0:
                axis.set_ylabel(f"{title}\n{unit}")
        axes[-1][column].set_xlabel("payload (bytes)")
    axes[0][0].legend(frameon=False, fontsize=8, loc="lower left")
    _finish(figure, path, "Payload size on every radio. Shaded: fragmented, a library capability.")


def plot_latency_cdf(rows, exchanges, path):
    """Empirical distribution of delivery latency for the three baselines, one panel per distance."""
    baseline_keys = {row["key"]: row["tech"] for row in rows if row["name"] == "baseline"}
    distances = sorted({row["distance_m"] for row in rows if row["name"] == "baseline"})
    figure, axes = plt.subplots(1, len(distances), figsize=(6.5 * len(distances), 4.6),
                                sharey=True, squeeze=False)
    for axis, distance in zip(axes[0], distances):
        for index, tech in enumerate(TECHS):
            values = sorted(int(e["latency_us"]) / 1000 for e in exchanges
                            if e["acked"] == "1" and baseline_keys.get(e["key"]) == tech
                            and float(e["distance_m"]) == float(distance))
            if not values:
                continue
            share = [100 * (i + 1) / len(values) for i in range(len(values))]
            axis.step(values, share, where="post", color=TECH_COLOR[tech])
            axis.annotate(f"{TECH_LABEL[tech]}\nmedian {values[len(values) // 2]:.3g} ms, "
                          f"max {values[-1]:.3g} ms", (values[0], 55 - 22 * index),
                          textcoords="offset points", xytext=(8 if index == 1 else -8, 0),
                          ha="left" if index == 1 else "right", color=INK_2, fontsize=8)
        _scale(axis, "x", True)
        axis.margins(x=0.25)
        axis.set_title(_distance_label(distance))
        axis.set_xlabel("delivery latency (ms): time inside send() until the ack")
        axis.grid(True)
    axes[0][0].set_ylabel("% of acknowledged payloads at or below")
    _finish(figure, path, "How delivery latency is distributed, baseline configurations")
