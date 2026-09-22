"""Pass B, second half: do the data support the claims the campaign wants to make?

Where stability.py asks whether the measurements hold still, these checks ask what may be said
about them: whether excursions betray external load, whether the *ordering* of configurations
survives repetition, whether there are enough samples for a tail claim, and whether the energy
numbers carry the baseline that makes them interpretable at all.
"""

import statistics
from collections import defaultdict

from ..analysis import (gross_outliers, median_confidence_interval, percentile,
                        triscale_runs_needed)
from ..energy import idle_baselines
from ..ranking import rank_stability
from .report import FAIL, MAX_OUTLIER_FRACTION, MIN_RANK_STABILITY, PASS, SKIP, WARN


def check_outliers(report, section, grouped):
    """P5: the classic external-load signature."""
    flagged, examined, degenerate = [], 0, 0
    for point_key, rotations in grouped.items():
        for rotation, values in rotations.items():
            # Tukey fences on a handful of samples flag ordinary scatter: with n=10 a single
            # slow exchange is already 10 %. Only judge runs long enough for the quartiles to
            # mean something.
            if len(values) < 20:
                continue
            outliers = gross_outliers(values)
            if outliers is None:
                # Median non-positive or too few samples: the rule does not apply.
                degenerate += 1
                continue
            examined += 1
            fraction = len(outliers) / len(values)
            if fraction > MAX_OUTLIER_FRACTION:
                flagged.append(f"{point_key} rot={rotation}: {fraction * 100:.0f}% outliers "
                                f"of {len(values)} exchanges")
    degenerate_note = (f"\n{degenerate} run(s) could not be tested") if degenerate else ""
    if not examined:
        report.add(section, "P5", "Outliers", SKIP,
                   "no run has the 20+ exchanges this test needs" + degenerate_note)
    elif flagged:
        report.add(section, "P5", "Outliers", WARN,
                   f"{len(flagged)}/{examined} run(s) above "
                   f"{MAX_OUTLIER_FRACTION * 100:.0f}% of exchanges taking >3x the median — the "
                   f"signature of external load\n" + "\n".join(flagged[:5]) + degenerate_note)
    else:
        report.add(section, "P5", "Outliers", PASS,
                   f"all {examined} runs keep excursions beyond 3x the median below "
                   f"{MAX_OUTLIER_FRACTION * 100:.0f}% of exchanges" + degenerate_note)


def check_rank_stability(report, section, grouped):
    """Whether the *conclusions* survive repetition."""
    per_tech = defaultdict(lambda: defaultdict(dict))
    for point_key, rotations in grouped.items():
        tech = point_key.split("/")[0]
        for rotation, values in rotations.items():
            per_tech[tech][rotation][point_key] = statistics.median(values)
    unstable_ranks, stable_ranks = [], []
    for tech, by_rotation in sorted(per_tech.items()):
        result = rank_stability(by_rotation)
        if result is None:
            continue
        if result["mean_spearman"] < MIN_RANK_STABILITY:
            unstable_ranks.append(f"{tech}: mean Spearman {result['mean_spearman']:.2f} "
                                   f"(worst pair {result['min_spearman']:.2f})")
        else:
            stable_ranks.append(f"{tech}: {result['mean_spearman']:.2f}")
    if not stable_ranks and not unstable_ranks:
        report.add(section, "", "Rank stability", SKIP, "needs 2+ repetitions and 3+ points")
    elif unstable_ranks:
        report.add(section, "", "Rank stability", FAIL,
                   "configuration points reorder between repetitions, so any ranking claim is "
                   "not reproducible regardless of how tight the error bars look\n" +
                   "\n".join(unstable_ranks))
    else:
        report.add(section, "", "Rank stability", PASS,
                   "ordering of configurations holds across repetitions — "
                   + ", ".join(stable_ranks))


