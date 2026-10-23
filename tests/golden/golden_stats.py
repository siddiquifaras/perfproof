"""Rates, bounds and paired tests for the golden-set report.

Pure Python and NumPy: the exact binomial sums are short (n in the
hundreds) and avoid an untyped dependency.
"""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

BAR: float = 1.03
"""The bar, ``1 + min_effect``."""
LIMIT: float = 0.025
"""The wrong-KEEP limit on the one-sided 95% upper bound."""


def binom_cdf(k: int, n: int, p: float) -> float:
    """Return ``P(X <= k)`` for ``X ~ Binomial(n, p)``, exactly.

    Parameters
    ----------
    k : int
        Count, may be outside 0 to ``n``.
    n : int
        Trials, non-negative.
    p : float
        Success probability in [0, 1].

    Returns
    -------
    float
        The probability; O(n).
    """
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    total = 0.0
    for i in range(k + 1):
        log_term = (
            math.lgamma(n + 1)
            - math.lgamma(i + 1)
            - math.lgamma(n - i + 1)
            + i * log_p
            + (n - i) * log_q
        )
        total += math.exp(log_term)
    return min(total, 1.0)


def cp_upper(errors: int, n: int, confidence: float = 0.95) -> float:
    """One-sided Clopper-Pearson upper bound on a rate.

    The ``p`` at which ``P(X <= errors | p)`` equals ``1 - confidence``,
    found by bisection to 1e-12.

    Parameters
    ----------
    errors : int
        Observed count, 0 to ``n``.
    n : int
        Trials, positive.
    confidence : float, default 0.95
        One-sided confidence.

    Returns
    -------
    float
        The bound; 1 when ``errors == n``.

    Raises
    ------
    ValueError
        If the counts are out of range.
    """
    if n <= 0 or not 0 <= errors <= n:
        msg = f"need 0 <= errors <= n and n > 0, got {errors}, {n}"
        raise ValueError(msg)
    if errors == n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if binom_cdf(errors, n, mid) > 1 - confidence:
            lo = mid
        else:
            hi = mid
    return hi


@dataclass(frozen=True, slots=True)
class Mcnemar:
    """Exact McNemar test on two methods' KEEP decisions.

    Attributes
    ----------
    only_a, only_b : int
        Comparisons KEEP by one method only (the discordant pairs).
    p : float
        Two-sided exact binomial p-value on the discordant pairs
        (twice the smaller tail, at most 1); 1 when there are none.
    """

    only_a: int
    only_b: int
    p: float


def mcnemar(a: NDArray[np.bool_], b: NDArray[np.bool_]) -> Mcnemar:
    """Compare two boolean decision vectors on the same comparisons.

    Parameters
    ----------
    a, b : numpy.ndarray of bool, shape (R,)
        Whether method A and method B decided KEEP, per comparison.

    Returns
    -------
    Mcnemar
        Discordant counts and the exact two-sided p-value.

    Raises
    ------
    ValueError
        If the shapes differ.
    """
    if a.shape != b.shape:
        msg = f"shapes differ: {a.shape} and {b.shape}"
        raise ValueError(msg)
    only_a = int(np.sum(a & ~b))
    only_b = int(np.sum(b & ~a))
    total = only_a + only_b
    if total == 0:
        return Mcnemar(0, 0, 1.0)
    tail = binom_cdf(min(only_a, only_b), total, 0.5)
    return Mcnemar(only_a, only_b, min(1.0, 2 * tail))
