"""The decision rule of ``docs/method.md``, as pure functions.

Numerical contract
------------------
The verdict is a function of the float64 ratio-scale bounds ``L`` and
``U`` that the caller records in `perfproof.schema.Verdict.interval`.
The interval procedure computes the interval for ``E[d]`` on the log
scale, with ``d = log(r)``, converts each bound with ``exp``, and,
as "Numerics" in ``docs/method.md`` requires, keeps the result from
coming out more favourable to the candidate than its exact value
through rounding. Those float64 bounds come in here, are compared as
they are, and come out unchanged, so the recorded interval is the one
that decided. Nothing here rounds, widens, or works on the log scale.

The bar is ``1 + min_effect`` computed in float64 from the stored
float64 ``min_effect`` (a typed ``0.118`` is the float64 nearest it),
the same expression `perfproof.schema.RunRecord` checks, not
``exp(log1p(min_effect))``, which can differ from it by an ulp. Then,
with no epsilon, tolerance or display rounding:

- rule 2 iff ``L > bar``: KEEP, unless a gate is ``not_available``;
- performance REJECT (rule 3) iff ``U <= bar``;
- continue (rule 4) iff ``L <= bar < U`` and budget remains;
- INCONCLUSIVE (rule 5) iff ``L <= bar < U`` and no budget remains;
- regression iff the verdict is a performance REJECT and ``U < 1``.

A bound that lands a few ulps off the bar is decided by where it
landed; keeping it on the conservative side is the interval
procedure's job (T1.1), never this module's. ``RunRecord`` refuses
any record that breaks these rules, so deciding on any other value, a
log-scale bound for instance, could produce a verdict that cannot be
written.

Any failed gate decides first (rule 1), whatever the interval, the
budget, the host mode or the pair count; when several fail,
`GATE_PRECEDENCE` names one. A gate fails only on evidence: what
perfproof could not identify or read (an unidentified runtime, an
unreadable host identity, no correctness check declared, changed lines
no passing check ran) is ``not_available``, never failed, so it can
only withhold a KEEP, never REJECT ("Unknown is not wrong" in
``docs/method.md``). A gate that is ``not_available`` (not
built yet, or nothing to check it with) never counts as passed: when
rule 2 would give KEEP, the comparison is INCONCLUSIVE under rule 2
instead, at the same look, naming the gates in ``blocked_by``, so it
can only remove a KEEP. It changes no other rule.

The guardrail step acts on rule 2 only ("Guardrails" in
``docs/method.md``), through `guardrail_actions`, the one place its
precedence is written. Given each guardrail's status at the look
(`perfproof.stats.guardrails` computes them): a ``breached`` guardrail
is REJECT with reason ``guardrail``, even with a gate ``not_available``
(a REJECT keeps nothing); otherwise a gate ``not_available`` or a
guardrail ``unmeasurable`` or ``not_declared``, which no further round
lifts, is INCONCLUSIVE at that look; otherwise a ``not_established``
guardrail continues while budget remains, since more rounds can narrow
its interval, and is INCONCLUSIVE when none does; otherwise KEEP. A
primary REJECT or INCONCLUSIVE (rules 1, 3, 4, 5) stands as it is, with
the guardrails reported alongside, so a breach never changes it.

Cross host, fewer than ``n0`` completed session pairs never give KEEP
or a performance REJECT: the comparison continues while another pair
can be collected (rule 4) and is INCONCLUSIVE when none can (rule 5).
From ``n0`` pairs on, the bar rules above apply.

Until the interval procedure is calibrated (``calibrated=False``, the
thin path before the M1 exit), rules 2 and 3 never fire, whatever the
interval: the comparison continues while budget remains (rule 4) and
is INCONCLUSIVE when it doesn't (rule 5), and every decision carries
``procedure_uncalibrated``. A failed gate still decides first.
"""

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, cast, get_args

import numpy as np
from numpy.typing import ArrayLike, NDArray

from perfproof.schema import (
    FIXED_GUARDRAIL_STATUSES,
    GATE_REASON,
    KEEP_BLOCKING_GATE_STATUSES,
    PENDING_GUARDRAIL_STATUSES,
    Arm,
    GateName,
    GateResult,
    GuardrailStatus,
    Reason,
)

