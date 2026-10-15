import itertools
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Any, cast, get_args

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ValidationError
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import (
    Arm,
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    GateName,
    GateResult,
    Interval,
    LookSchedule,
    MachineFingerprint,
    MetricSpec,
    RunRecord,
    TreeEnvironment,
    Verdict,
)
from perfproof.stats.decision import (
    GATE_PRECEDENCE,
    Decision,
    completed_session_pairs,
    decide,
    interval_rules,
)

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
N0 = 10
EVERY_GATE: tuple[GateName, ...] = (
    "correctness",
    "lock",
    "procedure",
    "runtime",
    "same_host",
)
UNPROBED = "not probed in this test"


def unprobed(model: type[BaseModel], *keep: str) -> dict[str, str]:
    """Reasons for every optional field of ``model`` left ``None``."""
    skip = {"captured_at", "version", "session", "env_values", "missing"}
    return {
        name: UNPROBED
        for name in model.model_fields
        if name not in skip and name not in keep
    }


def machine() -> MachineFingerprint:
    return MachineFingerprint(
        captured_at=T0,
        host_id="gate-runner",
        missing=unprobed(MachineFingerprint, "host_id"),
    )


def environment(version: Arm) -> TreeEnvironment:
    return TreeEnvironment(version=version, missing=unprobed(TreeEnvironment))


def spec(min_effect: float = 0.03, *, cross: bool = False) -> BenchmarkSpec:
    return BenchmarkSpec(
        name="bench",
        command=("python", "bench.py"),
        metric=MetricSpec(kind="wall", direction="lower_is_better", unit="s"),
        correctness=CorrectnessSpec(command=("pytest",), timeout_seconds=60.0),
        warmup=0,
        budget=Budget(max_rounds=1000, max_seconds=600.0),
        looks=LookSchedule(n0=N0, k=10, max_looks=5),
        min_effect=min_effect,
        timeout_seconds=30.0,
        harness=("bench.py",),
        allow_cross_host=cross,
    )


def passed(*, cross: bool = False) -> tuple[GateResult, ...]:
    """All five gates passed, with ``same_host`` waived cross host."""
    return tuple(
        GateResult(gate="same_host", status="waived", detail="cross-host")
        if cross and gate == "same_host"
        else GateResult(gate=gate, status="passed")
        for gate in EVERY_GATE
    )


def with_failure(
    gates: tuple[GateResult, ...], *failed: GateName
) -> tuple[GateResult, ...]:
    return tuple(
        GateResult(gate=g.gate, status="failed", detail="differs")
        if g.gate in failed
        else g
        for g in gates
    )


def verdict(d: Decision, *, rounds: int = 40) -> Verdict:
    """Build the verdict a caller would record from ``d``."""
    assert d.outcome != "CONTINUE"
    assert d.rule != 4
    assert d.reason is not None
    measured = d.lower is not None and d.upper is not None
    interval = (
        Interval(lower=d.lower, upper=d.upper, confidence=0.99)
        if d.lower is not None and d.upper is not None
        else None
    )
    return Verdict(
        outcome=d.outcome,
        reason=d.reason,
        rule=d.rule,
        gate=d.gate,
        estimate=(
            math.sqrt(d.lower * d.upper)
            if d.lower is not None and d.upper is not None
            else None
        ),
        interval=interval,
        rounds=rounds if measured else 0,
        looks=4 if measured else 0,
        procedure="t_log_ratio",
        regression=d.regression,
        cross_host=d.session_pairs is not None,
        session_pairs=d.session_pairs,
        calibration="uncalibrated",
    )


def record(
    v: Verdict,
    gates: tuple[GateResult, ...],
    *,
    min_effect: float = 0.03,
    machines: tuple[MachineFingerprint, ...] | None = None,
) -> RunRecord:
    cross = v.session_pairs is not None
    if machines is None:
        machines = (machine(),) * max(1, 2 * (v.session_pairs or 0))
    return RunRecord(
        run_id="20261015-120000-bench",
        perfproof_version="0.1.0",
        started_at=T0,
        finished_at=T0 + timedelta(minutes=3),
        spec=spec(min_effect, cross=cross),
        baseline_commit=COMMIT,
        order_seed=1,
        verdict=v,
        gates=gates,
        machines=machines,
        environments=(environment("baseline"), environment("candidate")),
        samples_path="samples.npz" if v.rounds else None,
    )


def step(x: float, k: int) -> float:
    """Return ``x`` moved ``k`` float64 ulps (negative is down)."""
    for _ in range(abs(k)):
        x = math.nextafter(x, math.inf if k > 0 else -math.inf)
    return x


# Method table


