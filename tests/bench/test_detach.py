"""Detached compare: real processes, real signals, real runs.

Nothing here mocks a process. The benchmark is a small Python program
run by the real runner; ``kill -9`` and ``cancel`` hit the real
detached process; the stalls spawn real children in sessions of their
own.
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from perfproof.bench.detach import (
    CANCELLED_NAME,
    JOB_NAME,
    PID_NAME,
    DetachError,
    Identity,
    Job,
    default_timeout,
    deliver,
    find_run,
    format_start,
    kill_tree,
    libproc_table,
    machine_differences,
    process_table,
    ps_table,
    run_status,
)
from perfproof.bench.journal import (
    JOURNAL_NAME,
    MACHINE_NAME,
    Journal,
    JournalError,
    RoundEntry,
    load_journal,
    write_json,
)
from perfproof.bench.runner import round_orders
from perfproof.cli import main
from perfproof.machine import fingerprint
from perfproof.report import read_record
from perfproof.schema import RunRecord, Sample

PY = sys.executable
ROUNDS = 10
GATE_SECONDS = 180.0

BENCH = """\
import os
import subprocess
import time

from work import run

if os.path.exists("stall"):
    kid = subprocess.Popen(["sleep", "300"], start_new_session=True)
    with open(@PIDS@, "a") as f:
        f.write(f"{os.getpid()} {kid.pid}\\n")
    time.sleep(300)
start = time.perf_counter()
result = run()
elapsed = time.perf_counter() - start
with open("out.txt", "w") as f:
    f.write(f"{result}\\n")
print("PERFPROOF {\\"value\\": %.6f}" % elapsed)
"""
TESTS = """\
from work import run


def test_run():
    assert run() == 42