__all__ = [
    "GATE_PRECEDENCE",
    "UNCHECKED",
    "Action",
    "Decision",
    "DecisionRule",
    "GuardrailActions",
    "IntervalRules",
    "completed_session_pairs",
    "decide",
    "guardrail_actions",
    "interval_rules",
]

Action = Literal["KEEP", "REJECT", "INCONCLUSIVE", "CONTINUE"]
"""What the comparison does next: one of the three verdicts, or
``CONTINUE`` to run more rounds."""
DecisionRule = Literal[1, 2, 3, 4, 5]
"""Decision rule number, as in ``docs/method.md``."""

GATE_PRECEDENCE: Final[tuple[GateName, ...]] = (
    "side_effects",
    "lock",
    "procedure",
    "runtime",
    "same_host",
    "correctness",
    "coverage",
)
"""Which failed gate decides when several failed, highest first.

The order of rule 1 in ``docs/method.md``. ``side_effects`` comes
first because it refuses the run before anything executes. The other
integrity gates come before correctness because a failed integrity
gate means the experiment itself is invalid, including the correctness
check run inside it: the ``lock`` gate covers the correctness oracle.
Among them, the order is that of the integrity table. ``coverage``
comes last because it asks whether passing checks reached the change.
The order the gates ran in never matters. Every failed gate stays
listed in `perfproof.schema.RunRecord.gates`; this only picks the one
`perfproof.schema.Verdict.gate` names.
"""

_ALL_GATES: Final[frozenset[str]] = frozenset(get_args(GateName))
_MIN_N0: Final = 10
_EXACT_FLOATS: Final = frozenset(
    np.dtype(t) for t in (np.float16, np.float32, np.float64)
)
_BY_RULE: Final[dict[int, tuple[DecisionRule, Action, Reason | None]]] = {
    2: (2, "KEEP", "performance"),
    3: (3, "REJECT", "performance"),
    4: (4, "CONTINUE", None),
    5: (5, "INCONCLUSIVE", "insufficient_evidence"),
}
UNCHECKED: Final = "<guardrails not given>"
"""What a rule 2 `Decision.guardrails_blocked` names when `decide` was
not given the guardrails: unchecked is never passed."""
_GUARDRAIL_STATUSES: Final = frozenset(get_args(GuardrailStatus))
# Indexed by rule: 1 is the resolution stop, 2 to 5 the decision rule.
_ACTION_BY_RULE: Final = np.array(
    ["", "INCONCLUSIVE", "KEEP", "REJECT", "CONTINUE", "INCONCLUSIVE"]
)


