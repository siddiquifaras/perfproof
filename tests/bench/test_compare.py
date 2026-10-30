import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import numpy as np
import pytest
from pydantic import ValidationError

from perfproof.bench import compare as compare_module
from perfproof.bench.compare import (
    PEAK_MEMORY_MEASURE,
    REPORTED_MEASURE,
    SAMPLES_NAME,
    CompareResult,
    CompareStoppedError,
    compare,
    keep_blocked,
    late_looks,
)
from perfproof.bench.lock import build_manifest
from perfproof.bench.runner import Round, Runner, RunnerError
from perfproof.bench.snapshot import add_baseline_worktree
from perfproof.commands._runs import not_verified
from perfproof.machine import fingerprint
from perfproof.project.store import model_path
from perfproof.report import read_record
from perfproof.schema import (
    KEEP_BLOCKING_WARNINGS,
    BenchmarkSpec,
    Confirmation,
    Direction,
    EntryStatus,
    FileHash,
    GateResult,
    GoalBar,
    GoalEntry,
    Interval,
    Invalidation,
    MachineFingerprint,
    ProjectModel,
    Provenance,
    RunRecord,
    TargetClass,
    TargetEntry,
    WorkloadEntry,
)
from perfproof.stats.intervals import hac_lf_interval, t_interval
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


type Targets = (
    Literal["spec", "none", "keep"]
    | tuple[tuple[TargetClass, EntryStatus], ...]
)
"""The project model a run sees: ``"spec"`` confirms the spec's own
``target_class`` (none if it declares none), ``"none"`` writes no model,
``"keep"`` leaves the model the test wrote, a tuple lists the targets
and their states."""


def write_model(
    root: Path,
    targets: tuple[tuple[TargetClass, str], ...],
    evidence: Mapping[str, str] | None = None,
    *,
    workload: BenchmarkSpec | None = None,
    workload_status: Literal["confirmed", "proposed"] = "confirmed",
    goal: tuple[str, str, str] = ("wall_time", "lower_is_better", "s"),
    workload_on: TargetClass | None = None,
    bound: bool = True,
) -> None:
    """Write a project model holding ``targets`` under ``root``.

    ``evidence`` maps file paths to contents: each is written under
    ``root`` and recorded in every target's provenance with its hash, as
    the files the target was derived from. ``workload``, when given,
    adds a workload named like that spec, on the target of the spec's
    class (else the first) and frozen at its hash, with a goal; both are
    in ``workload_status``, which is what `compare` requires of the
    benchmark's workload. ``workload_on`` puts it on the target of that
    class instead; ``bound=False`` leaves it with no ``spec_hash``.
    """
    when = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    files = tuple(
        FileHash(
            path=name,
            sha256=hashlib.sha256(text.encode()).hexdigest(),
        )
        for name, text in (evidence or {}).items()
    )
    for name, text in (evidence or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    provenance = Provenance(
        files=files, commands=(), perfproof_version="0.1.0", derived_at=when
    )
    entries = tuple(
        TargetEntry(
            id=f"target-{cls}-{i}",
            status=cast("EntryStatus", status),
            provenance=provenance,
            confirmation=(
                Confirmation(session="test", confirmed_at=when)
                if status in ("confirmed", "stale")
                else None
            ),
            previous_status="confirmed" if status == "stale" else None,
            invalidation=(
                Invalidation(
                    trigger="file_changed", detail="x", detected_at=when
                )
                if status == "stale"
                else None
            ),
            retired_reason="gone" if status == "retired" else None,
            target_class=cls,
            evidence=("a test target",),
        )
        for i, (cls, status) in enumerate(targets)
    )
    workloads: tuple[WorkloadEntry, ...] = ()
    goals: tuple[GoalEntry, ...] = ()
    if workload is not None and entries:
        provenance = Provenance(
            files=(), commands=(), perfproof_version="0.1.0", derived_at=when
        )
        confirmation = (
            Confirmation(session="test", confirmed_at=when)
            if workload_status == "confirmed"
            else None
        )
        own = next(
            (
                t
                for t in entries
                if t.target_class == (workload_on or workload.target_class)
            ),
            entries[0],
        )
        workloads = (
            WorkloadEntry(
                id=workload.name,
                target_id=own.id,
                source="agent_defined",
                command=workload.command,
                cwd=workload.cwd,
                spec_hash=workload.spec_hash if bound else None,
                status=workload_status,
                provenance=provenance,
                confirmation=confirmation,
            ),
        )
        goals = (
            GoalEntry(
                id=f"goal-{workload.name}",
                workload_id=workload.name,
                metric=goal[0],
                direction=cast("Direction", goal[1]),
                unit=goal[2],
                bar=GoalBar(kind="ratio", value=1.05),
                guardrails=(),
                status=workload_status,
                provenance=provenance,
                confirmation=confirmation,
            ),
        )
    model = ProjectModel(
        updated_at=when, targets=entries, workloads=workloads, goals=goals
    )
    path = model_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(), encoding="utf-8")


@dataclass
class Project:
    root: Path
    commit: str
    tmp: Path
    runs: int = 0

    @property
    def model_root(self) -> Path:
        """Where the project model lives, apart from the trees."""
        return self.tmp / "model-root"

    def candidate(self, name: str, files: Mapping[str, str]) -> Path:
        tree = self.tmp / f"candidate-{name}"
        shutil.copytree(self.root, tree, ignore=shutil.ignore_patterns(".git"))
        for rel, text in files.items():
            (tree / rel).write_text(text)
        return tree

    def run(
        self,
        candidate: Path,
        spec: BenchmarkSpec | None = None,
        *,
        targets: Targets = "spec",
        project_root: Literal["model", "none"] = "model",
    ) -> CompareResult:
        spec = spec or BenchmarkSpec.model_validate(spec_fields())
        self.runs += 1
        if targets == "spec":
            if spec.target_class is not None:
                write_model(
                    self.model_root,
                    ((spec.target_class, "confirmed"),),
                    workload=spec,
                )
        elif targets not in ("none", "keep"):
            write_model(self.model_root, targets, workload=spec)
        return compare(
            spec,
            build_manifest(spec, self.root),
            baseline_root=self.root,
            baseline_commit=self.commit,
            candidate_root=candidate,
            run_dir=self.run_dir(),
            process_env=os.environ,
            order_seed=7,
            project_root=self.model_root if project_root == "model" else None,
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
    assert (v.outcome, v.reason, v.gate) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        None,
    )
    # CALIBRATED is True since 2026-10-28. This spec (12 rounds, one
    # look, no class) is outside the calibrated domain and the coverage
    # gate is not_available, so the KEEP is withheld: INCONCLUSIVE under
    # rule 2 or rule 5, which the machine's load decides, so the rule is
    # not pinned. `calibration` stays "uncalibrated".
    assert not v.diagnostics.procedure_uncalibrated
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
    states = {g.metric: g.status for g in record.guardrails}
    # Twelve rounds leave two blocks, so whether the interval of the
    # memory ratio clears the allowance depends on page-level jitter;
    # either way it was measured, and the evidence is recorded.
    assert states.pop("peak_memory") in (
        "passed",
        "not_established",
        "unmeasurable",
    )
    assert states == {
        "peak_gpu_memory": "not_applicable",
        "p99_latency": "not_applicable",
    }
    memory = record.guardrails[0]
    assert memory.measure == PEAK_MEMORY_MEASURE
    if memory.status != "unmeasurable":
        assert memory.estimate is not None
        assert memory.worsening is not None
        lower, upper = memory.worsening.lower, memory.worsening.upper
        assert lower <= memory.estimate <= upper
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
    # Deterministic values with a large fixed spread (`NOISY_BENCH`), so
    # every look's interval straddles the bar on every run and the run
    # goes to the budget; real timings of an identical candidate can
    # chance-REJECT now that rule 3 decides.
    project = make_project(
        tmp_path, bench=NOISY_BENCH, baseline=noisy_work(37)
    )
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            budget={"max_rounds": max_rounds, "max_seconds": 600.0},
            looks={"n0": n0, "k": k, "max_looks": max_looks},
        )
    )
    decided = spy_on_looks(monkeypatch)
    result = project.run(
        project.candidate("other-order", {"work.py": noisy_work(53)}), spec
    )
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
    project = make_project(
        tmp_path, bench=NOISY_BENCH, baseline=noisy_work(37)
    )
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
    result = project.run(
        project.candidate("other-order", {"work.py": noisy_work(53)}), spec
    )
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
    # The KEEP is withheld (spec outside the domain): INCONCLUSIVE under
    # rule 2 or 5, which load decides, so the rule is not pinned.
    assert (v.outcome, v.rounds) == ("INCONCLUSIVE", 12)
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


def test_cli_rolling_fix_is_inconclusive_with_its_blockers(
    tmp_path: Path, rolling: tuple[Path, str, str]
) -> None:
    root, thin, _ = rolling
    fix = example_candidate(tmp_path, ROLLING, "fix")
    result = compare_cli(root, thin, fix)["result"]
    v = result["verdict"]
    # The KEEP is withheld (schedule outside the domain, coverage
    # not_available): rule 2 or rule 5 by load; the rule is not pinned.
    assert (v["outcome"], v["reason"]) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
    )
    assert v["diagnostics"]["procedure_uncalibrated"] is False
    # The fix is a real gain, but load on the machine running the suite
    # can widen the interval past the bar; the estimate shows the gain.
    assert v["estimate"] > 1.03
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
        **{
            "budget": {"max_rounds": 12, "max_seconds": 300.0},
            "looks": {"n0": 12, "k": 2, "max_looks": 1},
            "env": {"WAVE_DIR": str(tmp_path)},
            **(spec_changes or {}),
        }
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
    # The procedure is calibrated now, so more rounds can lift the gate.
    assert "no number of rounds can change" not in report
    assert "More rounds, or less dependence" in report
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
    # An identical candidate on real timings can chance-REJECT; what is
    # guaranteed is no KEEP. The test is about the warning below.
    assert v.outcome != "KEEP"
    assert v.rounds >= 120
    warned = [
        w for w in result.record.warnings if w.code == "schedule_uncalibrated"
    ]
    assert len(warned) == 1
    assert "can never be a KEEP" in warned[0].message
    assert "schedule_uncalibrated" in result.report_path.read_text()


