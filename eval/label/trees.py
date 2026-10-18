"""Build a task's trees with the miner's recipe, and run commands there.

The checkout (depth-1 fetch, ``git archive``, submodules, a benchmark
row's patch and script) is ``eval/mine/check.py``'s, so a tree here is
built exactly as the one the task was drawn on. Only the work
directory differs: trees live under ``eval/label/work/<task-id>/``, at
the same depth as ``eval/mine/work/<task-id>/``, so the recipes'
relative paths (``../../../../tasks/workloads/...``) still reach
``eval/tasks/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

HERE = Path(__file__).resolve().parent
MINE = HERE.parent / "mine"
WORK = HERE / "work"
TAIL_LINES = 30

# The miner's modules import each other by bare name, so its directory
# goes on the path the way running check.py puts it there.
sys.path.insert(0, str(MINE))

import check  # noqa: E402
from task import Task, load_tasks  # noqa: E402

Tree = Literal["task", "reference"]


@dataclass(frozen=True)
class CommandRecord:
    """One command run in a tree.

    Attributes
    ----------
    step : str
        ``setup``, ``oracle``, ``suite-setup`` or ``suite``.
    tree : str
        ``task`` or ``reference``.
    command : str
        The command line as written in the recipe, with any extra
        environment variables as a prefix.
    exit_code : int
        Exit status; 124 on timeout, 127 when it could not start.
    duration_s : float
        Wall time, seconds.
    date : str
        UTC start time, ISO 8601.
    output_tail : str
        The last lines of its output, paths relative to the repository
        and the home directory.
    """

    step: str
    tree: str
    command: str
    exit_code: int
    duration_s: float
    date: str
    output_tail: str

    def to_json(self) -> dict[str, Any]:
        """Return the record as JSON-ready data.

        Returns
        -------
        dict
            Field names to values.
        """
        return asdict(self)


def scrub(text: str) -> str:
    """Rewrite this checkout's work directory and the home directory.

    Parameters
    ----------
    text : str
        Captured output.

    Returns
    -------
    str
        The text with ``eval/label/work`` and ``~`` in their place.
    """
    text = text.replace(str(WORK), "eval/label/work")
    return text.replace(str(Path.home()), "~")


def task_by_id(task_id: str) -> Task:
    """Return a task's manifest.

    Parameters
    ----------
    task_id : str
        The task id.

    Returns
    -------
    Task
        The manifest.

    Raises
    ------
    KeyError
        If no manifest has that id.
    """
    for task in load_tasks():
        if task.id == task_id:
            return task
    raise KeyError(task_id)


def spec_for(task_id: str) -> dict[str, Any]:
    """Return a task's build recipe from ``eval/mine/checks.json``.

    Parameters
    ----------
    task_id : str
        The task id.

    Returns
    -------
    dict
        The recipe (``setup``, ``workload``, ``oracle``, optional
        ``env`` and ``teardown``).

    Raises
    ------
    KeyError
        If the task has no recipe.
    """
    specs = cast(
        dict[str, dict[str, Any]], json.loads(check.CHECKS.read_text())
    )
    return specs[task_id]


def digest(data: object) -> str:
    """Return the SHA-256 of a JSON-serialisable value.

    Parameters
    ----------
    data : object
        Any JSON-serialisable value.

    Returns
    -------
    str
        Hex digest of its canonical JSON.
    """
    raw = json.dumps(data, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def tree_env(path: Path, spec: dict[str, Any]) -> dict[str, str]:
    """Return the environment commands in a tree run with.

    The same as ``check.py`` builds: only what build tools need (no
    tokens or keys), the recipe's extra variables, and the tree's
    virtualenv first on ``PATH``.

    Parameters
    ----------
    path : Path
        The tree.
    spec : dict
        Its recipe.

    Returns
    -------
    dict of str to str
        The environment.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k in check.ENV_KEEP or k.startswith("LC_")
    }
    env.update(cast(dict[str, str], spec.get("env", {})))
    env["VIRTUAL_ENV"] = str(path / ".venv")
    env["PATH"] = f"{path / '.venv' / 'bin'}:{env.get('PATH', '')}"
    return env


def env_prefix(spec: dict[str, Any]) -> str:
    """Return the recipe's extra variables as a command-line prefix.

    Parameters
    ----------
    spec : dict
        A recipe.

    Returns
    -------
    str
        ``K=V `` for each extra variable, sorted, or empty.
    """
    extra = cast(dict[str, str], spec.get("env", {}))
    return "".join(f"{k}={v} " for k, v in sorted(extra.items()))


def _text(data: str | bytes | None) -> str:
    if data is None:
        return ""
    return data if isinstance(data, str) else data.decode(errors="replace")


