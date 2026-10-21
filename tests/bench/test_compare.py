import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pytest
from pydantic import ValidationError

from perfproof.bench import compare as compare_module
from perfproof.bench.compare import (
    SAMPLES_NAME,
    CompareResult,
    CompareStoppedError,
    compare,
    keep_blocked,
    late_looks,
)
from perfproof.bench.lock import build_manifest
from perfproof.bench.runner import Round, Runner, RunnerError
from perfproof.machine import fingerprint
from perfproof.report import read_record
from perfproof.schema import (
    BenchmarkSpec,
    GateResult,
    Interval,
    MachineFingerprint,
    RunRecord,
)
from perfproof.stats.intervals import t_interval
from perfproof.stats.sequential import BATCH_BLOCK, Look, look_confidence

PY = sys.executable
REPO_ROOT = Path(__file__).resolve().parents[2]
ROLLING = REPO_ROOT / "examples" / "rolling"
MEMBERSHIP = REPO_ROOT / "examples" / "membership"
SKIPPED = shutil.ignore_patterns(
    "candidates", "__pycache__", "output", ".pytest_cache"
)
ALL_GATES = [
    "side_effects",
    "lock",
    "procedure",
    "runtime",
    "same_host",
    "correctness",
    "coverage",
]

BENCH = """\
import time

from work import run

start = time.perf_counter()
result = run()
elapsed = time.perf_counter() - start
with open("out.txt", "w") as f:
    f.write(f"{result}\\n")
print("PERFPROOF {\\"value\\": %.6f}" % elapsed)
"""
COARSE_BENCH = """\
from work import run

with open("out.txt", "w") as f:
    f.write(f"{run()}\\n")
print('PERFPROOF {"value": 1}')
"""
TESTS = """\
from work import run


def test_run():
    assert run() == 42
"""

ENV_DUMP = (
    "import os\n\n"
    "with open({name!r}, 'a') as f:\n"
    "    f.write('\\n'.join(sorted(os.environ)) + '\\n')\n"
)
ENV_BENCH = ENV_DUMP.format(name="bench-env.txt") + BENCH
ENV_TESTS = ENV_DUMP.format(name="tests-env.txt") + TESTS
RUN_PY = """\
#!/usr/bin/env python3
import runpy

runpy.run_path("bench.py", run_name="__main__")
"""


def work(sleep: float, result: int = 42) -> str:
    return (
        f"import time\n\n\ndef run():\n    time.sleep({sleep})\n"
        f"    return {result}\n"
    )


FAILS = 'def run():\n    raise SystemExit("candidate broke: exit 3")\n'
FAILS_SECOND_TIME = """\
import os


def run():
    if os.path.exists("ran.marker"):
        raise SystemExit("baseline broke on its second run")
    open("ran.marker", "w").close()
    return 42
"""
GUARDRAILS: list[dict[str, Any]] = [
    {
        "metric": "peak_memory",
        "direction": "lower_is_better",
        "unit": "bytes",
        "allowance": 0.1,
        "allowance_source": "default",
    },
    {
        "metric": "peak_gpu_memory",
        "direction": "lower_is_better",
        "unit": "bytes",
        "applicable": False,
        "reason": "a CPU-only benchmark",
    },
    {
        "metric": "p99_latency",
        "direction": "lower_is_better",
        "unit": "s",
        "applicable": False,
        "reason": "serves no requests",
    },
]


def spec_fields(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "name": "work",
        "command": ["python", "bench.py"],
        "metric": {
            "kind": "reported",
            "direction": "lower_is_better",
            "unit": "s",
        },
        "correctness": {
            "command": ["python", "-m", "pytest", "-q"],
            "timeout_seconds": 60.0,
            "outputs": [{"path": "out.txt", "compare": "text"}],
        },
        "warmup": 1,
        "budget": {"max_rounds": 10, "max_seconds": 300.0},
        "looks": {"n0": 10, "k": 2, "max_looks": 1},
        "timeout_seconds": 30.0,
        "harness": ["bench.py"],
        "oracle": ["test_work.py"],
        "config": ["pytest.ini"],
        "objective": "wall_time",
        "guardrails": GUARDRAILS,
    }
    fields.update(changes)
    return fields


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    # The benchmarks run `python`; this one has numpy and pytest.
    path = os.environ.get("PATH", os.defpath)
    monkeypatch.setenv("PATH", f"{Path(PY).parent}{os.pathsep}{path}")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done.stdout.strip()


def commit_all(repo: Path) -> str:
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "baseline")
    return git(repo, "rev-parse", "HEAD")


@dataclass
class Project:
    root: Path
    commit: str
    tmp: Path
    runs: int = 0

    def candidate(self, name: str, files: Mapping[str, str]) -> Path:
        tree = self.tmp / f"candidate-{name}"
        shutil.copytree(self.root, tree, ignore=shutil.ignore_patterns(".git"))
        for rel, text in files.items():
            (tree / rel).write_text(text)
        return tree

    def run(
        self, candidate: Path, spec: BenchmarkSpec | None = None
    ) -> CompareResult:
        spec = spec or BenchmarkSpec.model_validate(spec_fields())
        self.runs += 1
        return compare(
            spec,
            build_manifest(spec, self.root),
            baseline_root=self.root,
            baseline_commit=self.commit,
            candidate_root=candidate,
            run_dir=self.run_dir(),
            process_env=os.environ,
            order_seed=7,
        )

    def run_dir(self) -> Path:
        return self.tmp / "runs" / f"run-{self.runs}"


def make_project(
    tmp_path: Path,
    *,
    bench: str = BENCH,
    baseline: str | None = None,
    tests: str = TESTS,
    executables: Mapping[str, str] | None = None,
) -> Project:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "bench.py").write_text(bench)
    (root / "work.py").write_text(baseline or work(0.03))
    (root / "test_work.py").write_text(tests)
    for name, text in (executables or {}).items():
        (root / name).write_text(text)
        (root / name).chmod(0o755)
    (root / "pytest.ini").write_text(
        "[pytest]\naddopts = -p no:cacheprovider\n"
    )
    return Project(root=root, commit=commit_all(root), tmp=tmp_path)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return make_project(tmp_path)


def gates(record: RunRecord) -> dict[str, str]:
    return {g.gate: g.status for g in record.gates}


def nothing_ran(project: Project, *trees: Path) -> None:
    logs = project.run_dir() / "logs"
    assert not [p for p in logs.rglob("*") if p.is_file()]
    for tree in trees:
        assert not (tree / "out.txt").exists()
        assert not (tree / "__pycache__").exists()


# The thin path: gates pass, the rounds run, the verdict is INCONCLUSIVE


def twelve_rounds() -> BenchmarkSpec:
    """One look on 12 rounds, the fewest with a batch-means interval."""
    return BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )


def test_passing_gates_end_inconclusive_with_the_interval(
    project: Project,
) -> None:
    faster = project.candidate("fast", {"work.py": work(0.005)})
    result = project.run(faster, twelve_rounds())
    record = result.record
    v = record.verdict
    assert (v.outcome, v.reason, v.rule, v.gate) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        5,
        None,
    )
    assert v.diagnostics.procedure_uncalibrated
    assert v.calibration == "uncalibrated"
    assert (v.rounds, v.looks) == (12, 1)
    assert v.procedure == "t_log_ratio_batch_means"
    assert v.interval is not None
    assert v.estimate is not None
    # Two blocks leave one degree of freedom, so under load the interval
    # can reach below 1; only its order is a property of the procedure.
    assert 0 < v.interval.lower <= v.estimate <= v.interval.upper
    assert v.interval.confidence == look_confidence(0.05, 1)
    assert v.diagnostics.wall_interval is not None
    assert v.diagnostics.metric_disagreement is False
    assert gates(record) == {
        "side_effects": "passed",
        "lock": "passed",
        "procedure": "passed",
        "runtime": "passed",
        "same_host": "passed",
        "correctness": "passed",
        "coverage": "not_available",
    }
    assert [g.gate for g in record.gates] == ALL_GATES
    assert {g.metric: g.status for g in record.guardrails} == {
        "peak_memory": "unmeasurable",
        "peak_gpu_memory": "not_applicable",
        "p99_latency": "not_applicable",
    }
    codes = {w.code for w in record.warnings}
    assert "differential_checks_not_run" in codes
    assert record.order_seed == 7
    assert record.run_id == result.run_dir.name
    assert read_record(result.record_path) == record
    assert result.report_path.read_text().startswith("# perfproof report")


def test_samples_are_saved_with_their_printed_resolution(
    project: Project,
) -> None:
    result = project.run(project.candidate("same", {}))
    assert result.record.samples_path == SAMPLES_NAME
    with np.load(result.run_dir / SAMPLES_NAME, allow_pickle=False) as data:
        assert data["round"].tolist() == [i // 2 for i in range(20)]
        assert data["position"].tolist() == [0, 1] * 10
        assert int(data["candidate"].sum()) == 10
        # Balanced order: the candidate ran first in half the rounds.
        assert int(data["candidate"][data["position"] == 0].sum()) == 5
        assert np.all(data["value"] > 0)
        assert np.all(data["wall_seconds"] > data["value"])
        assert len(set(data["command_hash"].tolist())) == 1
        steps = data["step"]
    # Printed with six decimals: the step is 1e-6, never below it.
    assert np.all(steps == steps[0])
    assert Decimal(float(steps[0])) >= Decimal("1e-6")
    assert float(steps[0]) == pytest.approx(1e-6, rel=1e-15)


def test_values_at_their_resolution_stop_inconclusive(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path, bench=COARSE_BENCH)
    result = project.run(project.candidate("same", {}))
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 5)
    assert v.interval is None
    assert v.estimate is None
    assert (v.rounds, v.looks) == (10, 1)
    assert "coarse_resolution" in {w.code for w in result.record.warnings}


def test_budget_that_ends_before_n0_has_no_look(project: Project) -> None:
    fields = spec_fields(budget={"max_rounds": 10, "max_seconds": 0.001})
    spec = BenchmarkSpec.model_validate(fields)
    result = project.run(project.candidate("same", {}), spec)
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        5,
    )
    assert (v.rounds, v.looks) == (1, 0)
    assert v.interval is None
    assert result.record.samples_path == SAMPLES_NAME


