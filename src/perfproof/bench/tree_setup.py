"""Per-tree setup and the hint for interpreters in ignored directories.

A spec's ``setup`` prepares each tree before warmup, outside the timed
region: ``uv sync --frozen`` gives a tree its own virtual environment
and its own editable install, so each tree imports its own source. The
commands run in order with the benchmark's environment. Output goes to
files in the run directory; a failure keeps the tails.

`ignored_program_hint` explains a command whose program sits in a
git-ignored directory such as ``.venv/``, which a fresh checkout of the
baseline does not have.
"""

import contextlib
import os
import signal
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Final

from perfproof.bench.side_effects import OUTPUT_TAIL_BYTES, points_at_tree
from perfproof.bench.snapshot import GIT_TIMEOUT_SECONDS
from perfproof.schema import CommandOutput, SetupSpec

__all__ = [
    "SetupFailedError",
    "SetupHostError",
    "ignored_program_hint",
    "run_setup",
]

_NOT_STARTED: Final = 127


class SetupFailedError(Exception):
    """A setup command did not succeed.

    Attributes
    ----------
    reason : str
        What happened: exit status, signal, timeout, or why it could not
        start.
    output : CommandOutput
        The argv, exit status (127 when it never started) and the tails
        of its output.
    logs : Path
        Directory of the full logs.
    """

    def __init__(self, reason: str, output: CommandOutput, logs: Path) -> None:
        super().__init__(reason)
        self.reason = reason
        self.output = output
        self.logs = logs


class SetupHostError(Exception):
    """A fault of the host or perfproof's own process, in neither tree.

    Raised for a failed log file, or for a launch error that does not
    point at the program or working directory (EMFILE, EAGAIN, ENOMEM,
    E2BIG).
    """


def _tail(handle: IO[bytes]) -> tuple[str, bool]:
    size = handle.seek(0, os.SEEK_END)
    handle.seek(max(0, size - OUTPUT_TAIL_BYTES))
    data = handle.read()
    return data.decode("utf-8", errors="replace"), size > OUTPUT_TAIL_BYTES


def run_setup(
    setup: SetupSpec,
    tree_root: Path,
    env: Mapping[str, str],
    logs: Path,
    arm: str,
) -> None:
    """Run a spec's setup commands in one tree, in order.

    Parameters
    ----------
    setup : SetupSpec
        The commands, working directory and per-command time limit.
    tree_root : Path
        Root of the tree to prepare.
    env : Mapping of str to str
        The benchmark's environment,
        `perfproof.bench.side_effects.benchmark_environment`.
    logs : Path
        Directory for ``<arm>-<index>.out`` and ``.err``; created.
    arm : str
        Names the log files, such as ``baseline`` or ``recheck``.

    Raises
    ------
    SetupHostError
        If perfproof's own process or the host faults while preparing
        logs or starting a command (not the program or working
        directory): blamed on neither tree.
    SetupFailedError
        On the first command that cannot be started, exits non-zero, is
        killed by a signal or runs past the time limit (its whole
        session is then killed). Later commands do not run.
    """
    try:
        _run_all(setup, tree_root, env, logs, arm)
    except OSError as exc:
        msg = f"a fault in perfproof's own process or the host: {exc}"
        raise SetupHostError(msg) from exc


def _run_all(
    setup: SetupSpec,
    tree_root: Path,
    env: Mapping[str, str],
    logs: Path,
    arm: str,
) -> None:
    """Run every command; an OSError here is a host fault."""
    logs.mkdir(parents=True, exist_ok=True)
    for index, argv in enumerate(setup.commands):
        stem = logs / f"{arm}-{index}"
        with (
            stem.with_suffix(".out").open("w+b") as out,
            stem.with_suffix(".err").open("w+b") as err,
        ):
            code, reason, timed_out = _run_one(
                list(argv), tree_root / setup.cwd, env, out, err, setup
            )
            if code == 0:
                continue
            stdout, cut_out = _tail(out)
            stderr, cut_err = _tail(err)
        output = CommandOutput(
            command=argv,
            exit_code=code,
            timed_out=timed_out,
            stdout=stdout,
            stderr=stderr,
            truncated=cut_out or cut_err,
        )
        raise SetupFailedError(
            f"setup command {' '.join(argv)!r} {reason}", output, logs
        )


def _run_one(
    argv: list[str],
    cwd: Path,
    env: Mapping[str, str],
    out: IO[bytes],
    err: IO[bytes],
    setup: SetupSpec,
) -> tuple[int, str, bool]:
    """Run one command; return its status, a reason and a timeout flag.

    The command's whole session is killed when it ends, however it
    ends, so a background child it left behind never outlives setup.
    """
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
    except OSError as exc:
        if not points_at_tree(exc, argv[0], str(cwd)):
            raise
        err.write(str(exc).encode("utf-8", errors="replace"))
        reason = f"could not be started: {exc.strerror or exc}"
        return _NOT_STARTED, reason, False
    try:
        code = process.wait(timeout=setup.timeout_seconds)
        timed_out = False
    except subprocess.TimeoutExpired:
        code = -signal.SIGKILL
        timed_out = True
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    if timed_out:
        reason = f"timed out after {setup.timeout_seconds} s and was killed"
    elif code < 0:
        reason = f"was killed by signal {-code}"
    else:
        reason = f"exited {code}"
    return code, reason, timed_out


def ignored_program_hint(
    tree_root: Path, cwd: str, program: str, *, has_setup: bool
) -> str | None:
    """Explain a missing program that lives in a git-ignored directory.

    Parameters
    ----------
    tree_root : Path
        Root of the tree the program is missing from.
    cwd : str
        The command's working directory, relative to the tree.
    program : str
        First element of the argv. Only a path with a slash is
        tree-relative; a bare name is looked up on ``PATH``.
    has_setup : bool
        Whether the spec already has a ``setup``.

    Returns
    -------
    str or None
        What to do instead, or ``None`` when the program is not a
        relative path inside the tree or git does not ignore it.
    """
    if "/" not in program or program.startswith("/"):
        return None
    path = Path(os.path.normpath((tree_root / cwd).absolute() / program))
    try:
        rel = path.relative_to(tree_root.absolute())
    except ValueError:
        return None
    argv = ["git", "-C", str(tree_root), "check-ignore", "-q", "--", str(rel)]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    python = Path(program).name.startswith("python")
    if has_setup:
        advice = (
            "the spec's setup did not create it; check that setup builds "
            f"{rel.as_posix()!r} in every tree"
            + (", or run the program with `uv run --frozen`" if python else "")
        )
    else:
        advice = "add a `setup` to the spec that builds it in every tree" + (
            ' (for example [["uv", "sync", "--frozen"]]) and run the '
            "benchmark with `uv run --frozen ...` or with this tree-relative "
            "path after setup"
            if python
            else " (the project's build command) and run it by this "
            "tree-relative path after setup"
        )
    return (
        f"{program!r} is in a git-ignored path ({rel.as_posix()}), which a "
        f"fresh checkout of the baseline does not have; {advice}. Never "
        "point it into the main checkout: it would run the main "
        "checkout's build, or import its source, in both trees"
    )
