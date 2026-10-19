import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.bench.conditions import MeasureError
from perfproof.bench.stub import (
    HOOK_SOURCE,
    hook_env,
    stub_function,
    write_config,
    write_hook,
)
from perfproof.schema import (
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    LookSchedule,
    MetricSpec,
)

HEAVY = """\
import os
import time

MARK = os.environ["MARK"]


def work(n):
    with open(MARK, "a") as f:
        f.write("x")
    return n * n


def boom(n):
    raise ValueError(f"bad {n}")


def grow(items, n):
    items.append(n)
    return len(items)


def apply(fn):
    return fn() + 1


def never(n):
    return n


def slow(n):
    time.sleep(0.02)
    return n


def gen(n):
    yield n


def cheap(n):
    return n


def count(items):
    return len(items)


class Box:
    def double(self, n):
        return 2 * n

    @staticmethod
    def triple(n):
        return 3 * n

    @classmethod
    def quad(cls, n):
        return 4 * n
"""
PROGRAM = """\
import json
import os

import heavy

out = []
for n in [int(x) for x in os.environ["NS"].split(",")]:
    out.append(heavy.work(n))
try:
    heavy.boom(5)
except ValueError as exc:
    out.append(str(exc))
items = []
out.append(heavy.grow(items, 1))
out.append(heavy.grow(items, 1))
out.append(heavy.apply(lambda: 41))
box = heavy.Box()
out.append(box.double(4))
out.append(heavy.Box.triple(5))
out.append(heavy.Box.quad(6))
out.extend(heavy.gen(7))
print(json.dumps(out))
"""
BENCH = """\
import json
import random
import time

import heavy

start = time.perf_counter()
for n in range(5):
    heavy.slow(n % 3)
time.sleep(0.01)
elapsed = time.perf_counter() - start
print("PERFPROOF", json.dumps({"value": elapsed}))
"""
BENCH_RANDOM = BENCH.replace(
    "heavy.slow(n % 3)", "heavy.slow(random.random())"
)
BENCH_BIG = """\
import json
import time

import heavy

payload = list(range(1_000_000))
start = time.perf_counter()
heavy.count(payload)
elapsed = time.perf_counter() - start
print("PERFPROOF", json.dumps({"value": elapsed}))
"""
BENCH_HOT = """\
import json
import time

import heavy

start = time.perf_counter()
for i in range(50_000):
    heavy.cheap(i % 7)
time.sleep(0.005)
elapsed = time.perf_counter() - start
print("PERFPROOF", json.dumps({"value": elapsed}))
"""
BENCH_FORK = """\
import json
import multiprocessing
import time

import heavy

ctx = multiprocessing.get_context("fork")
start = time.perf_counter()
with ctx.Pool(2) as pool:
    pool.map(heavy.slow, [0, 1, 2, 0, 1, 2], chunksize=1)
elapsed = time.perf_counter() - start
print("PERFPROOF", json.dumps({"value": elapsed}))
"""
BENCH_BROKEN = "raise SystemExit(3)\n"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "heavy.py").write_text(HEAVY)
    (root / "prog.py").write_text(PROGRAM)
    (root / "bench.py").write_text(BENCH)
    return root


def make_spec(
    root: Path,
    *,
    command: tuple[str, ...] | None = None,
    metric: MetricSpec | None = None,
    env: dict[str, str] | None = None,
    **extra: Any,
) -> BenchmarkSpec:
    return BenchmarkSpec(
        name="bench",
        command=command or (sys.executable, "bench.py"),
        env={"MARK": str(root / "mark.txt"), **(env or {})},
        metric=metric
        or MetricSpec(kind="reported", direction="lower_is_better", unit="s"),
        correctness=CorrectnessSpec(command=("true",), timeout_seconds=10.0),
        warmup=1,
        budget=Budget(max_rounds=20, max_seconds=600.0),
        looks=LookSchedule(n0=10, k=10, max_looks=3),
        timeout_seconds=60.0,
        harness=("bench.py",),
        **extra,
    )