# Gate REJECTs


def test_locked_file_edit_is_rejected_before_anything_runs(
    project: Project,
) -> None:
    edited = project.candidate("bench-edit", {"bench.py": BENCH + "\n"})
    result = project.run(edited)
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule, v.gate) == (
        "REJECT",
        "integrity",
        1,
        "lock",
    )
    assert (v.estimate, v.interval, v.rounds) == (None, None, 0)
    assert gates(result.record) == {"side_effects": "passed", "lock": "failed"}
    lock = result.record.gates[1]
    assert [d.item for d in lock.differences] == ["bench.py"]
    assert result.record.guardrails == ()
    assert result.record.samples_path is None
    nothing_ran(project, project.root, edited)


def test_remote_service_is_refused_before_anything_runs(
    project: Project,
) -> None:
    url = "postgresql://db.example.com:5432/orders"
    spec = BenchmarkSpec.model_validate(spec_fields(env={"DATABASE_URL": url}))
    candidate = project.candidate("same", {})
    result = project.run(candidate, spec)
    v = result.record.verdict
    assert (v.outcome, v.reason, v.gate) == (
        "REJECT",
        "integrity",
        "side_effects",
    )
    assert gates(result.record) == {"side_effects": "failed"}
    assert "db.example.com" in result.record.gates[0].detail
    nothing_ran(project, project.root, candidate)


def test_wrong_output_is_a_correctness_reject(project: Project) -> None:
    wrong = project.candidate("wrong", {"work.py": work(0.005, 41)})
    result = project.run(wrong)
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule, v.gate) == (
        "REJECT",
        "correctness",
        1,
        "correctness",
    )
    assert v.estimate is None
    assert v.rounds == 0
    correctness = result.record.gates[-1]
    assert correctness.gate == "correctness"
    assert "out.txt" in correctness.detail
    assert "coverage" not in gates(result.record)


def test_failed_candidate_execution_is_a_correctness_reject(
    project: Project,
) -> None:
    broken = project.candidate("broken", {"work.py": FAILS})
    result = project.run(broken)
    v = result.record.verdict
    assert (v.outcome, v.reason, v.gate) == (
        "REJECT",
        "correctness",
        "correctness",
    )
    gate = result.record.gates[-1]
    assert gate.output is not None
    assert gate.output.exit_code == 1
    assert "candidate broke: exit 3" in gate.output.stderr
    assert gate.output.command == ("python", "bench.py")
    # The baseline ran again, one execution, before blame.
    recheck = result.run_dir / "logs" / "recheck-warmup-0"
    assert sorted(p.name for p in recheck.iterdir()) == [
        "recheck.baseline.stderr",
        "recheck.baseline.stdout",
    ]


def test_candidate_failing_mid_rounds_is_a_correctness_reject(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Runner.run_round
    candidate = project.candidate("late", {"work.py": work(0.005)})

    def break_after_three(self: Runner, index: int) -> Round:
        if index == 3:
            (candidate / "work.py").write_text(FAILS)
        return real(self, index)

    monkeypatch.setattr(Runner, "run_round", break_after_three)
    result = project.run(candidate)
    v = result.record.verdict
    assert (v.outcome, v.gate, v.rounds) == ("REJECT", "correctness", 3)
    assert v.estimate is None
    assert result.record.samples_path == SAMPLES_NAME
    failed = {g.gate: g for g in result.record.gates}["correctness"]
    assert "round 3" in failed.detail


def test_host_change_fails_the_same_host_gate(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[MachineFingerprint] = []

    def probe() -> MachineFingerprint:
        found = fingerprint()
        if calls:
            found = found.model_copy(update={"host_id": "another-host"})
        calls.append(found)
        return found

    monkeypatch.setattr(compare_module, "fingerprint", probe)
    result = project.run(project.candidate("same", {}))
    v = result.record.verdict
    assert (v.outcome, v.reason, v.gate) == (
        "REJECT",
        "integrity",
        "same_host",
    )
    assert v.rounds == 10
    assert v.interval is None
    assert len(calls) == 2
    assert "another-host" in result.record.gates[4].detail


# The look schedule


@pytest.mark.parametrize(
    ("n0", "max_rounds", "k", "max_looks", "looks", "rounds"),
    [
        (10, 13, 2, 3, 2, 13),
        (10, 12, 2, 3, 2, 12),
        (10, 20, 4, 2, 2, 14),
        (12, 13, 2, 2, 1, 13),
    ],
    ids=["odd-budget", "budget-on-a-look", "kth-look", "re-decided-final"],
)
def test_looks_follow_the_schedule_and_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    n0: int,
    max_rounds: int,
    k: int,
    max_looks: int,
    looks: int,
    rounds: int,
) -> None:
    project = make_project(tmp_path, baseline=work(0.005))
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": max_rounds, "max_seconds": 600.0},
            looks={"n0": n0, "k": k, "max_looks": max_looks},
        )
    )
    decided = spy_on_looks(monkeypatch)
    result = project.run(project.candidate("same", {}), spec)
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 5)
    assert (v.looks, v.rounds) == (looks, rounds)
    assert_decided_at(v.interval, decided, look_confidence(0.05, max_looks))
    assert look_confidence(0.05, max_looks) != look_confidence(0.05, 1)


def spy_on_looks(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[float, float, float]]:
    """Record each look's confidence and the bounds it decided on."""
    decided: list[tuple[float, float, float]] = []
    real = compare_module.decide_look

    def spy(*args: Any, **kwargs: Any) -> Look:
        look = real(*args, **kwargs)
        bounds = (float(look.lower[0]), float(look.upper[0]))
        decided.append((kwargs["confidence"], *bounds))
        return look

    monkeypatch.setattr(compare_module, "decide_look", spy)
    return decided


def assert_decided_at(
    interval: Interval | None,
    decided: list[tuple[float, float, float]],
    confidence: float,
) -> None:
    """The recorded bounds are a look's, decided at ``confidence``."""
    assert interval is not None
    assert decided
    assert {c for c, _, _ in decided} == {confidence}
    assert interval.confidence == confidence
    assert (interval.lower, interval.upper) in {
        (lo, hi) for _, lo, hi in decided
    }


def test_time_budget_after_n0_re_decides_the_last_look_as_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path, baseline=work(0.005))
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": 20, "max_seconds": 600.0},
            looks={"n0": 12, "k": 2, "max_looks": 3},
        )
    )
    skipped = [0.0]
    clock = SimpleNamespace(
        monotonic=lambda: time.monotonic() + skipped[0],
        perf_counter_ns=time.perf_counter_ns,
        get_clock_info=time.get_clock_info,
    )
    monkeypatch.setattr(compare_module, "time", clock)
    real = Runner.run_round

    def slow_after_n0(self: Runner, index: int) -> Round:
        done = real(self, index)
        if index == 12:
            skipped[0] = 1e6
        return done

    monkeypatch.setattr(Runner, "run_round", slow_after_n0)
    decided = spy_on_looks(monkeypatch)
    result = project.run(project.candidate("same", {}), spec)
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 5)
    assert (v.looks, v.rounds) == (1, 13)
    assert_decided_at(v.interval, decided, look_confidence(0.05, 3))


# Reset

EVENTS_BENCH = (
    "with open('events.txt', 'a') as f:\n    f.write('bench\\n')\n" + BENCH
)
RESET_LOG = (
    "import os, sys\n"
    "if os.path.exists('reset-fails'):\n"
    "    sys.exit('reset broke')\n"
    "with open('events.txt', 'a') as f:\n"
    "    f.write('reset\\n')\n"
)
RESET = {"command": ["python", "-c", RESET_LOG], "timeout_seconds": 30.0}


def events(tree: Path) -> list[str]:
    path = tree / "events.txt"
    return path.read_text().splitlines() if path.exists() else []


def test_reset_runs_before_every_execution_end_to_end(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path, bench=EVENTS_BENCH)
    candidate = project.candidate("fast", {"work.py": work(0.005)})
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            reset=RESET,
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    result = project.run(candidate, spec)
    v = result.record.verdict
    assert (v.outcome, v.rule, v.rounds) == ("INCONCLUSIVE", 5, 12)
    assert v.interval is not None
    assert set(gates(result.record).values()) <= {"passed", "not_available"}
    # Per tree: the output-producing execution, one warmup, 12 rounds.
    for tree in (project.root, candidate):
        assert events(tree) == ["reset", "bench"] * 14


def test_recheck_runs_one_baseline_execution_after_its_reset(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path, bench=EVENTS_BENCH)
    broken = project.candidate("broken", {"work.py": FAILS})
    spec = BenchmarkSpec.model_validate(spec_fields(reset=RESET))
    result = project.run(broken, spec)
    v = result.record.verdict
    assert (v.outcome, v.gate) == ("REJECT", "correctness")
    # The output-producing execution, then the recheck: one execution.
    assert events(project.root) == ["reset", "bench"] * 2
    assert events(broken) == ["reset", "bench"]


def test_failed_reset_fails_the_side_effects_gate(tmp_path: Path) -> None:
    project = make_project(tmp_path, bench=EVENTS_BENCH)
    candidate = project.candidate("same", {})
    (candidate / "reset-fails").touch()
    spec = BenchmarkSpec.model_validate(spec_fields(reset=RESET))
    result = project.run(candidate, spec)
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule, v.gate) == (
        "REJECT",
        "integrity",
        1,
        "side_effects",
    )
    assert (v.rounds, v.estimate, v.interval) == (0, None, None)
    assert gates(result.record)["side_effects"] == "failed"
    assert "correctness" not in gates(result.record)
    failed = result.record.gates[0]
    assert failed.detail.startswith("before warmup-0 candidate: ")
    assert failed.output is not None
    assert failed.output.exit_code == 1
    assert "reset broke" in failed.output.stderr
    # The candidate's execution never ran; the baseline's did.
    assert events(project.root) == ["reset", "bench"]
    assert events(candidate) == []


