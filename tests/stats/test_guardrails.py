"""Tests of the guardrails and the guardrail step of the decision rule.

``docs/method.md``, "Guardrails", is the specification: every row of its
status table and its decision list is a test here, beside the
invariants of the intervals and the schema's reading of the results.
"""

import itertools
import math
from collections.abc import Sequence
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray
from pydantic import ValidationError

from perfproof.schema import (
    DEFAULT_GUARDRAILS,
    AbsoluteLimit,
    Direction,
    GateResult,
    GuardrailResult,
    GuardrailSpec,
    GuardrailStatus,
    Interval,
    Procedure,
    _check_guardrail_status,  # pyright: ignore[reportPrivateUsage]
    _check_standing,  # pyright: ignore[reportPrivateUsage]
)
from perfproof.stats.decision import (
    GATE_PRECEDENCE,
    UNCHECKED,
    decide,
    guardrail_actions,
)
from perfproof.stats.guardrails import (
    HAC_MAX_CHECKS,
    PROVISIONAL_ALLOWANCES,
    TARGET_CLASS_ALLOWANCES,
    GuardrailLook,
    _limit_state,  # pyright: ignore[reportPrivateUsage]
    _relative_state,  # pyright: ignore[reportPrivateUsage]
    check_count,
    earned_status,
    evaluate_guardrail,
    guardrail_confidence,
    guardrail_procedure,
    guardrail_result,
    limit_bounds,
    rank_bounds,
    resolve_allowance,
)
from perfproof.stats.intervals import (
    hac_lf_interval,
    paired_ratios,
    t_interval,
)
from perfproof.stats.sequential import (
    BATCH_BLOCK,
    look_confidence,
    procedure_for_schedule,
)

FloatArray = NDArray[np.float64]
GATES: tuple[str, ...] = tuple(GATE_PRECEDENCE)
CONF = look_confidence(0.05, 3)
HAC: Procedure = "hac_lf"
BATCH: Procedure = "t_log_ratio_batch_means"


def spec(**changes: Any) -> GuardrailSpec:
    fields: dict[str, Any] = {
        "metric": "peak_memory",
        "direction": "lower_is_better",
        "unit": "bytes",
        "allowance": 0.10,
        "allowance_source": "default",
    }
    fields.update(changes)
    return GuardrailSpec.model_validate(fields)


def pairs(
    ratio: float,
    *,
    rows: int = 4,
    n: int = 24,
    level: float = 100e6,
    sd: float = 0.01,
    seed: int = 0,
) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    """Paired values whose candidate is ``ratio`` times the baseline."""
    rng = np.random.default_rng(seed)
    base = level * np.exp(sd * rng.standard_normal((rows, n)))
    cand = base * ratio * np.exp(sd * rng.standard_normal((rows, n)))
    first = np.zeros((rows, n), dtype=np.bool_)
    first[:, 0::2] = True
    return base, cand, first


def evaluate(
    base: FloatArray,
    cand: FloatArray,
    first: NDArray[np.bool_],
    *,
    allowance: float | None = 0.10,
    limits: Sequence[float] = (),
    direction: Direction = "lower_is_better",
    procedure: Procedure = HAC,
    confidence: float = CONF,
    **extra: Any,
) -> GuardrailLook:
    return evaluate_guardrail(
        base,
        cand,
        direction=direction,
        allowance=allowance,
        limits=limits,
        confidence=confidence,
        procedure=procedure,
        final=np.zeros(base.shape[0], dtype=np.bool_),
        candidate_first=first,
        **extra,
    )


# Allowances and their sources


def test_the_provisional_allowances_are_the_documented_ones() -> None:
    assert dict(PROVISIONAL_ALLOWANCES) == {
        "peak_memory": 0.10,
        "peak_gpu_memory": 0.10,
        "p99_latency": 0.05,
    }
    for names in DEFAULT_GUARDRAILS.values():
        assert set(names) <= set(PROVISIONAL_ALLOWANCES)


def test_an_allowance_records_where_it_came_from() -> None:
    assert resolve_allowance("peak_memory") == (0.10, "default")
    assert resolve_allowance("p99_latency", target_class="web_service") == (
        0.05,
        "default",
    )
    assert resolve_allowance("peak_memory", project=0.02) == (0.02, "project")
    assert resolve_allowance("throughput") is None
    assert resolve_allowance("throughput", project=0.0) == (0.0, "project")


def test_the_project_overrides_the_target_class_which_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from perfproof.stats import guardrails

    monkeypatch.setattr(
        guardrails,
        "TARGET_CLASS_ALLOWANCES",
        {"gpu_kernel": {"peak_memory": 0.25}},
    )
    assert resolve_allowance("peak_memory", target_class="gpu_kernel") == (
        0.25,
        "target_class",
    )
    assert resolve_allowance("peak_memory", target_class="native") == (
        0.10,
        "default",
    )
    assert resolve_allowance(
        "peak_memory", target_class="gpu_kernel", project=0.01
    ) == (0.01, "project")
    assert dict(TARGET_CLASS_ALLOWANCES) == {}


@pytest.mark.parametrize("bad", [-0.1, math.nan, math.inf, 1, "0.1"])
def test_a_project_allowance_must_be_a_finite_float_of_at_least_zero(
    bad: Any,
) -> None:
    with pytest.raises(ValueError, match="project allowance"):
        resolve_allowance("peak_memory", project=bad)


def test_the_goal_defaults_take_their_allowance_and_source_from_here() -> None:
    from perfproof.project.goals import default_guardrails, parse_guardrail

    found = {
        g.metric: (g.allowance, g.allowance_source)
        for g in default_guardrails("llm_serving", "throughput")
    }
    assert found == {
        "peak_memory": (0.10, "default"),
        "peak_gpu_memory": (0.10, "default"),
        "p99_latency": (0.05, "default"),
    }
    chosen = parse_guardrail("p99=0.2")
    assert (chosen.allowance, chosen.allowance_source) == (0.2, "project")


# G, the confidence and the procedure


def test_g_counts_every_allowance_and_limit_of_an_applicable_guardrail() -> (
    None
):
    off = GuardrailSpec.model_validate(
        {
            "metric": "peak_gpu_memory",
            "direction": "lower_is_better",
            "unit": "bytes",
            "applicable": False,
            "reason": "no GPU",
        }
    )
    ceiling = AbsoluteLimit(value=8e9, label="container limit")
    guards = [
        spec(),
        spec(metric="p99_latency", unit="s", limits=(ceiling,)),
        spec(
            metric="x",
            allowance=None,
            allowance_source=None,
            limits=(ceiling, ceiling),
        ),
        off,
    ]
    assert check_count(guards) == 1 + 2 + 2
    assert check_count([off]) == 0
    assert check_count([]) == 0


def test_the_confidence_is_one_minus_alpha_over_two_k_g() -> None:
    for k, g in itertools.product((1, 3, 6), (1, 2, 5)):
        assert guardrail_confidence(0.05, k, g) == look_confidence(0.05, k * g)
        assert guardrail_confidence(0.05, k, g) >= 1 - 0.05 / (2 * k * g)
    with pytest.raises(ValueError, match="checks"):
        guardrail_confidence(0.05, 3, 0)
    with pytest.raises(ValueError, match="max_looks"):
        guardrail_confidence(0.05, 0, 1)


def test_guardrails_follow_the_primary_where_hac_lf_reaches_them() -> None:
    assert HAC_MAX_CHECKS == 12
    assert guardrail_procedure(HAC, 6, 2) == HAC
    assert guardrail_procedure(HAC, 3, 4) == HAC
    assert guardrail_procedure(BATCH, 3, 1) == BATCH
    # Past the last tabulated look count there is no ``hac_lf`` table.
    assert guardrail_procedure(HAC, 6, 3) == BATCH
    assert guardrail_procedure(HAC, 3, 5) == BATCH


