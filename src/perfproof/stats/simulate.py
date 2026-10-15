"""Synthetic paired benchmark samples with a known true speedup.

Calibration in ``docs/method.md`` runs the complete procedure on
samples whose true speedup is known. This module draws them: ``R``
replications of ``n`` observations, each a baseline value and a
candidate value, with any mix of the noise effects the method lists,
set by parameters in `NoiseScenario`. The development scenarios are
presets on top (`development_scenarios`); the audit ranges are plain
parameter values.

Metric direction
----------------
Every value is the natural log of a **time: lower is better**. The
candidate's log times are reduced by ``log(S)``, the log of the true
speedup, so ``d = log(baseline) - log(candidate) = log(r)`` is above 0
when the candidate is faster, matching the orientation of
``r = baseline / candidate`` in ``docs/method.md``. Times are
dimensionless, in units of the baseline's typical run time: with every
effect off, the baseline's log time is 0.

The output is log times, not times, because the procedures work on
log ratios and because ``exp`` of the model can leave float64. Student
t noise has polynomial tails, so inside the audit ranges a log time
occasionally exceeds 709 and its time would overflow to ``inf``. Log
times are sums of finite draws and stay finite.

The model
---------
On the log scale, for replication ``r``, observation ``i`` and arm
``a`` (0 baseline, 1 candidate)::

    log t = sigma_i * e[a, i]               run-to-run noise
          + log(slow_factor) * B            bimodal slow mode
          + log(outlier_factor) * O         rare outliers
          - log1p(order_effect) * second    order effect (one host)
          + log1p(drift) * p / (2n - 1)     linear drift (one host)
          + o                               offset
          - log(S) * [a = 1]                true speedup

- ``e[a, ·]`` is a unit-variance series per arm: i.i.d. standard
  normal, or Student-t with ``t_df`` degrees of freedom scaled to unit
  variance; with ``ar1 = rho`` it is the stationary AR(1) filter of
  those innovations, ``e_i = rho e_{i-1} + sqrt(1 - rho^2) z_i``,
  started from its stationary variance. The two arms' series are
  independent, so ``d = log(r)`` is AR(1) with the same ``rho`` and
  lag-1 autocorrelation ``rho``.
- ``sigma_i = sqrt(log1p(c_i^2))`` with ``c_i = cv`` before the
  variance step and ``cv * variance_step`` from observation ``i`` with
  ``i / n >= variance_step_at`` on. For normal noise ``c_i`` is the CV
  of the run time; with t tails the log-scale sd is the same and the
  time-scale CV is infinite, since ``exp`` of a t variable has no mean.
- ``B`` and ``O`` are independent Bernoulli draws per value with
  probabilities ``slow_fraction`` and ``outlier_rate``.
- **One host.** Each round runs both arms back to back in an order
  drawn by a fair coin per round, returned as
  `Simulated.candidate_first`. ``second`` is 1 for the arm that ran
  second, ``p = 2 i + second`` is the execution's position in the
  session, and the offset ``o`` is drawn once per round and shared by
  both runs, so it cancels in the round's ratio.
- **Cross host.** Observation ``i`` is a session pair, as under
  "Comparisons across machines": a baseline session on a fresh host,
  then the candidate session on another fresh host, so
  `Simulated.candidate_first` is all False. Each value stands for a
  whole session's geometric mean, and ``o`` is drawn per session, so
  offsets vary from pair to pair and do not cancel. Drift and the
  order effect are refused here: they describe state inside one host
  (heat, warm caches) that a fresh host does not inherit, so they have
  no cross-host counterpart that leaves the true speedup defined.

Every term but the last has the same distribution for both arms, or
is shared within the observation, or enters with the sign of a fair
coin, so ``E[d] = log(S)`` exactly and ``S = exp(E[d])`` is the
geometric-mean speedup the method's estimand targets.

Memory and cost
---------------
Work is done in chunks of replications. A chunk holds as many
replications as fit one ``(chunk, n, 2)`` float64 array in
`CHUNK_BYTES`, and at least one, so each working array takes at most
``max(CHUNK_BYTES, 16 n)`` bytes: one replication exceeds the budget
when ``n > CHUNK_BYTES / 16`` (524,288). Peak memory beyond the
returned arrays is a small multiple of that. Time is ``O(R n)``; with
``ar1 != 0`` the AR(1) recursion is a loop of ``n`` vectorized steps
over the replications.
"""