def run_logged(
    step: str,
    tree: str,
    command: str,
    cwd: Path,
    env: dict[str, str],
    timeout_s: float,
    prefix: str = "",
    log: Path | None = None,
) -> CommandRecord:
    """Run one recipe line without a shell and record it.

    Parameters
    ----------
    step, tree : str
        What the command is for, and which tree it runs in.
    command : str
        The line, split with `shlex.split`.
    cwd : Path
        Working directory.
    env : dict of str to str
        Environment.
    timeout_s : float
        Timeout, seconds. On timeout a named ``docker run`` has its
        container killed too.
    prefix : str
        Recorded in front of the command (extra variables).
    log : Path, optional
        Where to write the full output; it is kept in memory only up
        to the tail otherwise.

    Returns
    -------
    CommandRecord
        The record.
    """
    started = datetime.now(UTC).replace(microsecond=0).isoformat()
    argv = shlex.split(command)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        code, output = proc.returncode, proc.stdout + proc.stderr
    except FileNotFoundError as exc:
        code, output = 127, str(exc)
    except subprocess.TimeoutExpired as exc:
        code = 124
        output = _text(exc.stdout) + _text(exc.stderr) + "\n[timed out]"
        name = check.container_name(argv)
        if name is not None:
            check._kill_container(name)  # pyright: ignore[reportPrivateUsage]
    duration = round(time.monotonic() - t0, 2)
    output = scrub(output)
    if log is not None:
        log.write_text(output)
    tail = "\n".join(output.strip().splitlines()[-TAIL_LINES:])
    sys.stdout.write(f"  [{tree}] {step} exit {code} in {duration}s\n")
    sys.stdout.flush()
    return CommandRecord(
        step, tree, prefix + command, code, duration, started, tail
    )


class SetupError(RuntimeError):
    """A tree's setup line exited non-zero."""

    def __init__(self, records: list[CommandRecord]) -> None:
        """Keep the setup records, the last of which failed.

        Parameters
        ----------
        records : list of CommandRecord
            Every setup line run, in order.
        """
        super().__init__(f"setup failed: {records[-1].command}")
        self.records = records


def _commit(task: Task, tree: Tree) -> str | None:
    return task.task_commit if tree == "task" else task.reference_change.commit


def prepare(
    task: Task, spec: dict[str, Any], trees: list[Tree]
) -> tuple[dict[Tree, Path], list[CommandRecord], list[str]]:
    """Check out and set up the trees a run needs, reusing ready ones.

    A tree whose setup finished before, from the same recipe and
    commit, is reused, unless the recipe has ``teardown`` lines: those
    start services (databases) that a resumed run needs again, so the
    tree is rebuilt.

    Parameters
    ----------
    task : Task
        The manifest.
    spec : dict
        Its recipe.
    trees : list of {"task", "reference"}
        The trees to prepare.

    Returns
    -------
    paths : dict
        Each tree's directory.
    records : list of CommandRecord
        The setup lines run now (none for a reused tree).
    notes : list of str
        Which trees were reused.

    Raises
    ------
    SetupError
        If a setup line exits non-zero.
    check.NetworkError
        If a fetch still fails after the miner's retries.
    """
    # The miner's helpers read their work directory from this global.
    check.WORK = WORK
    WORK.mkdir(exist_ok=True)
    (WORK / "pytest.ini").write_text(
        "# Stops pytest's config search; see eval/mine/check.py.\n[pytest]\n"
    )
    row = check.benchmark_row(task)
    prefix = env_prefix(spec)
    paths: dict[Tree, Path] = {}
    records: list[CommandRecord] = []
    notes: list[str] = []
    clone: Path | None = None
    for tree in trees:
        path = WORK / task.id / tree
        marker = WORK / task.id / f"{tree}.ready.json"
        want = {"recipe": digest(spec), "commit": _commit(task, tree)}
        if (
            not spec.get("teardown")
            and marker.exists()
            and json.loads(marker.read_text()) == want
            and path.exists()
        ):
            paths[tree] = path
            notes.append(f"{tree} tree reused from an earlier setup")
            continue
        marker.unlink(missing_ok=True)
        if clone is None:
            clone = check._clone(task)  # pyright: ignore[reportPrivateUsage]
        path = check._checkout(task, clone, tree, row)  # pyright: ignore[reportPrivateUsage]
        env = tree_env(path, spec)
        for line in cast(list[str], spec["setup"]):
            rec = run_logged(
                "setup", tree, line, path, env, check.TIMEOUT_S, prefix
            )
            records.append(rec)
            if rec.exit_code != 0:
                raise SetupError(records)
        marker.write_text(json.dumps(want))
        paths[tree] = path
    return paths, records, notes


def teardown(spec: dict[str, Any]) -> None:
    """Run a recipe's teardown lines (removing service containers).

    Parameters
    ----------
    spec : dict
        The recipe.
    """
    check._teardown(spec)  # pyright: ignore[reportPrivateUsage]