@pytest.mark.parametrize(
    ("looks", "max_rounds", "guardrail", "unit", "blocked"),
    [
        ({"n0": 10, "k": 10, "max_looks": 6}, 60, "none", "", False),
        # gpu_kernels has no real traces: withheld since 2026-10-28.
        ({"n0": 20, "k": 20, "max_looks": 6}, 120, "none", "", True),
        ({"n0": 20, "k": 20, "max_looks": 10}, 200, "none", "", True),
        (
            {"n0": 10, "k": 10, "max_looks": 6},
            60,
            "peak_memory",
            "bytes",
            False,
        ),
        ({"n0": 10, "k": 10, "max_looks": 6}, 60, "p99_latency", "s", False),
        ({"n0": 10, "k": 10, "max_looks": 6}, 60, "peak_memory", "MB", True),
    ],
    ids=[
        "python-default",
        "gpu-default",
        "late-looks",
        "measured-guardrail",
        "reported-guardrail",
        "unmeasurable-guardrail",
    ],
)
def test_a_kept_decision_is_withheld_off_the_calibrated_schedules(
    looks: dict[str, int],
    max_rounds: int,
    guardrail: str,
    unit: str,
    blocked: bool,
) -> None:
    """A guardrail with a value to measure does not block by itself.

    ``peak_memory`` is decided at each look from its measurements, and
    a reported one such as ``p99_latency`` from the values the benchmark
    prints; ``peak_memory`` in a unit other than bytes can never be
    shown compliant, so the spec rules out a KEEP.
    """
    guardrails: list[dict[str, Any]] = [
        {
            "metric": g["metric"],
            "direction": "lower_is_better",
            "unit": unit,
            "allowance": 0.1,
            "allowance_source": "default",
        }
        if g["metric"] == guardrail
        else {
            "metric": g["metric"],
            "direction": "lower_is_better",
            "unit": g["unit"],
            "applicable": False,
            "reason": "not used here",
        }
        for g in GUARDRAILS
    ]
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            looks=looks,
            budget={"max_rounds": max_rounds, "max_seconds": 600.0},
            guardrails=guardrails,
            # The class whose own schedule the in-domain cases use, so
            # only the guardrail or the schedule can block here.
            target_class="python_cpu",
        )
    )
    assert keep_blocked(spec) is blocked


PYTHON_SCHEDULE = ({"n0": 10, "k": 10, "max_looks": 6}, 60)
WEB_SCHEDULE = ({"n0": 10, "k": 10, "max_looks": 3}, 30)
LLM_SCHEDULE_CASE = ({"n0": 10, "k": 6, "max_looks": 3}, 22)
GPU_SCHEDULE = ({"n0": 20, "k": 20, "max_looks": 6}, 120)


@pytest.mark.parametrize(
    ("schedule", "alpha", "target_class", "blocked"),
    [
        # Each class on its own schedule, at the default alpha.
        (PYTHON_SCHEDULE, 0.05, "python_cpu", False),
        (PYTHON_SCHEDULE, 0.05, "native", False),
        (WEB_SCHEDULE, 0.05, "web_service", False),
        (WEB_SCHEDULE, 0.05, "agentic", False),
        (LLM_SCHEDULE_CASE, 0.05, "llm_serving", False),
        # gpu_kernels has no real traces: withheld since 2026-10-28.
        (GPU_SCHEDULE, 0.05, "gpu_kernel", True),
        (GPU_SCHEDULE, 0.05, None, True),
        # No class declared: no class's calibration can be claimed.
        (PYTHON_SCHEDULE, 0.05, None, True),
        (WEB_SCHEDULE, 0.05, None, True),
        # A class on another class's schedule.
        (PYTHON_SCHEDULE, 0.05, "web_service", True),
        (WEB_SCHEDULE, 0.05, "python_cpu", True),
        (LLM_SCHEDULE_CASE, 0.05, "agentic", True),
        (WEB_SCHEDULE, 0.05, "gpu_kernel", True),
        # alpha off the default.
        (PYTHON_SCHEDULE, 0.1, "python_cpu", True),
        (WEB_SCHEDULE, 0.2, "web_service", True),
        (LLM_SCHEDULE_CASE, 0.01, "llm_serving", True),
        (GPU_SCHEDULE, 0.1, "gpu_kernel", True),
        # A custom schedule.
        (({"n0": 10, "k": 10, "max_looks": 3}, 40), 0.05, "web_service", True),
        (({"n0": 12, "k": 2, "max_looks": 1}, 12), 0.05, "python_cpu", True),
    ],
    ids=[
        "python",
        "native",
        "web",
        "agentic",
        "llm",
        "gpu",
        "gpu-no-class",
        "python-no-class",
        "web-no-class",
        "python-schedule-web-class",
        "web-schedule-python-class",
        "llm-schedule-agentic-class",
        "web-schedule-gpu-class",
        "python-alpha-0.1",
        "web-alpha-0.2",
        "llm-alpha-0.01",
        "gpu-alpha-0.1",
        "custom-schedule",
        "single-look-schedule",
    ],
)
def test_a_kept_decision_is_withheld_outside_the_calibrated_domain(
    schedule: tuple[dict[str, int], int],
    alpha: float,
    target_class: str | None,
    blocked: bool,
) -> None:
    looks, max_rounds = schedule
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            looks=looks,
            budget={"max_rounds": max_rounds, "max_seconds": 600.0},
            alpha=alpha,
            guardrails=NOT_APPLICABLE,
            target_class=target_class,
        )
    )
    assert keep_blocked(spec) is blocked


def test_a_custom_schedule_ends_inconclusive_with_the_reason_once_calibrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = slow_baseline(tmp_path)
    result = project.run(faster_candidate(project), keepworthy_spec())
    blocked_everywhere(seen)
    assert_withheld(result, blocker="schedule_uncalibrated")
    warned = [
        w for w in result.record.warnings if w.code == "schedule_uncalibrated"
    ]
    assert len(warned) == 1
    assert "calibrated domain" in warned[0].message


def test_alpha_other_than_the_default_ends_inconclusive_once_calibrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = make_project(tmp_path, bench=WIDE_BENCH, baseline=det_work(1.0))
    result = project.run(faster_candidate(project), web_spec(alpha=0.1))
    assert all(seen.blocked), seen.blocked
    assert (seen.rules[-1], seen.held[-1]) == (2, True)
    assert_withheld(result, blocker="schedule_uncalibrated")
    warned = [
        w for w in result.record.warnings if w.code == "schedule_uncalibrated"
    ]
    assert len(warned) == 1
    assert "alpha 0.1" in warned[0].message


def test_a_keep_on_a_class_schedule_is_a_rule_2_keep_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    result = project.run(faster_candidate(project), web_spec())
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule) == ("KEEP", "performance", 2)
    assert v.procedure == "hac_lf"
    assert v.diagnostics.procedure_uncalibrated is False
    assert v.regression is False
    assert v.estimate is not None
    assert v.estimate == pytest.approx(2.0, rel=1e-3)
    assert v.interval is not None
    assert v.interval.lower > 1.03
    # web_service on its own schedule (web_agentic), at alpha 0.05.
    assert v.target_class == "web_service"
    assert v.calibration == "calibrated"
    joined = "; ".join(v.calibration_conditions)
    assert "hac_lf" in joined
    assert "alpha 0.05" in joined
    assert "8268209547887630561" in joined
    assert "4 real traces (web 2, web-node, web-go)" in joined
    assert (
        not {w.code for w in result.record.warnings} & KEEP_BLOCKING_WARNINGS
    )
    assert "KEEP" in result.report_path.read_text()
    # RunRecord's bar check (schema `_check_bar`) accepted the record.
    assert read_record(result.record_path) == result.record


def test_the_same_spec_on_the_python_schedule_is_not_class_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    spec = keepworthy_spec(
        budget={"max_rounds": 60, "max_seconds": 300.0},
        looks={"n0": 10, "k": 10, "max_looks": 6},
        target_class="web_service",
    )
    result = project.run(faster_candidate(project), spec)
    v = result.record.verdict
    # The interval clears the bar (rule 2) but the KEEP is withheld.
    assert (v.outcome, v.reason, v.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        2,
    )
    assert v.target_class == "web_service"
    assert v.calibration == "uncalibrated"
    assert v.calibration_conditions == ()
    assert v.interval is not None
    assert v.interval.lower > 1.03
    warned = [
        w for w in result.record.warnings if w.code == "schedule_not_class_own"
    ]
    assert len(warned) == 1
    assert "web_service" in warned[0].message
    assert read_record(result.record_path) == result.record


@pytest.mark.parametrize(
    "targets",
    [
        (("python_cpu", "confirmed"),),
        "none",
        (("web_service", "proposed"),),
        (("web_service", "stale"),),
        (("web_service", "retired"),),
        (),
    ],
    ids=[
        "class-absent-from-targets",
        "no-project-model",
        "proposed-only",
        "stale-only",
        "retired-only",
        "model-without-targets",
    ],
)
def test_a_class_the_project_does_not_confirm_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, targets: Targets
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    result = project.run(
        faster_candidate(project), web_spec(), targets=targets
    )
    v = result.record.verdict
    # The interval clears the bar (rule 2) but the KEEP is withheld.
    assert (v.outcome, v.reason, v.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        2,
    )
    assert v.calibration == "uncalibrated"
    assert v.calibration_conditions == ()
    assert v.interval is not None
    assert v.interval.lower > 1.03
    warned = [
        w
        for w in result.record.warnings
        if w.code == "target_class_unverified"
    ]
    assert len(warned) == 1
    assert "web_service" in warned[0].message
    assert read_record(result.record_path) == result.record


