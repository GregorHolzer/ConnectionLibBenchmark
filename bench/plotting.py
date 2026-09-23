"""The visual system every figure in the suite is drawn in: palette, axis styling, bar helpers.

Lifted out of `plot_results.py`, which the other two plotting scripts used to import *from* - a
script importing a script. The rules here are binding on every figure, so they live in one place
where they cannot drift apart:

* A technology's colour follows its identity, never its rank, so omitting one from a figure never
  repaints another.
* Linear axes start at zero; log axes start at a decade. Matplotlib's autoscaling would happily
  start a bar axis at 95 mA and turn a 0.3 % difference into a visual landslide.
* Everything is written as PNG *and* vector PDF, light mode only. These are print figures for the
  thesis, not a web page.
"""

import math
import textwrap

import matplotlib

matplotlib.use("Agg")   # headless: the Pi has no display
import matplotlib.pyplot as plt

# Slots 1-3 of the validated reference categorical palette; the documented set that clears the
# all-pairs CVD and normal-vision floors on this surface (worst CVD ΔE 9.2, normal-vision 24.0).
# Colour follows the technology's identity, never its rank, so omitting one never repaints another.
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e3e3df"

TECH_ORDER = ["espnow", "lora", "zigbee"]
COLOR = {"espnow": "#2a78d6", "lora": "#eb6834", "zigbee": "#1baf7a"}
MARKER = {"espnow": "o", "lora": "s", "zigbee": "^"}
LABEL = {"espnow": "ESP-NOW", "lora": "LoRa", "zigbee": "Zigbee"}

FACTOR_LABEL = {
    "sf": "spreading factor", "bw": "bandwidth (kHz)", "cr": "coding rate (4/N)",
    "pwr": "transmit power (dBm)", "preamble": "preamble symbols", "payload": "payload (bytes)",
    "channel": "WiFi channel", "encrypt": "encryption", "timeout": "send timeout (ms)",
    "broadcast": "broadcast addressing",
}


def wrap(text, width):
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def style_axes(ax, xlabel, ylabel, title, subtitle=None):
    ax.set_facecolor(SURFACE)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=3, color=GRID)
    ax.set_xlabel(xlabel, color=INK_2, fontsize=10)
    ax.set_ylabel(ylabel, color=INK_2, fontsize=10)
    sub_lines = wrap(subtitle, 84) if subtitle else []
    if title:
        ax.set_title(title, color=INK, fontsize=12, loc="left", pad=10 + 12 * len(sub_lines))
    if sub_lines:
        ax.text(0.0, 1.015, "\n".join(sub_lines), transform=ax.transAxes, color=INK_2,
                 fontsize=8.5, va="bottom", ha="left", linespacing=1.4)


def lighten(hex_color, t):
    src = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    dst = [int(SURFACE[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (s - c) * t):02x}" for c, s in zip(src, dst))


def save(fig, out_dir, name):
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(out_dir / f"{name}.{ext}", dpi=200, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    print(f"  {name}.png / .pdf")


def overlay_medians(ax, data, positions, color):
    """Always-visible median marker per group.

    A violin and a box both encode spread as *size*, so a near-deterministic distribution -
    LoRa's time-on-air is repeatable to well under a millisecond - collapses to a sub-pixel
    sliver and the figure reads as empty. The marker is drawn in axis-independent point units,
    so it survives a log axis spanning two decades while still sitting inside the box wherever
    there is genuine spread to show.
    """
    for position, values in zip(positions, data):
        if not values:
            continue
        ordered = sorted(values)
        median = ordered[len(ordered) // 2]
        ax.plot([position], [median], marker="o", markersize=5, markerfacecolor=color,
                 markeredgecolor=SURFACE, markeredgewidth=1.1, linestyle="none", zorder=6)

def ms_formatter(value, _):
    return f"{value:g}"


def apply_floor(ax, values, log=False, headroom=1.30):
    """Force the axis to start at zero, or at a decade when the data spans orders of magnitude.

    One helper for every panel so the rule cannot drift between them. Matplotlib's autoscaling
    would happily start a bar axis at 95 mA and turn a 0.3 % difference into a visual landslide.
    """
    finite = [v for v in values if v is not None and math.isfinite(v)]
    if not finite:
        return
    if log:
        positive = [v for v in finite if v > 0]
        if not positive:
            return
        ax.set_yscale("log")
        low = 10 ** math.floor(math.log10(min(positive)))
        high = 10 ** math.ceil(math.log10(max(positive)))
        ax.set_ylim(low, high)
    else:
        ax.set_ylim(0, max(finite) * headroom)


def bars(ax, techs, values, *, log=False, fmt="{:.1f}", unit="", headroom=1.30):
    """One bar per technology with its value printed inside it.

    Inside, not above: two technologies landing within a percent of each other - which happens on
    delivery ratio and on framing efficiency - put their labels on top of one another otherwise.
    """
    drawn = [(i, t, v) for i, (t, v) in enumerate(zip(techs, values))
             if v is not None and math.isfinite(v)]
    for i, tech, value in drawn:
        ax.bar(i, value, color=COLOR[tech], width=0.58, zorder=3)
    apply_floor(ax, [v for _, _, v in drawn], log=log, headroom=headroom)

    for i, tech, value in drawn:
        ax.text(i, value, fmt.format(value) + unit, ha="center", va="top", color=SURFACE,
                 fontsize=8.5, fontweight="bold", zorder=6,
                 bbox=dict(boxstyle="round,pad=0.22", facecolor=COLOR[tech], edgecolor="none"))
    # A technology with no value for this metric is named and left blank rather than dropped, so
    # the reader sees that the metric does not exist for it instead of silently missing a bar.
    for i, (tech, value) in enumerate(zip(techs, values)):
        if value is None or not math.isfinite(value):
            ax.text(i, 0, "  n/a  ", ha="center", va="bottom", color=INK_2, fontsize=8.5,
                     style="italic")
    ax.set_xticks(range(len(techs)))
    ax.set_xticklabels([LABEL[t] for t in techs], fontsize=9)


def panel_note(ax, text, width=52):
    """A caveat pinned under the panel, for metrics that mislead without one.

    Wrapped here rather than by hand-placed newlines in the caller: a note written to look right
    at one figure width runs into the neighbouring panel at any other, and these panels sit four
    to a row with very little gutter between them.
    """
    ax.text(0.0, -0.30, textwrap.fill(" ".join(text.split()), width), transform=ax.transAxes,
             color=INK_2, fontsize=7.4, va="top", ha="left", linespacing=1.35, style="italic")
