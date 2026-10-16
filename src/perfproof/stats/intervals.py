"""Paired confidence intervals for the speedup, from interleaved rounds.

One round runs the baseline and the candidate once each, and its
paired ratio ``r`` is one observation, oriented so that ``r > 1``
means the candidate is faster (`paired_ratios`). The intervals work on
the log ratios ``d = log(r)``, with a leading batch axis: inputs are
shaped ``(..., n)`` with ``n`` rounds on the last axis, and every
result has the batch shape ``(...)``.

- `t_interval`, the primary procedure of ``docs/method.md``: the
  Student t interval on the mean of ``d``, exponentiated to an interval
  for the geometric-mean speedup ``S = exp(E[d])``. With ``block > 1``
  it is the batch-means fallback: `batch_means` turns each run of
  ``block`` consecutive rounds into one observation first.
- `hodges_lehmann_interval`, a challenger: the Hodges-Lehmann estimate
  and the Wilcoxon signed-rank interval, for ``exp`` of the
  pseudo-median of ``d``.

Numerics
--------
"Numerics" in ``docs/method.md`` requires that no bound comes out more
favourable to the candidate than its exact value because of rounding.
The exact value of a bound is the procedure evaluated in real
arithmetic on the float64 ratios, which are the observations: exact
logarithms, exact sums, exact quantile, exact ``exp``. Two measures
give that guarantee, and the decision then compares the returned
floats with the bar as they are (`perfproof.stats.decision`).

1. When every ratio of a row is the same float, estimate and both
   bounds are that float, untouched by arithmetic. The exact interval
   of identical observations is that point for both procedures (zero
   spread, every Walsh average equal).
2. Otherwise, each bound is widened outward by an error bound derived
   from the operations that produced it. The analysis below is the
   whole justification; no constant in it was tuned to a test.

Model. ``u = 2**-53``. ``+ - * /`` and ``sqrt`` are correctly rounded
(IEEE 754): ``fl(x op y) = (x op y)(1 + delta)`` with
``|delta| <= u``, in the normal range, which every quantity here is in
for ratios between about ``1e-290`` and ``1e290``. ``log`` and ``exp``
come from the platform's libm through NumPy and are not correctly
rounded; the analysis assumes each is within `LIBM_ULPS` ulps of the
exact result. That is an assumption about the platform, not a
derivation, and the test suite checks it against correctly rounded
references on this machine. With ``gamma_k = k u / (1 - k u)``
(Higham, *Accuracy and Stability of Numerical Algorithms*, 2nd ed.,
sections 3.1 and 4.2), we use:

- (S) any order of floating-point summation of ``k`` terms, which is
  what ``np.sum`` does, has error at most
  ``gamma_(k-1) * sum(|x_i|)``, since no term passes through more than
  ``k - 1`` additions;
- (P) a value computed from nonnegative exact inputs with ``+ * /``
  and ``sqrt``, with at most ``k`` roundings on any path from an input
  to the result, is ``X (1 + theta)`` with ``|theta| <= gamma_k``;
  then ``x * (1 + 2 (k + 1) u)``, rounded once more, is at least ``X``
  (`_up`), because ``1 / ((1 - u)(1 - gamma_k)) <= 1 + 2 (k + 1) u``
  while ``(k + 1) u <= 1/4``;
- ``gamma_k <= 2 k u = k * 2**-52`` while ``k u <= 1/2``, an exact
  float, used wherever a ``gamma`` enters a bound.

Log ratios. ``d_j = fl(log r_j)`` is within ``LIBM_ULPS`` ulps of
``log r_j``, so ``|d_j - log r_j| <= c |d_j|`` with
``c = LIBM_ULPS * 2**-51``, since an ulp is at most ``2 u`` times the
magnitude.

t interval. Let ``y_j``, ``j = 1..m``, be the computed observations:
``d_j`` itself (``block = 1``), or the block means
``y_b = fl(fl(sum of k = block logs) / k)``. Against the exact
observations ``y*_j`` (exact means of exact logs), (S) and the log
bound give ``|y_j - y*_j| <= e_j`` with ``sum_j e_j <= rho A / k``,
where ``A`` is the sum of ``|d_j|`` over the rounds used and
``rho = c + [k > 1] k 2**-52``. The computed mean
``mbar = fl(fl(sum y_j) / m)`` then satisfies
``|mbar - ybar*| <= E_m = (m 2**-52 A_y + rho A / k) / m`` with
``A_y = sum |y_j|``. For the spread, the centred sum of squares is
smallest at the mean, so for any centre, here ``mbar``,
``||y* - ybar*|| <= ||y* - mbar|| <= sqrt(T) + ||e||``, with
``T = sum (y_j - mbar)**2`` and ``||e|| <= sum e_j``. The computed
``SS = fl(sum fl(fl(y_j - mbar)**2))`` has at most ``m + 2`` roundings
per term, all terms nonnegative, so ``T <= SS / (1 - gamma_(m+2))
<= SS (1 + (m + 2) 2**-51)``. The quantile ``q`` comes from
`t_critical`, certified ``q >= q*``. Hence the exact half-width is at
most ``H = q (sqrt(SS (1 + (m + 2) 2**-51)) + rho A / k) /
sqrt(m (m - 1))`` and the exact log-scale bounds lie outside
``mbar -/+ W`` with ``W = E_m + H``. ``W`` is evaluated by (P) with
``n + m + 20`` roundings, more than any path has, and moved up by
`_up`.

Hodges-Lehmann. A computed Walsh average
``fl(d_i + d_j) * 0.5`` is within ``(u + c) max |d|`` of the exact
one, and an order statistic moves by at most the largest move of its
inputs, so each chosen order statistic is within
``eps = (1 + 4 LIBM_ULPS) 2**-53 max |d|`` of its exact counterpart.
The order statistics are picked by an integer index ``c`` that is
never larger than the exact one: exact integer arithmetic for small
``n``, and a margin that covers every rounding of the normal
approximation above `WILCOXON_EXACT_MAX_N`, with a certified ``z``.
A smaller index widens the interval.

Back to the ratio scale. The log-scale bound ``t = mbar - W`` (or
``+ W``, or an order statistic ``-/+ eps``) is rounded once to ``a``,
and ``exp`` adds up to ``LIBM_ULPS`` ulps. With ``eta = 2 LIBM_ULPS``,
``exp(t) >= exp(a)(1 - u |a| / (1 - u))`` and
``exp(a) >= fl(exp(a)) (1 - eta u)``; the lower bound is returned as
``e - fl(e * eps_a)`` with ``e = fl(exp(a))`` and
``eps_a = fl(|a| + eta + 2) * 2**-52``, which covers both factors and
the two roundings of that expression (`_exp_down`). The upper bound is
``e + fl(e * eps_a)`` by the mirror argument (`_exp_up`). A lower bound
below about ``1e-289`` is returned as ``0.0`` and an upper bound beyond
the float range as ``inf``, both still on the conservative side.

The certified quantiles evaluate the exact two-sided mass of the t
(or normal) distribution in 60-digit decimal arithmetic, from finite
series with positive terms, and step the SciPy quantile up until the
mass is at least the confidence plus ``1e-40``; that margin exceeds
the decimal rounding by many orders of magnitude for any degrees of
freedom below ``1e15``.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
from typing import Final, NoReturn, cast

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import special  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import Direction, Procedure

__all__ = [
    "LIBM_ULPS",
    "MAX_HL_ROUNDS",
    "WILCOXON_EXACT_MAX_N",
    "PairedInterval",
    "batch_means",
    "hodges_lehmann_interval",
    "normal_critical",
    "paired_ratios",
    "t_critical",
    "t_interval",
]

FloatArray = NDArray[np.float64]

LIBM_ULPS: Final = 4
"""Assumed bound, in ulps, on the error of NumPy's float64 ``log`` and
``exp`` on this platform. The glibc 2.28 manual's table of known errors
lists at most 1 ulp for double ``exp`` and ``log`` on every
architecture; 4 leaves room for other libms and SIMD paths. It enters
the widening linearly, at about ``1e-15`` relative, so a larger value
costs nothing measurable."""
WILCOXON_EXACT_MAX_N: Final = 50
"""Largest ``n`` for which the Wilcoxon critical value comes from the
exact null distribution; above it, the normal approximation with tie
and continuity correction. The same cut-off as ``scipy.stats.wilcoxon``
with ``method="auto"``."""
MAX_HL_ROUNDS: Final = 2045
"""Most rounds `hodges_lehmann_interval` takes: the largest ``n`` whose
working set for one row, ``32 n (n + 1) / 2 + 64 n + 512`` bytes, fits
the 64 MiB budget. Each of the ``n (n + 1) / 2`` Walsh averages needs
16 bytes per row (the average and one gathered operand) and 16 bytes
once (the two int64 index arrays); ``64 n + 512`` per row covers the
rest of a row's work in its chunk (its logs, the tie counts of the
normal approximation, and its per-row results)."""

_U: Final = 2.0**-53
_ULP1: Final = 2.0**-52
_LOG_REL: Final = LIBM_ULPS * 2.0**-51
_ETA: Final = 2 * LIBM_ULPS
_TINY: Final = 2.0**-960
_WALSH_BYTES: Final = 64 * 2**20
_ROW_BYTES: Final = 16
_INDEX_BYTES: Final = 16
_ROUND_BYTES: Final = 64
_ROW_FIXED_BYTES: Final = 512
_PREC: Final = 60
_MARGIN: Final = Decimal(10) ** -40
_EXACT_FLOATS: Final = frozenset(
    np.dtype(t) for t in (np.float16, np.float32, np.float64)
)


@dataclass(frozen=True, slots=True)
class PairedInterval:
    """Estimate and two-sided interval for the speedup, per batch row.

    Attributes
    ----------
    estimate : numpy.ndarray of float64
        Point estimate on the ratio scale, above 1 meaning the
        candidate is faster; shape ``(...)``, the batch shape. The
        geometric mean of the ratios for the t interval, ``exp`` of
        the Hodges-Lehmann estimate for the challenger.
    lower, upper : numpy.ndarray of float64
        ``L`` and ``U``, ratio scale, same shape,
        ``L <= estimate <= U``.
        Never more favourable to the candidate than the exact bounds;
        see the module docstring.
    confidence : float
        Two-sided confidence level of each interval.
    procedure : str
        ``"t_log_ratio"``, ``"t_log_ratio_batch_means"`` or
        ``"hodges_lehmann_wilcoxon"``, as in `perfproof.schema`.
    rounds : int
        Rounds the interval used; batch means drops a trailing
        incomplete block.
    observations : int
        Observations the interval was computed from: ``rounds``, or the
        number of blocks for batch means.
    """

    estimate: FloatArray
    lower: FloatArray
    upper: FloatArray
    confidence: float
    procedure: Procedure
    rounds: int
    observations: int


def paired_ratios(
    baseline: ArrayLike, candidate: ArrayLike, direction: Direction
) -> FloatArray:
    """Return the per-round ratios, oriented so above 1 is faster.

    ``baseline / candidate`` when lower is better, ``candidate /
    baseline`` when higher is better, each one correctly rounded
    float64 division, as in ``docs/method.md``.

    Parameters
    ----------
    baseline, candidate : array_like
        Measurements shaped ``(..., n)``, one round per element of the
        last axis, paired by position. Same shape (no broadcasting, so
        no value is paired with another round's), finite and positive.
        Float dtypes up to float64, or integers of magnitude at most
        ``2**53`` (counter metrics), are widened exactly to float64.
    direction : {"lower_is_better", "higher_is_better"}
        The metric's direction.

    Returns
    -------
    numpy.ndarray of float64
        Ratios, shape ``(..., n)``, C-contiguous whatever the input
        layout, so every reduction along the rounds axis sums in the
        same order and results are bit-reproducible.

    Raises
    ------
    ValueError
        If the arrays differ in shape, have no axis or no rounds on
        it, a dtype would not widen exactly, a value is not finite and
        positive, the direction is unknown, or a ratio leaves the
        normal float64 range.
    """
    b = _measurements("baseline", baseline)
    c = _measurements("candidate", candidate)
    if b.shape != c.shape:
        msg = f"baseline shape {b.shape} and candidate shape {c.shape} differ"
        raise ValueError(msg)
    if b.shape[-1] == 0:
        msg = f"measurements need at least one round, got shape {b.shape}"
        raise ValueError(msg)
    if direction == "lower_is_better":
        num, den = b, c
    elif direction == "higher_is_better":
        num, den = c, b
    else:
        msg = "direction must be lower_is_better or higher_is_better, got "
        msg += repr(direction)
        raise ValueError(msg)
    with np.errstate(over="ignore", under="ignore"):
        ratios: FloatArray = np.divide(num, den, order="C")
    bad = ~(np.isfinite(ratios) & (ratios >= np.finfo(np.float64).tiny))
    if np.any(bad):
        first = tuple(np.argwhere(bad)[0])
        msg = (
            f"ratio {float(num[first])!r} / {float(den[first])!r} leaves "
            "the normal float64 range"
        )
        raise ValueError(msg)
    return ratios


def batch_means(values: ArrayLike, block: int) -> FloatArray:
    """Average consecutive blocks of ``block`` rounds.

    The batch-means transform of ``docs/method.md``: each block's mean
    becomes one observation, which weakens dependence between rounds.
    A trailing incomplete block is dropped, so block boundaries stay
    where they were as rounds accumulate. Each mean is computed as
    ``sum / block``, the operations the error analysis in the module
    docstring assumes.

    Parameters
    ----------
    values : array_like of float
        Shape ``(..., n)``, usually log ratios.
    block : int
        Rounds per block, at least 1 and at most ``n``.

    Returns
    -------
    numpy.ndarray of float64
        Shape ``(..., n // block)``.

    Raises
    ------
    ValueError
        If ``values`` has no axis or is not float, or ``block`` is not
        an int in ``[1, n]``.
    """
    array = np.asarray(values)
    if array.ndim < 1 or array.dtype not in _EXACT_FLOATS:
        msg = "values must be a float array with a last axis, got "
        msg += f"dtype {array.dtype}, shape {array.shape}"
        raise ValueError(msg)
    x = array.astype(np.float64)
    n = x.shape[-1]
    if type(block) is not int or not 1 <= block <= n:
        msg = f"block must be an int in [1, {n}], got {block!r}"
        raise ValueError(msg)
    m = n // block
    if block == 1:
        return x
    used = x[..., : m * block].reshape(*x.shape[:-1], m, block)
    return np.sum(used, axis=-1) / block


def t_interval(
    baseline: ArrayLike,
    candidate: ArrayLike,
    *,
    direction: Direction,
    confidence: float,
    block: int = 1,
) -> PairedInterval:
    """Student t interval on the mean log ratio, on the ratio scale.

    The primary procedure of ``docs/method.md``. With ``m``
    observations ``y`` (the log ratios, or their block means when
    ``block > 1``), the interval for ``E[d]`` is
    ``mean(y) -/+ q s / sqrt(m)``, with ``s`` the sample standard
    deviation (``ddof = 1``) and ``q`` the two-sided t quantile on
    ``m - 1`` degrees of freedom. Estimate and bounds are ``exp`` of
    those. A row of identical ratios gives that ratio exactly;
    otherwise the bounds are widened outward by the derived error
    bound in the module docstring. O(n) time and memory per row.

    Parameters
    ----------
    baseline, candidate : array_like
        Measurements shaped ``(..., n)``; see `paired_ratios`.
    direction : {"lower_is_better", "higher_is_better"}
        The metric's direction.
    confidence : float
        Two-sided confidence level, ``0 < confidence < 1``; per look,
        so the caller applies any Bonferroni split.
    block : int, default 1
        Rounds per batch-means block; 1 means no batching. A trailing
        incomplete block is dropped.

    Returns
    -------
    PairedInterval
        Per batch row; procedure ``"t_log_ratio"`` or, with
        ``block > 1``, ``"t_log_ratio_batch_means"``.

    Raises
    ------
    ValueError
        If the measurements are invalid (see `paired_ratios`),
        ``confidence`` is not a float in ``(0, 1)``, or ``block`` is
        not an int at least 1 leaving at least 2 observations.
    """
    ratios = paired_ratios(baseline, candidate, direction)
    conf = _check_confidence(confidence)
    n = ratios.shape[-1]
    if type(block) is not int or block < 1:
        msg = f"block must be an int >= 1, got {block!r}"
        raise ValueError(msg)
    m = n // block
    if m < 2:
        msg = (
            f"the t interval needs at least 2 observations, got {n} "
            f"rounds in blocks of {block}"
        )
        raise ValueError(msg)
    used = ratios[..., : m * block]
    rounds = m * block
    q = t_critical(m - 1, conf)

    d = np.log(used)
    y = batch_means(d, block)
    mean = np.sum(y, axis=-1) / m

    rho = _LOG_REL + (block * _ULP1 if block > 1 else 0.0)
    log_err = rho * np.sum(np.abs(d), axis=-1) / block
    mean_err = (m * _ULP1 * np.sum(np.abs(y), axis=-1) + log_err) / m
    spread = np.sqrt(_centred_squares_upper(y, mean))
    half = q * (spread + log_err) / math.sqrt(m * (m - 1))
    width = _up(mean_err + half, rounds + m + 20)

    with np.errstate(over="ignore", under="ignore"):
        estimate = np.exp(mean)
    lower = _exp_down(mean - width)
    upper = _exp_up(mean + width)
    estimate, lower, upper = _exact_where_identical(
        used, estimate, lower, upper
    )
    return PairedInterval(
        estimate=estimate,
        lower=lower,
        upper=upper,
        confidence=conf,
        procedure="t_log_ratio" if block == 1 else "t_log_ratio_batch_means",
        rounds=rounds,
        observations=m,
    )


def hodges_lehmann_interval(
    baseline: ArrayLike,
    candidate: ArrayLike,
    *,
    direction: Direction,
    confidence: float,
) -> PairedInterval:
    """Hodges-Lehmann estimate and Wilcoxon signed-rank interval.

    A challenger in ``docs/method.md``. With the ``N = n (n + 1) / 2``
    Walsh averages ``(d_i + d_j) / 2``, ``i <= j``, sorted, the
    estimate is their median and the interval is
    ``[W_(c+1), W_(N-c)]``, where ``c`` is the largest count with
    ``P(T+ <= c) <= (1 - confidence) / 2`` under the signed-rank null
    distribution. That is the set of shifts the two-sided signed-rank
    test does not reject, closed at its ends. Everything is then
    exponentiated.

    For ``n <= WILCOXON_EXACT_MAX_N``, ``c`` comes from the exact null
    distribution, in integer arithmetic. Above it, from the normal
    approximation with continuity correction and the tie correction
    for groups of equal ratios, ``c = ceil(N/2 - z sigma - 1/2) - 1``
    with ``sigma**2 = n(n+1)(2n+1)/24 - sum(t**3 - t)/48``; equal
    ratios are the only ties ``|d - theta|`` has at a shift ``theta``
    off the Walsh averages, so ``c`` differs per row only through
    them. A row of identical ratios gives that ratio exactly; otherwise
    the bounds are widened outward as in the module docstring.

    O(n**2) memory and O(n**2 log n) time per row, for the Walsh
    averages. Everything computed per row (critical index, logs, Walsh
    averages, bounds) is computed in chunks of rows whose working set
    stays within 64 MiB, with the per-row cost given under
    `MAX_HL_ROUNDS`; ``n`` above it, where one row alone would not
    fit, is refused once the inputs are validated, before any Walsh
    average or other per-row work is allocated. On top of the chunk
    budget, for ``R`` rows of float64 input in any memory order, come
    the ratios (``8 R n`` bytes), the three results (``24 R``), and
    while the inputs are validated, before any chunk runs, boolean
    masks of up to ``3 R n`` more; other input dtypes add ``16 R n``
    for their float64 copies.

    Parameters
    ----------
    baseline, candidate : array_like
        Measurements shaped ``(..., n)``; see `paired_ratios`.
    direction : {"lower_is_better", "higher_is_better"}
        The metric's direction.
    confidence : float
        Two-sided confidence level, ``0 < confidence < 1``.

    Returns
    -------
    PairedInterval
        Per batch row, procedure ``"hodges_lehmann_wilcoxon"``.

    Raises
    ------
    ValueError
        If the measurements are invalid (see `paired_ratios`),
        ``confidence`` is not a float in ``(0, 1)``, ``n`` exceeds
        `MAX_HL_ROUNDS`, or ``n`` is too small for any order statistic
        to reach the confidence (``c < 0``).
    """
    ratios = paired_ratios(baseline, candidate, direction)
    conf = _check_confidence(confidence)
    n = ratios.shape[-1]
    count = n * (n + 1) // 2
    one_row = (_ROW_BYTES + _INDEX_BYTES) * count
    if one_row + _ROUND_BYTES * n + _ROW_FIXED_BYTES > _WALSH_BYTES:
        msg = (
            f"hodges_lehmann_interval takes at most {MAX_HL_ROUNDS} rounds "
            f"within its {_WALSH_BYTES // 2**20} MiB working set, got {n}"
        )
        raise ValueError(msg)
    batch = ratios.shape[:-1]
    rows = ratios.reshape(-1, n)
    exact_crit = (
        _exact_signed_rank_critical(n, conf)
        if n <= WILCOXON_EXACT_MAX_N
        else None
    )
    if rows.shape[0] and exact_crit is not None and exact_crit < 0:
        _too_few_rounds(n, conf)

    first, second = np.triu_indices(n)
    estimate = np.empty(rows.shape[0])
    lower = np.empty(rows.shape[0])
    upper = np.empty(rows.shape[0])
    per_row = _ROW_BYTES * count + _ROUND_BYTES * n + _ROW_FIXED_BYTES
    chunk = (_WALSH_BYTES - _INDEX_BYTES * count) // per_row
    for start in range(0, rows.shape[0], chunk):
        stop = min(start + chunk, rows.shape[0])
        part = rows[start:stop]
        if exact_crit is None:
            crit = _normal_signed_rank_critical(part, conf)
            if int(crit.min()) < 0:
                _too_few_rounds(n, conf)
        else:
            crit = np.full(stop - start, exact_crit)
        est, lo, hi = _hl_chunk(part, crit, first, second)
        estimate[start:stop] = est
        lower[start:stop] = lo
        upper[start:stop] = hi
    return PairedInterval(
        estimate=estimate.reshape(batch),
        lower=lower.reshape(batch),
        upper=upper.reshape(batch),
        confidence=conf,
        procedure="hodges_lehmann_wilcoxon",
        rounds=n,
        observations=n,
    )


@lru_cache(maxsize=1024)
def t_critical(df: int, confidence: float) -> float:
    """Two-sided Student t critical value, certified not below exact.

    Returns a float ``q`` with ``P(|T| <= q) >= confidence`` for ``T``
    on ``df`` degrees of freedom, verified in 60-digit decimal
    arithmetic: SciPy's quantile of the tail ``(1 - confidence) / 2``
    is the start, stepped up by doubling ulp steps until the verified
    mass reaches ``confidence`` plus ``1e-40``. The tail form matters:
    ``1 - confidence`` is exact for ``confidence >= 0.5``, whereas
    ``(1 + confidence) / 2`` rounds, which near 1 moves the quantile by
    up to about ``1e-11`` relative.
    The mass is the finite series of Abramowitz and Stegun 26.7.3
    (odd ``df``) and 26.7.4 (even ``df``), all terms positive. O(df)
    decimal operations per check; cached.

    Parameters
    ----------
    df : int
        Degrees of freedom, at least 1.
    confidence : float
        Two-sided confidence level, ``0 < confidence < 1``.

    Returns
    -------
    float
        The critical value, positive.

    Raises
    ------
    ValueError
        If ``df`` is not an int at least 1, ``confidence`` is out of
        range, or SciPy gives no finite starting quantile.
    """
    if type(df) is not int or df < 1:
        msg = f"df must be an int >= 1, got {df!r}"
        raise ValueError(msg)
    conf = _check_confidence(confidence)
    stdtrit = cast(
        "Callable[[int, float], float]",
        special.stdtrit,  # pyright: ignore[reportUnknownMemberType]
    )
    start = -float(stdtrit(df, (1.0 - conf) / 2))
    return _certify(start, conf, lambda q: _t_central_mass(df, q))


@lru_cache(maxsize=256)
def normal_critical(confidence: float) -> float:
    """Two-sided normal critical value, certified not below exact.

    Returns a float ``z`` with ``P(|Z| <= z) >= confidence``, verified
    in 60-digit decimal arithmetic from ``erf(z / sqrt 2) =
    2 phi(z) sum z**(2k+1) / (2k+1)!!``, all terms positive, as for
    `t_critical`. Cached.

    Parameters
    ----------
    confidence : float
        Two-sided confidence level, ``0 < confidence < 1``.

    Returns
    -------
    float
        The critical value, positive.

    Raises
    ------
    ValueError
        If ``confidence`` is out of range or SciPy gives no finite
        starting quantile.
    """
    conf = _check_confidence(confidence)
    ndtri = cast(
        "Callable[[float], float]",
        special.ndtri,  # pyright: ignore[reportUnknownMemberType]
    )
    start = -float(ndtri((1.0 - conf) / 2))
    return _certify(start, conf, _normal_central_mass)


def _certify(
    start: float, confidence: float, mass: Callable[[float], Decimal]
) -> float:
    """Step ``start`` up until ``mass`` shows it covers ``confidence``.

    Raises
    ------
    ValueError
        If ``start`` is not finite and positive.
    """
    if not math.isfinite(start) or start <= 0:
        msg = f"no finite critical value for confidence {confidence!r}"
        raise ValueError(msg)
    target = Decimal(confidence) + _MARGIN
    q = start
    step = math.ulp(q)
    while mass(q) < target:
        q += step
        step *= 2
    return q


def _t_central_mass(df: int, q: float) -> Decimal:
    """``P(|T| <= q)`` for ``T`` on ``df`` degrees of freedom.

    Abramowitz and Stegun 26.7.3 and 26.7.4 with
    ``theta = atan(q / sqrt df)``.
    """
    with localcontext() as ctx:
        ctx.prec = _PREC
        t = Decimal(q)
        nu = Decimal(df)
        cos2 = nu / (nu + t * t)
        sin = t / (nu + t * t).sqrt()
        total = Decimal(1)
        term = Decimal(1)
        if df % 2 == 0:
            for j in range(1, df // 2):
                term = term * cos2 * (2 * j - 1) / (2 * j)
                total += term
            return sin * total
        theta = _atan(t / nu.sqrt())
        if df == 1:
            return 2 * theta / _pi()
        for j in range(1, (df - 1) // 2):
            term = term * cos2 * (2 * j) / (2 * j + 1)
            total += term
        return 2 * (theta + sin * cos2.sqrt() * total) / _pi()


def _normal_central_mass(z: float) -> Decimal:
    """``P(|Z| <= z)`` for a standard normal ``Z``.

    The series stops once the ratio of successive terms is below 1/2,
    so the omitted tail is below the last term, and that term is below
    ``1e-65`` of the sum.
    """
    with localcontext() as ctx:
        ctx.prec = _PREC
        x = Decimal(z)
        x2 = x * x
        term = x
        total = x
        k = 0
        tiny = Decimal(10) ** -65
        while True:
            k += 1
            term = term * x2 / (2 * k + 1)
            total += term
            if term < total * tiny and 2 * x2 < 2 * k + 3:
                break
        phi = (-x2 / 2).exp() / (2 * _pi()).sqrt()
        return 2 * phi * total


def _atan(y: Decimal) -> Decimal:
    """``atan(y)`` for ``y > 0`` in the current decimal context.

    Halves the angle with ``atan(y) = 2 atan(y / (1 + sqrt(1 + y**2)))``
    until ``y <= 0.01``, then sums the alternating Taylor series until
    the next term, which bounds the truncation error, is below
    ``1e-70``.
    """
    doublings = 0
    small = Decimal("0.01")
    while y > small:
        y = y / (1 + (1 + y * y).sqrt())
        doublings += 1
    y2 = y * y
    term = y
    total = y
    k = 1
    tiny = Decimal(10) ** -70
    while True:
        term = -term * y2
        k += 2
        add = term / k
        if abs(add) < tiny:
            break
        total += add
    return total * (2**doublings)


@lru_cache(maxsize=1)
def _pi() -> Decimal:
    """Pi to `_PREC` digits, as ``4 atan(1)``."""
    with localcontext() as ctx:
        ctx.prec = _PREC
        return 4 * _atan(Decimal(1))


def _hl_chunk(
    part: FloatArray,
    crit: NDArray[np.int64],
    first: NDArray[np.intp],
    second: NDArray[np.intp],
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Return estimate, lower and upper bound for one chunk of rows.

    A function of its own so that nothing it allocates (logs, Walsh
    averages, temporaries) outlives the chunk; everything it returns
    is a fresh array of one value per row, never a view of the Walsh
    averages, which would keep the whole buffer alive into the next
    chunk.
    """
    count = first.size
    mid = count // 2
    d = np.log(part)
    walsh = d[:, first]
    walsh += d[:, second]
    walsh *= 0.5
    walsh.sort(axis=-1)
    lo_stat = np.take_along_axis(walsh, crit[:, None], axis=-1)[:, 0]
    hi_stat = np.take_along_axis(walsh, count - 1 - crit[:, None], axis=-1)
    hi_stat = hi_stat[:, 0]
    median = (
        walsh[:, mid].copy()
        if count % 2
        else (walsh[:, mid - 1] + walsh[:, mid]) * 0.5
    )
    del walsh
    eps = _up((1 + 4 * LIBM_ULPS) * _U * np.max(np.abs(d), axis=-1), 1)
    with np.errstate(over="ignore", under="ignore"):
        est = np.exp(median)
    return _exact_where_identical(
        part, est, _exp_down(lo_stat - eps), _exp_up(hi_stat + eps)
    )


def _too_few_rounds(n: int, confidence: float) -> NoReturn:
    """Raise the `ValueError` for a signed-rank interval out of reach.

    Raises
    ------
    ValueError
        Always.
    """
    msg = (
        f"{n} rounds are too few for a signed-rank interval at "
        f"confidence {confidence!r}"
    )
    raise ValueError(msg)


@lru_cache(maxsize=128)
def _exact_signed_rank_critical(n: int, confidence: float) -> int:
    """Largest ``c`` with ``P(T+ <= c) <= (1 - confidence) / 2``, or -1.

    The null distribution of ``T+`` on ``n`` untied, nonzero values is
    the distribution of a sum of a uniformly random subset of
    ``{1, ..., n}``; its counts come from the product of
    ``(1 + x**i)``, in exact integers. O(n**3) integer operations.
    """
    total = n * (n + 1) // 2
    counts = [1] + [0] * total
    for i in range(1, n + 1):
        for s in range(total, i - 1, -1):
            counts[s] += counts[s - i]
    tail = (1 - Fraction(confidence)) / 2 * 2**n
    cumulative = 0
    crit = -1
    for c, k in enumerate(counts):
        cumulative += k
        if cumulative > tail:
            break
        crit = c
    return crit


def _normal_signed_rank_critical(
    rows: FloatArray, confidence: float
) -> NDArray[np.int64]:
    """Per-row ``c`` from the tie- and continuity-corrected normal law.

    ``x = N/2 - z sigma - 1/2`` is computed in float64 with five
    roundings, each moving ``x`` by at most ``u M``, with
    ``M = N/2 + z sigma + 1``; the margin ``M 2**-49 = 16 u M`` covers
    them and the rounding of the subtraction that applies it, so
    ``ceil(x - margin) - 1`` never exceeds the exact ``c``. ``z`` is
    certified not below the exact quantile, which only lowers ``c``.
    """
    n = rows.shape[-1]
    count = n * (n + 1) // 2
    ties = _tie_sums(rows)
    numerator = 2 * n * (n + 1) * (2 * n + 1) - ties
    sigma = np.sqrt(numerator.astype(np.float64) / 48.0)
    z = normal_critical(confidence)
    x = count / 2 - z * sigma - 0.5
    margin = (count / 2 + z * sigma + 1.0) * 2.0**-49
    return np.ceil(x - margin).astype(np.int64) - 1


def _tie_sums(rows: FloatArray) -> NDArray[np.int64]:
    """Per row, ``sum(t**3 - t)`` over groups of ``t`` equal values."""
    count, n = rows.shape
    ordered = np.sort(rows, axis=-1)
    new = np.ones(ordered.shape, dtype=np.bool_)
    new[:, 1:] = ordered[:, 1:] != ordered[:, :-1]
    offsets = (np.arange(count, dtype=np.int64) * n)[:, None]
    group: NDArray[np.int64] = np.cumsum(new, axis=-1, dtype=np.int64)
    group = group - 1 + offsets
    sizes = np.bincount(group.ravel(), minlength=count * n)
    sizes = sizes.reshape(count, n).astype(np.int64)
    return np.sum(sizes**3 - sizes, axis=-1)


def _centred_squares_upper(y: FloatArray, mean: FloatArray) -> FloatArray:
    """Return ``SS (1 + (m + 2) 2**-51)``, rounded once.

    ``SS`` is the computed ``sum((y - mean)**2)`` over the last axis,
    with ``m`` terms. As a real number the product is at least
    ``(1 - gamma_(m+2))(1 + 4 (m + 2) u) T >= (1 + (m + 2) u) T`` for
    the exact ``T = sum((y_j - mean)**2)`` (module docstring), so it
    stays at least ``T`` after its one rounding, which the width counts
    as well.
    """
    m = y.shape[-1]
    dev: FloatArray = y - mean[..., None]
    ss: FloatArray = np.sum(dev * dev, axis=-1)
    return ss * (1.0 + (m + 2) * 2.0**-51)


def _up(x: FloatArray, roundings: int) -> FloatArray:
    """Return a float array not below the exact value ``x`` rounds.

    ``x`` must come from nonnegative exact inputs through ``+ * /`` and
    ``sqrt`` with at most ``roundings`` roundings on any path; see (P)
    in the module docstring.
    """
    return x * (1.0 + (roundings + 1) * _ULP1)


def _exp_down(a: FloatArray) -> FloatArray:
    """Return a float not above ``exp(t)``, where ``a`` rounds ``t``.

    See "Back to the ratio scale" in the module docstring.
    """
    with np.errstate(over="ignore", under="ignore"):
        e = np.exp(a)
        bound = e - e * ((np.abs(a) + (_ETA + 2)) * _ULP1)
    return np.where(e >= _TINY, bound, 0.0)


def _exp_up(a: FloatArray) -> FloatArray:
    """Return a float not below ``exp(t)``, where ``a`` rounds ``t``.

    See "Back to the ratio scale" in the module docstring.
    """
    with np.errstate(over="ignore", under="ignore"):
        e = np.exp(a)
        return e + e * ((np.abs(a) + (_ETA + 2)) * _ULP1)


def _exact_where_identical(
    ratios: FloatArray,
    estimate: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Replace rows whose ratios are all one float with that float."""
    first = ratios[..., 0]
    same = np.all(ratios == ratios[..., :1], axis=-1)
    return (
        np.where(same, first, estimate),
        np.where(same, first, lower),
        np.where(same, first, upper),
    )


def _measurements(name: str, values: ArrayLike) -> FloatArray:
    """Return measurements as float64, checked for `paired_ratios`."""
    given = np.asarray(values)
    if given.ndim < 1:
        msg = f"{name} needs a rounds axis, got shape {given.shape}"
        raise ValueError(msg)
    if given.dtype in _EXACT_FLOATS:
        array = given.astype(np.float64, copy=False)
    elif given.dtype.kind in "iu":
        limit = 2**53
        if np.any(given > limit) or (
            given.dtype.kind == "i" and np.any(given < -limit)
        ):
            msg = f"{name} integers beyond 2**53 in magnitude would round"
            raise ValueError(msg)
        array = given.astype(np.float64)
    else:
        msg = f"{name} must be float64 or narrower, or integer, got "
        msg += f"dtype {given.dtype}"
        raise ValueError(msg)
    bad = ~(np.isfinite(array) & (array > 0))
    if np.any(bad):
        first = float(array[bad].flat[0])
        msg = f"{name} values must be finite and positive, got {first!r}"
        raise ValueError(msg)
    return array


def _check_confidence(confidence: object) -> float:
    """Return ``confidence`` if it is a float strictly inside (0, 1)."""
    if (
        not isinstance(confidence, float)
        or not math.isfinite(confidence)
        or not 0 < confidence < 1
    ):
        msg = f"confidence must be a float in (0, 1), got {confidence!r}"
        raise ValueError(msg)
    return float(confidence)
