"""Build a task here and run its workload and oracle on both trees.

For each task named in ``checks.json`` this fetches the task commit
and the reference commit, at depth 1, into a repository under
``eval/mine/work/`` (gitignored), extracts both side by side, applies
a benchmark row's patch where the reference is a patch, runs the
setup commands, then the oracle and the workload, and records
every command with its exit status, wall time and output tail in the
task's manifest. The manifest's status becomes (see `classify`):

``runs``
    Setup, workload and oracle all exit 0 on both trees, except that
    the oracle is only required on the reference tree when it names
    test files the reference change adds.
``reference-fails``
    The task tree runs, but the reference tree's setup, oracle or
    workload exits non-zero (a timeout included). Whether a change
    breaks behaviour, or its own build, is what labelling measures, so
    this is recorded as data and the task can be drawn.
``builds``
    The task tree's setup succeeded, but its workload or oracle timed
    out, could not start or failed.
``failed``
    Checkout, or the task tree's setup, failed.

Buildability is judged on the task tree alone.

A clone or fetch that still fails after its retries is a network
failure, not a property of the task, so the manifest is left as it was
and the task is reported as not checked. For a held-out task the
reference tree's workload is recorded by exit status only, without
wall time or output. A spec's ``teardown`` lines (removing database
containers) run after every check and are not recorded.

Usage: ``uv run python eval/mine/check.py <task-id> [...]``, or
``--restatus <task-id> [...]`` to recompute statuses from the runs
already recorded, without running anything.
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

from benchmarks import BENCHMARKS, iter_rows, workload_script
from task import (
    Command,
    Run,
    Status,
    Task,
    load_tasks,
    task_path,
    write_task,
)

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"
CHECKS = HERE / "checks.json"
TIMEOUT_S = 1800.0
TAIL_LINES = 15
NETWORK_ATTEMPTS = 3
NETWORK_PAUSE_S = 30.0
MAX_SUBMODULE_DEPTH = 3
DOCKER_KILL_TIMEOUT_S = 60.0
# Exit statuses meaning the command never got to run its check: 124 is
# this script's timeout, 125 a docker daemon error, 126 and 127 a
# command that could not be executed or found.
UNRUNNABLE = frozenset({124, 125, 126, 127})
HIDDEN = "[not recorded: the reference workload of a held-out task]"
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


def container_name(argv: list[str]) -> str | None:
    """Return the ``--name`` of a ``docker run`` command line.

    A timeout kills the container by this name: killing only the docker
    CLI leaves the container running.

    Parameters
    ----------
    argv : list of str
        A command line, already split.

    Returns
    -------
    str or None
        The name, or None for any other command or an unnamed run.
    """
    if argv[:2] != ["docker", "run"] or "--name" not in argv:
        return None
    i = argv.index("--name")
    return argv[i + 1] if i + 1 < len(argv) else None


def _kill_container(name: str) -> None:
    for argv in (["docker", "kill", name], ["docker", "rm", "-f", name]):
        subprocess.run(
            argv,
            capture_output=True,
            check=False,
            timeout=DOCKER_KILL_TIMEOUT_S,
        )


def _scrub(output: str) -> str:
    # Paths are recorded relative to the repository and the home
    # directory, not this machine.
    output = output.replace(str(WORK), "eval/mine/work")
    return output.replace(str(Path.home()), "~")


def _run(
    step: Step,
    tree: Tree,
    command: str,
    cwd: Path,
    env: dict[str, str],
    *,
    hide_result: bool = False,
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
    hide_result : bool
        Record neither wall time nor output, only the exit status: for
        the reference tree's workload of a held-out task, whose effect
        size must not be seen before it is labelled.

    Returns
    -------
    Run
        The record, with exit status 124 on timeout and 127 when the
        executable is not found. On timeout a ``docker run`` with a
        ``--name`` has its container killed.
    """
    started = datetime.now(UTC).replace(microsecond=0).isoformat()
    t0 = time.monotonic()
    argv = shlex.split(command)
    try:
        proc = subprocess.run(
            argv,
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
        name = container_name(argv)
        if name is not None:
            _kill_container(name)
    duration: float | None = round(time.monotonic() - t0, 2)
    tail = "\n".join(_scrub(output).strip().splitlines()[-TAIL_LINES:])
    if hide_result:
        duration, tail = None, HIDDEN
    took = "" if duration is None else f" in {duration}s"
    _out(f"  [{tree}] {step} exit {code}{took}: {command}")
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


def classify(runs: list[Run], need_task_oracle: bool) -> tuple[Status, str]:
    """Derive a task's status from its recorded runs.

    Parameters
    ----------
    runs : list of Run
        Every command run for the task, in order.
    need_task_oracle : bool
        Whether the oracle must pass on the task tree too (False when
        it names tests the reference change adds).

    Returns
    -------
    status : {"runs", "reference-fails", "builds", "failed"}
        Buildability is judged on the task tree alone; whatever happens
        only on the reference tree is a result of the change.
        ``failed`` when a task-tree setup line failed. ``builds`` when
        the task tree can't be run here: its workload, or its oracle
        where required, timed out, could not start (exit 124 to 127)
        or failed. ``reference-fails`` when the task tree runs but the
        reference tree's setup (often the build itself), oracle or
        workload exits non-zero, timeouts included: an outcome of the
        reference change, kept for labelling. ``runs`` when everything
        exits 0.
    reason : str
        What was found, for ``status_reason``.
    """
    if any(
        r.tree == "task" and r.step == "setup" and r.exit_code != 0
        for r in runs
    ):
        return "failed", "setup failed on the task tree; see runs"
    codes = {(r.tree, r.step): r.exit_code for r in runs if r.step != "setup"}
    task_steps = ["workload", "oracle"] if need_task_oracle else ["workload"]
    task_codes = [codes.get(("task", s), 0) for s in task_steps]
    if any(code in UNRUNNABLE for code in task_codes):
        return "builds", (
            "built here, but the task tree's workload or oracle timed out "
            "or could not start"
        )
    if any(code != 0 for code in task_codes):
        return "builds", (
            "built here, but the task tree's workload or oracle failed"
        )
    failing = [
        f"setup exit {r.exit_code}"
        for r in runs
        if r.tree == "reference" and r.step == "setup" and r.exit_code != 0
    ]
    failing += [
        f"{s} exit {codes[('reference', s)]}"
        for s in ("oracle", "workload")
        if codes.get(("reference", s), 0) != 0
    ]
    reference_built = not any(f.startswith("setup") for f in failing)
    reason = (
        "built here and ran workload and oracle on the task and "
        "reference trees; see runs"
        if reference_built
        else "built here and ran workload and oracle on the task tree; "
        "see runs"
    )
    if not need_task_oracle:
        reason += (
            " (oracle required on the reference tree only: it names tests "
            "the reference change adds)"
        )
    if failing:
        return "reference-fails", (
            reason
            + ". On the reference tree only: "
            + ", ".join(failing)
            + ". That is an outcome of the reference change, recorded "
            "for labelling, not a build failure"
        )
    return "runs", reason


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, timeout=TIMEOUT_S)


