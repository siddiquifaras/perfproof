"""Git views of the working tree that `perfproof find` relies on.

`tree_id` names the tree a profile was taken on, so a profile of older
code is never read as current. `status_entries` is the before/after
check that catches a probe which changed the user's tree.

``.perfproof/`` is excluded from both: it is perfproof's own state and
changes with every probe.
"""

import hashlib
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final, cast

from perfproof.bench.snapshot import GIT_TIMEOUT_SECONDS
from perfproof.bench.spec import STATE_DIR

__all__ = [
    "NO_EXEMPTIONS",
    "Exemptions",
    "GitError",
    "added_entries",
    "status_entries",
    "tree_id",
]

_NO_HEAD: Final = b"no-head"
_CHUNK: Final = 1 << 20
_GIT_BIG_TIMEOUT: Final = 10 * GIT_TIMEOUT_SECONDS


class GitError(RuntimeError):
    """A git command the tree checks need did not run."""


def _git(root: Path, *args: str, check: bool = True) -> bytes:
    """Run git in ``root``, returning stdout bytes."""
    argv = ["git", "-C", str(root), *args]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            timeout=_GIT_BIG_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"git {args[0]} failed: {exc}"
        raise GitError(msg) from exc
    if check and done.returncode != 0:
        detail = done.stderr.decode("utf-8", "replace").strip()
        msg = f"git {args[0]} failed: {detail}"
        raise GitError(msg)
    return done.stdout if done.returncode == 0 else b""


def _git_sha256(root: Path, *args: str) -> bytes:
    """Run git in ``root`` and return the SHA-256 digest of its stdout.

    Streams the output, so a large diff is never held in memory.
    """
    argv = ["git", "-C", str(root), *args]
    try:
        with tempfile.TemporaryFile() as err:
            with subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=err
            ) as proc:
                # Killing a stalled git ends the blocked read.
                timer = threading.Timer(_GIT_BIG_TIMEOUT, proc.kill)
                timer.start()
                digest = hashlib.sha256()
                try:
                    out = cast("IO[bytes]", proc.stdout)
                    while chunk := out.read(_CHUNK):
                        digest.update(chunk)
                    code = proc.wait()
                finally:
                    timer.cancel()
            if code != 0:
                err.seek(0)
                detail = err.read().decode("utf-8", "replace").strip()
                msg = f"git {args[0]} failed: {detail or 'timed out'}"
                raise GitError(msg)
    except OSError as exc:
        msg = f"git {args[0]} failed: {exc}"
        raise GitError(msg) from exc
    return digest.digest()


def _file_hash(path: Path) -> str:
    """Return the SHA-256 of a file; a marker if it can't be read."""
    digest = hashlib.sha256()
    try:
        if path.is_symlink():
            digest.update(b"link:" + str(path.readlink()).encode())
            return digest.hexdigest()
        with path.open("rb") as f:
            while chunk := f.read(_CHUNK):
                digest.update(chunk)
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"
    return digest.hexdigest()


def _own_state(path: str) -> bool:
    """Whether a repo-relative path is under ``.perfproof/``."""
    return path == STATE_DIR or path.startswith(f"{STATE_DIR}/")


_CACHE_DIRS: Final = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)


@dataclass(frozen=True, slots=True)
class Exemptions:
    """Part roots where more tool output is not a probe's change.

    Each root is repository-relative with ``/`` separators, ``""`` for
    the repository root itself.

    Attributes
    ----------
    coverage : frozenset of str
        Every part root: ``.coverage*`` and ``htmlcov/`` directly in it.
    cargo : frozenset of str
        Cargo part roots: ``target/`` directly in them.
    gradle : frozenset of str
        Gradle part roots: ``.gradle/`` directly in them.
    """

    coverage: frozenset[str] = frozenset()
    cargo: frozenset[str] = frozenset()
    gradle: frozenset[str] = frozenset()


NO_EXEMPTIONS: Final = Exemptions()
"""No part roots: only the exemptions that hold anywhere."""


