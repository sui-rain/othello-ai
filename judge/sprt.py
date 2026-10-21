"""Sequential probability ratio test on game pairs (pentanomial model).

Each opening is played twice with colours swapped; a pair's score for the new
engine is (game1 + game2) / 2 in {0, 0.25, 0.5, 0.75, 1}. The log-likelihood
ratio uses the normal approximation of the generalized SPRT (as in fishtest):

    LLR = N * (s1 - s0) * (2 * mean - s0 - s1) / (2 * var)

where s0, s1 are the expected scores at elo0 and elo1 (logistic Elo).
"""

import math

PAIR_SCORES = (0.0, 0.25, 0.5, 0.75, 1.0)


def elo_to_score(elo):
    return 1.0 / (1.0 + 10.0 ** (-elo / 400.0))


def score_to_elo(s):
    s = min(max(s, 1e-6), 1 - 1e-6)
    return -400.0 * math.log10(1.0 / s - 1.0)


def bounds(alpha, beta):
    """(lower, upper) LLR bounds."""
    return math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)


def stats(counts):
    """(pairs, mean, variance) of pair scores from pentanomial counts [n0..n4]."""
    n = sum(counts)
    if n == 0:
        return 0, 0.5, 0.0
    mean = sum(c * s for c, s in zip(counts, PAIR_SCORES)) / n
    var = sum(c * (s - mean) ** 2 for c, s in zip(counts, PAIR_SCORES)) / n
    return n, mean, var


def llr(counts, elo0, elo1):
    n, mean, var = stats(counts)
    if n == 0:
        return 0.0
    # Regularize: a tiny variance early on would make the LLR explode.
    var = max(var, 1e-3)
    s0, s1 = elo_to_score(elo0), elo_to_score(elo1)
    return n * (s1 - s0) * (2 * mean - s0 - s1) / (2 * var)


def decide(counts, elo0, elo1, alpha, beta, min_pairs=20):
    """'accept' (new is stronger), 'reject', or None (keep playing)."""
    if sum(counts) < min_pairs:
        return None
    lo, hi = bounds(alpha, beta)
    v = llr(counts, elo0, elo1)
    if v >= hi:
        return "accept"
    if v <= lo:
        return "reject"
    return None


def elo_estimate(counts):
    """Elo of the new engine and an approximate 95% interval, from pair scores."""
    n, mean, var = stats(counts)
    if n == 0:
        return 0.0, (0.0, 0.0)
    se = math.sqrt(var / n) if n > 1 else 0.5
    lo, hi = mean - 1.96 * se, mean + 1.96 * se
    return score_to_elo(mean), (score_to_elo(lo), score_to_elo(hi))