def test_the_shipped_schedules_get_the_procedure_of_their_default_specs() -> (
    None
):
    from perfproof.schema import LookSchedule

    web = procedure_for_schedule(LookSchedule(n0=10, k=10, max_looks=3), 30)
    assert web == HAC
    # peak memory and p99: G = 2, K G = 6, tabulated.
    assert guardrail_procedure(web, 3, 2) == HAC
    # the LLM class has three: K G = 9 uses the K = 12 level.
    assert guardrail_procedure(web, 3, 3) == HAC
    gpu = procedure_for_schedule(LookSchedule(n0=20, k=20, max_looks=6), 120)
    assert guardrail_procedure(gpu, 6, 2) == BATCH


# The interval is the schedule's procedure on the worsening ratio


@pytest.mark.parametrize("procedure", [HAC, BATCH])
def test_the_worsening_interval_is_the_procedures_interval_on_the_swapped_arms(
    procedure: Procedure,
) -> None:
    base, cand, first = pairs(1.04, rows=5, n=30)
    got = evaluate(base, cand, first, procedure=procedure)
    if procedure == HAC:
        direct = hac_lf_interval(
            cand,
            base,
            direction="lower_is_better",
            confidence=CONF,
            candidate_first=first,
            mode="one_host",
        ).interval
    else:
        direct = t_interval(
            cand,
            base,
            direction="lower_is_better",
            confidence=CONF,
            block=BATCH_BLOCK,
        )
    assert got.worsening_lower.tolist() == direct.lower.tolist()
    assert got.worsening_upper.tolist() == direct.upper.tolist()
    assert got.estimate == pytest.approx(direct.estimate, rel=1e-12)
    assert got.procedure == procedure


def test_worsening_is_candidate_over_baseline_when_lower_is_better() -> None:
    base, cand, first = pairs(1.30)
    got = evaluate(base, cand, first)
    ratios = paired_ratios(cand, base, "lower_is_better")
    assert np.all(got.worsening_lower <= np.exp(np.log(ratios).mean(axis=1)))
    assert np.all(got.worsening_lower > 1.2)
    assert np.all(got.estimate > 1.25)


def test_worsening_is_baseline_over_candidate_when_higher_is_better() -> None:
    base, cand, first = pairs(0.70)
    got = evaluate(base, cand, first, direction="higher_is_better")
    assert np.all(got.worsening_lower > 1.3)
    assert np.all(got.estimate > 1.35)
    assert set(got.status) == {"breached"}
    better = evaluate(*pairs(1.30), direction="higher_is_better")
    assert np.all(better.worsening_upper < 1.0)
    assert set(better.status) == {"passed"}


# Status: one case per row of the table in "Guardrails"


def test_within_the_allowance_is_passed() -> None:
    got = evaluate(*pairs(1.00))
    assert set(got.status) == {"passed"}
    assert np.all(got.worsening_upper <= 1.10)
    assert set(got.checks.ravel()) == {"within"}


def test_beyond_the_allowance_is_breached() -> None:
    got = evaluate(*pairs(1.25))
    assert set(got.status) == {"breached"}
    assert np.all(got.worsening_lower > 1.10)
    assert set(got.checks.ravel()) == {"breach"}


def test_an_interval_straddling_the_allowance_is_not_established() -> None:
    got = evaluate(*pairs(1.10, sd=0.03))
    assert "not_established" in set(got.status)
    straddle = got.status == "not_established"
    assert np.all(got.worsening_lower[straddle] <= 1.10)
    assert np.all(got.worsening_upper[straddle] > 1.10)
    assert np.all(got.interval)


def test_too_few_observations_are_not_established_with_no_interval() -> None:
    got = evaluate(*pairs(1.0, n=10))
    assert set(got.status) == {"not_established"}
    assert not got.interval.any()
    assert np.isnan(got.worsening_lower).all()
    assert set(got.checks.ravel()) == {"absent"}


def test_a_value_no_larger_than_its_resolution_is_unmeasurable() -> None:
    base, cand, first = pairs(1.0, level=1000.0)
    step = np.full(base.shape, 4096.0)
    got = evaluate(base, cand, first, baseline_step=step, candidate_step=step)
    assert set(got.status) == {"unmeasurable"}


def test_the_resolution_widens_the_interval_outward() -> None:
    base, cand, first = pairs(1.0, sd=0.002)
    exact = evaluate(base, cand, first)
    step = np.full(base.shape, 1e6)
    widened = evaluate(
        base, cand, first, baseline_step=step, candidate_step=step
    )
    assert np.all(widened.worsening_lower < exact.worsening_lower)
    assert np.all(widened.worsening_upper > exact.worsening_upper)


def test_identical_values_give_the_exact_interval_of_one() -> None:
    base = np.full((2, 24), 50e6)
    first = np.zeros((2, 24), dtype=np.bool_)
    first[:, 0::2] = True
    got = evaluate(base, base.copy(), first)
    assert got.worsening_lower.tolist() == [1.0, 1.0]
    assert got.worsening_upper.tolist() == [1.0, 1.0]
    assert set(got.status) == {"passed"}


def test_a_zero_allowance_refuses_any_proven_worsening() -> None:
    worse = evaluate(*pairs(1.05, sd=0.002), allowance=0.0)
    assert set(worse.status) == {"breached"}
    same = evaluate(*pairs(0.95, sd=0.002), allowance=0.0)
    assert set(same.status) == {"passed"}


def test_the_bar_is_inclusive_for_compliance_and_strict_for_a_breach() -> None:
    base = np.full((1, 24), 100.0)
    cand = np.full((1, 24), 110.0)
    first = np.zeros((1, 24), dtype=np.bool_)
    first[:, 0::2] = True
    # Every ratio is exactly 110 / 100 in float64, the bar 1 + 0.1.
    ratio = 110.0 / 100.0
    at_bar = evaluate(base, cand, first, allowance=ratio - 1.0)
    assert at_bar.worsening_lower[0] == at_bar.worsening_upper[0] == ratio
    assert at_bar.status.tolist() == ["passed"]
    just_below = evaluate(
        base, cand, first, allowance=math.nextafter(ratio, 0.0) - 1.0
    )
    assert just_below.status.tolist() == ["breached"]


# Absolute limits, on the candidate's own metric


def test_a_candidate_inside_its_ceiling_passes_it() -> None:
    got = evaluate(*pairs(1.05, n=120), limits=(200e6,))
    assert set(got.status) == {"passed"}
    assert np.all(got.candidate_upper <= 200e6)
    assert got.checks.shape == (4, 2)


def test_a_candidate_beyond_its_ceiling_is_breached() -> None:
    got = evaluate(*pairs(1.05, n=120), limits=(90e6,))
    assert set(got.status) == {"breached"}
    assert np.all(got.candidate_lower > 90e6)


def test_a_limit_breach_inside_the_relative_allowance_is_a_breach() -> None:
    # 5% worse is inside the 10% allowance, but the 100 MB ceiling is
    # beyond the candidate's own 105 MB.
    got = evaluate(*pairs(1.05, n=120), limits=(100e6,))
    assert np.all(got.checks[:, 0] == "within")
    assert np.all(got.checks[:, 1] == "breach")
    assert set(got.status) == {"breached"}


def test_a_breach_of_one_check_is_enough_with_the_other_not_established() -> (
    None
):
    got = evaluate(*pairs(1.10, sd=0.03, n=120), limits=(50e6,))
    assert np.all(got.checks[:, 1] == "breach")
    assert set(got.status) == {"breached"}


