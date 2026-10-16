"""Baseline snapshots as detached git worktrees.

The baseline is a git commit. It is checked out with ``git worktree add
--detach`` under ``.perfproof/worktrees/<name>/`` so the baseline and
the candidate exist side by side and can be run in turns. The state
directory ``.perfproof/`` is added to the repository's
``info/exclude`` so it never shows as untracked.

Every git call takes an argv list and a timeout. Failures raise
`SnapshotError` with git's own message.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Final

from perfproof.bench.spec import STATE_DIR

__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "WORKTREE_ADD_TIMEOUT_SECONDS",
    "SnapshotError",
    "add_baseline_worktree",
    "exclude_state_dir",
    "resolve_commit",
    "verify_baseline_worktree",
    "worktree_commit",
]

GIT_TIMEOUT_SECONDS: Final = 60.0
"""Time limit for a git query, in seconds."""
WORKTREE_ADD_TIMEOUT_SECONDS: Final = 600.0
"""Time limit for checking out a worktree, in seconds; large
repositories take a while."""

_EXCLUDE_LINE: Final = f"{STATE_DIR}/"
_FULL_SHA: Final = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class SnapshotError(RuntimeError):
    """A git operation failed or a worktree is not the snapshot."""


def _git(cwd: Path, *args: str, timeout: float = GIT_TIMEOUT_SECONDS) -> str:
    """Run git in ``cwd`` and return its stripped stdout.

    Raises
    ------
    SnapshotError
        If git exits non-zero, cannot be started, or times out.
    """
    argv = ["git", "-C", str(cwd), *args]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"{' '.join(argv)} failed: {exc}"
        raise SnapshotError(msg) from exc
    if done.returncode != 0:
        msg = (
            f"{' '.join(argv)} exited {done.returncode}: {done.stderr.strip()}"
        )
        raise SnapshotError(msg)
    return done.stdout.strip()


def _toplevel(repo: Path) -> Path:
    """Return the root of the working tree containing ``repo``."""
    return Path(_git(repo, "rev-parse", "--show-toplevel"))


def resolve_commit(repo: Path, rev: str) -> str:
    """Resolve a revision to the full SHA of a commit.

    Parameters
    ----------
    repo : Path
        Any path inside the repository.
    rev : str
        A revision git understands: a branch, tag, ``HEAD~1`` or an
        abbreviated SHA.

    Returns
    -------
    str
        The commit's full SHA, 40 (or 64) lowercase hex digits.

    Raises
    ------
    SnapshotError
        If ``rev`` names no commit, or ``repo`` is not in a repository.
    """
    if not rev:
        msg = "an empty revision names no commit"
        raise SnapshotError(msg)
    return _git(
        repo,
        "rev-parse",
        "--verify",
        "--quiet",
        "--end-of-options",
        f"{rev}^{{commit}}",
    )


def worktree_commit(path: Path) -> str:
    """Return the full SHA of the commit checked out at ``path``.

    Parameters
    ----------
    path : Path
        Root of a working tree.

    Returns
    -------
    str
        Full SHA of ``HEAD`` in that tree.

    Raises
    ------
    SnapshotError
        If ``path`` is not in a git working tree.
    """
    return resolve_commit(path, "HEAD")


def exclude_state_dir(repo: Path) -> Path:
    """Add ``.perfproof/`` to the repository's ``info/exclude``.

    Idempotent: the line is added only if it is not there yet. From a
    linked worktree this edits the main repository's file, which git
    reads for every worktree.

    Parameters
    ----------
    repo : Path
        Any path inside the repository.

    Returns
    -------
    Path
        The exclude file.

    Raises
    ------
    SnapshotError
        If ``repo`` is not in a git repository.
    """
    exclude = Path(_git(repo, "rev-parse", "--git-path", "info/exclude"))
    if not exclude.is_absolute():
        exclude = repo.absolute() / exclude
    exclude = Path(os.path.normpath(exclude))
    exclude.parent.mkdir(parents=True, exist_ok=True)
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if _EXCLUDE_LINE in (line.strip() for line in text.splitlines()):
        return exclude
    prefix = "" if not text or text.endswith("\n") else "\n"
    with exclude.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}{_EXCLUDE_LINE}\n")
    return exclude


def verify_baseline_worktree(path: Path, commit: str) -> None:
    """Check that a baseline worktree still is the snapshot.

    Untracked files are allowed, because running the benchmark and its
    tests writes byproducts such as ``__pycache__``; locked files are
    checked by the lock gate whether tracked or not.

    Parameters
    ----------
    path : Path
        Root of the baseline worktree.
    commit : str
        Full SHA of the snapshot commit.

    Raises
    ------
    SnapshotError
        If ``HEAD`` is not ``commit`` or tracked files have changes,
        staged or not.
    """
    head = worktree_commit(path)
    if head != commit:
        msg = f"baseline worktree {path} is at {head}, not {commit}"
        raise SnapshotError(msg)
    changes = _git(path, "status", "--porcelain", "--untracked-files=no")
    if changes:
        msg = f"baseline worktree {path} has changes: {changes}"
        raise SnapshotError(msg)


def add_baseline_worktree(repo: Path, commit: str, name: str) -> Path:
    """Check out a commit as a detached worktree for the baseline.

    Also adds ``.perfproof/`` to ``info/exclude``. If the worktree
    already exists at ``commit`` with no tracked changes, it is reused.

    Parameters
    ----------
    repo : Path
        Any path inside the user's repository.
    commit : str
        Full SHA of the snapshot commit, as `resolve_commit` returns.
    name : str
        Directory name under ``.perfproof/worktrees/``.

    Returns
    -------
    Path
        Root of the baseline worktree.

    Raises
    ------
    ValueError
        If ``commit`` is not a full SHA or ``name`` is not a safe
        directory name.
    SnapshotError
        If git fails, the commit is not in the repository, or the
        target exists and is not a clean worktree at ``commit``.
    """
    if not _FULL_SHA.fullmatch(commit):
        msg = f"{commit!r} is not a full commit SHA"
        raise ValueError(msg)
    if not _NAME.fullmatch(name):
        msg = f"{name!r} is not a valid worktree name"
        raise ValueError(msg)
    root = _toplevel(repo)
    exclude_state_dir(root)
    path = root / STATE_DIR / "worktrees" / name
    if path.exists():
        if _toplevel(path) != path:
            msg = f"{path} exists and is not a worktree"
            raise SnapshotError(msg)
        verify_baseline_worktree(path, commit)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(
        root,
        "worktree",
        "add",
        "--detach",
        "--quiet",
        str(path),
        commit,
        timeout=WORKTREE_ADD_TIMEOUT_SECONDS,
    )
    verify_baseline_worktree(path, commit)
    return path
