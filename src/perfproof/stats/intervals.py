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
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, localcontext
from functools import lru_cache
from typing import Final, cast

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import special  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import Direction, Procedure

__all__ = [
    "LIBM_ULPS",
    "PairedInterval",
    "batch_means",
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
        `perfproof.schema`.
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