def test_a_floor_for_a_higher_is_better_metric() -> None:
    base, cand, first = pairs(1.0, level=1e3, n=120)
    fine = evaluate(
        base, cand, first, direction="higher_is_better", limits=(800.0,)
    )
    assert np.all(fine.candidate_lower >= 800.0)
    assert set(fine.status) == {"passed"}
    below = evaluate(
        base, cand, first, direction="higher_is_better", limits=(1200.0,)
    )
    assert np.all(below.candidate_upper < 1200.0)
    assert set(below.status) == {"breached"}


def test_limits_alone_need_no_allowance() -> None:
    got = evaluate(*pairs(3.0, n=120), allowance=None, limits=(1e9,))
    assert set(got.status) == {"passed"}
    assert np.isnan(got.worsening_lower).all()
    assert np.isnan(got.estimate).all()
    assert got.checks.shape == (4, 1)


def test_every_limit_is_judged_on_the_one_candidate_interval() -> None:
    base, cand, first = pairs(1.0, n=120)
    got = evaluate(base, cand, first, allowance=None, limits=(80e6, 200e6))
    assert np.all(got.checks[:, 0] == "breach")
    assert np.all(got.checks[:, 1] == "within")


def test_the_candidate_interval_is_order_statistics_of_its_own_values() -> (
    None
):
    base, cand, first = pairs(1.2, rows=2, n=120, sd=0.02)
    got = evaluate(base, cand, first, limits=(150e6,), procedure=BATCH)
    median = np.median(cand, axis=1)
    assert np.all(got.candidate_lower <= median)
    assert np.all(got.candidate_upper >= median)
    assert np.all(got.typical == median)
    for row in range(2):
        assert got.candidate_lower[row] in cand[row]
        assert got.candidate_upper[row] in cand[row]
    assert np.all(got.candidate_upper / got.candidate_lower < 1.1)


# The diagnostic gate of the batch-means primary holds a look back


def lumpy_ratios() -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    """Mostly equal ratios, a few off: outliers by the gate's rule."""
    base = np.full((1, 18), 50e6)
    cand = base.copy()
    cand[0, [2, 9]] *= 1.0005
    first = np.zeros((1, 18), dtype=np.bool_)
    first[:, 0::2] = True
    return base, cand, first


def test_a_look_the_gate_holds_back_is_not_established_whatever_it_shows() -> (
    None
):
    base, cand, first = lumpy_ratios()
    got = evaluate(base, cand, first, procedure=BATCH)
    assert got.held.tolist() == ["outlier_rounds"]
    assert got.status.tolist() == ["not_established"]
    assert set(got.checks.ravel()) == {"open"}
    # The interval is there, and would have passed the allowance.
    assert got.interval.all()
    assert got.worsening_upper[0] <= 1.10
    free = evaluate(base, cand, first, procedure=HAC)
    assert free.held.tolist() == [""]
    assert free.status.tolist() == ["passed"]


def test_hac_lf_has_no_gate() -> None:
    held = {
        str(h)
        for ratio, seed in itertools.product((1.0, 1.2), range(3))
        for h in evaluate(*pairs(ratio, seed=seed), procedure=HAC).held
    }
    assert held == {""}


# The record: what a status needs and what the schema reads


def test_a_passed_result_carries_its_estimate_and_intervals() -> None:
    guard = spec(limits=(AbsoluteLimit(value=200e6, label="limit"),))
    got = evaluate(*pairs(1.02, n=120), limits=(200e6,))
    result = guardrail_result(guard, got, 0, CONF)
    assert result.status == "passed"
    assert result.estimate == float(got.estimate[0])
    assert result.worsening == Interval(
        lower=got.worsening_lower[0],
        upper=got.worsening_upper[0],
        confidence=CONF,
    )
    assert result.candidate is not None
    assert result.candidate.upper <= 200e6
    _check_guardrail_status(guard, result)


def test_a_breached_result_keeps_every_measured_interval() -> None:
    guard = spec(limits=(AbsoluteLimit(value=100e6, label="limit"),))
    got = evaluate(*pairs(1.4, n=120), limits=(100e6,))
    result = guardrail_result(guard, got, 0, CONF)
    assert result.status == "breached"
    assert result.worsening is not None
    assert result.worsening.lower > 1.10
    assert result.candidate is not None
    _check_guardrail_status(guard, result)


def test_a_held_look_is_recorded_unmeasurable_with_the_reason() -> None:
    got = evaluate(*lumpy_ratios(), procedure=BATCH)
    result = guardrail_result(spec(), got, 0, CONF)
    assert result.status == "unmeasurable"
    assert "outlier" in result.reason
    assert result.worsening is None
    assert result.estimate is None
    _check_guardrail_status(spec(), result)


def test_a_held_allowance_does_not_open_a_limit() -> None:
    base, cand, first = lumpy_ratios()
    guard = spec(limits=(AbsoluteLimit(value=1e12, label="cap"),))
    lenient = evaluate(base, cand, first, procedure=BATCH, limits=(1e12,))
    assert lenient.held.tolist() == ["outlier_rounds"]
    assert lenient.status.tolist() == ["not_established"]
    result = guardrail_result(guard, lenient, 0, CONF)
    assert result.status == "not_established"
    assert result.worsening is None
    assert result.candidate is not None
    assert result.standing[0].reliable_value is not None
    _check_guardrail_status(guard, result)
    tight = evaluate(base, cand, first, procedure=BATCH, limits=(1.0,))
    assert tight.status.tolist() == ["breached"]
    breach = guardrail_result(
        spec(limits=(AbsoluteLimit(value=1.0, label="cap"),)), tight, 0, CONF
    )
    assert breach.status == "breached"
    _check_guardrail_status(
        spec(limits=(AbsoluteLimit(value=1.0, label="cap"),)), breach
    )


def test_a_look_with_no_interval_is_recorded_unmeasurable() -> None:
    got = evaluate(*pairs(1.0, n=10))
    result = guardrail_result(spec(), got, 0, CONF)
    assert result.status == "unmeasurable"
    assert "no interval" in result.reason
    _check_guardrail_status(spec(), result)


def test_a_coarse_value_is_recorded_unmeasurable() -> None:
    base, cand, first = pairs(1.0, level=1000.0)
    step = np.full(base.shape, 4096.0)
    got = evaluate(base, cand, first, baseline_step=step, candidate_step=step)
    result = guardrail_result(spec(), got, 0, CONF)
    assert result.status == "unmeasurable"
    assert "resolution" in result.reason


def test_a_status_the_intervals_do_not_earn_is_a_bug_and_raises() -> None:
    got = evaluate(*pairs(1.0))
    forged = replace(got, status=np.array(["breached"] * 4))
    with pytest.raises(ValueError, match="earn passed"):
        guardrail_result(spec(), forged, 0, CONF)


def test_a_not_applicable_guardrail_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="not applicable: say why"):
        spec(applicable=False, allowance=None, allowance_source=None)
    with pytest.raises(ValidationError, match="no allowance or limits"):
        spec(applicable=False, reason="no GPU")
    off = spec(
        applicable=False,
        reason="no GPU",
        allowance=None,
        allowance_source=None,
    )
    with pytest.raises(ValueError, match="not applicable"):
        guardrail_result(off, evaluate(*pairs(1.0)), 0, CONF)
    with pytest.raises(ValueError, match="not applicable"):
        earned_status(off, None, None)
    with pytest.raises(ValidationError, match="say why"):
        GuardrailResult(metric="peak_gpu_memory", status="not_applicable")