# The benchmark's environment


def test_perfproofs_own_environment_reaches_no_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_SECRET_TOKEN", "x")
    project = make_project(tmp_path, bench=ENV_BENCH, tests=ENV_TESTS)
    candidate = project.candidate("same", {})
    result = project.run(candidate)
    assert result.record.verdict.rule == 5
    for tree in (project.root, candidate):
        for name in ("bench-env.txt", "tests-env.txt"):
            seen = set((tree / name).read_text().split())
            assert "PATH" in seen
            assert "AGENT_SECRET_TOKEN" not in seen


# No verdict


@pytest.mark.parametrize(
    ("command", "where"),
    [(["./run.py"], "candidate"), (["python3.99", "bench.py"], "neither")],
    ids=["only-in-candidate", "in-neither"],
)
def test_baseline_that_cannot_start_stops_with_no_verdict(
    tmp_path: Path, command: list[str], where: str
) -> None:
    project = make_project(tmp_path)
    files = {"run.py": RUN_PY} if where == "candidate" else {}
    candidate = project.candidate("with-run", files)
    if files:
        (candidate / "run.py").chmod(0o755)
    spec = BenchmarkSpec.model_validate(spec_fields(command=command))
    with pytest.raises(CompareStoppedError) as stopped:
        project.run(candidate, spec)
    assert stopped.value.code == "broken_baseline"
    assert "can't be started" in stopped.value.message
    assert not (project.run_dir() / "record.json").exists()
    nothing_ran(project, project.root, candidate)


@pytest.mark.parametrize(
    ("command", "executables"),
    [
        (["./run.py"], {"run.py": "#!/nonexistent/python3\nprint(1)\n"}),
        (["/usr/bin/env", "python3.99", "bench.py"], {}),
    ],
    ids=["bad-interpreter-line", "env-missing-interpreter"],
)
def test_baseline_whose_interpreter_is_missing_stops_with_no_verdict(
    tmp_path: Path, command: list[str], executables: dict[str, str]
) -> None:
    project = make_project(tmp_path, executables=executables)
    candidate = project.candidate("same", {})
    spec = BenchmarkSpec.model_validate(spec_fields(command=command))
    with pytest.raises(CompareStoppedError) as stopped:
        project.run(candidate, spec)
    assert stopped.value.code == "broken_baseline"
    assert "the baseline failed" in stopped.value.message
    assert not (project.run_dir() / "record.json").exists()


# No verdict, from a failure


def test_failing_baseline_stops_with_no_verdict(tmp_path: Path) -> None:
    project = make_project(tmp_path, baseline=FAILS)
    with pytest.raises(CompareStoppedError) as stopped:
        project.run(project.candidate("fast", {"work.py": work(0.005)}))
    assert stopped.value.code == "broken_baseline"
    assert not (project.run_dir() / "record.json").exists()


def test_failure_shared_with_the_baseline_blames_neither(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path, baseline=FAILS_SECOND_TIME)
    with pytest.raises(CompareStoppedError) as stopped:
        project.run(project.candidate("broken", {"work.py": FAILS}))
    assert stopped.value.code == "broken_baseline"
    assert "shared" in stopped.value.message
    assert not (project.run_dir() / "record.json").exists()


def test_harness_fault_stops_with_no_verdict(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fault(self: Runner, index: int) -> Round:
        msg = "out of file descriptors"
        raise RunnerError(msg)

    monkeypatch.setattr(Runner, "run_round", fault)
    with pytest.raises(CompareStoppedError) as stopped:
        project.run(project.candidate("same", {}))
    assert stopped.value.code == "harness_fault"
    assert "out of file descriptors" in stopped.value.message
    assert not (project.run_dir() / "record.json").exists()


def test_an_odd_one_host_schedule_never_reaches_compare() -> None:
    # Schema 3 refuses an odd n0 on one host when the spec is built, so
    # compare's own look-schedule refusal can't be reached this way.
    budget = {"max_rounds": 12, "max_seconds": 300.0}
    odd = {"n0": 11, "k": 2, "max_looks": 1}
    with pytest.raises(ValidationError, match="order pairs"):
        BenchmarkSpec.model_validate(spec_fields(budget=budget, looks=odd))


def test_an_undeclared_guardrail_still_lets_a_gate_reject(
    project: Project,
) -> None:
    spec = BenchmarkSpec.model_validate(
        spec_fields(objective=None, guardrails=[])
    )
    edited = project.candidate("bench-edit", {"bench.py": BENCH + "\n"})
    result = project.run(edited, spec)
    assert result.record.verdict.gate == "lock"


# The CLI, end to end, on examples/rolling and examples/membership


def cli(*argv: str) -> dict[str, Any]:
    done = subprocess.run(
        [PY, "-m", "perfproof", *argv, "--json"],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    document: dict[str, Any] = json.loads(done.stdout)
    assert done.returncode == (0 if document["ok"] else 1), done.stderr
    return document


def example_repo(tmp_path: Path, example: Path) -> Path:
    root = tmp_path / f"{example.name}-repo"
    shutil.copytree(example, root, ignore=SKIPPED)
    return root


def example_candidate(tmp_path: Path, example: Path, name: str) -> Path:
    tree = tmp_path / f"{example.name}-{name}"
    shutil.copytree(example, tree, ignore=SKIPPED)
    source = example / "candidates" / name
    for path in source.rglob("*.py"):
        target = tree / path.relative_to(source)
        shutil.copy2(path, target)
    return tree


def thin_rolling_spec(root: Path) -> Path:
    fields = json.loads((root / "spec.json").read_text())
    fields.update(
        budget={"max_rounds": 12, "max_seconds": 600},
        looks={"n0": 12, "k": 10, "max_looks": 1},
        env={"ROLLING_FLAKY_SEED": "0"},
        objective="wall_time",
        guardrails=GUARDRAILS,
    )
    path = root.parent / "rolling-thin-spec.json"
    path.write_text(json.dumps(fields))
    return path


@pytest.fixture
def rolling(tmp_path: Path) -> tuple[Path, str, str]:
    root = example_repo(tmp_path, ROLLING)
    commit_all(root)
    thin = cli(
        "bench",
        "define",
        "rolling-sum",
        "--spec",
        str(thin_rolling_spec(root)),
        "--repo",
        str(root),
    )
    shipped = cli(
        "bench",
        "define",
        "rolling-sum",
        "--spec",
        str(root / "spec.json"),
        "--repo",
        str(root),
    )
    return root, thin["result"]["spec_hash"], shipped["result"]["spec_hash"]


def compare_cli(root: Path, spec_hash: str, candidate: Path) -> dict[str, Any]:
    return cli(
        "compare",
        "rolling-sum",
        "--repo",
        str(root),
        "--spec-hash",
        spec_hash,
        "--candidate",
        str(candidate),
    )


def test_cli_rolling_fix_is_inconclusive_before_calibration(
    tmp_path: Path, rolling: tuple[Path, str, str]
) -> None:
    root, thin, _ = rolling
    fix = example_candidate(tmp_path, ROLLING, "fix")
    result = compare_cli(root, thin, fix)["result"]
    v = result["verdict"]
    assert (v["outcome"], v["reason"], v["rule"]) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        5,
    )
    assert v["diagnostics"]["procedure_uncalibrated"] is True
    assert v["interval"]["lower"] > 1.03
    assert v["procedure"] == "t_log_ratio_batch_means"
    with np.load(
        Path(result["run_dir"]) / SAMPLES_NAME, allow_pickle=False
    ) as data:
        mine = data["candidate"] == 1
        value = data["value"][np.argsort(data["round"], kind="stable")]
        mine = mine[np.argsort(data["round"], kind="stable")]
    means = t_interval(
        value[~mine][np.newaxis],
        value[mine][np.newaxis],
        direction="lower_is_better",
        confidence=v["interval"]["confidence"],
        block=BATCH_BLOCK,
    )
    assert v["interval"]["lower"] <= float(means.lower[0])
    assert v["interval"]["lower"] == pytest.approx(
        float(means.lower[0]), rel=1e-2
    )
    record = read_record(Path(result["record_path"]))
    assert record.verdict.model_dump(mode="json") == v
    assert Path(result["report_path"]).is_file()
    assert Path(result["run_dir"]).parent == root / ".perfproof" / "runs"
    assert {g["gate"]: g["status"] for g in result["gates"]}["coverage"] == (
        "not_available"
    )
    # The skill reads only this JSON: what was not verified is in it.
    unverified_json = {
        (item["kind"], item["name"]): item for item in result["not_verified"]
    }
    assert ("gate", "coverage") in unverified_json
    assert unverified_json[("gate", "coverage")]["reason"]
    assert result["measured_metric"] is None
    assert result["warnings"] == [
        {"code": w.code, "message": w.message} for w in record.warnings
    ]
    assert git(root, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    ("candidate", "spec", "gate", "reason"),
    [
        ("wrong-output", "thin", "correctness", "correctness"),
        ("bench-edit", "thin", "lock", "integrity"),
        ("bench-edit", "shipped", "lock", "integrity"),
    ],
)
def test_cli_rolling_decoys_get_their_gate_reject(
    tmp_path: Path,
    rolling: tuple[Path, str, str],
    candidate: str,
    spec: str,
    gate: str,
    reason: str,
) -> None:
    root, thin, shipped = rolling
    tree = example_candidate(tmp_path, ROLLING, candidate)
    result = compare_cli(root, thin if spec == "thin" else shipped, tree)
    v = result["result"]["verdict"]
    assert (v["outcome"], v["reason"], v["gate"]) == ("REJECT", reason, gate)
    assert v["estimate"] is None
    assert Path(result["result"]["record_path"]).is_file()


def test_cli_remote_database_spec_is_refused_before_anything_runs(
    tmp_path: Path,
) -> None:
    root = example_repo(tmp_path, MEMBERSHIP)
    commit_all(root)
    spec = str(root / "spec-remote-db.json")
    cli("bench", "define", "held-orders", "--spec", spec, "--repo", str(root))
    fix = example_candidate(tmp_path, MEMBERSHIP, "fix")
    result = cli(
        "compare", "held-orders", "--repo", str(root), "--candidate", str(fix)
    )["result"]
    v = result["verdict"]
    assert (v["outcome"], v["reason"], v["gate"]) == (
        "REJECT",
        "integrity",
        "side_effects",
    )
    record = read_record(Path(result["record_path"]))
    assert "db.example.com" in record.gates[0].detail
    run_dir = Path(result["run_dir"])
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "record.json",
        "report.md",
    ]
    assert not (fix / "output").exists()