"""
WORK = "import time\n\n\ndef run():\n    time.sleep(0.1)\n    return 42\n"
BROKEN = 'def run():\n    raise SystemExit("baseline broke")\n'

SPEC: dict[str, Any] = {
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
    "budget": {"max_rounds": ROUNDS, "max_seconds": 300.0},
    "looks": {"n0": 10, "k": 2, "max_looks": 1},
    "timeout_seconds": 30.0,
    "harness": ["bench.py"],
    "oracle": ["test_work.py"],
    "config": ["pytest.ini"],
    "objective": "wall_time",
    "guardrails": [
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
    ],
}


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


type Cli = Callable[..., tuple[int, dict[str, Any]]]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Cli:
    def run(*argv: str) -> tuple[int, dict[str, Any]]:
        capsys.readouterr()
        status = main([*argv, "--json"])
        document = json.loads(capsys.readouterr().out)
        return status, cast("dict[str, Any]", document)

    return run


@dataclass
class Project:
    root: Path
    candidate: Path
    cli: Cli
    pids: Path

    def detach(self, *extra: str) -> dict[str, Any]:
        status, doc = self.cli(
            "compare",
            "work",
            "--candidate",
            str(self.candidate),
            "--repo",
            str(self.root),
            "--detach",
            *extra,
        )
        assert status == 0, doc
        return cast("dict[str, Any]", doc["result"])

    def status(self, run_id: str) -> dict[str, Any]:
        status, doc = self.cli("status", run_id, "--repo", str(self.root))
        assert status == 0, doc
        return cast("dict[str, Any]", doc["result"])

    def resume(self, run_id: str) -> tuple[int, dict[str, Any]]:
        return self.cli(
            "compare", "--resume", run_id, "--repo", str(self.root)
        )

    def run_dir(self, run_id: str) -> Path:
        return self.root / ".perfproof" / "runs" / run_id


@pytest.fixture
def project(
    tmp_path: Path, cli: Cli, request: pytest.FixtureRequest
) -> Iterator[Project]:
    root = tmp_path / "repo"
    root.mkdir()
    pids = tmp_path / "pids.txt"
    bench = BENCH.replace("@PIDS@", repr(str(pids)))
    spec = SPEC
    harnessed = getattr(request, "param", None) == "harness"
    if harnessed:
        spec = {
            **SPEC,
            "command": ["python", ".perfproof/harness/bench.py"],
            "harness": [".perfproof/harness/bench.py"],
        }
    else:
        (root / "bench.py").write_text(bench)
    (root / "work.py").write_text(WORK)
    (root / "test_work.py").write_text(TESTS)
    (root / "pytest.ini").write_text(
        "[pytest]\naddopts = -p no:cacheprovider\n"
    )
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "baseline")
    if harnessed:
        # Written after the commit, so neither the baseline checkout nor
        # a copy of the tree has it: only installing it makes it exist.
        harness = root / ".perfproof" / "harness"
        harness.mkdir(parents=True)
        (harness / "bench.py").write_text(
            "import os, sys\nsys.path.insert(0, os.getcwd())\n" + bench
        )
    candidate = tmp_path / "candidate"
    shutil.copytree(
        root,
        candidate,
        ignore=shutil.ignore_patterns(".git", ".perfproof"),
    )
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec))
    status, doc = cli(
        "bench",
        "define",
        "work",
        "--spec",
        str(spec_file),
        "--repo",
        str(root),
    )
    assert status == 0, doc
    proj = Project(root, candidate, cli, pids)
    yield proj
    # Whatever a test left running must not outlive it.
    runs = root / ".perfproof" / "runs"
    for run_dir in runs.glob("*") if runs.is_dir() else ():
        identity = read_pid(run_dir)
        if identity is not None:
            kill_tree(identity, grace_seconds=0)
    for pid in pids_of(pids):
        kill(pid)


def read_pid(run_dir: Path) -> Identity | None:
    try:
        doc = json.loads((run_dir / PID_NAME).read_text())
    except (OSError, ValueError):
        return None
    return Identity(int(doc["pid"]), str(doc["start"]))


def pids_of(pids: Path) -> list[int]:
    try:
        text = pids.read_text()
    except OSError:
        return []
    return [int(w) for w in text.split()]


def kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def alive(pid: int) -> bool:
    row = process_table(pid).get(pid)
    return row is not None and not row.zombie


def wait_for(
    what: str, check: Callable[[], bool], seconds: float = GATE_SECONDS
) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.05)


def wait_state(proj: Project, run_id: str, *states: str) -> dict[str, Any]:
    seen: list[dict[str, Any]] = []

    def reached() -> bool:
        seen[:] = [proj.status(run_id)]
        return seen[0]["state"] in states

    wait_for(f"state {states}", reached)
    return seen[0]


def wait_rounds(proj: Project, run_id: str, rounds: int) -> None:
    wait_for(
        f"{rounds} rounds",
        lambda: proj.status(run_id)["rounds_done"] >= rounds,
    )


def journal_rounds(run_dir: Path) -> list[dict[str, Any]]:
    lines = (run_dir / JOURNAL_NAME).read_text().splitlines()
    return [cast("dict[str, Any]", json.loads(line)) for line in lines]


# A run that outlasts the caller


def test_detached_run_outlives_its_caller_and_finishes_through_status(
    project: Project,
) -> None:
    argv = [
        PY,
        "-m",
        "perfproof",
        "compare",
        "work",
        "--candidate",
        str(project.candidate),
        "--repo",
        str(project.root),
        "--detach",
        "--json",
    ]
    began = time.monotonic()
    done = subprocess.run(
        argv,
        cwd=project.root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    returned = time.monotonic() - began
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)["result"]
    run_id = result["run_id"]
    assert result["state"] == "running"
    # The caller is gone; the run is not.
    first = project.status(run_id)
    assert first["state"] == "running"
    wait_rounds(project, run_id, 1)
    mid = project.status(run_id)
    if mid["state"] == "running":
        assert "verdict" not in mid
        assert mid["phase"] == "rounds"
        assert mid["max_rounds"] == ROUNDS
    final = wait_state(project, run_id, "finished")
    # The tool call returned before the comparison could have ended.
    record = read_record(project.run_dir(run_id) / "record.json")
    wall = (record.finished_at - record.started_at).total_seconds()
    assert returned < wall
    assert final["rounds_done"] == ROUNDS
    assert final["verdict"]["outcome"] == record.verdict.outcome
    assert final["verdict"]["rounds"] == record.verdict.rounds == ROUNDS
    assert len(journal_rounds(project.run_dir(run_id))) == ROUNDS
    pid = read_pid_of(project, run_id).pid
    wait_for("the process to exit", lambda: not alive(pid), seconds=30)


def read_pid_of(proj: Project, run_id: str) -> Identity:
    identity = read_pid(proj.run_dir(run_id))
    assert identity is not None
    return identity


def test_a_stopped_compare_is_failed_with_its_code(
    project: Project, tmp_path: Path
) -> None:
    (project.root / "work.py").write_text(BROKEN)
    git(project.root, "commit", "-q", "-am", "break the baseline")
    # The frozen spec locks only the harness, oracle and config.
    run_id = project.detach()["run_id"]
    final = wait_state(project, run_id, "failed", "finished")
    assert final["state"] == "failed"
    assert final["error"]["code"] == "broken_baseline"
    assert "verdict" not in final
    assert not (project.run_dir(run_id) / "record.json").exists()
    status, doc = project.resume(run_id)
    assert status == 1
    assert doc["error"]["code"] == "not_resumable"


# kill -9, interrupted, resume


@pytest.mark.parametrize("project", ["harness"], indirect=True)
def test_a_harness_only_in_the_main_checkout_runs_and_resumes(
    project: Project,
) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    assert project.status(run_id)["state"] == "interrupted"
    status, doc = project.resume(run_id)
    assert status == 0, doc
    final = wait_state(project, run_id, "finished", "failed")
    assert final["state"] == "finished", final
    record = read_record(run_dir / "record.json")
    assert record.verdict.rounds == ROUNDS
    assert {g.gate: g.status for g in record.gates}["lock"] == "passed"
    for tree in (project.candidate, project.root):
        assert (tree / ".perfproof" / "harness" / "bench.py").is_file()


@pytest.mark.parametrize("project", ["harness"], indirect=True)
def test_a_detached_harness_run_finishes_with_a_verdict(
    project: Project,
) -> None:
    run_id = project.detach()["run_id"]
    final = wait_state(project, run_id, "finished", "failed")
    assert final["state"] == "finished", final
    assert final["verdict"]["rounds"] == ROUNDS


def start_and_kill(project: Project, after_rounds: int = 3) -> str:
    run_id = project.detach()["run_id"]
    wait_rounds(project, run_id, after_rounds)
    pid = read_pid_of(project, run_id).pid
    os.kill(pid, signal.SIGKILL)
    wait_for("the process to be gone", lambda: not alive(pid))
    return run_id


def test_kill_9_is_interrupted_and_resume_loses_and_repeats_no_round(
    project: Project,
) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    killed = project.status(run_id)
    assert killed["state"] == "interrupted"
    assert "verdict" not in killed
    assert not (run_dir / "record.json").exists()
    # The benchmark child the dead process left may finish its round.
    time.sleep(0.5)
    before = journal_rounds(run_dir)
    assert 3 <= len(before) < ROUNDS
    first_pid = read_pid_of(project, run_id)

    status, doc = project.resume(run_id)
    assert status == 0, doc
    assert doc["result"]["run_id"] == run_id
    assert read_pid_of(project, run_id) != first_pid
    final = wait_state(project, run_id, "finished", "failed")
    assert final["state"] == "finished", final

    after = journal_rounds(run_dir)
    assert [line["round"] for line in after] == list(range(ROUNDS))
    # The rounds before the kill are the ones measured then.
    assert after[: len(before)] == before
    record = read_record(run_dir / "record.json")
    assert record.verdict.rounds == ROUNDS
    resumed = [w for w in record.warnings if w.code == "resumed"]
    assert len(resumed) == 1
    assert f"after {len(before)} completed rounds" in resumed[0].message
    with np.load(run_dir / "samples.npz", allow_pickle=False) as data:
        assert data["round"].tolist() == [i // 2 for i in range(2 * ROUNDS)]
        assert data["position"].tolist() == [0, 1] * ROUNDS
        first = data["candidate"][data["position"] == 0].tolist()
        value = data["value"].tolist()
    # The seeded order held across the kill.
    orders = round_orders(record.order_seed, ROUNDS)
    assert first == [int(a == "candidate") for a in orders]
    for entry in before:
        i = entry["round"]
        assert value[2 * i : 2 * i + 2] == [
            s["value"] for s in entry["samples"]
        ]
    # Every gate ran again in the resumed attempt, in logs of its own.
    assert (run_dir / "logs" / "resume-1" / "outputs").is_dir()
    assert {g.gate for g in record.gates} >= {"correctness", "lock", "runtime"}


def test_second_resume_while_the_first_runs_is_refused(
    project: Project,
) -> None:
    run_id = start_and_kill(project)
    status, doc = project.resume(run_id)
    assert status == 0, doc
    status, doc = project.resume(run_id)
    assert status == 1
    assert doc["error"]["code"] == "not_resumable"
    assert doc["error"]["details"]["state"] == "running"
    wait_state(project, run_id, "finished", "failed")


def test_resume_on_a_changed_machine_is_refused(project: Project) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    saved = json.loads((run_dir / MACHINE_NAME).read_text())
    saved["machine"]["kernel"] = "9.9.9-other"
    saved["machine"]["host_id"] = "some-other-host"
    (run_dir / MACHINE_NAME).write_text(json.dumps(saved))
    rounds = (run_dir / JOURNAL_NAME).read_bytes()
    pid = read_pid_of(project, run_id)

    status, doc = project.resume(run_id)
    assert status == 1
    assert doc["error"]["code"] == "machine_changed"
    assert set(doc["error"]["details"]["differs"]) == {"kernel", "host_id"}
    # Nothing started and nothing was touched.
    assert read_pid_of(project, run_id) == pid
    assert (run_dir / JOURNAL_NAME).read_bytes() == rounds
    assert project.status(run_id)["state"] == "interrupted"
    job = json.loads((run_dir / JOB_NAME).read_text())
    assert job["resumes"] == []


def test_torn_last_line_is_cut_and_the_round_is_run_again(
    project: Project,
) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    time.sleep(0.5)
    whole = journal_rounds(run_dir)
    with (run_dir / JOURNAL_NAME).open("ab") as f:
        f.write(b'{"round": %d, "sam' % len(whole))
    assert len(load_journal(run_dir)) == len(whole)
    status, doc = project.resume(run_id)
    assert status == 0, doc
    final = wait_state(project, run_id, "finished", "failed")
    assert final["state"] == "finished"
    lines = journal_rounds(run_dir)
    assert [line["round"] for line in lines] == list(range(ROUNDS))
    assert lines[: len(whole)] == whole


def test_a_damaged_journal_refuses_the_resume(project: Project) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    time.sleep(0.5)
    lines = (run_dir / JOURNAL_NAME).read_text().splitlines()
    del lines[1]
    (run_dir / JOURNAL_NAME).write_text("\n".join(lines) + "\n")
    status, doc = project.resume(run_id)
    assert status == 1
    assert doc["error"]["code"] == "invalid_journal"
    assert project.status(run_id)["state"] == "interrupted"


# Cancel, timeout and the process tree


def stall(project: Project, run_id: str | None = None) -> tuple[int, ...]:
    """Make the candidate's next execution spawn a child and hang."""
    (project.candidate / "stall").write_text("")
    wait_for("the stalled execution", lambda: bool(pids_of(project.pids)))
    pids = pids_of(project.pids)
    assert len(pids) == 2
    return (*pids,)