@dataclass(frozen=True, slots=True)
class Decision:
    """The outcome of one look, or of the gates before any look.

    Attributes
    ----------
    outcome : {"KEEP", "REJECT", "INCONCLUSIVE", "CONTINUE"}
        The verdict, or ``CONTINUE`` when rule 4 says to keep running.
    reason : {"performance", "correctness", "integrity", "guardrail", \
"insufficient_evidence"} or None
        Why; ``None`` only for ``CONTINUE``. ``"guardrail"`` for a rule
        2 REJECT because a guardrail is ``breached``.
    rule : {1, 2, 3, 4, 5}
        The rule that decided. Rule 2 with ``INCONCLUSIVE`` or
        ``CONTINUE`` means the interval cleared the bar but a gate was
        ``not_available`` or a guardrail did not pass.
    gate : str or None
        The failed gate that decided, for rule 1; ``None`` otherwise.
    regression : bool
        True exactly for a performance REJECT with ``U < 1``.
    lower, upper : float or None
        ``L`` and ``U``, bit-identical to the bounds passed in, ratio
        scale, dimensionless. ``None`` for rule 1, whose verdict
        carries no speedup claim, and when no interval was passed.
    session_pairs : int or None
        Completed baseline-then-candidate session pairs, cross host;
        ``None`` on one host, and when a failed ``same_host`` gate ends
        the cross-host design.
    procedure_uncalibrated : bool
        True when decided with ``calibrated=False``; the caller records
        it as `perfproof.schema.Diagnostics.procedure_uncalibrated`.
    blocked_by : tuple of str
        For a rule 2 INCONCLUSIVE, the ``not_available`` gates that
        blocked the KEEP, in `GATE_PRECEDENCE` order; empty otherwise.
        The record names them through their gate status.
    breached : tuple of str
        For a rule 2 REJECT, the metrics of the ``breached``
        guardrails, in the order given; empty otherwise.
    guardrails_blocked : tuple of str
        For a rule 2 look that does not keep and is not a breach, the
        metrics that withheld the KEEP: ``unmeasurable`` and
        ``not_declared`` guardrails (INCONCLUSIVE now) and
        ``not_established`` ones (continue, or INCONCLUSIVE with no
        budget), in the order given; empty otherwise.
    """

    outcome: Action
    reason: Reason | None
    rule: DecisionRule
    gate: GateName | None
    regression: bool
    lower: float | None
    upper: float | None
    session_pairs: int | None
    procedure_uncalibrated: bool
    blocked_by: tuple[GateName, ...]
    breached: tuple[str, ...] = ()
    guardrails_blocked: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GuardrailActions:
    """What the guardrail step makes of a batch of looks.

    Attributes
    ----------
    action : numpy.ndarray of str
        One of ``KEEP``, ``REJECT``, ``INCONCLUSIVE`` or ``CONTINUE``
        per element, in the broadcast shape of the inputs.
    guardrail_reject : numpy.ndarray of bool
        True where ``action`` is REJECT because a guardrail is
        breached (rule 2); a performance REJECT is False.
    held : numpy.ndarray of bool
        True where rule 2 fired and something other than a breach held
        back the KEEP, so the look continues or is INCONCLUSIVE under
        rule 2.
    """

    action: NDArray[np.str_]
    guardrail_reject: NDArray[np.bool_]
    held: NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class IntervalRules:
    """Rules 2 to 5 over a batch of intervals.

    Attributes
    ----------
    rule : numpy.ndarray of int8
        2, 3, 4 or 5 per interval, in the broadcast shape of the inputs.
    regression : numpy.ndarray of bool
        True where ``rule == 3`` and ``U < 1``; same shape as ``rule``.
    """

    rule: NDArray[np.int8]
    regression: NDArray[np.bool_]


