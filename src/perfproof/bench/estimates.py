"""Estimates behind the discovery measurements.

Two small, exact computations that `perfproof.bench.scaling`,
`perfproof.bench.stub` and ``measure --set`` share. Both give a point
estimate with a ``level`` interval from a t distribution on the log
scale. Neither is calibrated the way a comparison's decision rule is:
the intervals assume independent, roughly log-normal measurements, which
a loaded machine does not guarantee, so every caller labels what it
reports as an estimate.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

import numpy as np
from numpy.typing import NDArray
from scipy.special import stdtrit  # pyright: ignore[reportMissingTypeStubs]

__all__ = [
    "LEVEL",
    "Estimate",
    "ExponentFit",
    "fit_exponent",
    "geometric_mean",
]

LEVEL: Final = 0.95
"""Coverage of every interval here."""


@dataclass(frozen=True, slots=True)
class Estimate:
    """A positive quantity with an interval.

    Attributes
    ----------
    value : float
        Point estimate: a geometric mean.
    low, high : float or None
        Ends of the ``LEVEL`` interval; ``None`` when fewer than two
        values were given, so no spread is known.
    n : int
        Values the estimate is from.
    """

    value: float
    low: float | None
    high: float | None
    n: int


@dataclass(frozen=True, slots=True)
class ExponentFit:
    """How a measured quantity grows with input size.

    The model is ``value = c * size ** exponent``, fitted by least
    squares on the logarithms of every measurement.

    Attributes
    ----------
    exponent : float
        Fitted slope on the log-log scale.
    low, high : float
        Ends of the ``LEVEL`` interval for the slope.
    r_squared : float
        Share of the log values' variance the line explains.
    residual_sd : float
        Standard deviation of the log residuals, with ``n - 2`` degrees
        of freedom.
    n : int
        Measurements fitted.
    sizes : tuple of float
        Distinct sizes, ascending.
    medians : tuple of float
        Median value at each size.
    local_exponents : tuple of float
        Slope between each pair of neighbouring sizes' medians, one
        fewer than ``sizes``. A constant cost (start-up, a fixed setup)
        pulls small sizes' local exponents down; a rising sequence
        means the growth is not a single power.
    """

    exponent: float
    low: float
    high: float
    r_squared: float
    residual_sd: float
    n: int
    sizes: tuple[float, ...]
    medians: tuple[float, ...]
    local_exponents: tuple[float, ...]


def _t_quantile(dof: int) -> float:
    """Return the upper critical value of the two-sided interval."""
    return float(
        stdtrit(float(dof), 0.5 + LEVEL / 2)  # pyright: ignore[reportUnknownArgumentType]
    )


def geometric_mean(values: Sequence[float]) -> Estimate:
    """Geometric mean of positive values, with an interval.

    Parameters
    ----------
    values : sequence of float
        Positive, finite values, at least one.

    Returns
    -------
    Estimate
        ``exp`` of the mean of the logs, and ``exp`` of a t interval on
        that mean.

    Raises
    ------
    ValueError
        If there are no values or any is not positive and finite.

    Notes
    -----
    Linear in the number of values.
    """
    x: NDArray[np.float64] = np.asarray(values, dtype=np.float64)
    if x.size == 0 or not np.all(np.isfinite(x)) or np.any(x <= 0):
        msg = "values must be non-empty, positive and finite"
        raise ValueError(msg)
    logs = np.log(x)
    mean = float(logs.mean())
    if x.size < 2:
        return Estimate(float(np.exp(mean)), None, None, int(x.size))
    se = float(logs.std(ddof=1)) / float(np.sqrt(x.size))
    half = _t_quantile(int(x.size) - 1) * se
    return Estimate(
        float(np.exp(mean)),
        float(np.exp(mean - half)),
        float(np.exp(mean + half)),
        int(x.size),
    )


def fit_exponent(
    sizes: Sequence[float], values: Sequence[float]
) -> ExponentFit:
    """Fit ``value = c * size ** exponent`` to repeated measurements.

    Parameters
    ----------
    sizes : sequence of float
        Input size of each measurement, positive.
    values : sequence of float
        The measured quantity, positive, one per size entry. Several
        measurements may share a size.

    Returns
    -------
    ExponentFit
        The exponent with its ``LEVEL`` interval and diagnostics.

    Raises
    ------
    ValueError
        If the lengths differ, any entry is not positive and finite,
        or fewer than three distinct sizes are given.

    Notes
    -----
    Linear in the number of measurements.
    """
    n_arr: NDArray[np.float64] = np.asarray(sizes, dtype=np.float64)
    y_arr: NDArray[np.float64] = np.asarray(values, dtype=np.float64)
    if n_arr.shape != y_arr.shape or n_arr.ndim != 1:
        msg = "sizes and values must be sequences of the same length"
        raise ValueError(msg)
    for arr in (n_arr, y_arr):
        if arr.size == 0 or not np.all(np.isfinite(arr)) or np.any(arr <= 0):
            msg = "sizes and values must be non-empty, positive and finite"
            raise ValueError(msg)
    distinct = np.unique(n_arr)
    if distinct.size < 3:
        msg = f"need at least 3 distinct sizes, got {distinct.size}"
        raise ValueError(msg)
    dof = int(n_arr.size) - 2
    lx, ly = np.log(n_arr), np.log(y_arr)
    xbar, ybar = float(lx.mean()), float(ly.mean())
    sxx = float(((lx - xbar) ** 2).sum())
    slope = float(((lx - xbar) * (ly - ybar)).sum()) / sxx
    intercept = ybar - slope * xbar
    resid = ly - (intercept + slope * lx)
    rss = float((resid**2).sum())
    tss = float(((ly - ybar) ** 2).sum())
    sd = float(np.sqrt(rss / dof))
    half = _t_quantile(dof) * sd / float(np.sqrt(sxx))
    medians = [float(np.median(y_arr[n_arr == s])) for s in distinct]
    local = [
        float(np.log(medians[i + 1] / medians[i]))
        / float(np.log(distinct[i + 1] / distinct[i]))
        for i in range(distinct.size - 1)
    ]
    return ExponentFit(
        exponent=slope,
        low=slope - half,
        high=slope + half,
        r_squared=1.0 - rss / tss if tss > 0 else 1.0,
        residual_sd=sd,
        n=int(n_arr.size),
        sizes=tuple(float(s) for s in distinct),
        medians=tuple(medians),
        local_exponents=tuple(local),
    )