def test_cli_compares_against_a_revision(
    tmp_path: Path, rolling: tuple[Path, str, str]
) -> None:
    root, _, shipped = rolling
    git(root, "checkout", "-q", "-b", "edit")
    (root / "bench.py").write_text((root / "bench.py").read_text() + "\n")
    git(root, "commit", "-q", "-am", "edit the benchmark")
    result = cli(
        "compare",
        "rolling-sum",
        "--repo",
        str(root),
        "--spec-hash",
        shipped,
        "--baseline",
        "main",
        "--candidate",
        "edit",
    )["result"]
    assert result["verdict"]["gate"] == "lock"
    worktrees = sorted(
        p.name.split("-")[0] for p in (root / ".perfproof/worktrees").iterdir()
    )
    assert worktrees == ["baseline", "candidate"]


@pytest.mark.parametrize(
    ("extra", "code"),
    [
        (["--candidate", ".", "--resume", "run-1"], "usage"),
        (["--candidate", ".", "--spec-hash", "zz"], "not_found"),
        (["--candidate", "no-such-revision"], "invalid_revision"),
        ([], "usage"),
    ],
    ids=["resume", "bad-hash", "bad-candidate", "no-candidate"],
)
def test_cli_errors(
    rolling: tuple[Path, str, str], extra: list[str], code: str
) -> None:
    root, _, shipped = rolling
    hashed = ["--spec-hash", shipped] if "--spec-hash" not in extra else []
    done = subprocess.run(
        [
            PY,
            "-m",
            "perfproof",
            "compare",
            "rolling-sum",
            "--repo",
            str(root),
            *hashed,
            *extra,
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert json.loads(done.stdout)["error"]["code"] == code


def test_cli_ambiguous_spec_needs_a_hash(
    tmp_path: Path, rolling: tuple[Path, str, str]
) -> None:
    root, thin, shipped = rolling
    result = cli(
        "compare",
        "rolling-sum",
        "--repo",
        str(root),
        "--candidate",
        str(tmp_path),
    )
    assert result["error"]["code"] == "ambiguous_hash"
    assert sorted(result["error"]["details"]["matches"]) == sorted(
        [thin, shipped]
    )


# Batch means, the diagnostic gate and the late-look refusal

WAVE_BENCH = """\
import hashlib
import os
from pathlib import Path

from work import factor

# Each tree counts its own executions, so round r has the same index i
# in both and the printed values are a function of (tree, round).
name = hashlib.md5(os.getcwd().encode()).hexdigest()
counter = Path(os.environ["WAVE_DIR"]) / name
i = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(i + 1))
with open("out.txt", "w") as f:
    f.write("42\\n")
print("PERFPROOF {\\"value\\": %r}" % factor(i))
"""
# Distinct values, so the continuity check of a reported metric passes.
WAVE_BASELINE = "def factor(i):\n    return 1.0 + 0.0001 * ((i * 7) % 11)\n"
WAVE_CANDIDATE = (
    "import math\n\n\ndef factor(i):\n"
    "    return 0.7 * math.exp(0.015 * math.sin(2 * math.pi * i / 40))\n"
)


def wave_run(
    tmp_path: Path, spec_changes: Mapping[str, Any] | None = None
) -> tuple[Project, CompareResult]:
    """A candidate 1.43 times faster, log ratios on a slow wave."""
    project = make_project(tmp_path, bench=WAVE_BENCH, baseline=WAVE_BASELINE)
    fields = spec_fields(
        budget={"max_rounds": 12, "max_seconds": 300.0},
        looks={"n0": 12, "k": 2, "max_looks": 1},
        env={"WAVE_DIR": str(tmp_path)},
        **(spec_changes or {}),
    )
    spec = BenchmarkSpec.model_validate(fields)
    candidate = project.candidate("wave", {"work.py": WAVE_CANDIDATE})
    return project, project.run(candidate, spec)


def arm_values(result: CompareResult) -> tuple[Any, Any]:
    """Both arms' recorded values in round order."""
    with np.load(result.run_dir / SAMPLES_NAME, allow_pickle=False) as data:
        mine = data["candidate"] == 1
        order = np.argsort(data["round"], kind="stable")
        value = data["value"][order]
        return value[~mine[order]], value[mine[order]]


def test_the_interval_is_the_batch_means_interval(project: Project) -> None:
    faster = project.candidate("fast", {"work.py": work(0.005)})
    result = project.run(faster, twelve_rounds())
    v = result.record.verdict
    assert v.interval is not None
    b, c = arm_values(result)
    kwargs: dict[str, Any] = {
        "direction": "lower_is_better",
        "confidence": v.interval.confidence,
    }
    means = t_interval(
        b[np.newaxis], c[np.newaxis], block=BATCH_BLOCK, **kwargs
    )
    plain = t_interval(b[np.newaxis], c[np.newaxis], **kwargs)
    # The resolution widens the bounds outward by a little: the printed
    # values have six decimals, so well under 1% here.
    assert v.interval.lower <= float(means.lower[0])
    assert v.interval.upper >= float(means.upper[0])
    assert v.interval.lower == pytest.approx(float(means.lower[0]), rel=1e-2)
    assert v.interval.upper == pytest.approx(float(means.upper[0]), rel=1e-2)
    assert abs(v.interval.lower - float(means.lower[0])) < abs(
        v.interval.lower - float(plain.lower[0])
    )
    assert v.estimate == float(means.estimate[0])


def test_the_order_of_each_round_reaches_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    real = compare_module.decide_look

    def spy(*args: Any, **kwargs: Any) -> Look:
        seen.append((kwargs["candidate_first"], args[0].shape[1]))
        return real(*args, **kwargs)

    monkeypatch.setattr(compare_module, "decide_look", spy)
    _, result = wave_run(tmp_path)
    with np.load(result.run_dir / SAMPLES_NAME, allow_pickle=False) as data:
        ran_first = data["candidate"][data["position"] == 0] == 1
    assert 0 < int(ran_first.sum()) < 12
    assert seen
    for flags, n in seen:
        assert flags.dtype == np.bool_
        assert flags.shape == (1, n)
        assert flags[0].tolist() == ran_first[:n].tolist()


def test_a_withheld_look_names_its_reason_in_the_record_and_report(
    tmp_path: Path,
) -> None:
    _, result = wave_run(tmp_path)
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 5)
    assert v.procedure == "t_log_ratio_batch_means"
    assert v.diagnostics.autocorrelated_rounds is True
    assert v.diagnostics.outlier_rounds is False
    assert v.interval is not None
    assert v.interval.lower > 1.03
    report = result.report_path.read_text()
    assert "autocorrelated_rounds: yes (decision withheld)" in report
    assert "The diagnostic gate withheld the decision" in report
    assert "no number of rounds can change" in report
    assert "More rounds, or less dependence" not in report
    assert read_record(result.record_path) == result.record


def test_high_lag1_autocorrelation_warns(tmp_path: Path) -> None:
    _, result = wave_run(tmp_path)
    warned = [
        w
        for w in result.record.warnings
        if w.code == "lag1_autocorrelation_high"
    ]
    assert len(warned) == 1
    lag1 = result.record.verdict.diagnostics.lag1_autocorrelation
    assert lag1 is not None
    assert lag1 > 0.3
    assert f"{lag1:.3f}" in warned[0].message


@pytest.mark.parametrize(
    ("n0", "k", "looks", "max_rounds", "late"),
    [
        (10, 10, 6, 60, 0),
        (20, 20, 6, 120, 1),
        (20, 20, 7, 121, 1),
        (20, 20, 7, 122, 2),
        (20, 20, 10, 200, 5),
        (10, 10, 14, 140, 3),
        (120, 2, 3, 122, 2),
        (120, 2, 1, 130, 1),
    ],
    ids=[
        "python-default",
        "gpu-default",
        "one-late-look-no-room-for-a-cut",
        "one-late-look-and-a-cut",
        "shipped-examples",
        "three-late",
        "n0-already-late",
        "single-look-late",
    ],
)
def test_late_looks_are_counted_as_the_method_says(
    n0: int, k: int, looks: int, max_rounds: int, late: int
) -> None:
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": max_rounds, "max_seconds": 300.0},
            looks={"n0": n0, "k": k, "max_looks": looks},
        )
    )
    assert late_looks(spec) == late


def test_a_schedule_outside_the_calibrated_ones_runs_to_a_verdict(
    project: Project,
) -> None:
    # Looks at 120, 122 and 124 rounds: three at 20 blocks or more.
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": 124, "max_seconds": 600.0},
            looks={"n0": 120, "k": 2, "max_looks": 3},
        )
    )
    result = project.run(project.candidate("same", {}), spec)
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 5)
    assert v.rounds >= 120
    warned = [
        w for w in result.record.warnings if w.code == "schedule_uncalibrated"
    ]
    assert len(warned) == 1
    assert "can never be a KEEP" in warned[0].message
    assert "schedule_uncalibrated" in result.report_path.read_text()