class NetworkError(RuntimeError):
    """A clone or fetch failed after every retry."""


def _git_network(args: list[str], cwd: Path) -> None:
    for attempt in range(NETWORK_ATTEMPTS):
        try:
            _git(args, cwd)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == NETWORK_ATTEMPTS - 1:
                msg = f"git {' '.join(args[:2])} failed"
                raise NetworkError(msg) from None
            time.sleep(NETWORK_PAUSE_S)
        else:
            return


def benchmark_row(task: Task) -> tuple[str, dict[str, Any]] | None:
    """Find a published-benchmark task's dataset row in the cache.

    Parameters
    ----------
    task : Task
        Any task.

    Returns
    -------
    tuple of (str, dict) or None
        The benchmark's short name and the row, or None when the task
        is not a benchmark instance.

    Raises
    ------
    ValueError
        If the task is a benchmark instance but no row has its id.
    """
    if task.family != "published-benchmark":
        return None
    for bench in BENCHMARKS:
        if not task.id.startswith(bench.name + "-"):
            continue
        for _, row in iter_rows(bench):
            instance = str(row["instance_id"]).lower().replace("__", "-")
            if f"{bench.name}-{instance}" == task.id:
                return bench.name, row
    msg = f"{task.id}: no benchmark row has this id"
    raise ValueError(msg)