def withheld_for(result: CompareResult, why: str) -> None:
    """KEEP withheld as ``target_class_unverified``, naming ``why``."""
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 2)
    assert v.calibration == "uncalibrated"
    warned = [
        w
        for w in result.record.warnings
        if w.code == "target_class_unverified"
    ]
    assert len(warned) == 1
    assert why in warned[0].message
    assert read_record(result.record_path) == result.record


def test_a_confirmed_target_whose_evidence_changed_is_stale_and_withholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    write_model(
        project.model_root,
        (("web_service", "confirmed"),),
        evidence={"pyproject.toml": "[project]\nname = 'a'\n"},
    )
    # Unchanged evidence: calibrated, so the check is not a blanket no.
    kept = project.run(faster_candidate(project), web_spec(), targets="keep")
    assert kept.record.verdict.calibration == "calibrated"
    # The file the target was derived from changes; nothing reruns
    # `project check`, and the model on disk is left as it is.
    (project.model_root / "pyproject.toml").write_text(
        "[project]\nname = 'b'\n"
    )
    before = model_path(project.model_root).read_bytes()
    again = project.candidate("fast-again", {"work.py": det_work(0.5)})
    result = project.run(again, web_spec(), targets="keep")
    withheld_for(result, "stale: file_changed")
    assert model_path(project.model_root).read_bytes() == before


def test_an_unreadable_project_model_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    path = model_path(project.model_root)
    path.parent.mkdir(parents=True)
    path.write_text("{not a project model", encoding="utf-8")
    result = project.run(faster_candidate(project), web_spec(), targets="keep")
    withheld_for(result, "unreadable")


def test_no_project_root_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    result = project.run(
        faster_candidate(project), web_spec(), project_root="none"
    )
    withheld_for(result, "no project root was given")


def workload_warnings(result: CompareResult) -> list[str]:
    return [
        w.message
        for w in result.record.warnings
        if w.code == "workload_not_confirmed"
    ]


def withheld_for_workload(result: CompareResult, why: str) -> None:
    """KEEP withheld as ``workload_not_confirmed``, naming ``why``.

    The class is confirmed and the verdict stays ``calibrated``: the
    workload is a separate condition of a KEEP.
    """
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        2,
    )
    assert v.calibration == "calibrated"
    assert v.interval is not None
    assert v.interval.lower > 1.03
    warned = workload_warnings(result)
    assert len(warned) == 1
    assert why in warned[0]
    assert not [
        w
        for w in result.record.warnings
        if w.code == "target_class_unverified"
    ]
    assert read_record(result.record_path) == result.record


def test_a_confirmed_workload_can_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    result = project.run(faster_candidate(project), web_spec())
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("KEEP", 2)
    assert workload_warnings(result) == []


def test_an_unconfirmed_workload_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    spec = web_spec()
    write_model(
        project.model_root,
        (("web_service", "confirmed"),),
        workload=spec,
        workload_status="proposed",
    )
    result = project.run(faster_candidate(project), spec, targets="keep")
    withheld_for_workload(result, "proposed")


def test_a_workload_missing_from_the_model_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    write_model(project.model_root, (("web_service", "confirmed"),))
    result = project.run(faster_candidate(project), web_spec(), targets="keep")
    withheld_for_workload(result, "no such workload")


def test_a_spec_changed_after_confirmation_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    write_model(
        project.model_root,
        (("web_service", "confirmed"),),
        workload=web_spec(),
    )
    # Same name, another spec: the confirmation was of the first.
    result = project.run(
        faster_candidate(project), web_spec(min_effect=0.05), targets="keep"
    )
    withheld_for_workload(result, "spec_changed")


def test_a_goal_reset_to_another_metric_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    spec = web_spec()
    write_model(
        project.model_root,
        (("web_service", "confirmed"),),
        workload=spec,
        goal=("throughput", "higher_is_better", "ops/s"),
    )
    result = project.run(faster_candidate(project), spec, targets="keep")
    withheld_for_workload(result, "metric is wall_time in the spec")
    message = workload_warnings(result)[0]
    assert "direction is lower_is_better in the spec" in message
    assert "unit is s in the spec" in message


def test_a_workload_confirmed_before_its_spec_existed_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    spec = web_spec()
    write_model(
        project.model_root,
        (("web_service", "confirmed"),),
        workload=spec,
        bound=False,
    )
    result = project.run(faster_candidate(project), spec, targets="keep")
    withheld_for_workload(
        result, "confirmed before its benchmark was defined: confirm again"
    )


def test_a_spec_of_another_class_than_the_workload_target_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    spec = web_spec()
    write_model(
        project.model_root,
        (("web_service", "confirmed"), ("python_cpu", "confirmed")),
        workload=spec,
        workload_on="python_cpu",
    )
    result = project.run(faster_candidate(project), spec, targets="keep")
    withheld_for_workload(result, "of class python_cpu")
    assert "declares web_service" in workload_warnings(result)[0]


def goal_entry(metric: str, direction: str, unit: str) -> GoalEntry:
    when = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
    return GoalEntry(
        id="goal-work",
        workload_id="work",
        metric=metric,
        direction=cast("Direction", direction),
        unit=unit,
        bar=None,
        guardrails=(),
        missing={"bar": "the developer has not said"},
        status="proposed",
        provenance=Provenance(
            files=(), commands=(), perfproof_version="0.1.0", derived_at=when
        ),
    )


@pytest.mark.parametrize(
    ("goal", "reported"),
    [
        (("wall_time", "lower_is_better", "s"), None),
        (("cpu_time", "lower_is_better", "s"), "metric is wall_time"),
        (("wall_time", "lower_is_better", "ms"), "unit is s"),
        (("tokens_per_s", "higher_is_better", "s"), "direction is lower"),
    ],
    ids=["match", "metric", "unit", "direction"],
)
def test_each_goal_field_that_differs_from_the_spec_is_reported(
    goal: tuple[str, str, str], reported: str | None
) -> None:
    found = compare_module._goal_mismatch(  # pyright: ignore[reportPrivateUsage]
        web_spec(), goal_entry(*goal)
    )
    if reported is None:
        assert found is None
    else:
        assert found is not None
        assert reported in found
        # Only the fields that differ are named.
        assert found.count(" in the spec, ") == (
            2 if goal[0] == "tokens_per_s" else 1
        )


PEND_BENCH = """\
import os

from work import A, P99, SCALE, STEP, run

n = int(open("n.txt").read()) if os.path.exists("n.txt") else 0
open("n.txt", "w").write(str(n + 1))
wobble = 1 + 0.004 * ((n * 37) % 31 - 15)
p99w = 1 + A * ((n * STEP) % 31 - 15) / 15.0
with open("out.txt", "w") as f:
    f.write(f"{run()}\\n")
print(
    "PERFPROOF {"
    + '"value": %.6f, "p99_latency": %.6f' % (SCALE * wobble, P99 * p99w)
    + "}"
)
"""
"""Reports a wall value and a p99 that cycle through 31 values each."""


def pend_work(scale: float, p99: float, step: int, amp: float) -> str:
    return (
        f"SCALE = {scale}\nP99 = {p99}\nSTEP = {step}\nA = {amp}\n\n\n"
        "def run():\n    return 42\n"
    )


def run_scenario(
    tmp_path: Path, name: str, scenario: str, confirmed: bool
) -> CompareResult:
    """Run one scenario with the workload confirmed or only proposed."""
    root = tmp_path / name
    root.mkdir()
    if scenario == "pending":
        project = make_project(
            root, bench=PEND_BENCH, baseline=pend_work(1.0, 1.0, 37, 0.1)
        )
        spec = web_spec(guardrails=P99_GUARDRAILS)
        candidate = project.candidate(
            "c", {"work.py": pend_work(0.5, 1.1, 11, 0.1)}
        )
    else:
        project = web_project(root)
        spec = web_spec()
        candidate = (
            faster_candidate(project)
            if scenario == "faster"
            else project.candidate("slow", {"work.py": det_work(2.0)})
        )
    write_model(
        project.model_root,
        (("web_service", "confirmed"),),
        workload=spec,
        workload_status="confirmed" if confirmed else "proposed",
    )
    return project.run(candidate, spec, targets="keep")