@pytest.mark.parametrize(
    ("looks", "max_rounds", "applicable", "blocked"),
    [
        ({"n0": 10, "k": 10, "max_looks": 6}, 60, False, False),
        ({"n0": 20, "k": 20, "max_looks": 6}, 120, False, False),
        ({"n0": 20, "k": 20, "max_looks": 10}, 200, False, True),
        ({"n0": 10, "k": 10, "max_looks": 6}, 60, True, True),
    ],
    ids=["python-default", "gpu-default", "late-looks", "guardrail"],
)
def test_a_kept_decision_is_withheld_off_the_calibrated_schedules(
    looks: dict[str, int], max_rounds: int, applicable: bool, blocked: bool
) -> None:
    guardrails = [
        {
            "metric": "peak_memory",
            "direction": "lower_is_better",
            "unit": "bytes",
            "applicable": False,
            "reason": "not used here",
        }
        if g["metric"] == "peak_memory" and not applicable
        else g
        for g in GUARDRAILS
    ]
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            looks=looks,
            budget={"max_rounds": max_rounds, "max_seconds": 600.0},
            guardrails=guardrails,
        )
    )
    assert keep_blocked(spec) is blocked


def test_the_default_schedules_have_no_late_look_warning(
    project: Project,
) -> None:
    result = project.run(project.candidate("same", {}))
    assert "schedule_uncalibrated" not in {
        w.code for w in result.record.warnings
    }


UNDERFLOW_BASELINE = (
    "def factor(i):\n"
    "    return 1.2345678901 * 10.0 ** (-260 + ((i * 7) % 11 - 5) * 3.5)\n"
)
UNDERFLOW_CANDIDATE = (
    "def factor(i):\n    return 1.0 + 0.0001 * ((i * 7) % 11)\n"
)


def test_an_unrepresentable_bound_is_inconclusive_with_its_cause(
    tmp_path: Path,
) -> None:
    # Ratios of 1e-260 spread over 35 decades: the lower bound
    # underflows, so the look has no interval, and the record says why.
    project = make_project(
        tmp_path, bench=WAVE_BENCH, baseline=UNDERFLOW_BASELINE
    )
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
            env={"WAVE_DIR": str(tmp_path)},
        )
    )
    candidate = project.candidate("same", {"work.py": UNDERFLOW_CANDIDATE})
    result = project.run(candidate, spec)
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        5,
    )
    assert (v.interval, v.estimate) == (None, None)
    assert v.rounds == 12
    codes = {w.code for w in result.record.warnings}
    assert "interval_unrepresentable" in codes
    assert "coarse_resolution" not in codes


EXTREME_BASELINE = (
    "def factor(i):\n"
    "    if i == {i} and {arm!r} == 'baseline':\n        return {value}\n"
    "    return 1.2345678e-5 * (1 + 0.002 * ((i * 7) % 11))\n"
)
EXTREME_CANDIDATE = (
    "import sys\n\n\ndef factor(i):\n"
    "    if i == {i} and {arm!r} == 'candidate':\n        return {value}\n"
    "    return 1.1111111e-5 * (1 + 0.002 * ((i * 7) % 11))\n"
)


def extreme_run(tmp_path: Path, arm: str, value: str) -> CompareResult:
    """One round prints ``value`` in ``arm``; the rest are ordinary."""
    project = make_project(
        tmp_path,
        bench=WAVE_BENCH,
        baseline=EXTREME_BASELINE.format(i=7, arm=arm, value=value),
    )
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
            env={"WAVE_DIR": str(tmp_path)},
        )
    )
    files = {"work.py": EXTREME_CANDIDATE.format(i=7, arm=arm, value=value)}
    return project.run(project.candidate("extreme", files), spec)


@pytest.mark.parametrize(
    ("arm", "value", "warning"),
    [
        ("candidate", "sys.float_info.max", "interval_unrepresentable"),
        ("baseline", "5e-324", "coarse_resolution"),
        ("candidate", "1.2345678901e305", "interval_unrepresentable"),
    ],
    ids=["float-max", "denormal", "ratio-below-normal"],
)
def test_one_extreme_value_gives_inconclusive_not_a_crash(
    tmp_path: Path, arm: str, value: str, warning: str
) -> None:
    result = extreme_run(tmp_path, arm, value)
    v = result.record.verdict
    assert v.outcome == "INCONCLUSIVE"
    assert v.interval is None
    assert warning in {w.code for w in result.record.warnings}
    assert result.record_path.is_file()
    assert read_record(result.record_path) == result.record


def test_a_huge_value_leaves_the_cv_finite(tmp_path: Path) -> None:
    result = extreme_run(tmp_path, "candidate", "1.2345678901e295")
    d = result.record.verdict.diagnostics
    assert d.cv_candidate is not None
    assert 0 < d.cv_candidate < 10
    assert d.sd_log_ratio is not None


def test_a_cv_that_is_not_finite_is_not_recorded(tmp_path: Path) -> None:
    result = extreme_run(tmp_path, "baseline", "5e-324")
    d = result.record.verdict.diagnostics
    assert d.cv_baseline is None or d.cv_baseline >= 0


def test_the_recorded_lag1_uses_the_order_of_each_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Any] = []
    real = compare_module.lag1_autocorrelation

    def spy(d: Any, first: Any) -> Any:
        seen.append(first)
        return real(d, first)

    monkeypatch.setattr(compare_module, "lag1_autocorrelation", spy)
    _, result = wave_run(tmp_path)
    with np.load(result.run_dir / SAMPLES_NAME, allow_pickle=False) as data:
        ran_first = data["candidate"][data["position"] == 0] == 1
    assert len(seen) == 1
    assert seen[0].tolist() == ran_first.tolist()
    assert 0 < int(ran_first.sum()) < 12


def test_a_guardrail_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked: list[Any] = []
    real = compare_module.decide_look

    def spy(*args: Any, **kwargs: Any) -> Look:
        if "keep_blocked" in kwargs:
            blocked.append(kwargs["keep_blocked"].tolist())
        return real(*args, **kwargs)

    monkeypatch.setattr(compare_module, "decide_look", spy)
    wave_run(tmp_path)
    assert blocked
    assert all(b == [True] for b in blocked)


def test_identical_ratios_record_no_lag1_warning(tmp_path: Path) -> None:
    flat = "def factor(i):\n    return 1.0\n"
    project = make_project(tmp_path, bench=WAVE_BENCH, baseline=flat)
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
            env={"WAVE_DIR": str(tmp_path)},
        )
    )
    result = project.run(project.candidate("flat", {"work.py": flat}), spec)
    codes = {w.code for w in result.record.warnings}
    assert "lag1_autocorrelation_high" not in codes
    assert result.record.verdict.diagnostics.lag1_autocorrelation is None


# Unknown is not wrong: measured, recorded, only a KEEP withheld


def unverified(result: CompareResult) -> dict[str, str]:
    """Return the gates that are not_available, with their detail."""
    return {
        g.gate: g.detail
        for g in result.record.gates
        if g.status == "not_available"
    }


def assert_measured_but_not_kept(result: CompareResult) -> None:
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.reason) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
    )
    assert verdict.rounds >= 2
    assert result.record.samples_path is not None
    assert "coverage" in unverified(result)


def wall_spec(**changes: Any) -> BenchmarkSpec:
    """A wall-time spec checking one declared output."""
    fields = spec_fields(
        metric={"kind": "wall", "direction": "lower_is_better", "unit": "s"},
        correctness={"outputs": [{"path": "out.txt", "compare": "text"}]},
        budget={"max_rounds": 12, "max_seconds": 300.0},
        looks={"n0": 12, "k": 2, "max_looks": 1},
        oracle=[],
        config=[],
        **changes,
    )
    return BenchmarkSpec.model_validate(fields)


def test_an_unidentified_runtime_is_measured_not_rejected(
    tmp_path: Path,
) -> None:
    run_sh = "#!/bin/sh\nexec python bench.py\n"
    project = make_project(tmp_path, executables={"run.sh": run_sh})
    spec = BenchmarkSpec.model_validate(
        spec_fields(command=["./run.sh"], harness=["bench.py", "run.sh"])
    )
    result = project.run(project.candidate("same", {}), spec)
    states = gates(result.record)
    assert states["runtime"] == "not_available"
    assert "is a shell" in unverified(result)["runtime"]
    assert states["correctness"] == "passed"
    assert_measured_but_not_kept(result)


def test_a_missing_correctness_check_is_not_available_not_passed(
    project: Project,
) -> None:
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            correctness={},
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    result = project.run(project.candidate("same", {}), spec)
    assert gates(result.record)["correctness"] == "not_available"
    assert "no correctness check" in unverified(result)["correctness"]
    assert_measured_but_not_kept(result)


def test_a_crash_is_still_rejected_without_a_correctness_check(
    project: Project,
) -> None:
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            correctness={},
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    broken = project.candidate("broken", {"work.py": FAILS})
    result = project.run(broken, spec)
    assert result.record.verdict.gate == "correctness"
    assert result.record.verdict.outcome == "REJECT"


def test_undeclared_default_guardrails_are_recorded_not_a_stop(
    project: Project,
) -> None:
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            objective=None,
            guardrails=[],
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    result = project.run(project.candidate("same", {}), spec)
    states = {g.metric: g.status for g in result.record.guardrails}
    assert states == {
        "peak_memory": "not_declared",
        "peak_gpu_memory": "not_declared",
        "p99_latency": "not_declared",
    }
    assert result.record.verdict.outcome == "INCONCLUSIVE"
    assert "not declared" in result.report_path.read_text()