def decide(
    gates: Sequence[GateResult],
    interval: tuple[float, float] | None,
    *,
    min_effect: float,
    budget_remains: bool,
    calibrated: bool,
    session_pairs: int | None = None,
    n0: int | None = None,
    guardrails: Mapping[str, GuardrailStatus] | None = None,
) -> Decision:
    """Decide one comparison at one look, as in ``docs/method.md``.

    The first rule that matches decides: (1) REJECT if a gate failed,
    (2) KEEP if ``L > bar``, unless the guardrail step or a gate
    ``not_available`` says otherwise (`guardrail_actions`), (3) REJECT
    if ``U <= bar``, (4) continue if budget remains, (5) INCONCLUSIVE.
    Rules 2 and 3 apply only when the procedure is calibrated. See the
    module docstring for how ``L``, ``U`` and the bar are computed and
    compared. Rules 2 to 5 are evaluated by `interval_rules`, so the
    bar comparison exists once.

    Parameters
    ----------
    gates : sequence of perfproof.schema.GateResult
        Gate results, in any order. If any failed, the failed gate
        first in `GATE_PRECEDENCE` decides, so the order the gates ran
        in never changes the verdict. If none failed, every gate must
        be listed, since an unrun gate is not a passed one; a gate
        that could not be checked is listed as ``not_available``.
    interval : tuple of (float, float) or None
        ``(L, U)``, the float64 ratio-scale bounds that will be
        recorded, ``L <= U``, both finite and positive. ``None`` when
        no interval exists yet (fewer than 2 rounds or pairs, or gates
        only); without one, only rules 1, 4 and 5 can decide.
    min_effect : float
        Smallest speedup worth keeping, finite and at least 0; the bar
        is ``1 + min_effect``.
    budget_remains : bool
        Whether another observation can still be collected within the
        run budget: another round on one host, another session pair
        cross host. False ends the comparison.
    calibrated : bool
        Whether the interval procedure in force has passed calibration.
        Required, with no default, so no caller can forget it. False
        means rules 2 and 3 never fire.
    session_pairs : int, optional
        Cross host only: completed baseline-then-candidate session
        pairs, from `completed_session_pairs`. Give it with ``n0``, and
        only then; ``None`` means one host.
    n0 : int, optional
        Cross host only: the look schedule's ``n0``, at least 10.
    guardrails : mapping of str to str, optional
        Each guardrail's status at this look, by metric: ``passed``,
        ``breached``, ``not_established``, ``unmeasurable``,
        ``not_declared`` or ``not_applicable``, as
        `perfproof.stats.guardrails` and the spec give them. Every
        guardrail of the comparison must be listed, since one left out
        is one nobody checked; the caller lists the undeclared defaults
        as ``not_declared``. Only rule 2 reads it. ``None`` means the
        caller did not give them, which fails closed: a rule 2 look is
        then INCONCLUSIVE, naming ``UNCHECKED``, never a KEEP. A caller
        that really has none passes an empty mapping.

    Returns
    -------
    Decision
        Outcome, reason, rule, deciding gate, regression flag, the
        bounds and session pairs passed in, whether the procedure
        was uncalibrated, and the gates and guardrails that decided or
        held a rule 2 look.

    Raises
    ------
    ValueError
        If a parameter is out of range or of the wrong type;
        ``session_pairs`` and ``n0`` are not given together; a gate is
        listed twice, is not a `GateResult`, or is one the schema
        refuses (an unknown name or status, or a waiver of a gate that
        cannot be waived); no gate failed and one is missing; the
        ``same_host`` gate contradicts the host mode (waived on one
        host; anything but waived or failed cross host); or a
        guardrail status is not one of the six.
    """
    _check_min_effect(min_effect)
    named = _checked_guardrails(guardrails)
    _check_host_mode(session_pairs, n0)
    if session_pairs is not None:
        _check_count("session_pairs", session_pairs, 0)
    bounds = None if interval is None else _check_interval(interval)
    budget = _check_bool("budget_remains", budget_remains)
    uncalibrated = not _check_bool("calibrated", calibrated)
    seen = _checked_gates(gates, cross=session_pairs is not None)
    failed = _deciding_gate(seen)
    if failed is not None:
        # A cross-host verdict goes with a waived same_host gate. A
        # failed same_host gate ends the cross-host design, so its
        # verdict is not cross host and counts no pairs.
        if _failed(seen.get("same_host")):
            session_pairs = None
        return Decision(
            outcome="REJECT",
            reason=GATE_REASON[failed],
            rule=1,
            gate=failed,
            regression=False,
            lower=None,
            upper=None,
            session_pairs=session_pairs,
            procedure_uncalibrated=uncalibrated,
            blocked_by=(),
        )
    if bounds is None:
        rule, outcome, reason = _BY_RULE[4 if budget else 5]
        return Decision(
            outcome=outcome,
            reason=reason,
            rule=rule,
            gate=None,
            regression=False,
            lower=None,
            upper=None,
            session_pairs=session_pairs,
            procedure_uncalibrated=uncalibrated,
            blocked_by=(),
        )
    lower, upper = bounds
    rules = interval_rules(
        lower,
        upper,
        min_effect=min_effect,
        budget_remains=budget,
        calibrated=not uncalibrated,
        session_pairs=session_pairs,
        n0=n0,
    )
    rule, outcome, reason = _BY_RULE[int(rules.rule)]
    blocked_by: tuple[GateName, ...] = ()
    breached: tuple[str, ...] = ()
    blocked_guardrails: tuple[str, ...] = ()
    if rule == 2:
        blocked_by = tuple(
            name
            for name in GATE_PRECEDENCE
            if (gate := seen.get(name)) is not None
            and gate.status in KEEP_BLOCKING_GATE_STATUSES
        )
        breached = tuple(m for m, st in named.items() if st == "breached")
        fixed = tuple(
            m for m, st in named.items() if st in FIXED_GUARDRAIL_STATUSES
        )
        pending = tuple(
            m for m, st in named.items() if st in PENDING_GUARDRAIL_STATUSES
        )
        step = guardrail_actions(
            np.int8(2),
            breached=bool(breached),
            fixed=bool(blocked_by or fixed or guardrails is None),
            pending=bool(pending),
            budget_remains=budget,
        )
        outcome = cast("Action", str(step.action))
        if bool(step.guardrail_reject):
            reason = "guardrail"
        elif outcome == "CONTINUE":
            reason = None
        elif outcome == "INCONCLUSIVE":
            reason = "insufficient_evidence"
        if bool(step.held):
            held = {*fixed, *pending}
            blocked_guardrails = tuple(m for m in named if m in held)
            if guardrails is None:
                blocked_guardrails = (UNCHECKED,)
    return Decision(
        outcome=outcome,
        reason=reason,
        rule=rule,
        gate=None,
        regression=bool(rules.regression),
        lower=lower,
        upper=upper,
        session_pairs=session_pairs,
        procedure_uncalibrated=uncalibrated,
        blocked_by=blocked_by,
        breached=breached,
        guardrails_blocked=blocked_guardrails,
    )