def test_an_unconfirmed_workload_does_not_end_a_run_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guardrail not established at look 2 is breached at look 3.

    Confirmed or not, the run goes on to 30 rounds and ends REJECT on
    the guardrail: the workload only ever relabels a KEEP.
    """
    watch_looks(monkeypatch)
    seen: dict[bool, CompareResult] = {
        flag: run_scenario(tmp_path, f"p{flag}", "pending", flag)
        for flag in (True, False)
    }
    for result in seen.values():
        v = result.record.verdict
        assert (v.outcome, v.reason, v.rule) == ("REJECT", "guardrail", 2)
        assert (v.rounds, v.looks) == (30, 3)
    assert len(workload_warnings(seen[False])) == 1
    assert workload_warnings(seen[True]) == []


@pytest.mark.parametrize("scenario", ["faster", "slower", "pending"])
def test_an_unconfirmed_workload_changes_only_a_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    watch_looks(monkeypatch)
    yes = run_scenario(tmp_path, "yes", scenario, True).record.verdict
    no = run_scenario(tmp_path, "no", scenario, False).record.verdict
    if yes.outcome == "KEEP":
        assert (no.outcome, no.reason) == (
            "INCONCLUSIVE",
            "insufficient_evidence",
        )
    else:
        assert (no.outcome, no.reason) == (yes.outcome, yes.reason)
    assert (no.rule, no.rounds, no.looks) == (yes.rule, yes.rounds, yes.looks)
    assert (no.estimate, no.interval) == (yes.estimate, yes.interval)
    assert no.calibration == yes.calibration


def test_every_workload_problem_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    spec = web_spec()
    write_model(
        project.model_root,
        (("web_service", "confirmed"), ("python_cpu", "confirmed")),
        workload=spec,
        workload_on="python_cpu",
        bound=False,
        goal=("throughput", "higher_is_better", "ops/s"),
    )
    result = project.run(faster_candidate(project), spec, targets="keep")
    message = workload_warnings(result)[0]
    for part in (
        "confirmed before its benchmark was defined",
        "of class python_cpu",
        "metric is wall_time in the spec",
        "direction is lower_is_better in the spec",
        "unit is s in the spec",
    ):
        assert part in message


def test_a_reject_on_evidence_does_not_need_a_confirmed_workload(
    tmp_path: Path,
) -> None:
    project = web_project(tmp_path)
    spec = web_spec()
    write_model(
        project.model_root,
        (("web_service", "confirmed"),),
        workload=spec,
        workload_status="proposed",
    )
    slower = project.candidate("slow", {"work.py": det_work(2.0)})
    result = project.run(slower, spec, targets="keep")
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule) == ("REJECT", "performance", 3)
    assert v.calibration == "calibrated"
    assert len(workload_warnings(result)) == 1
    assert read_record(result.record_path) == result.record


def test_a_reject_on_evidence_does_not_need_a_confirmed_class(
    tmp_path: Path,
) -> None:
    project = web_project(tmp_path)
    slower = project.candidate("slow", {"work.py": det_work(2.0)})
    result = project.run(slower, web_spec(), targets="none")
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule) == ("REJECT", "performance", 3)
    assert v.regression is True
    assert v.calibration == "uncalibrated"
    assert read_record(result.record_path) == result.record


def test_a_confirmed_target_among_others_makes_the_verdict_calibrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    result = project.run(
        faster_candidate(project),
        web_spec(),
        targets=(("python_cpu", "proposed"), ("web_service", "confirmed")),
    )
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("KEEP", 2)
    assert (v.calibration, v.target_class) == ("calibrated", "web_service")


def test_no_target_class_withholds_keep_and_is_uncalibrated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    project = web_project(tmp_path)
    result = project.run(
        faster_candidate(project), web_spec(target_class=None)
    )
    v = result.record.verdict
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 2)
    assert v.target_class is None
    assert v.calibration == "uncalibrated"
    warned = [
        w for w in result.record.warnings if w.code == "target_class_unknown"
    ]
    assert len(warned) == 1
    assert read_record(result.record_path) == result.record


def test_a_straddling_interval_on_a_class_schedule_is_calibrated_rule_5(
    tmp_path: Path,
) -> None:
    project = web_project(tmp_path)
    # The benchmark's values are deterministic, so a candidate 1.03
    # times faster sits on the bar: its interval straddles it.
    on_the_bar = project.candidate("bar", {"work.py": det_work(1 / 1.03)})
    result = project.run(on_the_bar, web_spec())
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        5,
    )
    assert v.calibration == "calibrated"
    assert v.target_class == "web_service"


def test_a_slower_candidate_on_a_class_schedule_is_a_performance_reject(
    tmp_path: Path,
) -> None:
    project = web_project(tmp_path)
    slower = project.candidate("slow", {"work.py": det_work(2.0)})
    result = project.run(slower, web_spec())
    v = result.record.verdict
    assert (v.outcome, v.reason, v.rule) == ("REJECT", "performance", 3)
    assert v.regression is True
    assert v.interval is not None
    assert v.interval.upper < 1
    assert v.estimate == pytest.approx(0.5, rel=1e-3)
    assert v.calibration == "calibrated"
    assert v.target_class == "web_service"
    assert read_record(result.record_path) == result.record


def test_the_gpu_kernels_schedule_withholds_keep_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 127 values, so that 120 rounds pass the continuity check.
    bench = DET_BENCH.replace("(n * 37) % 11 - 5", "(n * 37) % 127 - 63")
    seen = watch_looks(monkeypatch)
    project = make_project(tmp_path, bench=bench, baseline=det_work(1.0))
    spec = keepworthy_spec(
        budget={"max_rounds": 120, "max_seconds": 600.0},
        looks={"n0": 20, "k": 20, "max_looks": 6},
        target_class="gpu_kernel",
    )
    assert spec.alpha == 0.05
    result = project.run(faster_candidate(project), spec)
    assert seen.blocked
    assert all(seen.blocked), seen.blocked
    assert_withheld(result, blocker="schedule_uncalibrated")
    v = result.record.verdict
    assert v.procedure == "t_log_ratio_batch_means"
    warned = [
        w for w in result.record.warnings if w.code == "schedule_uncalibrated"
    ]
    assert len(warned) == 1
    assert "gpu_kernels" in warned[0].message
    assert "no real GPU traces" in warned[0].message


def test_the_default_schedules_have_no_late_look_warning(
    project: Project,
) -> None:
    result = project.run(project.candidate("same", {}), web_spec())
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
    """The run measured and recorded, and its verdict is not a KEEP.

    The candidates here are identical to the baseline and timed for
    real, so a chance REJECT (performance, at about the calibrated
    rate) is possible; a KEEP is withheld by what the test is about.
    """
    verdict = result.record.verdict
    assert verdict.outcome != "KEEP"
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
    # An identical candidate on real timings: only "never a KEEP" is
    # guaranteed (a chance REJECT happens at the calibrated rate).
    assert result.record.verdict.outcome != "KEEP"
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
    # An identical candidate on real timings: only "never a KEEP" is
    # guaranteed (a chance REJECT happens at the calibrated rate).
    assert result.record.verdict.outcome != "KEEP"


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
    # An identical candidate on real timings: only "never a KEEP" is
    # guaranteed (a chance REJECT happens at the calibrated rate).
    assert result.record.verdict.outcome != "KEEP"
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
    # An identical candidate on real timings: only "never a KEEP" is
    # guaranteed (a chance REJECT happens at the calibrated rate).
    assert result.record.verdict.outcome != "KEEP"
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
    # Identical candidate, real timings: never a KEEP is the guarantee.
    assert verdict.outcome != "KEEP"
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


NOISY_BENCH = DET_BENCH.replace(
    "from work import SCALE, run", "from work import SCALE, STEP, run"
).replace(
    "value = SCALE * (1 + 0.004 * ((n * 37) % 11 - 5))",
    "value = SCALE * (1 + 0.04 * ((n * STEP) % 11 - 5))",
)
"""A deterministic benchmark whose value is a permutation of 11 levels
(+-20%) chosen by the work module's ``STEP``. Two arms with different
steps give log ratios of large, fixed spread, so an interval straddles
the bar the same way on every machine and every run."""


def noisy_work(step: int) -> str:
    """A work module for `NOISY_BENCH` with permutation ``step``."""
    return f"SCALE = 1.0\nSTEP = {step}\n\n\ndef run():\n    return 42\n"


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


def watch_looks(
    monkeypatch: pytest.MonkeyPatch, *, coverage_passed: bool = True
) -> Seen:
    """Force the coverage gate passed, and spy on every look.

    Returns what `decide_look` was asked and answered at each look. It
    forces nothing else: `CALIBRATED` is True in the code (it was set by
    this helper until the flip of 2026-10-28).
    """
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
    assert 2 in seen.rules, f"no rule 2 look was taken: {seen.rules}"
    held = [h for r, h in zip(seen.rules, seen.held, strict=True) if r == 2]
    assert all(held), (seen.rules, seen.held)


def web_project(tmp_path: Path, **kwargs: Any) -> Project:
    """A project whose baseline is twice as slow, for `web_spec`."""
    return make_project(
        tmp_path, bench=WIDE_BENCH, baseline=det_work(1.0), **kwargs
    )


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
    seen = watch_looks(monkeypatch)
    project = make_project(tmp_path, bench=WIDE_BENCH, baseline=det_work(1.0))
    result = project.run(faster_candidate(project), web_spec())
    # The look kept: with nothing unverified, rule 2 is a KEEP.
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.reason, verdict.rule) == (
        "KEEP",
        "performance",
        2,
    )
    assert verdict.interval is not None
    assert verdict.interval.lower > 1.03
    assert seen.blocked == [False, False]
    assert seen.rules == [4, 2]
    assert seen.held == [False, False]


def memory_work(scale: float, mib: int) -> str:
    """`det_work` that also holds ``mib`` MiB resident while it runs.

    Large enough that the peak is the benchmark's own on every platform,
    above the launch helper's floor on Linux.
    """
    return (
        f"SCALE = {scale}\n\n\ndef run():\n"
        f"    buf = bytearray({mib} << 20)\n"
        "    buf[::4096] = bytes(len(buf[::4096]))\n"
        "    return 42\n"
    )


def memory_guardrail(allowance: float) -> list[dict[str, Any]]:
    """Declare ``peak_memory`` with ``allowance``, the rest n/a."""
    return [
        {
            "metric": "peak_memory",
            "direction": "lower_is_better",
            "unit": "bytes",
            "allowance": allowance,
            "allowance_source": "project",
        },
        *NOT_APPLICABLE[1:],
    ]


def web_spec(**changes: Any) -> BenchmarkSpec:
    """The web schedule, decided with ``hac_lf``: looks at 10, 20, 30.

    It declares ``web_service``, whose own schedule this is, unless
    ``target_class`` is given: a KEEP needs a class.

    The first look has too few observations for an interval, so the
    first that can decide is at 20 rounds.
    """
    changes.setdefault("target_class", "web_service")
    return keepworthy_spec(
        budget={"max_rounds": 30, "max_seconds": 300.0},
        looks={"n0": 10, "k": 10, "max_looks": 3},
        **changes,
    )


WIDE_BENCH = DET_BENCH.replace("(n * 37) % 11 - 5", "(n * 37) % 31 - 15")
"""`DET_BENCH` with 31 distinct values, so that 30 rounds pass the
continuity check of a ``reported`` metric."""


def memory_project(tmp_path: Path, mib: int) -> Project:
    return make_project(
        tmp_path, bench=WIDE_BENCH, baseline=memory_work(1.0, mib)
    )


def test_a_memory_breach_at_a_rule_2_look_is_a_guardrail_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = memory_project(tmp_path, 20)
    fast_fat = project.candidate("fat", {"work.py": memory_work(0.5, 64)})
    result = project.run(fast_fat, web_spec(guardrails=memory_guardrail(0.1)))
    record = result.record
    verdict = record.verdict
    assert (verdict.outcome, verdict.reason, verdict.rule) == (
        "REJECT",
        "guardrail",
        2,
    )
    assert verdict.procedure == "hac_lf"
    assert verdict.calibration == "calibrated"
    assert verdict.estimate is not None
    assert verdict.estimate > 1.03
    assert seen.rules == [4, 2]
    memory = record.guardrails[0]
    assert (memory.metric, memory.status) == ("peak_memory", "breached")
    assert memory.measure == PEAK_MEMORY_MEASURE
    assert memory.estimate is not None
    assert memory.estimate > 1.1
    assert memory.worsening is not None
    assert memory.worsening.lower > 1.1
    assert "breached" in result.report_path.read_text()


def test_a_declared_limit_quantile_reaches_the_limit_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch_looks(monkeypatch)
    quantiles: list[float] = []
    real = compare_module.evaluate_guardrail

    def spy(*args: Any, **kwargs: Any) -> Any:
        quantiles.append(kwargs["quantile"])
        return real(*args, **kwargs)

    monkeypatch.setattr(compare_module, "evaluate_guardrail", spy)
    project = memory_project(tmp_path, 20)
    candidate = project.candidate("fat", {"work.py": memory_work(0.5, 64)})
    guardrails = memory_guardrail(0.1)
    guardrails[0]["limits"] = [{"value": 1.0e12, "label": "cap"}]
    guardrails[0]["limit_quantile"] = 0.99
    result = project.run(candidate, web_spec(guardrails=guardrails))
    assert quantiles
    assert set(quantiles) == {0.99}
    standing = result.record.guardrails[0].standing
    assert [s.quantile for s in standing] == [0.99]


def test_a_measured_memory_within_its_allowance_does_not_block_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = memory_project(tmp_path, 20)
    same_memory = project.candidate("lean", {"work.py": memory_work(0.5, 20)})
    spec = web_spec(guardrails=memory_guardrail(0.5))
    result = project.run(same_memory, spec)
    # The look kept, with the guardrail passed, so nothing withheld it.
    # At the first look the guardrail had no interval yet, so it was not
    # established.
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.reason, verdict.rule) == (
        "KEEP",
        "performance",
        2,
    )
    assert verdict.interval is not None
    assert verdict.interval.lower > 1.03
    assert seen.rules == [4, 2]
    assert seen.blocked == [True, False]


def scripted_guardrail(
    monkeypatch: pytest.MonkeyPatch, status_at: dict[int, str]
) -> list[int]:
    """Make `evaluate_guardrail` answer ``status_at`` by look length.

    Returns the observation counts it was asked about. Only the status
    is scripted; the intervals are the real ones.
    """
    real = compare_module.evaluate_guardrail
    asked: list[int] = []

    def scripted(*args: Any, **kwargs: Any) -> Any:
        found = real(*args, **kwargs)
        n = int(args[0].shape[1])
        asked.append(n)
        return replace(found, status=np.array([status_at[n]]))

    monkeypatch.setattr(compare_module, "evaluate_guardrail", scripted)
    return asked


def test_a_guardrail_not_established_yet_lets_the_look_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    asked = scripted_guardrail(
        monkeypatch,
        {10: "not_established", 20: "not_established", 30: "passed"},
    )
    project = memory_project(tmp_path, 20)
    spec = web_spec(guardrails=memory_guardrail(0.5))
    result = project.run(
        project.candidate("lean", {"work.py": memory_work(0.5, 20)}),
        spec,
    )
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.reason, verdict.rule) == (
        "KEEP",
        "performance",
        2,
    )
    assert verdict.interval is not None
    assert verdict.interval.lower > 1.03
    # Look 2 held the KEEP for the guardrail and went on; look 3 kept.
    assert asked == [10, 20, 30]
    assert seen.rules == [4, 2, 2]
    assert seen.blocked == [True, True, False]
    assert seen.held == [False, True, False]


def test_an_unmeasurable_guardrail_ends_the_run_at_that_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = memory_project(tmp_path, 20)
    spec = web_spec(
        guardrails=[
            *memory_guardrail(0.5)[:2],
            {
                "metric": "p99_latency",
                "direction": "lower_is_better",
                "unit": "s",
                "allowance": 0.05,
                "allowance_source": "default",
            },
        ]
    )
    result = project.run(
        project.candidate("lean", {"work.py": memory_work(0.5, 20)}), spec
    )
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.rule) == ("INCONCLUSIVE", 2)
    assert verdict.rounds == 20
    assert seen.held == [False, True]
    by_metric = {g.metric: g for g in result.record.guardrails}
    assert by_metric["p99_latency"].status == "unmeasurable"
    assert "p99_latency" in by_metric["p99_latency"].reason


def test_an_unknown_peak_memory_is_unmeasurable_not_measured_on_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    real = compare_module.Runner.run_round

    def blind(self: Runner, index: int) -> Round:
        rnd = real(self, index)
        if index != 3:
            return rnd
        samples = tuple(
            s.model_copy(update={"max_rss_bytes": None}) for s in rnd.samples
        )
        return replace(rnd, samples=samples)  # type: ignore[arg-type]

    monkeypatch.setattr(compare_module.Runner, "run_round", blind)
    project = memory_project(tmp_path, 20)
    result = project.run(
        project.candidate("lean", {"work.py": memory_work(0.5, 20)}),
        web_spec(guardrails=memory_guardrail(0.5)),
    )
    memory = result.record.guardrails[0]
    assert memory.status == "unmeasurable"
    assert "unknown for 2 of 40 executions" in memory.reason
    assert memory.measure == PEAK_MEMORY_MEASURE
    assert memory.worsening is None
    assert seen.held == [False, True]


def test_an_unidentified_runtime_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = web_project(
        tmp_path, executables={"run.sh": "#!/bin/sh\nexec python bench.py\n"}
    )
    spec = web_spec(command=["./run.sh"], harness=["bench.py", "run.sh"])
    result = project.run(faster_candidate(project), spec)
    blocked_everywhere(seen)
    assert_withheld(result, blocker="runtime")


def test_an_unreadable_host_identity_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    real = fingerprint()
    blind = real.model_copy(
        update={"host_id": None, "missing": {**real.missing, "host_id": "x"}}
    )
    monkeypatch.setattr(compare_module, "fingerprint", lambda: blind)
    project = web_project(tmp_path)
    result = project.run(faster_candidate(project), web_spec())
    blocked_everywhere(seen)
    assert_withheld(result, blocker="same_host")


def test_a_host_identity_lost_after_the_last_look_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    real = fingerprint()
    blind = real.model_copy(
        update={"host_id": None, "missing": {**real.missing, "host_id": "x"}}
    )
    calls = iter([real])
    monkeypatch.setattr(
        compare_module, "fingerprint", lambda: next(calls, blind)
    )
    project = make_project(tmp_path, bench=WIDE_BENCH, baseline=det_work(1.0))
    result = project.run(faster_candidate(project), web_spec())
    # Nothing blocked at the look: the host was readable then.
    assert seen.blocked == [False, False]
    assert_withheld(result, blocker="same_host")
    assert gates(result.record)["same_host"] == "not_available"


def test_no_correctness_check_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = web_project(tmp_path)
    result = project.run(faster_candidate(project), web_spec(correctness={}))
    blocked_everywhere(seen)
    assert_withheld(result, blocker="correctness")


def test_coverage_not_available_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch, coverage_passed=False)
    project = web_project(tmp_path)
    result = project.run(faster_candidate(project), web_spec())
    blocked_everywhere(seen)
    assert_withheld(result, blocker="coverage")


def test_an_undeclared_guardrail_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = web_project(tmp_path)
    spec = web_spec(objective=None, guardrails=[])
    result = project.run(faster_candidate(project), spec)
    blocked_everywhere(seen)
    assert_withheld(result, blocker="peak_memory")


def test_a_counter_fallback_blocks_keep_at_every_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)

    def no_perf(event: str, *, env: Mapping[str, str] | None = None) -> str:
        return "perf is not on PATH"

    monkeypatch.setattr(compare_module, "counter_unavailable_reason", no_perf)
    # Wall time of the whole command: the work itself must dominate it.
    project = make_project(tmp_path, baseline=work(0.4))
    spec = web_spec(
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
    seen = watch_looks(monkeypatch)

    def unreadable(ex: object) -> None:
        return None

    monkeypatch.setattr(compare_module, "_printed_step", unreadable)
    project = web_project(tmp_path)
    result = project.run(faster_candidate(project), web_spec())
    blocked_everywhere(seen)
    assert_withheld(result, blocker="resolution_unknown")


def test_a_held_look_ends_the_run_at_that_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # More budget remains, and nothing in the run can lift the block.
    seen = watch_looks(monkeypatch)
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
    # An identical candidate on real timings: only "never a KEEP" is
    # guaranteed (a chance REJECT happens at the calibrated rate).
    assert result.record.verdict.outcome != "KEEP"
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
    # Recorded, not stopped: no gate failed, and a KEEP is withheld. An
    # identical candidate on real timings can chance-REJECT.
    verdict = result.record.verdict
    assert verdict.gate is None
    assert verdict.outcome != "KEEP"


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
            guardrails=[
                GUARDRAILS[0],
                {
                    "metric": "p99_latency",
                    "direction": "lower_is_better",
                    "unit": "s",
                    "allowance": 0.05,
                    "allowance_source": "default",
                },
            ],
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
    memory = items.get(("guardrail", "peak_memory"))
    assert memory is None or memory["status"] in (
        "not_established",
        "unmeasurable",
    )
    assert items[("guardrail", "peak_gpu_memory")]["status"] == "not_declared"
    assert items[("guardrail", "p99_latency")]["status"] == "unmeasurable"
    assert (
        "missing from the PERFPROOF line"
        in items[("guardrail", "p99_latency")]["reason"]
    )
    for code in (*withholding, "schedule_uncalibrated"):
        entry = items[("warning", code)]
        assert entry["status"] == "withholds_keep"
        assert entry["reason"]
    assert ("warning", "high_load") not in items
    codes = {
        w["code"] for w in cast("list[dict[str, Any]]", warnings_json(shown))
    }
    assert codes >= {"high_load", "schedule_uncalibrated"}


# A guardrail the benchmark reports itself, on the line of its objective

P99_BENCH = """\
import os

