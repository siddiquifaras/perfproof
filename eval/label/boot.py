"""Moving-block bootstrap of the mean log ratio, written here.

This is the labeller's own interval, independent of perfproof's
statistics: perfproof uses a t interval on the mean log ratio, this is
a resampling interval with its own code. The rule it serves is in
``eval/label/README.md``, "Analysis of one machine's result".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

RESAMPLES = 10_000
LEVEL = 0.95
# Index arrays are drawn in chunks of at most this many bytes, so
# memory stays bounded whatever ``n`` and ``resamples`` are.
CHUNK_BYTES = 64 * 1024 * 1024

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Interval:
    """A bootstrap interval for the geometric-mean speedup.

    Attributes
    ----------
    estimate : float
        ``exp(mean(r))``, the geometric-mean ratio, dimensionless.
    lower, upper : float
        The interval's bounds, dimensionless.
    n : int
        Number of ratios.
    block : int
        Block length used.
    resamples : int
        Number of bootstrap resamples.
    seed : int
        Seed of ``numpy.random.default_rng``.
    """

    estimate: float
    lower: float
    upper: float
    n: int
    block: int
    resamples: int
    seed: int


def block_length(n: int) -> int:
    """Return the block length for ``n`` ratios, ``ceil(n ** (1/3))``.

    Parameters
    ----------
    n : int
        Number of ratios, at least 1.

    Returns
    -------
    int
        The block length, between 1 and ``n``.

    Raises
    ------
    ValueError
        If ``n < 1``.
    """
    if n < 1:
        msg = "need at least one ratio"
        raise ValueError(msg)
    # Integer arithmetic: a float cube root such as 64 ** (1/3) =
    # 3.9999999999999996 would make ceil exact cubes wrong.
    root = 1
    while root**3 < n:
        root += 1
    return min(root, n)


def resampled_means(
    r: FloatArray, block: int, resamples: int, seed: int
) -> FloatArray:
    """Draw moving-block bootstrap means of ``r``.

    Each resample is ``ceil(n / block)`` blocks of ``block``
    consecutive values, with starts uniform on ``0 .. n - block``,
    concatenated and cut to ``n`` values. Cost is
    ``O(resamples * n)`` time; memory is bounded by `CHUNK_BYTES`.

    Parameters
    ----------
    r : ndarray of float64, shape (n,)
        The log ratios.
    block : int
        Block length, ``1 <= block <= n``.
    resamples : int
        Number of resamples.
    seed : int
        Seed for ``numpy.random.default_rng``.

    Returns
    -------
    ndarray of float64, shape (resamples,)
        The mean of each resample.
    """
    n = r.shape[0]
    blocks = math.ceil(n / block)
    rng = np.random.default_rng(seed)
    offsets = np.arange(block, dtype=np.int64)
    per_row = blocks * block * np.dtype(np.int64).itemsize
    rows = max(1, CHUNK_BYTES // per_row)
    out = np.empty(resamples, dtype=np.float64)
    for start in range(0, resamples, rows):
        stop = min(start + rows, resamples)
        starts = rng.integers(
            0, n - block + 1, size=(stop - start, blocks), dtype=np.int64
        )
        idx = (starts[:, :, None] + offsets).reshape(stop - start, -1)[:, :n]
        out[start:stop] = r[idx].mean(axis=1)
    return out


def interval(
    a: FloatArray,
    b: FloatArray,
    seed: int,
    resamples: int = RESAMPLES,
    level: float = LEVEL,
) -> Interval:
    """Bootstrap interval for the geometric-mean speedup of B over A.

    Parameters
    ----------
    a, b : ndarray of float64, shape (n,)
        Paired measurements of arm A and arm B (seconds, or counts),
        all positive.
    seed : int
        Seed for the resampling.
    resamples : int
        Number of resamples.
    level : float
        Two-sided coverage, in (0, 1).

    Returns
    -------
    Interval
        ``estimate = exp(mean(ln(a / b)))`` and the percentile bounds.
        When every ratio ``a / b`` is the same float the bounds and the
        estimate are that ratio exactly.

    Raises
    ------
    ValueError
        If the arrays differ in shape, are empty, or hold a value that
        is not positive and finite.
    """
    if a.shape != b.shape or a.ndim != 1 or a.shape[0] == 0:
        msg = "a and b must be non-empty 1-d arrays of the same length"
        raise ValueError(msg)
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        msg = "measurements must be finite"
        raise ValueError(msg)
    if np.any(a <= 0) or np.any(b <= 0):
        msg = "measurements must be positive"
        raise ValueError(msg)
    n = a.shape[0]
    block = block_length(n)
    ratios = a / b
    if np.all(ratios == ratios[0]):
        same = float(ratios[0])
        return Interval(same, same, same, n, block, resamples, seed)
    r = np.log(ratios)
    means = resampled_means(r, block, resamples, seed)
    tail = (1 - level) / 2
    lo, hi = np.quantile(means, [tail, 1 - tail])
    return Interval(
        estimate=math.exp(float(r.mean())),
        lower=math.exp(float(lo)),
        upper=math.exp(float(hi)),
        n=n,
        block=block,
        resamples=resamples,
        seed=seed,
    )


def geometric_mean_ratio(a: FloatArray, b: FloatArray) -> float:
    """Return ``exp(mean(ln(a / b)))``.

    Parameters
    ----------
    a, b : ndarray of float64, shape (n,)
        Paired positive measurements, ``n >= 1``.

    Returns
    -------
    float
        The geometric-mean ratio, dimensionless.
    """
    return math.exp(float(np.log(a / b).mean()))
