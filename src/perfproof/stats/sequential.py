"""Sequential looks: the look schedule and the driver that decides.

"Stopping" in ``docs/method.md`` fixes the schedule before the first
round. The first look comes after ``n0`` rounds, then one every ``k``
rounds, for at most ``K`` looks (`perfproof.schema.LookSchedule`, with
``K = max_looks``). Everything here works on a leading replication
axis, so one call decides ``R`` simulated comparisons, and ``R = 1``
is one real comparison.

- **Complete order pairs.** On one host rounds come in order pairs
  (``docs/method.md``, "Observations"), so ``n0`` and ``k`` must be
  even and every scheduled look falls on complete pairs. Cross host
  an observation is a completed session pair and no parity applies.
- **Budget.** If the budget runs out between looks, a final look is
  taken at the complete order pairs collected so far (an unpaired last
  round dropped), and it replaces the remaining scheduled looks, so
  there are never more than ``K``. A budget that ends before ``n0``
  gives no look at all and the comparison is INCONCLUSIVE. See
  `plan_looks`.
- **Confidence.** Every look's two-sided interval is at confidence
  ``1 - alpha / (2 K)`` (`look_confidence`).
- **Resolution.** Each round's log ratio is known only to within
  ``e_i = -log(1 - q_b / b_i) - log(1 - q_c / c_i)`` for the values'
  resolutions ``q``, so both bounds of every interval are widened
  outward by the mean ``e_i`` of the rounds it used
  (`round_error_bounds`, `widen`); cross host, each session pair's
  observation is bounded by the mean bounds of its two sessions'
  rounds (`session_pair_error`, passed as `ObservationBounds`). The
  resolution stop ends the comparison INCONCLUSIVE with
  ``coarse_resolution`` when a value is no larger than its step, or,
  for a ``reported`` metric with no declared resolution, when either
  arm has fewer distinct values than half its rounds
  (`continuity_fails`). Both apply in both host modes.

- **Interval.** The primary is batch means: the mean log ratios of
  blocks of `BATCH_BLOCK` consecutive rounds (cross host, session
  pairs) are the observations of a Student t interval. Fewer than two
  complete blocks give no interval: the look neither keeps nor
  rejects, it continues, and at its last look it is INCONCLUSIVE.
- **Diagnostic gate.** Two diagnostics of the log ratios withhold a
  KEEP or a performance REJECT, with the look continuing (or
  INCONCLUSIVE at the last look), while the look has fewer blocks
  than the diagnostic needs: a lag-1 autocorrelation above
  `LAG1_LIMIT` (fewer than `LAG1_MIN_BLOCKS` blocks) and an outlier
  share above `OUTLIER_SHARE` (fewer than `OUTLIER_MIN_BLOCKS`
  blocks). Where the interval's coverage is not shown, the decision
  waits for the length at which calibration showed it holds. See
  `Look.withheld`.

Every look is decided by `decide_look`, in the order of the decision
rule in ``docs/method.md``: the resolution stop, then the widening,
then rules 2 to 5 (and the diagnostic gate on rules 2 and 3), decided
only by
`perfproof.stats.decision.interval_rules` with the look's widened
bounds; the bar comparison is never repeated here. `run_looks` drives
the looks over a batch; a runner that takes its own looks calls
`decide_look` for each, so that neither the resolution stop nor the
widening can be skipped. The comparison stops at the first look whose
action is not ``CONTINUE``, and at its last look (the ``K``-th, or the
budget's final look) rule 5 makes it INCONCLUSIVE.

Guardrails can turn a KEEP into a continue. Per "Guardrails" in
``docs/method.md``, when the interval clears the bar (rule 2) but some
guardrail is ``not_established`` or ``unmeasurable``, the comparison
continues while budget remains and is INCONCLUSIVE at its last look.
The guardrail statuses are not computed here: the caller passes them
as ``keep_blocked``, and a held rule 2 is marked in `Look.held`. A
guardrail breach (REJECT, ``guardrail``) and a gate that is
``not_available`` (INCONCLUSIVE at that look) also act only on a rule
2 look; the caller applies them to the look's result, as
`perfproof.stats.decision.decide` does for the gate. Rule 1 is not
decided here either: gates run before any timing, so a failed gate is
decided by `perfproof.stats.decision.decide` before the first round,
and every look here assumes the gates passed.

Numerics of the resolution widening
-----------------------------------
The widening follows "Numerics" in ``docs/method.md``: no bound may
come out more favourable to the candidate than its exact value through
rounding. With ``u = 2**-53``, correctly rounded ``+ - * /`` and
`perfproof.stats.intervals.LIBM_ULPS` as the assumed error of NumPy's
``exp`` and ``log1p`` (the same assumption the intervals make for
``exp`` and ``log``; the tests check ``log1p`` against decimal
references):

1. ``x = fl(q / v)`` is within half an ulp of ``q / v``, so the next
   float up, ``x+``, is at least ``q / v``. ``-log(1 - t)`` increases
   with ``t``, so ``-log1p(-x+)`` is at least the exact per-value
   bound. libm returns ``g`` within ``LIBM_ULPS`` ulps of it, and an
   ulp is at most ``2u`` times the value, so
   ``g (1 + 5 * 2**-52)``, rounded once and raised by five of the
   smallest subnormals (which cover the subnormal range, where the
   relative argument fails), is not below it. A step of 0 gives
   exactly 0. ``x+ >= 1`` means the value is within rounding of its
   step and the bound is infinite; the resolution stop treats it like
   a value at its step.
2. ``e_i`` is the sum of the two arms' bounds, rounded up as (P) in
   `perfproof.stats.intervals` with one rounding, and the mean of
   ``n`` of them has at most ``n`` more roundings on any path, so
   ``_up(sum / n, n)`` is not below the exact mean of the bounds.
3. Widening by ``a``: ``f = fl(exp(-a))`` satisfies
   ``exp(-a) >= f (1 - 2 LIBM_ULPS u)``, and ``fl(L f)`` is at most
   ``L f (1 + u)``, so ``fl(fl(L f) (1 - (2 LIBM_ULPS + 4) u))`` is at
   most ``L exp(-a)``. The upper bound mirrors it with ``exp(a)`` and
   ``1 + (2 LIBM_ULPS + 4) u``. A widened lower bound below the
   smallest normal float is returned as ``0.0``, as
   `perfproof.stats.intervals` does for its own bounds.

A mean bound of exactly 0 (every step 0) leaves the bounds untouched,
so the exact interval of identical ratios stays exact.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

import numpy as np
from numpy.typing import ArrayLike, NDArray

from perfproof.schema import Direction, LookSchedule, Procedure
from perfproof.stats.decision import interval_rules
from perfproof.stats.intervals import (
    LIBM_ULPS,
    PairedInterval,
    paired_ratios,
    t_interval,
)
from perfproof.stats.simulate import HostMode

__all__ = [
    "BATCH_BLOCK",
    "DEFAULT_ALPHA",
    "LAG1_LIMIT",
    "LAG1_MIN_BLOCKS",
    "OUTCOMES",
    "OUTLIER_MIN_BLOCKS",
    "OUTLIER_SHARE",
    "OUTLIER_Z",
    "Look",
    "LookPlan",
    "ObservationBounds",
    "Resolution",
    "SequentialResult",
    "continuity_fails",
    "decide_look",
    "lag1_autocorrelation",
    "look_confidence",
    "outlier_share",
    "plan_looks",
    "round_error_bounds",
    "run_looks",
    "session_pair_error",
    "widen",
]

FloatArray = NDArray[np.float64]

DEFAULT_ALPHA: Final = 0.05
"""``alpha`` of ``docs/method.md``: the error the promise allows."""
BATCH_BLOCK: Final = 6
"""Rounds per block of the primary, batch means, in ``docs/method.md``:
three order pairs on one host, six session pairs cross host."""
LAG1_LIMIT: Final = 0.1
"""Lag-1 autocorrelation of the log ratios above which a KEEP or a
performance REJECT needs `LAG1_MIN_BLOCKS` blocks. Fixed in
``docs/method.md`` ("Diagnostic gate"); lower than the 0.3 at which
the record warns, because cross host the session offsets dilute the
autocorrelation the log ratios show."""
LAG1_MIN_BLOCKS: Final = 20
"""Blocks of `BATCH_BLOCK` rounds a look needs to decide when the
lag-1 autocorrelation is above `LAG1_LIMIT`."""
OUTLIER_Z: Final = 6.0
"""A round is an outlier when its log ratio is more than this many
robust standard deviations (1.4826 times the median absolute deviation)
from the median of its order."""
OUTLIER_SHARE: Final = 0.03
"""Share of outlier rounds above which a KEEP or a performance REJECT
needs `OUTLIER_MIN_BLOCKS` blocks."""
OUTLIER_MIN_BLOCKS: Final = 10
"""Blocks of `BATCH_BLOCK` rounds a look needs to decide when the
outlier share is above `OUTLIER_SHARE`."""
OUTCOMES: Final = ("KEEP", "REJECT", "INCONCLUSIVE", "CONTINUE")
"""The values `SequentialResult.outcome` takes."""

_U: Final = 2.0**-53
_ULP1: Final = 2.0**-52
_LOG_UP: Final = 1.0 + (LIBM_ULPS + 1) * _ULP1
_SUBNORMAL_PAD: Final = 5 * 2.0**-1074
_WIDEN_DOWN: Final = 1.0 - (2 * LIBM_ULPS + 4) * _U
_WIDEN_UP: Final = 1.0 + (2 * LIBM_ULPS + 4) * _U
_TINY_NORMAL: Final = float(np.finfo(np.float64).tiny)
_MIN_N0: Final = 10
_MODES: Final = ("one_host", "cross_host")
_DIRECTIONS: Final = ("lower_is_better", "higher_is_better")
_PROCEDURES: Final = ("t_log_ratio", "t_log_ratio_batch_means")
_MAD_TO_SD: Final = 1.482602218505602
_WITHHELD: Final = np.array(["", "autocorrelated_rounds", "outlier_rounds"])
_EXACT_FLOATS: Final = frozenset(
    np.dtype(t) for t in (np.float16, np.float32, np.float64)
)
# Indexed by rule: 1 is the resolution stop, 2 to 5 the decision rule.
_ACTION_BY_RULE: Final = np.array(
    ["", "INCONCLUSIVE", "KEEP", "REJECT", "CONTINUE", "INCONCLUSIVE"]
)


@dataclass(frozen=True, slots=True)
class LookPlan:
    """Where each replication looks, given the rounds it collected.

    Attributes
    ----------
    rounds : numpy.ndarray of int64, shape (R, K)
        Rounds (cross host: session pairs) each look uses; 0 where
        that look is not taken. Looks taken are the leading entries of
        each row.
    final : numpy.ndarray of bool, shape (R, K)
        True at the last look a replication can take: the ``K``-th
        look, or the look at which the budget ran out. No budget
        remains there, so rule 5 applies if the interval straddles.
    exhausted : numpy.ndarray of bool, shape (R,)
        Whether the budget ran out, as given; with no look taken, it
        tells INCONCLUSIVE (the budget ended before ``n0``) from
        continue.
    """

    rounds: NDArray[np.int64]
    final: NDArray[np.bool_]
    exhausted: NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class Resolution:
    """The resolution of every value, as under "Resolution".

    Attributes
    ----------
    baseline_step, candidate_step : array_like of float
        The step ``q`` of each value, in the values' unit, finite and
        at least 0; broadcast to the measurements' shape ``(R, n)``, so
        a scalar gives every value the same step. 0 means the value is
        exact.
    check_continuity : bool
        True for a ``reported`` metric with no declared resolution,
        whose steps come from the printed digits: the resolution stop
        then also fires when either arm has fewer distinct values than
        half its rounds.
    """

    baseline_step: ArrayLike
    candidate_step: ArrayLike
    check_continuity: bool


@dataclass(frozen=True, slots=True)
class ObservationBounds:
    """Resolution bounds per observation, computed by the caller.

    The form the resolution takes cross host, where an observation is a
    session pair and its bound comes from the rounds inside its two
    sessions (`session_pair_error`); it serves on one host too.

    Attributes
    ----------
    errors : array_like of float
        Shape ``(R, n)``, like the measurements: an upper bound on how
        far each observation's log ratio can be from its exact value,
        at least 0. ``inf`` where no finite bound exists (a value no
        larger than its step), which the resolution stop treats as
        coarse.
    coarse : array_like of bool, default False
        Broadcast to ``(R, n)``: True at observation ``j`` when a look
        that uses it must stop for a reason the bound does not carry,
        such as the continuity check of a ``reported`` metric failing
        on the rounds through ``j``.
    """

    errors: ArrayLike
    coarse: ArrayLike = False


@dataclass(frozen=True, slots=True)
class Look:
    """One look's decision, per row.

    Attributes
    ----------
    lower, upper : numpy.ndarray of float64, shape (R,)
        ``L`` and ``U`` after the resolution widening, bit-identical to
        what went into `perfproof.stats.decision.interval_rules`; NaN
        where the resolution stop fired, and cross host below ``n0``
        where an observation is unbounded or coarse (rule 4 or 5). A
        NaN bound is not an interval and must never be recorded as one.
    rule : numpy.ndarray of int8, shape (R,)
        1 where the resolution stop fired (``coarse_resolution``),
        otherwise rule 2, 3, 4 or 5 from
        `perfproof.stats.decision.interval_rules`.
    regression : numpy.ndarray of bool, shape (R,)
        A performance REJECT with ``U < 1``.
    held : numpy.ndarray of bool, shape (R,)
        Rule 2 fired but ``keep_blocked`` held it back from KEEP.
    withheld : numpy.ndarray of str, shape (R,)
        ``""`` where no diagnostic withheld the look's decision;
        ``"autocorrelated_rounds"`` or ``"outlier_rounds"`` where the
        interval would have given rule 2 or 3 but the diagnostic gate
        (module docstring) held the look to rule 4, or 5 at its last
        look. A withheld look records the interval it computed, and
        its ``regression`` flag is False.
    action : numpy.ndarray of str, shape (R,)
        One of `OUTCOMES`: what the comparison does after this look.
    """

    lower: FloatArray
    upper: FloatArray
    rule: NDArray[np.int8]
    regression: NDArray[np.bool_]
    held: NDArray[np.bool_]
    withheld: NDArray[np.str_]
    action: NDArray[np.str_]


@dataclass(frozen=True, slots=True)
class SequentialResult:
    """Every look of every replication, and how each comparison ended.

    ``R`` is the number of replications and ``K`` the schedule's
    ``max_looks``. Slot ``j`` is the ``j``-th look taken.

    Attributes
    ----------
    rounds : numpy.ndarray of int64, shape (R, K)
        Rounds (cross host: session pairs) each look used; 0 where no
        look was taken, because the schedule or budget had none there,
        the comparison had stopped, or the rounds given ended first.
        The batch-means interval drops a trailing incomplete block of
        these.
    lower, upper : numpy.ndarray of float64, shape (R, K)
        ``L`` and ``U`` each look decided on, on the ratio scale,
        after the resolution widening; bit-identical to what went into
        `perfproof.stats.decision.interval_rules`. NaN where no look
        was taken, where the resolution stop fired, and cross host below
        ``n0`` where an observation is unbounded or coarse (that look
        has rule 4 or 5). A NaN bound is not an interval and must never
        be recorded as one.
    rule : numpy.ndarray of int8, shape (R, K)
        0 where no look was taken; 1 where the resolution stop fired
        (``coarse_resolution``, checked after rule 1 of the decision
        rule); otherwise rule 2, 3, 4 or 5 from
        `perfproof.stats.decision.interval_rules`.
    regression : numpy.ndarray of bool, shape (R, K)
        The regression flag of each look: a performance REJECT with
        ``U < 1``.
    held : numpy.ndarray of bool, shape (R, K)
        Where rule 2 fired but ``keep_blocked`` held it back.
    withheld : numpy.ndarray of str, shape (R, K)
        Each look's `Look.withheld`; empty where no look was taken.
    action : numpy.ndarray of str, shape (R, K)
        Each look's `Look.action`; empty where no look was taken.
    outcome : numpy.ndarray of str, shape (R,)
        One of `OUTCOMES`. ``CONTINUE`` only when the rounds given
        ended before the comparison's last look, which is how a live
        comparison is called between looks.
    final_rule : numpy.ndarray of int8, shape (R,)
        The rule of the last look taken; with no look, 5 when the
        budget ran out before ``n0`` and 4 otherwise.
    looks : numpy.ndarray of int64, shape (R,)
        Looks taken.
    rounds_used : numpy.ndarray of int64, shape (R,)
        Rounds at the last look taken; 0 with no look.
    confidence : float
        Two-sided confidence of every look's interval.
    procedure : str
        The interval procedure, as in `perfproof.schema.Procedure`.
    """

    rounds: NDArray[np.int64]
    lower: FloatArray
    upper: FloatArray
    rule: NDArray[np.int8]
    regression: NDArray[np.bool_]
    held: NDArray[np.bool_]
    withheld: NDArray[np.str_]
    action: NDArray[np.str_]
    outcome: NDArray[np.str_]
    final_rule: NDArray[np.int8]
    looks: NDArray[np.int64]
    rounds_used: NDArray[np.int64]
    confidence: float
    procedure: Procedure


def look_confidence(alpha: float, max_looks: int) -> float:
    """Return the per-look confidence ``1 - alpha / (2 K)``.

    The Bonferroni split of "Stopping" in ``docs/method.md``. The float
    returned is never below the exact value, so the rounding of the
    expression cannot make an interval narrower than designed: if
    ``fl(1 - alpha / (2 K))`` is below the exact rational
    ``1 - alpha / (2 K)`` of the float ``alpha``, the next float up is
    returned.

    Parameters
    ----------
    alpha : float
        Total error the promise allows, ``0 < alpha < 1``.
    max_looks : int
        ``K``, at least 1.

    Returns
    -------
    float
        The confidence, ``0.5 <= c < 1``.

    Raises
    ------
    ValueError
        If ``alpha`` is not a float in ``(0, 1)`` or ``max_looks`` is
        not an int at least 1.
    """
    if (
        not isinstance(alpha, float)
        or not math.isfinite(alpha)
        or not 0 < alpha < 1
    ):
        msg = f"alpha must be a float in (0, 1), got {alpha!r}"
        raise ValueError(msg)
    _check_int("max_looks", max_looks, 1)
    exact = 1 - Fraction(alpha) / (2 * max_looks)
    conf = 1.0 - alpha / (2 * max_looks)
    if Fraction(conf) < exact:
        conf = math.nextafter(conf, 1.0)
    return conf


def plan_looks(
    schedule: LookSchedule,
    rounds: ArrayLike,
    exhausted: ArrayLike,
    *,
    mode: HostMode,
) -> LookPlan:
    """Place each replication's looks, given what it collected.

    The scheduled looks are at ``n0 + j k`` for ``j < K``. A
    replication that collected ``a`` rounds takes every scheduled look
    at or below ``a``. If its budget ran out (``exhausted``) before
    the ``K``-th look, it also takes a final look at ``a'``, the
    complete order pairs it has (``a`` rounded down to even on one
    host, ``a`` cross host), unless ``a'`` is itself a scheduled look;
    that look takes the place of the remaining ones, so no replication
    has more than ``K`` looks. With ``a' < n0`` there is no look at
    all. O(R K).

    Parameters
    ----------
    schedule : perfproof.schema.LookSchedule
        ``n0`` (at least 10), ``k`` and ``max_looks``. On one host
        ``n0`` and ``k`` must be even.
    rounds : array_like of int, shape (R,)
        Rounds (cross host: completed session pairs) collected per
        replication, at least 0. Never more than the budget's
        ``max_rounds``; the caller stops collecting there.
    exhausted : array_like of bool
        Whether the budget (``max_rounds`` or ``max_seconds``) ran out
        after those rounds, so no more can be collected; broadcast to
        ``rounds``.
    mode : {"one_host", "cross_host"}
        Host mode.

    Returns
    -------
    LookPlan
        Rounds per look, and which look is each replication's last.

    Raises
    ------
    ValueError
        If ``schedule`` is not a `perfproof.schema.LookSchedule` or its
        values are out of range, ``n0`` or ``k`` is odd on one host,
        ``mode`` is unknown, ``rounds`` is not a one-dimensional array
        of non-negative integers, or ``exhausted`` is not boolean or
        does not broadcast to it.
    """
    n0, k, looks = _schedule_values(schedule, mode)
    collected = np.asarray(rounds)
    if (
        collected.ndim != 1
        or not np.issubdtype(collected.dtype, np.integer)
        or np.any(collected < 0)
    ):
        msg = "rounds must be a 1-D array of non-negative integers, got "
        msg += f"dtype {collected.dtype}, shape {collected.shape}"
        raise ValueError(msg)
    done = _bool_array("exhausted", exhausted, collected.shape)
    a = collected.astype(np.int64)
    if mode == "one_host":
        a = a - a % 2
    scheduled = n0 + k * np.arange(looks, dtype=np.int64)
    taken = scheduled[np.newaxis, :] <= a[:, np.newaxis]
    count = taken.sum(axis=1)
    last_scheduled = scheduled[np.maximum(count - 1, 0)]
    extra = done & (count >= 1) & (count < looks) & (a > last_scheduled)
    planned = np.where(taken, scheduled[np.newaxis, :], 0)
    slot = np.arange(looks)[np.newaxis, :]
    at_extra = extra[:, np.newaxis] & (slot == count[:, np.newaxis])
    planned = np.where(at_extra, a[:, np.newaxis], planned)
    total = count + extra
    final = (slot == looks - 1) & (planned > 0)
    final |= done[:, np.newaxis] & (slot == total[:, np.newaxis] - 1)
    return LookPlan(
        rounds=planned.astype(np.int64), final=final, exhausted=done
    )


def round_error_bounds(
    baseline: ArrayLike,
    candidate: ArrayLike,
    baseline_step: ArrayLike,
    candidate_step: ArrayLike,
) -> FloatArray:
    """Bound each round's log-ratio error from the values' resolution.

    ``e_i = -log(1 - q_b / b_i) - log(1 - q_c / c_i)`` from "Resolution"
    in ``docs/method.md``, an exact bound on how far the measured log
    ratio can be from the true one when each value is known only to
    within one step ``q``, since ``|log(1 + x)| <= -log(1 - |x|)``. The
    same for either metric direction. Computed so that the float
    returned is never below the exact ``e_i``; see the module
    docstring. O(R n).

    Parameters
    ----------
    baseline, candidate : array_like of float
        Measurements shaped ``(..., n)``, finite and positive, float64
        or narrower.
    baseline_step, candidate_step : array_like of float
        Each value's step ``q``, finite and at least 0, broadcast to
        the measurements' shape.

    Returns
    -------
    numpy.ndarray of float64
        ``e_i`` per round, shape ``(..., n)``, at least 0; ``inf``
        where a value is no larger than its step, or within rounding
        of it (``fl(q / v)`` one float below 1), where no finite bound
        exists.

    Raises
    ------
    ValueError
        If the measurements differ in shape or are not finite and
        positive, or a step is negative, not finite, or does not
        broadcast.
    """
    b = _values("baseline", baseline)
    c = _values("candidate", candidate)
    if b.shape != c.shape:
        msg = f"baseline shape {b.shape} and candidate shape {c.shape} differ"
        raise ValueError(msg)
    qb = _steps("baseline_step", baseline_step, b.shape)
    qc = _steps("candidate_step", candidate_step, c.shape)
    total = _value_error(qb, b) + _value_error(qc, c)
    return np.where(np.isfinite(total), _up(total, 1), np.inf)


def widen(
    lower: ArrayLike, upper: ArrayLike, mean_error: ArrayLike
) -> tuple[FloatArray, FloatArray]:
    """Widen ratio-scale bounds outward by a log-scale amount.

    Returns ``L exp(-a)`` and ``U exp(a)``, each rounded outward so it
    is never inside its exact value (module docstring). Where ``a`` is
    0 the bounds come back unchanged.

    Parameters
    ----------
    lower, upper : array_like of float
        ``L`` and ``U``, finite and positive, same shape.
    mean_error : array_like of float
        ``a``, finite and at least 0, broadcast to the bounds: the
        upper bound on the mean of ``e_i`` over the rounds used.

    Returns
    -------
    tuple of numpy.ndarray of float64
        The widened ``(L, U)``. A lower bound below the smallest normal
        float comes back as 0.0, and an upper bound beyond the float
        range as ``inf``.

    Raises
    ------
    ValueError
        If a bound is not finite and positive, the shapes differ or do
        not broadcast, or ``a`` is negative or not finite.
    """
    lo = _values("lower", lower)
    hi = _values("upper", upper)
    if lo.shape != hi.shape:
        msg = f"lower shape {lo.shape} and upper shape {hi.shape} differ"
        raise ValueError(msg)
    a = _steps("mean_error", mean_error, lo.shape)
    with np.errstate(over="ignore", under="ignore"):
        down = (lo * np.exp(-a)) * _WIDEN_DOWN
        up = (hi * np.exp(a)) * _WIDEN_UP
    down = np.where(down >= _TINY_NORMAL, down, 0.0)
    keep = a == 0
    return np.where(keep, lo, down), np.where(keep, hi, up)


def continuity_fails(values: ArrayLike) -> NDArray[np.bool_]:
    """Whether a row has fewer distinct values than half its rounds.

    The continuity check of "Resolution" in ``docs/method.md`` for a
    ``reported`` metric with no declared resolution, applied to one
    arm. O(n log n) per row.

    Parameters
    ----------
    values : array_like of float
        Shape ``(..., n)``, ``n >= 1``.

    Returns
    -------
    numpy.ndarray of bool
        Shape ``(...)``: True where ``2 * distinct < n``.

    Raises
    ------
    ValueError
        If ``values`` has no rounds axis or no rounds.
    """
    x = np.asarray(values)
    if x.ndim < 1 or x.shape[-1] == 0:
        msg = f"values need a non-empty rounds axis, got shape {x.shape}"
        raise ValueError(msg)
    ordered = np.sort(x, axis=-1)
    changes: NDArray[np.bool_] = ordered[..., 1:] != ordered[..., :-1]
    distinct = 1 + np.sum(changes, axis=-1, dtype=np.int64)
    return 2 * distinct < x.shape[-1]


def lag1_autocorrelation(
    log_ratios: ArrayLike, candidate_first: ArrayLike
) -> FloatArray:
    """Lag-1 autocorrelation of ``d`` after removing each order's mean.

    The diagnostic of "Assumptions" in ``docs/method.md``. Balanced
    order alternates the order effect's sign from round to round, which
    alone drives the plain lag-1 autocorrelation negative, so each
    round's deviation is taken from the mean of the rounds that ran in
    the same order. With ``x`` those deviations, the estimate is
    ``sum(x_i x_(i+1)) / sum(x_i**2)``. O(R n).

    Parameters
    ----------
    log_ratios : array_like of float
        ``d = log(r)`` shaped ``(..., n)``, ``n >= 2``, finite.
    candidate_first : array_like of bool
        Same shape: True where the candidate ran first. All False cross
        host, where it is the plain lag-1 autocorrelation.

    Returns
    -------
    numpy.ndarray of float64
        Shape ``(...)``, in ``[-1, 1]``; NaN where every deviation is
        0, so no autocorrelation is defined. The exact value lies in
        ``[-1, 1]`` by Cauchy-Schwarz; the result is clipped there so
        rounding cannot leave it.

    Raises
    ------
    ValueError
        If the shapes differ, ``n < 2``, ``d`` is not finite, or
        ``candidate_first`` is not boolean.
    """
    d = np.asarray(log_ratios, dtype=np.float64)
    first = np.asarray(candidate_first)
    if first.dtype != np.bool_:
        msg = f"candidate_first must be boolean, got dtype {first.dtype}"
        raise ValueError(msg)
    if d.shape != first.shape or d.ndim < 1 or d.shape[-1] < 2:
        msg = (
            f"log_ratios shape {d.shape} and candidate_first shape "
            f"{first.shape} must match, with at least 2 rounds"
        )
        raise ValueError(msg)
    if not np.all(np.isfinite(d)):
        msg = "log_ratios must be finite"
        raise ValueError(msg)
    in_first = np.sum(first, axis=-1, keepdims=True)
    in_second = d.shape[-1] - in_first
    sum_first = np.sum(np.where(first, d, 0.0), axis=-1, keepdims=True)
    sum_second = np.sum(np.where(first, 0.0, d), axis=-1, keepdims=True)
    mean_first = sum_first / np.maximum(in_first, 1)
    mean_second = sum_second / np.maximum(in_second, 1)
    x = d - np.where(first, mean_first, mean_second)
    num = np.sum(x[..., 1:] * x[..., :-1], axis=-1)
    den = np.sum(x * x, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = np.clip(num / den, -1.0, 1.0)
    return np.where(den > 0, ratio, np.nan)


def outlier_share(
    log_ratios: ArrayLike, candidate_first: ArrayLike
) -> FloatArray:
    """Share of rounds whose log ratio is an outlier.

    The diagnostic of the gate in ``docs/method.md``. Each round's
    deviation is taken from the median of the rounds that ran in the
    same order, as for `lag1_autocorrelation`, so an order effect is not
    read as outliers. A round is an outlier when its deviation exceeds
    `OUTLIER_Z` robust standard deviations: ``1.4826`` times the median
    absolute deviation over all rounds. When more than half the
    deviations are 0 the robust scale is 0 and every nonzero deviation
    is an outlier. O(R n log n).

    Parameters
    ----------
    log_ratios : array_like of float
        ``d = log(r)`` shaped ``(..., n)``, ``n >= 1``, finite.
    candidate_first : array_like of bool
        Same shape: True where the candidate ran first. All False cross
        host.

    Returns
    -------
    numpy.ndarray of float64
        Shape ``(...)``, in ``[0, 1]``.

    Raises
    ------
    ValueError
        If the shapes differ, ``n < 1``, ``d`` is not finite, or
        ``candidate_first`` is not boolean.
    """
    d = np.asarray(log_ratios, dtype=np.float64)
    first = np.asarray(candidate_first)
    if first.dtype != np.bool_:
        msg = f"candidate_first must be boolean, got dtype {first.dtype}"
        raise ValueError(msg)
    if d.shape != first.shape or d.ndim < 1 or d.shape[-1] < 1:
        msg = (
            f"log_ratios shape {d.shape} and candidate_first shape "
            f"{first.shape} must match, with at least 1 round"
        )
        raise ValueError(msg)
    if not np.all(np.isfinite(d)):
        msg = "log_ratios must be finite"
        raise ValueError(msg)
    centred = d - np.where(
        first, _median_where(d, first), _median_where(d, ~first)
    )
    spread = np.abs(centred)
    scale = _MAD_TO_SD * np.median(spread, axis=-1, keepdims=True)
    return np.mean(spread > OUTLIER_Z * scale, axis=-1)


def _median_where(d: FloatArray, mask: NDArray[np.bool_]) -> FloatArray:
    """Median of ``d`` over the masked rounds, shape ``(..., 1)``.

    0 where the mask is empty, so callers can broadcast it unused.
    """
    count = np.sum(mask, axis=-1, keepdims=True)
    ordered = np.sort(np.where(mask, d, np.inf), axis=-1)
    low = np.take_along_axis(ordered, np.maximum(count - 1, 0) // 2, axis=-1)
    high = np.take_along_axis(ordered, count // 2, axis=-1)
    with np.errstate(invalid="ignore"):
        middle = (low + high) / 2
    return np.where(count > 0, middle, 0.0)


def session_pair_error(
    baseline: ArrayLike,
    candidate: ArrayLike,
    baseline_step: ArrayLike,
    candidate_step: ArrayLike,
) -> float:
    """Bound the resolution error of one cross-host session pair.

    Cross host an observation is the baseline session's mean log value
    minus the candidate session's, so it is off by at most the mean of
    the baseline values' per-value bounds plus the mean of the
    candidate values' ("Resolution" in ``docs/method.md``: each session
    pair's observation is widened by the mean ``e_i`` of its rounds).
    Each per-value bound is the one of `round_error_bounds`, and the
    means and their sum are rounded up as in item 2 of the module
    docstring. O(n) in the session lengths.

    Parameters
    ----------
    baseline, candidate : array_like of float
        The values of the baseline session's rounds and of the
        candidate session's, one-dimensional, finite and positive; the
        two sessions may differ in length.
    baseline_step, candidate_step : array_like of float
        Each value's step, finite and at least 0, broadcast to its
        session's values.

    Returns
    -------
    float
        The bound, at least 0; ``inf`` where some value is no larger
        than its step (or within rounding of it), which the resolution
        stop then treats as coarse.

    Raises
    ------
    ValueError
        If a session is empty or not one-dimensional, or a value or
        step is invalid as for `round_error_bounds`.
    """
    # Both sessions are validated before any bound is returned, so an
    # invalid session is refused even when the other is already coarse.
    bounds: list[FloatArray] = []
    for name, values, step in (
        ("baseline", baseline, baseline_step),
        ("candidate", candidate, candidate_step),
    ):
        v = _values(name, values)
        if v.ndim != 1 or v.size == 0:
            msg = f"{name} must be a non-empty 1-D session, got {v.shape}"
            raise ValueError(msg)
        q = _steps(f"{name}_step", step, v.shape)
        bounds.append(_value_error(q, v))
    if not all(np.all(np.isfinite(e)) for e in bounds):
        return math.inf
    means = [float(_mean_up(e[np.newaxis, :])[0]) for e in bounds]
    return float(_up(np.array(means[0] + means[1]), 1))


def decide_look(
    baseline: ArrayLike,
    candidate: ArrayLike,
    *,
    direction: Direction,
    confidence: float,
    min_effect: float,
    calibrated: bool,
    final: ArrayLike,
    resolution: Resolution | ObservationBounds | None,
    n0: int | None = None,
    keep_blocked: ArrayLike = False,
    procedure: Procedure = "t_log_ratio_batch_means",
    candidate_first: ArrayLike | None = None,
) -> Look:
    """Decide one look, for rows that share its length.

    The one place a look is decided, for `run_looks` and for a runner
    that drives its own looks. It decides the look it is given and
    enforces nothing about the schedule: a caller that drives its own
    looks takes their lengths and ``final`` from `plan_looks` (which
    holds ``n0``, complete order pairs on one host, at most ``K``
    looks and the budget cut-off) and ``confidence`` from
    `look_confidence` (the ``alpha / (2 K)`` split). Cross host is
    signalled by passing ``n0``; without it the look is one host. In
    the order of the decision rule in ``docs/method.md``, after rule 1
    (gates, decided before any round):

    1. the interval of ``procedure`` at ``confidence`` on the ``n``
       observations given; batch means with fewer than two complete
       blocks has no interval (NaN bounds), and the look goes to
       rule 4, or 5 at its last look, unless the resolution stop fires;
    2. the resolution stop (rule 1 here, ``coarse_resolution``,
       INCONCLUSIVE), except cross host below ``n0`` pairs, where speed
       never decides: there the look continues while budget remains
       and is INCONCLUSIVE (rule 5) with no diagnostic at the last
       look, as in the cross-host table of ``docs/method.md``, and a
       row with an unbounded or coarse observation records NaN bounds;
    3. the widening of both bounds by the upper bound on the mean
       observation error over the observations the interval used;
    4. rules 2 to 5 from `perfproof.stats.decision.interval_rules`,
       with budget remaining exactly where ``final`` is False; cross
       host (``n0`` given) the ``n`` observations are passed as
       completed session pairs;
    5. for ``procedure`` batch means only, the diagnostic gate: where
       rule 2 or 3 fired and the look has fewer blocks of
       `BATCH_BLOCK` observations than a diagnostic needs (lag-1
       autocorrelation above `LAG1_LIMIT` with fewer than
       `LAG1_MIN_BLOCKS`, outlier share above `OUTLIER_SHARE` with
       fewer than `OUTLIER_MIN_BLOCKS`), the look is rule 4, or rule 5
       at its last look, and `Look.withheld` names the diagnostic;
    6. where rule 2 fires and ``keep_blocked`` is True (a guardrail
       ``not_established`` or ``unmeasurable``, per "Guardrails" in
       ``docs/method.md``), the look continues while budget remains
       and is INCONCLUSIVE at the last look, instead of KEEP.

    Parameters
    ----------
    baseline, candidate : array_like of float
        Shape ``(R, n)``: the observations so far, rounds on one host
        and session geometric means cross host; positive and finite.
    direction : {"lower_is_better", "higher_is_better"}
        The metric's direction.
    confidence : float
        Two-sided confidence of the interval, from `look_confidence`.
    min_effect : float
        Smallest speedup worth keeping, a finite float at least 0.
    calibrated : bool
        Whether the procedure has passed calibration.
    final : array_like of bool
        Broadcast to ``(R,)``: whether this is the row's last look, so
        that no budget remains.
    resolution : Resolution, ObservationBounds or None
        `Resolution` on one host, from the values' steps;
        `ObservationBounds` in either mode, with bounds the caller
        computed (cross host from `session_pair_error`); ``None`` only
        for values known exactly.
    n0 : int, optional
        Cross host only, and what marks the look as cross host: the
        schedule's ``n0``, at least 10. ``None`` means one host.
    keep_blocked : array_like of bool, default False
        Broadcast to ``(R,)``: where True, rule 2 does not keep.
    procedure : str, default "t_log_ratio_batch_means"
        ``"t_log_ratio_batch_means"`` (the primary, blocks of
        `BATCH_BLOCK`) or ``"t_log_ratio"`` (plain t, blocks of 1).
    candidate_first : array_like of bool, optional
        Shape ``(R, n)``: True where the candidate ran first in the
        round, which the diagnostics need to remove the order effect
        (`lag1_autocorrelation`, `outlier_share`). ``None`` treats
        every round as the same order, as cross host.

    Returns
    -------
    Look
        Bounds, rule, regression flag, whether rule 2 was held back,
        which diagnostic withheld the decision, and the action, per
        row.

    Raises
    ------
    ValueError
        If the measurements are not shaped ``(R, n)`` alike, finite
        and positive; the procedure or direction is unknown;
        ``min_effect``, ``calibrated``, ``final`` or ``keep_blocked``
        is invalid; a `Resolution` is given cross host; the bounds are
        invalid (see `ObservationBounds`); ``candidate_first`` is not
        boolean or not shaped ``(R, n)``; or an interval bound is not
        finite and positive where the decision needs it.
    """
    b, c = _measurement_pair(baseline, candidate)
    rows, n = b.shape
    _check_decision_args(procedure, direction, min_effect, calibrated)
    if n0 is not None:
        _check_int("n0", n0, _MIN_N0)
    last = _bool_array("final", final, (rows,))
    blocked = _bool_array("keep_blocked", keep_blocked, (rows,))
    first = _candidate_first(candidate_first, (rows, n))
    has_interval = procedure == "t_log_ratio" or n // BATCH_BLOCK >= 2
    if has_interval:
        found = _interval_procedure(procedure, direction, confidence)(b, c)
        lo, hi, used = found.lower, found.upper, found.rounds
    else:
        lo = hi = np.full(rows, np.nan)
        used = 0
    coarse = np.zeros(rows, dtype=np.bool_)
    unbounded = np.zeros(rows, dtype=np.bool_)
    errors = _look_errors(b, c, resolution, cross=n0 is not None)
    if errors is not None:
        bound, flagged = errors
        unbounded = flagged | ~np.all(np.isfinite(bound), axis=-1)
        # Cross host below n0 pairs speed never decides: the comparison
        # continues, then is INCONCLUSIVE with no diagnostic, so the
        # resolution stop waits for n0.
        if n0 is None or n >= n0:
            coarse = unbounded
        # The interval may drop a trailing incomplete block; the
        # widening is the mean bound over the observations it used.
        if has_interval:
            spent = np.where(unbounded[:, np.newaxis], 0.0, bound[:, :used])
            lo, hi = widen(lo, hi, _mean_up(spent))
    rule = np.ones(rows, dtype=np.int8)
    regression = np.zeros(rows, dtype=np.bool_)
    clear = ~coarse
    if clear.any() and has_interval:
        pairs = None if n0 is None else np.full(int(clear.sum()), n)
        decided = interval_rules(
            lo[clear],
            hi[clear],
            min_effect=min_effect,
            budget_remains=~last[clear],
            calibrated=calibrated,
            session_pairs=pairs,
            n0=n0,
        )
        rule[clear] = decided.rule
        regression[clear] = decided.regression
    elif clear.any():
        rule[clear] = np.where(last[clear], 5, 4)
    why = np.zeros(rows, dtype=np.intp)
    if procedure == "t_log_ratio_batch_means":
        why = _withheld_codes(b, c, direction, first, rule)
    withheld = why > 0
    rule = np.where(withheld, np.where(last, 5, 4), rule).astype(np.int8)
    regression &= ~withheld
    held = (rule == 2) & blocked
    action = _ACTION_BY_RULE[rule]
    action = np.where(held & ~last, "CONTINUE", action)
    action = np.where(held & last, "INCONCLUSIVE", action)
    # An unbounded row below n0 went to interval_rules unwidened, which
    # cannot decide there (rules 4 and 5 only); it has no valid widened
    # interval to record.
    return Look(
        lower=np.where(unbounded, np.nan, lo),
        upper=np.where(unbounded, np.nan, hi),
        rule=rule,
        regression=regression,
        held=held,
        withheld=_WITHHELD[why].astype(np.str_),
        action=action.astype(np.str_),
    )


def run_looks(
    baseline: ArrayLike,
    candidate: ArrayLike,
    *,
    direction: Direction,
    schedule: LookSchedule,
    mode: HostMode,
    min_effect: float,
    calibrated: bool,
    resolution: Resolution | ObservationBounds | None,
    exhausted: ArrayLike,
    rounds: ArrayLike | None = None,
    alpha: float = DEFAULT_ALPHA,
    procedure: Procedure = "t_log_ratio_batch_means",
    keep_blocked: ArrayLike = False,
    candidate_first: ArrayLike | None = None,
) -> SequentialResult:
    """Run the sequential procedure over a batch of comparisons.

    For every replication, takes the looks `plan_looks` places, in
    order, and decides each with `decide_look` at `look_confidence`,
    on the observations so far, with budget remaining at every look
    but the replication's last. A replication stops at the first look
    whose action is not ``CONTINUE``: the resolution stop, rule 3, or
    rule 2 unless ``keep_blocked`` holds it back; at its last look rule
    5 (or a held rule 2) makes it INCONCLUSIVE. See the module
    docstring for what is and is not decided here.

    Work per look is one `decide_look` call per distinct look length
    among the replications still running (one for the scheduled looks,
    plus one per distinct budget cut-off), on copies of their rounds,
    so memory beyond the inputs is a small multiple of ``R n`` floats.
    Time is O(K R n log n) for the intervals and diagnostics.

    Parameters
    ----------
    baseline, candidate : array_like of float
        Measurements shaped ``(R, n)``, one replication per row and one
        observation per column, in the order run; positive and finite,
        as for `perfproof.stats.intervals.paired_ratios`. Cross host
        each column is a completed session pair, and each value is that
        session's geometric mean. Columns past a row's ``rounds`` are
        ignored.
    direction : {"lower_is_better", "higher_is_better"}
        The metric's direction.
    schedule : perfproof.schema.LookSchedule
        The look schedule; ``n0`` and ``k`` even on one host.
    mode : {"one_host", "cross_host"}
        Host mode. Cross host, the look's observation count is passed
        to the decision as completed session pairs, with ``n0``.
    min_effect : float
        Smallest speedup worth keeping; the bar is ``1 + min_effect``.
    calibrated : bool
        Whether the procedure has passed calibration; False means
        rules 2 and 3 never fire, and only the resolution stop can end
        a comparison before its last look.
    resolution : Resolution, ObservationBounds or None
        Every value's resolution, which every measured comparison
        passes: `Resolution` (the values' steps) on one host, or
        `ObservationBounds` shaped ``(R, n)`` like the measurements, in
        either mode; cross host, each session pair's bound comes from
        `session_pair_error`. ``None`` only for values known exactly,
        as simulated ones are: no widening and no resolution stop.
    exhausted : array_like of bool
        Per replication, broadcast to ``(R,)``: whether the budget ran
        out after its ``rounds``, so that its last look is final. A
        replication whose rounds cover the ``K``-th look ends there
        either way.
    rounds : array_like of int, optional
        Observations collected per replication, shape ``(R,)``, each at
        most ``n``; default ``n`` for every row.
    alpha : float, default 0.05
        Total error the promise allows.
    procedure : str, default "t_log_ratio_batch_means"
        ``"t_log_ratio_batch_means"`` (the primary, blocks of
        `BATCH_BLOCK`) or ``"t_log_ratio"``.
    keep_blocked : array_like of bool, default False
        Shape ``(R,)`` (the same at every look) or ``(R, K)`` (per
        look): where True, a guardrail not yet established holds back
        rule 2, as `decide_look` describes.
    candidate_first : array_like of bool, optional
        Shape ``(R, n)`` like the measurements: True where the
        candidate ran first in the round, for the diagnostics of
        `decide_look`. ``None``: every round the same order.

    Returns
    -------
    SequentialResult
        Every look's rounds, bounds, rule, regression flag and action,
        and each replication's outcome.

    Raises
    ------
    ValueError
        If an argument is invalid (see `plan_looks` and `decide_look`),
        the measurements are not two-dimensional, ``rounds`` exceeds
        ``n``, a `Resolution` is given cross host, or ``keep_blocked``
        or the resolution bounds do not have the shapes above.
    """
    b, c = _measurement_pair(baseline, candidate)
    replications, available = b.shape
    _check_decision_args(procedure, direction, min_effect, calibrated)
    looks = _schedule_values(schedule, mode)[2]
    conf = look_confidence(alpha, looks)
    collected = (
        np.full(replications, available, dtype=np.int64)
        if rounds is None
        else np.asarray(rounds)
    )
    plan = plan_looks(schedule, collected, exhausted, mode=mode)
    if collected.shape != (replications,) or np.any(collected > available):
        msg = (
            f"rounds must have shape ({replications},) and be at most "
            f"{available}, got shape {collected.shape}"
        )
        raise ValueError(msg)
    blocked = _blocked_per_look(keep_blocked, replications, looks)
    first = _candidate_first(candidate_first, b.shape)
    full = _full_resolution(resolution, b.shape, cross=mode == "cross_host")
    n0 = schedule.n0 if mode == "cross_host" else None
    lower = np.full((replications, looks), np.nan)
    upper = np.full((replications, looks), np.nan)
    rule = np.zeros((replications, looks), dtype=np.int8)
    regression = np.zeros((replications, looks), dtype=np.bool_)
    held = np.zeros((replications, looks), dtype=np.bool_)
    withheld = np.full((replications, looks), "", dtype="<U24")
    action = np.full((replications, looks), "", dtype="<U12")
    used = np.zeros((replications, looks), dtype=np.int64)
    running = np.ones(replications, dtype=np.bool_)
    for j in range(looks):
        here = running & (plan.rounds[:, j] > 0)
        if not here.any():
            break
        for n in np.unique(plan.rounds[here, j]).tolist():
            rows = np.flatnonzero(here & (plan.rounds[:, j] == n))
            look = decide_look(
                b[rows, :n],
                c[rows, :n],
                direction=direction,
                confidence=conf,
                min_effect=min_effect,
                calibrated=calibrated,
                final=plan.final[rows, j],
                resolution=_slice_resolution(full, rows, n),
                n0=n0,
                keep_blocked=blocked[rows, j],
                procedure=procedure,
                candidate_first=first[rows, :n],
            )
            withheld[rows, j] = look.withheld
            lower[rows, j] = look.lower
            upper[rows, j] = look.upper
            rule[rows, j] = look.rule
            regression[rows, j] = look.regression
            held[rows, j] = look.held
            action[rows, j] = look.action
            used[rows, j] = n
        running = here & (action[:, j] == "CONTINUE")
    return _summary(
        used,
        lower,
        upper,
        rule,
        regression,
        held,
        withheld,
        action,
        plan.exhausted,
        conf,
        procedure,
    )


def _summary(
    used: NDArray[np.int64],
    lower: FloatArray,
    upper: FloatArray,
    rule: NDArray[np.int8],
    regression: NDArray[np.bool_],
    held: NDArray[np.bool_],
    withheld: NDArray[np.str_],
    action: NDArray[np.str_],
    exhausted: NDArray[np.bool_],
    confidence: float,
    procedure: Procedure,
) -> SequentialResult:
    """Collect the per-look arrays and each replication's ending."""
    replications = used.shape[0]
    looks = np.sum(used > 0, axis=1).astype(np.int64)
    last = np.maximum(looks - 1, 0)
    index = np.arange(replications, dtype=np.int64)
    no_look = np.where(exhausted, 5, 4)
    final_rule = np.where(looks > 0, rule[index, last], no_look)
    no_look_outcome = np.where(exhausted, "INCONCLUSIVE", "CONTINUE")
    outcome = np.where(looks > 0, action[index, last], no_look_outcome)
    return SequentialResult(
        rounds=used,
        lower=lower,
        upper=upper,
        rule=rule,
        regression=regression,
        held=held,
        withheld=withheld.astype(np.str_),
        action=action.astype(np.str_),
        outcome=outcome.astype(np.str_),
        final_rule=final_rule.astype(np.int8),
        looks=looks,
        rounds_used=np.where(looks > 0, used[index, last], 0).astype(np.int64),
        confidence=confidence,
        procedure=procedure,
    )


def _measurement_pair(
    baseline: ArrayLike, candidate: ArrayLike
) -> tuple[FloatArray, FloatArray]:
    """Return both arms as float64 ``(R, n)`` arrays, checked."""
    b = _values("baseline", baseline)
    c = _values("candidate", candidate)
    if b.ndim != 2 or b.shape != c.shape:
        msg = (
            "baseline and candidate must both be shaped (R, n), got "
            f"{b.shape} and {c.shape}"
        )
        raise ValueError(msg)
    return b, c


def _check_decision_args(
    procedure: object,
    direction: object,
    min_effect: object,
    calibrated: object,
) -> None:
    """Refuse an unknown procedure or direction, or a bad decision arg.

    Checked before any look, so arguments are refused even when the
    budget allows none.
    """
    if procedure not in _PROCEDURES:
        msg = f"procedure must be one of {_PROCEDURES}, got {procedure!r}"
        raise ValueError(msg)
    if direction not in _DIRECTIONS:
        msg = f"direction must be one of {_DIRECTIONS}, got {direction!r}"
        raise ValueError(msg)
    if (
        not isinstance(min_effect, float)
        or not math.isfinite(min_effect)
        or min_effect < 0
    ):
        msg = f"min_effect must be a finite float >= 0, got {min_effect!r}"
        raise ValueError(msg)
    _check_flag("calibrated", calibrated)


def _blocked_per_look(
    keep_blocked: ArrayLike, replications: int, looks: int
) -> NDArray[np.bool_]:
    """Return ``keep_blocked`` broadcast to ``(R, K)``.

    A scalar or an ``(R,)`` mask holds at every look.
    """
    given = np.asarray(keep_blocked)
    if given.dtype != np.bool_:
        msg = f"keep_blocked must be boolean, got dtype {given.dtype}"
        raise ValueError(msg)
    if given.ndim == 1:
        given = given[:, np.newaxis]
    try:
        return np.broadcast_to(given, (replications, looks))
    except ValueError:
        msg = (
            f"keep_blocked shape {np.asarray(keep_blocked).shape} is "
            f"neither ({replications},) nor ({replications}, {looks})"
        )
        raise ValueError(msg) from None


def _full_resolution(
    resolution: object, shape: tuple[int, int], *, cross: bool
) -> Resolution | ObservationBounds | None:
    """Return ``resolution`` with its arrays broadcast to ``shape``.

    So each look can slice its rows and observations.
    """
    if resolution is None:
        return None
    if isinstance(resolution, Resolution):
        if cross:
            msg = (
                "a Resolution cross host would treat session means as "
                "single values; pass ObservationBounds from "
                "session_pair_error"
            )
            raise ValueError(msg)
        return Resolution(
            _steps("baseline_step", resolution.baseline_step, shape),
            _steps("candidate_step", resolution.candidate_step, shape),
            _check_flag("check_continuity", resolution.check_continuity),
        )
    if isinstance(resolution, ObservationBounds):
        errors, coarse = _observation_bounds(resolution, shape)
        return ObservationBounds(errors, coarse)
    msg = (
        "resolution must be a Resolution, ObservationBounds or None, got "
        f"{resolution!r}"
    )
    raise ValueError(msg)


def _slice_resolution(
    full: Resolution | ObservationBounds | None,
    rows: NDArray[np.intp],
    n: int,
) -> Resolution | ObservationBounds | None:
    """Return the part of ``full`` for ``rows`` and the first ``n``."""
    if full is None:
        return None
    if isinstance(full, Resolution):
        qb = np.asarray(full.baseline_step)[rows, :n]
        qc = np.asarray(full.candidate_step)[rows, :n]
        return Resolution(qb, qc, full.check_continuity)
    errors = np.asarray(full.errors)[rows, :n]
    coarse = np.asarray(full.coarse)[rows, :n]
    return ObservationBounds(errors, coarse)


def _look_errors(
    b: FloatArray,
    c: FloatArray,
    resolution: object,
    *,
    cross: bool,
) -> tuple[FloatArray, NDArray[np.bool_]] | None:
    """Per-observation error bounds and the extra coarse flag per row.

    ``None`` for exact values. A bound of ``inf`` also means coarse;
    the flag adds what a bound does not carry (the continuity check).
    """
    full = _full_resolution(resolution, b.shape, cross=cross)
    if full is None:
        return None
    if isinstance(full, Resolution):
        bound = round_error_bounds(
            b, c, full.baseline_step, full.candidate_step
        )
        flagged = np.zeros(b.shape[0], dtype=np.bool_)
        if full.check_continuity:
            flagged = continuity_fails(b) | continuity_fails(c)
        return bound, flagged
    bound = np.asarray(full.errors, dtype=np.float64)
    return bound, np.any(np.asarray(full.coarse), axis=-1)


def _observation_bounds(
    bounds: ObservationBounds, shape: tuple[int, int]
) -> tuple[FloatArray, NDArray[np.bool_]]:
    """Return the bounds' arrays broadcast to ``shape``, checked."""
    given = np.asarray(bounds.errors)
    if given.dtype not in _EXACT_FLOATS:
        msg = f"errors must be float, got dtype {given.dtype}"
        raise ValueError(msg)
    errors = given.astype(np.float64)
    bad = np.isnan(errors) | (errors < 0)
    if np.any(bad):
        first = float(errors[bad].flat[0])
        msg = f"errors must be at least 0 (inf allowed), got {first!r}"
        raise ValueError(msg)
    if errors.shape != shape:
        msg = f"errors shape {errors.shape} must be {shape}"
        raise ValueError(msg)
    coarse = _bool_array("coarse", bounds.coarse, shape)
    return errors, coarse


def _mean_up(x: FloatArray) -> FloatArray:
    """Mean over the last axis of nonnegative bounds, rounded up.

    ``n - 1`` additions and one division: at most ``n`` roundings on
    any path, item 2 of the module docstring.
    """
    n = x.shape[-1]
    return _up(np.sum(x, axis=-1) / n, n)


def _interval_procedure(
    procedure: Procedure, direction: Direction, confidence: float
) -> Callable[[FloatArray, FloatArray], PairedInterval]:
    """Return the interval of ``procedure`` as a function of data."""
    block = BATCH_BLOCK if procedure == "t_log_ratio_batch_means" else 1

    def student(b: FloatArray, c: FloatArray) -> PairedInterval:
        return t_interval(
            b, c, direction=direction, confidence=confidence, block=block
        )

    return student


def _candidate_first(
    value: ArrayLike | None, shape: tuple[int, int]
) -> NDArray[np.bool_]:
    """Return the order flags as a boolean array of ``shape``.

    ``None`` is every round in the same order.
    """
    if value is None:
        return np.zeros(shape, dtype=np.bool_)
    given = np.asarray(value)
    if given.dtype != np.bool_ or given.shape != shape:
        msg = (
            f"candidate_first must be a boolean array shaped {shape}, got "
            f"dtype {given.dtype}, shape {given.shape}"
        )
        raise ValueError(msg)
    return given


def _withheld_codes(
    b: FloatArray,
    c: FloatArray,
    direction: Direction,
    first: NDArray[np.bool_],
    rule: NDArray[np.int8],
) -> NDArray[np.intp]:
    """Which diagnostic withholds each row's decision, 0 for none.

    Only rows at rule 2 or 3 are examined: 1 where the lag-1
    autocorrelation is above `LAG1_LIMIT` with fewer than
    `LAG1_MIN_BLOCKS` blocks, 2 where the outlier share is above
    `OUTLIER_SHARE` with fewer than `OUTLIER_MIN_BLOCKS` (the first
    when both). A look with `LAG1_MIN_BLOCKS` blocks or more is never
    withheld, which relies on `OUTLIER_MIN_BLOCKS` being the smaller.
    """
    codes = np.zeros(rule.shape, dtype=np.intp)
    rows = np.flatnonzero((rule == 2) | (rule == 3))
    n = b.shape[1]
    blocks = n // BATCH_BLOCK
    if rows.size == 0 or n < 2 or blocks >= LAG1_MIN_BLOCKS:
        return codes
    d = np.log(paired_ratios(b[rows], c[rows], direction))
    f = first[rows]
    lag = lag1_autocorrelation(d, f)
    # Identical log ratios have no autocorrelation, whatever residue
    # the mean leaves; the exact interval of identical ratios stays
    # decidable ("Numerics" in docs/method.md).
    varies = np.ptp(d, axis=-1) > 0
    codes[rows] = np.where((lag > LAG1_LIMIT) & varies, 1, 0)
    if blocks < OUTLIER_MIN_BLOCKS:
        wild = outlier_share(d, f) > OUTLIER_SHARE
        codes[rows] = np.where((codes[rows] == 0) & wild, 2, codes[rows])
    return codes


def _schedule_values(schedule: object, mode: object) -> tuple[int, int, int]:
    """Return ``(n0, k, K)`` after checking them for ``mode``.

    The schema enforces the ranges, but a model built with
    ``model_construct`` skips that, so they are checked again; the
    parity of one host is not in the schema.
    """
    if not isinstance(schedule, LookSchedule):
        msg = f"schedule must be a LookSchedule, got {schedule!r}"
        raise ValueError(msg)
    if mode not in _MODES:
        msg = f"mode must be one of {_MODES}, got {mode!r}"
        raise ValueError(msg)
    n0, k, looks = schedule.n0, schedule.k, schedule.max_looks
    _check_int("n0", n0, _MIN_N0)
    _check_int("k", k, 1)
    _check_int("max_looks", looks, 1)
    if mode == "one_host" and (n0 % 2 or k % 2):
        msg = (
            "on one host n0 and k must be even, so every look falls on "
            f"complete order pairs; got n0={n0}, k={k}"
        )
        raise ValueError(msg)
    return n0, k, looks


def _check_int(name: str, value: object, minimum: int) -> None:
    """Refuse anything but an int (not a bool) at least ``minimum``."""
    if type(value) is not int or value < minimum:
        msg = f"{name} must be an int >= {minimum}, got {value!r}"
        raise ValueError(msg)


def _check_flag(name: str, value: object) -> bool:
    """Return ``value`` if it is a bool."""
    if not isinstance(value, bool):
        msg = f"{name} must be a bool, got {value!r}"
        raise ValueError(msg)
    return value


def _bool_array(
    name: str, value: ArrayLike, shape: tuple[int, ...]
) -> NDArray[np.bool_]:
    """Return ``value`` broadcast to ``shape``, checked boolean."""
    array = np.asarray(value)
    if array.dtype != np.bool_:
        msg = f"{name} must be boolean, got dtype {array.dtype}"
        raise ValueError(msg)
    try:
        return np.broadcast_to(array, shape).copy()
    except ValueError:
        msg = f"{name} shape {array.shape} does not broadcast to {shape}"
        raise ValueError(msg) from None


def _values(name: str, values: ArrayLike) -> FloatArray:
    """Return ``values`` as float64, checked finite and positive.

    Only float dtypes that widen to float64 exactly are taken.
    """
    given = np.asarray(values)
    if given.dtype not in _EXACT_FLOATS:
        msg = f"{name} must be float64 or narrower, got dtype {given.dtype}"
        raise ValueError(msg)
    array = given.astype(np.float64)
    bad = ~(np.isfinite(array) & (array > 0))
    if np.any(bad):
        first = float(array[bad].flat[0])
        msg = f"{name} values must be finite and positive, got {first!r}"
        raise ValueError(msg)
    return array


def _steps(name: str, steps: ArrayLike, shape: tuple[int, ...]) -> FloatArray:
    """Return ``steps`` as float64 broadcast to ``shape``, checked.

    Steps must be finite and at least 0; integers are taken, since a
    count's step is 1.
    """
    given = np.asarray(steps)
    if given.dtype not in _EXACT_FLOATS and given.dtype.kind not in "iu":
        msg = f"{name} must be a real array, got dtype {given.dtype}"
        raise ValueError(msg)
    array = given.astype(np.float64)
    bad = ~(np.isfinite(array) & (array >= 0))
    if np.any(bad):
        first = float(array[bad].flat[0])
        msg = f"{name} must be finite and at least 0, got {first!r}"
        raise ValueError(msg)
    try:
        return np.broadcast_to(array, shape)
    except ValueError:
        msg = f"{name} shape {array.shape} does not broadcast to {shape}"
        raise ValueError(msg) from None


def _value_error(step: FloatArray, value: FloatArray) -> FloatArray:
    """Upper bound on ``-log(1 - step / value)``; inf when unbounded.

    See item 1 of the module docstring.
    """
    with np.errstate(divide="ignore", under="ignore"):
        ratio = np.nextafter(step / value, np.inf)
        ratio = np.where(step == 0, 0.0, ratio)
        bounded = ratio < 1
        g = -np.log1p(-np.where(bounded, ratio, 0.0))
    e = np.where(step == 0, 0.0, g * _LOG_UP + _SUBNORMAL_PAD)
    return np.where(bounded & (value > step), e, np.inf)


def _up(x: FloatArray, roundings: int) -> FloatArray:
    """Return a float array not below the exact value ``x`` rounds.

    ``x`` comes from nonnegative exact inputs through ``+`` and ``/``
    with at most ``roundings`` roundings on any path; the factor
    ``1 + (roundings + 1) 2**-52`` is (P) in
    `perfproof.stats.intervals`.
    """
    return x * (1.0 + (roundings + 1) * _ULP1)