@pytest.mark.parametrize(
    ("bounds", "outcome_with_budget", "outcome_at_end", "regression"),
    [
        # +8%, +5% to +11%: rule 2.
        ((1.05, 1.11), "KEEP", "KEEP", False),
        # +4%, +3.5% to +4.5%: rule 2.
        ((1.035, 1.045), "KEEP", "KEEP", False),
        # +5%, +2% to +8%: rule 4, then 5 at the end of the budget.
        ((1.02, 1.08), "CONTINUE", "INCONCLUSIVE", False),
        # +10%, -2% to +21%: rule 4, then 5.
        ((0.98, 1.21), "CONTINUE", "INCONCLUSIVE", False),
        # +1%, -1% to +2%: rule 3.
        ((0.99, 1.02), "REJECT", "REJECT", False),
        # -6%, -9% to -3%: rule 3, regression flagged.
        ((0.91, 0.97), "REJECT", "REJECT", True),
    ],
)
def test_method_table_rows(
    bounds: tuple[float, float],
    outcome_with_budget: str,
    outcome_at_end: str,
    regression: bool,
) -> None:
    rule = {"KEEP": 2, "REJECT": 3, "CONTINUE": 4, "INCONCLUSIVE": 5}
    reason = {
        "KEEP": "performance",
        "REJECT": "performance",
        "CONTINUE": None,
        "INCONCLUSIVE": "insufficient_evidence",
    }
    for budget, outcome in (
        (True, outcome_with_budget),
        (False, outcome_at_end),
    ):
        d = decide(passed(), bounds, min_effect=0.03, budget_remains=budget)
        assert d.outcome == outcome
        assert d.rule == rule[outcome]
        assert d.reason == reason[outcome]
        assert d.gate is None
        assert d.regression is regression
        assert (d.lower, d.upper) == bounds
    record(
        verdict(
            decide(passed(), bounds, min_effect=0.03, budget_remains=False)
        ),
        passed(),
    )


def test_method_table_not_measured_row() -> None:
    gates = (GateResult(gate="correctness", status="failed", detail="x"),)
    d = decide(gates, None, min_effect=0.03, budget_remains=True)
    assert (d.outcome, d.reason, d.rule, d.gate) == (
        "REJECT",
        "correctness",
        1,
        "correctness",
    )
    assert (d.lower, d.upper, d.regression) == (None, None, False)
    record(verdict(d), gates)


# Bar boundary, one float64 ulp either side


@pytest.mark.parametrize("min_effect", [0.03, 0.0])
@pytest.mark.parametrize(
    ("ulps", "keep"), [(-1, False), (0, False), (1, True)]
)
def test_lower_bound_at_the_bar(
    min_effect: float, ulps: int, keep: bool
) -> None:
    bar = 1 + min_effect
    lower = step(bar, ulps)
    d = decide(
        passed(), (lower, 2.0), min_effect=min_effect, budget_remains=False
    )
    assert d.outcome == ("KEEP" if keep else "INCONCLUSIVE")
    assert d.lower is not None
    assert d.lower.hex() == lower.hex()
    record(verdict(d), passed(), min_effect=min_effect)


@pytest.mark.parametrize("min_effect", [0.03, 0.0])
@pytest.mark.parametrize(
    ("ulps", "reject"), [(-1, True), (0, True), (1, False)]
)
def test_upper_bound_at_the_bar(
    min_effect: float, ulps: int, reject: bool
) -> None:
    bar = 1 + min_effect
    upper = step(bar, ulps)
    d = decide(
        passed(), (0.5, upper), min_effect=min_effect, budget_remains=False
    )
    assert d.outcome == ("REJECT" if reject else "INCONCLUSIVE")
    assert d.rule == (3 if reject else 5)
    assert d.upper is not None
    assert d.upper.hex() == upper.hex()
    record(verdict(d), passed(), min_effect=min_effect)


@pytest.mark.parametrize("min_effect", [0.03, 0.0])
def test_interval_collapsed_on_the_bar_is_a_reject(min_effect: float) -> None:
    bar = 1 + min_effect
    d = decide(
        passed(), (bar, bar), min_effect=min_effect, budget_remains=True
    )
    assert (d.outcome, d.rule) == ("REJECT", 3)
    record(verdict(d), passed(), min_effect=min_effect)


@pytest.mark.parametrize(
    ("upper", "regression"),
    [(1.0, False), (math.nextafter(1.0, 0), True)],
)
@pytest.mark.parametrize("min_effect", [0.03, 0.0])
def test_regression_is_exactly_upper_below_one(
    upper: float, regression: bool, min_effect: float
) -> None:
    d = decide(
        passed(), (0.9, upper), min_effect=min_effect, budget_remains=True
    )
    assert (d.outcome, d.rule) == ("REJECT", 3)
    assert d.regression is regression
    record(verdict(d), passed(), min_effect=min_effect)


@pytest.mark.parametrize("min_effect", [0.118, 0.124])
def test_bar_is_one_plus_min_effect_not_exp_log1p(min_effect: float) -> None:
    # For these values exp(log1p(m)) and 1 + m differ by one ulp, so a
    # bar computed on the log scale would decide differently.
    bar = 1 + min_effect
    via_log = math.exp(math.log1p(min_effect))
    assert via_log != bar
    d = decide(
        passed(), (via_log, 2.0), min_effect=min_effect, budget_remains=False
    )
    assert (d.outcome == "KEEP") is (via_log > bar)
    d = decide(
        passed(), (0.5, via_log), min_effect=min_effect, budget_remains=False
    )
    assert (d.outcome == "REJECT") is (via_log <= bar)
    record(verdict(d), passed(), min_effect=min_effect)