def check_sample_size(report, section, grouped):
    """P10: does N support the percentile being claimed?"""
    needed_95 = triscale_runs_needed(95, 0.95)
    thin_samples = []
    for point_key, rotations in grouped.items():
        pooled = [v for values in rotations.values() for v in values]
        if len(pooled) < needed_95:
            thin_samples.append(f"{point_key}: n={len(pooled)}")
    if thin_samples:
        report.add(section, "P10", "Sample size vs. claims", WARN,
                   f"{len(thin_samples)}/{len(grouped)} points have fewer than {needed_95} "
                   f"samples, which TriScale Eq. 4 requires for a 95th-percentile claim at 95% "
                   f"confidence. Medians remain sound; tail claims do not.\n" +
                   "\n".join(thin_samples[:5]))
    else:
        report.add(section, "P10", "Sample size vs. claims", PASS,
                   f"every point has ≥{needed_95} samples — enough for a 95th-percentile claim "
                   f"at 95% confidence")


def check_ci_width(report, section, grouped):
    """P11: median confidence intervals, as a summary rather than a gate."""
    widths = []
    for point_key, rotations in grouped.items():
        pooled = [v for values in rotations.values() for v in values]
        interval = median_confidence_interval(pooled)
        if interval:
            low, high, _ = interval
            centre = statistics.median(pooled)
            if centre:
                widths.append((high - low) / centre)
    if widths:
        report.add(section, "P11", "Median CI width", PASS if percentile(widths, 90) < 0.5 else WARN,
                   f"95% CI on the median spans {statistics.median(widths) * 100:.1f}% of the "
                   f"median (typical), {percentile(widths, 90) * 100:.1f}% at the 90th percentile "
                   f"of points")


def check_energy_baselines(report, section, points):
    """Energy figures must come with the baseline that makes them interpretable."""
    energy_points = [p for p in points if p.get("energy") and p.get("factor") != "idle"]
    if not energy_points:
        report.add(section, "", "Energy baselines", SKIP,
                   "no energy measurements in this campaign (no PPK2 attached)")
        return
    baselines = idle_baselines(points)
    orphans = [p["point_key"] for p in energy_points
               if (p["tech"], p["rotation"]) not in baselines]
    floors = [r for (t, r) in baselines if t == "none"]
    if orphans:
        report.add(section, "", "Energy baselines", FAIL,
                   f"{len(orphans)}/{len(energy_points)} energy figures have no idle "
                   f"measurement from the same repetition, e.g. {orphans[0]}. An energy "
                   f"number without its baseline cannot be separated into what the radio "
                   f"costs to run and what the traffic costs.")
    elif not floors:
        report.add(section, "", "Energy baselines", WARN,
                   "per-technology idle present but no board floor (tech=none) — the ESP32's "
                   "own draw cannot be separated from the radio's")
    else:
        report.add(section, "", "Energy baselines", PASS,
                   f"every one of {len(energy_points)} energy figures has a matching "
                   f"per-technology idle and a board floor from the same repetition")


def check_duty_cycle(report, section, meta):
    """P7: duty-cycle compliance, from measured airtime rather than intent."""
    airtime = meta.get("lora_airtime_total_s")
    elapsed = meta.get("elapsed_s")
    if airtime and elapsed:
        realised = airtime / elapsed
        verdict = PASS if realised <= 0.10 else FAIL
        report.add(section, "P7", "LoRa duty cycle honoured", verdict,
                   f"{airtime:.0f}s airtime over {elapsed / 3600:.1f}h = "
                   f"{realised * 100:.2f}% mean duty cycle (limit 10%); "
                   f"{meta.get('duty_wait_total_s', 0):.0f}s spent waiting for budget")
    else:
        report.add(section, "P7", "LoRa duty cycle honoured", SKIP, "no airtime recorded")


def run(report, section, grouped, meta, points):
    check_outliers(report, section, grouped)
    check_rank_stability(report, section, grouped)
    check_sample_size(report, section, grouped)
    check_ci_width(report, section, grouped)
    check_energy_baselines(report, section, points)
    check_duty_cycle(report, section, meta)
