"""The stub's cap and when it is withheld, from synthetic numbers.

No process runs and nothing is timed: the observations and the hook's
counters are written out as given, so each rule of `stub_result` is
exercised exactly.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from perfproof.bench.conditions import MeasureError, Observation
from perfproof.bench.stub import StubResult, stub_result
from perfproof.schema import (
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    LookSchedule,
    MetricSpec,
)

ROUNDS = 3
SPEC = BenchmarkSpec(
    name="bench",
    command=("python", "bench.py"),
    metric=MetricSpec(kind="reported", direction="lower_is_better", unit="s"),
    correctness=CorrectnessSpec(command=("true",), timeout_seconds=10.0),
    warmup=1,
    budget=Budget(max_rounds=20, max_seconds=600.0),
    looks=LookSchedule(n0=10, k=10, max_looks=3),
    timeout_seconds=60.0,
    harness=("bench.py",),
)
CAPTURE: tuple[int, int, int, tuple[str, ...], list[str]] = (1, 0, 0, (), [])


def build(
    tmp_path: Path,
    *,
    base: tuple[float, ...],
    stub: tuple[float, ...],
    overhead: float = 0.0,
    real: float = 0.0,
    install: float = 0.0,
    calls: int = 10,
    replayed: int | None = None,
    processes: int = 1,
    lost: int = 0,
    kind: str = "reported",
) -> StubResult:
    """Run `stub_result` on round times and per-execution counters."""
    observed: list[Observation] = []
    for r in range(ROUNDS):
        for key, value in (("baseline", base[r]), ("stub", stub[r])):
            observed.append(
                Observation(
                    key=key,
                    round=r,
                    values=(value, value),
                    wall_seconds=(value, value),
                    cpu_seconds=(value, value),
                    directory=tmp_path,
                )
            )
        directory = tmp_path / "replay" / f"round-{r}"
        directory.mkdir(parents=True)
        # One file for the round's two executions: totals are doubled.
        counters: dict[str, Any] = {
            "calls": 2 * calls,
            "replayed": 2 * (calls if replayed is None else replayed),
            "fallthrough": 2
            * (calls - (calls if replayed is None else replayed)),
            "fallthrough_seconds": 2 * real,
            "overhead_seconds": 2 * overhead,
            "install_seconds": 2 * install,
            "processes": 2 * processes,
            "processes_lost": 2 * lost,
        }
        (directory / "stats-1.json").write_text(json.dumps(counters))
    return stub_result(SPEC, "m:f", kind, ROUNDS, observed, tmp_path, CAPTURE)


def test_a_clear_remainder_gives_a_cap_and_its_upper_end_is_the_bound(
    tmp_path: Path,
) -> None:
    result = build(
        tmp_path,
        base=(1.0, 1.1, 0.9),
        stub=(0.2, 0.23, 0.17),
        overhead=0.01,
    )
    assert result.cap_withheld is None
    assert result.cap is not None
    assert result.cap.high is not None
    assert result.cap.low is not None
    # base / (stub - overhead), round by round.
    ratios = [1.0 / 0.19, 1.1 / 0.22, 0.9 / 0.16]
    assert result.cap.low < result.cap.value < result.cap.high
    assert min(ratios) < result.cap.value < max(ratios)
    assert result.cap_bound == result.cap.high
    assert result.cap_bound is not None
    assert result.cap_bound > result.cap.value
    assert result.cap_lower_limit is None
    assert result.remaining is not None
    assert result.removed_share is not None
    assert result.overhead_seconds_per_run == pytest.approx(0.01)
    assert result.notes == ()


def test_the_cap_is_baseline_over_the_remainder_exactly(
    tmp_path: Path,
) -> None:
    result = build(tmp_path, base=(1.0,) * 3, stub=(0.25,) * 3, overhead=0.05)
    assert result.cap is not None
    assert result.cap.value == pytest.approx(1.0 / 0.2)
    assert result.cap_bound == pytest.approx(1.0 / 0.2)
    assert result.cap_uncorrected.value == pytest.approx(4.0)


def test_real_calls_are_subtracted_but_are_not_hook_cost(
    tmp_path: Path,
) -> None:
    # 0.4 s of the replayed run was the function running for real: it is
    # taken out, and it does not count against the remainder.
    result = build(
        tmp_path,
        base=(1.0,) * 3,
        stub=(0.5,) * 3,
        overhead=0.001,
        real=0.4,
        replayed=5,
    )
    assert result.cap is not None
    assert result.cap.value == pytest.approx(1.0 / 0.099)
    assert result.real_per_run == pytest.approx(5)
    assert any("ran for real" in n for n in result.notes)


def test_a_remainder_not_below_the_baseline_withholds_the_cap(
    tmp_path: Path,
) -> None:
    # The replayed run took longer than the original: the function is
    # not where the time went, and 1 / 0.83 would be a cap below 1.
    result = build(tmp_path, base=(1.0,) * 3, stub=(1.2,) * 3, overhead=0.001)
    assert result.cap is None
    assert result.cap_bound is None
    assert result.cap_withheld == "uninformative"
    assert result.remaining is None
    assert result.removed_share is None
    assert result.cap_lower_limit is None
    assert any("No cap can be given" in n for n in result.notes)


def test_a_hook_cost_above_the_remainder_withholds_the_cap(
    tmp_path: Path,
) -> None:
    # 0.02 s left after taking out 0.03 s of hook cost.
    result = build(tmp_path, base=(1.0,) * 3, stub=(0.05,) * 3, overhead=0.03)
    assert result.cap is None
    assert result.cap_withheld == "uninformative"
    assert result.cap_uncorrected.value == pytest.approx(20.0)
    assert result.cap_lower_limit == pytest.approx(20.0)
    assert any("A lower limit on the cap is 20" in n for n in result.notes)


def test_a_hook_cost_just_below_the_remainder_keeps_the_cap(
    tmp_path: Path,
) -> None:
    result = build(tmp_path, base=(1.0,) * 3, stub=(0.05,) * 3, overhead=0.02)
    assert result.cap is not None
    assert result.cap.value == pytest.approx(1.0 / 0.03)


def test_a_remainder_not_above_zero_withholds_the_cap(tmp_path: Path) -> None:
    result = build(tmp_path, base=(1.0,) * 3, stub=(0.05,) * 3, overhead=0.06)
    assert result.cap is None
    assert result.cap_withheld == "uninformative"
    assert result.remaining is None


def test_one_round_not_above_zero_withholds_the_cap(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        base=(1.0,) * 3,
        stub=(0.3, 0.3, 0.05),
        overhead=0.06,
    )
    assert result.cap is None
    assert result.cap_withheld == "uninformative"


def test_install_time_is_hook_cost_for_wall_and_cpu_but_not_reported(
    tmp_path: Path,
) -> None:
    def run(where: str, kind: str) -> StubResult:
        return build(
            tmp_path / where,
            base=(1.0,) * 3,
            stub=(0.1,) * 3,
            overhead=0.001,
            install=0.08,
            kind=kind,
        )

    reported = run("a", "reported")
    assert reported.cap is not None
    assert reported.cap.value == pytest.approx(1.0 / 0.099)
    for kind in ("wall", "cpu"):
        timed = run(kind, kind)
        # 0.1 - 0.001 - 0.08 = 0.019 left, below the 0.081 hook cost.
        assert timed.cap is None, kind
        assert timed.cap_withheld == "uninformative"


def test_a_lower_limit_at_or_below_one_is_not_offered(tmp_path: Path) -> None:
    result = build(tmp_path, base=(1.0,) * 3, stub=(2.0,) * 3, overhead=0.001)
    assert result.cap is None
    assert result.cap_uncorrected.value == pytest.approx(0.5)
    assert result.cap_lower_limit is None
    assert not any("lower limit" in n for n in result.notes)


def test_uncounted_forked_processes_withhold_the_cap(tmp_path: Path) -> None:
    result = build(
        tmp_path, base=(1.0,) * 3, stub=(0.2,) * 3, overhead=0.01, lost=1
    )
    assert result.cap is None
    assert result.cap_withheld == "uninformative"
    assert any("could not be counted" in n for n in result.notes)


def test_processes_are_counted_and_noted(tmp_path: Path) -> None:
    result = build(
        tmp_path,
        base=(1.0,) * 3,
        stub=(0.2,) * 3,
        overhead=0.01,
        processes=4,
    )
    assert result.processes_per_run == pytest.approx(4)
    assert any("4 processes per run" in n for n in result.notes)
    assert result.cap is not None


def test_a_round_without_counters_is_refused(tmp_path: Path) -> None:
    observed = [
        Observation(k, r, (1.0, 1.0), (1.0, 1.0), (1.0, 1.0), tmp_path)
        for r in range(ROUNDS)
        for k in ("baseline", "stub")
    ]
    (tmp_path / "replay" / "round-0").mkdir(parents=True)
    with pytest.raises(MeasureError) as info:
        stub_result(
            SPEC, "m:f", "reported", ROUNDS, observed, tmp_path, CAPTURE
        )
    assert info.value.code == "hook_not_loaded"