from work import OMIT, P99, SCALE, run

n = int(open("n.txt").read()) if os.path.exists("n.txt") else 0
open("n.txt", "w").write(str(n + 1))
wobble = 1 + 0.004 * ((n * 37) % 31 - 15)
with open("out.txt", "w") as f:
    f.write(f"{run()}\\n")
fields = ['"value": %.6f' % (SCALE * wobble)]
if not OMIT or n % OMIT != 3:
    fields.append('"p99_latency": %.6f' % (P99 * wobble))
print("PERFPROOF {" + ", ".join(fields) + "}")
"""


def p99_work(scale: float, p99: float, omit: int = 0) -> str:
    """A work module whose benchmark reports ``scale`` and ``p99``.

    Each is times the same zigzag; every ``omit``-th execution leaves
    p99 out.
    """
    return (
        f"SCALE = {scale}\nP99 = {p99}\nOMIT = {omit}\n\n\n"
        "def run():\n    return 42\n"
    )


P99_GUARDRAILS: list[dict[str, Any]] = [
    NOT_APPLICABLE[0],
    NOT_APPLICABLE[1],
    {
        "metric": "p99_latency",
        "direction": "lower_is_better",
        "unit": "s",
        "allowance": 0.05,
        "allowance_source": "default",
    },
]


def p99_project(tmp_path: Path, omit: int = 0) -> Project:
    return make_project(
        tmp_path, bench=P99_BENCH, baseline=p99_work(1.0, 1.0, omit)
    )


def p99_column(result: CompareResult, name: str, *, candidate: bool) -> Any:
    with np.load(result.run_dir / SAMPLES_NAME, allow_pickle=False) as data:
        which = data["candidate"] == int(candidate)
        return data[name][which]


def test_a_p99_breach_is_a_guardrail_reject_on_reported_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = p99_project(tmp_path)
    worse = project.candidate("worse", {"work.py": p99_work(0.5, 2.0)})
    result = project.run(worse, web_spec(guardrails=P99_GUARDRAILS))
    record = result.record
    verdict = record.verdict
    assert (verdict.outcome, verdict.reason, verdict.rule) == (
        "REJECT",
        "guardrail",
        2,
    )
    assert seen.rules == [4, 2]
    by_metric = {g.metric: g for g in record.guardrails}
    p99 = by_metric["p99_latency"]
    assert p99.status == "breached"
    assert p99.measure == REPORTED_MEASURE.format(metric="p99_latency")
    assert p99.estimate is not None
    assert p99.estimate == pytest.approx(2.0, rel=1e-4)
    assert p99.worsening is not None
    assert p99.worsening.lower > 1.05
    # The per-round values are the benchmark's, paired by round.
    base = p99_column(result, "reported_p99_latency", candidate=False)
    cand = p99_column(result, "reported_p99_latency", candidate=True)
    assert len(base) == len(cand) == verdict.rounds
    assert np.allclose(cand / base, 2.0, rtol=1e-4)
    assert "breached" in result.report_path.read_text()


def test_a_reported_p99_within_its_allowance_lets_keep_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = p99_project(tmp_path)
    same = project.candidate("same", {"work.py": p99_work(0.5, 1.0)})
    result = project.run(same, web_spec(guardrails=P99_GUARDRAILS))
    # The look kept: a p99 guardrail that could not be measured never
    # allowed it, and this one is within its allowance.
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.reason, verdict.rule) == (
        "KEEP",
        "performance",
        2,
    )
    assert verdict.interval is not None
    assert verdict.interval.lower > 1.03
    assert seen.rules == [4, 2]
    assert seen.blocked == [True, False]


def test_a_p99_missing_from_some_rounds_is_unmeasurable_not_defaulted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = watch_looks(monkeypatch)
    project = p99_project(tmp_path, omit=7)
    candidate = project.candidate("fast", {"work.py": p99_work(0.5, 1.0, 7)})
    result = project.run(candidate, web_spec(guardrails=P99_GUARDRAILS))
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.rule) == ("INCONCLUSIVE", 2)
    assert seen.held == [False, True]
    p99 = {g.metric: g for g in result.record.guardrails}["p99_latency"]
    assert p99.status == "unmeasurable"
    assert re.search(
        r"p99_latency is missing from the PERFPROOF line of \d+ of "
        rf"{2 * verdict.rounds} executions",
        p99.reason,
    )
    assert p99.worsening is None
    # The gap is recorded as a gap, not as a number.
    base = p99_column(result, "reported_p99_latency", candidate=False)
    assert 0 < int(np.isnan(base).sum()) < len(base)


def test_a_malformed_p99_stops_the_comparison_with_the_line(
    tmp_path: Path,
) -> None:
    bad = P99_BENCH.replace(
        "'\"p99_latency\": %.6f' % (P99 * wobble)",
        '\'"p99_latency": "fast"\'',
    )
    assert bad != P99_BENCH
    project = make_project(tmp_path, bench=bad, baseline=p99_work(1.0, 1.0))
    with pytest.raises(CompareStoppedError) as stopped:
        project.run(
            project.candidate("same", {}),
            web_spec(guardrails=P99_GUARDRAILS),
        )
    assert stopped.value.code == "broken_baseline"
    assert "PERFPROOF p99_latency must be a number" in stopped.value.message


def test_a_guardrail_value_that_cannot_be_read_back_withholds_keep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the guardrail is reported, so only its step is unknown."""
    seen = watch_looks(monkeypatch)

    def unreadable(path: object) -> None:
        return None

    monkeypatch.setattr(compare_module, "_reported_literals", unreadable)
    only_p99 = P99_BENCH.replace(
        "fields = ['\"value\": %.6f' % (SCALE * wobble)]", "fields = []"
    )
    assert only_p99 != P99_BENCH
    slow = "import time\n" + p99_work(1.0, 1.0).replace(
        "return 42", "time.sleep(0.05)\n    return 42"
    )
    project = make_project(tmp_path, bench=only_p99, baseline=slow)
    spec = web_spec(
        metric={
            "kind": "wall",
            "direction": "lower_is_better",
            "unit": "s",
        },
        guardrails=P99_GUARDRAILS,
    )
    fast = project.candidate("fast", {"work.py": p99_work(1.0, 1.0)})
    result = project.run(fast, spec)
    assert "resolution_unknown" in {w.code for w in result.record.warnings}
    assert result.record.verdict.outcome == "INCONCLUSIVE"
    assert all(seen.blocked)


