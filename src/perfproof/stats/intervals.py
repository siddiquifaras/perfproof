"""Paired confidence intervals for the speedup, from interleaved rounds.

One round runs the baseline and the candidate once each, and its
paired ratio ``r`` is one observation, oriented so that ``r > 1``
means the candidate is faster (`paired_ratios`). The intervals work on
the log ratios ``d = log(r)``, with a leading batch axis: inputs are
shaped ``(..., n)`` with ``n`` rounds on the last axis, and every
result has the batch shape ``(...)``.

- `t_interval`: the Student t interval on the mean of ``d``,
  exponentiated to an interval for the geometric-mean speedup
  ``S = exp(E[d])``. With ``block > 1`` it is the batch-means interval,
  the primary procedure of ``docs/method.md``: `batch_means` turns each
  run of ``block`` consecutive rounds into one observation first. With
  ``block = 1`` it is the plain t interval, which calibration showed
  failing under autocorrelation, kept for estimates and as the
  reference the batch-means interval is checked against.

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

Back to the ratio scale. The log-scale bound ``t = mbar - W`` (or
``+ W``) is rounded once to ``a``,
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
distribution in 60-digit decimal arithmetic, from finite
series with positive terms, and step the SciPy quantile up until the
mass is at least the confidence plus ``1e-40``; that margin exceeds
the decimal rounding by many orders of magnitude for any degrees of
freedom below ``1e15``.

Numerics of ``hac_lf``
----------------------
The exact interval is the procedure of ``docs/method.md`` in real
arithmetic on the float64 ratios: exact logs ``d*``, exact order
centring ``u* = P d*`` (``P`` the projection that subtracts each
order's mean), exact variance ``V* = u*' T u* / n^2`` with ``T`` the
Bartlett Toeplitz matrix of bandwidth ``M``, exact lag-1 ``r*``, and
the table's critical value at ``r*``. The computed bounds lie outside
the exact ones by the following, with ``u_2 = 2**-53`` and
``gamma_k <= k 2**-52``:

- The mean: ``|fl(sum d / n) - mean(d*)| <= 1.5 2**-52 A + c A / n``
  with ``A = sum |d|`` (raised for its own rounding) and
  ``c = LIBM_ULPS 2**-51`` for the logs.
- ``T`` is positive semidefinite with row sums at most ``M``, so
  ``sqrt(x' T x)`` is a seminorm and ``sqrt(x' T x) <= sqrt(M)
  ||x||_2``. Hence
  ``sqrt(V*) <= sqrt(V_c) + sqrt(M) R / n`` where ``V_c`` is the exact
  variance of the computed centred values ``u_c`` and ``R`` bounds
  ``||u_c - u*||_2``: ``c A`` for the logs (``P`` does not increase a
  norm and ``||x||_2 <= ||x||_1``), ``2 sqrt(n) 2**-52 A`` for the
  order means (each is one sum over at most ``n`` terms, so within
  ``2 2**-52 A``), and ``2**-53 ||u_c||_2`` for the subtractions.
- The computed variance differs from ``V_c`` by at most
  ``(n + M + 8) 2**-52 (2 M) S2 / n^2`` where ``S2 = sum u_c^2``
  (raised for its rounding): each of at most ``2 M`` weighted terms is
  at most ``S2 / n`` by Cauchy-Schwarz and carries at most ``n + M + 8``
  roundings.
- The critical value is a table entry interpolated in ``r``; the table
  never falls as ``r`` rises, so the lag-1 is raised by its rounding
  bound ``2 (n + 8) 2**-52 + 3 R / sqrt(S2)`` (1 where ``S2`` is 0)
  before it is looked up, and the interpolated value is raised by
  ``16 2**-52`` relative. The ``3 R / sqrt(S2)`` term is a margin, not
  a proved bound: the first-order change of a lag-1 under a
  perturbation of relative size ``R / sqrt(S2)`` is at most twice that,
  and a numerical search over perturbations at ``n = 12`` up to
  ``R / sqrt(S2) = 0.5`` found at most 1.94 times it; the term is
  about ``1e-15`` unless the centred values are themselves at rounding
  scale, where it saturates at 1 (the largest limit)
- The width ``E + q (sqrt(V_up) (1 + 3 2**-52) + sqrt(M) R / n)`` is
  evaluated by (P) with ``n + M + 40`` roundings and moved up by `_up`;
  the exponentials are as for `t_interval`.

The tests check the bounds against exact rational arithmetic on random
and on extreme inputs.
"""

