import math

import montecarlo as mc
import numpy as np
import pytest
from exposed import EXPOSED
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.stats import sequential
from perfproof.stats.decision import interval_rules
from perfproof.stats.intervals import t_interval
from perfproof.stats.sequential import (
    decide_look,
    look_confidence,
    plan_looks,
    run_looks,
)
from perfproof.stats.simulate import simulate


def test_clopper_pearson_bound_passes_83_and_fails_84_of_4000() -> None:
    # docs/method.md, "Acceptance rule": at R = 4000 the largest
    # passing count is 83, checked both ways.
    assert mc.passes(83, 4000)
    assert not mc.passes(84, 4000)
    assert stats.binom.cdf(83, 4000, 0.025) <= 0.05  # pyright: ignore[reportUnknownMemberType]
    assert stats.binom.cdf(84, 4000, 0.025) > 0.05  # pyright: ignore[reportUnknownMemberType]
    assert mc.clopper_pearson_upper(4000, 4000) == 1.0
    assert 0 < mc.clopper_pearson_upper(0, 4000) < 0.001


def test_cells_cover_every_scenario_mode_schedule_and_speedup() -> None:
    cells = mc.all_cells()
    names = [c.name for c in cells]
    assert len(set(names)) == len(names)
    # 11 development scenarios one host, 9 cross host (no drift, no
    # order effect), and every exposed scenario in both.
    expected = (11 + 9 + 2 * len(EXPOSED)) * len(mc.SCHEDULES)
    assert len(cells) == expected * len(mc.SPEEDUPS)
    cross = mc.scenarios("cross_host")
    assert "drift" not in cross
    assert "order_effect" not in cross
    assert set(EXPOSED) <= set(cross)
    assert all(s.drift == 0 and s.order_effect == 0 for s in cross.values())
    assert all(s.session_offset_cv == 0.15 for s in cross.values())
    one = mc.scenarios("one_host")
    assert all(one[name] == EXPOSED[name] for name in EXPOSED)
    for cell in cells:
        assert mc.cell_by_name(cell.name) == cell
        expected = "KEEP" if cell.speedup in (0.95, 1.00, 1.03) else "REJECT"
        assert cell.error == expected


@pytest.mark.parametrize("schedule", mc.SCHEDULES, ids=lambda s: s.name)
def test_schedules_match_the_method_defaults(
    schedule: mc.TargetSchedule,
) -> None:
    looks = schedule.looks
    assert schedule.max_rounds == looks.n0 + looks.k * (looks.max_looks - 1)
    assert looks.n0 % 2 == 0
    assert looks.k % 2 == 0


def test_time_budget_matches_the_runner_rule_in_linear_space() -> None:
    sched = mc.SCHEDULE_BY_NAME["python_native"]
    g = np.random.default_rng(3)
    log_times = g.normal(0.0, 0.3, size=(500, sched.max_rounds, 2))
    got = mc.collected_rounds(log_times, sched)
    t0 = sched.max_seconds / (2 * (sched.max_rounds + 1))
    rounds = t0 * np.exp(log_times).sum(axis=-1)
    for row, n in zip(rounds, got.tolist(), strict=True):
        elapsed = np.cumsum(row)
        longest = np.maximum.accumulate(row)
        over = elapsed + longest > sched.max_seconds
        expected = int(np.argmax(over)) + 1 if over.any() else 60
        assert n == expected
    assert 0 < np.count_nonzero(got < sched.max_rounds) < 500


def test_time_budget_survives_runs_beyond_the_float_range() -> None:
    sched = mc.SCHEDULE_BY_NAME["web_agentic"]
    log_times = np.zeros((2, sched.max_rounds, 2))
    log_times[0, 4, 1] = 5000.0
    got = mc.collected_rounds(log_times, sched)
    assert got.tolist() == [5, sched.max_rounds]


def test_typical_rounds_spend_the_round_budget_not_the_time_budget() -> None:
    sched = mc.SCHEDULE_BY_NAME["gpu_kernels"]
    got = mc.collected_rounds(np.zeros((1, sched.max_rounds, 2)), sched)
    assert got.tolist() == [sched.max_rounds]