QUANT_BENCH = """\
import os

from work import MODE, P99_MS, SCALE, run

n = int(open("n.txt").read()) if os.path.exists("n.txt") else 0
open("n.txt", "w").write(str(n + 1))
wobble = 1 + 0.004 * ((n * 37) % 31 - 15)
true_ms = P99_MS + ((n * 17) % 20) / 100.0
with open("out.txt", "w") as f:
    f.write(f"{run()}\\n")
ms = int(true_ms) if MODE == "whole_ms" else true_ms
print(
    "PERFPROOF {"
    + '"value": %.6f, "p99_latency": %.6f' % (SCALE * wobble, ms / 1000.0)
    + "}"
)
"""


def quant_work(scale: float, p99_ms: float, mode: str) -> str:
    return (
        f"SCALE = {scale}\nP99_MS = {p99_ms}\nMODE = {mode!r}\n\n\n"
        "def run():\n    return 42\n"
    )


@pytest.mark.parametrize(
    ("mode", "base_ms", "cand_ms", "status"),
    [
        # Truly +7.4%, past the 5% allowance, every pair reads 10 ms.
        ("whole_ms", 10.0, 10.74, "unmeasurable"),
        # Truly +4.8%, inside it, every pair reads 10 ms against 11 ms.
        ("whole_ms", 10.5, 11.0, "unmeasurable"),
        # The control: a true +10% printed continuously is a breach.
        ("exact", 10.0, 11.0, "breached"),
    ],
    ids=["hidden-breach", "false-breach", "continuous-control"],
)
def test_a_discrete_reported_p99_is_never_judged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    base_ms: float,
    cand_ms: float,
    status: str,
) -> None:
    watch_looks(monkeypatch)
    project = make_project(
        tmp_path, bench=QUANT_BENCH, baseline=quant_work(1.0, base_ms, mode)
    )
    candidate = project.candidate(
        "c", {"work.py": quant_work(0.5, cand_ms, mode)}
    )
    result = project.run(candidate, web_spec(guardrails=P99_GUARDRAILS))
    p99 = {g.metric: g for g in result.record.guardrails}["p99_latency"]
    assert p99.status == status
    verdict = result.record.verdict
    if status == "unmeasurable":
        assert "looks discrete" in p99.reason
        assert "print more digits" in p99.reason
        assert p99.worsening is None
        assert (verdict.outcome, verdict.rule) == ("INCONCLUSIVE", 2)
    else:
        assert (verdict.outcome, verdict.reason) == ("REJECT", "guardrail")