def test_bounds_pass_through_bit_identical() -> None:
    lower = math.exp(math.log(1.03) - 1e-17)
    upper = math.exp(math.log1p(0.2) + 3e-16)
    d = decide(
        passed(),
        (np.float64(lower), np.float64(upper)),
        min_effect=0.03,
        budget_remains=False,
    )
    assert type(d.lower) is float
    assert type(d.upper) is float
    assert d.lower is not None
    assert d.upper is not None
    assert (d.lower.hex(), d.upper.hex()) == (lower.hex(), upper.hex())


# Gates


@pytest.mark.parametrize("cross", [False, True])
@pytest.mark.parametrize("budget", [False, True])
@pytest.mark.parametrize(
    "failed", ["correctness", "lock", "procedure", "runtime", "same_host"]
)
def test_failed_gate_overrides_a_keep_worthy_interval(
    cross: bool, budget: bool, failed: GateName
) -> None:
    gates = with_failure(passed(cross=cross), failed)
    pairs = N0 + 5 if cross else None
    keep = decide(
        passed(cross=cross),
        (1.2, 1.3),
        min_effect=0.03,
        budget_remains=budget,
        session_pairs=pairs,
        n0=N0 if cross else None,
    )
    assert keep.outcome == "KEEP"
    d = decide(
        gates,
        (1.2, 1.3),
        min_effect=0.03,
        budget_remains=budget,
        session_pairs=pairs,
        n0=N0 if cross else None,
    )
    reason = "correctness" if failed == "correctness" else "integrity"
    assert (d.outcome, d.reason, d.rule, d.gate) == (
        "REJECT",
        reason,
        1,
        failed,
    )
    assert (d.lower, d.upper, d.regression) == (None, None, False)
    # A failed same_host gate ends the cross-host design: the verdict
    # is not cross host, as RunRecord requires.
    assert d.session_pairs == (None if failed == "same_host" else pairs)
    record(verdict(d), gates)


def test_failed_same_host_ends_cross_host_with_another_failure() -> None:
    gates = with_failure(passed(cross=True), "lock", "same_host")
    d = decide(
        gates,
        None,
        min_effect=0.03,
        budget_remains=False,
        session_pairs=0,
        n0=N0,
    )
    assert (d.gate, d.session_pairs) == ("lock", None)
    record(verdict(d), gates)


@pytest.mark.parametrize("failed", [None, "lock"])
def test_unknown_gate_status_raises(failed: GateName | None) -> None:
    # GateStatus has three values today. A new one must neither pass a
    # gate nor be reported as a definite correctness failure.
    unknown = GateResult.model_construct(
        gate="correctness", status="error", detail="crashed"
    )
    gates = (unknown, *passed()[1:])
    if failed is not None:
        gates = with_failure(gates, failed)
    with pytest.raises(ValueError, match="input_value='error'"):
        decide(gates, (1.2, 1.3), min_effect=0.03, budget_remains=True)


@pytest.mark.parametrize("name", ["speed", ["lock"]])
def test_unknown_gate_name_raises(name: object) -> None:
    # The precedence scan only visits known names, so an unknown failed
    # gate must be refused rather than skipped into a KEEP.
    unknown = GateResult.model_construct(
        gate=name, status="failed", detail="x"
    )
    with pytest.raises(ValueError, match=re.escape(repr(name))):
        decide(
            (*passed(), unknown),
            (1.2, 1.3),
            min_effect=0.03,
            budget_remains=True,
        )


@pytest.mark.parametrize("gate", ["correctness", "lock", "procedure"])
def test_waived_gate_that_cannot_be_waived_raises(gate: GateName) -> None:
    # Only runtime and same_host can be waived. A waiver of any other
    # gate, which only unvalidated input can carry, must not pass it.
    waived = GateResult.model_construct(gate=gate, status="waived", detail="x")
    gates = tuple(waived if g.gate == gate else g for g in passed())
    with pytest.raises(ValueError, match="cannot be waived"):
        decide(gates, (1.2, 1.3), min_effect=0.03, budget_remains=True)


@pytest.mark.parametrize("interval", [(0.0, 1.0), (1.3, 1.2), (math.nan, 1.0)])
def test_invalid_interval_raises_even_when_a_gate_failed(
    interval: tuple[float, float],
) -> None:
    gates = with_failure(passed(), "correctness")
    with pytest.raises(ValueError, match="interval"):
        decide(gates, interval, min_effect=0.03, budget_remains=True)


