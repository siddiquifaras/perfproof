"""Helpers the commands on detached runs share (not a command)."""

import subprocess
from pathlib import Path
from typing import cast

from perfproof.bench.detach import DetachError, find_run
from perfproof.bench.snapshot import GIT_TIMEOUT_SECONDS
from perfproof.commands import CommandError, JsonValue

__all__ = ["as_command_error", "repo_root", "run_directory"]


def as_command_error(exc: DetachError) -> CommandError:
    """Turn a refused detach request into the command's error.

    Parameters
    ----------
    exc : DetachError
        The refusal.

    Returns
    -------
    CommandError
        Same code and message, with the details as JSON values.
    """
    details: dict[str, JsonValue] = {}
    for key, value in exc.details.items():
        if isinstance(value, list):
            details[key] = [str(v) for v in cast("list[object]", value)]
        elif isinstance(value, (bool, int, float, str)):
            details[key] = value
        else:
            details[key] = str(value)
    return CommandError(exc.code, exc.message, details)


def repo_root(repo: Path) -> Path:
    """Return the root of the git working tree containing ``repo``.

    Parameters
    ----------
    repo : pathlib.Path
        Any path in the repository.

    Returns
    -------
    pathlib.Path
        The root.

    Raises
    ------
    CommandError
        ``not_a_repository`` if ``repo`` is in no git working tree.
    """
    argv = ["git", "-C", str(repo), "rev-parse", "--show-toplevel"]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"git rev-parse failed: {exc}"
        raise CommandError("not_a_repository", msg) from exc
    if done.returncode != 0:
        msg = f"{repo} is not in a git working tree: {done.stderr.strip()}"
        raise CommandError("not_a_repository", msg, {"repo": str(repo)})
    return Path(done.stdout.strip())


def run_directory(repo: Path, run_id: str) -> Path:
    """Return the directory of run ``run_id``.

    Parameters
    ----------
    repo : pathlib.Path
        Any path in the repository.
    run_id : str
        A run id or a unique prefix; see
        `perfproof.bench.detach.find_run`.

    Returns
    -------
    pathlib.Path
        The run directory.

    Raises
    ------
    CommandError
        ``not_a_repository`` or ``not_found``.
    """
    try:
        return find_run(repo_root(repo), run_id)
    except DetachError as exc:
        raise as_command_error(exc) from exc