@pytest.mark.parametrize(
    ("base_mode", "cand_mode", "named"),
    [
        ("exact", "whole_ms", "the candidate (1 distinct)"),
        ("whole_ms", "exact", "the baseline (1 distinct)"),
    ],
    ids=["candidate-coarse", "baseline-coarse"],
)
def test_one_coarse_arm_is_enough_to_stop_judging_a_reported_p99(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_mode: str,
    cand_mode: str,
    named: str,
) -> None:
    """Truly +7.4%, one arm whole ms and the other continuous."""
    watch_looks(monkeypatch)
    project = make_project(
        tmp_path,
        bench=QUANT_BENCH,
        baseline=quant_work(1.0, 10.0, base_mode),
    )
    candidate = project.candidate(
        "c", {"work.py": quant_work(0.5, 10.74, cand_mode)}
    )
    result = project.run(candidate, web_spec(guardrails=P99_GUARDRAILS))
    p99 = {g.metric: g for g in result.record.guardrails}["p99_latency"]
    assert p99.status == "unmeasurable"
    assert named in p99.reason
    assert "print more digits" in p99.reason
    verdict = result.record.verdict
    assert (verdict.outcome, verdict.rule) == ("INCONCLUSIVE", 2)


@pytest.mark.integration
def test_a_real_benchmark_reports_p99_through_compare(
    tmp_path: Path,
) -> None:
    """The whole path with nothing patched.

    The child prints one line, the runner reads it, the samples keep it
    and the record judges it.
    """
    project = p99_project(tmp_path)
    worse = project.candidate("worse", {"work.py": p99_work(0.5, 2.0)})
    result = project.run(worse, web_spec(guardrails=P99_GUARDRAILS))
    p99 = {g.metric: g for g in result.record.guardrails}["p99_latency"]
    assert p99.status == "breached"
    assert p99.worsening is not None
    assert p99.worsening.lower > 1.05
    base = p99_column(result, "reported_p99_latency", candidate=False)
    cand = p99_column(result, "reported_p99_latency", candidate=True)
    assert np.allclose(cand / base, 2.0, rtol=1e-4)
    # The run directory holds the very line the benchmark printed.
    logs = sorted((result.run_dir / "logs").rglob("round-0.*.stdout"))
    assert len(logs) == 2
    for log in logs:
        line = next(
            ln for ln in log.read_text().splitlines() if ln.startswith("PERF")
        )
        assert set(json.loads(line.removeprefix("PERFPROOF "))) == {
            "value",
            "p99_latency",
        }


# The coverage gate on a real baseline checkout and a candidate that is
# the repository's own tree, as `perfproof compare` sets them up


def coverage_run(
    project: Project, changes: Mapping[str, str]
) -> CompareResult:
    """Compare the edited tree to its commit."""
    spec = twelve_rounds()
    lock = build_manifest(spec, project.root)
    baseline = add_baseline_worktree(project.root, project.commit, "baseline")
    for rel, text in changes.items():
        (project.root / rel).write_text(text)
    project.runs += 1
    return compare(
        spec,
        lock,
        baseline_root=baseline,
        baseline_commit=project.commit,
        candidate_root=project.root,
        run_dir=project.run_dir(),
        process_env=os.environ,
        order_seed=7,
    )


def coverage_gate_of(result: CompareResult) -> GateResult:
    (found,) = (g for g in result.record.gates if g.gate == "coverage")
    return found


def test_a_covered_python_change_passes_the_coverage_gate(
    project: Project,
) -> None:
    result = coverage_run(project, {"work.py": work(0.005)})
    found = coverage_gate_of(result)
    assert found.status == "passed", found.detail
    assert found.unverified == ()
    assert "measured with coverage.py" in found.detail


def test_python_lines_no_test_runs_make_coverage_not_available(
    project: Project,
) -> None:
    unused = work(0.03) + "\n\ndef unused():\n    return 1\n"
    result = coverage_run(project, {"work.py": unused})
    found = coverage_gate_of(result)
    assert found.status == "not_available"
    assert [(u.path, u.lines) for u in found.unverified] == [
        ("work.py", (10,))
    ]
    assert "work.py lines 10 never ran" in found.detail
    verdict = result.record.verdict
    # Real timings: a KEEP is withheld, whatever else the look decides.
    assert verdict.outcome != "KEEP"
    items = [cast(dict[str, str], i) for i in not_verified(result.record)]
    (reason,) = (i["reason"] for i in items if i["name"] == "coverage")
    assert "work.py lines 10 never ran" in reason


def test_a_candidate_that_is_not_a_repository_leaves_coverage_unknown(
    project: Project,
) -> None:
    faster = project.candidate("fast", {"work.py": work(0.005)})
    result = project.run(faster, twelve_rounds())
    found = coverage_gate_of(result)
    assert found.status == "not_available"
    assert "could not be listed" in found.detail


def test_a_malformed_coverage_report_is_inconclusive_not_a_crash(
    project: Project,
) -> None:
    writer = (
        "import os; open(os.environ['PERFPROOF_COVERAGE_REPORT'], 'wb')"
        ".write(b'SF:a\\x00b.c\\nDA:1,1\\nend_of_record\\n')"
    )
    fields = spec_fields(
        budget={"max_rounds": 12, "max_seconds": 300.0},
        looks={"n0": 12, "k": 2, "max_looks": 1},
    )
    fields["correctness"] = {
        **fields["correctness"],
        "coverage_command": ["python", "-c", writer],
        "coverage_timeout_seconds": 60.0,
    }
    spec = BenchmarkSpec.model_validate(fields)
    lock = build_manifest(spec, project.root)
    baseline = add_baseline_worktree(project.root, project.commit, "baseline")
    (project.root / "data.c").write_text("int f(void) { return 1; }\n")
    project.runs += 1
    result = compare(
        spec,
        lock,
        baseline_root=baseline,
        baseline_commit=project.commit,
        candidate_root=project.root,
        run_dir=project.run_dir(),
        process_env=os.environ,
        order_seed=7,
    )
    found = coverage_gate_of(result)
    assert found.status == "not_available"
    assert "the coverage report is unreadable" in found.detail
    # An identical candidate on real timings: only "never a KEEP" is
    # guaranteed (a chance REJECT happens at the calibrated rate).
    assert result.record.verdict.outcome != "KEEP"


# The split: the spec's look schedule picks the interval procedure

LLM_SCHEDULE: dict[str, Any] = {
    "budget": {"max_rounds": 22, "max_seconds": 300.0},
    "looks": {"n0": 10, "k": 6, "max_looks": 3},
}


def arm_order(result: CompareResult) -> Any:
    """Whether the candidate ran first, per round, in round order."""
    with np.load(result.run_dir / SAMPLES_NAME, allow_pickle=False) as data:
        mine = data["candidate"] == 1
        order = np.argsort(data["round"], kind="stable")
        return (data["position"][order] == 0)[mine[order]]


def test_a_default_schedule_is_decided_by_hac_lf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The wall clock's step is measured per run (1 ms under emulated
    # amd64, about 1 ns natively) and widens the wall interval, so fix
    # it to make the expectation the same everywhere.
    step = 2.5e-4
    monkeypatch.setattr(compare_module, "_wall_step", lambda: step)
    spec = BenchmarkSpec.model_validate(spec_fields(**LLM_SCHEDULE))
    # Deterministic values (`DET_BENCH`): real sleep timings printed to
    # the microsecond can repeat often enough, in 10 rounds, to fail the
    # continuity check (fewer distinct values than half the rounds),
    # which ends the run at the first look as INCONCLUSIVE.
    project = make_project(tmp_path, bench=DET_BENCH, baseline=det_work(1.0))
    faster = project.candidate("fast", {"work.py": det_work(1 / 6)})
    result = project.run(faster, spec)
    v = result.record.verdict
    assert v.procedure == "hac_lf"
    # The candidate is 6 times faster, so the first look with an
    # interval (16 rounds, the second look) clears the bar; no class is
    # declared, so that KEEP is held and ends it.
    assert (v.rounds, v.looks) == (16, 2)
    assert v.interval is not None
    assert v.estimate is not None
    assert v.interval.confidence == look_confidence(0.05, 3)
    # hac_lf has no gate: nothing is withheld, and it says so with None
    # rather than the False a gate that looked and passed would record.
    assert v.diagnostics.autocorrelated_rounds is None
    assert v.diagnostics.outlier_rounds is None
    base, cand = arm_values(result)
    made = hac_lf_interval(
        base[np.newaxis],
        cand[np.newaxis],
        direction="lower_is_better",
        confidence=v.interval.confidence,
        candidate_first=arm_order(result)[np.newaxis],
        mode="one_host",
    )
    assert v.estimate == float(made.interval.estimate[0])
    # The record's interval is the procedure's, widened outward by the
    # values' resolution (1e-6 on a printed value of about 0.03).
    assert v.interval.lower <= float(made.interval.lower[0])
    assert v.interval.upper >= float(made.interval.upper[0])
    assert v.interval.lower == pytest.approx(
        float(made.interval.lower[0]), rel=1e-3
    )
    # The wall-clock interval of a reported metric is hac_lf's too, on
    # the recorded wall seconds, widened by the wall clock's step.
    wall = v.diagnostics.wall_interval
    assert wall is not None
    with np.load(result.run_dir / SAMPLES_NAME, allow_pickle=False) as data:
        mine = data["candidate"] == 1
        order = np.argsort(data["round"], kind="stable")
        seconds = data["wall_seconds"][order]
        arm = mine[order]
    expected = hac_lf_interval(
        seconds[~arm][np.newaxis],
        seconds[arm][np.newaxis],
        direction="lower_is_better",
        confidence=v.interval.confidence,
        candidate_first=arm_order(result)[np.newaxis],
        mode="one_host",
    )
    base_s = np.asarray(seconds[~arm], dtype=np.float64)
    cand_s = np.asarray(seconds[arm], dtype=np.float64)
    widening = float(
        np.mean(-np.log1p(-step / base_s) - np.log1p(-step / cand_s))
    )
    assert wall.lower == pytest.approx(
        float(expected.interval.lower[0]) * math.exp(-widening), rel=1e-6
    )
    assert wall.upper == pytest.approx(
        float(expected.interval.upper[0]) * math.exp(widening), rel=1e-6
    )
    assert read_record(result.record_path) == result.record
    assert "`hac_lf`" in result.report_path.read_text()


