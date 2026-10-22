"""Time, money and process limits of a run, exercised for real."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import realrepo
import runner
import skill_eval
import taskset

REAL = taskset.load(Path(__file__).parent / "tasks-real.json").tasks
TASK = REAL[0]


def _dead(pid: int) -> bool:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.1)
    return False


def _spawner(pidfile: Path, then: str) -> list[str]:
    code = (
        "import subprocess,sys,time\n"
        "p=subprocess.Popen(['sleep','60'])\n"
        f"open({str(pidfile)!r},'w').write(str(p.pid))\n"
        f"{then}\n"
    )
    return [sys.executable, "-c", code]


def test_a_call_past_its_limit_is_killed_with_its_children(
    tmp_path: Path,
) -> None:
    pidfile = tmp_path / "pid"
    started = time.monotonic()
    meta, result = runner.run_turn(
        _spawner(pidfile, "time.sleep(60)"),
        tmp_path,
        dict(os.environ),
        tmp_path / "out.jsonl",
        tmp_path / "err",
        2.0,
    )
    assert time.monotonic() - started < 30
    assert meta.exit_code is None
    assert result is None
    assert _dead(int(pidfile.read_text()))


def test_background_work_does_not_outlive_a_finished_call(
    tmp_path: Path,
) -> None:
    pidfile = tmp_path / "pid"
    meta, _ = runner.run_turn(
        _spawner(pidfile, "sys.exit(0)"),
        tmp_path,
        dict(os.environ),
        tmp_path / "out.jsonl",
        tmp_path / "err",
        60.0,
    )
    assert meta.exit_code == 0
    assert _dead(int(pidfile.read_text()))


def _docker_ready() -> bool:
    try:
        up = subprocess.run(
            ["docker", "image", "inspect", "python:3.12-slim"],
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return up.returncode == 0


@pytest.mark.skipif(not _docker_ready(), reason="no docker with the image")
def test_launcher_containers_are_killed() -> None:
    started = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--label",
            realrepo.CONTAINER_LABEL,
            "python:3.12-slim",
            "sleep",
            "120",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    cid = started.stdout.strip()[:12]
    try:
        realrepo.kill_containers()
        alive = cid
        for _ in range(50):
            alive = subprocess.run(
                ["docker", "ps", "-q", "--filter", f"id={cid}"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            if not alive:
                break
            time.sleep(0.2)
        assert alive == ""
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)


def test_the_task_budget_caps_each_call_and_stops_the_task() -> None:
    task = TASK.model_copy(update={"budget_usd": 2.0, "task_budget_usd": 3.0})
    assert runner.call_budget(task, 0.0) == 2.0
    assert runner.call_budget(task, 2.5) == pytest.approx(0.5)
    assert not runner.budget_spent(task, 2.0)
    assert runner.budget_spent(task, 2.98)
    free = task.model_copy(update={"task_budget_usd": None})
    assert runner.call_budget(free, 99.0) == 2.0
    assert not runner.budget_spent(free, 99.0)


def test_the_call_timeout_is_the_tasks_or_the_default() -> None:
    task = TASK.model_copy(update={"timeout_s": 42.0})
    assert runner.call_timeout(task) == 42.0
    unset = TASK.model_copy(update={"timeout_s": None})
    assert runner.call_timeout(unset) == runner.TURN_TIMEOUT_SECONDS


def test_every_real_task_states_its_limits() -> None:
    for task in REAL:
        assert task.timeout_s is not None
        assert task.task_budget_usd is not None


def test_the_run_waits_while_the_pause_marker_exists(tmp_path: Path) -> None:
    marker = tmp_path / "TRACE_COLLECTING"
    marker.write_text("x", encoding="utf-8")
    sleeps: list[float] = []

    def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 2:
            marker.unlink()

    waited = skill_eval.wait_while_marked(marker, 120.0, sleeper)
    assert waited == 240.0
    assert sleeps == [120.0, 120.0]
    assert skill_eval.wait_while_marked(None) == 0.0
    assert skill_eval.wait_while_marked(marker) == 0.0