def test_cancel_leaves_no_child_process(project: Project) -> None:
    run_id = project.detach()["run_id"]
    wait_rounds(project, run_id, 1)
    root = read_pid_of(project, run_id).pid
    bench, sleeper = stall(project)
    assert all(alive(p) for p in (root, bench, sleeper))
    assert project.status(run_id)["state"] == "running"

    status, doc = project.cli(
        "cancel", run_id, "--repo", str(project.root), "--grace", "5"
    )
    assert status == 0, doc
    assert doc["result"] == {"run_id": run_id, "state": "cancelled"}
    wait_for(
        "every process to be gone",
        lambda: not any(alive(p) for p in (root, bench, sleeper)),
        seconds=10,
    )
    final = project.status(run_id)
    assert final["state"] == "cancelled"
    assert "verdict" not in final
    assert not (project.run_dir(run_id) / "record.json").exists()
    assert (project.run_dir(run_id) / CANCELLED_NAME).exists()
    # A cancelled run is not resumed, and cancelling twice is harmless.
    status, doc = project.resume(run_id)
    assert doc["error"]["code"] == "not_resumable"
    status, doc = project.cli("cancel", run_id, "--repo", str(project.root))
    assert status == 0
    assert doc["result"]["state"] == "cancelled"


def test_cancel_after_the_record_is_refused(project: Project) -> None:
    run_id = project.detach()["run_id"]
    wait_state(project, run_id, "finished")
    status, doc = project.cli("cancel", run_id, "--repo", str(project.root))
    assert status == 1
    assert doc["error"]["code"] == "already_finished"
    assert project.status(run_id)["state"] == "finished"