def test_an_unreadable_host_identity_is_not_available_not_failed(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = fingerprint()
    blind = real.model_copy(
        update={
            "host_id": None,
            "missing": {**real.missing, "host_id": "no machine id here"},
        }
    )
    monkeypatch.setattr(compare_module, "fingerprint", lambda: blind)
    result = project.run(project.candidate("same", {}), twelve_rounds())
    assert gates(result.record)["same_host"] == "not_available"
    assert "no machine id here" in unverified(result)["same_host"]
    assert result.record.verdict.outcome == "INCONCLUSIVE"


def test_a_different_host_identity_still_fails(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = fingerprint()
    other = real.model_copy(update={"host_id": "somewhere-else"})
    calls = iter([real])
    monkeypatch.setattr(
        compare_module, "fingerprint", lambda: next(calls, other)
    )
    result = project.run(project.candidate("same", {}), twelve_rounds())
    assert result.record.verdict.gate == "same_host"
    assert result.record.verdict.outcome == "REJECT"


def other_interpreter() -> Path:
    """Return a working Python other than the one running the tests."""
    mine = Path(PY).resolve()
    for name in ("python3", "python3.12", "python3.13", "python3.14"):
        found = shutil.which(name)
        if found is None or Path(found).resolve() == mine:
            continue
        done = subprocess.run(
            [found, "-I", "-c", "pass"],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if done.returncode == 0:
            return Path(found).resolve()
    pytest.skip("no second Python interpreter is installed")


def test_different_identified_runtimes_are_still_rejected(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    venv = project.root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(Path(PY).resolve())
    candidate = project.candidate("other", {})
    link = candidate / ".venv" / "bin" / "python"
    link.unlink()
    link.symlink_to(other_interpreter())
    spec = BenchmarkSpec.model_validate(
        spec_fields(command=[".venv/bin/python", "bench.py"])
    )
    result = project.run(candidate, spec)
    assert result.record.verdict.gate == "runtime"
    assert result.record.verdict.outcome == "REJECT"
    assert result.record.verdict.reason == "integrity"


COMPILED_BENCH = """\
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    volatile long total = 0;
    for (long i = 0; i < 3000000; i++) total += i % 7;
    FILE *f = fopen("out.txt", "w");
    if (!f) return 1;
    fprintf(f, "%ld\\n", total);
    fclose(f);
    return 0;
}
"""
GO_STUB = """\
#include <unistd.h>

int main(void) {
    execl("./gobench", "gobench", (char *)0);
    return 127;
}
"""
MAKEFILE = "run: prog\n\t./prog\n\nprog: prog.c\n\tcc -O1 -o prog prog.c\n"
CARGO_TOML = (
    '[package]\nname = "rbench"\nversion = "0.1.0"\nedition = "2021"\n'
)
RUST_MAIN = """\
fn main() {
    let mut total: u64 = 0;
    for i in 0..3_000_000u64 {
        total = total.wrapping_add(std::hint::black_box(i % 7));
    }
    std::fs::write("out.txt", format!("{}\\n", total)).unwrap();
}
"""
NODE_BENCH = """\
let total = 0;
for (let i = 0; i < 3000000; i++) total += i % 7;
require("fs").writeFileSync("out.txt", total + "\\n");
"""


def compile_c(source: Path, out: Path) -> None:
    cc = shutil.which("cc")
    if cc is None:
        pytest.skip("no C compiler")
    subprocess.run(
        [cc, "-O1", "-o", str(out), str(source)], check=True, timeout=120
    )


def bare_project(tmp_path: Path, files: Mapping[str, str]) -> Project:
    root = tmp_path / "repo"
    root.mkdir()
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return Project(root=root, commit=commit_all(root), tmp=tmp_path)


def test_a_c_program_built_by_make_runs_to_an_inconclusive_estimate(
    tmp_path: Path,
) -> None:
    if shutil.which("make") is None:
        pytest.skip("no make")
    project = bare_project(
        tmp_path, {"Makefile": MAKEFILE, "prog.c": COMPILED_BENCH}
    )
    spec = wall_spec(
        command=["make", "-s", "run"], harness=["Makefile", "prog.c"]
    )
    result = project.run(project.candidate("same", {}), spec)
    states = gates(result.record)
    assert states["runtime"] == "not_available"
    assert states["correctness"] == "passed"
    assert_measured_but_not_kept(result)
    assert result.record.verdict.estimate is not None


def test_go_run_runs_to_an_inconclusive_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Go is not installed here: a compiled `go` stub execs a compiled
    # program, which is what `go run` does for perfproof's purposes.
    project = bare_project(
        tmp_path,
        {
            "prog.c": COMPILED_BENCH,
            "stub.c": GO_STUB,
            "main.go": "package main\n",
        },
    )
    compile_c(project.root / "prog.c", project.root / "gobench")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    compile_c(project.root / "stub.c", stubs / "go")
    monkeypatch.setenv(
        "PATH", f"{stubs}{os.pathsep}{os.environ.get('PATH', os.defpath)}"
    )
    spec = wall_spec(command=["go", "run", "."], harness=["main.go"])
    result = project.run(project.candidate("same", {}), spec)
    assert gates(result.record)["runtime"] == "not_available"
    assert gates(result.record)["correctness"] == "passed"
    assert_measured_but_not_kept(result)


def test_cargo_run_runs_to_an_inconclusive_estimate(tmp_path: Path) -> None:
    if shutil.which("cargo") is None:
        pytest.skip("no cargo")
    project = bare_project(
        tmp_path, {"Cargo.toml": CARGO_TOML, "src/main.rs": RUST_MAIN}
    )
    spec = wall_spec(
        command=["cargo", "run", "--offline", "--quiet"],
        harness=["Cargo.toml", "src/main.rs"],
    )
    result = project.run(project.candidate("same", {}), spec)
    assert gates(result.record)["runtime"] == "not_available"
    assert gates(result.record)["correctness"] == "passed"
    assert_measured_but_not_kept(result)


def test_a_node_script_runs_to_an_inconclusive_estimate(
    tmp_path: Path,
) -> None:
    if shutil.which("node") is None:
        pytest.skip("no node")
    project = bare_project(tmp_path, {"bench.js": NODE_BENCH})
    spec = wall_spec(command=["node", "bench.js"], harness=["bench.js"])
    result = project.run(project.candidate("same", {}), spec)
    assert gates(result.record)["runtime"] == "passed"
    assert gates(result.record)["correctness"] == "passed"
    assert_measured_but_not_kept(result)


def test_a_wrong_output_still_rejects_a_build_tool_benchmark(
    tmp_path: Path,
) -> None:
    if shutil.which("make") is None:
        pytest.skip("no make")
    project = bare_project(
        tmp_path, {"Makefile": MAKEFILE, "prog.c": COMPILED_BENCH}
    )
    spec = wall_spec(command=["make", "-s", "run"], harness=["Makefile"])
    wrong = project.candidate(
        "wrong", {"prog.c": COMPILED_BENCH.replace("i % 7", "i % 8")}
    )
    result = project.run(wrong, spec)
    assert result.record.verdict.gate == "correctness"
    assert result.record.verdict.outcome == "REJECT"


def test_a_missing_perf_falls_back_to_wall_time_and_says_so(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_perf(event: str, *, env: Mapping[str, str] | None = None) -> str:
        return "perf is not on PATH"

    monkeypatch.setattr(compare_module, "counter_unavailable_reason", no_perf)
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            metric={
                "kind": "counter",
                "direction": "lower_is_better",
                "unit": "instructions",
                "event": "instructions",
            },
            objective="instructions",
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    result = project.run(project.candidate("same", {}), spec)
    codes = {w.code for w in result.record.warnings}
    assert "counter_unavailable" in codes
    assert result.record.verdict.outcome == "INCONCLUSIVE"
    assert result.record.verdict.rounds >= 2


def test_a_reported_value_that_cannot_be_read_back_is_measured_anyway(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreadable(ex: object) -> None:
        return None

    monkeypatch.setattr(compare_module, "_printed_step", unreadable)
    result = project.run(project.candidate("same", {}), twelve_rounds())
    codes = {w.code for w in result.record.warnings}
    assert "resolution_unknown" in codes
    assert result.record.verdict.outcome == "INCONCLUSIVE"
    assert result.record.verdict.rounds >= 2


def test_the_deadline_ends_the_rounds_inconclusive(
    project: Project,
) -> None:
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": 200, "max_seconds": 3000.0},
            looks={"n0": 200, "k": 2, "max_looks": 1},
        )
    )
    candidate = project.candidate("same", {})
    result = compare(
        spec,
        build_manifest(spec, project.root),
        baseline_root=project.root,
        baseline_commit=project.commit,
        candidate_root=candidate,
        run_dir=project.run_dir(),
        process_env=os.environ,
        order_seed=7,
        deadline=time.time() + 16.0,
    )
    verdict = result.record.verdict
    assert verdict.outcome == "INCONCLUSIVE"
    assert 1 <= verdict.rounds < 200
    assert "time_limit_reached" in {w.code for w in result.record.warnings}


# Each condition that withholds a KEEP does so at every look, through
# the real compare, with the procedure forced calibrated.

NOT_APPLICABLE = [
    {
        "metric": m,
        "direction": "lower_is_better",
        "unit": u,
        "applicable": False,
        "reason": "not used by this workload",
    }
    for m, u in (
        ("peak_memory", "bytes"),
        ("peak_gpu_memory", "bytes"),
        ("p99_latency", "s"),
    )
]


DET_BENCH = """\
import os

from work import SCALE, run

n = int(open("n.txt").read()) if os.path.exists("n.txt") else 0
open("n.txt", "w").write(str(n + 1))
value = SCALE * (1 + 0.004 * ((n * 37) % 11 - 5))
with open("out.txt", "w") as f:
    f.write(f"{run()}\\n")
print("PERFPROOF {\\"value\\": %.6f}" % value)
"""


def det_work(scale: float) -> str:
    """A work module whose benchmark value is exactly ``scale`` times a
    fixed zigzag, so the ratio of two arms never varies."""
    return f"SCALE = {scale}\n\n\ndef run():\n    return 42\n"


def keepworthy_spec(**changes: Any) -> BenchmarkSpec:
    """One look on 12 rounds; guardrails declared not applicable."""
    fields = spec_fields(
        budget={"max_rounds": 12, "max_seconds": 300.0},
        looks={"n0": 12, "k": 2, "max_looks": 1},
        guardrails=NOT_APPLICABLE,
    )
    fields.update(changes)
    return BenchmarkSpec.model_validate(fields)


class Seen:
    """What `decide_look` was asked at each look of one run."""

    def __init__(self) -> None:
        self.blocked: list[bool] = []
        self.rules: list[int] = []
        self.held: list[bool] = []


def calibrated(
    monkeypatch: pytest.MonkeyPatch, *, coverage_passed: bool = True
) -> Seen:
    """Force CALIBRATED and coverage passed, and spy on looks."""
    monkeypatch.setattr(compare_module, "CALIBRATED", True)
    real_gate = compare_module.GateResult
    if coverage_passed:

        def gate(**fields: Any) -> GateResult:
            if fields.get("gate") == "coverage":
                fields = {**fields, "status": "passed", "detail": "forced"}
            return real_gate(**fields)

        monkeypatch.setattr(compare_module, "GateResult", gate)
    seen = Seen()
    real_look = compare_module.decide_look

    def spy(*args: Any, **kwargs: Any) -> Any:
        look = real_look(*args, **kwargs)
        if "keep_blocked" not in kwargs:
            return look
        seen.blocked.append(bool(kwargs["keep_blocked"][0]))
        seen.rules.append(int(look.rule[0]))
        seen.held.append(bool(look.held[0]))
        return look

    monkeypatch.setattr(compare_module, "decide_look", spy)
    return seen


def faster_candidate(project: Project) -> Path:
    return project.candidate("fast", {"work.py": det_work(0.5)})


def slow_baseline(tmp_path: Path, **kwargs: Any) -> Project:
    return make_project(
        tmp_path, bench=DET_BENCH, baseline=det_work(1.0), **kwargs
    )


def blocked_everywhere(seen: Seen) -> None:
    assert seen.blocked, "no look was taken"
    assert all(seen.blocked), seen.blocked
    assert all(seen.held), (seen.rules, seen.held)


def assert_withheld(result: CompareResult, *, blocker: str) -> None:
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.rule) == ("INCONCLUSIVE", 2)
    assert verdict.estimate is not None
    assert verdict.estimate > 1.03
    assert verdict.interval is not None
    assert verdict.interval.lower > 1.03
    codes = {w.code for w in result.record.warnings}
    states = {g.gate: g.status for g in result.record.gates}
    shown = {g.metric: g.status for g in result.record.guardrails}
    assert (
        blocker in codes
        or states.get(blocker) == "not_available"
        or shown.get(blocker) == "not_declared"
    ), (blocker, codes, states, shown)


def test_control_nothing_unverified_is_not_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch)
    project = slow_baseline(tmp_path)
    spec = keepworthy_spec()
    with pytest.raises(CompareStoppedError) as stopped:
        project.run(faster_candidate(project), spec)
    # A KEEP is not built in the thin path: the look would have kept.
    assert stopped.value.code == "internal_fault"
    assert seen.blocked == [False]
    assert seen.rules == [2]
    assert seen.held == [False]


def test_an_unidentified_runtime_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch)
    project = slow_baseline(
        tmp_path, executables={"run.sh": "#!/bin/sh\nexec python bench.py\n"}
    )
    spec = keepworthy_spec(
        command=["./run.sh"], harness=["bench.py", "run.sh"]
    )
    result = project.run(faster_candidate(project), spec)
    blocked_everywhere(seen)
    assert_withheld(result, blocker="runtime")


def test_an_unreadable_host_identity_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch)
    real = fingerprint()
    blind = real.model_copy(
        update={"host_id": None, "missing": {**real.missing, "host_id": "x"}}
    )
    monkeypatch.setattr(compare_module, "fingerprint", lambda: blind)
    project = slow_baseline(tmp_path)
    result = project.run(faster_candidate(project), keepworthy_spec())
    blocked_everywhere(seen)
    assert_withheld(result, blocker="same_host")


