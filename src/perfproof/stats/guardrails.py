"""Guardrails: metrics that must not get worse as the primary does.

"Guardrails" in ``docs/method.md`` is the specification. A guardrail has
a relative allowance, absolute limits, or both. At each look every one
of those *checks* gets an interval at confidence
``1 - alpha / (2 K G)``, with ``G`` the number of checks over every
applicable guardrail of the spec (`check_count`), so, for rounds that
behave like a stable machine, the chance of a KEEP while some guardrail
is truly beyond a check is at most 1.25% by the union bound, as for the
primary. That bound is not claimed under persistent drift: see "Scope of
the claim" under "Guardrails" in ``docs/method.md``, which gives the
measured misses.

What is computed
----------------
- **Allowance.** The *worsening ratio* of round ``i`` is
  ``candidate_i / baseline_i`` when lower is better and
  ``baseline_i / candidate_i`` when higher is better, so above 1 means
  the candidate is worse. Its interval is the interval of the look's
  procedure on the worsening ratios, and it is within the allowance
  ``a`` when ``U <= 1 + a`` and breached when ``L > 1 + a``.
- **Limit.** A limit is checked on the candidate's own metric, which is
  not paired, and on a statistic that is robust to a few wild rounds:
  its median, or the quantile the project declares (such as the 99th).
  The interval is the exact order-statistic (binomial) bounds of
  `limit_bounds`, which assume nothing of the values' shape, widened
  for the dependence of the rounds. It is within a ceiling when its
  upper bound is at most the limit and breached when its lower bound is
  above it; a floor mirrors that. Between the two it is not
  established. The numbers are always recorded, whatever the status
  (`LimitStanding`): the typical value, the one-sided bound in the
  cap's direction (``reliable_value``, information and never by itself
  a verdict), its signed gap to the cap and the rounds over it.

The allowance comes from `perfproof.stats.sequential.decide_look`, with
``min_effect`` the allowance and the arms in the order that makes the
worsening ratio the speedup it computes. So the interval, its outward
rounding, the resolution widening and the diagnostic gate are the ones
of the primary, and the bar comparison is
`perfproof.stats.decision.interval_rules`, once. A guardrail is
decided by the procedure of the spec's schedule: ``hac_lf`` where the
primary's is, and the gated batch means otherwise, or where ``hac_lf``'s
table does not reach the guardrail's confidence (`guardrail_procedure`).
A look that the gate holds back (batch means only) is not established.

Status
------
A check is ``within``, ``breach`` (strictly beyond), ``open`` or
``absent`` (no interval: too few observations, or a bound that is not
representable). The guardrail is ``breached`` if any check breaches (one
check is enough), ``passed`` if every check is within, otherwise
``not_established`` once any interval exists, and at a look with no
interval at all ``not_established`` as well, since more rounds make one.
``unmeasurable`` is for a resolution too coarse to widen against, which
no round fixes. What the *record* can hold is narrower, because the
schema ties a status to the intervals recorded with it:
`guardrail_result` turns a ``not_established`` the schema could not
recognise (the gate held the look, or nothing was computed) into
``unmeasurable`` with its reason. Nothing here decides a verdict;
`perfproof.stats.decision` does.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from functools import cache
from types import MappingProxyType
from typing import Final, Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy.stats import rankdata  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

from perfproof.schema import (
    AllowanceSource,
    Direction,
    GuardrailResult,
    GuardrailSpec,
    GuardrailStatus,
    Interval,
    LimitStanding,
    Procedure,
    TargetClass,
)
from perfproof.stats import lf_tables
from perfproof.stats.intervals import paired_ratios
from perfproof.stats.sequential import (
    BATCH_BLOCK,
    Look,
    Resolution,
    decide_look,
    lag1_autocorrelation,
    look_confidence,
)

__all__ = [
    "HAC_MAX_CHECKS",
    "PROVISIONAL_ALLOWANCES",
    "RANK_MAX_CORRELATION",
    "RANK_UCB_Z",
    "TARGET_CLASS_ALLOWANCES",
    "GuardrailLook",
    "LimitBounds",
    "check_count",
    "earned_status",
    "evaluate_guardrail",
    "guardrail_confidence",
    "guardrail_procedure",
    "guardrail_result",
    "limit_bounds",
    "rank_bounds",
    "resolve_allowance",
]

FloatArray = NDArray[np.float64]
CheckState = Literal["within", "breach", "open", "absent"]

PROVISIONAL_ALLOWANCES: Final[Mapping[str, float]] = MappingProxyType(
    {"peak_memory": 0.10, "peak_gpu_memory": 0.10, "p99_latency": 0.05}
)
"""The provisional allowance of each default guardrail, as a fraction
(``docs/method.md``, "Guardrails"). Refusal thresholds used until the
project sets its own, not budgets to spend: a regression within one is
still reported."""
TARGET_CLASS_ALLOWANCES: Final[Mapping[TargetClass, Mapping[str, float]]] = (
    MappingProxyType({})
)
"""Allowances a target class sets for itself, over the provisional
default. Empty: no class has set one, so every default guardrail
carries the provisional allowance until the project overrides it. The
mechanism is here so a class's allowance has one place to live and
`resolve_allowance` records where each came from."""

HAC_MAX_CHECKS: Final = max(lf_tables.LEVEL_KS)
"""Most looks times checks the ``hac_lf`` table reaches: a guardrail's
confidence ``1 - alpha / (2 K G)`` is tabulated for ``K G`` up to
this."""

RANK_UCB_Z: Final = 2.0
"""Standard errors in the upper confidence bound on the lag-1
autocorrelation of ranks a limit check widens by: twice the ``hac_lf``
z. A bound issued on a series whose dependence the sample underrated is
the way a rank bound goes wrong, and one standard error leaves misses
above the design at moderate dependence."""
RANK_MAX_CORRELATION: Final = 0.99
"""The largest autocorrelation a limit check widens for: the upper
bound is clipped here so the effective rounds stay finite."""

_STATUS_CODES: Final = np.array(
    ["passed", "breached", "not_established", "unmeasurable"]
)
_HELD_REASON: Final = {
    "autocorrelated_rounds": (
        "the lag-1 autocorrelation of the log ratios is above 0.1 with "
        "too few blocks for the batch-means interval's coverage to be "
        "shown"
    ),
    "outlier_rounds": (
        "the share of outlier rounds is above 3% with too few blocks for "
        "the batch-means interval's coverage to be shown"
    ),
}


def resolve_allowance(
    metric: str,
    *,
    target_class: TargetClass | None = None,
    project: float | None = None,
) -> tuple[float, AllowanceSource] | None:
    """Return a default guardrail's allowance and where it came from.

    The project's own wins over the target class's, which wins over the
    provisional default.

    Parameters
    ----------
    metric : str
        The guardrail's metric name.
    target_class : str, optional
        The workload's target class, if known.
    project : float, optional
        The allowance the project set for this metric, a fraction of at
        least 0, finite.

    Returns
    -------
    tuple of (float, str) or None
        ``(allowance, source)`` with source ``project``,
        ``target_class`` or ``default``; ``None`` when no allowance
        exists for the metric (no default, and none given).

    Raises
    ------
    ValueError
        If ``project`` is not a finite float of at least 0.
    """
    if project is not None:
        if (
            not isinstance(project, float)
            or not math.isfinite(project)
            or project < 0
        ):
            msg = f"project allowance must be a finite float >= 0: {project!r}"
            raise ValueError(msg)
        return project, "project"
    if target_class is not None:
        own = TARGET_CLASS_ALLOWANCES.get(target_class, {}).get(metric)
        if own is not None:
            return own, "target_class"
    default = PROVISIONAL_ALLOWANCES.get(metric)
    return None if default is None else (default, "default")


def check_count(guardrails: Sequence[GuardrailSpec]) -> int:
    """Return ``G``, the allowances and limits checked over the spec.

    Counts one per allowance and one per limit of every applicable
    guardrail; a guardrail marked not applicable checks nothing, and a
    default one the spec does not declare has no entry here.

    Parameters
    ----------
    guardrails : sequence of perfproof.schema.GuardrailSpec
        The spec's guardrails.

    Returns
    -------
    int
        ``G``, at least 0.
    """
    return sum(
        (g.allowance is not None) + len(g.limits)
        for g in guardrails
        if g.applicable
    )


def guardrail_confidence(alpha: float, max_looks: int, checks: int) -> float:
    """Return the per-check confidence ``1 - alpha / (2 K G)``.

    Exactly `perfproof.stats.sequential.look_confidence` for ``K G``
    looks: never below the exact value.

    Parameters
    ----------
    alpha : float
        The spec's ``alpha``, ``0 < alpha < 1``.
    max_looks : int
        ``K``, at least 1.
    checks : int
        ``G``, at least 1.

    Returns
    -------
    float
        The confidence.

    Raises
    ------
    ValueError
        If an argument is out of range.
    """
    if type(checks) is not int or checks < 1:
        msg = f"checks must be an int >= 1, got {checks!r}"
        raise ValueError(msg)
    if type(max_looks) is not int or max_looks < 1:
        msg = f"max_looks must be an int >= 1, got {max_looks!r}"
        raise ValueError(msg)
    return look_confidence(alpha, max_looks * checks)


def guardrail_procedure(
    primary: Procedure, max_looks: int, checks: int
) -> Procedure:
    """Return the interval procedure a spec's guardrails use.

    The primary's own, so a guardrail has the guarantee the schedule's
    procedure was calibrated for, except where ``hac_lf`` has no table
    for the guardrail's confidence: ``K G`` above `HAC_MAX_CHECKS` (the
    last tabulated look count) falls to the gated batch means, which
    has a quantile at any confidence. The spec fixes ``K`` and ``G``
    before any round, so the choice is made once, as the primary's is.

    Parameters
    ----------
    primary : str
        `perfproof.stats.sequential.procedure_for_schedule`'s answer for
        the spec.
    max_looks : int
        ``K``.
    checks : int
        ``G``, at least 1.

    Returns
    -------
    str
        ``"hac_lf"`` or ``"t_log_ratio_batch_means"``.

    Raises
    ------
    ValueError
        If an argument is out of range.
    """
    guardrail_confidence(0.5, max_looks, checks)
    if primary == "hac_lf" and max_looks * checks <= HAC_MAX_CHECKS:
        return "hac_lf"
    return "t_log_ratio_batch_means"


@dataclass(frozen=True, slots=True)
class GuardrailLook:
    """One look of one guardrail, per row.

    Attributes
    ----------
    status : numpy.ndarray of str, shape (R,)
        ``passed``, ``breached``, ``not_established`` or
        ``unmeasurable`` (a resolution too coarse), as under "Status"
        in the module docstring.
    estimate : numpy.ndarray of float64, shape (R,)
        Geometric-mean worsening ratio over the observations the
        interval used; above 1 means worse. NaN where there is no
        interval.
    worsening_lower, worsening_upper : numpy.ndarray of float64
        Shape ``(R,)``. The interval on the worsening ratio after the
        resolution widening; NaN where there is no allowance or no
        interval. Never record a NaN bound.
    candidate_lower, candidate_upper : numpy.ndarray of float64
        Shape ``(R,)``. The order-statistic bounds on the cap's
        statistic of the candidate's own values (`limit_bounds`), in
        the metric's unit; NaN where there are no limits or no bound
        yet.
    held : numpy.ndarray of str, shape (R,)
        ``""``, or the diagnostic (``autocorrelated_rounds`` or
        ``outlier_rounds``) that held the allowance's batch-means look
        back: the interval exists, but its coverage is not shown at
        this length, so the allowance check is open and its interval is
        not recorded. Limit checks are not affected.
    interval : numpy.ndarray of bool, shape (R,)
        Whether every check has an interval.
    checks : numpy.ndarray of str, shape (R, G_g)
        The state of each check of this guardrail: the allowance first
        if there is one, then each limit in the order given. ``within``,
        ``breach``, ``open`` or ``absent``; ``open`` for the allowance
        check of a held row.
    procedure : str
        The interval procedure used.
    typical : numpy.ndarray of float64, shape (R,)
        The sample quantile of the candidate's values; NaN with no
        limit.
    rounds_over : numpy.ndarray of int64, shape (R, G_l)
        Per limit, the rounds whose value is beyond it.
    effective : numpy.ndarray of int64, shape (R,)
        The independent rounds' worth the limit bounds were taken at
        (`LimitBounds`); 0 with no limit.
    quantile : float
        The statistic the limits are on.
    rounds : int
        The rounds of the look.
    """

    status: NDArray[np.str_]
    estimate: FloatArray
    worsening_lower: FloatArray
    worsening_upper: FloatArray
    candidate_lower: FloatArray
    candidate_upper: FloatArray
    held: NDArray[np.str_]
    interval: NDArray[np.bool_]
    checks: NDArray[np.str_]
    procedure: Procedure
    typical: FloatArray
    rounds_over: NDArray[np.int64]
    effective: NDArray[np.int64]
    quantile: float
    rounds: int


def evaluate_guardrail(
    baseline: ArrayLike,
    candidate: ArrayLike,
    *,
    direction: Direction,
    allowance: float | None,
    limits: Sequence[float] = (),
    confidence: float,
    procedure: Procedure,
    final: ArrayLike,
    candidate_first: ArrayLike,
    baseline_step: ArrayLike | None = None,
    candidate_step: ArrayLike | None = None,
    quantile: float = 0.5,
) -> GuardrailLook:
    """Evaluate one guardrail at one look, for rows of one length.

    A look takes ``n`` observations of both arms, the rounds of one
    host, paired by position. Guardrails are one host only: a
    cross-host guardrail has no calibration. Work and memory are those
    of one `decide_look` for the allowance plus one for the limits
    (they share one interval, built on the first limit), so O(R n log
    n) for batch means and O(R n^2) for ``hac_lf``, in chunks bounded
    as that function's are.

    The limit check is on the candidate's own values, whatever the
    baseline did: `limit_bounds` of their ``quantile`` at the same
    confidence. The lower bound over a ceiling is a breach, the upper
    bound at or under it a pass, and a bound that straddles it is not
    established. Every limit is judged on the one pair of bounds, as
    the record does; they are the candidate's recorded interval.

    Parameters
    ----------
    baseline, candidate : array_like of float
        Shape ``(R, n)``: the guarded metric in each arm, positive and
        finite, in the metric's unit. Rounds missing a value are the
        caller's to handle: the guardrail is unmeasurable, never
        measured on what is left.
    direction : {"lower_is_better", "higher_is_better"}
        The metric's direction.
    allowance : float or None
        The relative allowance, a finite float at least 0, or ``None``.
    limits : sequence of float
        Absolute limits, positive and finite: ceilings when lower is
        better, floors when higher is. Together with ``allowance`` at
        least one check.
    confidence : float
        Two-sided confidence of every check, from
        `guardrail_confidence`.
    procedure : str
        From `guardrail_procedure`.
    final : array_like of bool
        Broadcast to ``(R,)``: whether this is each row's last look.
    candidate_first : array_like of bool
        Shape ``(R, n)``: True where the candidate ran first in the
        round, as for `decide_look`.
    baseline_step, candidate_step : array_like of float, optional
        The resolution step of each arm's values, broadcast to
        ``(R, n)``, in the metric's unit; both or neither. ``None``
        means the values are exact. They widen the allowance's interval
        only: a limit is judged on order statistics of the measured
        values themselves, which a step does not bias.
    quantile : float, default 0.5
        The statistic the limits are on, in ``(0, 1)``: the median
        unless a project declares another, such as 0.99.

    Returns
    -------
    GuardrailLook
        Status, interval and per-check states for every row.

    Raises
    ------
    ValueError
        If the arguments are invalid (see `decide_look`), there is no
        check, a limit is not positive and finite, the allowance is
        not a finite float of at least 0, or only one step is given.
    """
    limit_values = _checked_checks(allowance, limits)
    if not (isinstance(quantile, float) and 0.0 < quantile < 1.0):
        msg = f"quantile must be a float in (0, 1), got {quantile!r}"
        raise ValueError(msg)
    b = np.asarray(baseline, dtype=np.float64)
    c = np.asarray(candidate, dtype=np.float64)
    if b.ndim != 2 or b.shape != c.shape:
        msg = (
            f"baseline and candidate must be shaped (R, n) alike, got "
            f"{b.shape} and {c.shape}"
        )
        raise ValueError(msg)
    if (baseline_step is None) != (candidate_step is None):
        msg = "give both resolution steps or neither"
        raise ValueError(msg)
    rows = b.shape[0]
    lower = direction == "lower_is_better"
    states: list[NDArray[np.str_]] = []
    worsening_lo = worsening_hi = np.full(rows, np.nan)
    estimate = np.full(rows, np.nan)
    held = np.full(rows, "", dtype="<U24")
    coarse = np.zeros(rows, dtype=np.bool_)
    interval = np.ones(rows, dtype=np.bool_)

    def look_of(
        num: FloatArray,
        den: FloatArray,
        num_step: ArrayLike | None,
        den_step: ArrayLike | None,
        bar: float,
    ) -> Look:
        resolution = (
            None
            if num_step is None or den_step is None
            else Resolution(
                baseline_step=num_step,
                candidate_step=den_step,
                check_continuity=False,
            )
        )
        return decide_look(
            num,
            den,
            direction="lower_is_better",
            confidence=confidence,
            min_effect=bar,
            calibrated=True,
            final=final,
            resolution=resolution,
            procedure=procedure,
            candidate_first=candidate_first,
        )

    if allowance is not None:
        num, den = (c, b) if lower else (b, c)
        steps = (
            (candidate_step, baseline_step)
            if lower
            else (baseline_step, candidate_step)
        )
        look = look_of(num, den, steps[0], steps[1], allowance)
        worsening_lo, worsening_hi = look.lower, look.upper
        held = np.where(look.withheld != "", look.withheld, held)
        coarse |= look.rule == 1
        has = np.isfinite(look.lower) & np.isfinite(look.upper)
        interval &= has
        states.append(_relative_state(look, 1.0 + allowance, has))
        estimate = _estimate(num, den, procedure, has)
    candidate_lo = candidate_hi = np.full(rows, np.nan)
    typical = np.full(rows, np.nan)
    rounds_over = np.zeros((rows, len(limit_values)), dtype=np.int64)
    effective = np.zeros(rows, dtype=np.int64)
    if limit_values:
        bounds = limit_bounds(
            c,
            quantile=quantile,
            tail=(1.0 - confidence) / 2.0,
            candidate_first=np.asarray(candidate_first),
        )
        candidate_lo, candidate_hi = bounds.lower, bounds.upper
        typical, effective = bounds.typical, bounds.effective
        has = np.isfinite(bounds.lower) & np.isfinite(bounds.upper)
        interval &= has
        states.extend(
            _limit_state(candidate_lo, candidate_hi, value, lower, has)
            for value in limit_values
        )
        rounds_over = np.stack(
            [
                np.sum(c > value if lower else c < value, axis=1)
                for value in limit_values
            ],
            axis=1,
        ).astype(np.int64)
    checks = np.stack(states, axis=1)
    return _summary(
        checks=checks,
        held=held,
        coarse=coarse,
        interval=interval,
        estimate=estimate,
        worsening=(worsening_lo, worsening_hi),
        candidate=(candidate_lo, candidate_hi),
        procedure=procedure,
        typical=typical,
        rounds_over=rounds_over,
        effective=effective,
        quantile=quantile,
        rounds=b.shape[1],
    )


@cache
def rank_bounds(n: int, quantile: float, tail: float) -> tuple[int, int]:
    """Return the order statistics that bound a quantile, exactly.

    For ``n`` independent values from any distribution, the ``s``-th
    smallest is below the ``quantile`` of the distribution with
    probability at most ``tail`` when ``s`` is the largest index with
    ``P(Bin(n, q) <= s - 1) <= tail``, and the ``r``-th smallest is
    above it with probability at most ``tail`` when ``r`` is the least
    index with ``P(Bin(n, q) >= r) <= tail``, ``q`` the quantile. They
    need no assumption about the distribution (ties and outliers cost
    nothing, and only make the bounds wider). The probabilities are
    summed in exact rationals, so no rounding decides an index.

    Parameters
    ----------
    n : int
        Values, at least 1.
    quantile : float
        The quantile, in ``(0, 1)``; 0.5 is the median.
    tail : float
        The error allowed on each side, in ``(0, 0.5)``.

    Returns
    -------
    tuple of (int, int)
        ``(s, r)``, 1-based. ``s`` is 0 when no order statistic bounds
        the quantile from below at this ``n`` (too few values), and
        ``r`` is ``n + 1`` when none does from above.

    Raises
    ------
    ValueError
        If an argument is out of range.
    """
    if type(n) is not int or n < 1:
        msg = f"n must be an int >= 1, got {n!r}"
        raise ValueError(msg)
    if not (isinstance(quantile, float) and 0.0 < quantile < 1.0):
        msg = f"quantile must be a float in (0, 1), got {quantile!r}"
        raise ValueError(msg)
    if not (isinstance(tail, float) and 0.0 < tail < 0.5):
        msg = f"tail must be a float in (0, 0.5), got {tail!r}"
        raise ValueError(msg)
    q, t = Fraction(quantile), Fraction(tail)
    pmf = [math.comb(n, i) * q**i * (1 - q) ** (n - i) for i in range(n + 1)]
    upper, above = n + 1, Fraction(0)
    for r in range(n, 0, -1):
        above += pmf[r]
        if above > t:
            break
        upper = r
    lower, below = 0, Fraction(0)
    for s in range(1, n + 1):
        below += pmf[s - 1]
        if below > t:
            break
        lower = s
    return lower, upper


@dataclass(frozen=True, slots=True)
class LimitBounds:
    """Rank bounds on the cap's statistic of a candidate's values.

    Attributes
    ----------
    lower, upper : numpy.ndarray of float64, shape (R,)
        Bounds on the quantile, each wrong with probability at most
        ``tail``; NaN where there are too few effective rounds for a
        bound on either side (both are NaN together).
    typical : numpy.ndarray of float64, shape (R,)
        The sample quantile of all the values.
    effective : numpy.ndarray of int64, shape (R,)
        The rounds' worth of independent information each row's bounds
        were taken at: ``n`` for independent rounds, fewer as the ranks
        are dependent.
    """

    lower: FloatArray
    upper: FloatArray
    typical: FloatArray
    effective: NDArray[np.int64]


def limit_bounds(
    values: ArrayLike,
    *,
    quantile: float,
    tail: float,
    candidate_first: ArrayLike,
) -> LimitBounds:
    """Bound a quantile of the candidate's own values, free of shape.

    The statistic an absolute limit is on is the ``quantile`` (the
    median unless a project declares another) of the candidate's values.
    The bounds are order statistics of the sorted values: the indices
    `rank_bounds` gives for ``n'`` independent values, mapped back to
    the ``n`` values by scale. ``n' = floor(n / kappa)`` with
    ``kappa = (1 + rho) / (1 - rho)``, the variance inflation of the
    mean of an AR(1) series, and ``rho`` the upper confidence bound
    (`RANK_UCB_Z` standard errors, clipped at `RANK_MAX_CORRELATION`)
    on the lag-1 autocorrelation of the values' ranks (average ranks,
    so ties add no dependence; each round centred on the mean rank of
    its order, as `perfproof.stats.sequential.lag1_autocorrelation`
    does).
    Independent rounds are taken as they are; dependent ones widen the
    bound as if there were fewer of them. Averaging rounds into blocks
    instead would estimate something else under an order effect or a
    mixture, whose block averages sit between the modes. Outliers and
    slow modes move an order statistic only by changing its rank.
    O(R n log n).

    Parameters
    ----------
    values : array_like of float
        Shape ``(R, n)``, positive and finite: the candidate's metric,
        one round per column, in the order run.
    quantile : float
        In ``(0, 1)``.
    tail : float
        The error on each side, ``(1 - confidence) / 2`` of the
        guardrail's confidence.
    candidate_first : array_like of bool
        Shape ``(R, n)``: True where the candidate ran first.

    Returns
    -------
    LimitBounds
        Bounds, typical value and block length per row.

    Raises
    ------
    ValueError
        If the values are not a positive finite ``(R, n)`` array, or
        the quantile or tail is out of range.
    """
    v = np.asarray(values, dtype=np.float64)
    first = np.asarray(candidate_first)
    if v.ndim != 2 or v.shape[1] < 1:
        msg = f"values must be shaped (R, n) with n >= 1, got {v.shape}"
        raise ValueError(msg)
    if not np.all(np.isfinite(v) & (v > 0)):
        msg = "values must be finite and positive"
        raise ValueError(msg)
    if first.dtype != np.bool_ or first.shape != v.shape:
        msg = f"candidate_first must be a boolean array shaped {v.shape}"
        raise ValueError(msg)
    rows, n = v.shape
    rank_bounds(1, quantile, tail)
    typical = np.quantile(v, quantile, axis=1)
    lower = np.full(rows, np.nan)
    upper = np.full(rows, np.nan)
    effective = np.zeros(rows, dtype=np.int64)
    if n < 2:
        return LimitBounds(lower, upper, typical, effective)
    ranks = np.asarray(rankdata(v, axis=1), dtype=np.float64)  # pyright: ignore[reportUnknownArgumentType]
    lag1 = np.nan_to_num(lag1_autocorrelation(ranks, first), nan=0.0)
    ucb = lag1 + RANK_UCB_Z * np.sqrt((1.0 - lag1**2) / n)
    rho = np.clip(ucb, 0.0, RANK_MAX_CORRELATION)
    kappa = (1.0 + rho) / (1.0 - rho)
    effective = np.floor(n / kappa).astype(np.int64)
    ordered = np.sort(v, axis=1)
    for count in np.unique(effective).tolist():
        if count < 1:
            continue
        low, high = rank_bounds(count, quantile, tail)
        if low < 1 or high > count:
            continue
        rows_here = np.flatnonzero(effective == count)
        s_full = max(1, (low * n) // count)
        r_full = min(n, -((-high * n) // count))
        lower[rows_here] = ordered[rows_here, s_full - 1]
        upper[rows_here] = ordered[rows_here, r_full - 1]
    return LimitBounds(lower, upper, typical, effective)


def _standing(
    spec: GuardrailSpec,
    look: GuardrailLook,
    row: int,
    candidate: Interval | None,
) -> tuple[LimitStanding, ...]:
    """Where the candidate stands against each limit of the guardrail.

    ``reliable_value`` and ``gap`` are ``None`` until a bound exists.
    ``reliable_value`` is the bound
    on the cap's side of the recorded interval, so it is within the cap
    exactly when the limit is shown met.
    """
    if not spec.limits:
        return ()
    ceiling = spec.direction == "lower_is_better"
    reliable = (
        None
        if candidate is None
        else candidate.upper
        if ceiling
        else candidate.lower
    )
    quantile = 0.5 if spec.limit_quantile is None else spec.limit_quantile
    return tuple(
        LimitStanding(
            label=limit.label,
            limit=limit.value,
            quantile=quantile,
            typical=float(look.typical[row]),
            reliable_value=reliable,
            gap=None if reliable is None else reliable - limit.value,
            rounds_over=int(look.rounds_over[row, k]),
            rounds=look.rounds,
        )
        for k, limit in enumerate(spec.limits)
    )


def _checked_checks(
    allowance: float | None, limits: Sequence[float]
) -> tuple[float, ...]:
    """Validate the allowance and limits; return the limits."""
    if allowance is not None and (
        not isinstance(allowance, float)
        or not math.isfinite(allowance)
        or allowance < 0
    ):
        msg = f"allowance must be a finite float >= 0 or None: {allowance!r}"
        raise ValueError(msg)
    values: list[float] = []
    for limit in limits:
        if (
            not isinstance(limit, float)
            or not math.isfinite(limit)
            or limit <= 0
        ):
            msg = f"a limit must be a finite float > 0, got {limit!r}"
            raise ValueError(msg)
        values.append(limit)
    if allowance is None and not values:
        msg = "a guardrail needs an allowance, a limit, or both"
        raise ValueError(msg)
    return tuple(values)


def _relative_state(
    look: Look, bar: float, has: NDArray[np.bool_]
) -> NDArray[np.str_]:
    """Return the allowance check's state from the look's bounds.

    The look's own rule is not used: it is 4 or 5 where the gate holds
    a decisive interval, and the check is then open however the bounds
    lie. The comparisons are those of `interval_rules`, and of the
    schema on the recorded interval: within is ``U <= 1 + a``, breach
    ``L > 1 + a``.
    """
    held = look.withheld != ""
    with np.errstate(invalid="ignore"):
        within = look.upper <= bar
        breach = look.lower > bar
    return _states(within, breach, has, held)


def _limit_state(
    lower: FloatArray,
    upper: FloatArray,
    limit: float,
    ceiling: bool,
    has: NDArray[np.bool_],
) -> NDArray[np.str_]:
    """Return one limit's state from the candidate interval.

    The schema's comparisons: a ceiling is within when the upper bound
    is at most the limit and breached when the lower bound exceeds it;
    a floor is within when the lower bound is at least the limit and
    breached when the upper bound is below it. The gate is not applied
    here: a held allowance does not open a limit, which is judged on
    order statistics, not on the batch-means interval the gate guards.
    """
    with np.errstate(invalid="ignore"):
        if ceiling:
            within, breach = upper <= limit, lower > limit
        else:
            within, breach = lower >= limit, upper < limit
    return _states(within, breach, has, np.zeros(has.shape, dtype=np.bool_))


def _states(
    within: NDArray[np.bool_],
    breach: NDArray[np.bool_],
    has: NDArray[np.bool_],
    held: NDArray[np.bool_],
) -> NDArray[np.str_]:
    state = np.where(within, "within", np.where(breach, "breach", "open"))
    state = np.where(has, state, "absent")
    return np.where(held & has, "open", state).astype("<U8")


def _estimate(
    num: FloatArray,
    den: FloatArray,
    procedure: Procedure,
    has: NDArray[np.bool_],
) -> FloatArray:
    """Geometric mean of the ratios the interval used, NaN where none.

    Batch means drops a trailing incomplete block, as the interval does.
    """
    n = num.shape[1]
    used = n if procedure == "hac_lf" else (n // BATCH_BLOCK) * BATCH_BLOCK
    if used == 0:
        return np.full(num.shape[0], np.nan)
    ratios = paired_ratios(num[:, :used], den[:, :used], "lower_is_better")
    with np.errstate(over="ignore", under="ignore"):
        mean = np.exp(np.mean(np.log(ratios), axis=1))
    return np.where(has, mean, np.nan)


def _summary(
    *,
    checks: NDArray[np.str_],
    held: NDArray[np.str_],
    coarse: NDArray[np.bool_],
    interval: NDArray[np.bool_],
    estimate: FloatArray,
    worsening: tuple[FloatArray, FloatArray],
    candidate: tuple[FloatArray, FloatArray],
    procedure: Procedure,
    typical: FloatArray,
    rounds_over: NDArray[np.int64],
    effective: NDArray[np.int64],
    quantile: float,
    rounds: int,
) -> GuardrailLook:
    """Combine the checks into the guardrail's status."""
    breach = np.any(checks == "breach", axis=1)
    within = np.all(checks == "within", axis=1)
    code = np.where(breach, 1, np.where(within, 0, 2))
    code = np.where(coarse, 3, code)
    return GuardrailLook(
        status=_STATUS_CODES[code].astype(np.str_),
        estimate=estimate,
        worsening_lower=worsening[0],
        worsening_upper=worsening[1],
        candidate_lower=candidate[0],
        candidate_upper=candidate[1],
        held=held.astype(np.str_),
        interval=interval,
        checks=checks.astype(np.str_),
        procedure=procedure,
        typical=typical,
        rounds_over=rounds_over,
        effective=effective,
        quantile=quantile,
        rounds=rounds,
    )