def test_hard_timeout_kills_the_whole_tree(project: Project) -> None:
    (project.candidate / "stall").write_text("")
    run_id = project.detach("--timeout", "15")["run_id"]
    root = read_pid_of(project, run_id).pid
    wait_for("the stalled execution", lambda: bool(pids_of(project.pids)))
    bench, sleeper = pids_of(project.pids)
    final = wait_state(project, run_id, "timed_out", "finished", "failed")
    assert final["state"] == "timed_out", final
    assert "verdict" not in final
    wait_for(
        "every process to be gone",
        lambda: not any(alive(p) for p in (root, bench, sleeper)),
        seconds=20,
    )
    assert not (project.run_dir(run_id) / "record.json").exists()
    _status, doc = project.resume(run_id)
    assert doc["error"]["code"] == "not_resumable"


# A reused PID


def test_a_reused_pid_is_not_mistaken_for_the_run(project: Project) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    bystander = subprocess.Popen(["sleep", "300"])
    try:
        row = process_table(bystander.pid)[bystander.pid]
        # The run's recorded PID now belongs to an unrelated process.
        (run_dir / PID_NAME).write_text(
            json.dumps({"pid": bystander.pid, "start": "not its start"})
        )
        assert project.status(run_id)["state"] == "interrupted"
        # With the right start time the same file does read as running.
        (run_dir / PID_NAME).write_text(
            json.dumps({"pid": bystander.pid, "start": row.start})
        )
        assert project.status(run_id)["state"] == "running"
        (run_dir / PID_NAME).write_text(
            json.dumps({"pid": bystander.pid, "start": "not its start"})
        )
        status, doc = project.cli(
            "cancel", run_id, "--repo", str(project.root)
        )
        assert status == 0, doc
        assert doc["result"]["state"] == "cancelled"
        assert bystander.poll() is None
        assert alive(bystander.pid)
        assert kill_tree(Identity(bystander.pid, "not its start")) == []
        assert bystander.poll() is None
    finally:
        bystander.kill()
        bystander.wait(timeout=10)


# Units


def sample(version: str, index: int, position: int) -> Sample:
    return Sample.model_validate(
        {
            "version": version,
            "round": index,
            "position": position,
            "value": 0.5,
            "wall_seconds": 0.6,
            "cpu_seconds": None,
            "max_rss_bytes": None,
            "exit_code": 0,
            "command_hash": "0" * 64,
        }
    )


