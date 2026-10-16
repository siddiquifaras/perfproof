"""Unpaired bootstrap intervals for a ratio of medians.

A challenger in ``docs/method.md`` ("Speedup and interval"): the
procedure the hand-run vLLM study used. Baseline and candidate are
resampled independently, so the rounds' pairing is ignored, and the
estimand is the ratio of medians rather than the geometric-mean
speedup. It runs in calibration for comparison with the primary
procedure.

Orientation
-----------
Every estimate and bound is on the ratio scale and oriented so that
above 1 means the candidate is faster: ``median(candidate) /
median(baseline)`` when higher is better, ``median(baseline) /
median(candidate)`` when lower is better. Internally every replicate
is computed as ``median(candidate) / median(baseline)`` whatever the
direction, so both directions select the same replicates and a
direction flip inverts the interval up to the rounding of one
reciprocal and the widening below.

Resampling
----------
For each batch row in turn, the ``B`` resamples are one integer draw
of shape ``(B, n_b + n_c)`` from the caller's generator: column ``i``
is uniform on the baseline indices for ``i < n_b`` and on the
candidate indices otherwise. Large draws are split along ``B`` into
chunks that fit ``max_bytes``. NumPy's bounded integer draws give the
same values whether drawn at once or in consecutive pieces, so the
result does not depend on ``max_bytes`` (a test checks this).

Intervals
---------
Both intervals are order statistics of the ``B`` replicate ratios, with
no interpolation, so every bound is one of the replicates. For a tail
mass ``t``, the lower bound is the ``j``-th smallest replicate and the
upper bound the ``j``-th largest, ``j = floor((B + 1) t)``. A ``j``
outside ``[1, B]`` names an order statistic that ``B`` resamples do
not have; it is never clipped to the extreme replicate, which would
give a bound narrower than the procedure's own. Replicates stay on
the ratio scale rather than as log ratios: order statistics are
unchanged by the monotone ``log``, and returning a replicate avoids an
``exp`` that would move the bound.

- Percentile: ``t = (1 - confidence) / 2`` in both tails, with ``j``
  computed in exact rational arithmetic from the float64 confidence;
  ``resamples`` too small for ``j >= 1`` raises.
- BCa (Efron and Tibshirani 1993, sections 14.3 and 15.4): the bias
  correction ``z0`` is ``ndtri`` of the mid-rank share of replicates
  below the estimate, ``(#{r* < r} + #{r* <= r}) / (2 B)``, which
  handles the ties a median produces. The acceleration ``a`` is the
  two-sample jackknife of ``log(r_(i) / r)``, leaving out one
  observation of one arm at a time, as in `scipy.stats.bootstrap`.
  With ``z = ndtri((1 - confidence) / 2)``, the lower tail mass is
  ``ndtr(z0 + (z0 + z) / (1 - a (z0 + z)))`` and the upper one
  ``ndtr(-(z0 + (z0 - z) / (1 - a (z0 - z))))``. While ``1 - a w >
  0``, the lower mass increases in ``z0`` and in ``a`` and the upper
  mass decreases in both, which "Numerics" below uses to widen the
  BCa bounds.

The BCa bounds are ``NaN`` where the procedure is undefined: the
jackknife has no spread in either arm (``a`` is ``0 / 0``), every
replicate lies on one side of the estimate (``z0`` is infinite), an
adjusted level needs an order statistic outside ``[1, B]`` (``B`` is
too small for it; more resamples can resolve it), or the adjusted
levels put the lower bound above the upper one. Both bounds are
``NaN`` when either is. An undefined interval stays unknown rather
than falling back to another procedure.

Numerics
--------
As "Numerics" in ``docs/method.md`` requires, no bound comes out more
favourable to the candidate, through rounding, than exact arithmetic
on the float64 measurements and their float64 ratio would give for
the same procedure on the same resamples. For BCa this rests on one
stated assumption about library functions, below.

- When every value of each arm in a row is the same float, every
  replicate is bit-identical to the estimate: a median of equal
  values is computed exactly, and each replicate repeats the one
  division the estimate makes. The estimate and both bounds of both
  intervals are then that ratio, with no further arithmetic.
- Otherwise each selected bound is widened outward by a factor
  derived from the operations that produced it. With unit roundoff
  ``u = 2**-53`` and every intermediate in the float64 normal range
  (guaranteed by the input range below), each correctly rounded
  operation contributes a factor ``1 + delta`` with ``|delta| <= u``.
  A median is exact for odd ``n`` and one rounding, ``(x + y) * 0.5``,
  for even ``n``. A replicate ratio is one more rounding, and the
  lower-is-better reciprocal one more, so each computed replicate is
  ``x (1 + theta)`` with ``|theta| <= gamma_4 = 4u / (1 - 4u)``
  (Higham 2002, lemma 3.1), where ``x`` is its exact value. If every
  value moves by at most that relative amount, the ``j``-th order
  statistic does too. The widening product rounds once more. The
  lower bound ``fl(y (1 - 5u))`` then satisfies ``fl(y (1 - 5u)) <=
  x (1 + gamma_5)(1 - 5u) <= x``, because ``(1 + gamma_5)(1 - 5u) =
  (1 - 5u) / (1 - 5u) = 1``. The upper bound ``fl(y (1 + 6u)) >= x
  (1 - gamma_5)(1 + 6u) >= x`` because ``6u >= 5u / (1 - 10u)``.
  ``1 - 5u`` and ``1 + 6u`` are exact float64 values; ``1 + 5u`` is
  not, which is why the upper factor uses ``6u``.
- Which replicate BCa selects depends on ``z0`` and ``a``, and both
  can be far off in floats: on data whose values differ by a few ulps,
  rounded replicates compared with a rounded estimate move the counts
  behind ``z0`` by hundreds of ranks, and the jackknife deviations
  behind ``a`` are themselves rounding noise. So both are computed as
  intervals that contain their exact values, and each bound is taken
  at its outward corner, the one that makes the interval widest. A
  lower bound pushed down guards against a false KEEP, and an upper
  bound pushed up against a false REJECT:

  - ``z0``: a computed replicate ``y`` and the computed estimate
    ``r~`` are each within ``gamma_3`` of their exact values, so the
    exact replicate is certainly below the exact estimate when ``y <
    fl(r~ (1 - 8u)) <= r~ (1 - 7u) <= r~ (1 - gamma_3) / (1 +
    gamma_3)``, and certainly above it when ``y > fl(r~ (1 + 8u))``.
    A replicate whose two medians equal the estimate's exactly (checked
    on each rounded median and its exact TwoSum error) is an exact tie
    and counts 1. Other replicates in between are counted once as
    below and once as above, which brackets the exact mid-rank count
    between ``2 #below + #ties`` and ``2 (B - #above) - #ties``. A
    bracket end of 0 or ``2 B`` makes ``z0`` possibly infinite, so the
    bounds are ``NaN``.
  - ``a``: the jackknife values are computed as ``log(m_b / m_b(i))``
    and ``log(m_c(i) / m_c)``, equal to ``log(r_(i) / r)`` in exact
    arithmetic, each within ``4u + 2 eps |theta|``. That error is
    carried through the mean, the deviations and the sums of squares
    and cubes as an absolute bound, giving intervals for the numerator
    and denominator of ``a``, then an interval for ``a``, intersected
    with ``[-1/6, 1/6]``. Any exact ``a`` lies in that range, because
    ``|sum U^3| <= (sum U^2)^(3/2)`` per arm and ``x^(3/2)`` is
    superadditive. When the denominator's interval reaches 0, ``a`` is
    known only to lie in ``[-1/6, 1/6]``. Whether the jackknife has no
    spread at all is decided exactly, from the middle order statistics.
  - The lower tail mass increases in ``z0`` and ``a``, so its smallest
    value is at the smallest ``z0`` and ``a``. The upper tail mass
    decreases in both, so its smallest value is at the largest. Taking
    each there puts each computed mass at or below the exact one, so
    the lower bound is a smaller order statistic and the upper bound a
    larger one than exact arithmetic would select. The arguments of
    ``ndtr`` are moved by a bound on their error in the direction that
    lowers each mass, and each mass is scaled by ``1 - 2 eps`` before
    ``j = floor((B + 1) t)``.
  - Those monotonicities hold only while ``D = 1 - a w > 0``. If ``D``
    comes within ``2**-20`` of 0 at any corner of the box, the bounds
    are ``NaN``. Past ``D = 0`` the term ``w / D`` changes sign: with
    ``z0 = -3.5`` and ``a = -1/6`` at 99%, the lower argument becomes
    about ``+480`` and the lower mass about 1, which would put the
    lower bound on the largest replicate. ``D`` is bilinear in ``a``
    and ``z0``, so positive corners make it positive at the exact point
    too.
  - With ``D > 0``, ``w / D`` increases in ``w``, so the exact lower
    argument is below the exact upper one and ``t_lo + t_hi < 1``. The
    exact bounds therefore never cross. If the computed ``j`` are in
    ``[1, B]``, the exact ones are as well: each exact ``j`` is at
    least the computed one, and at most ``B`` less the other. A
    computed ``j`` outside the range gives ``NaN``.

  The assumption: `scipy.special.ndtr`, `scipy.special.ndtri` and
  `numpy.log` are within ``eps = 2**-40`` (about ``9.1e-13``) of their
  exact values, relative for ``ndtr`` and ``log`` and as ``eps (1 +
  |z|)`` for ``ndtri``. Against 50-digit references on about 40,000
  points each (scipy 1.18.1, numpy 2.5.4, macOS arm64), the largest
  errors measured were ``1.4e-14`` for ``ndtr`` (deep in the tails),
  ``3.1e-16`` for ``ndtri`` and ``1.1e-16`` for ``log``. ``eps`` is
  wider than all three, but it is an assumption about those
  libraries, not a derived bound.

Inputs must lie in ``[2**-500, 2**500]`` so that every median, ratio,
reciprocal and widened bound stays in the normal range the error bound
needs.

Memory and time
---------------
``max_bytes`` bounds the peak of the arrays one call allocates. Out
of it come the float64 copies of the inputs, the returned arrays and
a 64 KiB allowance for small objects. The rest holds, per row in the
current block, the replicate buffer and its comparison mask (``9 B``
bytes) and the jackknife arrays (at most ``128 (n_b + n_c)`` bytes),
and, per resample in the current chunk, the index and gathered arrays
(``16 (n_b + n_c) + 160`` bytes). Whole rows are processed together
when one row's resamples fit; otherwise rows go one at a time with
``B`` in chunks. Time is ``O(R B (n_b + n_c))`` for the draws and the
median selections plus ``O(R B log B)`` for sorting the replicates.
"""

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Final, get_args

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.special import (  # pyright: ignore[reportMissingTypeStubs]
    ndtr,  # pyright: ignore[reportUnknownVariableType]
    ndtri,  # pyright: ignore[reportUnknownVariableType]
)