def _archive(clone: Path, commit: str) -> bytes:
    # In a partial clone (as earlier versions of this script made)
    # `git archive` fetches missing blobs on demand, so it can fail for
    # the same network reasons as a fetch.
    stderr = ""
    for attempt in range(NETWORK_ATTEMPTS):
        if attempt:
            time.sleep(NETWORK_PAUSE_S)
        try:
            proc = subprocess.run(
                ["git", "archive", commit],
                cwd=clone,
                capture_output=True,
                check=False,
                timeout=TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            stderr = "timed out"
            continue
        if proc.returncode == 0:
            return proc.stdout
        stderr = proc.stderr.decode(errors="replace").strip()[-300:]
    msg = f"git archive {commit} failed: {stderr}"
    raise NetworkError(msg)


def _checkout(
    task: Task,
    clone: Path,
    tree: Tree,
    row: tuple[str, dict[str, Any]] | None,
) -> Path:
    commit = (
        task.task_commit if tree == "task" else task.reference_change.commit
    )
    patch: str | None = None
    if commit is None and row is not None and row[1].get("patch"):
        # SWE-Perf and SWE-fficiency give the reference as the base
        # commit plus the row's patch, not as a commit.
        commit, patch = task.task_commit, str(row[1]["patch"])
    if commit is None:
        msg = f"{task.id}: the reference change has no commit to check out"
        raise ValueError(msg)
    path = WORK / task.id / tree
    if path.exists():
        shutil.rmtree(path)
    _extract(clone, commit, path, _github_url(task.repository), 0)
    if patch is not None:
        subprocess.run(
            ["git", "apply", "-"],
            cwd=path,
            input=patch.encode(),
            check=True,
            timeout=TIMEOUT_S,
        )
    if row is not None:
        script = workload_script(*row)
        if script is not None:
            # The benchmark's own script, run as `python workload.py`.
            (path / "workload.py").write_text(script)
    return path


def _github_url(repository: str) -> str:
    return f"https://github.com/{repository}.git"


def _fetch(url: str, shas: list[str]) -> Path:
    name = url.removeprefix("https://").removesuffix(".git")
    if name.startswith("github.com/"):
        name = name.removeprefix("github.com/")
    clone = WORK / "clones" / name.replace("/", "__")
    if not clone.exists():
        # An empty repository that fetches only the commits it needs, at
        # depth 1: a full clone, even without blobs, downloads every
        # commit and tree in the history.
        clone.mkdir(parents=True)
        _git(["init", "--quiet"], clone)
        _git(["remote", "add", "origin", url], clone)
    for sha in shas:
        _git_network(
            ["fetch", "--quiet", "--depth", "1", "origin", sha], clone
        )
    return clone


def _clone(task: Task) -> Path:
    shas = [task.task_commit, task.reference_change.commit]
    return _fetch(_github_url(task.repository), [s for s in shas if s])


def _extract(
    clone: Path, commit: str, path: Path, url: str, depth: int
) -> None:
    # A plain archive of the commit, not a worktree, so nothing from
    # later history is reachable from the checked-out tree. An archive
    # leaves submodules out, so each is fetched at the commit its
    # gitlink names and extracted in place.
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["tar", "-x", "-C", str(path)],
        input=_archive(clone, commit),
        check=True,
        timeout=TIMEOUT_S,
    )
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "-z", commit],
        cwd=clone,
        capture_output=True,
        text=True,
        check=True,
        timeout=TIMEOUT_S,
    ).stdout
    links = [
        (meta.split()[2], sub)
        for meta, sub in (e.split("\t", 1) for e in listing.split("\0") if e)
        if meta.startswith("160000 ")
    ]
    if not links:
        return
    if depth >= MAX_SUBMODULE_DEPTH:
        msg = f"submodules nested deeper than {MAX_SUBMODULE_DEPTH} in {url}"
        raise ValueError(msg)
    # Read from the commit, not the extracted tree: a project can mark
    # .gitmodules export-ignore, so the archive leaves it out.
    config = subprocess.run(
        [
            "git",
            "config",
            "--blob",
            f"{commit}:.gitmodules",
            "--get-regexp",
            r"^submodule\..*\.(path|url)$",
        ],
        cwd=clone,
        capture_output=True,
        text=True,
        check=True,
        timeout=TIMEOUT_S,
    ).stdout
    entries = [line.split(" ", 1) for line in config.splitlines()]
    paths = {k.rsplit(".", 1)[0]: v for k, v in entries if k.endswith(".path")}
    urls = {k.rsplit(".", 1)[0]: v for k, v in entries if k.endswith(".url")}
    by_path = {p: urls[name] for name, p in paths.items()}
    for sha, sub in links:
        sub_url = by_path.get(sub)
        if sub_url is None:
            msg = f"{url}: no .gitmodules entry for the gitlink {sub}"
            raise ValueError(msg)
        if sub_url.startswith("../"):
            # Relative to the superproject's own URL.
            sub_url = url.rsplit("/", 1)[0] + "/" + sub_url.removeprefix("../")
        sub_clone = _fetch(sub_url, [sha])
        _extract(sub_clone, sha, path / sub, sub_url, depth + 1)


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

    Raises
    ------
    NetworkError
        If cloning, fetching or archiving a commit still fails after
        its retries.
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
        row = benchmark_row(task)
        clone = _clone(task)
        trees = {
            t: _checkout(task, clone, t, row) for t in ("task", "reference")
        }
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        ValueError,
    ) as exc:
        status_reason = f"clone or checkout failed: {exc}"
        return task.model_copy(
            update={"status": "failed", "status_reason": status_reason}
        )
    held_out = task.split == "held-out"
    try:
        for tree, path in cast(dict[Tree, Path], trees).items():
            tree_env = dict(env, VIRTUAL_ENV=str(path / ".venv"))
            tree_env["PATH"] = f"{path / '.venv' / 'bin'}:{env['PATH']}"
            setup_ok = True
            for line in spec["setup"]:
                run = record(_run("setup", tree, line, path, tree_env))
                runs.append(run)
                if run.exit_code != 0:
                    setup_ok = False
                    break
            if not setup_ok:
                break
            for step in ("oracle", "workload"):
                hide = held_out and tree == "reference" and step == "workload"
                run = _run(
                    cast(Step, step),
                    tree,
                    spec[step],
                    path,
                    tree_env,
                    hide_result=hide,
                )
                runs.append(record(run))
    finally:
        _teardown(spec)
    return with_status(task, spec, runs)