@pytest.mark.parametrize("interval", [None, (1.2, 1.3)])
@pytest.mark.parametrize("failed", [(), ("lock",)])
def test_negative_session_pairs_raise_on_every_path(
    interval: tuple[float, float] | None, failed: tuple[GateName, ...]
) -> None:
    # Rule 1 and the no-interval path never reach interval_rules, so
    # decide must refuse a negative count itself.
    gates = with_failure(passed(cross=True), *failed)
    with pytest.raises(ValueError, match="got -1"):
        decide(
            gates,
            interval,
            min_effect=0.03,
            budget_remains=True,
            session_pairs=-1,
            n0=N0,
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"gate": "lock", "detail": "x"},
        {"gate": "lock", "status": "failed"},
    ],
)
def test_gate_missing_a_required_field_raises(fields: dict[str, Any]) -> None:
    broken = GateResult.model_construct(**fields)
    gates = (*passed()[:1], broken, *passed()[2:])
    with pytest.raises(ValueError, match="lock"):
        decide(gates, (1.2, 1.3), min_effect=0.03, budget_remains=True)


def test_gates_are_read_once() -> None:
    gates = with_failure(passed(cross=True), "same_host")
    d = decide(
        iter(gates),  # pyright: ignore[reportArgumentType]
        None,
        min_effect=0.03,
        budget_remains=False,
        session_pairs=N0,
        n0=N0,
    )
    assert (d.gate, d.session_pairs) == ("same_host", None)


# Written out, not read from GATE_PRECEDENCE, so a changed order fails.
PRECEDENCE: tuple[GateName, ...] = (
    "lock",
    "procedure",
    "runtime",
    "same_host",
    "correctness",
)


def test_gate_precedence_is_integrity_first_and_names_every_gate() -> None:
    assert GATE_PRECEDENCE == PRECEDENCE
    assert sorted(GATE_PRECEDENCE) == sorted(get_args(GateName))


def test_lock_and_correctness_failing_report_lock_in_either_order() -> None:
    # The run order decided before GATE_PRECEDENCE: this pair reported
    # correctness or integrity depending on which gate ran first.
    gates = with_failure(passed(), "lock", "correctness")
    for order in (gates, gates[::-1]):
        d = decide(order, None, min_effect=0.03, budget_remains=False)
        assert (d.gate, d.reason) == ("lock", "integrity")


FAILED_SETS = [
    failed
    for size in range(1, len(EVERY_GATE) + 1)
    for failed in itertools.combinations(EVERY_GATE, size)
]


@pytest.mark.parametrize("cross", [False, True])
@pytest.mark.parametrize("interval", [(1.2, 1.3), (0.8, 0.9), None])
@pytest.mark.parametrize("failed", FAILED_SETS)
def test_failed_gates_decide_alike_in_every_run_order(
    cross: bool,
    interval: tuple[float, float] | None,
    failed: tuple[GateName, ...],
) -> None:
    gates = with_failure(passed(cross=cross), *failed)
    decisions = {
        decide(
            order,
            interval,
            min_effect=0.03,
            budget_remains=True,
            session_pairs=N0 if cross else None,
            n0=N0 if cross else None,
        )
        for order in itertools.permutations(gates)
    }
    assert len(decisions) == 1
    (d,) = decisions
    gate = next(g for g in PRECEDENCE if g in failed)
    reason = "correctness" if gate == "correctness" else "integrity"
    assert (d.outcome, d.rule, d.gate, d.reason) == (
        "REJECT",
        1,
        gate,
        reason,
    )
    assert (d.lower, d.upper, d.regression) == (None, None, False)
    cross_after = cross and "same_host" not in failed
    assert d.session_pairs == (N0 if cross_after else None)
    rec = record(verdict(d), gates)
    assert rec.verdict.estimate is None
    assert rec.verdict.interval is None


@pytest.mark.parametrize("failed", EVERY_GATE[:4])
def test_gate_fails_before_any_remote_host(failed: GateName) -> None:
    # The same_host waiver comes from the spec, so it is known before
    # any host exists; the failed gate is the only other gate that ran.
    gates = (
        GateResult(gate="same_host", status="waived", detail="cross-host"),
        GateResult(gate=failed, status="failed", detail="differs"),
    )
    d = decide(
        gates,
        None,
        min_effect=0.03,
        budget_remains=True,
        session_pairs=0,
        n0=N0,
    )
    assert (d.outcome, d.rule, d.gate, d.session_pairs) == (
        "REJECT",
        1,
        failed,
        0,
    )
    rec = record(verdict(d), gates, machines=(machine(),))
    assert rec.verdict.rounds == 0
    assert rec.verdict.session_pairs == 0
    assert len(rec.machines) == 1
    assert rec.machines[0].host_id == "gate-runner"


# Cross host


@pytest.mark.parametrize("pairs", [0, 1, N0 - 1])
@pytest.mark.parametrize(
    "interval", [(1.2, 1.3), (0.8, 0.9), (1.03, 1.03), (1.0, 1.1), None]
)
def test_below_n0_pairs_never_decide_on_speed(
    pairs: int, interval: tuple[float, float] | None
) -> None:
    # Continue while budget remains; INCONCLUSIVE once it is out. Never
    # KEEP or a performance REJECT, whatever the interval.
    for budget, outcome, rule in (
        (True, "CONTINUE", 4),
        (False, "INCONCLUSIVE", 5),
    ):
        d = decide(
            passed(cross=True),
            interval,
            min_effect=0.03,
            budget_remains=budget,
            session_pairs=pairs,
            n0=N0,
        )
        assert (d.outcome, d.rule, d.regression) == (outcome, rule, False)
        assert (d.lower, d.upper) == (interval or (None, None))
        assert d.session_pairs == pairs
    record(verdict(d), passed(cross=True))