def guardrail_actions(
    rule: ArrayLike,
    *,
    breached: ArrayLike,
    fixed: ArrayLike,
    pending: ArrayLike,
    budget_remains: ArrayLike,
) -> GuardrailActions:
    """Apply the guardrail step of rule 2 to a batch of looks.

    The one place the precedence of "Guardrails" in ``docs/method.md``
    is written; `decide` calls it for one look, and the sequential
    drivers and the calibration suite call it for batches. Guardrails
    matter only where the primary interval gave rule 2 (KEEP); every
    other rule keeps its own action (1 INCONCLUSIVE, 3 REJECT, 4
    CONTINUE, 5 INCONCLUSIVE), whatever the guardrails say. At rule 2,
    in order: ``breached`` is REJECT (a REJECT keeps nothing, so it
    holds even when something else would withhold a KEEP); else
    ``fixed``, a blocker no further round lifts (a gate
    ``not_available``, a guardrail ``unmeasurable`` or
    ``not_declared``), is INCONCLUSIVE now; else ``pending``, a
    guardrail ``not_established``, is CONTINUE while budget remains and
    INCONCLUSIVE when none does; else KEEP.

    Parameters
    ----------
    rule : array_like of int
        The look's rule, 1 to 5, as `interval_rules` and the
        resolution stop give it, after any diagnostic gate.
    breached, fixed, pending : array_like of bool
        Whether any guardrail is breached, whether something that no
        round can lift withholds the KEEP, and whether a guardrail is
        not established. Broadcast with ``rule``.
    budget_remains : array_like of bool
        Whether another observation can still be collected.

    Returns
    -------
    GuardrailActions
        Action, whether it is a guardrail REJECT, and whether rule 2
        was held, in the broadcast shape.

    Raises
    ------
    ValueError
        If a rule is not an integer from 1 to 5 or a flag is not
        boolean, or the shapes do not broadcast.
    """
    code = np.asarray(rule)
    if not np.issubdtype(code.dtype, np.integer) or np.any(
        (code < 1) | (code > 5)
    ):
        msg = f"rule must be integers from 1 to 5, got {code!r}"
        raise ValueError(msg)
    flags = {
        "breached": breached,
        "fixed": fixed,
        "pending": pending,
        "budget_remains": budget_remains,
    }
    arrays = {k: np.asarray(v) for k, v in flags.items()}
    for name, flag in arrays.items():
        if flag.dtype != np.bool_:
            msg = f"{name} must be boolean, got dtype {flag.dtype}"
            raise ValueError(msg)
    try:
        code, *rest = np.broadcast_arrays(code, *arrays.values())
    except ValueError:
        msg = "rule, breached, fixed, pending, budget_remains do not broadcast"
        raise ValueError(msg) from None
    is_breached, is_fixed, is_pending, budget = rest
    kept = code == 2
    reject = kept & is_breached
    held = kept & ~is_breached & (is_fixed | is_pending)
    action = np.where(
        reject,
        "REJECT",
        np.where(
            held & (is_fixed | ~budget),
            "INCONCLUSIVE",
            np.where(held, "CONTINUE", _ACTION_BY_RULE[code]),
        ),
    )
    return GuardrailActions(
        action=action.astype(np.str_), guardrail_reject=reject, held=held
    )


