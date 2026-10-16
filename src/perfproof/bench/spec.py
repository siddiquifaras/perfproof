"""Frozen benchmark spec records and the launch hash.

A spec is stored once, at
``.perfproof/benchmarks/<name>/<spec-hash>.json``, together with the
lock manifest taken when it was defined. The file is created with an
exclusive link, so a record is never overwritten: saving the same spec
and manifest again is a no-op, and saving the same spec with a
different manifest is refused. Changing any spec field changes its
hash, so a changed spec is always a new file beside the old one.

The launch hash is what `perfproof.schema.Sample.command_hash` holds:
the SHA-256 of the argv, declared environment, working directory and
resolved executable of one launched process. Paths inside the tree are
hashed relative to it, so the same launch in the baseline and the
candidate tree hashes the same.
"""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from perfproof.schema import BenchmarkSpec, LockManifest

__all__ = [
    "STATE_DIR",
    "SpecConflictError",
    "SpecRecordError",
    "command_hash",
    "load_spec",
    "normalize_launch_path",
    "save_spec",
    "spec_command_hash",
    "spec_record_path",
]

STATE_DIR: Final = ".perfproof"
"""Directory, at the repository root, that holds perfproof's state."""

_NAME: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256: Final = re.compile(r"^[0-9a-f]{64}$")


class SpecRecordError(ValueError):
    """A stored spec record is unreadable or does not match its key."""


class SpecConflictError(FileExistsError):
    """A different record already exists under a spec's hash."""


