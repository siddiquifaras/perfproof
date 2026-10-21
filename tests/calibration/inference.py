"""Candidate interval arithmetic for T1.11, in the calibration harness.

Phase 1 of T1.11 replaces nothing in production. These functions are
the challengers' statistics, vectorised over replications (leading
axis) like the production ones: ``d`` is the ``(R, n)`` array of paired
log ratios ``log(b / c)`` (above 0 is faster) and ``first`` the
``(R, n)`` flags of rounds where the candidate ran first.

Every candidate centres on the order: ``order_centre`` subtracts from
each ``d_i`` the mean of the rounds that ran in the same order, so that
an order effect, which shifts ``d`` by plus or minus a constant in
alternating rounds, never enters a variance estimate. Cross host
``first`` is all False and the centring is the plain one. The point
estimate keeps whole order pairs (the mean over every round, balanced
by the design), so the order effect cancels there too.

The estimators are

- ``mean``: ``d`` averaged, with the order-centred ``d`` as the
  estimating function;
- ``huber``: the Huber M-estimate of location (``HUBER_K`` robust
  standard deviations, scale the MAD of the order-centred residuals)
  computed per order and averaged with the order's share of the rounds,
  with the order-centred influence function
  ``psi(r) / E[psi'](r)`` as the estimating function. A robust centre
  targets the centre of symmetry of ``d``, which is ``E[d]`` only when
  ``d`` is symmetric; every scenario of ``simulate`` gives a symmetric
  ``d`` because both arms draw the same noise.

and the variances of the sum of the estimating function are

- ``bartlett``: Newey-West with Bartlett weights and bandwidth
  ``M = max(2, ceil(b n))`` (fixed-b);
- ``sb``: the exact variance of the stationary bootstrap of
  Politis and Romano (1994) (the ``B -> infinity`` limit of resampling,
  closed form in the circular autocovariances) with the automatic mean
  block length of Politis and White (2004);
- batch means with a data-chosen block length (`auto_batch_length`)
  and ``t`` with ``B - 1`` degrees of freedom.

Critical values for ``bartlett`` and ``sb`` come from `fixedb`.
"""

import math
from dataclasses import dataclass
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]

MIN_N: Final = 12
"""Fewest observations at which a look has an interval, as in the
primary (two blocks of 6)."""
HUBER_K: Final = 1.345
"""Huber's tuning constant, in robust standard deviations (95%
efficiency under normal noise)."""
HUBER_ITERATIONS: Final = 15
MAD_TO_SD: Final = 1.482602218505602
MIN_BLOCKS: Final = 4
"""Fewest batches the data-chosen block length leaves."""
_NO_SCALE: Final = 1e300

Estimator = Literal["mean", "huber", "adjusted", "huber_adjusted"]
Variance = Literal["iid", "bartlett", "sb"]


@dataclass(frozen=True, slots=True)
class Spec:
    """A studentised procedure whose critical value `fixedb` tabulates.

    Attributes
    ----------
    name : str
        Procedure name, the key of its critical values.
    estimator : {"mean", "huber"}
        The point estimate and its estimating function.
    variance : {"iid", "bartlett", "sb"}
        The variance of the estimate: the order-centred influence
        function's variance over ``n`` (``iid``), Newey-West at
        ``b = M / n`` (``bartlett``) or the stationary bootstrap's.
    b : float
        Fixed-b ratio ``M / n`` for ``bartlett``; unused otherwise.
    lf : bool
        Whether its critical value is the least favourable over
        AR(1) dependence given the sample lag-1 autocorrelation
        (`fixedb.lf_critical`) instead of the i.i.d. normal quantile.
    """

    name: str
    estimator: Estimator
    variance: Variance
    b: float = 0.0
    lf: bool = False


