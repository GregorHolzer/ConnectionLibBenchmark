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
    return {
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