from perfproof.schema import Direction

__all__ = [
    "DEFAULT_MAX_BYTES",
    "BootstrapIntervals",
    "RatioInterval",
    "bootstrap_median_ratio",
]

DEFAULT_MAX_BYTES: Final = 64 * 2**20
"""Default peak-memory budget of one call, in bytes (64 MiB)."""

_U: Final = 2.0**-53
_WIDEN_DOWN: Final = 1.0 - 5 * _U
_WIDEN_UP: Final = 1.0 + 6 * _U
_BAND_DOWN: Final = 1.0 - 8 * _U
_BAND_UP: Final = 1.0 + 8 * _U
_EPS_LIB: Final = 2.0**-40
_A_MAX: Final = 1.0 / 6.0
_MIN_SLOPE: Final = 2.0**-20
_EXACT_FLOATS: Final = frozenset(
    np.dtype(t) for t in (np.float16, np.float32, np.float64)
)
_MIN_VALUE: Final = 2.0**-500
_MAX_VALUE: Final = 2.0**500
_MAX_EXACT_INT: Final = 2**53
_MIN_N: Final = 2
_BYTES_PER_ROW_PER_RESAMPLE: Final = 9
_BYTES_PER_ROW_PER_OBSERVATION: Final = 128
_BYTES_PER_RESAMPLE_PER_OBSERVATION: Final = 16
_BYTES_PER_RESAMPLE: Final = 160
_BYTES_OUT_PER_ROW: Final = 48
_OVERHEAD_BYTES: Final = 64 * 2**10


