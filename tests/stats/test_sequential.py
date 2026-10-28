"""Tests for `perfproof.stats.sequential`.

The hand-checked inputs use log ratios that alternate ``m + s`` and
``m - s``: over an even number ``n`` of rounds their mean is ``m`` and
their sample standard deviation ``s sqrt(n / (n - 1))``, so the t
interval is ``exp(m -/+ q s / sqrt(n - 1))`` with ``q`` the two-sided t
quantile on ``n - 1`` degrees of freedom. The expected rule at every
look is written out as a literal, worked from those bounds and the bar.
"""

import math
from collections.abc import Callable
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import partial
from typing import Any, ClassVar, cast

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from numpy.typing import NDArray
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import GateName, GateResult, LookSchedule
from perfproof.stats import decision, sequential
from perfproof.stats.decision import IntervalRules
from perfproof.stats.decision import decide as decide_with
from perfproof.stats.intervals import (
    LIBM_ULPS,
    t_interval,
)
from perfproof.stats.sequential import (
    BATCH_BLOCK,
    OUTCOMES,
    ObservationBounds,
    Resolution,
    SequentialResult,
    continuity_fails,
    decide_look,
    lag1_autocorrelation,
    look_confidence,
    outlier_share,
    plan_looks,
    round_error_bounds,
    run_looks,
    session_pair_error,
    widen,
)
from perfproof.stats.simulate import HostMode, NoiseScenario, simulate

FloatArray = NDArray[np.float64]
MIN_EFFECT = 0.03
BAR = 1.0 + MIN_EFFECT
SEED = 20261016
PREC = 60
SETTINGS = settings(max_examples=200, derandomize=True, deadline=None)
EVERY_GATE: tuple[GateName, ...] = (
    "side_effects",
    "lock",
    "procedure",
    "runtime",
    "same_host",
    "correctness",
    "coverage",
)
SHORT = LookSchedule(n0=10, k=10, max_looks=3)
SIX = LookSchedule(n0=10, k=10, max_looks=6)

isf_t = cast(
    "Callable[[float, int], float]",
    stats.t.isf,  # pyright: ignore[reportUnknownMemberType]
)

decide = partial(decide_with, guardrails={})
"""`decide` with no guardrails in the comparison (an empty mapping)."""


def alternating(m: float, s: float, n: int) -> FloatArray:
    """Log ratios ``m + s, m - s, ...``, ``n`` of them."""
    sign = np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    return m + s * sign


def arms(d: FloatArray) -> tuple[FloatArray, FloatArray]:
    """Baseline ``exp(d)`` and candidate 1: each ratio is ``exp(d)``."""
    return np.exp(d), np.ones_like(d)


def hand_bounds(
    m: float, s: float, n: int, confidence: float
) -> tuple[float, float]:
    """Closed-form t interval of `alternating` data."""
    q = isf_t((1 - confidence) / 2, n - 1)
    half = q * s / math.sqrt(n - 1)
    return math.exp(m - half), math.exp(m + half)


def drive(
    baseline: FloatArray,
    candidate: FloatArray,
    *,
    schedule: LookSchedule = SHORT,
    mode: HostMode = "one_host",
    calibrated: bool = True,
    resolution: Resolution | ObservationBounds | None = None,
    exhausted: Any = True,
    rounds: Any = None,
    procedure: Any = "t_log_ratio",
    keep_blocked: Any = False,
) -> SequentialResult:
    return run_looks(
        baseline,
        candidate,
        direction="lower_is_better",
        schedule=schedule,
        mode=mode,
        min_effect=MIN_EFFECT,
        calibrated=calibrated,
        resolution=resolution,
        exhausted=exhausted,
        rounds=rounds,
        procedure=procedure,
        keep_blocked=keep_blocked,
        candidate_first=(
            np.zeros(np.shape(baseline), dtype=np.bool_)
            if mode == "one_host"
            else None
        ),
    )


def passed(*, cross: bool = False) -> tuple[GateResult, ...]:
    """Every gate passed, with ``same_host`` waived cross host."""
    return tuple(
        GateResult(gate="same_host", status="waived", detail="cross-host")
        if cross and gate == "same_host"
        else GateResult(gate=gate, status="passed")
        for gate in EVERY_GATE
    )


def exact_log_error(step: float, value: float) -> Decimal:
    """``-ln(1 - step / value)`` in 60-digit decimal arithmetic."""
    with localcontext() as ctx:
        ctx.prec = PREC
        return -(1 - Decimal(step) / Decimal(value)).ln()


class TestLookConfidence:
    @pytest.mark.parametrize(
        ("looks", "expected"),
        [(1, 0.975), (2, 0.9875), (6, 1 - 0.05 / 12)],
    )
    def test_bonferroni_split_over_twice_the_looks(
        self, looks: int, expected: float
    ) -> None:
        conf = look_confidence(0.05, looks)
        assert conf == pytest.approx(expected, abs=2e-16)
        assert Fraction(conf) >= 1 - Fraction(0.05) / (2 * looks)

    @SETTINGS
    @given(
        alpha=st.floats(min_value=1e-6, max_value=0.5),
        looks=st.integers(min_value=1, max_value=1000),
    )
    def test_never_below_the_exact_level(
        self, alpha: float, looks: int
    ) -> None:
        conf = look_confidence(alpha, looks)
        exact = 1 - Fraction(alpha) / (2 * looks)
        assert Fraction(conf) >= exact
        # And the closest such float: the one below is under it.
        assert Fraction(math.nextafter(conf, 0.0)) < exact

    @pytest.mark.parametrize(
        ("alpha", "looks"),
        [(0.0, 6), (1.0, 6), (math.nan, 6), (1, 6), (0.05, 0), (0.05, 2.0)],
    )
    def test_refuses_bad_arguments(self, alpha: object, looks: object) -> None:
        with pytest.raises(ValueError, match=r"alpha|max_looks"):
            look_confidence(alpha, looks)  # pyright: ignore[reportArgumentType]