@pytest.mark.parametrize("mode", mc.MODES)
def test_drive_looks_decides_exactly_as_run_looks(
    mode: mc.HostMode,
) -> None:
    # The plain t interval has no gate, so the harness loop and the
    # production driver must agree on every outcome and length.
    sched = mc.SCHEDULE_BY_NAME["llm_serving"]
    scenario = mc.scenarios(mode)["outliers"]
    rng = np.random.default_rng(11)
    drawn = simulate(
        scenario,
        speedup=1.04,
        n=sched.max_rounds,
        replications=300,
        mode=mode,
        rng=rng,
    )
    rounds = mc.collected_rounds(drawn.log_times, sched)
    values = np.exp(drawn.log_times)
    b = np.ascontiguousarray(values[..., 0])
    c = np.ascontiguousarray(values[..., 1])
    exhausted = np.ones(300, dtype=np.bool_)
    conf = look_confidence(0.05, sched.looks.max_looks)
    expected = run_looks(
        b,
        c,
        direction="lower_is_better",
        schedule=sched.looks,
        mode=mode,
        min_effect=mc.MIN_EFFECT,
        calibrated=True,
        resolution=None,
        exhausted=exhausted,
        rounds=rounds,
        procedure="t_log_ratio",
    )

    def bounds(
        rows: mc.NDArray[np.intp], n: int
    ) -> tuple[mc.FloatArray, mc.FloatArray]:
        found = t_interval(
            b[rows, :n],
            c[rows, :n],
            direction="lower_is_better",
            confidence=conf,
        )
        return found.lower, found.upper

    plan = plan_looks(sched.looks, rounds, exhausted, mode=mode)
    n0 = sched.looks.n0 if mode == "cross_host" else None
    got = mc.drive_looks(plan, bounds, n0=n0)
    np.testing.assert_array_equal(got.outcome, expected.outcome)
    np.testing.assert_array_equal(got.rounds, expected.rounds_used)
    assert len(set(got.outcome.tolist())) >= 2


def test_undefined_interval_continues_then_is_inconclusive() -> None:
    sched = mc.SCHEDULE_BY_NAME["python_native"]
    rounds = np.array([60, 38], dtype=np.int64)
    exhausted = np.ones(2, dtype=np.bool_)
    plan = plan_looks(sched.looks, rounds, exhausted, mode="one_host")

    def never(
        rows: mc.NDArray[np.intp], n: int
    ) -> tuple[mc.FloatArray, mc.FloatArray]:
        del n
        return np.full(rows.size, np.nan), np.full(rows.size, np.nan)

    got = mc.drive_looks(plan, never, n0=None)
    assert got.outcome.tolist() == ["INCONCLUSIVE", "INCONCLUSIVE"]
    assert got.rounds.tolist() == [60, 38]


def test_batch_means_bounds_need_two_whole_blocks() -> None:
    b = np.ones((3, 19))
    c = np.full((3, 19), 0.5)
    lo, hi = mc.batch_means_bounds(b, c, 10, 0.99)
    assert np.all(np.isnan(lo))
    assert np.all(np.isnan(hi))
    lo, hi = mc.batch_means_bounds(b[:, :18], c[:, :18], 6, 0.99)
    assert lo.tolist() == [2.0, 2.0, 2.0]
    assert hi.tolist() == [2.0, 2.0, 2.0]


def test_a_cell_is_reproducible_whatever_else_runs_with_it() -> None:
    cell = mc.cell_by_name("web_agentic/bimodal/one_host/1.04")
    first = mc.run_cell(cell, seed=5, replications=120)
    again = mc.run_cell(cell, seed=5, replications=120)
    for result in (first, again):
        del result["wall_seconds"]
    assert first == again
    only = [mc.PRIMARY]
    alone = mc.run_cell(cell, procedures=only, seed=5, replications=120)
    procs = first["procedures"]
    assert isinstance(procs, dict)
    primary_alone = alone["procedures"]
    assert isinstance(primary_alone, dict)
    assert primary_alone[mc.PRIMARY] == procs[mc.PRIMARY]
    reseeded = mc.run_cell(cell, procedures=only, seed=6, replications=120)
    assert reseeded["procedures"] != primary_alone


def test_the_harness_block_and_gate_are_the_methods() -> None:
    assert mc.BATCH_BLOCK == sequential.BATCH_BLOCK == 6
    assert mc.PRIMARY == "batch_means_6"
    assert sequential.LAG1_LIMIT == 0.1
    assert sequential.LAG1_MIN_BLOCKS == 20
    assert sequential.OUTLIER_Z == 6.0
    assert sequential.OUTLIER_SHARE == 0.03
    assert sequential.OUTLIER_MIN_BLOCKS == 10


def gated_draw(
    name: str, mode: mc.HostMode, sched: mc.TargetSchedule, speedup: float
) -> tuple[
    mc.FloatArray,
    mc.FloatArray,
    mc.NDArray[np.bool_],
    mc.NDArray[np.int64],
]:
    scenario = mc.scenarios(mode)[name]
    drawn = simulate(
        scenario,
        speedup=speedup,
        n=sched.max_rounds,
        replications=400,
        mode=mode,
        rng=np.random.default_rng(23),
    )
    values = np.exp(drawn.log_times)
    b = np.ascontiguousarray(values[..., 0])
    c = np.ascontiguousarray(values[..., 1])
    rounds = mc.collected_rounds(drawn.log_times, sched)
    return b, c, drawn.candidate_first, rounds


GATED_CASES = [
    ("RHO0.7", "gpu_kernels", 1.03),
    ("RHO0.6", "python_native", 1.04),
    ("A00-outliers-only", "llm_serving", 1.03),
    ("outliers", "web_agentic", 1.10),
    ("lognormal_cv5", "python_native", 1.10),
    ("A20261052-04", "gpu_kernels", 1.05),
]


