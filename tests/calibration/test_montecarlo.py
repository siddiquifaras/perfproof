import math

import montecarlo as mc
import numpy as np
import pytest
from scipy import stats  # pyright: ignore[reportMissingTypeStubs]

from perfproof.schema import Procedure
from perfproof.stats.intervals import t_interval
from perfproof.stats.sequential import look_confidence, plan_looks, run_looks
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
    # 11 scenarios one host, 9 cross host (no drift, no order effect).
    assert len(cells) == len(mc.SCHEDULES) * (11 + 9) * len(mc.SPEEDUPS)
    cross = mc.scenarios("cross_host")
    assert "drift" not in cross
    assert "order_effect" not in cross
    assert all(s.session_offset_cv == 0.15 for s in cross.values())
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
@pytest.mark.parametrize(
    ("name", "block", "procedure"),
    [("t", 1, "t_log_ratio"), ("batch_means_5", 5, "t_log_ratio_batch_means")],
)
def test_drive_looks_decides_exactly_as_run_looks(
    mode: mc.HostMode, name: str, block: int, procedure: Procedure
) -> None:
    # The fallback with another block size goes through drive_looks; on
    # the procedures run_looks can also run, every outcome and length
    # must agree.
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
        procedure=procedure,
    )

    def bounds(
        rows: mc.NDArray[np.intp], n: int
    ) -> tuple[mc.FloatArray, mc.FloatArray]:
        if block == 1:
            found = t_interval(
                b[rows, :n],
                c[rows, :n],
                direction="lower_is_better",
                confidence=conf,
            )
            return found.lower, found.upper
        return mc.batch_means_bounds(b[rows, :n], c[rows, :n], block, conf)

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
    alone = mc.run_cell(cell, procedures=["t"], seed=5, replications=120)
    procs = first["procedures"]
    assert isinstance(procs, dict)
    t_alone = alone["procedures"]
    assert isinstance(t_alone, dict)
    assert t_alone["t"] == procs["t"]
    reseeded = mc.run_cell(cell, procedures=["t"], seed=6, replications=120)
    assert reseeded["procedures"] != t_alone


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
