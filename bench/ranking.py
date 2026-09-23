"""Whether the *ordering* of configurations survives repetition.

Separated from the summary statistics because it answers a different kind of question. Tight error
bars on each point mean nothing if the points reshuffle between repetitions - the ranking is
usually the thing being claimed, so it gets checked in its own right.
"""

import statistics

from .analysis import linear_regression

def rank_stability(per_repetition_medians):
    """Do configuration points keep their relative order across repetitions?

    `per_repetition_medians` maps rotation -> {point_key: median}. Returns the mean Spearman
    correlation over every pair of repetitions, or None if fewer than two are comparable.

    This is the check that matters most for whether a *conclusion* survives. Tight error bars on
    each point mean nothing if the ordering between points reshuffles from one repetition to the
    next - the ranking is usually the thing being claimed.
    """
    rotations = sorted(per_repetition_medians)
    if len(rotations) < 2:
        return None

    correlations = []
    for i in range(len(rotations)):
        for j in range(i + 1, len(rotations)):
            a, b = per_repetition_medians[rotations[i]], per_repetition_medians[rotations[j]]
            shared = sorted(set(a) & set(b))
            if len(shared) < 3:
                continue
            rank_a = _ranks([a[k] for k in shared])
            rank_b = _ranks([b[k] for k in shared])
            fit = linear_regression(rank_a, rank_b)
            if fit:
                correlations.append(fit["r"])
    if not correlations:
        return None
    return {"mean_spearman": statistics.fmean(correlations), "pairs": len(correlations),
            "min_spearman": min(correlations)}


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for rank, index in enumerate(order):
        ranks[index] = float(rank)
    return ranks