@pytest.mark.parametrize("mode", mc.MODES)
@pytest.mark.parametrize(("name", "schedule", "speedup"), GATED_CASES)
def test_the_primary_is_the_harness_with_its_own_gate(
    mode: mc.HostMode, name: str, schedule: str, speedup: float
) -> None:
    # Production run_looks (default procedure, with the order flags)
    # and the harness loop with the harness's own diagnostics, on the
    # same rounds: every outcome and length must agree, in cells where
    # the gate acts.
    sched = mc.SCHEDULE_BY_NAME[schedule]
    b, c, first, rounds = gated_draw(name, mode, sched, speedup)
    exhausted = np.ones(b.shape[0], dtype=np.bool_)
    conf = look_confidence(0.05, sched.looks.max_looks)
    expected = run_looks(
        b,
        c,
        direction="lower_is_better",
        schedule=sched.looks,
        mode=mode,
        min_effect=mc.MIN_EFFECT,
        calibrated=True,
        resolution=None,
        exhausted=exhausted,
        rounds=rounds,
        candidate_first=first,
    )

    def bounds(
        rows: mc.NDArray[np.intp], n: int
    ) -> tuple[mc.FloatArray, mc.FloatArray]:
        return mc.batch_means_bounds(
            b[rows, :n], c[rows, :n], mc.BATCH_BLOCK, conf
        )

    def withhold(rows: mc.NDArray[np.intp], n: int) -> mc.NDArray[np.bool_]:
        return mc.harness_withheld(
            np.log(b[rows, :n] / c[rows, :n]), first[rows, :n]
        )

    plan = plan_looks(sched.looks, rounds, exhausted, mode=mode)
    n0 = sched.looks.n0 if mode == "cross_host" else None
    got = mc.drive_looks(plan, bounds, n0=n0, withhold=withhold)
    np.testing.assert_array_equal(got.outcome, expected.outcome)
    np.testing.assert_array_equal(got.rounds, expected.rounds_used)


@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize("mode", mc.MODES)
def test_decide_look_at_block_six_equals_the_harness(
    mode: mc.HostMode, final: bool
) -> None:
    # One look at a time over many lengths and rows from the scenarios
    # the gate acts on: production decide_look against the harness's
    # bounds, interval_rules and gate, bound for bound and outcome for
    # outcome.
    sched = mc.SCHEDULE_BY_NAME["gpu_kernels"]
    parts = [
        gated_draw(name, mode, sched, speedup)
        for name, speedup in (
            ("RHO0.7", 1.10),
            ("lognormal_cv5", 1.10),
            ("A00-outliers-only", 1.20),
        )
    ]
    b = np.concatenate([p[0] for p in parts])
    c = np.concatenate([p[1] for p in parts])
    first = np.concatenate([p[2] for p in parts])
    conf = look_confidence(0.05, sched.looks.max_looks)
    n0 = 10 if mode == "cross_host" else None
    withheld_seen = decided_seen = 0
    for n in (10, 11, 12, 16, 22, 30, 41, 60, 90, 119, 120):
        look = decide_look(
            b[:, :n],
            c[:, :n],
            direction="lower_is_better",
            confidence=conf,
            min_effect=mc.MIN_EFFECT,
            calibrated=True,
            final=final,
            resolution=None,
            n0=n0,
            candidate_first=first[:, :n],
        )
        lo, hi = mc.batch_means_bounds(
            b[:, :n], c[:, :n], mc.BATCH_BLOCK, conf
        )
        defined = np.isfinite(lo)
        np.testing.assert_array_equal(look.lower[defined], lo[defined])
        np.testing.assert_array_equal(look.upper[defined], hi[defined])
        assert np.isnan(look.lower[~defined]).all()
        rule = np.full(b.shape[0], 5 if final else 4, dtype=np.int8)
        if defined.any():
            decided = interval_rules(
                lo[defined],
                hi[defined],
                min_effect=mc.MIN_EFFECT,
                budget_remains=not final,
                calibrated=True,
                session_pairs=(
                    None if n0 is None else np.full(int(defined.sum()), n)
                ),
                n0=n0,
            )
            rule[defined] = decided.rule
        wild = mc.harness_withheld(np.log(b[:, :n] / c[:, :n]), first[:, :n])
        held = wild & ((rule == 2) | (rule == 3))
        rule = np.where(held, 5 if final else 4, rule).astype(np.int8)
        np.testing.assert_array_equal(look.rule, rule)
        np.testing.assert_array_equal(look.withheld != "", held)
        withheld_seen += int(held.sum())
        decided_seen += int(((rule == 2) | (rule == 3)).sum())
    assert withheld_seen > 0
    assert decided_seen > 0


def test_seed_comes_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(mc.SEED_ENV, raising=False)
    assert mc.calibration_seed() == mc.DEFAULT_SEED
    monkeypatch.setenv(mc.SEED_ENV, "123")
    assert mc.calibration_seed() == 123
    monkeypatch.setenv(mc.SEED_ENV, "-1")
    with pytest.raises(ValueError, match="non-negative"):
        mc.calibration_seed()


def test_bar_is_the_float_the_decision_uses() -> None:
    assert mc.BAR == 1.0 + 0.03
    assert mc.BAR == 1.03
    assert math.isclose(mc.LIMIT, 0.025)