def entry(index: int) -> RoundEntry:
    return RoundEntry(
        samples=(
            sample("baseline", index, 0),
            sample("candidate", index, 1),
        ),
        steps={"baseline": 1e-6, "candidate": 1e-6},
        elapsed=index + 1.0,
        duration=1.0,
    )


def test_journal_refuses_a_repeated_or_skipped_round(tmp_path: Path) -> None:
    journal = Journal(tmp_path, ROUNDS)
    journal.round_done(0, entry(0))
    with pytest.raises(ValueError, match="round 0 written"):
        journal.round_done(0, entry(0))
    with pytest.raises(ValueError, match="round 2 written"):
        journal.round_done(2, entry(2))
    journal.round_done(1, entry(1))
    journal.close()
    assert [e.samples[0].round for e in load_journal(tmp_path)] == [0, 1]
    progress = json.loads((tmp_path / "progress.json").read_text())
    assert (progress["rounds_done"], progress["max_rounds"]) == (2, ROUNDS)


def test_journal_round_trips_a_round(tmp_path: Path) -> None:
    journal = Journal(tmp_path, ROUNDS)
    journal.round_done(0, entry(0))
    journal.close()
    assert load_journal(tmp_path) == [entry(0)]


def test_journal_round_trips_reported_guardrail_values(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path, ROUNDS)
    reported = {
        "baseline": {"p99_latency": (0.0412, 1e-4)},
        "candidate": {},
    }
    journal.round_done(0, replace(entry(0), reported=reported))
    journal.close()
    (got,) = load_journal(tmp_path)
    assert got.reported == reported


def test_a_journal_without_reported_values_reads_them_as_unknown(
    tmp_path: Path,
) -> None:
    journal = Journal(tmp_path, ROUNDS)
    journal.round_done(0, entry(0))
    journal.close()
    path = tmp_path / JOURNAL_NAME
    doc = json.loads(path.read_text())
    del doc["reported"]
    path.write_text(json.dumps(doc) + "\n")
    assert load_journal(tmp_path)[0].reported == {}


@pytest.mark.parametrize(
    "reported",
    [
        None,
        {"baseline": [1]},
        {"baseline": {"p99": [1.0]}},
        {"baseline": {"p99": [-1, 0]}},
        {"baseline": {"p99": [1, -1]}},
        {"baseline": {"p99": ["1", 0]}},
        {"bogus": {"p99": [1, 0]}},
    ],
)
def test_a_damaged_reported_field_is_a_journal_error(
    tmp_path: Path, reported: object
) -> None:
    journal = Journal(tmp_path, ROUNDS)
    journal.round_done(0, entry(0))
    journal.close()
    path = tmp_path / JOURNAL_NAME
    doc = json.loads(path.read_text())
    doc["reported"] = reported
    path.write_text(json.dumps(doc) + "\n")
    with pytest.raises(JournalError):
        load_journal(tmp_path)


def test_journal_repair_cuts_only_a_torn_tail(tmp_path: Path) -> None:
    journal = Journal(tmp_path, ROUNDS)
    journal.round_done(0, entry(0))
    journal.close()
    path = tmp_path / JOURNAL_NAME
    whole = path.read_bytes()
    path.write_bytes(whole + b'{"round": 1')
    assert len(load_journal(tmp_path)) == 1
    assert path.read_bytes() != whole
    assert len(load_journal(tmp_path, repair=True)) == 1
    assert path.read_bytes() == whole
    path.write_bytes(whole + b"not json\n")
    with pytest.raises(JournalError):
        load_journal(tmp_path)


def test_machine_differences_name_the_identity_probes() -> None:
    now = fingerprint()
    assert machine_differences(now, now) == []
    other = now.model_copy(update={"load_average": (9.0, 9.0, 9.0)})
    assert machine_differences(now, other) == []
    moved = now.model_copy(update={"cpu_model": "another"})
    assert "cpu_model" in machine_differences(now, moved)


def test_find_run_takes_a_unique_prefix(tmp_path: Path) -> None:
    runs = tmp_path / ".perfproof" / "runs"
    for name in ("20260101T000000Z-7a2e11", "20260101T000001Z-7b3f22"):
        (runs / name).mkdir(parents=True)
    assert find_run(tmp_path, "7a2e").name.endswith("7a2e11")
    assert find_run(tmp_path, "20260101T000001Z").name.endswith("7b3f22")
    for bad in ("7", "20260101", "zzzz", ""):
        with pytest.raises(DetachError) as caught:
            find_run(tmp_path, bad)
        assert caught.value.code == "not_found"


def test_default_timeout_is_twice_the_budget_plus_an_hour() -> None:
    assert default_timeout(300.0) == 4200.0