def interval_rules(
    lower: ArrayLike,
    upper: ArrayLike,
    *,
    min_effect: float,
    budget_remains: ArrayLike,
    calibrated: bool,
    session_pairs: ArrayLike | None = None,
    n0: int | None = None,
) -> IntervalRules:
    """Apply rules 2 to 5 to a batch of intervals whose gates passed.

    This is the one place the bounds are compared with the bar; `decide`
    calls it for a single interval. Batches let the sequential driver
    and the calibration suite decide every replication in one call.

    Parameters
    ----------
    lower, upper : array_like of float
        ``L`` and ``U`` on the ratio scale, finite, positive, and
        ``L <= U`` elementwise; float16, float32 or float64, widened
        exactly to float64. Both have the same shape, so an interval's
        bounds are never paired with another interval's.
    min_effect : float
        Smallest speedup worth keeping, finite and at least 0.
    budget_remains : array_like of bool
        Whether another observation (a round, or cross host a session
        pair) can still be collected; boolean dtype.
    calibrated : bool
        Whether the interval procedure has passed calibration; no
        default. False means rules 2 and 3 never fire: every element is
        rule 4 or 5 by ``budget_remains``.
    session_pairs : array_like of int, optional
        Cross host only: completed session pairs, integer dtype, at
        least 0. Give it with ``n0``, and only then.
    n0 : int, optional
        Cross host only: the look schedule's ``n0``, at least 10.

    Returns
    -------
    IntervalRules
        Rule and regression flag per element, in the broadcast shape of
        ``lower``, ``upper``, ``budget_remains`` and ``session_pairs``.

    Raises
    ------
    ValueError
        If a bound is not finite and positive, ``L > U`` anywhere,
        ``lower`` and ``upper`` differ in shape, the inputs do not
        broadcast, ``budget_remains`` is not boolean, ``calibrated`` is
        not a bool, ``session_pairs`` is not a non-negative integer
        array, ``session_pairs`` and ``n0`` are not given together, or
        ``min_effect`` or ``n0`` is out of range.
    """
    bar = 1.0 + float(_check_min_effect(min_effect))
    ready = _check_bool("calibrated", calibrated)
    _check_host_mode(session_pairs, n0)
    lo = _bounds_array("lower", lower)
    hi = _bounds_array("upper", upper)
    budget = np.asarray(budget_remains)
    if budget.dtype != np.bool_:
        msg = f"budget_remains must be boolean, got dtype {budget.dtype}"
        raise ValueError(msg)
    pairs = None if session_pairs is None else _pairs_array(session_pairs)
    arrays = [lo, hi, budget] if pairs is None else [lo, hi, budget, pairs]
    try:
        shape = np.broadcast_shapes(*(a.shape for a in arrays))
    except ValueError:
        shapes = [a.shape for a in arrays]
        msg = f"lower, upper, budget_remains, session_pairs shapes {shapes}"
        msg += " do not broadcast"
        raise ValueError(msg) from None
    if lo.shape != hi.shape:
        msg = f"lower shape {lo.shape} and upper shape {hi.shape} differ"
        raise ValueError(msg)
    inverted = lo > hi
    if np.any(inverted):
        first = tuple(np.argwhere(inverted)[0])
        msg = (
            f"lower bound {float(lo[first])!r} exceeds upper "
            f"bound {float(hi[first])!r}"
        )
        raise ValueError(msg)
    keep = (lo > bar) & ready
    reject = (hi <= bar) & ready
    if pairs is not None and n0 is not None:
        enough = pairs >= n0
        keep = keep & enough
        reject = reject & enough
    rule = np.broadcast_to(
        np.where(keep, 2, np.where(reject, 3, np.where(budget, 4, 5))), shape
    ).astype(np.int8)
    regression = (rule == 3) & (hi < 1.0)
    return IntervalRules(rule=rule, regression=regression)


