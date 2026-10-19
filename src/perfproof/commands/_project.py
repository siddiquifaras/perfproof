"""Helpers shared by the ``init`` and ``project`` commands."""

import os
import secrets
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

from perfproof.bench.snapshot import GIT_TIMEOUT_SECONDS
from perfproof.commands import CommandError, JsonValue
from perfproof.project.errors import ProjectError

__all__ = ["as_command_error", "default_session", "repo_root"]

SESSION_ENV: Final = "PERFPROOF_SESSION"


def repo_root(repo: Path) -> Path:
    """Return the root of the git working tree containing ``repo``.

    Raises
    ------
    CommandError
        ``not_a_repository`` outside a git working tree.
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
        msg = (
            f"{repo} is not in a git working tree: {done.stderr.strip()}; "
            "perfproof keeps the project model in the repository's "
            ".perfproof/"
        )
        raise CommandError("not_a_repository", msg, {"repo": str(repo)})
    return Path(done.stdout.strip())


def as_command_error(exc: ProjectError) -> CommandError:
    """Turn a `ProjectError` into the `CommandError` with its code."""
    details = cast("Mapping[str, JsonValue]", exc.details)
    return CommandError(exc.code, exc.message, details)


def default_session() -> str:
    """Return the session ID a confirmation is recorded under.

    ``$PERFPROOF_SESSION`` when set, so an agent session can name
    itself; otherwise a fresh ``cli-`` ID for this invocation.
    """
    return os.environ.get(SESSION_ENV) or f"cli-{secrets.token_hex(4)}"