def test_resume_takes_no_benchmark_settings(
    project: Project, cli: Cli
) -> None:
    status, doc = cli(
        "compare", "work", "--resume", "x", "--repo", str(project.root)
    )
    assert status == 1
    assert doc["error"]["code"] == "usage"
    status, doc = cli("compare", "--repo", str(project.root))
    assert doc["error"]["code"] == "usage"
    status, doc = cli(
        "compare",
        "work",
        "--candidate",
        str(project.candidate),
        "--timeout",
        "5",
        "--repo",
        str(project.root),
    )
    assert doc["error"]["code"] == "usage"


def test_timeout_above_the_alarm_limit_is_refused(
    project: Project, cli: Cli
) -> None:
    for value in ("2147483648", "0"):
        status, doc = cli(
            "compare",
            "work",
            "--candidate",
            str(project.candidate),
            "--detach",
            "--timeout",
            value,
            "--repo",
            str(project.root),
        )
        assert status == 1
        assert doc["error"]["code"] == "usage"


def test_status_of_an_unknown_run_is_not_found(
    project: Project, cli: Cli
) -> None:
    status, doc = cli("status", "nothing", "--repo", str(project.root))
    assert status == 1
    assert doc["error"]["code"] == "not_found"


def test_status_reads_a_directory_with_no_files_as_interrupted(
    tmp_path: Path,
) -> None:
    state = run_status(tmp_path)
    assert (state.state, state.rounds_done) == ("interrupted", 0)


def test_record_type_is_what_status_reads(project: Project) -> None:
    run_id = project.detach()["run_id"]
    final = wait_state(project, run_id, "finished")
    record = read_record(project.run_dir(run_id) / "record.json")
    assert isinstance(record, RunRecord)
    assert final["verdict"]["record_path"].endswith("record.json")


# A killed run's benchmark child, a changed tree, an unreadable table


def kill_while_stalled(project: Project) -> tuple[str, int, int]:
    """Start a run, stall an execution, then kill -9 the run."""
    run_id = project.detach()["run_id"]
    wait_rounds(project, run_id, 1)
    root = read_pid_of(project, run_id).pid
    bench, sleeper = stall(project)
    os.kill(root, signal.SIGKILL)
    wait_for("the run to be gone", lambda: not alive(root))
    # The benchmark runs for 300 s, far past any wait in these tests.
    assert alive(bench)
    assert project.status(run_id)["state"] == "interrupted"
    return run_id, bench, sleeper


def test_cancel_ends_the_benchmark_a_kill_9_left_behind(
    project: Project,
) -> None:
    run_id, bench, _ = kill_while_stalled(project)
    status, doc = project.cli("cancel", run_id, "--repo", str(project.root))
    assert status == 0, doc
    assert doc["result"]["state"] == "cancelled"
    wait_for("the benchmark to be gone", lambda: not alive(bench), seconds=10)


def test_resume_ends_the_orphan_before_it_measures(project: Project) -> None:
    run_id, bench, _ = kill_while_stalled(project)
    (project.candidate / "stall").unlink()
    status, doc = project.resume(run_id)
    assert status == 0, doc
    # It was ended by the resume itself, not by running out its sleep.
    assert not alive(bench)
    final = wait_state(project, run_id, "finished", "failed")
    assert final["state"] == "finished", final


@pytest.mark.parametrize("name", ["work.py", "bench.py"])
def test_resume_after_the_code_changed_is_refused(
    project: Project, name: str
) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    # A candidate file, then (second case) a locked harness file.
    (project.candidate / name).write_text(
        (project.candidate / name).read_text() + "\n# edited\n"
    )
    rounds = (run_dir / JOURNAL_NAME).read_bytes()
    status, doc = project.resume(run_id)
    assert status == 1
    assert doc["error"]["code"] == "not_resumable"
    assert "the code changed since this run started" in doc["error"]["message"]
    assert (run_dir / JOURNAL_NAME).read_bytes() == rounds
    assert project.status(run_id)["state"] == "interrupted"