def test_a_host_identity_lost_after_the_last_look_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch)
    real = fingerprint()
    blind = real.model_copy(
        update={"host_id": None, "missing": {**real.missing, "host_id": "x"}}
    )
    calls = iter([real])
    monkeypatch.setattr(
        compare_module, "fingerprint", lambda: next(calls, blind)
    )
    project = slow_baseline(tmp_path)
    result = project.run(faster_candidate(project), keepworthy_spec())
    # Nothing blocked at the look: the host was readable then.
    assert seen.blocked == [False]
    assert_withheld(result, blocker="same_host")
    assert gates(result.record)["same_host"] == "not_available"


def test_no_correctness_check_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch)
    project = slow_baseline(tmp_path)
    result = project.run(
        faster_candidate(project), keepworthy_spec(correctness={})
    )
    blocked_everywhere(seen)
    assert_withheld(result, blocker="correctness")


def test_coverage_not_available_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch, coverage_passed=False)
    project = slow_baseline(tmp_path)
    result = project.run(faster_candidate(project), keepworthy_spec())
    blocked_everywhere(seen)
    assert_withheld(result, blocker="coverage")


def test_an_undeclared_guardrail_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch)
    project = slow_baseline(tmp_path)
    spec = keepworthy_spec(objective=None, guardrails=[])
    result = project.run(faster_candidate(project), spec)
    blocked_everywhere(seen)
    assert_withheld(result, blocker="peak_memory")


def test_a_counter_fallback_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch)

    def no_perf(event: str, *, env: Mapping[str, str] | None = None) -> str:
        return "perf is not on PATH"

    monkeypatch.setattr(compare_module, "counter_unavailable_reason", no_perf)
    # Wall time of the whole command: the work itself must dominate it.
    project = make_project(tmp_path, baseline=work(0.4))
    spec = keepworthy_spec(
        metric={
            "kind": "counter",
            "direction": "lower_is_better",
            "unit": "instructions",
            "event": "instructions",
        },
        objective="instructions",
    )
    result = project.run(
        project.candidate("fast", {"work.py": work(0.02)}), spec
    )
    # Real wall times: whether the look held a rule 2 depends on the
    # noise, but that the block was on at every look does not.
    assert seen.blocked
    assert all(seen.blocked)
    assert result.record.verdict.outcome == "INCONCLUSIVE"
    assert "counter_unavailable" in {w.code for w in result.record.warnings}
    record = result.record
    assert record.spec.metric.kind == "counter"
    assert record.measured_metric is not None
    assert (record.measured_metric.kind, record.measured_metric.unit) == (
        "wall",
        "s",
    )
    assert "`wall` in `s`" in result.report_path.read_text()


def test_an_unknown_resolution_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = calibrated(monkeypatch)

    def unreadable(ex: object) -> None:
        return None

    monkeypatch.setattr(compare_module, "_printed_step", unreadable)
    project = slow_baseline(tmp_path)
    result = project.run(faster_candidate(project), keepworthy_spec())
    blocked_everywhere(seen)
    assert_withheld(result, blocker="resolution_unknown")


def test_a_held_look_ends_the_run_at_that_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # More budget remains, and nothing in the run can lift the block.
    seen = calibrated(monkeypatch)
    project = slow_baseline(tmp_path)
    spec = keepworthy_spec(
        correctness={},
        budget={"max_rounds": 40, "max_seconds": 300.0},
        looks={"n0": 12, "k": 12, "max_looks": 3},
    )
    result = project.run(faster_candidate(project), spec)
    assert seen.blocked == [True]
    assert seen.held == [True]
    assert result.record.verdict.rounds == 12
    assert result.record.verdict.looks == 1
    assert result.record.verdict.rule == 2


# A launch failure of only one arm means the arms do not run the same
# program: the runtime gate, not the correctness gate.


def test_a_candidate_that_cannot_start_fails_the_runtime_gate(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path, executables={"run.py": RUN_PY})
    candidate = project.candidate("no-run", {})
    (candidate / "run.py").unlink()
    spec = BenchmarkSpec.model_validate(spec_fields(command=["./run.py"]))
    result = project.run(candidate, spec)
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.reason, verdict.gate) == (
        "REJECT",
        "integrity",
        "runtime",
    )
    gate = {g.gate: g for g in result.record.gates}["runtime"]
    assert "do not run the same program" in gate.detail


def test_a_program_only_the_baseline_has_fails_the_runtime_gate(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    venv = project.root / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(Path(PY).resolve())
    candidate = project.candidate("novenv", {})
    shutil.rmtree(candidate / ".venv")
    spec = BenchmarkSpec.model_validate(
        spec_fields(command=[".venv/bin/python", "bench.py"])
    )
    result = project.run(candidate, spec)
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.reason, verdict.gate) == (
        "REJECT",
        "integrity",
        "runtime",
    )