def completed_session_pairs(sessions: Sequence[tuple[Arm, bool]]) -> int:
    """Count completed baseline-then-candidate session pairs.

    Cross-host sessions alternate baseline, candidate, baseline, ...,
    each on a freshly allocated host, so sessions ``2k`` and ``2k + 1``
    form pair ``k``. A pair counts only when both of its sessions
    completed. A failed or incomplete session voids its own pair, and
    sessions are never re-paired across the gap it leaves. A trailing
    baseline with no candidate after it is unpaired. O(n) in the
    number of sessions.

    Parameters
    ----------
    sessions : sequence of (str, bool)
        Every session started, in launch order, as ``(arm, completed)``
        with ``arm`` either ``"baseline"`` or ``"candidate"``. A
        session that crashed, timed out or failed a gate is started
        and not completed. A host that was never allocated started no
        session and is left out, as it has no fingerprint either; the
        session launched in its place takes its turn in the
        alternation.

    Returns
    -------
    int
        Completed pairs, at least 0.

    Raises
    ------
    ValueError
        If a session is not an ``(arm, completed)`` pair, an arm is
        unknown, ``completed`` is not a bool, or the sessions do not
        alternate starting with a baseline.
    """
    count = 0
    baseline_completed = False
    for i, session in enumerate(sessions):
        arm, completed = _check_session(i, session)
        expected: Arm = "baseline" if i % 2 == 0 else "candidate"
        if arm != expected:
            msg = (
                f"session {i} is {arm!r} but {expected!r} was due: "
                "sessions alternate baseline, candidate, starting with "
                "baseline"
            )
            raise ValueError(msg)
        if arm == "baseline":
            baseline_completed = completed
        elif baseline_completed and completed:
            count += 1
    return count


def _check_min_effect(min_effect: object) -> float:
    """Return ``min_effect`` if it is a finite float at least 0."""
    if (
        not isinstance(min_effect, float)
        or not math.isfinite(min_effect)
        or min_effect < 0
    ):
        msg = f"min_effect must be a finite float >= 0, got {min_effect!r}"
        raise ValueError(msg)
    return min_effect


def _checked_guardrails(
    guardrails: Mapping[str, GuardrailStatus] | None,
) -> dict[str, GuardrailStatus]:
    """Return the guardrail statuses by metric, each one of the six."""
    if guardrails is None:
        return {}
    out: dict[str, GuardrailStatus] = {}
    for metric, status in cast("Mapping[object, object]", guardrails).items():
        if not isinstance(metric, str) or status not in _GUARDRAIL_STATUSES:
            msg = f"guardrail {metric!r} has status {status!r}, not one of "
            msg += f"{sorted(_GUARDRAIL_STATUSES)}"
            raise ValueError(msg)
        out[metric] = cast("GuardrailStatus", status)
    return out


def _check_bool(name: str, value: object) -> bool:
    """Return ``value`` as a bool if it is a Python or NumPy bool."""
    if not isinstance(value, bool | np.bool_):
        msg = f"{name} must be a bool, got {value!r}"
        raise ValueError(msg)
    return value is True or value is np.True_


def _check_count(name: str, value: object, minimum: int) -> None:
    """Refuse anything but an int (not a bool) at least ``minimum``."""
    if type(value) is not int or value < minimum:
        msg = f"{name} must be an int >= {minimum}, got {value!r}"
        raise ValueError(msg)


def _check_host_mode(session_pairs: object, n0: object) -> None:
    """Check the cross-host arguments come together; ``n0`` >= 10."""
    if (session_pairs is None) != (n0 is None):
        msg = (
            "session_pairs and n0 are given together, cross host only; "
            f"got session_pairs={session_pairs!r}, n0={n0!r}"
        )
        raise ValueError(msg)
    if n0 is not None:
        _check_count("n0", n0, _MIN_N0)


def _check_interval(interval: object) -> tuple[float, float]:
    """Return ``(L, U)`` as Python floats, bit for bit, after checks."""
    pair = _as_pair(interval)
    if pair is None:
        msg = f"interval must be (lower, upper), got {interval!r}"
        raise ValueError(msg)
    bounds: list[float] = []
    for bound in pair:
        if (
            not isinstance(bound, float)
            or not math.isfinite(bound)
            or bound <= 0
        ):
            msg = f"interval {interval!r} needs finite, positive float bounds"
            raise ValueError(msg)
        bounds.append(float(bound))
    if bounds[0] > bounds[1]:
        msg = f"interval {interval!r} has its lower bound above its upper"
        raise ValueError(msg)
    return bounds[0], bounds[1]


def _bounds_array(name: str, values: ArrayLike) -> NDArray[np.float64]:
    """Return ``values`` as float64, checked finite and positive.

    Only float dtypes that widen to float64 exactly are taken, so the
    bounds compared are the bounds given: strings, ints and bools are
    refused, and so is ``longdouble``, which would be rounded.
    """
    given = np.asarray(values)
    if given.dtype not in _EXACT_FLOATS:
        msg = f"{name} bounds must be float64 or narrower, got {given.dtype}"
        raise ValueError(msg)
    array = given.astype(np.float64)
    bad = ~(np.isfinite(array) & (array > 0))
    if np.any(bad):
        first = float(array[bad].flat[0])
        msg = f"{name} bounds must be finite and positive, got {first!r}"
        raise ValueError(msg)
    return array


