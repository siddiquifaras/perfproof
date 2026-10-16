"""Build a task here and run its workload and oracle on both trees.

For each task named in ``checks.json`` this clones the repository into
``eval/mine/work/`` (gitignored), checks out the task commit and the
reference change side by side, creates a uv virtualenv in each, runs
the setup commands, then the oracle and the workload, and records
every command with its exit status, wall time and output tail in the
task's manifest. The manifest's status becomes:

``runs``
    Setup, workload and oracle all exit 0 on both trees, except that
    the oracle is only required on the reference tree when it names
    test files the reference change adds.
``builds``
    Setup succeeded on both trees but a workload or oracle failed.
``failed``
    Cloning or setup failed.

Usage: ``uv run python eval/mine/check.py <task-id> [...]``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from task import Command, Run, Task, load_tasks, task_path, write_task

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
CHECKS = HERE / "checks.json"
TIMEOUT_S = 1800.0
TAIL_LINES = 15
ENV_KEEP = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "TMPDIR",
        "SHELL",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
    }
)

Tree = Literal["task", "reference"]
Step = Literal["clone", "setup", "oracle", "workload"]


def _out(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _run(
    step: Step, tree: Tree, command: str, cwd: Path, env: dict[str, str]
) -> Run:
    """Run one shell command and record it.

    Parameters
    ----------
    step : {"clone", "setup", "oracle", "workload"}
        Which part of the task the command checks.
    tree : {"task", "reference"}
        Which checkout it runs in.
    command : str
        The command, split with `shlex.split` and run without a shell;
        executables resolve on the tree's virtualenv ``PATH``.
    cwd : Path
        Working directory.
    env : dict of str to str
        Environment for the command.

    Returns
    -------
    Run
        The record, with exit status 124 on timeout and 127 when the
        executable is not found.
    """
    started = datetime.now(UTC).replace(microsecond=0).isoformat()
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            shlex.split(command),
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
        code, output = proc.returncode, proc.stdout + proc.stderr
    except FileNotFoundError as exc:
        code, output = 127, str(exc)
    except subprocess.TimeoutExpired as exc:
        code = 124
        raw = (exc.stdout or b"") + (exc.stderr or b"")
        output = raw.decode(errors="replace") + "\n[timed out]"
    duration = round(time.monotonic() - t0, 2)
    # Paths are recorded relative to the repository, not this machine.
    output = output.replace(str(WORK), "eval/mine/work")
    tail = "\n".join(output.strip().splitlines()[-TAIL_LINES:])
    _out(f"  [{tree}] {step} exit {code} in {duration}s: {command}")
    return Run(
        step=step,
        tree=tree,
        command=command,
        exit_code=code,
        duration_s=duration,
        date=started,
        machine=platform.platform(),
        output_tail=tail,
    )


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, timeout=TIMEOUT_S)


def _checkout(task: Task, clone: Path, tree: Tree) -> Path:
    commit = (
        task.task_commit if tree == "task" else task.reference_change.commit
    )
    if commit is None:
        msg = f"{task.id}: the reference change has no commit to check out"
        raise ValueError(msg)
    path = WORK / task.id / tree
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    # A plain archive of the commit, not a worktree, so nothing from
    # later history is reachable from the checked-out tree.
    archive = subprocess.run(
        ["git", "archive", commit],
        cwd=clone,
        capture_output=True,
        check=True,
        timeout=TIMEOUT_S,
    )
    subprocess.run(
        ["tar", "-x", "-C", str(path)],
        input=archive.stdout,
        check=True,
        timeout=TIMEOUT_S,
    )
    return path


def _clone(task: Task) -> Path:
    clone = WORK / "clones" / task.repository.replace("/", "__")
    if not clone.exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(
            [
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                f"https://github.com/{task.repository}.git",
                str(clone),
            ],
            WORK,
        )
    for sha in (task.task_commit, task.reference_change.commit):
        if sha:
            _git(["fetch", "--quiet", "origin", sha], clone)
    return clone


def check(task: Task, spec: dict[str, Any]) -> Task:
    """Build and run one task on both trees.

    Parameters
    ----------
    task : Task
        The manifest to check.
    spec : dict
        ``setup`` (list of shell lines), ``workload`` and ``oracle``
        (shell lines), and optional ``env`` (extra environment
        variables, recorded as a prefix on every command line),
        ``oracle_task_tree`` (bool, whether the oracle must also pass at
        the task commit), ``workload_source``, ``oracle_source`` and
        ``notes``.

    Returns
    -------
    Task
        The manifest with runs, commands and status updated.
    """
    _out(f"{task.id}")
    runs: list[Run] = []
    # Only what the build tools need: cloned projects' build backends
    # run with this environment, so tokens and keys stay out of it.
    env = {
        k: v
        for k, v in os.environ.items()
        if k in ENV_KEEP or k.startswith("LC_")
    }
    extra = cast(dict[str, str], spec.get("env", {}))
    env.update(extra)
    prefix = "".join(f"{k}={v} " for k, v in sorted(extra.items()))

    def record(run: Run) -> Run:
        # Extra variables are part of how the command ran, so they go
        # into the recorded command line.
        return run.model_copy(update={"command": prefix + run.command})

    try:
        clone = _clone(task)
        trees = {t: _checkout(task, clone, t) for t in ("task", "reference")}
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
    ) as exc:
        status_reason = f"clone or checkout failed: {exc}"
        return task.model_copy(
            update={"status": "failed", "status_reason": status_reason}
        )
    setup_ok = True
    results: dict[tuple[Tree, Step], int] = {}
    for tree, path in cast(dict[Tree, Path], trees).items():
        tree_env = dict(env, VIRTUAL_ENV=str(path / ".venv"))
        tree_env["PATH"] = f"{path / '.venv' / 'bin'}:{env['PATH']}"
        for line in spec["setup"]:
            run = record(_run("setup", tree, line, path, tree_env))
            runs.append(run)
            if run.exit_code != 0:
                setup_ok = False
                break
        if not setup_ok:
            break
        for step in ("oracle", "workload"):
            run = record(
                _run(cast(Step, step), tree, spec[step], path, tree_env)
            )
            runs.append(run)
            results[(tree, cast(Step, step))] = run.exit_code
    need_task_oracle = bool(spec.get("oracle_task_tree", True))
    ok = setup_ok and all(
        code == 0
        for (tree, step), code in results.items()
        if not (step == "oracle" and tree == "task" and not need_task_oracle)
    )
    if not setup_ok:
        status, reason = "failed", "setup failed; see runs"
    elif ok:
        status = "runs"
        reason = (
            "built here and ran workload and oracle on the task and "
            "reference trees; see runs"
        )
        if not need_task_oracle:
            reason += (
                " (oracle required on the reference tree only: it names "
                "tests the reference change adds)"
            )
    else:
        status = "builds"
        reason = "built here, but a workload or oracle run failed"
    if spec.get("notes"):
        reason += ". " + str(spec["notes"])
    workload = Command(
        command=spec["workload"],
        source=spec.get("workload_source", task.workload.source),
        verified=ok,
    )
    oracle = Command(
        command=spec["oracle"],
        source=spec.get("oracle_source", task.oracle.source),
        verified=ok,
    )
    return task.model_copy(
        update={
            "status": status,
            "status_reason": reason,
            "runs": runs,
            "workload": workload,
            "oracle": oracle,
        }
    )


def main(argv: list[str] | None = None) -> int:
    """Check the named tasks and rewrite their manifests.

    Parameters
    ----------
    argv : list of str, optional
        Task ids; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        0 when every named task reached ``runs``, else 1.
    """
    parser = argparse.ArgumentParser(description="Build and run tasks.")
    parser.add_argument("ids", nargs="+")
    args = parser.parse_args(argv)
    specs = json.loads(CHECKS.read_text())
    tasks = {t.id: t for t in load_tasks()}
    WORK.mkdir(exist_ok=True)
    # pytest walks up from a checkout looking for its config. Without a
    # stop here, a project with no pytest config of its own would pick
    # up perfproof's pyproject.toml (warnings as errors and all).
    (WORK / "pytest.ini").write_text(
        "# Stops pytest's config search; see check.py.\n[pytest]\n"
    )
    all_ok = True
    for task_id in args.ids:
        old = tasks[task_id]
        new = check(old, specs[task_id])
        if task_path(new) != task_path(old):
            task_path(old).unlink()
        write_task(new)
        _out(f"  -> {new.status}: {new.status_reason}")
        all_ok = all_ok and new.status == "runs"
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