import math
from dataclasses import dataclass
from typing import Final, Literal, get_args

import numpy as np
from numpy.typing import NDArray

__all__ = [
    "CHUNK_BYTES",
    "LOGNORMAL_CVS",
    "HostMode",
    "NoiseScenario",
    "Simulated",
    "development_scenarios",
    "simulate",
]

HostMode = Literal["one_host", "cross_host"]
"""``one_host``: offsets shared within a round. ``cross_host``: random
per-session offsets in alternating baseline, candidate session pairs.
"""

CHUNK_BYTES: Final = 8 * 2**20
"""Byte budget for one ``(chunk, n, 2)`` float64 working array.

Exceeded only when a single replication is larger, ``n > 524,288``.
"""

LOGNORMAL_CVS: Final = (0.01, 0.05, 0.15)
"""The CVs of the log-normal development scenarios."""

_MODES: Final = frozenset(get_args(HostMode))

_Range = tuple[float, float, bool, bool]
"""``(low, high, low_closed, high_closed)``."""
_INF: Final = math.inf
_RANGES: Final[dict[str, _Range]] = {
    "cv": (0, _INF, True, False),
    "t_df": (2, _INF, False, False),
    "slow_fraction": (0, 1, True, True),
    "slow_factor": (0, _INF, False, False),
    "outlier_rate": (0, 1, True, True),
    "outlier_factor": (0, _INF, False, False),
    "drift": (-1, _INF, False, False),
    "variance_step": (0, _INF, False, False),
    "variance_step_at": (0, 1, True, True),
    "ar1": (-1, 1, False, False),
    "order_effect": (-1, _INF, False, False),
    "session_offset_cv": (0, _INF, True, False),
}
_POSITIVE: Final[_Range] = (0, _INF, False, False)


@dataclass(frozen=True, slots=True, kw_only=True)
class NoiseScenario:
    """Noise effects of one simulated scenario, combinable freely.

    Every effect but ``cv`` defaults to off. Fractions are of the run
    time: ``0.03`` is 3%.

    Attributes
    ----------
    cv : float
        Run-to-run noise as a coefficient of variation, ``>= 0``; sets
        the log-scale sd ``sqrt(log1p(cv**2))``.
    t_df : float or None
        Student-t degrees of freedom for the noise, ``> 2`` so its
        variance is finite; ``None`` for normal noise (log-normal
        times).
    slow_fraction : float
        Probability that a value is in the slow mode, in ``[0, 1]``.
    slow_factor : float
        How many times as long a slow-mode value takes, ``> 0``;
        ``1.3`` is 30% slower.
    outlier_rate : float
        Probability that a value is an outlier, in ``[0, 1]``; drawn
        independently of the slow mode.
    outlier_factor : float
        How many times as long an outlier takes, ``> 0``.
    drift : float
        Linear drift across the session, ``> -1``: the last execution
        takes ``1 + drift`` times as long as the first, log-linearly
        in between. One host only.
    variance_step : float
        Factor on the CV from the step on, ``> 0``; 1 for no step.
    variance_step_at : float
        Where the step falls, as a fraction of the ``n`` observations
        in ``[0, 1]``: observation ``i`` is after it when
        ``i / n >= variance_step_at``.
    ar1 : float
        AR(1) coefficient of each arm's noise across observations, in
        ``(-1, 1)``.
    order_effect : float
        The second run of a round takes ``1 / (1 + order_effect)``
        times as long, ``> -1``; ``0.03`` is 3% faster in the sense of
        a 1.03 speedup. One host only.
    session_offset_cv : float
        CV of a log-normal multiplicative offset, ``>= 0``: drawn per
        round and shared by both runs on one host, per session cross
        host.

    Raises
    ------
    ValueError
        If a value is not a finite real number (bools refused) or is
        out of its range.
    """

    cv: float
    t_df: float | None = None
    slow_fraction: float = 0.0
    slow_factor: float = 1.0
    outlier_rate: float = 0.0
    outlier_factor: float = 1.0
    drift: float = 0.0
    variance_step: float = 1.0
    variance_step_at: float = 0.5
    ar1: float = 0.0
    order_effect: float = 0.0
    session_offset_cv: float = 0.0

    def __post_init__(self) -> None:
        """Check every field against its range."""
        for name, bounds in _RANGES.items():
            value: object = getattr(self, name)
            if not (name == "t_df" and value is None):
                _check(name, value, bounds)

    @property
    def one_host_only(self) -> bool:
        """Whether the scenario uses drift or an order effect."""
        return self.drift != 0 or self.order_effect != 0


