"""Statistics behind every reported number.

Pure standard library: no numpy or scipy. Every statistic here is deliberately simple enough to
be read and checked by hand.

Aggregation follows practice P11 (slide 19 of `02_experimentation.pdf`): a single number is always
a median, percentiles are nearest-rank rather than interpolated, and the raw samples stay available
so any other statistic can be recomputed without re-running the campaign.
"""

import math
import statistics


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