@dataclass(frozen=True, slots=True)
class RatioInterval:
    """Estimate and confidence bounds on the oriented ratio scale.

    Attributes
    ----------
    estimate : numpy.ndarray of float64
        Ratio of the sample medians, oriented so that above 1 means
        the candidate is faster; shape is the batch shape of the
        inputs (0-d for one comparison).
    lower, upper : numpy.ndarray of float64
        Interval bounds, same shape and orientation as ``estimate``;
        ``NaN`` where the procedure is undefined (BCa only).
    """

    estimate: NDArray[np.float64]
    lower: NDArray[np.float64]
    upper: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class BootstrapIntervals:
    """Percentile and BCa intervals from one set of resamples.

    Attributes
    ----------
    percentile : RatioInterval
        The percentile interval.
    bca : RatioInterval
        The bias-corrected and accelerated interval.
    """

    percentile: RatioInterval
    bca: RatioInterval


def bootstrap_median_ratio(
    baseline: ArrayLike,
    candidate: ArrayLike,
    direction: Direction,
    *,
    rng: np.random.Generator,
    resamples: int = 10_000,
    confidence: float = 0.95,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> BootstrapIntervals:
    """Bootstrap the ratio of medians, resampling each arm on its own.

    See the module docstring for the resampling layout, both interval
    constructions and the rounding guarantee.

    Parameters
    ----------
    baseline : array_like of float, shape (..., n_b)
        Baseline measurements in the metric's unit, one per execution,
        with any leading batch shape. Float or integer dtype; floats
        are widened exactly to float64, integers must be at most
        ``2**53`` in magnitude so that they convert exactly.
    candidate : array_like of float, shape (..., n_c)
        Candidate measurements, same unit and batch shape as
        ``baseline``. ``n_c`` may differ from ``n_b``.
    direction : {"lower_is_better", "higher_is_better"}
        The metric's direction; orients the ratio so that above 1
        means faster.
    rng : numpy.random.Generator
        Source of every random draw. Batch rows consume it in order,
        so a batched call equals consecutive calls on its rows with
        the same generator.
    resamples : int, default 10000
        Bootstrap resamples ``B`` per row. At least
        ``2 / (1 - confidence) - 1``, so that the percentile bounds
        exist.
    confidence : float, default 0.95
        Two-sided confidence level, in ``(0, 1)``; pass
        ``1 - alpha / K`` for a Bonferroni split over ``K`` looks.
    max_bytes : int, default DEFAULT_MAX_BYTES
        Peak bytes of the arrays this call allocates, as broken down in
        the module docstring. It must hold the float64 copies of the
        inputs, the outputs, one row's replicate buffer (about ``9 B``
        bytes) and one resample's index and gathered arrays.

    Returns
    -------
    BootstrapIntervals
        Percentile and BCa intervals, each as estimate, lower and
        upper bound with the batch shape of the inputs.

    Raises
    ------
    ValueError
        If an input is not a real numeric array with at least one
        axis, an arm has fewer than 2 observations, the batch shapes
        differ, a value is not finite or lies outside ``[2**-500,
        2**500]``, an integer input exceeds ``2**53`` in magnitude,
        ``direction`` is unknown, ``rng`` is not a
        `numpy.random.Generator`, ``confidence`` is not in ``(0, 1)``,
        ``resamples`` is too small for ``confidence``, or ``max_bytes``
        cannot hold one row.

    Notes
    -----
    Time is ``O(R B (n_b + n_c) + R B log B)`` for ``R`` batch rows.
    """
    base = _sample_array("baseline", baseline)
    cand = _sample_array("candidate", candidate)
    if base.shape[:-1] != cand.shape[:-1]:
        msg = (
            "baseline and candidate batch shapes differ: "
            f"{base.shape[:-1]} and {cand.shape[:-1]}"
        )
        raise ValueError(msg)
    if direction not in get_args(Direction):
        msg = f"unknown direction {direction!r}"
        raise ValueError(msg)
    if not isinstance(rng, np.random.Generator):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = f"rng must be a numpy.random.Generator, got {type(rng)!r}"
        raise ValueError(msg)
    level = _check_confidence(confidence)
    b = _check_positive_int("resamples", resamples)
    j_pct = math.floor((b + 1) * (1 - Fraction(level)) / 2)
    if j_pct < 1:
        need = math.ceil(2 / (1 - Fraction(level))) - 1
        msg = (
            f"resamples={b} is too few for confidence={level!r}; "
            f"the percentile bounds need at least {need}"
        )
        raise ValueError(msg)
    budget = _check_positive_int("max_bytes", max_bytes)

    batch = base.shape[:-1]
    n_b, n_c = base.shape[-1], cand.shape[-1]
    rows = math.prod(batch)
    base2 = base.reshape(rows, n_b)
    cand2 = cand.reshape(rows, n_c)
    fixed = base.nbytes + cand.nbytes + _BYTES_OUT_PER_ROW * rows
    rows_per_block, per_chunk = _plan(
        budget, budget - fixed - _OVERHEAD_BYTES, b, n_b + n_c
    )
    z = float(ndtri((1.0 - level) / 2.0))  # pyright: ignore[reportUnknownArgumentType]

    out = np.empty((5, rows), dtype=np.float64)
    for r0 in range(0, rows, rows_per_block):
        r1 = min(r0 + rows_per_block, rows)
        block = np.concatenate([base2[r0:r1], cand2[r0:r1]], axis=-1)
        out[:, r0:r1] = _block(
            block, n_b, b, per_chunk, j_pct, z, direction, rng
        )
        del block
    est, p_lo, p_hi, c_lo, c_hi = (row.reshape(batch) for row in out)
    return BootstrapIntervals(
        percentile=RatioInterval(est, p_lo, p_hi),
        bca=RatioInterval(est.copy(), c_lo, c_hi),
    )


def _block(
    x: NDArray[np.float64],
    n_b: int,
    b: int,
    per_chunk: int,
    j_pct: int,
    z: float,
    direction: Direction,
    rng: np.random.Generator,
) -> NDArray[np.float64]:
    """Intervals for a block of rows of ``x = [baseline | candidate]``.

    Returns a ``(5, rows)`` array: oriented estimate, percentile lower
    and upper, BCa lower and upper.
    """
    rows, n = x.shape
    sorted_b = np.sort(x[:, :n_b], axis=-1)
    sorted_c = np.sort(x[:, n_b:], axis=-1)
    m_b, err_b = _median_of_sorted(sorted_b)
    m_c, err_c = _median_of_sorted(sorted_c)
    ratio = m_c / m_b
    a_lo, a_hi = _acceleration_bounds(sorted_b, sorted_c, m_b, m_c)
    constant = (sorted_b[:, 0] == sorted_b[:, -1]) & (
        sorted_c[:, 0] == sorted_c[:, -1]
    )
    del sorted_b, sorted_c

    low = np.zeros(n, dtype=np.int64)
    low[n_b:] = n_b
    high = np.full(n, n_b, dtype=np.int64)
    high[n_b:] = n
    row_index = np.arange(rows, dtype=np.intp)[:, None, None]
    replicates = np.empty((rows, b), dtype=np.float64)
    ties = np.zeros(rows, dtype=np.int64)
    for k0 in range(0, b, per_chunk):
        k1 = min(k0 + per_chunk, b)
        idx = rng.integers(low, high, size=(rows, k1 - k0, n))
        gathered = x[row_index, idx]
        del idx
        rep_c, rep_err_c = _median_inplace(gathered[..., n_b:])
        rep_b, rep_err_b = _median_inplace(gathered[..., :n_b])
        del gathered
        replicates[:, k0:k1] = rep_c / rep_b
        ties += np.count_nonzero(
            (rep_c == m_c[:, None])
            & (rep_err_c == err_c[:, None])
            & (rep_b == m_b[:, None])
            & (rep_err_b == err_b[:, None]),
            axis=-1,
        )
        del rep_c, rep_err_c, rep_b, rep_err_b
    replicates.sort(axis=-1)

    below = np.count_nonzero(
        replicates < (ratio * _BAND_DOWN)[:, None], axis=-1
    )
    above = np.count_nonzero(replicates > (ratio * _BAND_UP)[:, None], axis=-1)
    z0_lo = _z0(2 * below + ties, 2 * b)
    z0_hi = _z0(2 * (b - above) - ties, 2 * b)
    tails = _bca_tails(z0_lo, z0_hi, a_lo, a_hi, z)
    bca_lo = _order_statistic(replicates, tails[0], smallest=True)
    bca_hi = _order_statistic(replicates, tails[1], smallest=False)
    pct_lo = replicates[:, j_pct - 1]
    pct_hi = replicates[:, b - j_pct]
    del replicates

    if direction == "higher_is_better":
        estimate = ratio
        bounds = (pct_lo, pct_hi, bca_lo, bca_hi)
    else:
        estimate = m_b / m_c
        bounds = (1.0 / pct_hi, 1.0 / pct_lo, 1.0 / bca_hi, 1.0 / bca_lo)
    result = np.empty((5, rows), dtype=np.float64)
    result[0] = estimate
    result[1] = bounds[0] * _WIDEN_DOWN
    result[2] = bounds[1] * _WIDEN_UP
    result[3] = bounds[2] * _WIDEN_DOWN
    result[4] = bounds[3] * _WIDEN_UP
    inverted = ~(result[3] <= result[4])
    result[3:, inverted] = np.nan
    result[1:, constant] = estimate[constant]
    return result


def _two_sum_half(
    p: NDArray[np.float64], q: NDArray[np.float64]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """``(p + q) / 2`` as a rounded value and its exact error.

    Knuth's TwoSum gives ``fl(p + q)`` and the exact remainder; halving
    both is exact in the normal range, so the exact mean is the sum of
    the two returned arrays. Two means are exactly equal if and only if
    both parts are bit-identical.
    """
    total = p + q
    p_part = total - q
    q_part = total - p_part
    err = (p - p_part) + (q - q_part)
    return total * 0.5, err * 0.5


def _median_of_sorted(
    s: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Median along the last axis of an array sorted along it.

    Returns the rounded median and its exact error (zero for odd
    ``n``), computed as in `_median_inplace`, so equal middle values
    give bit-identical results in both.
    """
    n = s.shape[-1]
    h = n // 2
    if n % 2:
        mid = s[..., h].copy()
        return mid, np.zeros_like(mid)
    return _two_sum_half(s[..., h - 1], s[..., h])


def _median_inplace(
    g: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Median along the last axis, partitioning ``g`` in place.

    ``g`` is a scratch array (or a view of one) that may be reordered;
    the results are new arrays, so ``g`` can be freed afterwards.
    Returns the rounded median and its exact error, as
    `_median_of_sorted` does.
    """
    n = g.shape[-1]
    h = n // 2
    if n % 2:
        g.partition(h, axis=-1)
        mid = g[..., h].copy()
        return mid, np.zeros_like(mid)
    g.partition((h - 1, h), axis=-1)
    return _two_sum_half(g[..., h - 1], g[..., h])


def _leave_one_out_medians(s: NDArray[np.float64]) -> NDArray[np.float64]:
    """Medians of ``s`` with each observation left out in turn.

    ``s`` is sorted along the last axis; column ``i`` of the result is
    the median without the observation of rank ``i``. Which of tied
    observations is left out does not change the median, so this is
    the jackknife set in rank order.
    """
    n = s.shape[-1]
    rank = np.arange(n, dtype=np.intp)
    if n % 2 == 0:
        h = n // 2 - 1
        return np.where(rank > h, s[..., h : h + 1], s[..., h + 1 : h + 2])
    h1, h2 = (n - 3) // 2, (n - 1) // 2
    first = np.where(rank > h1, s[..., h1 : h1 + 1], s[..., h1 + 1 : h1 + 2])
    second = np.where(rank > h2, s[..., h2 : h2 + 1], s[..., h2 + 1 : h2 + 2])
    return (first + second) * 0.5


def _gamma(k: int) -> float:
    """Higham's ``gamma_k = k u / (1 - k u)``."""
    return k * _U / (1.0 - k * _U)


def _no_spread(s: NDArray[np.float64]) -> NDArray[np.bool_]:
    """Rows whose leave-one-out medians are all equal, decided exactly.

    For even ``n`` they take the two middle values; for odd ``n`` the
    averages of pairs among the three middle values. Equal values give
    equal medians, so comparing those order statistics is exact.
    """
    n = s.shape[-1]
    h = n // 2
    if n % 2 == 0:
        return s[:, h - 1] == s[:, h]
    return (s[:, h - 1] == s[:, h]) & (s[:, h] == s[:, h + 1])


def _acceleration_bounds(
    sorted_b: NDArray[np.float64],
    sorted_c: NDArray[np.float64],
    m_b: NDArray[np.float64],
    m_c: NDArray[np.float64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Bound the exact BCa acceleration with an interval per row.

    The jackknife values ``log(m_b / m_b(i))`` and ``log(m_c(i) /
    m_c)`` equal ``log(r_(i) / r)`` exactly. Each computed value is
    within ``e = 4u + 2 eps |theta|`` of its exact value: its argument
    carries at most three roundings (``gamma_3``, so the log moves by
    at most ``3.01u``) and ``log`` itself is within ``eps`` relative.
    The bound is carried through the mean (summation within
    ``gamma_n``), the deviations, the factor ``n - 1``, and the sums of
    ``U**2`` and ``U**3`` by first-order expansion with the exact
    remainder terms, plus the rounding of each sum (``gamma_{n+2}``).
    The factor 1.01 covers the rounding of the slack terms themselves.

    The remaining terms, each with what it needs and what is used:

    - Combining the arms (``3u`` times the sum of the magnitudes): each
      arm's term ``s / n**k`` rounds once (``n**k`` is exact below
      ``2**53``) and adding the two rounds once more, so the combined
      sum is within ``2u (|t_b| + |t_c|) (1 + O(u))`` of the exact one.
      The magnitude sum is itself computed with one rounding, so the
      ``u`` beyond ``2u`` covers it and the second-order terms.
    - Forming each interval end (``3u (|x| + err)`` beyond ``x -+
      err``): computing ``x -+ err`` rounds once, and subtracting or
      adding the extra margin rounds again, each by up to ``u (|x| +
      err)``. That needs ``2u`` plus the ``O(u**2)`` rounding of the
      margin itself, so ``2u`` would fall short by ``O(u**2)``; ``3u``
      covers it.
    - The division into ``a`` (``8u |a|``): ``6 d``, ``sqrt(d)``, their
      product and the division are four roundings (``gamma_4``, about
      ``4u``), and applying the widening rounds twice more (``2u``).
      That needs about ``6u``; ``8u`` is larger than needed, by ``2u``.

    Both ends are ``NaN`` where neither arm's jackknife has spread.
    """
    num = np.zeros(m_b.shape)
    num_err = np.zeros(m_b.shape)
    num_abs = np.zeros(m_b.shape)
    den = np.zeros(m_b.shape)
    den_err = np.zeros(m_b.shape)
    thetas: tuple[NDArray[np.float64], NDArray[np.float64]] = (
        np.log(m_b[:, None] / _leave_one_out_medians(sorted_b)),
        np.log(_leave_one_out_medians(sorted_c) / m_c[:, None]),
    )
    for theta in thetas:
        n = theta.shape[-1]
        magnitude = np.max(np.abs(theta), axis=-1)
        e_theta = 4.0 * _U + 2.0 * _EPS_LIB * magnitude
        e_mean = e_theta + (_gamma(n) + _U) * magnitude
        dev: NDArray[np.float64] = (
            np.mean(theta, axis=-1, keepdims=True) - theta
        )
        e_dev = e_theta + e_mean + _U * np.max(np.abs(dev), axis=-1)
        u_ji: NDArray[np.float64] = (n - 1.0) * dev
        mag: NDArray[np.float64] = np.abs(u_ji)
        e: NDArray[np.float64] = (
            (n - 1.0) * e_dev * (1.0 + _U) + _U * np.max(mag, axis=-1)
        )[:, None]
        s3: NDArray[np.float64] = np.sum(u_ji * u_ji * u_ji, axis=-1)
        s2: NDArray[np.float64] = np.sum(u_ji * u_ji, axis=-1)
        e3: NDArray[np.float64] = 1.01 * (
            np.sum(3.0 * mag**2 * e + 3.0 * mag * e**2 + e**3, axis=-1)
            + _gamma(n + 2) * np.sum(mag**3, axis=-1)
        )
        e2: NDArray[np.float64] = 1.01 * (
            np.sum(2.0 * mag * e + e**2, axis=-1)
            + _gamma(n + 1) * np.sum(mag**2, axis=-1)
        )
        cube, square = float(n) ** 3, float(n) ** 2
        num += s3 / cube
        num_abs += np.abs(s3 / cube)
        num_err += e3 / cube
        den += s2 / square
        den_err += e2 / square
    num_err = 1.01 * num_err + 3.0 * _U * num_abs
    den_err = 1.01 * den_err + 3.0 * _U * den
    num_lo = num - num_err - 3.0 * _U * (np.abs(num) + num_err)
    num_hi = num + num_err + 3.0 * _U * (np.abs(num) + num_err)
    den_lo = den - den_err - 3.0 * _U * (den + den_err)
    den_hi = den + den_err + 3.0 * _U * (den + den_err)
    bounded = den_lo > 0
    safe_lo = np.where(bounded, den_lo, 1.0)
    scale_lo = 6.0 * safe_lo * np.sqrt(safe_lo)
    scale_hi = 6.0 * den_hi * np.sqrt(den_hi)
    a_hi = np.where(num_hi >= 0, num_hi / scale_lo, num_hi / scale_hi)
    a_lo = np.where(num_lo >= 0, num_lo / scale_hi, num_lo / scale_lo)
    a_hi = np.where(bounded, a_hi + 8.0 * _U * np.abs(a_hi), _A_MAX)
    a_lo = np.where(bounded, a_lo - 8.0 * _U * np.abs(a_lo), -_A_MAX)
    a_lo = np.maximum(a_lo, -_A_MAX)
    a_hi = np.minimum(a_hi, _A_MAX)
    flat = _no_spread(sorted_b) & _no_spread(sorted_c)
    return np.where(flat, np.nan, a_lo), np.where(flat, np.nan, a_hi)


def _bca_tails(
    z0_lo: NDArray[np.float64],
    z0_hi: NDArray[np.float64],
    a_lo: NDArray[np.float64],
    a_hi: NDArray[np.float64],
    z: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Compute the BCa tail masses at their outward corners.

    The lower mass increases in ``z0`` and ``a`` and is taken at
    ``(z0_lo, a_lo)``; the upper mass decreases in both and is taken at
    ``(z0_hi, a_hi)``. Each is then no larger than the exact mass, which
    widens the interval on both sides. The guard below is what makes
    those monotonicities hold. Each
    ``ndtr`` argument ``z0 + w / D`` with ``D = 1 - a w`` is moved by
    ``(eps (2 + |z0| + |z|) + 16u (1 + |z0| + |w| + |w / D|)) (1 +
    1 / D**2)``: the first term is the error of ``ndtri`` in ``z0``
    and ``z``, the second the rounding of the five operations, and
    ``1 + 1 / D**2`` bounds the derivative of the argument in ``z0``
    and ``w`` (``1 / D <= 1 + 1 / D**2``). The tail mass is then scaled
    by ``1 - 2 eps`` for ``ndtr``'s error and the product with ``B +
    1``. ``NaN`` wherever an end is ``NaN`` or ``D < 2**-20`` at any
    corner of the box.
    """
    corners_ok = np.isfinite(z0_lo) & np.isfinite(z0_hi)
    corners_ok &= np.isfinite(a_lo) & np.isfinite(a_hi)
    with np.errstate(invalid="ignore"):
        for a in (a_lo, a_hi):
            for z0 in (z0_lo, z0_hi):
                for w in (z0 + z, z0 - z):
                    corners_ok &= 1.0 - a * w >= _MIN_SLOPE

    def arg(
        z0: NDArray[np.float64], a: NDArray[np.float64], w: NDArray[np.float64]
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        d = 1.0 - a * w
        q = w / d
        slope = 1.0 + 1.0 / d**2
        err = (
            _EPS_LIB * (2.0 + np.abs(z0) + abs(z))
            + 16.0 * _U * (1.0 + np.abs(z0) + np.abs(w) + np.abs(q))
        ) * slope
        return z0 + q, err

    def mass(x: NDArray[np.float64]) -> NDArray[np.float64]:
        return np.asarray(ndtr(x), dtype=np.float64)

    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        lo_arg, lo_err = arg(z0_lo, a_lo, z0_lo + z)
        hi_arg, hi_err = arg(z0_hi, a_hi, z0_hi - z)
        shrink = 1.0 - 2.0 * _EPS_LIB
        t_lo = mass(lo_arg - lo_err) * shrink
        t_hi = mass(-hi_arg - hi_err) * shrink
    return (
        np.where(corners_ok, t_lo, np.nan),
        np.where(corners_ok, t_hi, np.nan),
    )


def _z0(count: NDArray[np.int64], total: int) -> NDArray[np.float64]:
    """BCa bias correction from the mid-rank count of replicates.

    ``count / total`` is the mid-rank share below the estimate. Above
    one half it is computed as ``-ndtri((total - count) / total)``, so
    mirrored counts give exactly negated ``z0``. ``NaN`` at 0 and
    ``total``, where ``z0`` is infinite.
    """
    upper = count > total // 2
    share = np.where(upper, total - count, count) / total
    z0 = np.asarray(ndtri(share), dtype=np.float64)
    z0 = np.where(upper, -z0, z0)
    return np.where((count == 0) | (count == total), np.nan, z0)


def _order_statistic(
    replicates: NDArray[np.float64],
    tail: NDArray[np.float64],
    *,
    smallest: bool,
) -> NDArray[np.float64]:
    """Pick the ``j``-th smallest or largest replicate for ``tail``.

    ``j = floor((B + 1) tail)``; ``NaN`` where ``tail`` is ``NaN`` or
    ``j`` lies outside ``[1, B]``, since no replicate is that order
    statistic.
    """
    b = replicates.shape[-1]
    j = np.floor((b + 1) * tail)
    unknown = ~((j >= 1) & (j <= b))
    j = np.where(unknown, 1.0, j)
    position = (j - 1 if smallest else b - j).astype(np.intp)
    picked = np.take_along_axis(replicates, position[:, None], axis=-1)[:, 0]
    return np.where(unknown, np.nan, picked)


def _plan(budget: int, work: int, b: int, n: int) -> tuple[int, int]:
    """Rows per block and resamples per chunk that fit ``work`` bytes.

    ``work`` is ``budget`` less the validated inputs, the outputs and a
    fixed allowance for small objects. Whole rows are batched when one
    row's resamples fit at once; otherwise rows go one at a time and
    ``B`` is chunked.
    """
    per_row = (
        _BYTES_PER_ROW_PER_RESAMPLE * b + _BYTES_PER_ROW_PER_OBSERVATION * n
    )
    per_resample = (
        _BYTES_PER_RESAMPLE_PER_OBSERVATION * n + _BYTES_PER_RESAMPLE
    )
    whole_row = per_row + b * per_resample
    if whole_row <= work:
        return work // whole_row, b
    if per_row + per_resample > work:
        msg = (
            f"max_bytes={budget} is too small: resamples={b} with {n} "
            f"observations per row needs at least "
            f"{budget - work + per_row + per_resample}"
        )
        raise ValueError(msg)
    return 1, (work - per_row) // per_resample


def _sample_array(name: str, value: ArrayLike) -> NDArray[np.float64]:
    """Validate one arm and widen it to float64."""
    arr = np.asarray(value)
    if arr.dtype.kind not in "fiu":
        msg = f"{name} must be a real numeric array, got dtype {arr.dtype}"
        raise ValueError(msg)
    if arr.dtype.kind == "f" and arr.dtype not in _EXACT_FLOATS:
        msg = (
            f"{name} must be float64 or narrower so that it widens "
            f"exactly, got {arr.dtype}"
        )
        raise ValueError(msg)
    if arr.ndim == 0:
        msg = f"{name} must have at least one axis, got a scalar"
        raise ValueError(msg)
    if arr.shape[-1] < _MIN_N:
        msg = (
            f"{name} needs at least {_MIN_N} observations per row, "
            f"got {arr.shape[-1]}"
        )
        raise ValueError(msg)
    if arr.dtype.kind in "iu" and arr.size:
        largest = max(abs(int(arr.max())), abs(int(arr.min())))
        if largest > _MAX_EXACT_INT:
            msg = f"{name} holds {largest}, beyond 2**53 in magnitude"
            raise ValueError(msg)
    out = arr.astype(np.float64)
    bad = ~((out >= _MIN_VALUE) & (out <= _MAX_VALUE))
    if bad.any():
        offending = float(out[bad].flat[0])
        msg = (
            f"{name} holds {offending!r}; values must be finite and in "
            "[2**-500, 2**500]"
        )
        raise ValueError(msg)
    return out


def _check_confidence(value: float) -> float:
    """Validate a two-sided confidence level."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = f"confidence must be a float, got {value!r}"
        raise ValueError(msg)
    level = float(value)
    if not 0.0 < level < 1.0:
        msg = f"confidence must be in (0, 1), got {value!r}"
        raise ValueError(msg)
    return level


def _check_positive_int(name: str, value: int) -> int:
    """Validate a positive integer parameter."""
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = f"{name} must be an integer, got {value!r}"
        raise ValueError(msg)
    if value < 1:
        msg = f"{name} must be at least 1, got {value!r}"
        raise ValueError(msg)
    return int(value)