@dataclass(frozen=True, slots=True)
class Simulated:
    """Paired samples drawn by `simulate`.

    Attributes
    ----------
    log_times : numpy.ndarray of float64, shape (R, n, 2)
        Natural log of the times (lower is better), finite.
        ``[..., 0]`` is the baseline and ``[..., 1]`` the candidate, by
        arm, not by run order; ``d = log_times[..., 0] -
        log_times[..., 1]``. Exponentiating can overflow under heavy
        tails; see the module docstring.
    candidate_first : numpy.ndarray of bool, shape (R, n)
        True where the candidate ran first in its round. All False
        cross host, where the baseline session always comes first.
    """

    log_times: NDArray[np.float64]
    candidate_first: NDArray[np.bool_]


def development_scenarios(base_cv: float) -> dict[str, NoiseScenario]:
    """Return the development scenarios of ``docs/method.md``.

    The method gives the CV only for the log-normal rows, so the other
    rows take theirs from ``base_cv``. Drift and the order effect are
    one host only (see `NoiseScenario.one_host_only`).

    Parameters
    ----------
    base_cv : float
        Run-to-run CV for every scenario but the log-normal ones,
        ``>= 0``.

    Returns
    -------
    dict of str to NoiseScenario
        Keyed by scenario name, in the method's order.

    Raises
    ------
    ValueError
        If ``base_cv`` is out of range.
    """
    cv = base_cv
    return {
        "lognormal_cv1": NoiseScenario(cv=LOGNORMAL_CVS[0]),
        "lognormal_cv5": NoiseScenario(cv=LOGNORMAL_CVS[1]),
        "lognormal_cv15": NoiseScenario(cv=LOGNORMAL_CVS[2]),
        "student_t_df3": NoiseScenario(cv=cv, t_df=3.0),
        "bimodal": NoiseScenario(cv=cv, slow_fraction=0.10, slow_factor=1.30),
        "outliers": NoiseScenario(
            cv=cv, outlier_rate=1 / 50, outlier_factor=3.0
        ),
        "drift": NoiseScenario(cv=cv, drift=0.10),
        "variance_step": NoiseScenario(
            cv=cv, variance_step=2.0, variance_step_at=0.5
        ),
        "ar1": NoiseScenario(cv=cv, ar1=0.5),
        "order_effect": NoiseScenario(cv=cv, order_effect=0.03),
        "session_offset": NoiseScenario(cv=cv, session_offset_cv=0.15),
    }