@pytest.mark.parametrize("ratio", [0.9, 1.0, 1.1, 1.3])
@pytest.mark.parametrize("limit", [None, 95e6, 120e6, 250e6])
@pytest.mark.parametrize("procedure", [HAC, BATCH])
def test_every_result_is_a_status_the_schema_recognises(
    ratio: float, limit: float | None, procedure: Procedure
) -> None:
    limits = () if limit is None else (limit,)
    guard = spec(
        limits=tuple(AbsoluteLimit(value=x, label="limit") for x in limits)
    )
    got = evaluate(
        *pairs(ratio, rows=6, sd=0.02, seed=3),
        limits=limits,
        procedure=procedure,
    )
    for row in range(6):
        result = guardrail_result(guard, got, row, CONF)
        _check_guardrail_status(guard, result)
        if result.status != "unmeasurable":
            assert result.status == str(got.status[row])


@settings(max_examples=60, deadline=None)
@given(
    st.floats(0.5, 2.0),
    st.floats(0.0, 0.3),
    st.sampled_from([None, 0.7, 1.0, 1.3, 2.0]),
    st.integers(0, 10_000),
)
def test_the_exact_comparisons_on_the_record_give_the_status(
    ratio: float, sd: float, limit_factor: float | None, seed: int
) -> None:
    limits = () if limit_factor is None else (100e6 * limit_factor,)
    guard = spec(
        limits=tuple(AbsoluteLimit(value=x, label="limit") for x in limits)
    )
    got = evaluate(
        *pairs(ratio, rows=3, sd=sd + 1e-6, seed=seed),
        limits=limits,
        procedure=HAC,
    )
    for row in range(3):
        result = guardrail_result(guard, got, row, CONF)
        if result.status in ("passed", "breached", "not_established"):
            assert (
                earned_status(guard, result.worsening, result.candidate)
                == result.status
                == str(got.status[row])
            )


# Invariants of the interval


@settings(max_examples=40, deadline=None)
@given(
    st.floats(0.5, 2.0),
    st.floats(0.5, 4.0),
    st.integers(0, 10_000),
    st.sampled_from([HAC, BATCH]),
)
def test_scaling_only_the_candidate_scales_the_worsening_interval(
    ratio: float, factor: float, seed: int, procedure: Procedure
) -> None:
    base, cand, first = pairs(ratio, rows=2, sd=0.05, seed=seed)
    one = evaluate(base, cand, first, procedure=procedure)
    two = evaluate(base, cand * factor, first, procedure=procedure)
    np.testing.assert_allclose(
        two.worsening_lower, one.worsening_lower * factor, rtol=1e-9
    )
    np.testing.assert_allclose(
        two.worsening_upper, one.worsening_upper * factor, rtol=1e-9
    )


@settings(max_examples=40, deadline=None)
@given(
    st.floats(0.5, 2.0),
    st.floats(0.5, 4.0),
    st.integers(0, 10_000),
    st.sampled_from([HAC, BATCH]),
)
def test_scaling_both_arms_leaves_the_worsening_interval_unchanged(
    ratio: float, factor: float, seed: int, procedure: Procedure
) -> None:
    base, cand, first = pairs(ratio, rows=2, sd=0.05, seed=seed)
    one = evaluate(base, cand, first, procedure=procedure)
    two = evaluate(base * factor, cand * factor, first, procedure=procedure)
    np.testing.assert_allclose(
        two.worsening_lower, one.worsening_lower, rtol=1e-9
    )
    np.testing.assert_allclose(
        two.worsening_upper, one.worsening_upper, rtol=1e-9
    )


@settings(max_examples=40, deadline=None)
@given(
    st.floats(0.5, 2.0), st.integers(0, 10_000), st.sampled_from([HAC, BATCH])
)
def test_swapping_the_arms_inverts_the_worsening_interval(
    ratio: float, seed: int, procedure: Procedure
) -> None:
    base, cand, first = pairs(ratio, rows=2, sd=0.05, seed=seed)
    one = evaluate(base, cand, first, procedure=procedure)
    swapped = evaluate(cand, base, first, procedure=procedure)
    np.testing.assert_allclose(
        swapped.worsening_lower, 1 / one.worsening_upper, rtol=1e-9
    )
    np.testing.assert_allclose(
        swapped.worsening_upper, 1 / one.worsening_lower, rtol=1e-9
    )


def test_the_direction_flips_the_worsening_ratio() -> None:
    base, cand, first = pairs(1.2, rows=2, sd=0.05)
    low = evaluate(base, cand, first)
    high = evaluate(base, cand, first, direction="higher_is_better")
    np.testing.assert_allclose(
        high.worsening_lower, 1 / low.worsening_upper, rtol=1e-9
    )