import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import cache, lru_cache
from typing import Final, cast

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import special  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import Direction, Procedure
from perfproof.stats import lf_tables

__all__ = [
    "HAC_MIN_N",
    "LIBM_ULPS",
    "RHO_FLOOR",
    "UCB_Z",
    "HacInterval",
    "PairedInterval",
    "batch_means",
    "check_hac_args",
    "hac_bandwidth",
    "hac_lf_interval",
    "lf_critical",
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

_U: Final = 2.0**-53
_ULP1: Final = 2.0**-52
_LOG_REL: Final = LIBM_ULPS * 2.0**-51
_ETA: Final = 2 * LIBM_ULPS
_TINY: Final = 2.0**-960
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
        geometric mean of the ratios.
    lower, upper : numpy.ndarray of float64
        ``L`` and ``U``, ratio scale, same shape,
        ``L <= estimate <= U``.
        Never more favourable to the candidate than the exact bounds;
        see the module docstring.
    confidence : float
        Two-sided confidence level of each interval.
    procedure : str
        ``"t_log_ratio"`` or ``"t_log_ratio_batch_means"``, as in
        `perfproof.schema`, or ``"hac_lf"`` (`hac_lf_interval`).
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

    With ``block > 1`` this is the batch-means interval, the primary
    procedure of ``docs/method.md``. With ``m``
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


HAC_MIN_N: Final = 12
"""Fewest observations at which ``hac_lf`` has an interval."""
HAC_B: Final = lf_tables.HAC_B
"""The fixed-b ratio of the ``hac_lf`` bandwidth, ``M = ceil(b n)``; the
table was built for this value, so it lives there."""
RHO_FLOOR: Final = 0.3
"""Smallest upper limit of ``rho`` the critical value covers: about
twice the largest paired lag-1 seen on a real trace (+0.13)."""
UCB_Z: Final = 1.0
"""Standard errors in the upper confidence bound of the lag-1
autocorrelation (``docs/method.md``, ``UCB_Z``)."""
_LF_MAX_K: Final = max(lf_tables.LEVEL_KS)
_ULP_TABLE: Final = 16 * _ULP1
_HAC_ROUNDING_PAD: Final = 40
_HAC_CHUNK_BYTES: Final = 32 * 2**20
"""Most bytes of ``(rows, n)`` float64 temporaries `hac_lf_interval`
holds at once; it works on as many rows as fit."""
_HAC_TEMPORARIES: Final = 16
"""Upper bound on the ``(rows, n)`` arrays alive at once in `_hac_rows`
(the logs, their absolute values, the centred values, the lagged
products, the order masks and sums)."""


@dataclass(frozen=True, slots=True)
class HacInterval:
    """The ``hac_lf`` interval of each row, and how it was made.

    Attributes
    ----------
    interval : PairedInterval
        Estimate and bounds, procedure ``"hac_lf"``.
    lag1 : numpy.ndarray of float64, shape (R,)
        The sample lag-1 autocorrelation of the order-centred log
        ratios, as computed (not raised by its rounding bound).
    ucb : numpy.ndarray of float64, shape (R,)
        Its upper confidence bound ``r + z sqrt((1 - r^2) / n)``, with
        ``r`` raised by its rounding bound.
    rho_limit : numpy.ndarray of float64, shape (R,)
        The upper limit of ``rho`` the critical value is worst case
        over: ``max(RHO_FLOOR, ucb)`` rounded up to a tabulated limit,
        at most the largest.
    """

    interval: PairedInterval
    lag1: FloatArray
    ucb: FloatArray
    rho_limit: FloatArray


def check_hac_args(ucb_z: object, rho_floor: object) -> None:
    """Refuse a bad ``ucb_z`` or ``rho_floor`` of `hac_lf_interval`.

    Parameters
    ----------
    ucb_z : object
        Must be a finite real number (not a bool) of at least 0.
    rho_floor : object
        Must be a finite real number (not a bool) within the tabulated
        limits.

    Raises
    ------
    ValueError
        If either is out of range.
    """
    limits = lf_tables.RHO_MAXES
    if (
        isinstance(ucb_z, bool)
        or not isinstance(ucb_z, int | float)
        or not math.isfinite(ucb_z)
        or ucb_z < 0
    ):
        msg = f"ucb_z must be a finite number >= 0, got {ucb_z!r}"
        raise ValueError(msg)
    if (
        isinstance(rho_floor, bool)
        or not isinstance(rho_floor, int | float)
        or not limits[0] <= rho_floor <= limits[-1]
    ):
        msg = (
            f"rho_floor must be a number in [{limits[0]}, {limits[-1]}], "
            f"got {rho_floor!r}"
        )
        raise ValueError(msg)


def hac_bandwidth(n: int) -> int:
    """Bartlett truncation lag ``M = max(2, ceil(0.1 n))``, below ``n``.

    ``ceil`` is taken of the IEEE double product ``0.1 * n``, the form
    the table was built with.

    Parameters
    ----------
    n : int
        Observations, at least 2.

    Returns
    -------
    int
        The bandwidth, at least 2 once ``n`` is at least 3 and always at
        most ``n - 1``.
    """
    return min(n - 1, max(2, math.ceil(HAC_B * n)))


@cache
def _level_confidences() -> tuple[float, ...]:
    """Return the per-look confidence of each tabulated ``K``.

    ``1 - 0.05 / (2 K)`` with the float never below the exact value, as
    `perfproof.stats.sequential.look_confidence` returns it.
    """
    out: list[float] = []
    for k in lf_tables.LEVEL_KS:
        exact = 1 - Fraction(0.05) / (2 * k)
        conf = 1.0 - 0.05 / (2 * k)
        if Fraction(conf) < exact:
            conf = math.nextafter(conf, 1.0)
        out.append(conf)
    return tuple(out)


@cache
def _table(mode: str) -> FloatArray:
    """Return one mode's table, nodes x limits x bins x levels."""
    return np.array(lf_tables.VALUES[mode], dtype=np.float64)


@cache
def _bin_nodes() -> FloatArray:
    """Return the lag-1 position of each bin."""
    edges = lf_tables.EDGES
    step = edges[1] - edges[0]
    return np.array([edges[0] - step, *edges], dtype=np.float64)


def _limit_index(rho_limit: FloatArray) -> NDArray[np.intp]:
    """Return the index of the least tabulated limit not below each.

    The largest tabulated limit where it exceeds all of them.
    """
    limits = np.asarray(lf_tables.RHO_MAXES, dtype=np.float64)
    return np.minimum(
        np.searchsorted(limits, rho_limit, side="left"), limits.size - 1
    )


def lf_critical(
    *,
    mode: str,
    n: int,
    confidence: float,
    lag1_up: FloatArray,
    rho_limit: FloatArray,
) -> FloatArray:
    """Least favourable critical value for each row.

    The table of ``docs/method.md`` ("The critical-value table"): the
    worst case over AR(1) noise with ``rho`` up to the row's limit,
    given its lag-1. The values are for the largest tabulated look
    length not above ``n`` (never falling as ``n`` falls, so
    conservative between lengths and past the last), at the smallest
    tabulated ``K`` whose confidence is at least ``confidence``, at the
    smallest tabulated limit not below the row's (the largest if it
    exceeds all), interpolated linearly in the lag-1 between the lower
    edges of its bin and the next bin.

    Parameters
    ----------
    mode : {"one_host", "cross_host"}
        Host mode.
    n : int
        Observations, at least `HAC_MIN_N`.
    confidence : float
        Per-look two-sided confidence, at most that of ``K = 12``.
    lag1_up : numpy.ndarray of float64, shape (R,)
        Lag-1 autocorrelation (raised by its rounding bound).
    rho_limit : numpy.ndarray of float64, shape (R,)
        Upper limit of ``rho`` per row.

    Returns
    -------
    numpy.ndarray of float64, shape (R,)
        Critical values.

    Raises
    ------
    ValueError
        If ``n`` is below `HAC_MIN_N`, the mode is unknown, or
        ``confidence`` is above the tabulated levels.
    """
    if mode not in ("one_host", "cross_host"):
        msg = f"unknown mode {mode!r}"
        raise ValueError(msg)
    if type(n) is not int or n < HAC_MIN_N:
        msg = f"n must be an int >= {HAC_MIN_N}, got {n!r}"
        raise ValueError(msg)
    levels = _level_confidences()
    found = [i for i, c in enumerate(levels) if c >= confidence]
    if not found:
        msg = (
            f"confidence {confidence!r} is above the tabulated level of "
            f"K = {_LF_MAX_K} looks, {levels[-1]!r}"
        )
        raise ValueError(msg)
    node = [i for i, m in enumerate(lf_tables.NODES) if m <= n][-1]
    chosen = _limit_index(rho_limit)
    out = np.empty(lag1_up.shape)
    for k in np.unique(chosen):
        rows = chosen == k
        column = _table(mode)[node, k, :, found[0]]
        out[rows] = np.interp(lag1_up[rows], _bin_nodes(), column)
    return out


def hac_lf_interval(
    baseline: ArrayLike,
    candidate: ArrayLike,
    *,
    direction: Direction,
    confidence: float,
    candidate_first: ArrayLike,
    mode: str,
    ucb_z: float = UCB_Z,
    rho_floor: float = RHO_FLOOR,
) -> HacInterval:
    """Return the T1.11 interval for ``E[d]``, on the ratio scale.

    The mean log ratio ``d_bar`` with the Newey-West (Bartlett)
    standard error of the order-centred log ratios at bandwidth
    `hac_bandwidth`, times the least favourable critical value at the
    sample lag-1 autocorrelation ``r`` (`lf_critical`), exponentiated.
    ``docs/method.md`` ("The T1.11 interval") is the specification. The
    critical value is worst case over AR(1) noise with ``rho`` up to
    ``max(rho_floor, ucb)``, where ``ucb = r + z sqrt((1 - r^2) / n)``
    is the upper confidence bound of the lag-1 autocorrelation, so the
    interval widens in proportion to the dependence the data cannot
    rule out. Nothing is withheld. A row of identical ratios gives that
    ratio exactly; otherwise the bounds are widened outward by the
    error bound derived in the "Numerics of ``hac_lf``" section of this
    module's docstring. O(R n M) time with ``M = ceil(0.1 n)``, so
    O(R n^2) in ``n``. Rows are processed in chunks of at most
    `_HAC_CHUNK_BYTES` of temporaries (32 MiB, whatever ``R``), so
    memory beyond the inputs, the ``(R, n)`` ratio array `paired_ratios`
    makes and the O(R) outputs is bounded by that budget; rows do not
    interact, so the chunking changes no value.

    Parameters
    ----------
    baseline, candidate : array_like
        Measurements shaped ``(R, n)`` with ``n`` at least `HAC_MIN_N`;
        see `paired_ratios`.
    direction : {"lower_is_better", "higher_is_better"}
        The metric's direction.
    confidence : float
        Two-sided per-look confidence, ``0 < confidence < 1``, at most
        the tabulated level of 12 looks.
    candidate_first : array_like of bool
        Shape ``(R, n)``: True where the candidate ran first in the
        round. All False cross host (one order group).
    mode : {"one_host", "cross_host"}
        Host mode, which selects the table.
    ucb_z : float, default `UCB_Z`
        Standard errors in the upper confidence bound, at least 0.
    rho_floor : float, default `RHO_FLOOR`
        Smallest ``rho`` limit, within the tabulated limits.

    Returns
    -------
    HacInterval
        Interval, the sample lag-1, its upper bound and the limit used.

    Raises
    ------
    ValueError
        If the measurements are invalid (see `paired_ratios`), not
        shaped ``(R, n)``, ``n`` is below `HAC_MIN_N`,
        ``candidate_first`` is not a boolean array of that shape,
        ``confidence``, ``ucb_z`` or ``rho_floor`` is out of range, or
        the mode is unknown.
    """
    ratios = paired_ratios(baseline, candidate, direction)
    conf = _check_confidence(confidence)
    if ratios.ndim != 2:
        msg = f"measurements must be shaped (R, n), got {ratios.shape}"
        raise ValueError(msg)
    rows, n = ratios.shape
    if n < HAC_MIN_N:
        msg = f"hac_lf needs at least {HAC_MIN_N} observations, got {n}"
        raise ValueError(msg)
    first = np.asarray(candidate_first)
    if first.dtype != np.bool_ or first.shape != ratios.shape:
        msg = (
            f"candidate_first must be a boolean array shaped {ratios.shape}"
            f", got dtype {first.dtype}, shape {first.shape}"
        )
        raise ValueError(msg)
    check_hac_args(ucb_z, rho_floor)
    parts = [
        _hac_rows(
            ratios[lo:hi],
            first[lo:hi],
            conf=conf,
            mode=mode,
            ucb_z=ucb_z,
            rho_floor=rho_floor,
        )
        for lo, hi in _row_chunks(rows, n)
    ]
    estimate, lower, upper, lag1, ucb, asked = (
        np.concatenate(column) for column in zip(*parts, strict=True)
    )
    return HacInterval(
        interval=PairedInterval(
            estimate=estimate,
            lower=lower,
            upper=upper,
            confidence=conf,
            procedure="hac_lf",
            rounds=n,
            observations=n,
        ),
        lag1=lag1,
        ucb=ucb,
        rho_limit=asked,
    )


def _row_chunks(rows: int, n: int) -> Iterator[tuple[int, int]]:
    """Yield row ranges whose ``(rows, n)`` temporaries fit the budget.

    At least one range, empty for no rows. The size depends only on
    ``n``, so a result never depends on the machine.
    """
    size = max(1, _HAC_CHUNK_BYTES // (_HAC_TEMPORARIES * 8 * n))
    for lo in range(0, max(rows, 1), size):
        yield lo, min(lo + size, rows)


def _hac_rows(
    ratios: FloatArray,
    first: NDArray[np.bool_],
    *,
    conf: float,
    mode: str,
    ucb_z: float,
    rho_floor: float,
) -> tuple[FloatArray, ...]:
    """Return ``hac_lf``'s estimate, bounds, lag-1, ucb and limit.

    One chunk of rows of validated input; every output is shaped
    ``(rows,)``. Rows do not interact, so any split of the rows gives
    the same values.
    """
    rows, n = ratios.shape
    limits = lf_tables.RHO_MAXES
    d = np.log(ratios)
    a_sum = np.sum(np.abs(d), axis=-1) * (1.0 + (n + 2) * _ULP1)
    mean = np.sum(d, axis=-1) / n
    u = _order_centred(d, first)
    s2 = np.sum(u * u, axis=-1)
    s2_up = s2 * (1.0 + (n + 2) * 2.0**-51)
    top = np.sum(u[:, :-1] * u[:, 1:], axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        lag1 = np.where(s2 > 0, np.clip(top / s2, -1.0, 1.0), 0.0)

    m = hac_bandwidth(n)
    weights = 1.0 - np.arange(m) / m
    weights[1:] *= 2.0
    gamma = np.empty((rows, m))
    for j in range(m):
        gamma[:, j] = np.sum(u[:, : n - j] * u[:, j:], axis=-1) / n
    variance = np.sum(gamma * weights, axis=-1) / n
    # Rounding of the variance: n + M + 8 roundings per term on at most
    # 2 M weighted terms of size at most S2 / n (Cauchy-Schwarz).
    var_err = (n + m + 8) * _ULP1 * 2 * m * s2_up / n / n
    var_up = np.maximum(variance + var_err, 0.0)

    # Distance of the computed centred values from the exact ones, in
    # the 2-norm: the log error, the centring error, the subtraction.
    pert = (
        _LOG_REL + 2.0 * math.sqrt(n) * _ULP1
    ) * a_sum + 2.0**-53 * np.sqrt(s2_up)
    se_up = np.sqrt(var_up) * (1.0 + 3 * _ULP1) + math.sqrt(m) * pert / n

    with np.errstate(invalid="ignore", divide="ignore"):
        sharp = np.where(s2 > 0, 3.0 * pert / np.sqrt(s2), 1.0)
    lag1_up = np.minimum(lag1 + 2.0 * (n + 8) * _ULP1 + sharp, 1.0)
    spread = np.sqrt(np.maximum(1.0 - lag1_up**2, 0.0) / n)
    ucb = lag1_up + ucb_z * spread
    wanted = np.maximum(rho_floor, ucb)
    asked = np.asarray(limits, dtype=np.float64)[_limit_index(wanted)]
    crit = lf_critical(
        mode=mode,
        n=n,
        confidence=conf,
        lag1_up=lag1_up,
        rho_limit=asked,
    ) * (1.0 + _ULP_TABLE)

    mean_err = 1.5 * _ULP1 * a_sum + _LOG_REL * a_sum / n
    width = _up(mean_err + crit * se_up, n + m + _HAC_ROUNDING_PAD)
    with np.errstate(over="ignore", under="ignore"):
        estimate = np.exp(mean)
    lower = _exp_down(mean - width)
    upper = _exp_up(mean + width)
    estimate, lower, upper = _exact_where_identical(
        ratios, estimate, lower, upper
    )
    return estimate, lower, upper, lag1, ucb, asked


def _order_centred(d: FloatArray, first: NDArray[np.bool_]) -> FloatArray:
    """Subtract from each ``d`` the mean of the rounds in its order.

    Each order's mean is the sum of its own values over their count (not
    a difference of sums), so its rounding is that of one sum. A row
    with one order only is centred on its overall mean.
    """
    count = first.sum(axis=-1, keepdims=True)
    n = d.shape[-1]
    sum_first = np.sum(np.where(first, d, 0.0), axis=-1, keepdims=True)
    sum_second = np.sum(np.where(first, 0.0, d), axis=-1, keepdims=True)
    mean_first = sum_first / np.maximum(count, 1)
    mean_second = sum_second / np.maximum(n - count, 1)
    return d - np.where(first, mean_first, mean_second)
