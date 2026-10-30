# pyright: reportPrivateUsage=false
"""Measuring a finished run's final tree again, with no agent."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

import arms
import runner
import skill_eval
import speedcheck
import taskset
from runner import State
from test_arms import CONFINED, HOTSPOT_TASK, _dry_run, _git, _write_run


@CONFINED
def test_baseline_starts_claude_from_a_directory_that_holds_perfproof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The eval user's ~/.local/bin holds claude and the perfproof
    # launcher; the baseline's PATH drops that directory, and
    # sandbox-exec then cannot find "claude" (exit 71, no output).
    local = tmp_path / "local-bin"
    local.mkdir()
    for name in ("claude", "perfproof"):
        (local / name).write_text("#!/bin/sh\nexit 0\n")
        (local / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{local}:{os.defpath}")
    recorder, task_dir, _ = _dry_run(
        tmp_path, monkeypatch, "baseline", "vague"
    )
    argv, kwargs = next(c for c in recorder.calls if "--model" in c[0])
    assert str(local / "claude") in argv
    assert "claude" not in argv
    assert shutil.which("claude", path=kwargs["env"]["PATH"]) is None
    assert shutil.which("perfproof", path=kwargs["env"]["PATH"]) is None
    meta = json.loads((task_dir / "meta.json").read_text())
    # 71 is sandbox-exec failing to find the command; the profile may
    # refuse to run this stub (126), which is past that point.
    assert meta["turns"][0]["exit_code"] != 71


def _failed_run(
    tmp_path: Path,
) -> tuple[taskset.Task, Path, Path, Path, str]:
    """A run whose first speed check failed, as it left the repository.

    Returns the task, run dir, task dir, repo and base sha: base
    committed, the agent's change in the working tree, ``.perfproof``
    ignored.
    """
    task = HOTSPOT_TASK.model_copy(
        update={
            "speed_check": taskset.SpeedCheck(
                command=f"{sys.executable} work.py"
            )
        }
    )
    run_dir = tmp_path / "runs" / "r1"
    unknown = speedcheck.SpeedResult(
        status="unknown",
        reason="harness error: git add -A -- . :!.perfproof failed",
    )
    task_dir = _write_run(
        run_dir, task, "skill", "vague", [], "", speed=unknown
    )
    repo = tmp_path / "work" / "r1" / "repos" / task.id
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".perfproof/\n")
    (repo / "work.py").write_text("import time\ntime.sleep(0.05)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "work.py").write_text("import time\ntime.sleep(0.01)\n")
    (repo / ".perfproof").mkdir()
    (repo / ".perfproof" / "x").write_text("state")
    state = State.model_validate_json((task_dir / "state.json").read_text())
    (task_dir / "state.json").write_text(
        state.model_copy(
            update={"seeded": {"main": base}, "final_ref": "working"}
        ).model_dump_json()
    )
    return task, run_dir, task_dir, repo, base


def _remeasure(
    tmp_path: Path, task: taskset.Task, run_dir: Path
) -> speedcheck.SpeedResult | None:
    return runner.remeasure_speed(
        task, run_dir, runner.shim_dir(tmp_path / "shims"), tmp_path / "work"
    )


@CONFINED
def test_remeasure_replaces_an_unknown_speed_check(tmp_path: Path) -> None:
    task, run_dir, task_dir, repo, _ = _failed_run(tmp_path)
    assert skill_eval.needs_remeasure(task_dir)
    speed = _remeasure(tmp_path, task, run_dir)
    assert speed is not None
    assert speed.status == "measured", speed.reason
    assert speed.final_ref == "working"
    assert speed.ratio is not None
    assert speed.ratio > 1.0
    assert arms.load_speed(task_dir) == speed
    assert not skill_eval.needs_remeasure(task_dir)
    tree = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").split()
    assert not [f for f in tree if f.startswith(".perfproof")]


def test_remeasure_names_a_task_repository_that_is_gone(
    tmp_path: Path,
) -> None:
    task = HOTSPOT_TASK.model_copy(
        update={"speed_check": taskset.SpeedCheck(command="true")}
    )
    run_dir = tmp_path / "r2"
    run_dir.mkdir()
    with pytest.raises(FileNotFoundError, match="no task repository"):
        runner.remeasure_speed(task, run_dir, tmp_path, tmp_path / "work")


@CONFINED
def test_remeasure_after_a_check_left_the_repo_on_the_base_tree(
    tmp_path: Path,
) -> None:
    # The first check committed the final tree, then failed on the base
    # tree and left the repository there: a fresh snapshot would be the
    # base ("unchanged"). The committed final tree must be timed.
    task, run_dir, _, repo, base = _failed_run(tmp_path)
    speedcheck.snapshot_working_tree(repo, lambda *a: _git(repo, *a))
    final = _git(repo, "rev-parse", "HEAD").strip()
    _git(repo, "checkout", "-q", "--detach", "-f", base)
    speed = _remeasure(tmp_path, task, run_dir)
    assert speed is not None
    assert speed.status == "measured", speed.reason
    assert speed.ratio is not None
    assert speed.ratio > 1.0
    assert _git(repo, "rev-parse", f"{final}^").strip() == base


def test_remeasure_says_unknown_when_the_final_tree_is_lost(
    tmp_path: Path,
) -> None:
    task, run_dir, _, repo, base = _failed_run(tmp_path)
    _git(repo, "checkout", "-q", "--detach", "-f", base)
    speed = _remeasure(tmp_path, task, run_dir)
    assert speed is not None
    assert speed.status == "unknown"
    assert "not identifiable" in speed.reason
