"""Provenance helpers: file hashes, claims and derivation versions.

Three definitions the lifecycle and staleness rules share live here.

**Claim.** What an entry says, as opposed to where it came from and
how far the developer trusts it: every field except ``provenance``,
``version`` and the status machinery (``status``, ``confirmation``,
``invalidation``, ``previous_status``, ``retired_reason``). Two entries
with equal claims are "identical" for revalidation, and an entry's
``version`` bumps exactly when its claim changes (see
``docs/discovery.md``).

**Lockfile.** A file whose change fires ``lockfile_changed`` rather
than ``file_changed``, by base name (`LOCKFILES`).

**Derivation revision.** `DERIVATION_REVISION` names, per entry kind,
the first perfproof version whose way of deriving that kind differs
from every earlier one. An entry derived by an older version is stale
with ``perfproof_upgraded``.
"""

import hashlib
import re
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

from perfproof.schema import EntryKind, FileHash, ProjectEntry, Provenance

__all__ = [
    "DERIVATION_REVISION",
    "LOCKFILES",
    "claim",
    "file_hashes",
    "hash_file",
    "is_lockfile",
    "make_provenance",
    "needs_rederivation",
]

LOCKFILES: Final = frozenset(
    {
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        "pdm.lock",
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
    }
)
"""Base names of dependency lockfiles."""

DERIVATION_REVISION: Final[dict[EntryKind, str]] = {
    "target": "0.1.0",
    "workload": "0.1.0",
    "how_to_run": "0.1.0",
    "goal": "0.1.0",
    "constraint": "0.1.0",
}
"""First perfproof version of each kind's current derivation."""

_CHUNK: Final = 1 << 20
_STATUS_FIELDS: Final = frozenset(
    {
        "provenance",
        "version",
        "status",
        "confirmation",
        "invalidation",
        "previous_status",
        "retired_reason",
    }
)
_NUMBER: Final = re.compile(r"\d+")


def hash_file(path: Path) -> str:
    """Return the SHA-256 of a file's bytes, read in chunks.

    Parameters
    ----------
    path : Path
        File to hash.

    Returns
    -------
    str
        64 lowercase hex digits.

    Raises
    ------
    OSError
        If the file cannot be read.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def file_hashes(root: Path, paths: Iterable[str]) -> tuple[FileHash, ...]:
    """Hash the given files, relative to ``root``.

    Parameters
    ----------
    root : Path
        Repository root.
    paths : Iterable of str
        Normalized relative POSIX paths; duplicates are kept once.

    Returns
    -------
    tuple of FileHash
        Sorted by path.

    Raises
    ------
    OSError
        If a file cannot be read.
    """
    return tuple(
        FileHash(path=p, sha256=hash_file(root / p))
        for p in sorted(set(paths))
    )


def make_provenance(
    files: tuple[FileHash, ...],
    commands: tuple[tuple[str, ...], ...],
    version: str,
    now: datetime,
) -> Provenance:
    """Build a `Provenance`.

    Parameters
    ----------
    files : tuple of FileHash
        Files the entry was derived from.
    commands : tuple of tuple of str
        Commands that produced it, as argv lists.
    version : str
        Perfproof version.
    now : datetime
        When it was derived, with time zone.

    Returns
    -------
    Provenance
        The provenance record.
    """
    return Provenance(
        files=files,
        commands=commands,
        perfproof_version=version,
        derived_at=now,
    )


def is_lockfile(path: str) -> bool:
    """Return whether a relative path names a dependency lockfile."""
    return PurePosixPath(path).name in LOCKFILES


def claim(entry: ProjectEntry) -> dict[str, Any]:
    """Return an entry's claim, as defined in this module's docstring.

    Parameters
    ----------
    entry : ProjectEntry
        Any entry.

    Returns
    -------
    dict of str to Any
        JSON-mode dump without provenance, version or status fields.
    """
    return entry.model_dump(mode="json", exclude=set(_STATUS_FIELDS))


def _parts(version: str) -> tuple[int, ...]:
    """Return the leading numeric components of a version string."""
    head = re.split(r"[^0-9.]", version, maxsplit=1)[0]
    return tuple(int(n) for n in _NUMBER.findall(head))


def needs_rederivation(
    kind: EntryKind,
    derived_by: str,
    revisions: dict[EntryKind, str] | None = None,
) -> bool:
    """Return whether an upgrade changed how ``kind`` is derived.

    Parameters
    ----------
    kind : EntryKind
        Entry kind.
    derived_by : str
        `Provenance.perfproof_version` of the entry.
    revisions : dict, optional
        Overrides `DERIVATION_REVISION`, for tests.

    Returns
    -------
    bool
        ``True`` when ``derived_by`` is older than the kind's
        derivation revision. An unparsable version counts as older.
    """
    table = DERIVATION_REVISION if revisions is None else revisions
    have, want = _parts(derived_by), _parts(table[kind])
    if not have:
        return True
    width = max(len(have), len(want))
    pad = (0,) * width
    return (*have, *pad)[:width] < (*want, *pad)[:width]
