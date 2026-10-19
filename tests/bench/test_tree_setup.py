"""Setup commands: timeouts, leftovers, and the ignored-program hint."""

import errno
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from perfproof.bench.compare import CompareStoppedError, compare
from perfproof.bench.lock import build_manifest
from perfproof.bench.tree_setup import (
    SetupFailedError,
    SetupHostError,
    ignored_program_hint,
    run_setup,
)
from perfproof.schema import BenchmarkSpec, SetupSpec

PY = sys.executable
SPAWN = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', "
    '\'import os, time; open("child.pid", "w").write(str(os.getpid())); '
    "time.sleep(60)']); "
    "time.sleep({parent})"
)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def dead_soon(pid: int) -> bool:
    for _ in range(100):
        if not alive(pid):
            return True
        time.sleep(0.05)
    return False


def read_pid(tree: Path) -> int:
    pid_file = tree / "child.pid"
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text():
            return int(pid_file.read_text())
        time.sleep(0.05)
    msg = "the child never started"
    raise AssertionError(msg)


def setup(code: str, timeout: float) -> SetupSpec:
    return SetupSpec(commands=((PY, "-c", code),), timeout_seconds=timeout)


def test_timeout_kills_the_whole_process_tree(tmp_path: Path) -> None:
    spec = setup(SPAWN.format(parent=60), 2.0)
    with pytest.raises(SetupFailedError) as caught:
        run_setup(spec, tmp_path, dict(os.environ), tmp_path / "logs", "b")
    assert caught.value.output.timed_out is True
    assert dead_soon(read_pid(tmp_path))


def test_background_child_of_a_successful_setup_is_reaped(
    tmp_path: Path,
) -> None:
    spec = setup(SPAWN.format(parent=0.5), 30.0)
    run_setup(spec, tmp_path, dict(os.environ), tmp_path / "logs", "b")
    assert dead_soon(read_pid(tmp_path))


def test_failure_that_mentions_timing_out_is_not_a_timeout(
    tmp_path: Path,
) -> None:
    code = "import sys; print('timed out upstream', file=sys.stderr); exit(3)"
    with pytest.raises(SetupFailedError) as caught:
        run_setup(
            setup(code, 30.0), tmp_path, dict(os.environ), tmp_path / "l", "b"
        )
    assert caught.value.output.timed_out is False
    assert caught.value.output.exit_code == 3


def test_hint_for_a_non_python_program_is_generic(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("build/\n.venv/\n")
    binary = ignored_program_hint(tmp_path, ".", "build/tool", has_setup=False)
    assert binary is not None
    assert "git-ignored" in binary
    assert "uv" not in binary
    assert "build command" in binary
    python = ignored_program_hint(
        tmp_path, ".", ".venv/bin/python", has_setup=False
    )
    assert python is not None
    assert "uv run" in python
    assert (
        ignored_program_hint(tmp_path, ".", "src/tool", has_setup=False)
        is None
    )


def raise_at_launch(
    monkeypatch: pytest.MonkeyPatch, code: int, filename: str | None = None
) -> None:
    def popen(argv: list[str], **_kwargs: object) -> subprocess.Popen[bytes]:
        raise OSError(code, os.strerror(code), filename or argv[0])

    monkeypatch.setattr("perfproof.bench.tree_setup.subprocess.Popen", popen)


@pytest.mark.parametrize("code", [errno.EMFILE, errno.EAGAIN, errno.E2BIG])
def test_host_launch_errors_are_not_the_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    raise_at_launch(monkeypatch, code)
    with pytest.raises(SetupHostError):
        run_setup(setup("pass", 5.0), tmp_path, {}, tmp_path / "logs", "b")


def test_a_missing_program_is_the_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raise_at_launch(monkeypatch, errno.ENOENT)
    with pytest.raises(SetupFailedError) as caught:
        run_setup(setup("pass", 5.0), tmp_path, {}, tmp_path / "logs", "b")
    assert caught.value.output.exit_code == 127


def test_a_program_errno_naming_another_file_is_the_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raise_at_launch(monkeypatch, errno.ENOENT, "/somewhere/else")
    with pytest.raises(SetupHostError):
        run_setup(setup("pass", 5.0), tmp_path, {}, tmp_path / "logs", "b")


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_an_unusable_working_directory_is_the_trees(
    tmp_path: Path, kind: str
) -> None:
    (tmp_path / "file").write_text("a file, not a directory")
    bad = SetupSpec(
        commands=((PY, "-c", "pass"),),
        timeout_seconds=5.0,
        cwd=("no-such-dir" if kind == "missing" else "file"),
    )
    with pytest.raises(SetupFailedError) as caught:
        run_setup(bad, tmp_path, {}, tmp_path / "logs", "b")
    assert caught.value.output.exit_code == 127


def test_an_unusable_log_directory_is_the_hosts(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    with pytest.raises(SetupHostError):
        run_setup(setup("pass", 5.0), tmp_path, {}, blocker / "logs", "b")


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
        "reason": "CPU only",
    },
    {
        "metric": "p99_latency",
        "direction": "lower_is_better",
        "unit": "s",
        "applicable": False,
        "reason": "no requests",
    },
]


def test_a_host_fault_at_the_candidates_launch_is_a_harness_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    (baseline / "bench.py").write_text("print('PERFPROOF {\"value\": 0.5}')\n")
    candidate = tmp_path / "candidate"
    shutil.copytree(baseline, candidate)
    spec = BenchmarkSpec.model_validate(
        {
            "name": "toy",
            "command": [PY, "bench.py"],
            "metric": {
                "kind": "reported",
                "direction": "lower_is_better",
                "unit": "s",
            },
            "correctness": {
                "command": [PY, "-c", "pass"],
                "timeout_seconds": 60.0,
            },
            "warmup": 1,
            "budget": {"max_rounds": 10, "max_seconds": 60.0},
            "looks": {"n0": 10, "k": 2, "max_looks": 1},
            "timeout_seconds": 30.0,
            "harness": ["bench.py"],
            "objective": "wall_time",
            "guardrails": GUARDRAILS,
            "setup": {
                "commands": [[PY, "-c", "pass"]],
                "timeout_seconds": 30.0,
            },
        }
    )
    real = subprocess.Popen

    def popen(argv: list[str], **kwargs: Any) -> Any:
        if str(kwargs.get("cwd")) == str(candidate):
            raise OSError(errno.EMFILE, os.strerror(errno.EMFILE), argv[0])
        return real(argv, **kwargs)

    monkeypatch.setattr("perfproof.bench.tree_setup.subprocess.Popen", popen)
    with pytest.raises(CompareStoppedError) as caught:
        compare(
            spec,
            build_manifest(spec, baseline),
            baseline_root=baseline,
            baseline_commit="0" * 40,
            candidate_root=candidate,
            run_dir=tmp_path / "run",
            process_env=os.environ,
            order_seed=1,
        )
    assert caught.value.code == "harness_fault"
    assert "neither tree" in caught.value.message