class TestPlanLooks:
    # (rounds collected, budget exhausted, looks, which look is final)
    CASES: ClassVar[list[tuple[int, bool, list[int], list[int]]]] = [
        (0, True, [], []),
        (9, True, [], []),
        (11, True, [10], [10]),
        (10, True, [10], [10]),
        (10, False, [10], []),
        (15, True, [10, 14], [14]),
        (19, True, [10, 18], [18]),
        (20, True, [10, 20], [20]),
        (25, False, [10, 20], []),
        (25, True, [10, 20, 24], [24]),
        (59, True, [10, 20, 30, 40, 50, 58], [58]),
        (60, True, [10, 20, 30, 40, 50, 60], [60]),
        (60, False, [10, 20, 30, 40, 50, 60], [60]),
        (75, True, [10, 20, 30, 40, 50, 60], [60]),
    ]

    @pytest.mark.parametrize(("collected", "done", "looks", "final"), CASES)
    def test_six_look_schedule_by_hand(
        self, collected: int, done: bool, looks: list[int], final: list[int]
    ) -> None:
        plan = plan_looks(
            SIX, np.array([collected]), np.array(done), mode="one_host"
        )
        row = plan.rounds[0]
        assert row.tolist() == looks + [0] * (6 - len(looks))
        assert row[plan.final[0]].tolist() == final

    def test_budget_cut_off_replaces_the_remaining_looks(self) -> None:
        # Out at 35 rounds: 10, 20, 30 scheduled, then the final look at
        # 34 complete order pairs' worth, and never a seventh look.
        plan = plan_looks(SIX, np.array([35]), True, mode="one_host")
        assert plan.rounds[0].tolist() == [10, 20, 30, 34, 0, 0]
        assert plan.final[0].tolist() == [False] * 3 + [True, False, False]

    def test_cross_host_counts_pairs_without_parity(self) -> None:
        schedule = LookSchedule(n0=11, k=3, max_looks=4)
        plan = plan_looks(
            schedule, np.array([10, 11, 15, 30]), True, mode="cross_host"
        )
        assert plan.rounds.tolist() == [
            [0, 0, 0, 0],
            [11, 0, 0, 0],
            [11, 14, 15, 0],
            [11, 14, 17, 20],
        ]
        assert plan.final.sum(axis=1).tolist() == [0, 1, 1, 1]

    def test_one_look_schedule(self) -> None:
        schedule = LookSchedule(n0=40, k=2, max_looks=1)
        plan = plan_looks(
            schedule, np.array([39, 40, 41, 100]), True, mode="one_host"
        )
        assert plan.rounds[:, 0].tolist() == [0, 40, 40, 40]
        assert plan.final[:, 0].tolist() == [False, True, True, True]

    @SETTINGS
    @given(
        n0=st.integers(min_value=5, max_value=30).map(lambda x: 2 * x),
        k=st.integers(min_value=1, max_value=20).map(lambda x: 2 * x),
        looks=st.integers(min_value=1, max_value=8),
        collected=st.lists(
            st.integers(min_value=0, max_value=400), min_size=1, max_size=20
        ),
        done=st.booleans(),
    )
    def test_invariants(
        self, n0: int, k: int, looks: int, collected: list[int], done: bool
    ) -> None:
        schedule = LookSchedule(n0=n0, k=k, max_looks=looks)
        a = np.array(collected)
        plan = plan_looks(schedule, a, done, mode="one_host")
        scheduled = {n0 + j * k for j in range(looks)}
        for row, final, got in zip(plan.rounds, plan.final, a, strict=True):
            taken = row[row > 0]
            count = taken.size
            assert count <= looks
            assert np.all(row[count:] == 0)
            assert np.all(np.diff(taken) > 0)
            assert np.all(taken % 2 == 0)
            assert np.all(taken <= got)
            assert np.all(taken >= n0)
            # Every look but the last is scheduled; the last may be the
            # budget's, at the complete pairs collected.
            assert set(taken[:-1].tolist()) <= scheduled
            ends = done or (count == looks)
            assert final.sum() == (1 if ends and count else 0)
            if final.any():
                assert int(np.argmax(final)) == count - 1
            if done and count:
                last = int(taken[-1])
                assert last in scheduled or last == got - got % 2
            if done and got - got % 2 >= n0:
                assert count >= 1

    @pytest.mark.parametrize(
        ("schedule", "mode", "match"),
        [
            (LookSchedule(n0=11, k=10, max_looks=3), "one_host", "even"),
            (LookSchedule(n0=10, k=5, max_looks=3), "one_host", "even"),
            (SHORT, "same_host", "mode"),
            (
                LookSchedule.model_construct(n0=8, k=2, max_looks=3),
                "one_host",
                "n0",
            ),
            ({"n0": 10, "k": 10, "max_looks": 3}, "one_host", "LookSchedule"),
        ],
    )
    def test_refuses_bad_schedules(
        self, schedule: object, mode: str, match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            plan_looks(
                schedule,  # pyright: ignore[reportArgumentType]
                np.array([20]),
                True,
                mode=mode,  # pyright: ignore[reportArgumentType]
            )

    @pytest.mark.parametrize(
        ("rounds", "exhausted", "match"),
        [
            (np.array([20.0]), True, "rounds"),
            (np.array([-1]), True, "rounds"),
            (np.array([[20]]), True, "rounds"),
            (np.array([20]), 1, "exhausted"),
            (np.array([20, 30]), np.array([True, False, True]), "exhausted"),
        ],
    )
    def test_refuses_bad_counts(
        self, rounds: object, exhausted: object, match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            plan_looks(
                SHORT,
                rounds,  # pyright: ignore[reportArgumentType]
                exhausted,  # pyright: ignore[reportArgumentType]
                mode="one_host",
            )


class TestRoundErrorBounds:
    def test_hand_values(self) -> None:
        # 10 with a step of 1 is known to within -log(0.9) on the log
        # scale; 4 with a step of 1 to within -log(0.75).
        e = round_error_bounds(
            np.array([10.0, 10.0, 7.0]),
            np.array([4.0, 4.0, 3.0]),
            np.array([1.0, 0.0, 0.0]),
            np.array([1.0, 1.0, 0.0]),
        )
        expected = [
            -math.log(0.9) - math.log(0.75),
            -math.log(0.75),
            0.0,
        ]
        np.testing.assert_allclose(e, expected, rtol=1e-14, atol=0)
        assert e[2] == 0.0

    def test_value_at_or_below_its_step_has_no_bound(self) -> None:
        e = round_error_bounds(
            np.array([1.0, 0.5, 1.0, 1.0]),
            np.array([2.0, 2.0, 2.0, 2.0]),
            np.array([1.0, 1.0, math.nextafter(1.0, 0.0), 0.5]),
            0.0,
        )
        # At the step, below it, and within rounding of it (q / v one
        # float below 1, so no finite bound can be certified).
        assert np.isinf(e[:3]).all()
        assert e[3] == pytest.approx(math.log(2), rel=1e-14)

    @SETTINGS
    @given(
        value=st.floats(min_value=1e-6, max_value=1e6),
        fraction=st.floats(min_value=1e-12, max_value=0.999),
    )
    def test_never_below_the_exact_bound(
        self, value: float, fraction: float
    ) -> None:
        step = value * fraction
        e = float(round_error_bounds([value], [1.0], [step], [0.0])[0])
        exact = exact_log_error(step, value)
        assert Decimal(e) >= exact
        # Conservative by rounding only: under 1e-13 relative.
        assert Decimal(e) <= exact * (1 + Decimal("1e-13"))

    def test_bound_covers_a_libm_error_of_libm_ulps(self) -> None:
        # This platform's log1p is more accurate than the analysis
        # assumes, so the margin is pinned directly: the bound clears
        # the libm result on the rounded-up quotient by LIBM_ULPS ulps.
        rng = np.random.default_rng(SEED)
        value = 10.0 ** rng.uniform(-3, 3, 1000)
        step = value * rng.uniform(1e-9, 0.99, 1000)
        e = round_error_bounds(value, np.ones(1000), step, 0.0)
        g = -np.log1p(-np.nextafter(step / value, np.inf))
        assert np.all(e >= g + LIBM_ULPS * np.spacing(g))

    def test_bound_is_the_derived_expression(self) -> None:
        # Items 1 and 2 of the module docstring, constant by constant:
        # each value's bound is g (1 + 5 * 2**-52) plus five subnormals,
        # with g = -log1p(-next float above q / v), and the round's sum
        # of the two is raised by one rounding, (1 + 2 * 2**-52).
        rng = np.random.default_rng(SEED)
        b = 10.0 ** rng.uniform(-3, 3, 500)
        c = 10.0 ** rng.uniform(-3, 3, 500)
        qb = b * rng.uniform(1e-9, 0.5, 500)
        qc = c * rng.uniform(1e-9, 0.5, 500)
        e = round_error_bounds(b, c, qb, qc)
        tiny = 2.0**-1074

        def per_value(q: FloatArray, v: FloatArray) -> FloatArray:
            g = -np.log1p(-np.nextafter(q / v, np.inf))
            return g * (1 + (LIBM_ULPS + 1) * 2.0**-52) + 5 * tiny

        total = per_value(qb, b) + per_value(qc, c)
        assert e.tolist() == (total * (1 + 2 * 2.0**-52)).tolist()
        for got, sb, sc in zip(
            e.tolist(),
            per_value(qb, b).tolist(),
            per_value(qc, c).tolist(),
            strict=True,
        ):
            assert Fraction(got) >= Fraction(sb) + Fraction(sc)

    def test_subnormal_quotients_keep_a_margin(self) -> None:
        # Where q / v is subnormal the relative margin rounds away, so
        # the five subnormals added carry the libm allowance.
        step = np.array([1e-320, 3e-322, 5e-324])
        e = round_error_bounds(np.ones(3), np.ones(3), step, 0.0)
        g = -np.log1p(-np.nextafter(step, np.inf))
        assert np.all(e >= g + LIBM_ULPS * 2.0**-1074)

    def test_log1p_meets_the_libm_assumption_here(self) -> None:
        # The widening assumes log1p within LIBM_ULPS ulps, as the
        # intervals assume for log and exp; checked on this platform.
        rng = np.random.default_rng(SEED)
        x = np.concatenate(
            [rng.random(2000), 10.0 ** rng.uniform(-300, 0, 2000)]
        )
        x = x[(x > 0) & (x < 1)]
        computed = -np.log1p(-x)
        for xi, got in zip(x.tolist(), computed.tolist(), strict=True):
            with localcontext() as ctx:
                # Enough digits that 1 - x is exact down to x = 1e-300.
                ctx.prec = PREC + 320
                exact = -(1 - Decimal(xi)).ln()
            ulp = Decimal(math.ulp(float(exact)))
            assert abs(Decimal(got) - exact) <= LIBM_ULPS * ulp

    @pytest.mark.parametrize(
        ("args", "match"),
        [
            (([1.0], [1.0, 2.0], 0.0, 0.0), "shape"),
            (([0.0], [1.0], 0.0, 0.0), "baseline"),
            (([1.0], [math.inf], 0.0, 0.0), "candidate"),
            (([1.0], [1.0], -1.0, 0.0), "baseline_step"),
            (([1.0], [1.0], 0.0, math.nan), "candidate_step"),
            (([1.0], [1.0], [0.0, 0.0], 0.0), "broadcast"),
        ],
    )
    def test_refuses_bad_inputs(
        self, args: tuple[Any, Any, Any, Any], match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            round_error_bounds(*args)


class TestWiden:
    def test_zero_leaves_bounds_bit_identical(self) -> None:
        lo, hi = np.array([1.03, 0.5]), np.array([1.03, 2.0])
        wl, wh = widen(lo, hi, 0.0)
        assert wl.tolist() == lo.tolist()
        assert wh.tolist() == hi.tolist()

    @SETTINGS
    @given(
        lower=st.floats(min_value=1e-3, max_value=1e3),
        spread=st.floats(min_value=1.0, max_value=10.0),
        a=st.floats(min_value=1e-12, max_value=5.0),
    )
    def test_outward_and_never_inside_the_exact_bounds(
        self, lower: float, spread: float, a: float
    ) -> None:
        upper = lower * spread
        wl, wh = widen(np.array([lower]), np.array([upper]), np.array([a]))
        with localcontext() as ctx:
            ctx.prec = PREC
            factor = Decimal(a).exp()
            exact_lo = Decimal(lower) / factor
            exact_hi = Decimal(upper) * factor
        assert Decimal(float(wl[0])) <= exact_lo
        assert Decimal(float(wh[0])) >= exact_hi
        assert Decimal(float(wl[0])) >= exact_lo * (1 - Decimal("1e-14"))
        assert Decimal(float(wh[0])) <= exact_hi * (1 + Decimal("1e-14"))

    def test_lower_bound_below_the_normal_range_is_zero(self) -> None:
        wl, wh = widen(np.array([1e-300]), np.array([1.0]), np.array([30.0]))
        assert wl[0] == 0.0
        assert wh[0] == pytest.approx(math.exp(30.0), rel=1e-14)

    @pytest.mark.parametrize(
        ("lower", "upper", "a", "match"),
        [
            ([1.0], [1.0, 2.0], 0.0, "shape"),
            ([0.0], [1.0], 0.0, "lower"),
            ([1.0], [1.0], -1e-3, "mean_error"),
        ],
    )
    def test_refuses_bad_inputs(
        self, lower: object, upper: object, a: object, match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            widen(lower, upper, a)  # pyright: ignore[reportArgumentType]


class TestContinuity:
    @pytest.mark.parametrize(
        ("values", "fails"),
        [
            ([1, 1, 1, 1], True),
            ([1, 2, 1, 2], False),
            ([1, 1, 1, 2, 1, 1], True),
            ([1, 2, 3, 1, 1, 1], False),
            ([9.4, 9.5, 9.4, 9.6, 9.5, 9.7, 9.4, 9.8], False),
            ([5.0], False),
        ],
    )
    def test_fewer_distinct_values_than_half_the_rounds(
        self, values: list[float], fails: bool
    ) -> None:
        assert bool(continuity_fails(np.array(values, dtype=float))) is fails

    def test_batched_rows(self) -> None:
        x = np.array([[1.0, 1.0, 1.0, 1.0], [1.0, 2.0, 3.0, 4.0]])
        assert continuity_fails(x).tolist() == [True, False]

    def test_refuses_no_rounds(self) -> None:
        with pytest.raises(ValueError, match="rounds"):
            continuity_fails(np.zeros((2, 0)))


class TestLag1:
    def test_hand_value_one_order(self) -> None:
        d = np.array([1.0, 2.0, 3.0, 4.0])
        # Deviations -1.5, -0.5, 0.5, 1.5: products 0.75 - 0.25 + 0.75
        # = 1.25 over squares 5.
        r = lag1_autocorrelation(d, np.zeros(4, dtype=np.bool_))
        assert float(r) == pytest.approx(0.25, abs=1e-15)

    def test_balanced_order_effect_is_not_autocorrelation(self) -> None:
        # A 5% order effect over 0.7% noise in d: within an order pair
        # the two deviations have opposite signs, so the plain estimate
        # is about -0.5 * 0.049**2 / (0.049**2 + 0.0071**2) = -0.49,
        # while independent rounds have none once each order's mean is
        # removed. 200 x 400 rounds: Monte Carlo se about 0.003.
        sample = simulate(
            NoiseScenario(cv=0.005, order_effect=0.05),
            speedup=1.03,
            n=400,
            replications=200,
            mode="one_host",
            rng=np.random.default_rng(SEED),
            order="balanced",
        )
        d = sample.log_times[..., 0] - sample.log_times[..., 1]
        x = d - d.mean(axis=-1, keepdims=True)
        plain = (x[:, 1:] * x[:, :-1]).sum(axis=-1) / (x * x).sum(axis=-1)
        assert float(plain.mean()) == pytest.approx(-0.49, abs=0.02)
        adjusted = lag1_autocorrelation(d, sample.candidate_first)
        assert float(adjusted.mean()) == pytest.approx(0.0, abs=0.02)

    def test_no_deviation_left_is_undefined(self) -> None:
        d = np.array([[0.5, -0.5, -0.5, 0.5], [1.0, 1.0, 1.0, 1.0]])
        first = np.array([[True, False, False, True]] * 2)
        assert np.isnan(lag1_autocorrelation(d, first)).all()

    def test_matches_the_definition_with_noise(self) -> None:
        rng = np.random.default_rng(SEED)
        d = rng.standard_normal((5, 30))
        first = rng.random((5, 30)) < 0.5
        d += np.where(first, 0.3, -0.3)
        got = lag1_autocorrelation(d, first)
        for row in range(5):
            x = d[row].copy()
            for flag in (True, False):
                x[first[row] == flag] -= d[row][first[row] == flag].mean()
            want = (x[1:] * x[:-1]).sum() / (x * x).sum()
            assert got[row] == pytest.approx(want, abs=1e-14)

    def test_ar1_is_recovered_under_balanced_order(self) -> None:
        sample = simulate(
            NoiseScenario(cv=0.05, ar1=0.5, order_effect=0.03),
            speedup=1.0,
            n=400,
            replications=200,
            mode="one_host",
            rng=np.random.default_rng(SEED),
            order="balanced",
        )
        d = sample.log_times[..., 0] - sample.log_times[..., 1]
        r = lag1_autocorrelation(d, sample.candidate_first)
        # 200 x 400 rounds: the mean's Monte Carlo se is about 0.003,
        # and the estimator's bias at n = 400 about -0.004.
        assert float(r.mean()) == pytest.approx(0.5, abs=0.02)

    @pytest.mark.parametrize(
        ("d", "first", "match"),
        [
            (np.zeros(4), np.zeros(4), "boolean"),
            (np.zeros(4), np.zeros(3, dtype=np.bool_), "shape"),
            (np.zeros(1), np.zeros(1, dtype=np.bool_), "2 rounds"),
            (np.array([0.0, math.inf]), np.zeros(2, dtype=np.bool_), "finite"),
        ],
    )
    def test_refuses_bad_inputs(
        self, d: FloatArray, first: NDArray[Any], match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            lag1_autocorrelation(d, first)


class TestDriverByHand:
    # (m, s) per replication, and the rule at each of the three looks
    # (10, 20, 30 rounds) of SHORT, worked from the closed form with
    # q = 3.69, 2.98, 2.85 at confidence 1 - 0.05 / 6:
    # A: 1.2 * exp(-/+ 0.0123) at 10 rounds, L > 1.03: KEEP.
    # B: 0.95 * exp(-/+ 0.0123), U < 1: REJECT with regression.
    # C: 1.05 * exp(-/+ 0.246, 0.137, 0.106): straddles at every look,
    #    INCONCLUSIVE at the third, the end of the schedule.
    # D: 1.0 * exp(-/+ 0.0062): U = 1.006 <= 1.03, REJECT, U >= 1.
    # E: 1.10 * exp(-/+ 0.0738) at 10, L = 1.022: continue; at 20,
    #    1.10 * exp(-/+ 0.0410), L = 1.056: KEEP.
    ROWS: tuple[tuple[str, float, float, list[int]], ...] = (
        ("A", 1.20, 0.01, [2, 0, 0]),
        ("B", 0.95, 0.01, [3, 0, 0]),
        ("C", 1.05, 0.20, [4, 4, 5]),
        ("D", 1.00, 0.005, [3, 0, 0]),
        ("E", 1.10, 0.06, [4, 2, 0]),
    )

    def result(self) -> tuple[SequentialResult, FloatArray]:
        d = np.stack(
            [alternating(math.log(s), sd, 30) for _, s, sd, _ in self.ROWS]
        )
        b, c = arms(d)
        return drive(b, c), d

    def test_rules_at_every_look(self) -> None:
        res, _ = self.result()
        assert res.rule.tolist() == [rules for *_, rules in self.ROWS]
        assert res.outcome.tolist() == [
            "KEEP",
            "REJECT",
            "INCONCLUSIVE",
            "REJECT",
            "KEEP",
        ]
        assert res.regression.tolist() == [
            [False] * 3,
            [True, False, False],
            [False] * 3,
            [False] * 3,
            [False] * 3,
        ]
        assert res.looks.tolist() == [1, 1, 3, 1, 2]
        assert res.rounds_used.tolist() == [10, 10, 30, 10, 20]
        assert res.final_rule.tolist() == [2, 3, 5, 3, 2]
        assert res.rounds.tolist() == [
            [10, 0, 0],
            [10, 0, 0],
            [10, 20, 30],
            [10, 0, 0],
            [10, 20, 0],
        ]

    def test_bounds_match_the_closed_form(self) -> None:
        res, _ = self.result()
        conf = 1 - 0.05 / 6
        assert res.confidence == look_confidence(0.05, 3)
        for row, (_, speedup, sd, rules) in enumerate(self.ROWS):
            for look, rule in enumerate(rules):
                if rule == 0:
                    assert math.isnan(res.lower[row, look])
                    assert math.isnan(res.upper[row, look])
                    continue
                n = 10 * (look + 1)
                lo, hi = hand_bounds(math.log(speedup), sd, n, conf)
                assert res.lower[row, look] == pytest.approx(lo, rel=1e-12)
                assert res.upper[row, look] == pytest.approx(hi, rel=1e-12)

    def test_bounds_are_the_interval_on_the_rounds_so_far(self) -> None:
        res, d = self.result()
        b, c = arms(d)
        for look in range(3):
            n = 10 * (look + 1)
            found = t_interval(
                b[:, :n],
                c[:, :n],
                direction="lower_is_better",
                confidence=res.confidence,
            )
            taken = res.rule[:, look] > 0
            assert res.lower[taken, look].tolist() == (
                found.lower[taken].tolist()
            )
            assert res.upper[taken, look].tolist() == (
                found.upper[taken].tolist()
            )


class TestDriverBudget:
    def test_budget_runs_out_between_looks(self) -> None:
        # Straddling data out of budget at 25 rounds of a six-look
        # schedule: looks at 10 and 20 continue, the final look at 24
        # (the unpaired 25th round dropped) ends it INCONCLUSIVE, at
        # the confidence of six looks.
        d = alternating(math.log(1.05), 0.2, 60)[np.newaxis, :]
        b, c = arms(d)
        res = drive(b, c, schedule=SIX, rounds=np.array([25]))
        assert res.rounds[0].tolist() == [10, 20, 24, 0, 0, 0]
        assert res.rule[0].tolist() == [4, 4, 5, 0, 0, 0]
        assert res.outcome.tolist() == ["INCONCLUSIVE"]
        assert res.confidence == look_confidence(0.05, 6)
        lo, hi = hand_bounds(math.log(1.05), 0.2, 24, res.confidence)
        assert res.lower[0, 2] == pytest.approx(lo, rel=1e-12)
        assert res.upper[0, 2] == pytest.approx(hi, rel=1e-12)

    def test_budget_cut_off_look_can_decide(self) -> None:
        d = alternating(math.log(1.10), 0.06, 60)[np.newaxis, :]
        b, c = arms(d)
        res = drive(b, c, schedule=SIX, rounds=np.array([17]))
        # 10: continue (as row E above); 16: L = 1.10 exp(-q s / sqrt
        # 15) with q = 3.12 at 15 df, 1.050 > 1.03: KEEP.
        assert res.rounds[0].tolist() == [10, 16, 0, 0, 0, 0]
        assert res.rule[0].tolist() == [4, 2, 0, 0, 0, 0]
        assert res.outcome.tolist() == ["KEEP"]

    def test_faster_candidate_straddling_at_the_budget_is_inconclusive(
        self,
    ) -> None:
        # A 15% faster point estimate, far above the bar, with an
        # interval that still straddles it when the budget ends: never
        # KEEP.
        d = alternating(math.log(1.15), 0.5, 60)[np.newaxis, :]
        b, c = arms(d)
        for budget in (10, 20, 33, 60):
            res = drive(b, c, schedule=SIX, rounds=np.array([budget]))
            taken = res.rule[0] > 0
            assert np.all(res.lower[0, taken] <= BAR)
            assert np.all(res.upper[0, taken] > BAR)
            n = int(res.rounds_used[0])
            assert math.exp(float(d[0, :n].mean())) > 1.14
            assert res.outcome.tolist() == ["INCONCLUSIVE"]
            assert int(res.final_rule[0]) == 5

    def test_budget_before_n0_gives_no_look(self) -> None:
        d = alternating(math.log(1.5), 0.001, 20)
        b, c = arms(np.stack([d, d]))
        res = drive(
            b, c, rounds=np.array([9, 9]), exhausted=np.array([True, False])
        )
        assert res.looks.tolist() == [0, 0]
        assert np.isnan(res.lower).all()
        assert res.outcome.tolist() == ["INCONCLUSIVE", "CONTINUE"]
        assert res.final_rule.tolist() == [5, 4]

    def test_live_comparison_between_looks_continues(self) -> None:
        d = alternating(math.log(1.05), 0.2, 15)[np.newaxis, :]
        b, c = arms(d)
        res = drive(b, c, exhausted=False)
        assert res.rule[0].tolist() == [4, 0, 0]
        assert res.outcome.tolist() == ["CONTINUE"]
        # The same rounds with the budget gone: a final look at 14.
        res = drive(b, c, exhausted=True)
        assert res.rounds[0].tolist() == [10, 14, 0]
        assert res.rule[0].tolist() == [4, 5, 0]

    def test_no_look_after_a_stop(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        res = drive(b, c)
        assert res.rounds[0].tolist() == [10, 0, 0]
        assert res.rule[0].tolist() == [2, 0, 0]

    def test_uncalibrated_never_keeps_or_rejects(self) -> None:
        d = np.stack(
            [
                alternating(math.log(1.2), 0.01, 30),
                alternating(math.log(0.9), 0.01, 30),
            ]
        )
        b, c = arms(d)
        res = drive(b, c, calibrated=False)
        assert res.rule.tolist() == [[4, 4, 5], [4, 4, 5]]
        assert res.outcome.tolist() == ["INCONCLUSIVE"] * 2


class TestDriverResolution:
    def test_bounds_are_widened_by_the_mean_round_error(self) -> None:
        rng = np.random.default_rng(SEED)
        b = 0.010 * np.exp(0.02 * rng.standard_normal((4, 30)))
        c = b / 1.06 * np.exp(0.02 * rng.standard_normal((4, 30)))
        q = 1e-6
        res = drive(b, c, resolution=Resolution(q, q, False))
        bare = drive(b, c)
        for row in range(4):
            for look in range(3):
                n = int(res.rounds[row, look])
                if n == 0:
                    continue
                found = t_interval(
                    b[row : row + 1, :n],
                    c[row : row + 1, :n],
                    direction="lower_is_better",
                    confidence=res.confidence,
                )
                e = round_error_bounds(b[row, :n], c[row, :n], q, q)
                exact = sum(
                    exact_log_error(q, v)
                    for v in [*b[row, :n].tolist(), *c[row, :n].tolist()]
                ) / Decimal(n)
                mean = float(e.sum() / n)
                assert Decimal(mean) <= exact * (1 + Decimal("1e-13"))
                wl, wh = widen(found.lower, found.upper, mean)
                # The driver's mean bound is rounded up, not below the
                # exact mean; its bounds sit at or outside these.
                assert res.lower[row, look] <= wl[0]
                assert res.upper[row, look] >= wh[0]
                assert res.lower[row, look] == pytest.approx(wl[0], rel=1e-13)
                assert res.upper[row, look] == pytest.approx(wh[0], rel=1e-13)
                assert res.lower[row, look] < bare.lower[row, look] or (
                    bare.rule[row, look] == 0
                )

    def test_widening_can_hold_back_a_keep(self) -> None:
        # Unwidened, L = 1.2 exp(-0.0123) = 1.185 at 10 rounds. With a
        # step of 0.1 on baseline values near 1.2 and candidate values
        # of 1, each bound widens by -log(1 - 0.1 / 1.2) - log(0.9) =
        # 0.192 on the log scale, so L = 0.978.
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        bare = drive(b, c)
        assert bare.rule[0].tolist() == [2, 0, 0]
        res = drive(b, c, resolution=Resolution(0.1, 0.1, False))
        assert res.rule[0, 0] == 4
        assert res.lower[0, 0] < BAR < bare.lower[0, 0]

    def test_identical_ratios_are_widened_too(self) -> None:
        b = np.full((1, 30), 2.4)
        c = np.full((1, 30), 2.0)
        bare = drive(b, c)
        assert bare.lower[0, 0] == bare.upper[0, 0] == 1.2
        res = drive(b, c, resolution=Resolution(0.001, 0.001, False))
        assert res.lower[0, 0] < 1.2 < res.upper[0, 0]

    def test_value_at_its_step_stops_before_rule_2(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        step = np.zeros_like(b)
        step[0, 3] = b[0, 3]
        res = drive(b, c, resolution=Resolution(step, 0.0, False))
        assert res.rule[0].tolist() == [1, 0, 0]
        assert res.outcome.tolist() == ["INCONCLUSIVE"]
        assert np.isnan(res.lower[0, 0])

    def test_resolution_stop_at_a_later_look(self) -> None:
        # Straddling at 10 rounds; round 13 (index 12) has a candidate
        # value no larger than its step, so the look at 20 stops.
        d = alternating(math.log(1.05), 0.2, 30)[np.newaxis, :]
        b, c = arms(d)
        step = np.zeros_like(c)
        step[0, 12] = 2.0
        res = drive(b, c, resolution=Resolution(0.0, step, False))
        assert res.rule[0].tolist() == [4, 1, 0]
        assert res.outcome.tolist() == ["INCONCLUSIVE"]
        assert int(res.final_rule[0]) == 1

    def test_resolution_stop_applies_before_calibration(self) -> None:
        d = alternating(math.log(1.05), 0.2, 30)[np.newaxis, :]
        b, c = arms(d)
        step = np.zeros_like(c)
        step[0, 0] = 1.0
        res = drive(
            b, c, calibrated=False, resolution=Resolution(0.0, step, False)
        )
        assert res.rule[0].tolist() == [1, 0, 0]

    @pytest.mark.parametrize("discrete", ["baseline", "candidate"])
    def test_continuity_check_covers_each_arm_alone(
        self, discrete: str
    ) -> None:
        # One arm prints two distinct values over 10 rounds, the other
        # is continuous; ratios near 1.2 with 0.1% spread are
        # KEEP-worthy. Either arm alone failing the check stops it.
        rng = np.random.default_rng(SEED)
        jitter = np.exp(0.001 * rng.standard_normal((1, 30)))
        two = np.where(np.arange(30) % 2 == 0, 1.0, 1.001)[np.newaxis, :]
        if discrete == "candidate":
            c = two
            b = 1.2 * c * jitter
        else:
            b = 1.2 * two
            c = b / 1.2 * jitter
        assert bool(continuity_fails(b[:, :10])) is (discrete == "baseline")
        assert bool(continuity_fails(c[:, :10])) is (discrete == "candidate")
        trusted = drive(b, c, resolution=Resolution(1e-9, 1e-9, False))
        assert trusted.rule[0, 0] == 2
        checked = drive(b, c, resolution=Resolution(1e-9, 1e-9, True))
        assert checked.rule[0].tolist() == [1, 0, 0]
        assert checked.outcome.tolist() == ["INCONCLUSIVE"]

    def test_continuity_check_only_when_asked(self) -> None:
        # A KEEP-worthy candidate whose values take two distinct values:
        # fewer than half of 10 rounds, so it can't be trusted when the
        # resolution came from printed digits.
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        trusted = drive(b, c, resolution=Resolution(1e-9, 1e-9, False))
        assert trusted.rule[0, 0] == 2
        checked = drive(b, c, resolution=Resolution(1e-9, 1e-9, True))
        assert checked.rule[0].tolist() == [1, 0, 0]

    def test_cross_host_takes_bounds_not_steps(self) -> None:
        # Cross host the values are session means, whose steps say
        # nothing about the rounds inside; the caller passes the session
        # pairs' bounds instead (TestCrossHostResolution).
        d = alternating(0.0, 0.01, 20)[np.newaxis, :]
        b, c = arms(d)
        with pytest.raises(ValueError, match="ObservationBounds"):
            drive(b, c, mode="cross_host", resolution=Resolution(0, 0, False))

    def test_refuses_a_non_bool_continuity_flag(self) -> None:
        d = alternating(0.0, 0.01, 20)[np.newaxis, :]
        b, c = arms(d)
        resolution = Resolution(0.0, 0.0, 1)  # pyright: ignore[reportArgumentType]
        with pytest.raises(ValueError, match="check_continuity"):
            drive(b, c, resolution=resolution)


class TestDriverDecidesThroughDecision:
    def simulated(
        self, mode: HostMode, speedup: float
    ) -> tuple[FloatArray, FloatArray]:
        sample = simulate(
            NoiseScenario(cv=0.03),
            speedup=speedup,
            n=60,
            replications=300,
            mode=mode,
            rng=np.random.default_rng(SEED),
            order="balanced" if mode == "one_host" else None,
        )
        times = np.exp(sample.log_times)
        return times[..., 0], times[..., 1]

    @pytest.mark.parametrize("mode", ["one_host", "cross_host"])
    @pytest.mark.parametrize("speedup", [0.98, 1.03, 1.05])
    def test_every_look_agrees_with_decide(
        self, mode: HostMode, speedup: float
    ) -> None:
        b, c = self.simulated(mode, speedup)
        rng = np.random.default_rng(SEED + 1)
        collected = rng.integers(0, 61, size=b.shape[0])
        res = drive(b, c, schedule=SIX, mode=mode, rounds=collected)
        plan = plan_looks(SIX, collected, True, mode=mode)
        cross = mode == "cross_host"
        gates = passed(cross=cross)
        seen = set[int]()
        for row in range(b.shape[0]):
            for look in range(6):
                rule = int(res.rule[row, look])
                if rule == 0:
                    continue
                n = int(res.rounds[row, look])
                assert n == plan.rounds[row, look]
                found = t_interval(
                    b[row, :n],
                    c[row, :n],
                    direction="lower_is_better",
                    confidence=res.confidence,
                )
                bounds = (float(found.lower), float(found.upper))
                assert (
                    float(res.lower[row, look]),
                    float(res.upper[row, look]),
                ) == bounds
                verdict = decide(
                    gates,
                    bounds,
                    min_effect=MIN_EFFECT,
                    budget_remains=not bool(plan.final[row, look]),
                    calibrated=True,
                    session_pairs=n if cross else None,
                    n0=SIX.n0 if cross else None,
                )
                assert verdict.rule == rule
                assert verdict.regression == res.regression[row, look]
                seen.add(rule)
            ended = res.final_rule[row]
            assert (
                res.outcome[row]
                == OUTCOMES[{2: 0, 3: 1, 5: 2, 1: 2, 4: 3}[int(ended)]]
            )
        # The draws reach more than one rule, so the agreement is not
        # vacuous.
        assert len(seen) >= 2

    def test_rules_come_only_from_interval_rules(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []

        def keep_all(lower: Any, upper: Any, **_: Any) -> IntervalRules:
            calls.append(np.asarray(lower).size)
            shape = np.asarray(lower).shape
            return IntervalRules(
                rule=np.full(shape, 2, dtype=np.int8),
                regression=np.zeros(shape, dtype=np.bool_),
            )

        monkeypatch.setattr(sequential, "interval_rules", keep_all)
        d = alternating(math.log(0.5), 0.01, 30)[np.newaxis, :]
        b, c = arms(np.repeat(d, 4, axis=0))
        res = drive(b, c)
        # A clear regression, and yet KEEP: the stub alone decided.
        assert res.outcome.tolist() == ["KEEP"] * 4
        assert calls == [4]
        assert decision.interval_rules is not keep_all


class TestDriverModesAndProcedures:
    def test_cross_host_passes_pairs_to_the_decision(self) -> None:
        d = np.stack(
            [
                alternating(math.log(1.2), 0.01, 30),
                alternating(math.log(1.05), 0.2, 30),
            ]
        )
        b, c = arms(d)
        one = drive(b, c)
        cross = drive(b, c, mode="cross_host")
        assert cross.rule.tolist() == one.rule.tolist()
        odd = drive(
            b,
            c,
            mode="cross_host",
            schedule=LookSchedule(n0=11, k=7, max_looks=3),
        )
        assert odd.rounds.tolist() == [[11, 0, 0], [11, 18, 25]]

    def test_widening_averages_only_the_rounds_the_interval_used(
        self,
    ) -> None:
        # Batch means at a cut-off look of 14 rounds uses 12 (two blocks
        # of 6). Identical ratios of 1.04766 give the exact interval at
        # that ratio; the 12 used rounds have bounds of 0.018 and the 2
        # dropped have 0. Widening by their mean, 0.018, gives
        # L = 1.04766 exp(-0.018) = 1.028971 <= 1.03: INCONCLUSIVE at
        # the last look. Averaging over all 14 (0.015429) would give
        # L = 1.031620 and a KEEP.
        b = np.full((1, 30), 1.04766)
        c = np.ones((1, 30))
        errors = np.zeros((1, 30))
        errors[0, :12] = 0.018
        look = decide_look(
            b[:, :14],
            c[:, :14],
            direction="lower_is_better",
            confidence=look_confidence(0.05, 3),
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=True,
            resolution=ObservationBounds(errors[:, :14]),
            procedure="t_log_ratio_batch_means",
            candidate_first=np.zeros(np.shape(b[:, :14]), dtype=np.bool_),
        )
        lo = 1.04766 * math.exp(-0.018)
        hi = 1.04766 * math.exp(0.018)
        assert float(look.lower[0]) <= lo
        assert float(look.upper[0]) >= hi
        assert float(look.lower[0]) == pytest.approx(lo, rel=1e-13)
        assert float(look.upper[0]) == pytest.approx(hi, rel=1e-13)
        assert 1.028970 < lo < 1.028972
        assert look.rule.tolist() == [5]
        assert look.action.tolist() == ["INCONCLUSIVE"]
        res = drive(
            b,
            c,
            rounds=np.array([15]),
            procedure="t_log_ratio_batch_means",
            resolution=ObservationBounds(errors),
        )
        assert res.rounds[0].tolist() == [10, 14, 0]
        assert res.rule[0].tolist() == [4, 5, 0]
        assert res.lower[0, 1] == look.lower[0]
        assert res.outcome.tolist() == ["INCONCLUSIVE"]

    def test_batch_means_drops_the_trailing_block(self) -> None:
        rng = np.random.default_rng(SEED)
        b = np.exp(0.05 * rng.standard_normal((3, 60)))
        c = b / 1.04 * np.exp(0.2 * rng.standard_normal((3, 60)))
        res = drive(
            b,
            c,
            schedule=SIX,
            rounds=np.array([14, 14, 14]),
            procedure="t_log_ratio_batch_means",
        )
        assert res.procedure == "t_log_ratio_batch_means"
        assert res.rounds[:, 1].tolist() == [14, 14, 14]
        found = t_interval(
            b[:, :14],
            c[:, :14],
            direction="lower_is_better",
            confidence=res.confidence,
            block=BATCH_BLOCK,
        )
        assert found.rounds == 12
        taken = res.rule[:, 1] > 0
        assert res.lower[taken, 1].tolist() == found.lower[taken].tolist()

    def test_higher_is_better_inverts_the_ratio(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        res = run_looks(
            c,
            b,
            direction="higher_is_better",
            schedule=SHORT,
            mode="one_host",
            min_effect=MIN_EFFECT,
            calibrated=True,
            resolution=None,
            exhausted=True,
            procedure="t_log_ratio",
            candidate_first=np.zeros(np.shape(c), dtype=np.bool_),
        )
        assert res.rule[0].tolist() == [2, 0, 0]

    def test_four_thousand_replications_in_one_call(self) -> None:
        sample = simulate(
            NoiseScenario(cv=0.05),
            speedup=1.03,
            n=60,
            replications=4000,
            mode="one_host",
            rng=np.random.default_rng(SEED),
            order="balanced",
        )
        times = np.exp(sample.log_times)
        res = drive(times[..., 0], times[..., 1], schedule=SIX)
        assert res.outcome.shape == (4000,)
        assert set(res.outcome.tolist()) <= set(OUTCOMES) - {"CONTINUE"}


class TestDriverValidation:
    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"procedure": "wilcoxon"}, "procedure"),
            ({"rounds": np.array([31])}, "at most"),
            ({"rounds": np.array([10, 10])}, "shape"),
            ({"exhausted": "yes"}, "exhausted"),
            ({"schedule": LookSchedule(n0=11, k=10, max_looks=3)}, "even"),
            ({"calibrated": 1}, "calibrated"),
            ({"rounds": np.array([5]), "calibrated": "yes"}, "calibrated"),
        ],
    )
    def test_refuses_bad_arguments(
        self, kwargs: dict[str, Any], match: str
    ) -> None:
        d = alternating(0.0, 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        with pytest.raises(ValueError, match=match):
            drive(b, c, **kwargs)

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("direction", "faster"),
            ("min_effect", -0.01),
            ("min_effect", 3),
            ("min_effect", math.nan),
        ],
    )
    def test_refuses_bad_decision_parameters_before_any_look(
        self, name: str, value: object
    ) -> None:
        # Nine rounds: no look happens, and the arguments are still
        # refused rather than passed through unchecked.
        kwargs: dict[str, Any] = {
            "direction": "lower_is_better",
            "schedule": SHORT,
            "mode": "one_host",
            "min_effect": MIN_EFFECT,
            "calibrated": True,
            "resolution": None,
            "exhausted": True,
            "candidate_first": np.zeros((1, 9), dtype=np.bool_),
            name: value,
        }
        with pytest.raises(ValueError, match=name):
            run_looks(np.ones((1, 9)), np.ones((1, 9)), **kwargs)

    def test_refuses_measurements_not_shaped_r_by_n(self) -> None:
        with pytest.raises(ValueError, match=r"\(R, n\)"):
            drive(np.ones(30), np.ones(30))
        with pytest.raises(ValueError, match=r"\(R, n\)"):
            drive(np.ones((2, 30)), np.ones((2, 29)))

    def test_refuses_non_positive_measurements(self) -> None:
        b = np.ones((1, 30))
        b[0, 5] = 0.0
        with pytest.raises(ValueError, match="baseline"):
            drive(b, np.ones((1, 30)))


class TestGuardrailsHoldBackKeep:
    def keep_worthy(self, rows: int = 1) -> tuple[FloatArray, FloatArray]:
        # L > 1.03 at 10, 20 and 30 rounds: row A of TestDriverByHand.
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        return arms(np.repeat(d, rows, axis=0))

    def test_blocked_keep_continues_then_ends_inconclusive(self) -> None:
        b, c = self.keep_worthy(2)
        res = drive(b, c, keep_blocked=np.array([False, True]))
        assert res.rule.tolist() == [[2, 0, 0], [2, 2, 2]]
        assert res.held.tolist() == [[False] * 3, [True] * 3]
        assert res.action.tolist() == [
            ["KEEP", "", ""],
            ["CONTINUE", "CONTINUE", "INCONCLUSIVE"],
        ]
        assert res.outcome.tolist() == ["KEEP", "INCONCLUSIVE"]
        assert res.final_rule.tolist() == [2, 2]

    def test_per_look_mask_releases_the_keep(self) -> None:
        b, c = self.keep_worthy()
        res = drive(b, c, keep_blocked=np.array([[True, False, False]]))
        assert res.action[0].tolist() == ["CONTINUE", "KEEP", ""]
        assert res.outcome.tolist() == ["KEEP"]

    def test_blocked_keep_at_the_budget_cut_off_is_inconclusive(self) -> None:
        b, c = self.keep_worthy()
        res = drive(b, c, rounds=np.array([15]), keep_blocked=np.array([True]))
        assert res.rounds[0].tolist() == [10, 14, 0]
        assert res.action[0].tolist() == ["CONTINUE", "INCONCLUSIVE", ""]

    def test_blocking_leaves_reject_and_continue_alone(self) -> None:
        d = np.stack(
            [
                alternating(math.log(0.95), 0.01, 30),
                alternating(math.log(1.05), 0.2, 30),
            ]
        )
        b, c = arms(d)
        free = drive(b, c)
        blocked = drive(b, c, keep_blocked=np.array([True, True]))
        assert blocked.rule.tolist() == free.rule.tolist()
        assert blocked.action.tolist() == free.action.tolist()
        assert not blocked.held.any()

    @pytest.mark.parametrize(
        ("mask", "match"),
        [
            (np.array([1]), "boolean"),
            (np.array([True, False]), "neither"),
            (np.array([[True, False]]), "neither"),
        ],
    )
    def test_refuses_bad_masks(self, mask: NDArray[Any], match: str) -> None:
        b, c = self.keep_worthy()
        with pytest.raises(ValueError, match=match):
            drive(b, c, keep_blocked=mask)


class TestDecideLook:
    def look(self, **kwargs: Any) -> Any:
        d = alternating(math.log(1.2), 0.01, 10)[np.newaxis, :]
        b, c = arms(np.repeat(d, 2, axis=0))
        args: dict[str, Any] = {
            "direction": "lower_is_better",
            "confidence": look_confidence(0.05, 3),
            "min_effect": MIN_EFFECT,
            "calibrated": True,
            "final": False,
            "resolution": None,
            "procedure": "t_log_ratio",
        }
        args["candidate_first"] = (
            None if "n0" in kwargs else np.zeros(b.shape, dtype=np.bool_)
        )
        args.update(kwargs)
        return decide_look(b, c, **args)

    def test_held_keep_continues_or_ends_by_budget(self) -> None:
        blocked = np.array([False, True])
        going = self.look(keep_blocked=blocked)
        assert going.rule.tolist() == [2, 2]
        assert going.held.tolist() == [False, True]
        assert going.action.tolist() == ["KEEP", "CONTINUE"]
        ending = self.look(keep_blocked=blocked, final=True)
        assert ending.action.tolist() == ["KEEP", "INCONCLUSIVE"]

    def test_is_what_run_looks_decides(self) -> None:
        rng = np.random.default_rng(SEED)
        b = 0.010 * np.exp(0.02 * rng.standard_normal((50, 30)))
        c = b / 1.04 * np.exp(0.02 * rng.standard_normal((50, 30)))
        resolution = Resolution(1e-6, 1e-6, False)
        res = drive(b, c, resolution=resolution)
        look = decide_look(
            b[:, :10],
            c[:, :10],
            direction="lower_is_better",
            confidence=res.confidence,
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=False,
            resolution=resolution,
            procedure="t_log_ratio",
            candidate_first=np.zeros(np.shape(b[:, :10]), dtype=np.bool_),
        )
        assert look.lower.tolist() == res.lower[:, 0].tolist()
        assert look.upper.tolist() == res.upper[:, 0].tolist()
        assert look.rule.tolist() == res.rule[:, 0].tolist()
        assert look.action.tolist() == res.action[:, 0].tolist()

    def test_widening_is_the_mean_bound_rounded_up(self) -> None:
        # Item 2 of the module docstring: the mean of n bounds is
        # fl(sum / n) raised by (1 + (n + 1) 2**-52), never below the
        # exact mean, and the bounds are widened by exactly that.
        rng = np.random.default_rng(SEED)
        # Bounds of a few units, so the rounding up shows in the bounds.
        errors = rng.uniform(1.0, 9.0, (2, 10))
        look = self.look(resolution=ObservationBounds(errors))
        bare = self.look()
        mean = np.sum(errors, axis=-1) / 10 * (1 + 11 * 2.0**-52)
        for row in range(2):
            exact = sum(Fraction(x) for x in errors[row].tolist()) / 10
            assert Fraction(float(mean[row])) >= exact
        wl, wh = widen(bare.lower, bare.upper, mean)
        assert look.lower.tolist() == wl.tolist()
        assert look.upper.tolist() == wh.tolist()

    def test_cross_host_passes_the_pairs(self) -> None:
        look = self.look(n0=10)
        assert look.rule.tolist() == [2, 2]
        verdict = decide(
            passed(cross=True),
            (float(look.lower[0]), float(look.upper[0])),
            min_effect=MIN_EFFECT,
            budget_remains=True,
            calibrated=True,
            session_pairs=10,
            n0=10,
        )
        assert verdict.rule == 2

    def test_resolution_stop_and_widening(self) -> None:
        bare = self.look()
        step = np.zeros((2, 10))
        step[1, 4] = 5.0
        stopped = self.look(resolution=Resolution(step, 0.0, False))
        assert stopped.rule.tolist() == [2, 1]
        assert stopped.action.tolist() == ["KEEP", "INCONCLUSIVE"]
        assert math.isnan(stopped.lower[1])
        assert stopped.lower[0] == bare.lower[0]
        widened = self.look(resolution=Resolution(0.1, 0.1, False))
        assert widened.rule.tolist() == [4, 4]
        assert np.all(widened.lower < bare.lower)

    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"n0": 9}, "n0"),
            ({"n0": 10, "resolution": Resolution(0.0, 0.0, False)}, "Bounds"),
            ({"final": np.array([True, False, True])}, "final"),
            ({"keep_blocked": 1}, "keep_blocked"),
            ({"procedure": "wilcoxon"}, "procedure"),
            ({"min_effect": 0}, "min_effect"),
            ({"resolution": 0.001}, "resolution"),
        ],
    )
    def test_refuses_bad_arguments(
        self, kwargs: dict[str, Any], match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            self.look(**kwargs)


class TestCrossHostResolution:
    def test_session_pair_error_by_hand(self) -> None:
        # Baseline session at 10 with a step of 1, candidate session at
        # 4: the pair's log ratio is off by at most -log(0.9) -
        # log(0.75), the means of each session's per-value bounds.
        e = session_pair_error([10.0, 10.0], [4.0, 4.0, 4.0], 1.0, 1.0)
        with localcontext() as ctx:
            ctx.prec = PREC
            exact = -Decimal("0.9").ln() - Decimal("0.75").ln()
        assert Decimal(e) >= exact
        assert Decimal(e) <= exact * (1 + Decimal("1e-13"))
        assert session_pair_error([2.0], [3.0], 0.0, 0.0) == 0.0

    def test_session_pair_error_means_each_session_alone(self) -> None:
        # A step on only one of three candidate values counts a third.
        e = session_pair_error([5.0], [4.0, 4.0, 4.0], 0.0, [1.0, 0.0, 0.0])
        assert e == pytest.approx(-math.log(0.75) / 3, rel=1e-13)

    def test_value_at_its_step_has_no_bound(self) -> None:
        assert math.isinf(session_pair_error([1.0, 2.0], [1.0], 1.0, 0.0))

    def test_session_pair_error_is_the_derived_expression(self) -> None:
        # Each session's mean bound is fl(sum / n) (1 + (n + 1) 2**-52),
        # and their sum is raised by one rounding, (1 + 2 * 2**-52).
        rng = np.random.default_rng(SEED)
        tiny = 2.0**-1074
        for _ in range(50):
            nb, nc = (int(x) for x in rng.integers(1, 12, 2))
            b = 10.0 ** rng.uniform(-3, 3, nb)
            c = 10.0 ** rng.uniform(-3, 3, nc)
            qb = b * rng.uniform(1e-9, 0.5, nb)
            qc = c * rng.uniform(1e-9, 0.5, nc)

            def mean_bound(q: FloatArray, v: FloatArray) -> float:
                g = -np.log1p(-np.nextafter(q / v, np.inf))
                e = g * (1 + (LIBM_ULPS + 1) * 2.0**-52) + 5 * tiny
                n = e.size
                return float(np.sum(e) / n * (1 + (n + 1) * 2.0**-52))

            mb, mc = mean_bound(qb, b), mean_bound(qc, c)
            got = session_pair_error(b, c, qb, qc)
            assert got == (mb + mc) * (1 + 2 * 2.0**-52)
            assert Fraction(got) >= Fraction(mb) + Fraction(mc)

    @pytest.mark.parametrize(
        ("baseline", "candidate", "match"),
        [
            ([], [1.0], "baseline"),
            ([[1.0]], [1.0], "baseline"),
            ([1.0], [0.0], "candidate"),
        ],
    )
    def test_session_pair_error_refuses_bad_sessions(
        self, baseline: Any, candidate: Any, match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            session_pair_error(baseline, candidate, 0.0, 0.0)

    @pytest.mark.parametrize(
        ("candidate", "step", "match"),
        [
            ([1.0], -1.0, "candidate_step"),
            ([math.nan], 0.0, "candidate"),
            ([-1.0], 0.0, "candidate"),
        ],
    )
    def test_both_sessions_are_checked_before_an_unbounded_result(
        self, candidate: list[float], step: float, match: str
    ) -> None:
        # The baseline session has a value at its step, so its bound is
        # already infinite; the invalid candidate session still raises.
        with pytest.raises(ValueError, match=match):
            session_pair_error([1.0, 2.0], candidate, 1.0, step)

    def test_widening_cross_host(self) -> None:
        rng = np.random.default_rng(SEED)
        b = np.exp(0.03 * rng.standard_normal((4, 30)))
        c = b / 1.05 * np.exp(0.03 * rng.standard_normal((4, 30)))
        errors = np.full((4, 30), 0.01)
        res = drive(
            b, c, mode="cross_host", resolution=ObservationBounds(errors)
        )
        bare = drive(b, c, mode="cross_host")
        for row in range(4):
            for look in range(3):
                n = int(res.rounds[row, look])
                if n == 0:
                    continue
                found = t_interval(
                    b[row : row + 1, :n],
                    c[row : row + 1, :n],
                    direction="lower_is_better",
                    confidence=res.confidence,
                )
                wl, wh = widen(found.lower, found.upper, 0.01)
                assert res.lower[row, look] <= wl[0]
                assert res.upper[row, look] >= wh[0]
                assert res.lower[row, look] == pytest.approx(wl[0], rel=1e-13)
                assert res.upper[row, look] == pytest.approx(wh[0], rel=1e-13)
        assert np.all(res.lower[:, 0] < bare.lower[:, 0])

    def test_widening_can_hold_back_a_keep_cross_host(self) -> None:
        # L = 1.185 unwidened at 10 pairs; widened by 0.2, 0.970.
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        bare = drive(b, c, mode="cross_host")
        assert bare.rule[0, 0] == 2
        res = drive(
            b,
            c,
            mode="cross_host",
            resolution=ObservationBounds(np.full((1, 30), 0.2)),
        )
        assert res.rule[0, 0] == 4
        assert res.lower[0, 0] < BAR

    def test_unbounded_pair_stops_the_look_that_uses_it(self) -> None:
        d = alternating(math.log(1.05), 0.2, 30)[np.newaxis, :]
        b, c = arms(d)
        errors = np.zeros((1, 30))
        errors[0, 12] = math.inf
        res = drive(
            b, c, mode="cross_host", resolution=ObservationBounds(errors)
        )
        assert res.rule[0].tolist() == [4, 1, 0]
        assert res.outcome.tolist() == ["INCONCLUSIVE"]
        assert math.isnan(res.lower[0, 1])

    def test_coarse_mask_stops_the_look_that_uses_it(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        coarse = np.zeros((1, 30), dtype=np.bool_)
        coarse[0, 3] = True
        res = drive(
            b,
            c,
            mode="cross_host",
            resolution=ObservationBounds(np.zeros((1, 30)), coarse),
        )
        assert res.rule[0].tolist() == [1, 0, 0]

    @pytest.mark.parametrize("final", [False, True])
    def test_no_resolution_stop_below_n0_pairs(self, final: bool) -> None:
        # Six pairs of a schedule with n0 = 10: speed can't decide, so
        # an unbounded pair (row 0) or a coarse one (row 1) neither
        # stops the look nor names coarse_resolution. The comparison
        # continues, and at the end of its budget is INCONCLUSIVE by
        # rule 5, like the bounded row 2.
        d = alternating(math.log(1.2), 0.01, 6)[np.newaxis, :]
        b, c = arms(np.repeat(d, 3, axis=0))
        errors = np.full((3, 6), 0.001)
        errors[0, 2] = math.inf
        coarse = np.zeros((3, 6), dtype=np.bool_)
        coarse[1, 4] = True
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=look_confidence(0.05, 3),
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=final,
            resolution=ObservationBounds(errors, coarse),
            n0=10,
            procedure="t_log_ratio",
            candidate_first=None,
        )
        rule = 5 if final else 4
        assert look.rule.tolist() == [rule] * 3
        want = "INCONCLUSIVE" if final else "CONTINUE"
        assert look.action.tolist() == [want] * 3
        assert np.isnan(look.lower[:2]).all()
        assert np.isnan(look.upper[:2]).all()
        bare = decide_look(
            b[2:],
            c[2:],
            direction="lower_is_better",
            confidence=look_confidence(0.05, 3),
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=final,
            resolution=None,
            n0=10,
            procedure="t_log_ratio",
            candidate_first=None,
        )
        assert look.lower[2] < bare.lower[0]
        assert look.upper[2] > bare.upper[0]

    def test_resolution_stop_from_n0_pairs_on(self) -> None:
        d = alternating(math.log(1.2), 0.01, 10)[np.newaxis, :]
        b, c = arms(d)
        errors = np.zeros((1, 10))
        errors[0, 2] = math.inf
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=look_confidence(0.05, 3),
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=False,
            resolution=ObservationBounds(errors),
            n0=10,
            candidate_first=None,
        )
        assert look.rule.tolist() == [1]
        assert look.action.tolist() == ["INCONCLUSIVE"]

    def test_bounds_on_one_host_match_steps(self) -> None:
        rng = np.random.default_rng(SEED)
        b = 0.010 * np.exp(0.02 * rng.standard_normal((20, 30)))
        c = b / 1.05 * np.exp(0.02 * rng.standard_normal((20, 30)))
        q = 1e-6
        steps = drive(b, c, resolution=Resolution(q, q, False))
        errors = round_error_bounds(b, c, q, q)
        bounds = drive(b, c, resolution=ObservationBounds(errors))
        np.testing.assert_array_equal(bounds.lower, steps.lower)
        np.testing.assert_array_equal(bounds.upper, steps.upper)
        assert bounds.rule.tolist() == steps.rule.tolist()

    @pytest.mark.parametrize(
        ("errors", "coarse", "match"),
        [
            (np.zeros((1, 29)), False, "shape"),
            (np.full((1, 30), -1.0), False, "at least 0"),
            (np.full((1, 30), math.nan), False, "at least 0"),
            (np.zeros((1, 30), dtype=np.int64), False, "float"),
            (np.zeros((1, 30)), 1, "coarse"),
        ],
    )
    def test_refuses_bad_bounds(
        self, errors: FloatArray, coarse: Any, match: str
    ) -> None:
        d = alternating(0.0, 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        with pytest.raises(ValueError, match=match):
            drive(
                b,
                c,
                mode="cross_host",
                resolution=ObservationBounds(errors, coarse),
            )


def slow_wave(m: float, amplitude: float, n: int) -> FloatArray:
    """Log ratios ``m`` plus a slow sine: strongly autocorrelated."""
    return m + amplitude * np.sin(2 * np.pi * np.arange(n) / 40)


def single_look(
    d: FloatArray,
    *,
    procedure: Any = "t_log_ratio_batch_means",
    final: bool = False,
    first: Any = None,
    n0: int | None = None,
    keep_blocked: Any = False,
    resolution: Any = None,
) -> Any:
    """Decide one look on one row of log ratios ``d``."""
    b, c = arms(d[np.newaxis, :])
    return decide_look(
        b,
        c,
        direction="lower_is_better",
        confidence=look_confidence(0.05, 3),
        min_effect=MIN_EFFECT,
        calibrated=True,
        final=final,
        resolution=resolution,
        n0=n0,
        keep_blocked=keep_blocked,
        procedure=procedure,
        candidate_first=(
            (None if n0 is not None else np.zeros(b.shape, dtype=np.bool_))
            if first is None
            else first[np.newaxis, :]
        ),
    )


class TestOutlierShare:
    def test_by_hand(self) -> None:
        # Deviations from the median 0.1 are 0.01 for 8 rounds and
        # 1.0 for 2, so the MAD is 0.01 and the robust sd 0.014826:
        # the threshold is 6 sd = 0.0889, and the two large ones are
        # outliers.
        d = np.full(10, 0.1)
        d[::2] += 0.01
        d[1::2] -= 0.01
        d[3] = 1.1
        d[8] = -0.9
        first = np.zeros(10, dtype=np.bool_)
        assert float(outlier_share(d, first)) == pytest.approx(0.2)

    def test_removes_each_orders_median(self) -> None:
        # 20 rounds that differ only by the order effect (0.2 apart)
        # have no outliers when each order has its own median, and
        # would all be far from one common median.
        first = np.arange(20) % 2 == 0
        d = np.where(first, 0.3, 0.1) + 0.001 * np.arange(20) % 3
        assert float(outlier_share(d, first)) == 0.0
        assert float(outlier_share(d, np.zeros(20, dtype=np.bool_))) == 0.0
        spread = np.where(first, 0.3, 0.1) + 0.001 * (np.arange(20) % 3)
        one_order = outlier_share(spread, np.zeros(20, dtype=np.bool_))
        assert float(one_order) == 0.0

    def test_zero_scale_makes_any_deviation_an_outlier(self) -> None:
        d = np.zeros(11)
        d[4] = 0.5
        first = np.zeros(11, dtype=np.bool_)
        assert float(outlier_share(d, first)) == pytest.approx(1 / 11)
        assert float(outlier_share(np.zeros(11), first)) == 0.0

    def test_batch_axes_and_one_order_only(self) -> None:
        rng = np.random.default_rng(SEED)
        d = 0.05 * rng.standard_normal((3, 4, 40))
        d[1, 2, 7] += 3.0
        first = np.zeros(d.shape, dtype=np.bool_)
        share = outlier_share(d, first)
        assert share.shape == (3, 4)
        assert share[1, 2] > 0
        assert np.count_nonzero(share[share > 0]) >= 1
        alone = outlier_share(d[1, 2], first[1, 2])
        assert float(alone) == float(share[1, 2])

    @given(
        st.lists(
            st.floats(-5, 5, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=40,
        ),
        st.data(),
    )
    @SETTINGS
    def test_matches_a_loop(self, values: list[float], data: Any) -> None:
        n = len(values)
        flags = data.draw(st.lists(st.booleans(), min_size=n, max_size=n))
        d = np.array(values)
        first = np.array(flags)
        centred = d.copy()
        for group in (True, False):
            mask = first == group
            if mask.any():
                centred[mask] = d[mask] - np.median(d[mask])
        scale = 1.482602218505602 * float(np.median(np.abs(centred)))
        expected = float(np.mean(np.abs(centred) > 6.0 * scale))
        assert float(outlier_share(d, first)) == expected

    @pytest.mark.parametrize(
        ("d", "first", "match"),
        [
            (np.ones(5), np.ones(4, dtype=np.bool_), "shape"),
            (np.ones(5), np.ones(5), "boolean"),
            (np.ones(0), np.ones(0, dtype=np.bool_), "at least 1"),
            (np.array([1.0, math.nan]), np.zeros(2, dtype=np.bool_), "finite"),
        ],
    )
    def test_refuses_bad_input(
        self, d: FloatArray, first: Any, match: str
    ) -> None:
        with pytest.raises(ValueError, match=match):
            outlier_share(d, first)


class TestNoIntervalBelowTwoBlocks:
    @pytest.mark.parametrize("n", [10, 11])
    @pytest.mark.parametrize("final", [False, True])
    def test_fewer_than_two_blocks_continue_or_end(
        self, n: int, final: bool
    ) -> None:
        d = alternating(math.log(1.2), 0.01, n)
        look = single_look(d, final=final)
        assert n < 2 * BATCH_BLOCK
        assert np.isnan(look.lower).all()
        assert np.isnan(look.upper).all()
        assert look.rule.tolist() == [5 if final else 4]
        assert look.action.tolist() == [
            "INCONCLUSIVE" if final else "CONTINUE"
        ]
        assert look.withheld.tolist() == [""]
        assert look.regression.tolist() == [False]

    def test_two_blocks_have_an_interval(self) -> None:
        d = alternating(math.log(1.2), 0.01, 2 * BATCH_BLOCK)
        look = single_look(d)
        assert np.isfinite(look.lower).all()
        assert look.rule.tolist() == [2]

    def test_the_plain_t_interval_still_needs_only_two_rounds(self) -> None:
        d = alternating(math.log(1.2), 0.01, 10)
        look = single_look(d, procedure="t_log_ratio")
        assert np.isfinite(look.lower).all()

    def test_the_resolution_stop_still_applies(self) -> None:
        d = alternating(math.log(1.2), 0.01, 10)
        errors = np.zeros((1, 10))
        errors[0, 3] = math.inf
        look = single_look(d, resolution=ObservationBounds(errors))
        assert look.rule.tolist() == [1]
        assert look.action.tolist() == ["INCONCLUSIVE"]

    def test_cross_host_below_n0_never_decides(self) -> None:
        d = alternating(math.log(1.2), 0.01, 10)
        look = single_look(d, n0=10)
        assert look.rule.tolist() == [4]

    def test_default_procedure_is_batch_means(self) -> None:
        d = alternating(math.log(1.2), 0.01, 12)
        b, c = arms(d[np.newaxis, :])
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=look_confidence(0.05, 3),
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=False,
            resolution=None,
            candidate_first=np.zeros(np.shape(b), dtype=np.bool_),
        )
        found = t_interval(
            b,
            c,
            direction="lower_is_better",
            confidence=look_confidence(0.05, 3),
            block=BATCH_BLOCK,
        )
        assert look.lower.tolist() == found.lower.tolist()
        assert BATCH_BLOCK == 6

    def test_run_looks_runs_the_looks_without_an_interval(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        res = run_looks(
            b,
            c,
            direction="lower_is_better",
            schedule=SHORT,
            mode="one_host",
            min_effect=MIN_EFFECT,
            calibrated=True,
            resolution=None,
            exhausted=True,
            candidate_first=np.zeros(np.shape(b), dtype=np.bool_),
        )
        assert res.procedure == "t_log_ratio_batch_means"
        assert res.rounds[0].tolist() == [10, 20, 0]
        assert np.isnan(res.lower[0, 0])
        assert res.rule[0, 0] == 4
        assert res.outcome.tolist() == ["KEEP"]
        assert res.rounds_used.tolist() == [20]


class TestDiagnosticGate:
    def test_a_slow_wave_is_withheld_below_twenty_blocks(self) -> None:
        d = slow_wave(math.log(1.2), 0.01, 60)
        row = np.log(np.exp(d))
        assert float(lag1_autocorrelation(row, np.zeros(60, bool))) > 0.9
        plain = single_look(d, procedure="t_log_ratio")
        assert plain.rule.tolist() == [2]
        going = single_look(d)
        assert going.rule.tolist() == [4]
        assert going.action.tolist() == ["CONTINUE"]
        assert going.withheld.tolist() == ["autocorrelated_rounds"]
        assert going.regression.tolist() == [False]
        assert np.isfinite(going.lower).all()
        ending = single_look(d, final=True)
        assert ending.rule.tolist() == [5]
        assert ending.action.tolist() == ["INCONCLUSIVE"]
        assert ending.withheld.tolist() == ["autocorrelated_rounds"]

    def test_twenty_blocks_are_decided_whatever_the_autocorrelation(
        self,
    ) -> None:
        d = slow_wave(math.log(1.2), 0.01, 120)
        look = single_look(d)
        assert look.rule.tolist() == [2]
        assert look.action.tolist() == ["KEEP"]
        assert look.withheld.tolist() == [""]
        short = single_look(d[:114])
        assert short.withheld.tolist() == ["autocorrelated_rounds"]

    def test_a_withheld_reject_is_not_a_regression(self) -> None:
        d = slow_wave(math.log(0.8), 0.01, 60)
        plain = single_look(d, procedure="t_log_ratio")
        assert plain.rule.tolist() == [3]
        assert plain.regression.tolist() == [True]
        look = single_look(d)
        assert look.rule.tolist() == [4]
        assert look.regression.tolist() == [False]
        assert look.withheld.tolist() == ["autocorrelated_rounds"]

    def test_outliers_are_withheld_below_ten_blocks(self) -> None:
        d = alternating(math.log(3.0), 0.01, 30)
        d[5] += 1.0
        d[20] -= 1.0
        plain = single_look(d, procedure="t_log_ratio")
        assert plain.rule.tolist() == [2]
        look = single_look(d)
        assert look.rule.tolist() == [4]
        assert look.withheld.tolist() == ["outlier_rounds"]
        ending = single_look(d, final=True)
        assert ending.action.tolist() == ["INCONCLUSIVE"]
        assert ending.withheld.tolist() == ["outlier_rounds"]

    def test_ten_blocks_are_decided_with_outliers(self) -> None:
        d = alternating(math.log(3.0), 0.01, 60)
        d[5] += 1.0
        d[20] -= 1.0
        look = single_look(d)
        assert look.rule.tolist() == [2]
        assert look.withheld.tolist() == [""]
        short = single_look(d[:54])
        assert short.withheld.tolist() == ["outlier_rounds"]

    def test_the_share_must_exceed_three_percent(self) -> None:
        # One outlier in 34 rounds is 2.9%; in 33 it is 3.03%.
        for n, want in ((34, ""), (32, "outlier_rounds")):
            d = alternating(math.log(3.0), 0.01, n)
            d[7] += 1.0
            assert single_look(d).withheld.tolist() == [want], n

    def test_autocorrelation_is_named_first(self) -> None:
        # One outlier in 30 rounds (3.3%), 0.08 beyond 6 robust sd of
        # the wave, which still has a lag-1 autocorrelation above 0.1.
        d = slow_wave(math.log(3.0), 0.01, 30)
        d[5] += 0.08
        first = np.zeros(30, dtype=np.bool_)
        assert float(lag1_autocorrelation(d, first)) > 0.1
        assert float(outlier_share(d, first)) > 0.03
        look = single_look(d)
        assert look.withheld.tolist() == ["autocorrelated_rounds"]

    def test_only_rules_two_and_three_are_withheld(self) -> None:
        d = slow_wave(0.0, 0.2, 30)
        look = single_look(d)
        assert look.rule.tolist() == [4]
        assert look.withheld.tolist() == [""]
        final = single_look(d, final=True)
        assert final.rule.tolist() == [5]
        assert final.withheld.tolist() == [""]

    def test_the_plain_t_procedure_has_no_gate(self) -> None:
        d = slow_wave(math.log(1.2), 0.01, 30)
        look = single_look(d, procedure="t_log_ratio")
        assert look.withheld.tolist() == [""]
        assert look.rule.tolist() == [2]

    def test_a_withheld_keep_is_not_a_guardrail_hold(self) -> None:
        d = slow_wave(math.log(1.2), 0.01, 60)
        look = single_look(d, keep_blocked=np.array([True]))
        assert look.withheld.tolist() == ["autocorrelated_rounds"]
        assert look.held.tolist() == [False]

    def test_negative_autocorrelation_is_not_withheld(self) -> None:
        d = alternating(math.log(1.2), 0.01, 60)
        look = single_look(d)
        assert look.rule.tolist() == [2]
        assert look.withheld.tolist() == [""]

    def test_the_order_effect_is_not_read_as_autocorrelation(self) -> None:
        # Pairs of rounds in opposite orders, with the order effect
        # shifting d by +-0.03: removing each order's mean leaves the
        # alternating noise, so lag-1 is negative and nothing is
        # withheld; without the order flags the shift pairs up the
        # rounds and the same data read as positively autocorrelated.
        n = 60
        pair = np.arange(n) // 2
        flip = np.random.default_rng(SEED).integers(0, 2, n // 2)[pair]
        first = (np.arange(n) % 2 == 0) == (flip == 1)
        shift = np.where(first, 0.03, -0.03)
        d = math.log(1.2) + shift + alternating(0.0, 0.002, n)
        ordered = single_look(d, first=first)
        assert ordered.withheld.tolist() == [""]
        assert float(lag1_autocorrelation(d, first)) < 0.1
        unordered = lag1_autocorrelation(d, np.zeros(n, dtype=np.bool_))
        assert float(unordered) != float(lag1_autocorrelation(d, first))

    def test_a_row_is_judged_alone(self) -> None:
        wave = slow_wave(math.log(1.2), 0.01, 60)
        calm = alternating(math.log(1.2), 0.01, 60)
        b, c = arms(np.stack([wave, calm]))
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=look_confidence(0.05, 3),
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=False,
            resolution=None,
            candidate_first=np.zeros(np.shape(b), dtype=np.bool_),
        )
        assert look.withheld.tolist() == ["autocorrelated_rounds", ""]
        assert look.action.tolist() == ["CONTINUE", "KEEP"]

    def test_run_looks_lets_a_withheld_row_decide_later(self) -> None:
        # Row 0 is KEEP-worthy from the second look but withheld until
        # 20 blocks; row 1 has no gate and keeps at the same look.
        schedule = LookSchedule(n0=20, k=20, max_looks=6)
        wave = slow_wave(math.log(1.2), 0.01, 120)
        calm = alternating(math.log(1.2), 0.01, 120)
        b, c = arms(np.stack([wave, calm]))
        res = run_looks(
            b,
            c,
            direction="lower_is_better",
            schedule=schedule,
            mode="one_host",
            min_effect=MIN_EFFECT,
            calibrated=True,
            resolution=None,
            exhausted=True,
            candidate_first=np.zeros(np.shape(b), dtype=np.bool_),
        )
        assert res.outcome.tolist() == ["KEEP", "KEEP"]
        assert res.rounds_used.tolist() == [120, 20]
        assert res.withheld[0].tolist() == [
            "autocorrelated_rounds",
            "autocorrelated_rounds",
            "autocorrelated_rounds",
            "autocorrelated_rounds",
            "autocorrelated_rounds",
            "",
        ]
        assert res.withheld[1].tolist() == [""] * 6
        assert res.regression.sum() == 0

    def test_run_looks_ends_inconclusive_when_the_gate_holds_to_the_end(
        self,
    ) -> None:
        wave = slow_wave(math.log(1.2), 0.01, 60)
        b, c = arms(wave[np.newaxis, :])
        res = run_looks(
            b,
            c,
            direction="lower_is_better",
            schedule=SIX,
            mode="one_host",
            min_effect=MIN_EFFECT,
            calibrated=True,
            resolution=None,
            exhausted=True,
            candidate_first=np.zeros(np.shape(b), dtype=np.bool_),
        )
        assert res.outcome.tolist() == ["INCONCLUSIVE"]
        assert res.final_rule.tolist() == [5]
        assert res.withheld[0, 5] == "autocorrelated_rounds"

    @pytest.mark.parametrize(
        ("first", "match"),
        [
            (np.zeros((1, 59), dtype=np.bool_), "candidate_first"),
            (np.zeros((1, 60), dtype=np.int64), "candidate_first"),
        ],
    )
    def test_refuses_bad_order_flags(self, first: Any, match: str) -> None:
        d = slow_wave(math.log(1.2), 0.01, 60)
        b, c = arms(d[np.newaxis, :])
        with pytest.raises(ValueError, match=match):
            decide_look(
                b,
                c,
                direction="lower_is_better",
                confidence=look_confidence(0.05, 3),
                min_effect=MIN_EFFECT,
                calibrated=True,
                final=False,
                resolution=None,
                candidate_first=first,
            )


class TestIdenticalRatiosAreNotWithheld:
    @pytest.mark.parametrize("ratio", [1.1, 1.2, 1.7, 2.9])
    def test_exact_interval_is_decided_at_sixty_rounds(
        self, ratio: float
    ) -> None:
        b = np.full((1, 60), ratio)
        c = np.ones((1, 60))
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=look_confidence(0.05, 6),
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=True,
            resolution=None,
            candidate_first=np.zeros(np.shape(b), dtype=np.bool_),
        )
        assert look.withheld.tolist() == [""]
        assert look.rule.tolist() == [2]
        assert float(look.lower[0]) == ratio


def one_row_look(
    d: FloatArray,
    *,
    calibrated: bool,
    final: bool = False,
    resolution: Any = None,
    first: Any,
) -> Any:
    b, c = arms(d[np.newaxis, :])
    return decide_look(
        b,
        c,
        direction="lower_is_better",
        confidence=look_confidence(0.05, 3),
        min_effect=MIN_EFFECT,
        calibrated=calibrated,
        final=final,
        resolution=resolution,
        candidate_first=first,
    )


class TestCandidateFirstIsRequired:
    def order_effect(self) -> tuple[FloatArray, Any]:
        # Runs of three rounds with the candidate first, then three
        # without: an order effect of 0.02 on top of independent noise
        # reads as lag-1 autocorrelation of about 0.3 unless the order
        # means are removed first.
        n = 60
        flags = (np.arange(n) // 3) % 2 == 0
        noise = np.random.default_rng(1).normal(0.0, 0.004, n)
        return math.log(1.5) + 0.02 * flags + noise, flags[np.newaxis, :]

    def test_the_order_flags_remove_the_order_effect(self) -> None:
        d, flags = self.order_effect()
        ignored = one_row_look(d, calibrated=True, first=np.zeros_like(flags))
        assert ignored.withheld.tolist() == ["autocorrelated_rounds"]
        centred = one_row_look(d, calibrated=True, first=flags)
        assert centred.withheld.tolist() == [""]
        assert centred.rule.tolist() == [2]

    def test_decide_look_needs_it(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        with pytest.raises(TypeError, match="candidate_first"):
            decide_look(  # pyright: ignore[reportCallIssue]
                b,
                c,
                direction="lower_is_better",
                confidence=look_confidence(0.05, 3),
                min_effect=MIN_EFFECT,
                calibrated=True,
                final=False,
                resolution=None,
            )

    def test_run_looks_needs_it(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        with pytest.raises(TypeError, match="candidate_first"):
            run_looks(  # pyright: ignore[reportCallIssue]
                b,
                c,
                direction="lower_is_better",
                schedule=SHORT,
                mode="one_host",
                min_effect=MIN_EFFECT,
                calibrated=True,
                resolution=None,
                exhausted=True,
            )

    def test_none_is_refused_on_one_host(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)
        with pytest.raises(ValueError, match="required on one host"):
            one_row_look(d, calibrated=True, first=None)

    def test_none_is_cross_host_only(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=look_confidence(0.05, 3),
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=False,
            resolution=None,
            n0=10,
            candidate_first=None,
        )
        assert look.rule.tolist() == [2]


class TestUncalibratedLooksRecordTheGate:
    def test_the_reason_is_recorded_though_no_rule_could_decide(self) -> None:
        d = slow_wave(math.log(1.2), 0.01, 60)
        flags = np.zeros((1, 60), dtype=np.bool_)
        for final in (False, True):
            look = one_row_look(d, calibrated=False, final=final, first=flags)
            assert look.rule.tolist() == [5 if final else 4]
            assert look.withheld.tolist() == ["autocorrelated_rounds"]
            assert np.isfinite(look.lower).all()

    def test_an_interval_the_gate_does_not_touch_has_no_reason(self) -> None:
        d = alternating(math.log(1.2), 0.01, 60)
        flags = np.zeros((1, 60), dtype=np.bool_)
        look = one_row_look(d, calibrated=False, first=flags)
        assert look.rule.tolist() == [4]
        assert look.withheld.tolist() == [""]

    def test_a_straddling_interval_is_not_withheld(self) -> None:
        # Autocorrelated as in the first test, but the interval would
        # not have decided, so the gate has nothing to withhold.
        d = slow_wave(math.log(1.0), 0.3, 60)
        flags = np.zeros((1, 60), dtype=np.bool_)
        look = one_row_look(d, calibrated=False, first=flags)
        assert look.withheld.tolist() == [""]


class TestUnrepresentableBounds:
    """A bound that underflows to 0 or overflows is no decision."""

    @pytest.mark.parametrize("centre", [-575.0, 575.0])
    @pytest.mark.parametrize("final", [False, True])
    @pytest.mark.parametrize("widened", [False, True])
    @pytest.mark.parametrize("calibrated", [False, True])
    def test_gives_rule_4_or_5_and_no_interval(
        self, centre: float, final: bool, widened: bool, calibrated: bool
    ) -> None:
        # Log ratios near +-575 (ratios near 1e250) with a spread of
        # 40: the conservative lower bound underflows to 0.0 for the
        # small ratios, the upper bound overflows to inf for the large.
        d = centre + np.random.default_rng(SEED).normal(0.0, 40.0, 12)
        b, c = arms(d[np.newaxis, :])
        found = t_interval(
            b,
            c,
            direction="lower_is_better",
            confidence=0.99,
            block=BATCH_BLOCK,
        )
        assert float(found.lower[0]) == 0.0 or math.isinf(
            float(found.upper[0])
        )
        step = np.full((1, 12), 1e-300)
        resolution = Resolution(step, step, False) if widened else None
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=0.99,
            min_effect=MIN_EFFECT,
            calibrated=calibrated,
            final=final,
            resolution=resolution,
            candidate_first=np.zeros((1, 12), dtype=np.bool_),
        )
        assert look.rule.tolist() == [5 if final else 4]
        assert look.action.tolist() == [
            "INCONCLUSIVE" if final else "CONTINUE"
        ]
        assert np.isnan(look.lower).all()
        assert np.isnan(look.upper).all()
        assert look.regression.tolist() == [False]
        assert look.withheld.tolist() == [""]

    def test_other_rows_in_the_batch_still_decide(self) -> None:
        wild = -575.0 + np.random.default_rng(SEED).normal(0.0, 40.0, 12)
        calm = alternating(math.log(1.2), 0.01, 12)
        b, c = arms(np.stack([wild, calm]))
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=0.99,
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=False,
            resolution=None,
            candidate_first=np.zeros((2, 12), dtype=np.bool_),
        )
        assert look.rule.tolist() == [4, 2]
        assert np.isnan(look.lower[0])
        assert np.isfinite(look.lower[1])

    @pytest.mark.parametrize("final", [False, True])
    def test_a_bound_pushed_out_of_range_by_the_widening(
        self, final: bool
    ) -> None:
        # The interval is finite (about 1e-285); steps a hair under the
        # values widen it by exp(-72), below the smallest normal float.
        b = 1e-285 * (1 + 0.01 * np.random.default_rng(1).normal(size=(1, 12)))
        c = np.ones((1, 12))
        found = t_interval(
            b,
            c,
            direction="lower_is_better",
            confidence=0.99,
            block=BATCH_BLOCK,
        )
        assert 0.0 < float(found.lower[0]) < float(found.upper[0]) < 1.0
        scale = 1 - 8.8e-16
        look = decide_look(
            b,
            c,
            direction="lower_is_better",
            confidence=0.99,
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=final,
            resolution=Resolution(b * scale, c * scale, False),
            candidate_first=np.zeros((1, 12), dtype=np.bool_),
        )
        assert look.rule.tolist() == [5 if final else 4]
        assert np.isnan(look.lower).all()


class TestRatioOutsideTheFloatRange:
    @pytest.mark.parametrize("final", [False, True])
    @pytest.mark.parametrize(
        "direction", ["lower_is_better", "higher_is_better"]
    )
    def test_the_row_has_no_interval_and_the_others_decide(
        self, final: bool, direction: Any
    ) -> None:
        calm = alternating(math.log(1.2), 0.01, 12)
        b, c = arms(np.stack([calm, calm]))
        small, big = (1e-300, 1e300)
        b[0, 0], c[0, 0] = (
            (small, big) if direction == "lower_is_better" else (big, small)
        )
        look = decide_look(
            b,
            c,
            direction=direction,
            confidence=0.99,
            min_effect=MIN_EFFECT,
            calibrated=True,
            final=final,
            resolution=None,
            candidate_first=np.zeros((2, 12), dtype=np.bool_),
        )
        assert look.rule[0] == (5 if final else 4)
        assert np.isnan(look.lower[0])
        assert np.isfinite(look.lower[1])

    def test_run_looks_takes_none_only_cross_host(self) -> None:
        d = alternating(math.log(1.2), 0.01, 30)[np.newaxis, :]
        b, c = arms(d)
        kwargs: dict[str, Any] = {
            "direction": "lower_is_better",
            "schedule": SHORT,
            "min_effect": MIN_EFFECT,
            "calibrated": True,
            "resolution": None,
            "exhausted": True,
            "candidate_first": None,
        }
        with pytest.raises(ValueError, match="required on one host"):
            run_looks(b, c, mode="one_host", **kwargs)
        run_looks(b, c, mode="cross_host", **kwargs)
