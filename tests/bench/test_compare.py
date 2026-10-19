import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from perfproof.bench import compare as compare_module
from perfproof.bench.compare import (
    SAMPLES_NAME,
    CompareResult,
    CompareStoppedError,
    compare,
)
from perfproof.bench.lock import build_manifest
from perfproof.bench.runner import Round, Runner, RunnerError
from perfproof.machine import fingerprint
from perfproof.report import read_record
from perfproof.schema import (
    BenchmarkSpec,
    Interval,
    MachineFingerprint,
    RunRecord,
)
from perfproof.stats.sequential import Look, look_confidence

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


def test_passing_gates_end_inconclusive_with_the_interval(
    project: Project,
) -> None:
    faster = project.candidate("fast", {"work.py": work(0.005)})
    result = project.run(faster)
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
    assert (v.rounds, v.looks) == (10, 1)
    assert v.interval is not None
    assert v.estimate is not None
    assert 1 < v.interval.lower <= v.estimate <= v.interval.upper
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
    ("max_rounds", "k", "max_looks", "looks", "rounds"),
    [
        (13, 2, 3, 2, 13),
        (12, 2, 3, 2, 12),
        (20, 4, 2, 2, 14),
        (11, 2, 2, 1, 11),
    ],
    ids=["odd-budget", "budget-on-a-look", "kth-look", "re-decided-final"],
)
def test_looks_follow_the_schedule_and_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
            looks={"n0": 10, "k": k, "max_looks": max_looks},
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
            looks={"n0": 10, "k": 2, "max_looks": 3},
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
        if index == 10:
            skipped[0] = 1e6
        return done

    monkeypatch.setattr(Runner, "run_round", slow_after_n0)
    decided = spy_on_looks(monkeypatch)
    result = project.run(project.candidate("same", {}), spec)
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 5)
    assert (v.looks, v.rounds) == (1, 11)
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
    spec = BenchmarkSpec.model_validate(spec_fields(reset=RESET))
    result = project.run(candidate, spec)
    v = result.record.verdict
    assert (v.outcome, v.rule, v.rounds) == ("INCONCLUSIVE", 5, 10)
    assert v.interval is not None
    assert set(gates(result.record).values()) <= {"passed", "not_available"}
    # Per tree: the output-producing execution, one warmup, ten rounds.
    for tree in (project.root, candidate):
        assert events(tree) == ["reset", "bench"] * 12


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
    assert "the baseline runtime is unknown" in stopped.value.message
    assert not (project.run_dir() / "record.json").exists()


def test_candidate_that_cannot_start_is_rejected(tmp_path: Path) -> None:
    project = make_project(tmp_path, executables={"run.py": RUN_PY})
    candidate = project.candidate("no-run", {})
    (candidate / "run.py").unlink()
    spec = BenchmarkSpec.model_validate(spec_fields(command=["./run.py"]))
    result = project.run(candidate, spec)
    v = result.record.verdict
    assert (v.outcome, v.reason, v.gate) == ("REJECT", "integrity", "runtime")
    runtime = {g.gate: g for g in result.record.gates}["runtime"]
    assert "candidate runtime is unknown" in runtime.detail
    nothing_ran(project, project.root, candidate)


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


@pytest.mark.parametrize(
    ("changes", "code"),
    [({"objective": None, "guardrails": []}, "undeclared_guardrails")],
    ids=["no-guardrails"],
)
def test_specs_the_thin_path_cannot_run_are_refused_first(
    project: Project, changes: dict[str, Any], code: str
) -> None:
    budget = {"max_rounds": 12, "max_seconds": 300.0}
    spec = BenchmarkSpec.model_validate(spec_fields(budget=budget, **changes))
    candidate = project.candidate("same", {})
    with pytest.raises(CompareStoppedError) as stopped:
        project.run(candidate, spec)
    assert stopped.value.code == code
    nothing_ran(project, project.root, candidate)


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
        budget={"max_rounds": 10, "max_seconds": 600},
        looks={"n0": 10, "k": 10, "max_looks": 1},
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
    record = read_record(Path(result["record_path"]))
    assert record.verdict.model_dump(mode="json") == v
    assert Path(result["report_path"]).is_file()
    assert Path(result["run_dir"]).parent == root / ".perfproof" / "runs"
    assert {g["gate"]: g["status"] for g in result["gates"]}["coverage"] == (
        "not_available"
    )
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