@pytest.mark.parametrize("pairs", [1, N0 - 1])
@pytest.mark.parametrize("failed", EVERY_GATE)
def test_gate_failure_rejects_below_n0_pairs(
    pairs: int, failed: GateName
) -> None:
    gates = with_failure(passed(cross=True), failed)
    d = decide(
        gates,
        (1.2, 1.3),
        min_effect=0.03,
        budget_remains=True,
        session_pairs=pairs,
        n0=N0,
    )
    assert (d.outcome, d.rule, d.gate) == ("REJECT", 1, failed)
    record(verdict(d), gates)


@pytest.mark.parametrize(
    ("pairs", "budget", "outcome"),
    [
        (N0 - 1, False, "INCONCLUSIVE"),
        (N0 - 1, True, "CONTINUE"),
        (N0, False, "KEEP"),
        (N0, True, "KEEP"),
        (N0 + 1, False, "KEEP"),
        (N0 + 1, True, "KEEP"),
    ],
)
def test_cross_host_keep_needs_n0_pairs(
    pairs: int, budget: bool, outcome: str
) -> None:
    d = decide(
        passed(cross=True),
        (1.2, 1.3),
        min_effect=0.03,
        budget_remains=budget,
        session_pairs=pairs,
        n0=N0,
    )
    assert d.outcome == outcome
    assert d.session_pairs == pairs
    assert (d.lower, d.upper) == (1.2, 1.3)
    if outcome != "CONTINUE":
        record(verdict(d), passed(cross=True))


@pytest.mark.parametrize(
    ("pairs", "outcome", "regression"),
    [
        (N0 - 1, "INCONCLUSIVE", False),
        (N0, "REJECT", True),
        (N0 + 1, "REJECT", True),
    ],
)
def test_cross_host_reject_needs_n0_pairs(
    pairs: int, outcome: str, regression: bool
) -> None:
    d = decide(
        passed(cross=True),
        (0.8, 0.9),
        min_effect=0.03,
        budget_remains=False,
        session_pairs=pairs,
        n0=N0,
    )
    assert (d.outcome, d.regression) == (outcome, regression)
    record(verdict(d), passed(cross=True))


@pytest.mark.parametrize("pairs", [0, 1])
def test_cross_host_without_an_interval(pairs: int) -> None:
    d = decide(
        passed(cross=True),
        None,
        min_effect=0.03,
        budget_remains=False,
        session_pairs=pairs,
        n0=N0,
    )
    assert (d.outcome, d.rule, d.lower, d.upper) == (
        "INCONCLUSIVE",
        5,
        None,
        None,
    )
    record(verdict(d), passed(cross=True))


@pytest.mark.parametrize("budget", [True, False])
def test_no_interval_never_decides_on_speed(budget: bool) -> None:
    d = decide(passed(), None, min_effect=0.03, budget_remains=budget)
    assert d.outcome == ("CONTINUE" if budget else "INCONCLUSIVE")
    assert d.reason == (None if budget else "insufficient_evidence")
    assert (d.lower, d.upper, d.regression) == (None, None, False)


# Completed session pairs

B: Arm = "baseline"
C: Arm = "candidate"


@pytest.mark.parametrize(
    ("sessions", "pairs"),
    [
        ([], 0),
        ([(B, True)], 0),
        ([(B, True), (C, True)], 1),
        ([(B, True), (C, True), (B, True), (C, True)], 2),
        # A failed middle session voids its own pair only.
        (
            [
                (B, True),
                (C, True),
                (B, False),
                (C, True),
                (B, True),
                (C, True),
            ],
            2,
        ),
        # A trailing baseline with no candidate after it is unpaired.
        ([(B, True), (C, True), (B, True)], 1),
        # An incomplete candidate voids its pair.
        ([(B, True), (C, False)], 0),
        ([(B, True), (C, True), (B, True), (C, False)], 1),
        # Never re-paired across a gap: B0 and C3 both completed, but
        # they were not launched back to back.
        ([(B, True), (C, False), (B, False), (C, True)], 0),
        (
            [
                (B, False),
                (C, True),
                (B, True),
                (C, False),
                (B, True),
                (C, True),
            ],
            1,
        ),
    ],
)
def test_completed_session_pairs(
    sessions: list[tuple[Arm, bool]], pairs: int
) -> None:
    assert completed_session_pairs(sessions) == pairs


def test_unallocated_host_is_not_a_session() -> None:
    # The candidate's first host was never allocated, so it is left out;
    # the candidate launched in its place pairs with the baseline.
    assert completed_session_pairs([(B, True), (C, True), (B, True)]) == 1
    # Recorded as a failed session instead, the retry breaks the
    # alternation, which is refused rather than miscounted.
    with pytest.raises(ValueError, match="'baseline' was due"):
        completed_session_pairs([(B, True), (C, False), (C, True)])


