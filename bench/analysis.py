"""Statistics shared by the plotting and the audit.

Pure standard library: no numpy or scipy. Two reasons - the Pi's Python is EXTERNALLY-MANAGED so
every dependency is friction, and every statistic here is deliberately simple enough to be read
and checked by hand, which matters more for an audit than convenience.

Aggregation follows practice P11 (slide 19 of `02_experimentation.pdf`): a single number is always
a median, percentiles are nearest-rank rather than interpolated, and the raw samples stay available
so any other statistic can be recomputed without re-running the campaign.
"""

import math
import statistics
from collections import defaultdict


def percentile(values, pct):
    """Nearest-rank percentile.

    Deliberately not interpolated: interpolation invents a value the sample never contained, and
    the confidence machinery below is non-parametric order statistics, which only makes sense on
    values that were actually observed.
    """
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(math.ceil(pct / 100.0 * len(ordered))) - 1, len(ordered) - 1)
    return ordered[max(index, 0)]


def describe(values):
    """Median-centred summary. Never a bare mean - see practice P11."""
    if not values:
        return {}
    ordered = sorted(values)
    p25, p75 = percentile(ordered, 25), percentile(ordered, 75)
    out = {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "p25": p25,
        "p75": p75,
        "iqr": p75 - p25,
        "p95": percentile(ordered, 95),
        "p99": percentile(ordered, 99),
        "mean": statistics.fmean(ordered),   # reported alongside, never instead
    }
    out["stdev"] = statistics.stdev(ordered) if len(ordered) > 1 else 0.0
    out["cv"] = (out["stdev"] / out["mean"]) if out["mean"] else 0.0
    # Robust dispersion: IQR over median. Preferred over `cv` for judging noise, because these
    # latency distributions are strongly quantised - an ESP-NOW point can sit at exactly 2.42 ms
    # for three quarters of its exchanges (IQR = 0) while a handful of slow ones drag the standard
    # deviation up to a CV of 0.31. The classical CV then reports "noisy" about a measurement that
    # is in fact repeating the same value. Same reasoning as the deck's preference for medians.
    out["robust_cv"] = (out["iqr"] / out["median"]) if out["median"] else 0.0
    return out


def triscale_runs_needed(percentile_pct, confidence):
    """TriScale Eq. 4: N >= log(1-C)/log(1-p), the samples a percentile claim needs.

    `p` is the distance from the *nearer tail*, not the percentile itself - the estimate is an
    order statistic counted inward from whichever end is closer, so the 95th percentile costs
    exactly what the 5th does. Feeding the percentile in directly makes a 95th-percentile claim
    look like it needs a single sample, which is nonsense.

    Reproduces the paper's table: 50th at 95 % -> 5, 25th at 95 % -> 11, 5th and 95th -> 59.
    """
    if not 0 < percentile_pct < 100 or not 0 < confidence < 1:
        return None
    p = min(percentile_pct, 100 - percentile_pct) / 100.0
    return math.ceil(math.log(1 - confidence) / math.log(1 - p))


def median_confidence_interval(values, confidence=0.95):
    """Non-parametric CI for the median, from the binomial order statistics.

    Returns (low, high, achieved_confidence) or None when the sample is too small for any
    interval at the requested level - which is itself the useful answer.
    """
    n = len(values)
    if n < 6:
        return None
    ordered = sorted(values)
    # Largest k such that P(X < k) + P(X > n-k) <= 1 - confidence, X ~ Binomial(n, 0.5).
    best = None
    for k in range(1, n // 2 + 1):
        tail = sum(math.comb(n, i) for i in range(k)) / (2 ** n)
        covered = 1 - 2 * tail
        if covered >= confidence:
            best = (ordered[k - 1], ordered[n - k], covered)
        else:
            break
    return best


def linear_regression(xs, ys):
    """Ordinary least squares slope, plus Pearson r and an approximate two-sided p-value.

    Used to detect *systematic* drift across a campaign. Practice P2 is the reason this exists:
    a significant slope means the error is systematic, and slide 12 is explicit that repetition
    cannot mitigate that - so it must be reported rather than averaged away.
    """
    n = len(xs)
    if n < 3:
        return None
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    if sxx == 0 or syy == 0:
        return None
    slope = sxy / sxx
    r = sxy / math.sqrt(sxx * syy)
    r = max(-0.999999, min(0.999999, r))
    # t = r*sqrt(n-2)/sqrt(1-r^2); the normal approximation to its tail is adequate here, where
    # the question is "is there an obvious trend", not a precise p.
    t = abs(r) * math.sqrt((n - 2) / (1 - r * r))
    p = math.erfc(t / math.sqrt(2))
    return {"slope": slope, "r": r, "p": p, "n": n, "intercept": mean_y - slope * mean_x}


def gross_outliers(values, factor=3.0):
    """Indices of exchanges taking more than `factor` times the median.

    This is the external-load detector (practice P5), in preference to Tukey fences. Tukey assumes
    a roughly normal spread; these latency distributions are tightly peaked with a long tail, so a
    quarter of a perfectly healthy ESP-NOW run lands outside a 1.5*IQR fence purely because the
    IQR is a few tens of microseconds. That flags every run and therefore says nothing.

    "More than three times the typical exchange" is scale-free, survives quantised data, and
    matches what an external-load event actually looks like: occasional large excursions rather
    than a slightly wider distribution.
    """
    if len(values) < 4:
        return None
    median = statistics.median(values)
    if median <= 0:
        return None
    return [i for i, v in enumerate(values) if v > factor * median]


def tukey_outliers(values):
    """Indices outside the 1.5*IQR fences - the classic external-load signature (practice P5).

    Returns None when the test does not apply rather than a misleading answer. With a zero IQR -
    common here, where a quantised latency repeats the same value for most of a run - both fences
    collapse onto the median and *every* distinct value is labelled an outlier. A check that fires
    on every run carries no information, so the degenerate case is reported as such.
    """
    if len(values) < 4:
        return None
    p25, p75 = percentile(values, 25), percentile(values, 75)
    iqr = p75 - p25
    if iqr <= 0:
        return None
    low, high = p25 - 1.5 * iqr, p75 + 1.5 * iqr
    return [i for i, v in enumerate(values) if v < low or v > high]

def group_exchanges(rows, ok_only=True, field="rtt_us"):
    """Group raw CSV rows into {point_key: {rotation: [values]}}."""
    grouped = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if ok_only and row.get("ok") != "1":
            continue
        try:
            value = float(row[field])
        except (KeyError, ValueError):
            continue
        if ok_only and value <= 0:
            continue
        grouped[row["point_key"]][int(row["rotation"])].append(value)
    return grouped


def summarise_points(rows, points):
    """One summary record per (point, rotation), the unit both the plots and the audit work on."""
    by_point = group_exchanges(rows, field="rtt_us")
    send_by_point = group_exchanges(rows, field="send_us")
    meta = {p["point_key"]: p for p in points}

    out = []
    for point_key, rotations in sorted(by_point.items()):
        for rotation, values in sorted(rotations.items()):
            record = meta.get(point_key, {})
            entry = {
                "point_key": point_key,
                "rotation": rotation,
                "tech": point_key.split("/")[0],
                "factor": record.get("factor", point_key.split("/")[1] if "/" in point_key else ""),
                "level": record.get("level", ""),
                "params": record.get("params", {}),
                "rtt_us": describe(values),
                "send_us": describe(send_by_point.get(point_key, {}).get(rotation, [])),
            }
            out.append(entry)
    return out