class Run:
    """One run of prog.py under the hook."""

    def __init__(self, root: Path, work: Path) -> None:
        self.root = root
        self.work = work
        self.hook = write_hook(work)
        self.calls = work / "calls"
        self.calls.mkdir()
        self.runs = 0

    def go(
        self,
        mode: str,
        target: str,
        numbers: str = "1,2,3,2",
        untimed_ns: int | None = None,
    ) -> tuple[list[object], list[dict[str, Any]], int]:
        module, _, qualname = target.partition(":")
        self.runs += 1
        config = self.work / f"{mode}-{self.runs}.json"
        stats = self.work / f"stats-{mode}-{self.runs}"
        write_config(
            config,
            mode=mode,
            module=module,
            qualname=qualname,
            calls=self.calls,
            stats=stats,
            clock="wall",
            untimed_ns=untimed_ns,
        )
        mark = self.work / f"mark-{mode}.txt"
        spec = make_spec(self.root, env={"MARK": str(mark)})
        env = {
            **os.environ,
            **hook_env(spec, self.hook, config),
            "MARK": str(mark),
            "NS": numbers,
        }
        done = subprocess.run(
            [sys.executable, "prog.py"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        output = cast("list[object]", json.loads(done.stdout))
        found = [
            cast("dict[str, Any]", json.loads(p.read_text()))
            for p in sorted(stats.glob("stats-*.json"))
        ]
        written = mark.read_text() if mark.exists() else ""
        return output, found, len(written)


@pytest.fixture
def run(project: Path, tmp_path: Path) -> Run:
    work = tmp_path / "work"
    work.mkdir()
    return Run(project, work)


def total(stats: list[dict[str, Any]], field: str) -> float:
    return sum(float(s[field]) for s in stats)


# The hook, in a real interpreter


def test_replay_returns_recorded_results_without_running_the_function(
    run: Run,
) -> None:
    recorded, rec_stats, rec_marks = run.go("record", "heavy:work")
    replayed, rep_stats, rep_marks = run.go("replay", "heavy:work")
    assert replayed == recorded
    assert rec_marks == 4  # four calls ran for real while recording
    assert rep_marks == 0  # none ran in the replay
    assert total(rec_stats, "calls") == 4
    assert total(rep_stats, "calls") == 4
    assert total(rep_stats, "replayed") == 4
    assert total(rep_stats, "fallthrough") == 0
    assert rep_stats[0]["status"] == "wrapped"


def test_a_side_effect_is_noted_in_the_capture_and_skipped_in_replay(
    run: Run,
) -> None:
    _, stats, _ = run.go("record", "heavy:work")
    assert total(stats, "effect_calls") >= 1
    assert "file written" in stats[0]["effects"]


def test_identical_calls_are_stored_once(run: Run) -> None:
    run.go("record", "heavy:work", numbers="2,2,2,2,3")
    events = [
        line
        for p in run.calls.glob("events-*.jsonl")
        for line in p.read_text().splitlines()
    ]
    assert len(events) == 2


def test_a_recorded_exception_is_raised_again(run: Run) -> None:
    recorded, _, _ = run.go("record", "heavy:boom")
    replayed, stats, _ = run.go("replay", "heavy:boom")
    assert replayed == recorded
    assert "bad 5" in replayed
    assert total(stats, "replayed") == 1


@pytest.mark.parametrize(
    "target", ["heavy:Box.double", "heavy:Box.triple", "heavy:Box.quad"]
)
def test_methods_static_and_class_methods_replay(
    run: Run, target: str
) -> None:
    recorded, _, _ = run.go("record", target)
    replayed, stats, _ = run.go("replay", target)
    assert replayed == recorded
    assert total(stats, "replayed") == 1
    assert total(stats, "fallthrough") == 0


def test_a_call_that_changes_its_arguments_runs_for_real_in_replay(
    run: Run,
) -> None:
    recorded, rec_stats, _ = run.go("record", "heavy:grow")
    replayed, stats, _ = run.go("replay", "heavy:grow")
    assert replayed == recorded
    assert total(rec_stats, "mutating_calls") == 2
    assert total(stats, "replayed") == 0
    assert total(stats, "fallthrough") == 2
    assert total(stats, "fallthrough_seconds") >= 0.0


def test_unpicklable_arguments_run_for_real_in_replay(run: Run) -> None:
    recorded, rec_stats, _ = run.go("record", "heavy:apply")
    replayed, stats, _ = run.go("replay", "heavy:apply")
    assert replayed == recorded
    assert total(rec_stats, "unpicklable_args") == 1
    assert total(stats, "fallthrough") == 1


def test_a_call_not_in_the_capture_runs_for_real_and_is_counted(
    run: Run,
) -> None:
    run.go("record", "heavy:work", numbers="1,2")
    replayed, stats, marks = run.go("replay", "heavy:work", numbers="1,2,9")
    assert replayed[:3] == [1, 4, 81]
    assert total(stats, "replayed") == 2
    assert total(stats, "fallthrough") == 1
    assert marks == 1


def test_the_per_call_untimed_cost_is_added_to_the_hook_cost(
    run: Run,
) -> None:
    run.go("record", "heavy:work")
    _, free, _ = run.go("replay", "heavy:work", untimed_ns=0)
    _, told, _ = run.go("replay", "heavy:work", untimed_ns=2_000_000)
    assert told[0]["untimed_ns"] == 2_000_000
    assert total(told, "calls") == total(free, "calls") == 4
    # Four calls at 2 ms each: the cost is hook time plus calls times
    # it, whatever the hook timed itself. Without the term it is
    # microseconds.
    assert total(free, "overhead_seconds") < 0.002
    assert total(told, "overhead_seconds") >= 4 * 0.002
    assert total(told, "overhead_seconds") < 4 * 0.002 + 0.05


def test_the_calibrated_per_call_cost_is_not_negligible(run: Run) -> None:
    # Entry, argument packing, the depth check and the counter updates
    # take far more than a few hundred nanoseconds in CPython.
    run.go("record", "heavy:work")
    _, stats, _ = run.go("replay", "heavy:work")
    assert stats[0]["untimed_ns"] > 300
    assert total(stats, "overhead_seconds") >= (
        4 * stats[0]["untimed_ns"] / 1e9
    )


def test_the_hooks_own_cost_is_measured(run: Run) -> None:
    run.go("record", "heavy:work")
    _, stats, _ = run.go("replay", "heavy:work")
    assert total(stats, "overhead_seconds") > 0.0
    assert stats[0]["untimed_ns"] > 0
    assert total(stats, "install_seconds") > 0.0


@pytest.mark.parametrize(
    ("target", "status"),
    [
        ("heavy:never", "wrapped"),
        ("absent:work", "module never imported"),
        ("heavy:missing", "not wrapped"),
        ("heavy:gen", "generator"),
    ],
)
def test_a_target_the_hook_cannot_wrap_says_why(
    run: Run, target: str, status: str
) -> None:
    _, stats, _ = run.go("record", target)
    assert status in stats[0]["status"]


def test_the_hook_loads_nothing_without_a_configuration(
    project: Path, tmp_path: Path
) -> None:
    work = tmp_path / "bare"
    work.mkdir()
    hook = write_hook(work)
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; print('_perfproof_stub_hook' in sys.modules)",
        ],
        cwd=project,
        env={**os.environ, "PYTHONPATH": str(hook)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.stdout.strip() == "False"
    assert HOOK_SOURCE.is_file()


# stub_function


def stub(
    spec: BenchmarkSpec,
    root: Path,
    target: str,
    tmp_path: Path,
    rounds: int = 3,
) -> Any:
    return stub_function(
        spec,
        root,
        target,
        rounds=rounds,
        work_dir=tmp_path / "stub",
        seed=3,
        process_env=os.environ,
    )


def test_the_cap_of_a_sleeping_function_is_about_the_ratio_it_leaves(
    project: Path, tmp_path: Path
) -> None:
    # The benchmark sleeps 5 x 0.02 s in heavy:slow and 0.01 s outside
    # it, so the function is 0.10 of 0.11 s and the cap is about 11.
    result = stub(make_spec(project), project, "heavy:slow", tmp_path)
    assert result.cap is not None
    assert result.remaining is not None
    assert 4 < result.cap.value < 40
    assert 0.005 < result.remaining.value < 0.03
    assert 0.08 < result.baseline.value - result.remaining.value < 0.3
    assert result.calls_per_run == 5
    assert result.replayed_per_run == 5
    assert result.real_per_run == 0
    assert result.recorded_calls == 3  # slow(0), slow(1), slow(2)
    assert result.cap_uncorrected.value <= result.cap.value
    assert result.stub_run.value < result.baseline.value
    assert result.unit == "s"
    assert result.rounds == 3


def test_calls_missing_from_the_capture_run_for_real_and_are_subtracted(
    project: Path, tmp_path: Path
) -> None:
    (project / "bench.py").write_text(BENCH_RANDOM)
    result = stub(make_spec(project), project, "heavy:slow", tmp_path)
    assert result.replayed_per_run == 0
    assert result.real_per_run == 5
    assert result.real_seconds_per_run >= 0.1
    assert any("no call was replayed" in n for n in result.notes)
    assert result.cap is not None
    assert 4 < result.cap.value < 40


def test_a_cap_resting_on_the_hooks_own_cost_is_withheld(
    project: Path, tmp_path: Path
) -> None:
    # Pickling a million integers to find the call costs far more than
    # the empty remainder of the timed region.
    (project / "bench.py").write_text(BENCH_BIG)
    result = stub(make_spec(project), project, "heavy:count", tmp_path)
    assert result.overhead_seconds_per_run > 0.005
    assert result.cap is None
    assert result.cap_bound is None
    assert result.cap_withheld == "uninformative"
    assert result.remaining is None
    assert result.removed_share is None
    assert result.cap_uncorrected.value > 0
    assert any("No cap can be given" in n for n in result.notes), result.notes


def test_a_hot_cheap_function_never_gives_a_cap_below_one(
    project: Path, tmp_path: Path
) -> None:
    # 50,000 calls of a function that does nothing: the hook costs far
    # more per call than the function, so the cap must be withheld, or
    # at least must not undercount to a value below one.
    (project / "bench.py").write_text(BENCH_HOT)
    result = stub(make_spec(project), project, "heavy:cheap", tmp_path)
    assert result.calls_per_run == 50_000
    assert result.replayed_per_run == 50_000
    if result.cap is not None:
        assert result.cap_bound is not None
        assert result.cap_bound >= 1.0
        assert result.cap_bound >= result.cap.value
    else:
        assert result.cap_withheld == "uninformative"


def test_calls_in_forked_children_are_counted(
    project: Path, tmp_path: Path
) -> None:
    (project / "bench.py").write_text(BENCH_FORK)
    result = stub(make_spec(project), project, "heavy:slow", tmp_path)
    assert result.calls_per_run == 6
    assert result.replayed_per_run == 6
    assert result.real_per_run == 0
    assert result.processes_per_run >= 2
    assert result.recorded_calls >= 3  # each worker keeps its own
    assert any("processes per run" in n for n in result.notes)
    if result.cap is not None:
        assert result.cap_bound is not None
        assert result.cap_bound >= 1.0


def test_the_bound_is_the_upper_end_of_the_interval(
    project: Path, tmp_path: Path
) -> None:
    result = stub(make_spec(project), project, "heavy:slow", tmp_path)
    assert result.cap is not None
    assert result.cap.high is not None
    assert result.cap_bound == result.cap.high
    assert result.cap_withheld is None


def test_the_capture_directory_is_removed_after_a_run(
    project: Path, tmp_path: Path
) -> None:
    stub(make_spec(project), project, "heavy:slow", tmp_path)
    assert not (tmp_path / "stub" / "capture").exists()
    assert (tmp_path / "stub" / "replay").is_dir()


@pytest.mark.parametrize("kind", ["wall", "cpu"])
def test_wall_and_cpu_metrics_take_the_hook_install_out_too(
    project: Path, tmp_path: Path, kind: str
) -> None:
    spec = make_spec(
        project,
        metric=MetricSpec(
            kind=cast("Any", kind), direction="lower_is_better", unit="s"
        ),
    )
    result = stub(spec, project, "heavy:slow", tmp_path)
    assert result.install_seconds_per_run > 0
    assert result.metric_kind == kind
    if result.cap is not None:
        assert result.cap_bound is not None
        assert result.cap_bound > 1
    else:
        assert result.cap_withheld == "uninformative"


def refused(spec: BenchmarkSpec, root: Path, target: str, tmp: Path) -> str:
    with pytest.raises(MeasureError) as info:
        stub(spec, root, target, tmp)
    return info.value.code


@pytest.mark.parametrize("target", ["nocolon", "a.b:", ":f", "a-b:f", "a:b c"])
def test_a_malformed_target_is_refused(
    project: Path, tmp_path: Path, target: str
) -> None:
    assert (
        refused(make_spec(project), project, target, tmp_path)
        == "invalid_target"
    )


def test_a_function_that_does_not_exist_is_refused(
    project: Path, tmp_path: Path
) -> None:
    code = refused(make_spec(project), project, "heavy:missing", tmp_path)
    assert code == "target_not_found"


def test_a_function_that_is_never_called_is_refused(
    project: Path, tmp_path: Path
) -> None:
    code = refused(make_spec(project), project, "heavy:never", tmp_path)
    assert code == "no_calls"


def test_a_module_the_benchmark_never_imports_is_refused(
    project: Path, tmp_path: Path
) -> None:
    code = refused(make_spec(project), project, "absent:f", tmp_path)
    assert code == "target_not_found"


def test_a_failing_benchmark_is_a_capture_failure(
    project: Path, tmp_path: Path
) -> None:
    (project / "bench.py").write_text(BENCH_BROKEN)
    code = refused(make_spec(project), project, "heavy:slow", tmp_path)
    assert code == "capture_failed"


def test_a_counter_metric_is_refused(project: Path, tmp_path: Path) -> None:
    spec = make_spec(
        project,
        metric=MetricSpec(
            kind="counter",
            direction="lower_is_better",
            unit="count",
            event="instructions",
        ),
    )
    assert refused(spec, project, "heavy:slow", tmp_path) == (
        "unsupported_metric"
    )


def test_a_reported_metric_not_in_seconds_is_refused(
    project: Path, tmp_path: Path
) -> None:
    spec = make_spec(
        project,
        metric=MetricSpec(
            kind="reported", direction="higher_is_better", unit="ops/s"
        ),
    )
    assert refused(spec, project, "heavy:slow", tmp_path) == (
        "unsupported_metric"
    )


def test_fewer_than_three_rounds_are_refused(
    project: Path, tmp_path: Path
) -> None:
    with pytest.raises(MeasureError) as info:
        stub(make_spec(project), project, "heavy:slow", tmp_path, rounds=2)
    assert info.value.code == "invalid_arguments"


def test_a_remote_service_in_the_environment_is_refused(
    project: Path, tmp_path: Path
) -> None:
    spec = make_spec(
        project,
        env={"DATABASE_URL": "postgresql://db.example.com:5432/orders"},
    )
    assert refused(spec, project, "heavy:slow", tmp_path) == "side_effects"
    assert not (tmp_path / "stub" / "capture").exists()