def order_means(x: FloatArray, first: BoolArray) -> FloatArray:
    """Mean of each row over the rounds that ran in the same order.

    Returns shape ``(R, n)``: element ``i`` is the mean of ``x`` over
    the rounds of row ``r`` whose ``first`` equals ``first[r, i]``. A
    row with one order only gets its overall mean.
    """
    n = x.shape[1]
    count = first.sum(axis=1, keepdims=True)
    total = x.sum(axis=1, keepdims=True)
    in_first = np.where(first, x, 0.0).sum(axis=1, keepdims=True)
    mean_first = in_first / np.maximum(count, 1)
    mean_second = (total - in_first) / np.maximum(n - count, 1)
    return np.where(first, mean_first, mean_second)


def order_centre(x: FloatArray, first: BoolArray) -> FloatArray:
    """Subtract each round's order mean (see `order_means`)."""
    return x - order_means(x, first)


def _group_median(d: FloatArray, mask: BoolArray) -> FloatArray:
    """Median of ``d`` over ``mask`` per row; 0 for an empty group."""
    n = d.shape[1]
    count = mask.sum(axis=1)
    ordered = np.sort(np.where(mask, d, np.inf), axis=1)
    lo = np.take_along_axis(
        ordered, np.maximum((count - 1) // 2, 0)[:, np.newaxis], axis=1
    )[:, 0]
    hi = np.take_along_axis(
        ordered, np.minimum(count // 2, n - 1)[:, np.newaxis], axis=1
    )[:, 0]
    return np.where(count > 0, 0.5 * (lo + hi), 0.0)


def huber(d: FloatArray, first: BoolArray) -> tuple[FloatArray, FloatArray]:
    """Huber location per order, averaged, and its influence function.

    Per row, the scale is `MAD_TO_SD` times the median absolute
    deviation of ``d`` from its order's median; a row whose deviations
    are mostly 0 gets no clipping, so the estimate is the mean. Each
    order's location is the fixed point of iteratively reweighted
    means started at the order's median (`HUBER_ITERATIONS` steps; the
    objective is convex, so any start converges to the same root).

    Returns
    -------
    theta : numpy.ndarray, shape (R,)
        The location, the orders' estimates weighted by their share of
        the rounds.
    influence : numpy.ndarray, shape (R, n)
        ``psi(r_i) / D`` with ``D`` the share of the order's residuals
        inside the clipping band, so that ``theta - theta_0`` is about
        the mean of the influence.
    """
    n = d.shape[1]
    second = ~first
    med_first = _group_median(d, first)
    med_second = _group_median(d, second)
    start = np.where(
        first, med_first[:, np.newaxis], med_second[:, np.newaxis]
    )
    mad = np.median(np.abs(d - start), axis=1)
    scale = MAD_TO_SD * mad
    band = HUBER_K * np.where(scale > 0, scale, _NO_SCALE)[:, np.newaxis]
    loc_first, loc_second = med_first, med_second
    for _ in range(HUBER_ITERATIONS):
        loc = np.where(
            first, loc_first[:, np.newaxis], loc_second[:, np.newaxis]
        )
        r = np.abs(d - loc)
        with np.errstate(over="ignore"):
            w = np.where(r <= band, 1.0, band / np.maximum(r, 1e-300))
        for mask, current in ((first, loc_first), (second, loc_second)):
            den = np.where(mask, w, 0.0).sum(axis=1)
            num = np.where(mask, w * d, 0.0).sum(axis=1)
            updated = np.where(den > 0, num / np.maximum(den, 1e-300), current)
            if mask is first:
                loc_first = updated
            else:
                loc_second = updated
    loc = np.where(first, loc_first[:, np.newaxis], loc_second[:, np.newaxis])
    r = d - loc
    psi = np.clip(r, -band, band)
    inside = (np.abs(r) <= band).astype(np.float64)
    n_first = first.sum(axis=1)
    share_first = (np.where(first, inside, 0.0).sum(axis=1)) / np.maximum(
        n_first, 1
    )
    share_second = (np.where(second, inside, 0.0).sum(axis=1)) / np.maximum(
        n - n_first, 1
    )
    slope = np.where(
        first, share_first[:, np.newaxis], share_second[:, np.newaxis]
    )
    influence = psi / np.maximum(slope, 1.0 / n)
    theta = (n_first * loc_first + (n - n_first) * loc_second) / n
    return theta, influence


def estimate(
    d: FloatArray, first: BoolArray, estimator: Estimator
) -> tuple[FloatArray, FloatArray]:
    """Point estimate of ``E[d]`` and the order-centred influence.

    ``theta`` has shape ``(R,)`` and ``influence`` shape ``(R, n)``,
    already order-centred, so that the variance of ``theta`` is the
    variance of the mean of ``influence``.
    """
    if estimator == "mean":
        return d.mean(axis=1), order_centre(d, first)
    theta, influence = huber(d, first)
    return theta, order_centre(influence, first)


def autocovariances(u: FloatArray, lags: int) -> FloatArray:
    """Biased autocovariances ``sum_i u_i u_{i+j} / n``, ``j <= lags``.

    ``u`` is shaped ``(R, n)`` and already centred; returns
    ``(R, lags + 1)``. Cost ``O(R n lags)``.
    """
    n = u.shape[1]
    out = np.empty((u.shape[0], lags + 1))
    for j in range(lags + 1):
        out[:, j] = (u[:, : n - j] * u[:, j:]).sum(axis=1) / n
    return out


def bandwidth(n: int, b: float) -> int:
    """Bartlett truncation ``max(2, ceil(b n))``, at most ``n - 1``."""
    return min(n - 1, max(2, math.ceil(b * n)))


def bartlett_variance(u: FloatArray, b: float) -> FloatArray:
    """Newey-West variance of the mean of ``u`` at fixed ``b = M / n``.

    ``(gamma_0 + 2 sum_{j<M} (1 - j / M) gamma_j) / n``, never
    negative (the Bartlett kernel is positive semidefinite).
    """
    n = u.shape[1]
    m = bandwidth(n, b)
    gamma = autocovariances(u, m - 1)
    weights = 1.0 - np.arange(m) / m
    weights[1:] *= 2.0
    return (gamma * weights).sum(axis=1) / n


def politis_white_length(u: FloatArray) -> FloatArray:
    """Politis-White (2004) automatic mean block length, stationary.

    ``b = (2 G^2 / D_SB)^(1/3) n^(1/3)`` with ``D_SB = 2 g(0)^2``,
    ``g`` and ``G`` from the flat-top window of Politis and Romano
    (1995) at ``M = 2 max(m, 1)`` (as Patton's reference code, so
    that ``m = 0`` still looks at lag 1) where ``m`` is the first lag
    after which ``K_N`` autocorrelations are inside
    ``c sqrt(log10(n) / n)``, with ``c = 2``,
    ``K_N = max(5, ceil(sqrt(log10 n)))`` and at most
    ``ceil(sqrt n) + K_N`` lags examined (the lag cap of the paper).
    The length is clipped to ``[1, ceil(min(3 sqrt n, n / 3))]``, the
    cap of Patton, Politis and White (2009). Returns shape ``(R,)``.
    """
    n = u.shape[1]
    k_n = max(5, math.ceil(math.sqrt(math.log10(n))))
    m_max = min(math.ceil(math.sqrt(n)) + k_n, n - 1)
    gamma = autocovariances(u, m_max)
    with np.errstate(invalid="ignore", divide="ignore"):
        rho = np.where(gamma[:, :1] > 0, gamma[:, 1:] / gamma[:, :1], 0.0)
    cut = 2.0 * math.sqrt(math.log10(n) / n)
    small = np.abs(rho) < cut
    starts = m_max - k_n + 1
    window = np.stack(
        [small[:, s : s + k_n].all(axis=1) for s in range(starts)]
    )
    found = window.any(axis=0)
    first_ok = np.argmax(window, axis=0)
    m_hat = np.where(found, first_ok, starts - 1)
    big_m = np.minimum(2 * np.maximum(m_hat, 1), m_max)
    lags = np.arange(m_max + 1)
    t = np.abs(lags)[np.newaxis, :] / np.maximum(big_m, 1)[:, np.newaxis]
    lam = np.where(t <= 0.5, 1.0, np.where(t <= 1.0, 2.0 * (1.0 - t), 0.0))
    two = np.where(lags == 0, 1.0, 2.0)[np.newaxis, :]
    g0 = (two * lam * gamma).sum(axis=1)
    cap = (two * lam * lags[np.newaxis, :] * gamma).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = np.where(
            g0 > 0, (cap**2 / np.maximum(g0**2, 1e-300)) ** (1 / 3), 1.0
        ) * n ** (1 / 3)
    top = math.ceil(min(3 * math.sqrt(n), n / 3))
    return np.clip(raw, 1.0, float(top))


def sb_variance(u: FloatArray) -> FloatArray:
    """Exact stationary-bootstrap variance of the mean of ``u``.

    Politis and Romano (1994): with ``p = 1 / b`` for the
    `politis_white_length` ``b``,
    ``(R(0) + 2 sum_{k<n} ((n - k) / n (1 - p)^k + k / n (1 - p)^(n-k))
    R(k)) / n`` where ``R(k) = sum_{i<=n-k} u_i u_{i+k} / n`` are the
    autocovariances of the centred ``u``. It is the limit of the
    bootstrap variance as the number of resamples grows, evaluated in
    closed form. Cost ``O(R n log n)``.
    """
    return sb_variance_at(u, 1.0 / politis_white_length(u))


def sb_variance_at(u: FloatArray, p: FloatArray) -> FloatArray:
    """`sb_variance` at restart probabilities ``p``, shape ``(R,)``."""
    n = u.shape[1]
    spectrum = np.fft.rfft(u, n=2 * n, axis=1)
    acov = np.fft.irfft(np.abs(spectrum) ** 2, n=2 * n, axis=1)[:, :n] / n
    k: FloatArray = np.arange(1, n, dtype=np.float64)[np.newaxis, :]
    q = (1.0 - p)[:, np.newaxis]
    weight: FloatArray = (n - k) / n * q**k + k / n * q ** (n - k)
    total: FloatArray = acov[:, 0] + 2.0 * (weight * acov[:, 1:]).sum(axis=1)
    return total / n


def studentised_se(u: FloatArray, spec: Spec) -> FloatArray:
    """Standard error of the estimate under ``spec``'s variance."""
    if spec.variance == "bartlett":
        v = bartlett_variance(u, spec.b)
    elif spec.variance == "iid":
        v = (u * u).sum(axis=1) / u.shape[1] ** 2
    else:
        v = sb_variance(u)
    return np.sqrt(np.maximum(v, 0.0))


def pivot(
    d: FloatArray,
    first: BoolArray,
    spec: Spec,
    x: FloatArray | None = None,
) -> FloatArray:
    """``(theta_hat - 0) / se``: the pivot at true ``E[d] = 0``.

    ``inf`` or ``nan`` where the standard error is 0. ``x`` is the
    covariate of an ``adjusted`` spec.
    """
    if spec.estimator in ("adjusted", "huber_adjusted"):
        if x is None:
            msg = f"{spec.name} needs a covariate"
            raise ValueError(msg)
        fit = adjusted_fit(
            d, first, x, spec.b, robust=spec.estimator == "huber_adjusted"
        )
        with np.errstate(invalid="ignore", divide="ignore"):
            return fit.theta / fit.se
    theta, u = estimate(d, first, spec.estimator)
    with np.errstate(invalid="ignore", divide="ignore"):
        return theta / studentised_se(u, spec)


def lag1(d: FloatArray, first: BoolArray) -> FloatArray:
    """Lag-1 autocorrelation of the order-centred ``d``; 0 if flat."""
    e = order_centre(d, first)
    top = (e[:, 1:] * e[:, :-1]).sum(axis=1)
    bottom = (e * e).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(bottom > 0, top / bottom, 0.0)


def auto_batch_length(
    rho: FloatArray, n: int, scale: float
) -> NDArray[np.int64]:
    """Even batch length from the lag-1 autocorrelation, continuously.

    ``scale * n^(1/3) (2 rho / (1 - rho^2))^(2/3)`` with ``rho`` clipped
    to ``[0, 0.9]``, the order of the MSE-optimal batch size of
    Song and Schmeiser (1995) for the variance of a batch mean under
    AR(1) dependence, rounded to the nearest even integer (whole order
    pairs) in ``[2, 2 floor(n / (2 MIN_BLOCKS))]`` so that at least
    `MIN_BLOCKS` batches remain. No threshold: the length moves with
    the estimate. Returns shape ``(R,)``.
    """
    r = np.clip(rho, 0.0, 0.9)
    raw = scale * n ** (1 / 3) * (2.0 * r / (1.0 - r * r)) ** (2 / 3)
    even = 2 * np.rint(raw / 2)
    top = max(2, 2 * (n // (2 * MIN_BLOCKS)))
    return np.clip(even, 2, top).astype(np.int64)


def batch_means_auto(
    d: FloatArray,
    first: BoolArray,
    *,
    confidence: float,
    scale: float,
    estimator: Estimator,
) -> tuple[FloatArray, FloatArray]:
    """Batch-means interval for ``E[d]``, data-chosen block length.

    Block length from `auto_batch_length` on the order-centred lag-1
    autocorrelation, ``B = n // length`` blocks, ``t`` interval with
    ``B - 1`` degrees of freedom. ``mean``: the mean of the block
    means of ``d``, as the primary does. ``huber``: the Huber estimate
    of all ``n`` rounds, with the variance of the block means of its
    influence function. Returns log-scale ``(lower, upper)``, shape
    ``(R,)``.
    """
    rows, n = d.shape
    theta, u = estimate(d, first, estimator)
    length = auto_batch_length(lag1(d, first), n, scale)
    lower = np.empty(rows)
    upper = np.empty(rows)
    for size in np.unique(length).tolist():
        r = np.flatnonzero(length == size)
        b = n // size
        used = slice(0, b * size)
        if estimator == "mean":
            means = d[r][:, used].reshape(r.size, b, size).mean(axis=2)
            centre = means.mean(axis=1)
        else:
            means = u[r][:, used].reshape(r.size, b, size).mean(axis=2)
            centre = theta[r]
        spread = means - means.mean(axis=1, keepdims=True)
        se = np.sqrt((spread * spread).sum(axis=1) / (b * (b - 1)))
        crit = float(stats.t.ppf(0.5 + confidence / 2, b - 1))  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        lower[r] = centre - crit * se
        upper[r] = centre + crit * se
    return lower, upper


@dataclass(frozen=True, slots=True)
class Adjusted:
    """A covariate-adjusted fit of ``d``, one entry per replication.

    Attributes
    ----------
    theta, se : numpy.ndarray, shape (R,)
        The intercept, ``E[d]`` at balanced conditions (``x = 0``, the
        order dummy at 0), and its Newey-West standard error.
    slope, slope_se : numpy.ndarray, shape (R,)
        The covariate's coefficient and its standard error.
    imbalance : numpy.ndarray, shape (R,)
        ``mean(x)`` over its Newey-West standard error: how far the
        recorded conditions are from balanced between the arms.
    """

    theta: FloatArray
    se: FloatArray
    slope: FloatArray
    slope_se: FloatArray
    imbalance: FloatArray


def _ridge_inverse(gram: FloatArray) -> FloatArray:
    """Inverse of batched Gram matrices with a relative ridge of 1e-10.

    Faster than the pseudo-inverse; a flat covariate leaves its
    coefficient at 0 through the ridge.
    """
    p = gram.shape[1]
    ridge = 1e-10 * np.trace(gram, axis1=1, axis2=2)[:, None, None]
    return np.linalg.inv(gram + ridge * np.eye(p) + 1e-300 * np.eye(p))


def adjusted_fit(
    d: FloatArray,
    first: BoolArray,
    x: FloatArray,
    b: float,
    *,
    robust: bool = False,
) -> Adjusted:
    """Regress ``d`` on the pair's condition difference, with HAC.

    ``d_i = theta + delta o_i + beta x_i + e_i`` where ``x_i`` is the
    recorded condition of the candidate's run minus the baseline's in
    round ``i`` and ``o_i`` is +-1/2 by which order ran first (left out
    cross host, where one order only exists). The coefficients are
    ordinary least squares; the covariance is the sandwich
    ``A^-1 Omega A^-1 / n`` with ``Omega`` the Bartlett long-run
    variance of the scores at ``b = M / n`` (`bandwidth`). Rank
    deficiency is handled by the pseudo-inverse. A row whose ``x`` is
    constant carries no information about the slope: the column is
    replaced by zeros (so the intercept is the plain mean, not a share
    of it) and the imbalance is ``inf`` if the constant is not 0, since
    every round then differs by the same amount.
    Cost ``O(R n M p^2)`` with ``p`` at most 3.

    With ``robust`` the coefficients are the Huber M-estimate of the
    regression (iteratively reweighted least squares from the OLS fit,
    `HUBER_ITERATIONS` steps, clipping at `HUBER_K` times the
    `MAD_TO_SD`-scaled median absolute deviation of the order-centred
    ``d``), and the sandwich uses the clipped residuals as scores and
    the share of unclipped rounds in the bread. Like `huber`, it
    targets the centre of symmetry of the errors.
    """
    rows, n = d.shape
    ones = np.ones((rows, n))
    columns = [ones]
    if first.any():
        columns.append(np.where(first, 0.5, -0.5))
    flat = np.ptp(x, axis=1) == 0
    columns.append(np.where(flat[:, np.newaxis], 0.0, x))
    design: FloatArray = np.stack(columns, axis=2)
    gram = np.einsum("rni,rnj->rij", design, design) / n
    inverse: FloatArray = np.linalg.pinv(gram)
    beta = np.einsum(
        "rij,rj->ri",
        inverse,
        np.einsum("rni,rn->ri", design, d) / n,
    )
    resid = d - np.einsum("rni,ri->rn", design, beta)
    if robust:
        centred = order_centre(d, first)
        scale = MAD_TO_SD * np.median(np.abs(centred), axis=1)
        band = HUBER_K * np.where(scale > 0, scale, _NO_SCALE)[:, np.newaxis]
        for _ in range(HUBER_ITERATIONS):
            with np.errstate(over="ignore"):
                w = np.where(
                    np.abs(resid) <= band,
                    1.0,
                    band / np.maximum(np.abs(resid), 1e-300),
                )
            wdesign = design * w[:, :, np.newaxis]
            weighted = np.einsum("rni,rnj->rij", wdesign, design) / n
            rhs = np.einsum("rni,rn->ri", wdesign, d) / n
            beta = np.einsum("rij,rj->ri", _ridge_inverse(weighted), rhs)
            resid = d - np.einsum("rni,ri->rn", design, beta)
        inside = (np.abs(resid) <= band).astype(np.float64)
        gram = (
            np.einsum(
                "rni,rnj->rij", design * inside[:, :, np.newaxis], design
            )
            / n
        )
        inverse = _ridge_inverse(gram)
        resid = np.clip(resid, -band, band)
    scores = design * resid[:, :, np.newaxis]
    m = bandwidth(n, b)
    omega = np.einsum("rni,rnj->rij", scores, scores) / n
    for j in range(1, m):
        gamma = np.einsum("rni,rnj->rij", scores[:, : n - j], scores[:, j:])
        gamma /= n
        omega += (1.0 - j / m) * (gamma + np.swapaxes(gamma, 1, 2))
    cov = inverse @ omega @ inverse / n
    centred = x - x.mean(axis=1, keepdims=True)
    se_x = np.sqrt(np.maximum(bartlett_variance(centred, b), 0.0))
    with np.errstate(invalid="ignore", divide="ignore"):
        imbalance = np.where(se_x > 0, x.mean(axis=1) / se_x, 0.0)
    imbalance = np.where(flat, np.where(x[:, 0] != 0, np.inf, 0.0), imbalance)
    return Adjusted(
        theta=beta[:, 0],
        se=np.sqrt(np.maximum(cov[:, 0, 0], 0.0)),
        slope=beta[:, -1],
        slope_se=np.sqrt(np.maximum(cov[:, -1, -1], 0.0)),
        imbalance=imbalance,
    )