def _teardown(spec: dict[str, Any]) -> None:
    # Containers a task's setup started (databases) are removed after
    # every check, whatever happened, and are not part of the record.
    for line in cast(list[str], spec.get("teardown", [])):
        try:
            proc = subprocess.run(
                shlex.split(line),
                capture_output=True,
                check=False,
                timeout=DOCKER_KILL_TIMEOUT_S,
            )
            _out(f"  teardown exit {proc.returncode}: {line}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            _out(f"  teardown failed: {line}: {exc}")


def with_status(task: Task, spec: dict[str, Any], runs: list[Run]) -> Task:
    """Return the manifest with runs, commands and the status they give.

    Parameters
    ----------
    task : Task
        The manifest.
    spec : dict
        The task's entry in ``checks.json`` (see `check`).
    runs : list of Run
        The runs to record.

    Returns
    -------
    Task
        The manifest with ``runs``, ``status``, ``status_reason`` and
        the workload and oracle commands set from `classify` and the
        spec.
    """
    need_task_oracle = bool(spec.get("oracle_task_tree", True))
    status, reason = classify(runs, need_task_oracle)
    if spec.get("notes"):
        reason += ". " + str(spec["notes"])
    ran = status in ("runs", "reference-fails")
    workload = Command(
        command=spec["workload"],
        source=spec.get("workload_source", task.workload.source),
        verified=ran,
    )
    oracle = Command(
        command=spec["oracle"],
        source=spec.get("oracle_source", task.oracle.source),
        verified=ran,
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
        0 when every named task reached ``runs`` or ``reference-fails``,
        else 1.
    """
    parser = argparse.ArgumentParser(description="Build and run tasks.")
    parser.add_argument("ids", nargs="+")
    parser.add_argument("--restatus", action="store_true")
    args = parser.parse_args(argv)
    specs = json.loads(CHECKS.read_text())
    tasks = {t.id: t for t in load_tasks()}
    if args.restatus:
        for task_id in args.ids:
            new = restatus(tasks[task_id], specs[task_id])
            write_task(new)
            _out(f"{task_id} -> {new.status}")
        return 0
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
        try:
            new = check(old, specs[task_id])
        except NetworkError as exc:
            _out(f"  -> not checked, manifest unchanged: {exc}")
            all_ok = False
            continue
        if task_path(new) != task_path(old):
            task_path(old).unlink()
        write_task(new)
        _out(f"  -> {new.status}: {new.status_reason}")
        all_ok = all_ok and new.status in ("runs", "reference-fails")
    return 0 if all_ok else 1


def restatus(task: Task, spec: dict[str, Any]) -> Task:
    """Recompute a checked task's status from the runs it records.

    Parameters
    ----------
    task : Task
        A manifest with runs.
    spec : dict
        Its entry in ``checks.json``, for ``oracle_task_tree`` and
        ``notes``.

    Returns
    -------
    Task
        The manifest with ``status``, ``status_reason`` and the
        commands' ``verified`` flags from `classify`. Runs and commands
        are left as recorded.
    """
    status, reason = classify(
        task.runs, bool(spec.get("oracle_task_tree", True))
    )
    if spec.get("notes"):
        reason += ". " + str(spec["notes"])
    ran = status in ("runs", "reference-fails")
    return task.model_copy(
        update={
            "status": status,
            "status_reason": reason,
            "workload": task.workload.model_copy(update={"verified": ran}),
            "oracle": task.oracle.model_copy(update={"verified": ran}),
        }
    )


if __name__ == "__main__":
    raise SystemExit(main())