class _SpecRecord(BaseModel):
    """On-disk layout of ``<spec-hash>.json``: a spec and its lock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: BenchmarkSpec
    lock: LockManifest

    @model_validator(mode="after")
    def _check_lock(self) -> Self:
        if self.lock.spec_hash != self.spec.spec_hash:
            msg = (
                f"lock manifest names spec {self.lock.spec_hash}, not "
                f"{self.spec.spec_hash}"
            )
            raise ValueError(msg)
        return self


def spec_record_path(repo_root: Path, name: str, spec_hash: str) -> Path:
    """Return where the record of one spec version is stored.

    Parameters
    ----------
    repo_root : Path
        Root of the user's repository.
    name : str
        `BenchmarkSpec.name`.
    spec_hash : str
        `BenchmarkSpec.spec_hash`, 64 lowercase hex digits.

    Returns
    -------
    Path
        ``repo_root/.perfproof/benchmarks/<name>/<spec_hash>.json``.

    Raises
    ------
    ValueError
        If ``name`` is not a benchmark name or ``spec_hash`` is not a
        SHA-256 hex digest, so neither can leave the state directory.
    """
    if not _NAME.fullmatch(name):
        msg = f"{name!r} is not a benchmark name"
        raise ValueError(msg)
    if not _SHA256.fullmatch(spec_hash):
        msg = f"{spec_hash!r} is not a SHA-256 hex digest"
        raise ValueError(msg)
    return repo_root / STATE_DIR / "benchmarks" / name / f"{spec_hash}.json"


def save_spec(
    repo_root: Path, spec: BenchmarkSpec, manifest: LockManifest
) -> Path:
    """Store a spec and its lock manifest as a frozen record.

    Parameters
    ----------
    repo_root : Path
        Root of the user's repository. ``.perfproof/`` is created under
        it if needed; adding it to ``.git/info/exclude`` is
        `perfproof.bench.snapshot.exclude_state_dir`'s job.
    spec : BenchmarkSpec
        The spec to freeze.
    manifest : LockManifest
        Its lock manifest, taken when the spec was defined.

    Returns
    -------
    Path
        Path of the record, new or already present.

    Raises
    ------
    ValueError
        If ``manifest`` belongs to another spec.
    SpecConflictError
        If a record with a different manifest already exists under this
        spec's hash. The existing record is left as it was.
    SpecRecordError
        If an existing record under this hash cannot be read.
    """
    if manifest.spec_hash != spec.spec_hash:
        msg = (
            f"lock manifest belongs to spec {manifest.spec_hash}, not "
            f"{spec.spec_hash}"
        )
        raise ValueError(msg)
    record = _SpecRecord(spec=spec, lock=manifest)
    path = spec_record_path(repo_root, spec.name, spec.spec_hash)
    if path.exists():
        _check_existing(path, record)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(
        record.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{spec.spec_hash}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text + "\n")
        try:
            os.link(tmp, path)
        except FileExistsError:
            _check_existing(path, record)
    finally:
        tmp.unlink()
    return path


def _check_existing(path: Path, record: _SpecRecord) -> None:
    """Accept an existing record only if it equals ``record``."""
    existing = _read_record(path)
    if existing != record:
        msg = (
            f"{path} is a frozen record of spec {record.spec.spec_hash} "
            "with a different lock manifest; it is never overwritten. "
            "Define a new spec instead."
        )
        raise SpecConflictError(msg)


def _read_record(path: Path) -> _SpecRecord:
    """Read and validate one record file."""
    try:
        return _SpecRecord.model_validate_json(path.read_bytes())
    except ValidationError as exc:
        msg = f"{path} is not a valid spec record: {exc}"
        raise SpecRecordError(msg) from exc


def load_spec(
    repo_root: Path, name: str, spec_hash: str
) -> tuple[BenchmarkSpec, LockManifest]:
    """Load one frozen spec and its lock manifest.

    The stored ``spec_hash`` is recomputed from the stored content, and
    must equal both the requested hash and the lock manifest's.

    Parameters
    ----------
    repo_root : Path
        Root of the user's repository.
    name : str
        `BenchmarkSpec.name`.
    spec_hash : str
        `BenchmarkSpec.spec_hash` of the version to load.

    Returns
    -------
    tuple of (BenchmarkSpec, LockManifest)
        The spec and the manifest frozen with it.

    Raises
    ------
    FileNotFoundError
        If no record exists for ``name`` and ``spec_hash``.
    SpecRecordError
        If the record is invalid, was edited, or is stored under a name
        or hash that is not its own.
    ValueError
        If ``name`` or ``spec_hash`` is malformed.
    """
    path = spec_record_path(repo_root, name, spec_hash)
    record = _read_record(path)
    if record.spec.spec_hash != spec_hash:
        msg = f"{path} holds spec hash {record.spec.spec_hash}"
        raise SpecRecordError(msg)
    if record.spec.name != name:
        msg = f"{path} holds benchmark name {record.spec.name!r}"
        raise SpecRecordError(msg)
    return record.spec, record.lock


def normalize_launch_path(path: Path, tree_root: Path) -> str:
    """Spell a launched path the same way in every tree.

    Parameters
    ----------
    path : Path
        Absolute path of a launched executable or working directory.
    tree_root : Path
        Root of the tree it was launched in.

    Returns
    -------
    str
        The POSIX path relative to ``tree_root`` (``"."`` for the root
        itself) when ``path`` is inside it, else the absolute path. The
        path is made absolute and normalized but symlinks are not
        followed, so ``<tree>/.venv/bin/python`` stays inside the tree.

    Raises
    ------
    ValueError
        If ``path`` is relative.
    """
    if not path.is_absolute():
        msg = f"{path} is not absolute"
        raise ValueError(msg)
    target = Path(os.path.normpath(path))
    root = Path(os.path.normpath(tree_root.absolute()))
    if target.is_relative_to(root):
        return target.relative_to(root).as_posix()
    return str(target)


def command_hash(
    argv: Sequence[str],
    env: Mapping[str, str],
    cwd: str,
    executable: str,
) -> str:
    """Hash one launch, as stored in `perfproof.schema.Sample`.

    Parameters
    ----------
    argv : sequence of str
        The argv launched, exactly as passed to the process.
    env : mapping of str to str
        The declared environment variables set for it; inherited
        variables are not part of the hash.
    cwd : str
        Working directory, as `normalize_launch_path` spells it.
    executable : str
        Resolved executable, as `normalize_launch_path` spells it.

    Returns
    -------
    str
        SHA-256 of the canonical JSON of the four fields, 64 lowercase
        hex digits. Independent of the order of ``env``.
    """
    payload = {
        "argv": list(argv),
        "cwd": cwd,
        "env": dict(env),
        "executable": executable,
    }
    text = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(
        text.encode("utf-8", errors="surrogateescape")
    ).hexdigest()


def spec_command_hash(spec: BenchmarkSpec, executable: str) -> str:
    """Return the launch hash a spec's benchmark command must have.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force.
    executable : str
        The tree's resolved executable for ``spec.command[0]``, as
        `normalize_launch_path` spells it.

    Returns
    -------
    str
        `command_hash` of the spec's argv, declared environment and
        working directory with ``executable``.
    """
    return command_hash(spec.command, spec.env, spec.cwd, executable)