def earned_status(
    spec: GuardrailSpec,
    worsening: Interval | None,
    candidate: Interval | None,
) -> GuardrailStatus:
    """Return the status that recorded intervals earn.

    `perfproof.schema.RunRecord` refuses a guardrail whose status is not
    the one its recorded intervals give against the spec's allowance and
    limits. This is that reading: ``passed`` when every check is within,
    ``breached`` when some check is breached, ``not_established`` when
    neither and at least one check's interval was recorded, and
    ``unmeasurable`` when no check has an interval. Compliance is
    inclusive and a breach strict, compared exactly in float64.

    Parameters
    ----------
    spec : perfproof.schema.GuardrailSpec
        An applicable guardrail.
    worsening, candidate : perfproof.schema.Interval or None
        The intervals as recorded.

    Returns
    -------
    str
        The status.

    Raises
    ------
    ValueError
        If the guardrail is not applicable.
    """
    if not spec.applicable:
        msg = f"guardrail {spec.metric} is not applicable"
        raise ValueError(msg)
    checks: list[CheckState] = []
    if spec.allowance is not None:
        bar = 1 + spec.allowance
        checks.append(
            "absent"
            if worsening is None
            else _three(worsening.upper <= bar, worsening.lower > bar)
        )
    for limit in spec.limits:
        if candidate is None:
            checks.append("absent")
        elif spec.direction == "lower_is_better":
            checks.append(
                _three(
                    candidate.upper <= limit.value,
                    candidate.lower > limit.value,
                )
            )
        else:
            checks.append(
                _three(
                    candidate.lower >= limit.value,
                    candidate.upper < limit.value,
                )
            )
    if "breach" in checks:
        return "breached"
    if all(c == "within" for c in checks):
        return "passed"
    if any(c != "absent" for c in checks):
        return "not_established"
    return "unmeasurable"