def test_a_failed_process_table_read_is_not_taken_as_gone(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = project.detach()["run_id"]
    wait_rounds(project, run_id, 1)

    def broken(only: int | None = None) -> dict[int, Any]:
        raise OSError("ps failed")

    monkeypatch.setattr("perfproof.bench.detach.process_table", broken)
    assert project.status(run_id)["state"] == "unknown"
    status, doc = project.resume(run_id)
    assert status == 1
    assert doc["error"]["details"]["state"] == "unknown"
    status, doc = project.cli("cancel", run_id, "--repo", str(project.root))
    assert status == 1
    assert not (project.run_dir(run_id) / CANCELLED_NAME).exists()
    monkeypatch.undo()
    assert project.status(run_id)["state"] == "running"
    wait_state(project, run_id, "finished")


def test_cancel_keeps_a_failed_run_failed(project: Project) -> None:
    (project.root / "work.py").write_text(BROKEN)
    git(project.root, "commit", "-q", "-am", "break the baseline")
    run_id = project.detach()["run_id"]
    assert wait_state(project, run_id, "failed")["state"] == "failed"
    status, doc = project.cli("cancel", run_id, "--repo", str(project.root))
    assert status == 0, doc
    assert doc["result"]["state"] == "failed"
    assert not (project.run_dir(run_id) / CANCELLED_NAME).exists()


def test_the_deadline_is_kept_across_a_resume(project: Project) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    before = json.loads((run_dir / JOB_NAME).read_text())
    assert before["deadline"] > time.time()
    status, doc = project.resume(run_id)
    assert status == 0, doc
    after = json.loads((run_dir / JOB_NAME).read_text())
    assert after["deadline"] == before["deadline"]
    wait_state(project, run_id, "finished", "failed")


def test_resume_after_the_deadline_is_refused(project: Project) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    job = json.loads((run_dir / JOB_NAME).read_text())
    job["deadline"] = time.time() - 1
    (run_dir / JOB_NAME).write_text(json.dumps(job))
    status, doc = project.resume(run_id)
    assert status == 1
    assert doc["error"]["code"] == "not_resumable"
    assert "hard timeout" in doc["error"]["message"]


KILL_AFTER_APPEND = """\
import os
import signal
import sys
from pathlib import Path

import perfproof.bench.journal as journal

original = journal.Journal._progress


def progress(self, phase):
    if self._count == 3:
        os.kill(os.getpid(), signal.SIGKILL)
    original(self, phase)


journal.Journal._progress = progress
from perfproof.bench.detach import run_detached

raise SystemExit(run_detached(Path(sys.argv[1])))
"""


def test_kill_after_the_append_before_the_progress_update(
    project: Project,
) -> None:
    spec_hash = next(
        (project.root / ".perfproof" / "benchmarks" / "work").glob("*.json")
    ).stem
    run_id = "20260101T000000Z-aaaaaa"
    run_dir = project.run_dir(run_id)
    run_dir.mkdir(parents=True)
    job = Job(
        repo=str(project.root),
        name="work",
        spec_hash=spec_hash,
        baseline_root=str(project.root),
        candidate_root=str(project.candidate),
        baseline_commit=git(project.root, "rev-parse", "HEAD"),
        order_seed=7,
        timeout_seconds=600.0,
        deadline=time.time() + 600.0,
    )
    write_json(run_dir / JOB_NAME, job.to_json())
    proc = subprocess.Popen(
        [PY, "-c", KILL_AFTER_APPEND, str(run_dir)],
        cwd=project.root,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    row = process_table(proc.pid)[proc.pid]
    write_json(run_dir / PID_NAME, {"pid": proc.pid, "start": row.start})
    assert proc.wait(timeout=GATE_SECONDS) == -signal.SIGKILL
    before = journal_rounds(run_dir)
    assert [line["round"] for line in before] == [0, 1, 2]
    # The progress file is one round behind the journal.
    progress = json.loads((run_dir / "progress.json").read_text())
    assert progress["rounds_done"] == 2
    state = project.status(run_id)
    assert (state["state"], state["rounds_done"]) == ("interrupted", 3)

    status, doc = project.resume(run_id)
    assert status == 0, doc
    final = wait_state(project, run_id, "finished", "failed")
    assert final["state"] == "finished", final
    after = journal_rounds(run_dir)
    assert [line["round"] for line in after] == list(range(ROUNDS))
    assert after[:3] == before


def test_a_file_added_since_the_start_refuses_the_resume(
    project: Project,
) -> None:
    run_id = start_and_kill(project)
    run_dir = project.run_dir(run_id)
    # Empty, but a module the benchmark could now import.
    (project.candidate / "fast.py").write_text("")
    # Caches and declared outputs are not changes.
    (project.candidate / "__pycache__").mkdir(exist_ok=True)
    (project.candidate / "__pycache__" / "x.pyc").write_text("")
    (project.candidate / "node_modules").mkdir()
    (project.candidate / "node_modules" / "m.js").write_text("")
    rounds = (run_dir / JOURNAL_NAME).read_bytes()
    status, doc = project.resume(run_id)
    assert status == 1
    assert doc["error"]["code"] == "not_resumable"
    assert doc["error"]["details"]["changed"] == ["candidate:+fast.py"]
    assert (run_dir / JOURNAL_NAME).read_bytes() == rounds
    (project.candidate / "fast.py").unlink()
    status, doc = project.resume(run_id)
    assert status == 0, doc
    wait_state(project, run_id, "finished", "failed")


def test_a_resume_is_killed_at_the_deadline_of_the_whole_run(
    project: Project,
) -> None:
    # The stall exists from the start, so it is part of the trees.
    (project.candidate / "stall").write_text("")
    run_id = project.detach()["run_id"]
    wait_for("the stalled execution", lambda: bool(pids_of(project.pids)))
    root = read_pid_of(project, run_id).pid
    os.kill(root, signal.SIGKILL)
    wait_for("the run to be gone", lambda: not alive(root))
    assert project.status(run_id)["state"] == "interrupted"
    run_dir = project.run_dir(run_id)
    job = json.loads((run_dir / JOB_NAME).read_text())
    # The timeout was 4200 s; only a few seconds of it are left.
    job["deadline"] = time.time() + 6
    (run_dir / JOB_NAME).write_text(json.dumps(job))
    status, doc = project.resume(run_id)
    assert status == 0, doc
    resumed = read_pid_of(project, run_id).pid
    final = wait_state(project, run_id, "timed_out", "finished", "failed")
    assert final["state"] == "timed_out", final
    assert "verdict" not in final
    wait_for(
        "the resumed run to be gone", lambda: not alive(resumed), seconds=20
    )
    for pid in pids_of(project.pids):
        assert not alive(pid)


needs_mac = pytest.mark.skipif(
    sys.platform != "darwin", reason="libproc and sandbox-exec are macOS"
)


def test_a_one_figure_day_start_reads_the_same_both_ways() -> None:
    # 2026-10-03 12:00:00 local time: ps pads the day, %e does too.
    stamp = time.mktime((2026, 10, 3, 12, 0, 0, 0, 0, -1))
    padded = time.strftime("%a %b %e %H:%M:%S %Y", time.localtime(stamp))
    assert "  " in padded
    assert format_start(int(stamp)) == " ".join(padded.split())
    assert format_start(int(stamp)).split()[2] == "3"


@needs_mac
def test_the_libproc_table_reads_what_ps_reads() -> None:
    mine = os.getpid()
    try:
        ps_all = ps_table(None)
    except OSError:
        pytest.skip("ps can't run here")
    everyone = libproc_table(None)
    assert everyone is not None
    shared = sorted(set(everyone) & set(ps_all))
    assert len(shared) > 10
    assert mine in shared
    for pid in shared:
        assert everyone[pid] == ps_all[pid], pid
    assert libproc_table(mine) == {mine: ps_all[mine]}
    assert libproc_table(2**22 - 1) == {}


@needs_mac
def test_a_denied_libproc_is_not_taken_as_gone(tmp_path: Path) -> None:
    profile = tmp_path / "deny.sb"
    profile.write_text(
        "(version 1)\n(allow default)\n(deny process-info-pidinfo)\n"
    )
    code = (
        "import os\n"
        "from perfproof.bench.detach import libproc_table, process_table\n"
        "me = os.getpid()\n"
        "assert libproc_table(me) is None\n"
        "assert libproc_table(None) is None\n"
        "try:\n"
        "    process_table(me)\n"
        "except OSError as exc:\n"
        "    print('OSError', exc)\n"
        "else:\n"
        "    print('table')\n"
    )
    done = subprocess.run(
        ["sandbox-exec", "-f", str(profile), PY, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith(("OSError", "table")), done.stdout


@needs_mac
def test_detach_starts_where_the_sandbox_refuses_to_run_ps(
    project: Project, tmp_path: Path
) -> None:
    # A confined agent (the eval's profile, Claude Code's sandbox) can't
    # exec a setuid program such as ps, even under `allow default`.
    profile = tmp_path / "allow.sb"
    profile.write_text("(version 1)\n(allow default)\n")
    confined = ["sandbox-exec", "-f", str(profile)]
    probe = subprocess.run(
        [*confined, "ps", "-p", "1"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if probe.returncode == 0:
        pytest.skip("this sandbox runs ps")
    done = subprocess.run(
        [
            *confined,
            PY,
            "-m",
            "perfproof",
            "compare",
            "work",
            "--candidate",
            str(project.candidate),
            "--repo",
            str(project.root),
            "--detach",
            "--json",
        ],
        cwd=project.root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    run_id = json.loads(done.stdout)["result"]["run_id"]
    assert project.status(run_id)["state"] == "running"
    status, doc = project.cli("cancel", run_id, "--repo", str(project.root))
    assert status == 0, doc


def refused(pid: int, sig: int) -> None:
    raise PermissionError(1, "Operation not permitted")


def test_eperm_for_a_process_that_is_gone_is_taken_as_exiting() -> None:
    child = subprocess.Popen([PY, "-c", "pass"])
    start = process_table(child.pid)[child.pid].start
    child.wait()
    deliver(refused, child.pid, signal.SIGTERM, start)


def test_eperm_for_a_reused_pid_is_taken_as_exiting() -> None:
    deliver(refused, os.getpid(), signal.SIGTERM, "Mon Jan 1 00:00:00 1990")


def test_eperm_for_a_live_process_is_raised() -> None:
    child = subprocess.Popen([PY, "-c", "import time; time.sleep(60)"])
    try:
        start = process_table(child.pid)[child.pid].start
        with pytest.raises(PermissionError):
            deliver(refused, child.pid, signal.SIGTERM, start)
    finally:
        child.kill()
        child.wait()