def simulate(
    scenario: NoiseScenario,
    *,
    speedup: float,
    n: int,
    replications: int,
    mode: HostMode,
    rng: np.random.Generator,
) -> Simulated:
    """Draw ``replications`` paired samples of ``n`` observations.

    See the module docstring for the model. Replications are
    independent; all randomness comes from ``rng``, so the same
    generator state gives the same arrays.

    Parameters
    ----------
    scenario : NoiseScenario
        The noise effects.
    speedup : float
        True geometric-mean speedup ``S = exp(E[log(b / c)])``, finite
        and ``> 0``; above 1 means the candidate is faster.
    n : int
        Observations per replication, ``>= 1``: rounds on one host,
        session pairs cross host.
    replications : int
        ``R``, ``>= 1``.
    mode : {"one_host", "cross_host"}
        Host mode; see `HostMode`.
    rng : numpy.random.Generator
        The only source of randomness; advanced by the call.

    Returns
    -------
    Simulated
        Log times shaped ``(R, n, 2)`` and the in-round order shaped
        ``(R, n)``.

    Raises
    ------
    ValueError
        If an argument has the wrong type or is out of range, or if
        ``mode`` is ``"cross_host"`` and the scenario is one host only.
    """
    _check_type("scenario", scenario, NoiseScenario)
    _check("speedup", speedup, _POSITIVE)
    _check_count("n", n)
    _check_count("replications", replications)
    if mode not in _MODES:
        msg = f"mode must be one of {sorted(_MODES)}, got {mode!r}"
        raise ValueError(msg)
    _check_type("rng", rng, np.random.Generator)
    if mode == "cross_host" and scenario.one_host_only:
        msg = (
            "drift and order_effect are one host only; cross_host got "
            f"drift={scenario.drift!r}, "
            f"order_effect={scenario.order_effect!r}"
        )
        raise ValueError(msg)

    log_times = np.empty((replications, n, 2), dtype=np.float64)
    first = np.zeros((replications, n), dtype=np.bool_)
    chunk = max(1, CHUNK_BYTES // (n * 2 * 8))
    log_speedup = math.log(speedup)
    for start in range(0, replications, chunk):
        stop = min(start + chunk, replications)
        _fill(
            scenario,
            log_speedup,
            mode,
            rng,
            log_times[start:stop],
            first[start:stop],
        )
    return Simulated(log_times=log_times, candidate_first=first)


def _fill(
    scenario: NoiseScenario,
    log_speedup: float,
    mode: HostMode,
    rng: np.random.Generator,
    out: NDArray[np.float64],
    first: NDArray[np.bool_],
) -> None:
    """Draw one chunk into ``out`` (log times) and ``first``."""
    s = scenario
    chunk, n, _ = out.shape
    shape = out.shape
    one_host = mode == "one_host"
    if one_host:
        first[...] = rng.random((chunk, n)) < 0.5

    x = _innovations(rng, shape, s.t_df)
    if s.ar1 != 0:
        _ar1_in_place(x, s.ar1)
    index = np.arange(n, dtype=np.float64)
    after = index / n >= s.variance_step_at
    sigma = np.where(after, _log_sd(s.cv * s.variance_step), _log_sd(s.cv))
    x *= sigma[:, np.newaxis]

    if s.slow_fraction > 0:
        x += math.log(s.slow_factor) * (rng.random(shape) < s.slow_fraction)
    if s.outlier_rate > 0:
        x += math.log(s.outlier_factor) * (rng.random(shape) < s.outlier_rate)

    if one_host:
        second = np.stack((first, ~first), axis=-1)
        if s.order_effect != 0:
            x -= math.log1p(s.order_effect) * second
        if s.drift != 0:
            position = 2 * index[:, np.newaxis] + second
            x += math.log1p(s.drift) / (2 * n - 1) * position
    if s.session_offset_cv > 0:
        offset_shape = (chunk, n, 1) if one_host else shape
        x += _log_sd(s.session_offset_cv) * rng.standard_normal(offset_shape)

    x[..., 1] -= log_speedup
    out[...] = x


def _innovations(
    rng: np.random.Generator,
    shape: tuple[int, ...],
    df: float | None,
) -> NDArray[np.float64]:
    """Unit-variance i.i.d. draws: standard normal, or scaled t."""
    if df is None:
        return rng.standard_normal(shape)
    return rng.standard_t(df, size=shape) * math.sqrt((df - 2) / df)


def _ar1_in_place(x: NDArray[np.float64], rho: float) -> None:
    """Turn unit-variance innovations into stationary unit AR(1).

    The recursion runs along axis 1. Leaving ``x[:, 0]`` as drawn
    starts the series at its stationary variance, and the
    ``sqrt(1 - rho**2)`` innovation scale keeps every later variance at
    1, so the marginal sd, and with it ``cv``, is unchanged by ``rho``.
    """
    scale = math.sqrt(1 - rho * rho)
    for i in range(1, x.shape[1]):
        x[:, i] *= scale
        x[:, i] += rho * x[:, i - 1]


def _log_sd(cv: float) -> float:
    """Log-scale sd of a log-normal variable with CV ``cv``."""
    return math.sqrt(math.log1p(cv * cv))


def _check(name: str, value: object, bounds: _Range) -> None:
    """Refuse a non-real, non-finite or out-of-range parameter."""
    low, high, low_closed, high_closed = bounds
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (value >= low if low_closed else value > low)
        and (value <= high if high_closed else value < high)
    ):
        return
    rule = (
        f"{'[' if low_closed else '('}{low}, {high}"
        f"{']' if high_closed else ')'}"
    )
    msg = f"{name} must be a finite real number in {rule}, got {value!r}"
    raise ValueError(msg)


def _check_type(name: str, value: object, kind: type) -> None:
    """Refuse a value that is not an instance of ``kind``."""
    if not isinstance(value, kind):
        msg = f"{name} must be a {kind.__qualname__}, got {value!r}"
        raise ValueError(msg)


def _check_count(name: str, value: object) -> None:
    """Refuse anything but an int (not a bool) at least 1."""
    if type(value) is not int or value < 1:
        msg = f"{name} must be an int >= 1, got {value!r}"
        raise ValueError(msg)