def _three(within: bool, breach: bool) -> CheckState:
    return "within" if within else "breach" if breach else "open"


def guardrail_result(
    spec: GuardrailSpec,
    look: GuardrailLook,
    row: int,
    confidence: float,
) -> GuardrailResult:
    """Build the record's `GuardrailResult` for one row of a look.

    The status and intervals are the look's, unless the schema would
    read the intervals as another status: an allowance interval the
    gate held is not recorded (the schema would read it as ``passed`` or
    ``breached``), so the allowance check is open and a limit's bounds
    still decide: breached over the cap, else ``not_established``. With
    no limit, or no interval at all, the guardrail is recorded
    ``unmeasurable`` with the reason, so the record never says what its
    evidence does not. A status that the intervals do not earn for any
    other reason is a bug, and raises.

    Parameters
    ----------
    spec : perfproof.schema.GuardrailSpec
        An applicable guardrail.
    look : GuardrailLook
        From `evaluate_guardrail` for this spec.
    row : int
        The row of ``look`` to record.
    confidence : float
        The intervals' confidence, the one the look used.

    Returns
    -------
    perfproof.schema.GuardrailResult
        The result, valid by the schema's own checks.

    Raises
    ------
    ValueError
        If the guardrail is not applicable or ``row`` is out of range.
    """
    if not spec.applicable:
        msg = f"guardrail {spec.metric} is not applicable"
        raise ValueError(msg)
    status = str(look.status[row])
    held = str(look.held[row])
    if status == "unmeasurable":
        return GuardrailResult(
            metric=spec.metric,
            status="unmeasurable",
            reason=(
                "a measured value is no larger than its resolution, so "
                "the interval cannot be widened for it"
            ),
        )
    worsening = _interval(
        look.worsening_lower[row], look.worsening_upper[row], confidence
    )
    candidate = _interval(
        look.candidate_lower[row], look.candidate_upper[row], confidence
    )
    if spec.allowance is None or held:
        worsening = None
    if not spec.limits:
        candidate = None
    earned = earned_status(spec, worsening, candidate)
    if (held and not spec.limits) or earned == "unmeasurable":
        reason = (
            _HELD_REASON[held]
            if held
            else "no interval could be computed from the rounds taken"
        )
        return GuardrailResult(
            metric=spec.metric,
            status="unmeasurable",
            reason=reason,
            standing=_standing(spec, look, row, candidate),
        )
    if earned != status:
        msg = (
            f"guardrail {spec.metric}: the look says {status} but its "
            f"intervals earn {earned}"
        )
        raise ValueError(msg)
    estimate = float(look.estimate[row])
    return GuardrailResult(
        metric=spec.metric,
        status=earned,
        estimate=None if worsening is None else estimate,
        worsening=worsening,
        candidate=candidate,
        standing=_standing(spec, look, row, candidate),
    )


def _interval(
    lower: float, upper: float, confidence: float
) -> Interval | None:
    if not (math.isfinite(lower) and math.isfinite(upper)):
        return None
    if lower <= 0 or upper < lower:
        return None
    return Interval(lower=lower, upper=upper, confidence=confidence)