def test_gate_failed_session_voids_its_pair() -> None:
    # A session whose gate failed on its host is started, not completed.
    sessions: list[tuple[Arm, bool]] = [
        (B, False),
        (C, True),
        (B, True),
        (C, True),
    ]
    assert completed_session_pairs(sessions) == 1


@pytest.mark.parametrize(
    ("sessions", "named"),
    [
        ([(C, True), (B, True)], "candidate"),
        ([(B, True), (B, True)], "baseline"),
        ([(B, True), (C, True), (C, True)], "candidate"),
        ([(B, 1)], "1"),
        ([("control", True)], "control"),
        ([(B, True, 3)], "3"),
    ],
)
def test_completed_session_pairs_refuses_bad_sessions(
    sessions: list[Any], named: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(named)):
        completed_session_pairs(sessions)


# Invalid parameters


def call(**update: Any) -> Decision:
    kwargs: dict[str, Any] = {
        "gates": passed(),
        "interval": (1.0, 1.1),
        "min_effect": 0.03,
        "budget_remains": True,
    }
    kwargs.update(update)
    return decide(**kwargs)


@pytest.mark.parametrize(
    ("update", "named"),
    [
        ({"min_effect": -0.01}, "-0.01"),
        ({"min_effect": math.nan}, "nan"),
        ({"min_effect": math.inf}, "inf"),
        ({"min_effect": True}, "True"),
        ({"min_effect": "0.03"}, "'0.03'"),
        ({"interval": (1.1, 1.0)}, "(1.1, 1.0)"),
        ({"interval": (0.0, 1.0)}, "0.0"),
        ({"interval": (-1.0, 1.0)}, "-1.0"),
        ({"interval": (math.nan, 1.0)}, "nan"),
        ({"interval": (1.0, math.inf)}, "inf"),
        ({"interval": (1.0,)}, "(1.0,)"),
        ({"interval": (True, 1.0)}, "True"),
        ({"interval": ("1.0", 1.1)}, "'1.0'"),
        ({"budget_remains": 1}, "1"),
        ({"budget_remains": None}, "None"),
        ({"session_pairs": -1, "n0": N0}, "-1"),
        ({"session_pairs": True, "n0": N0}, "True"),
        ({"session_pairs": 12.0, "n0": N0}, "12.0"),
        ({"session_pairs": 12, "n0": N0 - 1}, str(N0 - 1)),
        ({"session_pairs": 12, "n0": 10.0}, "10.0"),
        ({"session_pairs": 12}, "n0"),
        ({"n0": N0}, "session_pairs"),
    ],
)
def test_invalid_parameters_raise(update: dict[str, Any], named: str) -> None:
    if "session_pairs" in update and "gates" not in update:
        update = {**update, "gates": passed(cross=True)}
    with pytest.raises(ValueError, match=re.escape(named)):
        call(**update)


@pytest.mark.parametrize(
    ("gates", "update", "named"),
    [
        ((*passed(), passed()[1]), {}, "lock"),
        (passed()[1:], {}, "correctness"),
        ((), {}, "correctness"),
        (passed(cross=True), {}, "same_host"),
        (passed(), {"session_pairs": 12, "n0": N0}, "same_host"),
        (
            with_failure(passed(cross=True), "lock"),
            {},
            "same_host",
        ),
        (
            (GateResult(gate="lock", status="failed", detail="x"),),
            {"session_pairs": 0, "n0": N0},
            "same_host",
        ),
        (("lock",), {}, "'lock'"),
    ],
)
def test_inconsistent_gates_raise(
    gates: tuple[Any, ...], update: dict[str, Any], named: str
) -> None:
    with pytest.raises(ValueError, match=re.escape(named)):
        call(gates=gates, **update)


# Batch form


def grid(bar: float) -> list[float]:
    points = [step(bar, k) for k in range(-2, 3)]
    points += [step(1.0, k) for k in range(-2, 3)]
    points += [math.exp(step(math.log(bar), k)) for k in (-1, 1)]
    return [*points, 0.5, 2.0]


@pytest.mark.parametrize("min_effect", [0.03, 0.0, 0.118])
def test_interval_rules_agree_with_decide(min_effect: float) -> None:
    points = grid(1 + min_effect)
    pairs = [(lo, hi) for lo in points for hi in points if lo <= hi]
    lower = np.array([p[0] for p in pairs])
    upper = np.array([p[1] for p in pairs])
    for budget in (False, True):
        for session_pairs in (None, N0 - 1, N0):
            rules = interval_rules(
                lower,
                upper,
                min_effect=min_effect,
                budget_remains=budget,
                session_pairs=session_pairs,
                n0=None if session_pairs is None else N0,
            )
            for i, bounds in enumerate(pairs):
                d = decide(
                    passed(cross=session_pairs is not None),
                    bounds,
                    min_effect=min_effect,
                    budget_remains=budget,
                    session_pairs=session_pairs,
                    n0=None if session_pairs is None else N0,
                )
                assert rules.rule[i] == d.rule
                assert bool(rules.regression[i]) is d.regression
                # decide calls interval_rules, so also check both
                # against the spec in exact rational arithmetic.
                assert d.rule == exact_rule(
                    bounds, min_effect, budget, session_pairs
                )
                assert d.regression is (
                    d.rule == 3 and Fraction(bounds[1]) < 1
                )