def _at_root(path: str, roots: frozenset[str], first: str) -> bool:
    """Whether ``path`` is under ``<root>/<first>`` for some root."""
    return any(
        path.startswith(f"{root}/{first}" if root else first) for root in roots
    )


def _is_cache(path: str, exempt: Exemptions) -> bool:
    """Whether a path is a well-known tool cache or build output.

    Only used for new, untracked paths: a tool writing its cache is not
    a probe changing the user's tree. ``__pycache__``,
    ``.pytest_cache``, ``.mypy_cache`` and ``.ruff_cache`` count
    anywhere, ``*.pyc`` and ``node_modules/.cache/`` too; the rest only
    where a part root says.
    """
    parts = path.split("/")
    name = parts[-1]
    if (
        not _CACHE_DIRS.isdisjoint(parts[:-1])
        or name.endswith(".pyc")
        or "node_modules/.cache/" in path
    ):
        return True
    parent = "/".join(parts[:-1])
    if name.startswith(".coverage") and parent in exempt.coverage:
        return True
    return (
        _at_root(path, exempt.coverage, "htmlcov/")
        or _at_root(path, exempt.cargo, "target/")
        or _at_root(path, exempt.gradle, ".gradle/")
    )


def tree_id(root: Path) -> str:
    """Return the identity of the working tree's content.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.

    Returns
    -------
    str
        SHA-256 hex of ``git rev-parse HEAD``, ``git diff HEAD`` and the
        sorted untracked, non-ignored files each with its content hash;
        ``.perfproof/`` excluded. Linear in the size of the diff and of
        the untracked files.

    Raises
    ------
    GitError
        If git does not run.
    """
    digest = hashlib.sha256()
    head = _git(root, "rev-parse", "--verify", "-q", "HEAD", check=False)
    digest.update(head.strip() or _NO_HEAD)
    digest.update(b"\0")
    if head.strip():
        diff = _git_sha256(
            root,
            "diff",
            "HEAD",
            "--binary",
            "--no-ext-diff",
            "--no-textconv",
            "--",
            ".",
            f":(exclude){STATE_DIR}",
        )
        digest.update(diff)
    digest.update(b"\0")
    listing = _git(root, "ls-files", "-o", "--exclude-standard", "-z")
    names = sorted(
        n.decode("utf-8", "surrogateescape") for n in listing.split(b"\0") if n
    )
    for name in names:
        if _own_state(name):
            continue
        digest.update(name.encode("utf-8", "surrogateescape") + b"\0")
        digest.update(_file_hash(root / name).encode() + b"\0")
    return digest.hexdigest()


def status_entries(
    root: Path, exempt: Exemptions = NO_EXEMPTIONS
) -> frozenset[str]:
    """List ``git status --porcelain`` lines, ignoring ``.perfproof/``.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    exempt : Exemptions, optional
        Part roots where more new untracked output is ignored.

    Returns
    -------
    frozenset of str
        One ``"XY path"`` entry per changed path, untracked files
        listed one by one; ignored paths are not in git's output.

    Raises
    ------
    GitError
        If git does not run.
    """
    raw = _git(root, "status", "--porcelain", "-z", "--untracked-files=all")
    tokens = [t for t in raw.split(b"\0") if t]
    entries: set[str] = set()
    index = 0
    while index < len(tokens):
        token = tokens[index].decode("utf-8", "surrogateescape")
        index += 1
        code, path = token[:2], token[3:]
        if code[0] in "RC":
            index += 1
        if not _own_state(path) and not (
            code == "??" and _is_cache(path, exempt)
        ):
            entries.add(f"{code} {path}")
    return frozenset(entries)


def added_entries(
    before: frozenset[str], after: frozenset[str]
) -> tuple[str, ...]:
    """Return the paths a probe added to the status.

    Parameters
    ----------
    before, after : frozenset of str
        `status_entries` before and after the probe.

    Returns
    -------
    tuple of str
        The paths of the entries only ``after`` has, sorted. An entry
        already present before the probe is not the probe's.
    """
    return tuple(sorted(e[3:] for e in after - before))