def _pairs_array(values: ArrayLike) -> NDArray[np.integer]:
    """Return ``values``, checked integer dtype and non-negative."""
    array = np.asarray(values)
    if not np.issubdtype(array.dtype, np.integer):
        msg = f"session_pairs must be integers, got dtype {array.dtype}"
        raise ValueError(msg)
    if np.any(array < 0):
        first = int(array[array < 0].flat[0])
        msg = f"session_pairs must be >= 0, got {first!r}"
        raise ValueError(msg)
    return array


def _checked_gates(
    gates: Iterable[object], *, cross: bool
) -> dict[str, GateResult]:
    """Return the gates by name, each validated again, read once.

    Cross host, the ``same_host`` gate is always listed, waived or
    failed, since its waiver comes from the spec before any host
    exists; on one host it is never waived.
    """
    seen: dict[str, GateResult] = {}
    for given in gates:
        if not isinstance(given, GateResult):
            msg = f"gates must be GateResult, got {given!r}"
            raise ValueError(msg)
        gate = _revalidated(given)
        if gate.gate in seen:
            msg = f"gate {gate.gate!r} is listed more than once"
            raise ValueError(msg)
        seen[gate.gate] = gate
    same_host = seen.get("same_host")
    status = None if same_host is None else same_host.status
    if cross and status not in ("waived", "failed"):
        msg = (
            "cross host needs the same_host gate listed, waived by the "
            f"opt-in or failed, got {status!r}"
        )
        raise ValueError(msg)
    if not cross and status == "waived":
        msg = (
            "the same_host gate is waived, so session_pairs and n0 "
            "are required"
        )
        raise ValueError(msg)
    return seen


def _deciding_gate(seen: Mapping[str, GateResult]) -> GateName | None:
    """Return the failed gate that decides, or ``None`` if none failed.

    The deciding gate is the failed one first in `GATE_PRECEDENCE`.

    Fails closed: with no failure, a gate that is absent was never run,
    so the gates cannot be called passed.
    """
    for name in GATE_PRECEDENCE:
        if _failed(seen.get(name)):
            return name
    missing = sorted(_ALL_GATES - seen.keys())
    if missing:
        msg = f"no gate failed, but gates {missing} are not listed"
        raise ValueError(msg)
    return None


def _revalidated(gate: GateResult) -> GateResult:
    """Return ``gate`` validated by the schema again.

    `GateResult.model_construct` skips validation, so a gate can arrive
    with a name or status the schema lacks, or waived when it cannot be
    waived. Such a gate says nothing about the candidate: it must never
    count as passed, nor become a definite correctness or integrity
    verdict. Re-validating keeps the schema the one source of those
    rules.

    Raises
    ------
    pydantic.ValidationError
        A `ValueError`, if the schema refuses the gate.
    """
    return GateResult.model_validate(gate.model_dump(warnings=False))


def _failed(gate: GateResult | None) -> bool:
    """Whether ``gate``, already validated, is listed and failed."""
    return gate is not None and gate.status == "failed"


def _check_session(index: int, session: object) -> tuple[Arm, bool]:
    """Return ``(arm, completed)`` from one session after checks."""
    pair = _as_pair(session)
    if pair is None:
        msg = f"session {index} must be (arm, completed), got {session!r}"
        raise ValueError(msg)
    arm, completed = pair
    if arm != "baseline" and arm != "candidate":
        msg = f"session {index} has unknown arm {arm!r}"
        raise ValueError(msg)
    if not isinstance(completed, bool):
        msg = f"session {index}: completed must be a bool, got {completed!r}"
        raise ValueError(msg)
    return ("baseline" if arm == "baseline" else "candidate"), completed


def _as_pair(value: object) -> tuple[object, object] | None:
    """Return the items of a two-item tuple or list, else ``None``."""
    if isinstance(value, tuple | list):
        items = cast("Sequence[object]", value)
        if len(items) == 2:
            return items[0], items[1]
    return None