def test_a_candidate_that_starts_and_exits_non_zero_is_correctness(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    broken = project.candidate("broken", {"work.py": FAILS})
    result = project.run(broken, twelve_rounds())
    verdict = result.record.verdict
    assert (verdict.reason, verdict.gate) == ("correctness", "correctness")


def test_the_launch_rule_is_waived_with_the_runtime_opt_in(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path, executables={"run.py": RUN_PY})
    candidate = project.candidate("no-run", {})
    (candidate / "run.py").unlink()
    spec = BenchmarkSpec.model_validate(
        spec_fields(command=["./run.py"], allow_runtime_change=True)
    )
    result = project.run(candidate, spec)
    assert result.record.verdict.gate == "correctness"


# A launcher that may download code is measured and not kept; the
# stubs here are inert scripts, so nothing touches the network.


def stub_launcher(tmp_path: Path, name: str, body: str) -> Path:
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    exe = stubs / name
    exe.write_text(f"#!/bin/sh\n{body}\n")
    exe.chmod(0o755)
    return stubs


def test_a_benchmark_launcher_that_may_download_is_measured_not_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stubs = stub_launcher(tmp_path, "npx", "exec python bench.py")
    monkeypatch.setenv(
        "PATH", f"{stubs}{os.pathsep}{os.environ.get('PATH', os.defpath)}"
    )
    project = make_project(tmp_path)
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            command=["npx", "some-benchmark-tool"],
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    result = project.run(project.candidate("same", {}), spec)
    assert gates(result.record)["runtime"] == "not_available"
    assert "may download code at run time" in unverified(result)["runtime"]
    assert result.record.verdict.outcome == "INCONCLUSIVE"
    assert result.record.verdict.rounds >= 2


def test_a_test_launcher_that_may_download_is_recorded_not_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stubs = stub_launcher(tmp_path, "pnpx", 'exec python -m pytest -q "$@"')
    monkeypatch.setenv(
        "PATH", f"{stubs}{os.pathsep}{os.environ.get('PATH', os.defpath)}"
    )
    project = make_project(tmp_path)
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            correctness={
                "command": ["pnpx", "pytest"],
                "timeout_seconds": 60.0,
                "outputs": [{"path": "out.txt", "compare": "text"}],
            },
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    result = project.run(project.candidate("same", {}), spec)
    assert "test command's launcher" in unverified(result)["runtime"]
    assert result.record.verdict.outcome == "INCONCLUSIVE"


# The metric kind is journaled, so a resume never mixes units


def test_a_resume_under_another_metric_kind_is_not_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from perfproof.bench import runner as runner_module
    from perfproof.bench.journal import (
        MACHINE_NAME,
        METRIC_NAME,
        Journal,
        Resumed,
        load_journal,
        read_json,
    )

    spec = BenchmarkSpec.model_validate(
        spec_fields(
            metric={
                "kind": "counter",
                "direction": "lower_is_better",
                "unit": "instructions",
                "event": "instructions",
            },
            objective="instructions",
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    project = make_project(tmp_path)
    candidate = project.candidate("same", {})
    run_dir = project.run_dir()
    run_dir.mkdir(parents=True)
    lock = build_manifest(spec, project.root)

    def no_perf(event: str, *, env: Mapping[str, str] | None = None) -> str:
        return "perf unavailable in attempt 1"

    monkeypatch.setattr(compare_module, "counter_unavailable_reason", no_perf)
    real_round = runner_module.Runner.run_round

    def dying(self: Runner, index: int) -> Round:
        if index == 4:
            raise KeyboardInterrupt
        return real_round(self, index)

    monkeypatch.setattr(runner_module.Runner, "run_round", dying)
    journal = Journal(run_dir, spec.budget.max_rounds)
    with pytest.raises(KeyboardInterrupt):
        compare(
            spec,
            lock,
            baseline_root=project.root,
            baseline_commit=project.commit,
            candidate_root=candidate,
            run_dir=run_dir,
            process_env=os.environ,
            order_seed=7,
            journal=journal,
        )
    journal.close()
    monkeypatch.setattr(runner_module.Runner, "run_round", real_round)
    assert read_json(run_dir / METRIC_NAME) == {"kind": "wall"}
    rounds = load_journal(run_dir, repair=True)
    assert len(rounds) == 4

    def perf_ok(event: str, *, env: Mapping[str, str] | None = None) -> None:
        return None

    monkeypatch.setattr(compare_module, "counter_unavailable_reason", perf_ok)
    doc = read_json(run_dir / MACHINE_NAME)
    assert doc is not None
    saved = MachineFingerprint.model_validate(doc["machine"])
    started = datetime.fromisoformat(str(doc["started_at"]))
    resumed = Resumed(
        rounds=tuple(rounds),
        machine=saved,
        started=started,
        attempt=1,
        metric_kind="wall",
    )
    journal = Journal(run_dir, 12, resume_after=len(rounds))
    try:
        with pytest.raises(CompareStoppedError) as stopped:
            compare(
                spec,
                lock,
                baseline_root=project.root,
                baseline_commit=project.commit,
                candidate_root=candidate,
                run_dir=run_dir,
                process_env=os.environ,
                order_seed=7,
                journal=journal,
                resume=resumed,
            )
    finally:
        journal.close()
    assert stopped.value.code == "not_resumable"
    assert "wall" in stopped.value.message
    assert "counter" in stopped.value.message


# The deadline


@pytest.mark.parametrize("deadline", [0.0, -1.0, float("inf"), float("nan")])
def test_a_deadline_must_be_positive_and_finite(
    project: Project, deadline: float
) -> None:
    spec = twelve_rounds()
    with pytest.raises(ValueError, match="deadline"):
        compare(
            spec,
            build_manifest(spec, project.root),
            baseline_root=project.root,
            baseline_commit=project.commit,
            candidate_root=project.candidate("same", {}),
            run_dir=project.run_dir(),
            process_env=os.environ,
            order_seed=7,
            deadline=deadline,
        )
    assert not project.run_dir().exists()


def test_the_deadline_warning_says_how_many_rounds_the_look_used(
    project: Project,
) -> None:
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": 200, "max_seconds": 3000.0},
            looks={"n0": 200, "k": 2, "max_looks": 1},
        )
    )
    result = compare(
        spec,
        build_manifest(spec, project.root),
        baseline_root=project.root,
        baseline_commit=project.commit,
        candidate_root=project.candidate("same", {}),
        run_dir=project.run_dir(),
        process_env=os.environ,
        order_seed=7,
        deadline=time.time() + 16.0,
    )
    warning = next(
        w for w in result.record.warnings if w.code == "time_limit_reached"
    )
    rounds = result.record.verdict.rounds
    assert f"after {rounds} rounds" in warning.message
    assert "final look used" in warning.message or "too few" in warning.message


PERF_STUB = """#!/bin/sh
# inert stand-in for: perf stat -x, -e EVENT -o FILE -- CMD ARGS...
event="$4"; out="$6"
shift 7
"$@"; rc=$?
echo "1000000,,$event,1000,100.00,," > "$out"
exit $rc
"""


def test_three_attempts_with_perf_flipping_never_mix_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from perfproof.bench import runner as runner_module
    from perfproof.bench.journal import (
        MACHINE_NAME,
        METRIC_NAME,
        Journal,
        Resumed,
        load_journal,
        read_json,
    )

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    perf = stubs / "perf"
    perf.write_text(PERF_STUB)
    perf.chmod(0o755)
    monkeypatch.setenv(
        "PATH", f"{stubs}{os.pathsep}{os.environ.get('PATH', os.defpath)}"
    )
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            metric={
                "kind": "counter",
                "direction": "lower_is_better",
                "unit": "instructions",
                "event": "instructions",
            },
            objective="instructions",
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    project = make_project(tmp_path)
    candidate = project.candidate("same", {})
    run_dir = project.run_dir()
    run_dir.mkdir(parents=True)
    lock = build_manifest(spec, project.root)
    real_round = runner_module.Runner.run_round

    def perf_is(usable: bool) -> None:
        def reason(
            event: str, *, env: Mapping[str, str] | None = None
        ) -> str | None:
            return None if usable else "perf is not usable in this attempt"

        monkeypatch.setattr(
            compare_module, "counter_unavailable_reason", reason
        )
        monkeypatch.setattr(
            runner_module, "counter_unavailable_reason", reason
        )

    def die_at(index: int) -> None:
        def dying(self: Runner, i: int) -> Round:
            if i == index:
                raise KeyboardInterrupt
            return real_round(self, i)

        monkeypatch.setattr(runner_module.Runner, "run_round", dying)

    def attempt(number: int) -> None:
        resume = None
        rounds = load_journal(run_dir, repair=True) if number else []
        if number:
            doc = read_json(run_dir / MACHINE_NAME)
            assert doc is not None
            recorded = read_json(run_dir / METRIC_NAME)
            kind = None if recorded is None else recorded.get("kind")
            resume = Resumed(
                rounds=tuple(rounds),
                machine=MachineFingerprint.model_validate(doc["machine"]),
                started=datetime.fromisoformat(str(doc["started_at"])),
                attempt=number,
                metric_kind=kind if isinstance(kind, str) else None,
            )
        journal = Journal(run_dir, 12, resume_after=len(rounds))
        try:
            compare(
                spec,
                lock,
                baseline_root=project.root,
                baseline_commit=project.commit,
                candidate_root=candidate,
                run_dir=run_dir,
                process_env=os.environ,
                order_seed=7,
                journal=journal,
                resume=resume,
            )
        finally:
            journal.close()

    perf_is(False)
    die_at(0)
    with pytest.raises(KeyboardInterrupt):
        attempt(0)
    assert read_json(run_dir / METRIC_NAME) == {"kind": "wall"}
    assert load_journal(run_dir, repair=True) == []

    perf_is(True)
    die_at(3)
    with pytest.raises(KeyboardInterrupt):
        attempt(1)
    # The first attempt kept no round, so this one decides the kind.
    assert read_json(run_dir / METRIC_NAME) == {"kind": "counter"}
    assert len(load_journal(run_dir, repair=True)) == 3

    perf_is(False)
    monkeypatch.setattr(runner_module.Runner, "run_round", real_round)
    with pytest.raises(CompareStoppedError) as stopped:
        attempt(2)
    assert stopped.value.code == "not_resumable"
    assert "counter" in stopped.value.message
    assert "wall" in stopped.value.message


def test_not_verified_lists_gates_guardrails_and_withholding_warnings(
    project: Project,
) -> None:
    from perfproof.commands._runs import not_verified, warnings_json
    from perfproof.schema import RunWarning

    spec = BenchmarkSpec.model_validate(
        spec_fields(
            correctness={},
            guardrails=[GUARDRAILS[0]],
            budget={"max_rounds": 12, "max_seconds": 300.0},
            looks={"n0": 12, "k": 2, "max_looks": 1},
        )
    )
    record = project.run(project.candidate("same", {}), spec).record
    withholding = ("counter_unavailable", "resolution_unknown")
    extra = (
        *(RunWarning(code=c, message=f"{c} text") for c in withholding),
        RunWarning(code="schedule_uncalibrated", message="late looks"),
        RunWarning(code="high_load", message="not a blocker"),
    )
    shown = record.model_copy(update={"warnings": (*record.warnings, *extra)})

    entries = cast("list[dict[str, Any]]", not_verified(shown))
    items = {(e["kind"], e["name"]): e for e in entries}

    assert items[("gate", "correctness")]["status"] == "not_available"
    assert items[("gate", "coverage")]["reason"]
    assert items[("guardrail", "peak_memory")]["status"] == "unmeasurable"
    assert items[("guardrail", "peak_gpu_memory")]["status"] == "not_declared"
    assert items[("guardrail", "p99_latency")]["status"] == "not_declared"
    for code in (*withholding, "schedule_uncalibrated"):
        entry = items[("warning", code)]
        assert entry["status"] == "withholds_keep"
        assert entry["reason"]
    assert ("warning", "high_load") not in items
    codes = {
        w["code"] for w in cast("list[dict[str, Any]]", warnings_json(shown))
    }
    assert codes >= {"high_load", "schedule_uncalibrated"}