def exact_rule(
    bounds: tuple[float, float],
    min_effect: float,
    budget: bool,
    session_pairs: int | None,
) -> int:
    """Rules 2 to 5 of ``docs/method.md`` with Fraction comparisons."""
    bar = Fraction(1 + min_effect)
    enough = session_pairs is None or session_pairs >= N0
    if enough and Fraction(bounds[0]) > bar:
        return 2
    if enough and Fraction(bounds[1]) <= bar:
        return 3
    return 4 if budget else 5


def test_interval_rules_broadcast_a_batch() -> None:
    lower = np.array([[1.05, 1.02], [0.91, 0.98]])
    upper = np.array([[1.11, 1.08], [0.97, 1.21]])
    budget = np.array([[True, False], [True, True]])
    pairs = np.array([N0, N0 - 1])
    rules = interval_rules(
        lower,
        upper,
        min_effect=0.03,
        budget_remains=budget,
        session_pairs=pairs,
        n0=N0,
    )
    assert rules.rule.dtype == np.int8
    np.testing.assert_array_equal(rules.rule, [[2, 5], [3, 4]])
    np.testing.assert_array_equal(
        rules.regression, [[False, False], [True, False]]
    )


@pytest.mark.parametrize(
    ("update", "named"),
    [
        ({"lower": np.array([1.2, 1.0])}, "1.2"),
        ({"upper": np.array([1.1, np.nan])}, "nan"),
        ({"lower": np.array([0.0, 1.0])}, "0.0"),
        ({"budget_remains": np.array([1, 0])}, "int64"),
        ({"session_pairs": np.array([3, -1]), "n0": N0}, "-1"),
        ({"session_pairs": np.array([3.0, 1.0]), "n0": N0}, "float64"),
        ({"upper": np.array([1.1, 1.2, 1.3])}, "shape"),
        ({"upper": np.array([[1.1], [1.2]])}, "shape"),
        ({"lower": np.array(["1.0", "1.0"])}, "<U3"),
        ({"lower": np.array([1, 1])}, "int64"),
        ({"upper": np.array([True, True])}, "bool"),
    ],
)
def test_interval_rules_refuse_bad_input(
    update: dict[str, Any], named: str
) -> None:
    kwargs: dict[str, Any] = {
        "lower": np.array([1.0, 1.0]),
        "upper": np.array([1.1, 1.1]),
        "min_effect": 0.03,
        "budget_remains": False,
    }
    kwargs.update(update)
    with pytest.raises(ValueError, match=re.escape(named)):
        interval_rules(**kwargs)


@pytest.mark.skipif(
    np.dtype(np.longdouble) == np.dtype(np.float64),
    reason="longdouble is float64 on this platform, so it is exact",
)
def test_interval_rules_refuse_longdouble() -> None:
    upper = np.array([1.1], dtype=np.longdouble)
    with pytest.raises(ValueError, match="float64 or narrower"):
        interval_rules(
            np.array([1.0]), upper, min_effect=0.03, budget_remains=False
        )


# From observations to the stored record

FOUND_FALSE_KEEP = 0.4730308035801494


def stand_in_t_interval(
    ratios: list[float], confidence: float
) -> tuple[float, float]:
    """Return a per-look t interval like the method's, for tests only.

    The t interval on the mean log ratio at the given confidence,
    exponentiated; no look schedule. T1.1 owns the real procedure;
    this exists to drive `decide` from observations. It
    shows the verdict agrees with what is stored, not that the interval
    is statistically valid.
    """
    d = np.log(np.asarray(ratios, dtype=np.float64))
    n = d.size
    ppf = cast(
        "Callable[[float, int], float]",
        stats.t.ppf,  # pyright: ignore[reportUnknownMemberType]
    )
    quantile = float(ppf(0.5 + confidence / 2, n - 1))
    half = quantile * float(np.std(d, ddof=1)) / math.sqrt(n)
    mean = float(np.mean(d))
    return math.exp(mean - half), math.exp(mean + half)


def at_bar_observations() -> list[tuple[float, list[float]]]:
    """Rounds with zero or tiny noise at, and an ulp off, the bar."""
    cases: list[tuple[float, list[float]]] = []
    rng = np.random.default_rng(20261015)
    for min_effect in (0.0, 0.03, 0.118, 0.124, FOUND_FALSE_KEEP):
        bar = 1 + min_effect
        for n in (10, 48, 200):
            cases.extend(
                (min_effect, [step(bar, ulps)] * n) for ulps in (-1, 0, 1)
            )
            for cv in (1e-12, 1e-3):
                noise = rng.normal(0.0, cv, n)
                cases.append((min_effect, list(bar * np.exp(noise))))
        # A deterministic counter, 1000 vs round(1000 * bar) events. The
        # ratio can land an ulp off the float bar: one up at 0.118.
        events = round(1000 * (1 + min_effect))
        cases.append((min_effect, [events / 1000] * 10))
    return cases