def test_rows_do_not_interact() -> None:
    base, cand, first = pairs(1.1, rows=6, sd=0.04, seed=11, n=120)
    together = evaluate(base, cand, first, limits=(150e6,))
    for row in range(6):
        alone = evaluate(
            base[row : row + 1],
            cand[row : row + 1],
            first[row : row + 1],
            limits=(150e6,),
        )
        assert alone.status[0] == together.status[row]
        assert alone.worsening_lower[0] == together.worsening_lower[row]
        assert alone.candidate_upper[0] == together.candidate_upper[row]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"allowance": None}, "allowance, a limit, or both"),
        ({"allowance": -0.1}, "allowance must be"),
        ({"allowance": 1}, "allowance must be"),
        ({"allowance": math.nan}, "allowance must be"),
        ({"limits": (0.0,)}, "limit must be"),
        ({"limits": (math.inf,)}, "limit must be"),
        ({"limits": (5,)}, "limit must be"),
    ],
)
def test_bad_checks_are_refused(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        evaluate(*pairs(1.0), **changes)


def test_bad_shapes_and_steps_are_refused() -> None:
    base, cand, first = pairs(1.0)
    with pytest.raises(ValueError, match="shaped"):
        evaluate(base[0], cand[0], first[0])
    with pytest.raises(ValueError, match="shaped"):
        evaluate(base, cand[:, :20], first)
    with pytest.raises(ValueError, match="both"):
        evaluate(base, cand, first, baseline_step=np.ones(base.shape))
    with pytest.raises(ValueError, match="positive"):
        evaluate(base, -cand, first)


# The guardrail step of the decision rule

KEEP_BOUNDS = (1.05, 1.12)
STATUSES: tuple[GuardrailStatus, ...] = (
    "passed",
    "breached",
    "not_established",
    "unmeasurable",
    "not_declared",
    "not_applicable",
)


def gates(**status: str) -> list[GateResult]:
    out: list[GateResult] = []
    for name in GATES:
        found = status.get(name, "passed")
        out.append(
            GateResult(
                gate=name,  # pyright: ignore[reportArgumentType]
                status=found,  # pyright: ignore[reportArgumentType]
                detail="" if found == "passed" else "d",
            )
        )
    return out


def decision(
    guardrails: dict[str, GuardrailStatus],
    *,
    interval: tuple[float, float] | None = KEEP_BOUNDS,
    budget: bool = True,
    **gate_status: str,
) -> Any:
    return decide(
        gates(**gate_status),
        interval,
        min_effect=0.03,
        budget_remains=budget,
        calibrated=True,
        guardrails=guardrails,
    )


def test_every_guardrail_passed_or_not_applicable_keeps() -> None:
    d = decision({"a": "passed", "b": "not_applicable", "c": "passed"})
    assert (d.outcome, d.reason, d.rule) == ("KEEP", "performance", 2)
    assert d.breached == d.guardrails_blocked == ()
    assert decision({"a": "not_applicable"}).outcome == "KEEP"


def test_a_breach_blocks_a_keep_and_rejects_for_the_guardrail() -> None:
    d = decision({"mem": "breached", "p99": "passed"})
    assert (d.outcome, d.reason, d.rule) == ("REJECT", "guardrail", 2)
    assert d.breached == ("mem",)
    assert d.lower == KEEP_BOUNDS[0]
    assert d.upper == KEEP_BOUNDS[1]
    assert not d.regression


def test_a_breach_rejects_even_when_something_else_would_withhold() -> None:
    d = decision(
        {"mem": "breached", "p99": "unmeasurable", "gpu": "not_established"},
        coverage="not_available",
    )
    assert (d.outcome, d.reason) == ("REJECT", "guardrail")
    assert d.breached == ("mem",)
    assert d.blocked_by == ("coverage",)


@pytest.mark.parametrize("budget", [True, False])
def test_an_unmeasurable_or_undeclared_guardrail_is_inconclusive_at_once(
    budget: bool,
) -> None:
    for status in ("unmeasurable", "not_declared"):
        d = decision({"mem": "passed", "p99": status}, budget=budget)
        assert (d.outcome, d.reason, d.rule) == (
            "INCONCLUSIVE",
            "insufficient_evidence",
            2,
        )
        assert d.guardrails_blocked == ("p99",)
        assert d.outcome != "KEEP"


def test_a_not_established_guardrail_continues_then_is_inconclusive() -> None:
    more = decision({"mem": "not_established", "p99": "passed"})
    assert (more.outcome, more.reason, more.rule) == ("CONTINUE", None, 2)
    assert more.guardrails_blocked == ("mem",)
    done = decision({"mem": "not_established"}, budget=False)
    assert (done.outcome, done.reason, done.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        2,
    )
    assert done.guardrails_blocked == ("mem",)


def test_a_fixed_blocker_ends_the_look_even_with_one_not_established() -> None:
    d = decision({"a": "not_established", "b": "unmeasurable"})
    assert d.outcome == "INCONCLUSIVE"
    assert d.guardrails_blocked == ("a", "b")


def test_a_gate_not_available_still_blocks_with_every_guardrail_passed() -> (
    None
):
    d = decision({"mem": "passed"}, coverage="not_available")
    assert (d.outcome, d.rule, d.blocked_by) == (
        "INCONCLUSIVE",
        2,
        ("coverage",),
    )
    assert d.guardrails_blocked == ()


@pytest.mark.parametrize("status", STATUSES)
@pytest.mark.parametrize(
    "interval", [(0.95, 1.01), (0.99, 1.2), (1.0, 1.02), (0.9, 1.0)]
)
def test_a_primary_reject_or_continue_stands_with_the_guardrails_alongside(
    status: GuardrailStatus, interval: tuple[float, float]
) -> None:
    alone = decision({}, interval=interval)
    with_guardrails = decision({"mem": status}, interval=interval)
    for field in ("outcome", "reason", "rule", "regression", "lower", "upper"):
        assert getattr(with_guardrails, field) == getattr(alone, field)
    assert with_guardrails.breached == with_guardrails.guardrails_blocked == ()


def test_a_breach_beside_a_performance_reject_leaves_it_alone() -> None:
    d = decision({"mem": "breached"}, interval=(0.9, 0.99))
    assert (d.outcome, d.reason, d.rule, d.regression) == (
        "REJECT",
        "performance",
        3,
        True,
    )


def test_a_breach_beside_continue_or_inconclusive_leaves_it_alone() -> None:
    straddle = (0.99, 1.2)
    more = decision({"mem": "breached"}, interval=straddle)
    assert (more.outcome, more.rule) == ("CONTINUE", 4)
    over = decision({"mem": "breached"}, interval=straddle, budget=False)
    assert (over.outcome, over.reason, over.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        5,
    )
    none = decision({"mem": "breached"}, interval=None)
    assert (none.outcome, none.rule) == ("CONTINUE", 4)


def test_a_gate_failure_decides_before_any_guardrail() -> None:
    d = decision({"mem": "breached"}, correctness="failed")
    assert (d.outcome, d.reason, d.rule, d.gate) == (
        "REJECT",
        "correctness",
        1,
        "correctness",
    )
    assert d.breached == ()


def test_before_calibration_guardrails_change_nothing() -> None:
    d = decide(
        gates(),
        KEEP_BOUNDS,
        min_effect=0.03,
        budget_remains=False,
        calibrated=False,
        guardrails={"mem": "breached"},
    )
    assert (d.outcome, d.rule, d.procedure_uncalibrated) == (
        "INCONCLUSIVE",
        5,
        True,
    )


def test_keep_needs_every_guardrail_passed_or_not_applicable() -> None:
    for combo in itertools.product(STATUSES, repeat=3):
        names = dict(zip("abc", combo, strict=True))
        d = decision(names)
        shown = all(s in ("passed", "not_applicable") for s in combo)
        assert (d.outcome == "KEEP") == shown, combo
        if "breached" in combo:
            assert d.outcome == "REJECT"
        elif not shown:
            assert d.outcome in ("CONTINUE", "INCONCLUSIVE")


def test_an_unknown_status_or_name_is_refused() -> None:
    with pytest.raises(ValueError, match="not one of"):
        decision(cast("Any", {"mem": "fine"}))
    with pytest.raises(ValueError, match="not one of"):
        decision(cast("Any", {1: "passed"}))


@pytest.mark.parametrize(
    ("rule", "breached", "fixed", "pending", "budget", "expected"),
    [
        (2, False, False, False, True, "KEEP"),
        (2, True, False, False, True, "REJECT"),
        (2, True, True, True, False, "REJECT"),
        (2, False, True, False, True, "INCONCLUSIVE"),
        (2, False, True, True, True, "INCONCLUSIVE"),
        (2, False, False, True, True, "CONTINUE"),
        (2, False, False, True, False, "INCONCLUSIVE"),
        (1, True, True, True, True, "INCONCLUSIVE"),
        (3, True, False, False, True, "REJECT"),
        (4, True, True, True, True, "CONTINUE"),
        (5, True, True, True, False, "INCONCLUSIVE"),
    ],
)
def test_the_guardrail_step_table(
    rule: int,
    breached: bool,
    fixed: bool,
    pending: bool,
    budget: bool,
    expected: str,
) -> None:
    step = guardrail_actions(
        np.int8(rule),
        breached=breached,
        fixed=fixed,
        pending=pending,
        budget_remains=budget,
    )
    assert str(step.action) == expected
    assert bool(step.guardrail_reject) == (rule == 2 and breached)
    assert bool(step.held) == (
        rule == 2 and not breached and (fixed or pending)
    )


def test_the_guardrail_step_works_on_a_batch() -> None:
    rule = np.array([2, 2, 2, 2, 3, 4, 5, 1], dtype=np.int8)
    breached = np.array([0, 1, 0, 0, 1, 1, 1, 1], dtype=bool)
    fixed = np.array([0, 0, 1, 0, 0, 0, 0, 0], dtype=bool)
    pending = np.array([0, 0, 0, 1, 0, 0, 0, 0], dtype=bool)
    budget = np.array([1, 1, 1, 1, 1, 1, 0, 0], dtype=bool)
    step = guardrail_actions(
        rule,
        breached=breached,
        fixed=fixed,
        pending=pending,
        budget_remains=budget,
    )
    assert step.action.tolist() == [
        "KEEP",
        "REJECT",
        "INCONCLUSIVE",
        "CONTINUE",
        "REJECT",
        "CONTINUE",
        "INCONCLUSIVE",
        "INCONCLUSIVE",
    ]
    assert step.guardrail_reject.tolist() == [
        False,
        True,
        False,
        False,
        False,
        False,
        False,
        False,
    ]
    assert step.held.tolist()[2:4] == [True, True]


def test_the_guardrail_step_refuses_bad_input() -> None:
    ok: dict[str, Any] = {
        "breached": False,
        "fixed": False,
        "pending": False,
        "budget_remains": True,
    }
    for bad in (0, 6, 2.0):
        with pytest.raises(ValueError, match="rule"):
            guardrail_actions(np.array(bad), **ok)
    with pytest.raises(ValueError, match="boolean"):
        guardrail_actions(np.int8(2), **{**ok, "fixed": 1})
    with pytest.raises(ValueError, match="broadcast"):
        guardrail_actions(
            np.array([2, 2, 2], dtype=np.int8),
            **{**ok, "breached": np.array([True, False])},
        )


# Review follow-ups: limits and the resolution, and exact boundaries


def test_a_limit_is_judged_on_the_measured_values_not_their_step() -> None:
    base, cand, first = pairs(1.0, level=1000.0, n=120)
    step = np.full(base.shape, 4096.0)
    plain = evaluate(base, cand, first, allowance=None, limits=(500.0,))
    stepped = evaluate(
        base,
        cand,
        first,
        allowance=None,
        limits=(500.0,),
        baseline_step=step,
        candidate_step=step,
    )
    assert set(stepped.status) == {"breached"} == set(plain.status)
    np.testing.assert_array_equal(
        stepped.candidate_upper, plain.candidate_upper
    )
    np.testing.assert_array_equal(
        stepped.candidate_lower, plain.candidate_lower
    )
    # The allowance's interval is the one a step widens or refuses.
    refused = evaluate(
        base,
        cand,
        first,
        limits=(500.0,),
        baseline_step=step,
        candidate_step=step,
    )
    assert set(refused.status) == {"unmeasurable"}


@pytest.mark.parametrize("direction", ["lower_is_better", "higher_is_better"])
def test_a_candidate_exactly_on_its_limit_passes_and_records(
    direction: Direction,
) -> None:
    base = np.full((1, 60), 50.0)
    first = np.zeros((1, 60), dtype=np.bool_)
    first[:, 0::2] = True
    got = evaluate(
        base,
        base.copy(),
        first,
        allowance=None,
        limits=(50.0,),
        direction=direction,
    )
    assert got.status.tolist() == ["passed"]
    assert got.candidate_lower[0] == got.candidate_upper[0] == 50.0
    guard = spec(
        metric="peak_memory"
        if direction == "lower_is_better"
        else "throughput",
        allowance=None,
        allowance_source=None,
        direction=direction,
        limits=(AbsoluteLimit(value=50.0, label="limit"),),
    )
    result = guardrail_result(guard, got, 0, CONF)
    assert result.status == "passed"
    assert earned_status(guard, None, result.candidate) == "passed"


def test_a_floor_interval_is_ordered_and_straddles_a_limit_at_its_median() -> (
    None
):
    base, cand, first = pairs(1.0, level=1e3, sd=0.02, n=120)
    middle = np.median(cand, axis=1)
    got = evaluate(
        base,
        cand,
        first,
        allowance=None,
        direction="higher_is_better",
        limits=tuple(float(x) for x in middle[:1]),
    )
    assert np.all(got.candidate_lower <= got.candidate_upper)
    assert got.status[0] == "not_established"


def test_earned_status_at_exact_boundaries() -> None:
    guard = spec(limits=(AbsoluteLimit(value=100.0, label="limit"),))

    def iv(lo: float, hi: float) -> Interval:
        return Interval(lower=lo, upper=hi, confidence=0.99)

    assert earned_status(guard, iv(0.9, 1.1), iv(90.0, 100.0)) == "passed"
    assert (
        earned_status(
            guard, iv(0.9, math.nextafter(1.1, 2.0)), iv(90.0, 100.0)
        )
        == "not_established"
    )
    assert earned_status(guard, iv(1.1, 1.2), iv(90.0, 100.0)) == (
        "not_established"
    )
    assert earned_status(guard, iv(math.nextafter(1.1, 2.0), 1.2), None) == (
        "breached"
    )
    assert earned_status(guard, iv(0.9, 1.0), iv(100.0, 110.0)) == (
        "not_established"
    )
    assert earned_status(guard, iv(0.9, 1.0), iv(100.0000001, 110.0)) == (
        "breached"
    )
    floor = spec(
        metric="throughput",
        direction="higher_is_better",
        allowance=None,
        allowance_source=None,
        limits=(AbsoluteLimit(value=100.0, label="floor"),),
    )
    assert earned_status(floor, None, iv(100.0, 120.0)) == "passed"
    assert earned_status(floor, None, iv(90.0, 100.0)) == "not_established"
    assert earned_status(floor, None, iv(90.0, 99.9)) == "breached"
    assert earned_status(floor, None, None) == "unmeasurable"


def test_decide_fails_closed_without_the_guardrails() -> None:
    d = decide(
        gates(),
        KEEP_BOUNDS,
        min_effect=0.03,
        budget_remains=True,
        calibrated=True,
    )
    assert (d.outcome, d.reason, d.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        2,
    )
    assert d.guardrails_blocked == (UNCHECKED,)
    empty = decision({})
    assert empty.outcome == "KEEP"
    other = decide(
        gates(),
        (0.9, 0.99),
        min_effect=0.03,
        budget_remains=True,
        calibrated=True,
    )
    assert (other.outcome, other.rule) == ("REJECT", 3)


# The owner's rule for limits: a robust rank bound, numbers always shown


def spiky_values(
    ratio: float = 1.0,
) -> tuple[FloatArray, FloatArray, NDArray[np.bool_]]:
    """Candidate values with 2 of 120 rounds at 4 times the rest."""
    base, cand, first = pairs(ratio, rows=3, sd=0.002, n=120)
    cand[:, [3, 14]] *= 4.0
    return base, cand, first


def test_outliers_do_not_hold_a_limit_they_decide_it_as_their_rank() -> None:
    base, cand, first = spiky_values()
    for limit, status in ((50e6, "breached"), (1e12, "passed")):
        got = evaluate(base, cand, first, allowance=None, limits=(limit,))
        assert set(got.status) == {status}, limit
        assert got.held.tolist() == [""] * 3
        assert np.all(
            got.rounds_over[:, 0] == (120 if limit == 50e6 else 0)
        ), limit
    # A limit just above the typical value passes though two rounds are
    # four times over it: the median does not move for them.
    mid = evaluate(base, cand, first, allowance=None, limits=(110e6,))
    assert set(mid.status) == {"passed"}
    assert np.all(mid.rounds_over[:, 0] == 2)


def test_a_spike_larger_still_does_not_move_the_bounds() -> None:
    base, cand, first = spiky_values()
    huge = cand.copy()
    huge[:, [3, 14]] *= 1e6
    one = evaluate(base, cand, first, allowance=None, limits=(1e12,))
    two = evaluate(base, huge, first, allowance=None, limits=(1e12,))
    np.testing.assert_array_equal(one.candidate_upper, two.candidate_upper)
    np.testing.assert_array_equal(one.candidate_lower, two.candidate_lower)
    np.testing.assert_array_equal(one.typical, two.typical)


def test_the_allowance_and_the_limit_are_judged_apart() -> None:
    base, cand, first = spiky_values(ratio=1.0)
    got = evaluate(base, cand, first, limits=(1e12,))
    alone = evaluate(base, cand, first)
    assert got.checks[:, 0].tolist() == alone.checks[:, 0].tolist()
    only = evaluate(*pairs(1.4, n=120), limits=(1e12,))
    assert set(only.status) == {"breached"}


def test_a_quantile_other_than_the_median_is_the_statistic() -> None:
    base, cand, first = pairs(1.0, rows=2, n=1200, sd=0.1)
    p99 = evaluate(
        base, cand, first, allowance=None, limits=(1e12,), quantile=0.99
    )
    med = evaluate(base, cand, first, allowance=None, limits=(1e12,))
    np.testing.assert_allclose(p99.typical, np.quantile(cand, 0.99, axis=1))
    assert np.all(p99.typical > med.typical)
    assert np.all(p99.candidate_upper > med.candidate_upper)
    short = evaluate(
        *pairs(1.0, n=60), allowance=None, limits=(1e12,), quantile=0.99
    )
    assert np.isnan(short.candidate_upper).all()
    assert set(short.status) == {"not_established"}


@pytest.mark.parametrize("quantile", [0.0, 1.0, 1, math.nan])
def test_a_bad_quantile_is_refused(quantile: Any) -> None:
    with pytest.raises(ValueError, match="quantile must be"):
        evaluate(*pairs(1.0, n=120), limits=(1e9,), quantile=quantile)


# The rank bounds themselves


def binom_at_least(k: int, n: int, q: float) -> float:
    """P(Bin(n, q) >= k), summed directly as an independent oracle."""
    return math.fsum(
        math.comb(n, i) * q**i * (1 - q) ** (n - i) for i in range(k, n + 1)
    )


def test_rank_bounds_are_the_exact_binomial_order_statistics() -> None:
    assert rank_bounds(10, 0.5, 0.025) == (2, 9)
    assert rank_bounds(5, 0.5, 0.025) == (0, 6)
    assert rank_bounds(1, 0.5, 0.25) == (0, 2)
    for n, q, tail in itertools.product(
        (10, 37, 120), (0.5, 0.9, 0.99), (0.001, 0.025)
    ):
        s_, r_ = rank_bounds(n, q, tail)
        if r_ <= n:
            assert binom_at_least(r_, n, q) <= tail
            assert r_ == 1 or binom_at_least(r_ - 1, n, q) > tail
        if s_ >= 1:
            assert 1 - binom_at_least(s_, n, q) <= tail
            assert 1 - binom_at_least(s_ + 1, n, q) > tail


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ((0, 0.5, 0.01), "n must be"),
        ((True, 0.5, 0.01), "n must be"),
        ((10, 1.0, 0.01), "quantile must be"),
        ((10, 0.5, 0.5), "tail must be"),
        ((10, 0.5, 0.0), "tail must be"),
    ],
)
def test_rank_bounds_refuse_bad_arguments(
    args: tuple[Any, Any, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        rank_bounds(*args)


def values(rows: int, n: int, seed: int = 0) -> FloatArray:
    rng = np.random.default_rng(seed)
    return 100.0 * np.exp(0.2 * rng.standard_normal((rows, n)))


def test_limit_bounds_scale_with_the_values_and_bracket_the_typical() -> None:
    v = values(50, 90)
    first = np.zeros(v.shape, dtype=np.bool_)
    one = limit_bounds(v, quantile=0.5, tail=0.004, candidate_first=first)
    two = limit_bounds(
        3.0 * v, quantile=0.5, tail=0.004, candidate_first=first
    )
    np.testing.assert_allclose(two.lower, 3.0 * one.lower)
    np.testing.assert_allclose(two.upper, 3.0 * one.upper)
    assert np.all(one.lower <= one.typical)
    assert np.all(one.typical <= one.upper)
    assert np.all(one.effective <= 90)
    assert np.all(one.effective >= 1)


def test_limit_bounds_cover_the_true_median_of_independent_rounds() -> None:
    rows, n, tail = 4000, 60, 0.004
    v = values(rows, n, seed=7)
    first = np.zeros(v.shape, dtype=np.bool_)
    got = limit_bounds(v, quantile=0.5, tail=tail, candidate_first=first)
    truth = 100.0
    bounded = np.isfinite(got.upper)
    assert bounded.all()
    miss_up = int(np.count_nonzero(got.upper < truth))
    miss_lo = int(np.count_nonzero(got.lower > truth))
    # Binomial 99.9% limit on the miss count at rate 0.004, R = 4000.
    limit = rows * tail + 4.0 * math.sqrt(rows * tail * (1 - tail))
    assert miss_up <= limit
    assert miss_lo <= limit


def test_dependent_rounds_widen_the_bounds_not_the_values() -> None:
    rng = np.random.default_rng(3)
    n = 240
    noise = rng.standard_normal((1, n))
    series = np.empty((1, n))
    series[0, 0] = noise[0, 0]
    for i in range(1, n):
        series[0, i] = 0.8 * series[0, i - 1] + 0.6 * noise[0, i]
    v = 100.0 * np.exp(0.2 * series)
    shuffled = v[:, rng.permutation(n)]
    first = np.zeros(v.shape, dtype=np.bool_)
    dep = limit_bounds(v, quantile=0.5, tail=0.004, candidate_first=first)
    iid = limit_bounds(
        shuffled, quantile=0.5, tail=0.004, candidate_first=first
    )
    assert dep.effective[0] < iid.effective[0]
    assert dep.upper[0] - dep.lower[0] >= iid.upper[0] - iid.lower[0]
    np.testing.assert_array_equal(dep.typical, iid.typical)


def test_too_few_rounds_give_no_bound_and_no_claim() -> None:
    v = values(2, 8)
    first = np.zeros(v.shape, dtype=np.bool_)
    got = limit_bounds(v, quantile=0.5, tail=0.004, candidate_first=first)
    assert np.isnan(got.lower).all()
    assert np.isnan(got.upper).all()
    one = limit_bounds(
        v[:, :1], quantile=0.5, tail=0.004, candidate_first=first[:, :1]
    )
    assert np.isnan(one.upper).all()
    assert one.typical.tolist() == v[:, 0].tolist()


def test_limit_bounds_refuse_bad_input() -> None:
    v = values(2, 40)
    first = np.zeros(v.shape, dtype=np.bool_)
    with pytest.raises(ValueError, match="shaped"):
        limit_bounds(v[0], quantile=0.5, tail=0.01, candidate_first=first[0])
    with pytest.raises(ValueError, match="finite and positive"):
        limit_bounds(-v, quantile=0.5, tail=0.01, candidate_first=first)
    with pytest.raises(ValueError, match="candidate_first"):
        limit_bounds(
            v, quantile=0.5, tail=0.01, candidate_first=first.astype(int)
        )


# The numbers are always there: typical, reliable value, gap, rounds


def limit_spec(value: float, **changes: Any) -> GuardrailSpec:
    return spec(limits=(AbsoluteLimit(value=value, label="limit"),), **changes)


@pytest.mark.parametrize(
    ("limit", "status"), [(200e6, "passed"), (90e6, "breached")]
)
def test_the_standing_is_recorded_for_a_pass_and_a_breach(
    limit: float, status: str
) -> None:
    guard = limit_spec(limit)
    got = evaluate(*pairs(1.05, n=120), limits=(limit,))
    result = guardrail_result(guard, got, 0, CONF)
    assert result.status == status
    (standing,) = result.standing
    assert standing.limit == limit
    assert standing.quantile == 0.5
    assert standing.typical == float(got.typical[0])
    assert standing.reliable_value == float(got.candidate_upper[0])
    assert standing.reliable_value is not None
    assert standing.gap == standing.reliable_value - limit
    assert standing.gap is not None
    assert standing.rounds == 120
    assert standing.rounds_over == int(got.rounds_over[0, 0])
    assert (standing.gap <= 0) == (status == "passed")
    _check_guardrail_status(guard, result)


def test_the_standing_is_recorded_when_the_limit_is_not_established() -> None:
    base, cand, first = pairs(1.0, rows=1, n=120, sd=0.02)
    middle = float(np.median(cand))
    guard = limit_spec(middle)
    got = evaluate(base, cand, first, limits=(middle,))
    result = guardrail_result(guard, got, 0, CONF)
    assert result.status == "not_established"
    (standing,) = result.standing
    assert standing.gap is not None
    assert standing.gap > 0
    assert standing.typical == pytest.approx(middle)
    assert standing.rounds_over in range(40, 80)
    _check_guardrail_status(guard, result)


def test_a_floors_reliable_value_is_its_lower_bound() -> None:
    guard = spec(
        metric="throughput",
        direction="higher_is_better",
        allowance=None,
        allowance_source=None,
        limits=(AbsoluteLimit(value=800.0, label="floor"),),
    )
    base, cand, first = pairs(1.0, level=1e3, n=120)
    got = evaluate(
        base,
        cand,
        first,
        allowance=None,
        direction="higher_is_better",
        limits=(800.0,),
    )
    result = guardrail_result(guard, got, 0, CONF)
    (standing,) = result.standing
    assert standing.reliable_value == float(got.candidate_lower[0])
    assert standing.reliable_value is not None
    assert standing.gap == standing.reliable_value - 800.0
    assert standing.gap is not None
    assert standing.gap >= 0
    assert standing.rounds_over == 0


def test_rounds_over_counts_each_limit_and_the_declared_quantile() -> None:
    base, cand, first = pairs(1.0, rows=1, n=1200, sd=0.1)
    cap = float(np.quantile(cand, 0.99))
    guard = limit_spec(cap, limit_quantile=0.99)
    got = evaluate(
        base, cand, first, allowance=0.10, limits=(cap, 1e12), quantile=0.99
    )
    assert got.rounds_over[0].tolist() == [int(np.sum(cand > cap)), 0]
    assert guard.limit_quantile == 0.99


def test_a_look_with_no_bound_yet_records_numbers_without_a_bound() -> None:
    guard = limit_spec(200e6)
    got = evaluate(*pairs(1.0, n=24), allowance=None, limits=(200e6,))
    result = guardrail_result(guard, got, 0, CONF)
    assert result.status == "unmeasurable"
    (standing,) = result.standing
    assert standing.reliable_value is None
    assert standing.gap is None
    assert standing.typical == float(got.typical[0])
    _check_guardrail_status(guard, result)


def test_the_standing_the_record_holds_must_match_its_interval() -> None:
    guard = limit_spec(200e6)
    got = evaluate(*pairs(1.05, n=120), limits=(200e6,))
    result = guardrail_result(guard, got, 0, CONF)
    (standing,) = result.standing
    forged = result.model_copy(
        update={
            "standing": (
                standing.model_copy(
                    update={"reliable_value": 1.0, "gap": 1.0 - 200e6}
                ),
            )
        }
    )
    _check_standing(guard, result)
    with pytest.raises(ValueError, match="standing"):
        _check_standing(guard, forged)
    with pytest.raises(ValueError, match="standing"):
        _check_standing(guard, result.model_copy(update={"standing": ()}))


def test_rank_bounds_take_an_index_whose_miss_equals_the_tail() -> None:
    # P(Bin(2, 0.5) >= 2) and P(Bin(2, 0.5) <= 0) are both exactly 0.25,
    # so a tail of 0.25 admits the extreme order statistics.
    assert rank_bounds(2, 0.5, 0.25) == (1, 2)


def test_a_limit_exactly_on_the_bounds_is_met_not_breached() -> None:
    base, cand, first = pairs(1.0, n=60)
    flat = np.full(cand.shape, 100.0)
    for direction, limit, status in (
        ("lower_is_better", 100.0, "passed"),
        ("lower_is_better", 99.0, "breached"),
        ("higher_is_better", 100.0, "passed"),
        ("higher_is_better", 101.0, "breached"),
    ):
        got = evaluate(
            base,
            flat,
            first,
            allowance=None,
            limits=(limit,),
            direction=cast("Direction", direction),
        )
        assert set(got.status) == {status}, (direction, limit)


def test_a_round_exactly_at_the_cap_is_not_over_it() -> None:
    base, cand, first = pairs(1.0, n=60)
    flat = np.full(cand.shape, 100.0)
    ceiling = evaluate(base, flat, first, allowance=None, limits=(100.0,))
    assert np.all(ceiling.rounds_over == 0)
    floor = evaluate(
        base,
        flat,
        first,
        allowance=None,
        limits=(100.0,),
        direction="higher_is_better",
    )
    assert np.all(floor.rounds_over == 0)


def test_the_limit_bounds_use_the_guardrails_per_side_tail() -> None:
    # Per side the tail is (1 - confidence) / 2, alpha / (4 K G). Twice
    # that is a different, narrower bound on this data, so a look that
    # used it would not equal the bound at the guardrail's own tail.
    rng = np.random.default_rng(5)
    rows, n = 200, 80
    values = np.stack(
        [rng.permutation(n) + 100.0 for _ in range(rows)]
    ).astype(np.float64)
    first = np.ones((rows, n), dtype=np.bool_)
    got = limit_bounds(
        values, quantile=0.5, tail=(1.0 - CONF) / 2.0, candidate_first=first
    )
    wide = limit_bounds(
        values, quantile=0.5, tail=1.0 - CONF, candidate_first=first
    )
    assert np.any(got.upper != wide.upper)
    look = evaluate(
        np.ones((rows, n)), values, first, allowance=None, limits=(1e9,)
    )
    np.testing.assert_array_equal(look.candidate_upper, got.upper)
    np.testing.assert_array_equal(look.candidate_lower, got.lower)


def _fake_look(lower: float, upper: float) -> Any:
    return SimpleNamespace(
        lower=np.array([lower]),
        upper=np.array([upper]),
        withheld=np.array([""]),
    )


def test_the_allowance_breach_is_strict_and_compliance_inclusive() -> None:
    bar = 1.10
    above = float(np.nextafter(bar, 2.0))
    below = float(np.nextafter(bar, 0.0))
    has = np.array([True])
    state = _relative_state  # pyright: ignore[reportPrivateUsage]
    assert state(_fake_look(bar, 1.5), bar, has).tolist() == ["open"]
    assert state(_fake_look(above, 1.5), bar, has).tolist() == ["breach"]
    assert state(_fake_look(0.9, bar), bar, has).tolist() == ["within"]
    assert state(_fake_look(0.9, above), bar, has).tolist() == ["open"]
    assert state(_fake_look(0.9, below), bar, has).tolist() == ["within"]


def test_a_floor_breach_is_strict_and_a_ceiling_breach_too() -> None:
    limit = 100.0
    above = float(np.nextafter(limit, 200.0))
    below = float(np.nextafter(limit, 0.0))
    has = np.array([True])
    state = _limit_state  # pyright: ignore[reportPrivateUsage]

    def floor(lo: float, hi: float) -> list[str]:
        return state(
            np.array([lo]), np.array([hi]), limit, False, has
        ).tolist()

    def ceiling(lo: float, hi: float) -> list[str]:
        return state(np.array([lo]), np.array([hi]), limit, True, has).tolist()

    assert floor(90.0, limit) == ["open"]
    assert floor(90.0, below) == ["breach"]
    assert floor(limit, 120.0) == ["within"]
    assert floor(below, 120.0) == ["open"]
    assert ceiling(limit, 120.0) == ["open"]
    assert ceiling(above, 120.0) == ["breach"]
    assert ceiling(90.0, limit) == ["within"]
    assert ceiling(90.0, above) == ["open"]