def test_a_schedule_off_the_defaults_is_decided_by_the_gated_primary(
    project: Project,
) -> None:
    # Any change to a default schedule, here k, gives the gated primary.
    changed = {**LLM_SCHEDULE, "looks": {"n0": 10, "k": 4, "max_looks": 4}}
    spec = BenchmarkSpec.model_validate(spec_fields(**changed))
    result = project.run(project.candidate("same", {}), spec)
    v = result.record.verdict
    assert v.procedure == "t_log_ratio_batch_means"
    assert v.diagnostics.autocorrelated_rounds is not None
    assert v.diagnostics.outlier_rounds is not None


def test_an_alpha_below_the_calibrated_one_keeps_the_run_alive(
    project: Project,
) -> None:
    # hac_lf's table stops at the confidence of 12 looks; alpha 0.01 on
    # a 3-look schedule is above it and would raise at the first look.
    spec = BenchmarkSpec.model_validate(
        spec_fields(alpha=0.01, **LLM_SCHEDULE)
    )
    result = project.run(project.candidate("same", {}), spec)
    assert result.record.verdict.procedure == "t_log_ratio_batch_means"
    assert result.record.verdict.looks == 3


def test_hac_lf_does_not_withhold_the_autocorrelated_wave(
    tmp_path: Path,
) -> None:
    # The kind of recorded wave the gate withholds (above) is decided on
    # its interval under hac_lf, which widens for the dependence the
    # data cannot rule out instead of withholding.
    _, result = wave_run(tmp_path, LLM_SCHEDULE)
    v = result.record.verdict
    assert v.procedure == "hac_lf"
    # The interval clears the bar, so rule 2 fires; the coverage gate is
    # not_available, so the KEEP is held (INCONCLUSIVE under rule 2).
    assert (v.outcome, v.rule) == ("INCONCLUSIVE", 2)
    assert not v.diagnostics.procedure_uncalibrated
    assert v.diagnostics.autocorrelated_rounds is None
    assert v.diagnostics.outlier_rounds is None
    assert v.interval is not None
    assert v.interval.lower > 1.03
    assert v.diagnostics.lag1_autocorrelation is not None
    assert v.diagnostics.lag1_autocorrelation > 0.3
    report = result.report_path.read_text()
    assert "The diagnostic gate withheld the decision" not in report
    assert read_record(result.record_path) == result.record


calibration_blocker = compare_module._calibration_blocker  # pyright: ignore[reportPrivateUsage]
calibration_of = compare_module._calibration  # pyright: ignore[reportPrivateUsage]


def metric_spec(target_class: str, kind: str) -> BenchmarkSpec:
    return BenchmarkSpec.model_validate(
        spec_fields(
            metric={
                "kind": kind,
                "direction": "lower_is_better",
                "unit": "s",
            },
            looks=PYTHON_SCHEDULE[0],
            budget={"max_rounds": 60, "max_seconds": 600.0},
            guardrails=[
                {
                    "metric": g["metric"],
                    "direction": "lower_is_better",
                    "unit": g["unit"],
                    "applicable": False,
                    "reason": "not used here",
                }
                for g in GUARDRAILS
            ],
            target_class=target_class,
            objective="cpu_time" if kind == "cpu" else "wall_time",
        )
    )


@pytest.mark.parametrize("target_class", ["node", "jvm"])
@pytest.mark.parametrize("kind", ["reported", "cpu"])
def test_node_and_jvm_withhold_keep_off_a_wall_metric(
    target_class: str, kind: str
) -> None:
    # The seven Node and Java traces are fresh-process wall timings;
    # the auditor's in-process Node A/A traces failed at 1.03.
    spec = metric_spec(target_class, kind)
    assert keep_blocked(spec)
    assert calibration_of(spec)[0] == "uncalibrated"
    blocker = calibration_blocker(spec)
    assert blocker is not None
    assert blocker[0] == "metric_not_calibrated"
    assert "never be a KEEP" in blocker[1]


@pytest.mark.parametrize("target_class", ["node", "jvm"])
def test_node_and_jvm_on_a_wall_metric_are_calibrated(
    target_class: str,
) -> None:
    spec = metric_spec(target_class, "wall")
    assert calibration_blocker(spec) is None
    assert calibration_of(spec)[0] == "calibrated"


@pytest.mark.parametrize(
    "target_class", ["python_cpu", "native", "web_service", "agentic"]
)
@pytest.mark.parametrize("kind", ["reported", "wall", "cpu"])
def test_the_metric_kind_restriction_is_only_node_and_jvm(
    target_class: str, kind: str
) -> None:
    schedule = (
        WEB_SCHEDULE
        if target_class in {"web_service", "agentic"}
        else PYTHON_SCHEDULE
    )
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            metric={
                "kind": kind,
                "direction": "lower_is_better",
                "unit": "s",
            },
            looks=schedule[0],
            budget={"max_rounds": schedule[1], "max_seconds": 600.0},
            target_class=target_class,
            objective="cpu_time" if kind == "cpu" else "wall_time",
        )
    )
    blocker = calibration_blocker(spec)
    assert blocker is None or blocker[0] != "metric_not_calibrated"


@pytest.mark.parametrize("family", ["node", "java"])
@pytest.mark.parametrize("target_class", ["native", "python_cpu"])
def test_a_node_or_java_runtime_withholds_keep_off_wall_whatever_the_class(
    family: str, target_class: str
) -> None:
    spec = metric_spec(target_class, "reported")
    assert calibration_blocker(spec) is None
    blocker = calibration_blocker(spec, None, frozenset({family}))
    assert blocker is not None
    assert blocker[0] == "metric_not_calibrated"
    assert calibration_of(spec, None, frozenset({family}))[0] == (
        "uncalibrated"
    )


@pytest.mark.parametrize("family", ["node", "java"])
@pytest.mark.parametrize("target_class", ["native", "python_cpu"])
def test_a_node_or_java_runtime_on_wall_is_unchanged(
    family: str, target_class: str
) -> None:
    spec = metric_spec(target_class, "wall")
    families = frozenset({family})
    assert calibration_blocker(spec, None, families) is None
    assert calibration_of(spec, None, families)[0] == "calibrated"


@pytest.mark.parametrize("family", ["ruby", "go", "dotnet", "cc"])
def test_other_runtime_families_do_not_restrict_the_metric(
    family: str,
) -> None:
    spec = metric_spec("python_cpu", "reported")
    assert calibration_blocker(spec, None, frozenset({family})) is None


NODE_BENCH_JS = """\
const { run } = require('./work.js');
const fs = require('fs');
const t0 = process.hrtime.bigint();
const r = run();
const dt = Number(process.hrtime.bigint() - t0) / 1e9;
fs.writeFileSync('out.txt', r + '\\n');
process.stdout.write('PERFPROOF {"value": ' + dt.toFixed(9) + '}\\n');
"""
NODE_WORK_JS = (
    "exports.run = () => { let s = 0; "
    "for (let i = 0; i < {n}; i++) s += i % 7; return 42; };\n"
)


def node_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target_class: str,
    kind: str,
) -> CompareResult:
    """A real ``node bench.js`` comparison, the candidate faster."""
    watch_looks(monkeypatch)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "bench.js").write_text(NODE_BENCH_JS)
    (root / "work.js").write_text(NODE_WORK_JS.replace("{n}", "30000000"))
    project = Project(root=root, commit=commit_all(root), tmp=tmp_path)
    spec = BenchmarkSpec.model_validate(
        spec_fields(
            command=["node", "bench.js"],
            harness=["bench.js"],
            oracle=[],
            config=[],
            correctness={"outputs": [{"path": "out.txt", "compare": "text"}]},
            metric={
                "kind": kind,
                "direction": "lower_is_better",
                "unit": "s",
            },
            objective="wall_time",
            guardrails=NOT_APPLICABLE,
            target_class=target_class,
            budget={"max_rounds": 60, "max_seconds": 900.0},
            looks={"n0": 10, "k": 10, "max_looks": 6},
        )
    )
    candidate = project.candidate("cand", {})
    (candidate / "work.js").write_text(NODE_WORK_JS.replace("{n}", "8000000"))
    return project.run(candidate, spec)


@pytest.mark.skipif(shutil.which("node") is None, reason="no node on PATH")
@pytest.mark.parametrize("target_class", ["native", "python_cpu"])
def test_a_real_node_run_off_wall_is_withheld_whatever_the_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_class: str
) -> None:
    # The runtime family is known only after the probe, so the warning
    # must be written there: compare() used to raise "a rule 2
    # INCONCLUSIVE needs something that blocked KEEP".
    result = node_run(tmp_path, monkeypatch, target_class, "reported")
    v = result.record.verdict
    assert result.record.environments[0].runtime_family == "node"
    assert (v.outcome, v.reason, v.rule) == (
        "INCONCLUSIVE",
        "insufficient_evidence",
        2,
    )
    assert v.calibration == "uncalibrated"
    codes = [w.code for w in result.record.warnings]
    assert codes.count("metric_not_calibrated") == 1
    assert read_record(result.record_path) == result.record


@pytest.mark.skipif(shutil.which("node") is None, reason="no node on PATH")
def test_a_real_node_run_on_wall_is_not_blocked_by_the_metric_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = node_run(tmp_path, monkeypatch, "native", "wall")
    codes = [w.code for w in result.record.warnings]
    assert "metric_not_calibrated" not in codes