@pytest.mark.parametrize(("min_effect", "ratios"), at_bar_observations())
def test_verdict_from_observations_matches_the_stored_record(
    min_effect: float, ratios: list[float]
) -> None:
    # For FOUND_FALSE_KEEP, zero noise exactly at the bar, n = 48, the
    # stand-in put L one ulp above the bar on macOS arm64, so the rule
    # said KEEP when S equals the bar. exp(log(bar)) is exact there; the
    # ulp comes from np.mean of 48 identical logs, which rounds up and
    # leaves a 5.6e-17 standard deviation. That is the interval stage,
    # which T1.1 owns; the decision agrees with the bound it stored.
    lower, upper = stand_in_t_interval(ratios, confidence=0.99)
    d = decide(
        passed(),
        (lower, upper),
        min_effect=min_effect,
        budget_remains=False,
    )
    bar = 1 + min_effect
    expected = 2 if lower > bar else 3 if upper <= bar else 5
    assert d.rule == expected
    assert d.regression is (expected == 3 and upper < 1)
    rec = record(verdict(d), passed(), min_effect=min_effect)
    stored = RunRecord.model_validate_json(rec.model_dump_json())
    assert stored == rec
    assert stored.verdict.interval is not None
    assert stored.verdict.interval.lower.hex() == lower.hex()
    assert stored.verdict.interval.upper.hex() == upper.hex()


# Property: every verdict validates as a RunRecord; no other rule does

FAILED_BAR = r"contradicts the interval|fewer than n0"


@st.composite
def near(draw: st.DrawFn, target: float) -> float:
    """Draw a float64 at, or a few ulps or a sliver from, ``target``."""
    kind = draw(st.sampled_from(["ulps", "exp_log", "exp_log1p", "rel"]))
    k = draw(st.integers(-3, 3))
    if kind == "ulps":
        return step(target, k)
    if kind == "exp_log":
        return math.exp(step(math.log(target), k))
    if kind == "exp_log1p":
        return math.exp(step(math.log1p(target - 1), k))
    return target * math.exp(draw(st.floats(-1e-6, 1e-6)))


@st.composite
def cases(draw: st.DrawFn) -> tuple[float, float, float, int | None]:
    min_effect = draw(
        st.one_of(
            st.sampled_from([0.0, 0.03, 0.118, 0.124]),
            st.floats(0, 0.3, allow_subnormal=False),
        )
    )
    bar = 1 + min_effect
    point = st.one_of(near(bar), near(1.0), st.floats(0.5, 2.0))
    if draw(st.booleans()):
        # A log-scale interval around a centre near the bar, then exp.
        logs = [math.log(bar), math.log1p(min_effect)]
        centre = step(draw(st.sampled_from(logs)), draw(st.integers(-3, 3)))
        half = draw(st.one_of(st.just(0.0), st.floats(0, 1e-3)))
        lower, upper = math.exp(centre - half), math.exp(centre + half)
    else:
        lower, upper = sorted((draw(point), draw(point)))
    pairs = draw(
        st.one_of(
            st.none(),
            st.sampled_from([N0 - 1, N0, N0 + 1]),
            st.integers(0, 2 * N0),
        )
    )
    return min_effect, lower, upper, pairs


def with_rule(v: Verdict, rule: int) -> Verdict:
    """Return ``v`` under another rule, all else consistent."""
    outcome, reason = {
        2: ("KEEP", "performance"),
        3: ("REJECT", "performance"),
        5: ("INCONCLUSIVE", "insufficient_evidence"),
    }[rule]
    assert v.interval is not None
    return v.model_copy(
        update={
            "rule": rule,
            "outcome": outcome,
            "reason": reason,
            "regression": rule == 3 and v.interval.upper < 1,
        }
    )


@settings(max_examples=400, derandomize=True, deadline=None)
@given(cases())
def test_every_verdict_validates_and_no_other_rule_does(
    case: tuple[float, float, float, int | None],
) -> None:
    min_effect, lower, upper, pairs = case
    cross = pairs is not None
    gates = passed(cross=cross)
    n0 = N0 if cross else None
    d = decide(
        gates,
        (lower, upper),
        min_effect=min_effect,
        budget_remains=False,
        session_pairs=pairs,
        n0=n0,
    )
    assert d.lower is not None
    assert d.upper is not None
    assert (d.lower.hex(), d.upper.hex()) == (lower.hex(), upper.hex())
    v = verdict(d)
    record(v, gates, min_effect=min_effect)
    for other in {2, 3, 5} - {v.rule}:
        with pytest.raises(ValidationError, match=FAILED_BAR):
            record(with_rule(v, other), gates, min_effect=min_effect)
    with_budget = decide(
        gates,
        (lower, upper),
        min_effect=min_effect,
        budget_remains=True,
        session_pairs=pairs,
        n0=n0,
    )
    if d.rule == 5:
        assert (with_budget.outcome, with_budget.rule) == ("CONTINUE", 4)
    else:
        assert with_budget == d
