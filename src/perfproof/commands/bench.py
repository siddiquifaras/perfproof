"""``perfproof bench``: define and lock a workload's benchmark spec.

``bench define NAME --spec FILE`` freezes a
`perfproof.schema.BenchmarkSpec` together with its lock manifest (the
SHA-256 of every harness, oracle, input and config file it declares)
at ``.perfproof/benchmarks/<name>/<spec-hash>.json`` in the repository,
and adds ``.perfproof/`` to the repository's ``info/exclude``. Defining
and locking are one step: a spec is never stored without the lock taken
when it was defined. A record is never overwritten; a changed spec is a
new hash and a new file. ``bench list NAME`` lists the stored versions
of a benchmark and ``bench show NAME HASH`` shows one, by its full hash
or a unique prefix.

The repository is the git working tree containing ``--repo`` (default:
the current directory); declared paths are relative to its root.

``--json`` result
-----------------
``define`` and ``show``
    ``{"action", "name", "spec_hash", "path", "spec", "lock"}``, plus
    ``"new"`` for ``define``: whether no record of this spec existed
    when ``define`` started. ``spec`` and ``lock`` are the JSON model
    dumps of the `BenchmarkSpec` and `perfproof.schema.LockManifest`;
    ``path`` is the record's absolute path.
``list``
    ``{"action", "name", "versions"}``; ``versions`` is a list of
    ``{"spec_hash", "path", "locked_files"}`` sorted by hash (not by
    age: a record holds no time).

Error codes
-----------
``invalid_spec``, ``invalid_name``, ``not_a_repository``,
``lock_failed``, ``spec_conflict``, ``invalid_record``, ``not_found``,
``ambiguous_hash``, and two for the operating system refusing access
to perfproof's own state, with the path in ``details.path``:
``state_write_failed`` (``define`` could not write the exclude file or
the record, for example because ``.perfproof`` is not a directory) and
``state_read_failed`` (``list`` or ``show`` could not read a record or
its directory).
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final, cast

from pydantic import ValidationError

from perfproof.bench.lock import LockError, build_manifest
from perfproof.bench.snapshot import (
    GIT_TIMEOUT_SECONDS,
    SnapshotError,
    exclude_state_dir,
)
from perfproof.bench.spec import (
    SpecConflictError,
    SpecRecordError,
    load_spec,
    save_spec,
    spec_record_path,
)
from perfproof.commands import CommandError, JsonValue, Output
from perfproof.schema import BenchmarkSpec, LockManifest

__all__ = ["add_arguments", "run"]

_HASH_FILE: Final = re.compile(r"^[0-9a-f]{64}\.json$")
_HEX: Final = re.compile(r"^[0-9a-f]+$")
_ZERO_HASH: Final = "0" * 64
_ROLES: Final = ("harness", "oracle", "inputs", "config")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``define``, ``list`` and ``show`` subcommands.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = "Define and lock a workload's benchmark spec."
    actions = parser.add_subparsers(
        dest="action", required=True, metavar="ACTION"
    )
    define = actions.add_parser(
        "define",
        help="freeze a spec and lock its declared files",
        description=(
            "Freeze a benchmark spec with the hashes of its harness, "
            "oracle, input and config files."
        ),
    )
    define.add_argument("name", help="benchmark name")
    define.add_argument(
        "--spec",
        required=True,
        metavar="FILE",
        help="JSON file with the spec's fields, or - for stdin",
    )
    listing = actions.add_parser(
        "list", help="list the stored versions of a benchmark"
    )
    listing.add_argument("name", help="benchmark name")
    show = actions.add_parser("show", help="show one stored spec version")
    show.add_argument("name", help="benchmark name")
    show.add_argument("spec_hash", metavar="HASH", help="hash or prefix")
    for sub in (define, listing, show):
        sub.add_argument(
            "--repo",
            type=Path,
            default=Path(),
            help="any path in the repository (default: .)",
        )


def run(args: argparse.Namespace) -> Output:
    """Run ``bench define``, ``bench list`` or ``bench show``.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed options.

    Returns
    -------
    Output
        The result described in this module's docstring. ``define``
        hashes every locked file, linear in their total bytes.

    Raises
    ------
    CommandError
        With one of the codes in this module's docstring.
    """
    action = cast("str", args.action)
    name = cast("str", args.name)
    root = _repo_root(cast("Path", args.repo))
    _check_name(root, name)
    if action == "define":
        return _define(root, name, cast("str", args.spec))
    if action == "list":
        return _list(root, name)
    return _show(root, name, cast("str", args.spec_hash))


def _repo_root(repo: Path) -> Path:
    """Return the root of the git working tree containing ``repo``."""
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
    except subprocess.TimeoutExpired as exc:
        msg = f"git rev-parse did not finish in {GIT_TIMEOUT_SECONDS} s"
        raise CommandError("not_a_repository", msg) from exc
    except OSError as exc:
        msg = f"git could not be started: {exc}"
        raise CommandError("not_a_repository", msg) from exc
    if done.returncode != 0:
        msg = (
            f"{repo} is not in a git working tree: {done.stderr.strip()}; "
            "perfproof keeps benchmarks in the repository's .perfproof/"
        )
        raise CommandError("not_a_repository", msg, {"repo": str(repo)})
    return Path(done.stdout.strip())


def _check_name(root: Path, name: str) -> None:
    """Refuse a name that is not a benchmark name."""
    try:
        spec_record_path(root, name, _ZERO_HASH)
    except ValueError as exc:
        raise CommandError("invalid_name", str(exc), {"name": name}) from exc


def _read_spec(source: str, name: str) -> BenchmarkSpec:
    """Read and validate the spec's fields from a file or stdin."""
    try:
        text = (
            sys.stdin.read()
            if source == "-"
            else Path(source).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"cannot read the spec from {source}: {exc}"
        raise CommandError("invalid_spec", msg, {"spec": source}) from exc
    try:
        fields: object = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"the spec in {source} is not JSON: {exc}"
        raise CommandError("invalid_spec", msg, {"spec": source}) from exc
    if not isinstance(fields, dict):
        msg = f"the spec in {source} must be a JSON object"
        raise CommandError("invalid_spec", msg, {"spec": source})
    data = cast("dict[str, object]", fields)
    given = data.setdefault("name", name)
    if given != name:
        msg = f"the spec in {source} is named {given!r}, not {name!r}"
        raise CommandError("invalid_spec", msg, {"spec": source})
    try:
        return BenchmarkSpec.model_validate_json(json.dumps(data))
    except ValidationError as exc:
        msg = f"the spec in {source} is not a valid benchmark spec: {exc}"
        raise CommandError("invalid_spec", msg, {"spec": source}) from exc


def _record(
    action: str,
    path: Path,
    spec: BenchmarkSpec,
    lock: LockManifest,
) -> dict[str, JsonValue]:
    """Build the ``--json`` result of one spec record."""
    return {
        "action": action,
        "name": spec.name,
        "spec_hash": spec.spec_hash,
        "path": str(path),
        "spec": spec.model_dump(mode="json"),
        "lock": lock.model_dump(mode="json"),
    }


def _locked_text(spec: BenchmarkSpec, lock: LockManifest) -> str:
    """Name each declared path, with its file count for a directory."""
    paths = [path for role in _ROLES for path in getattr(spec, role)]
    files = [f.path for f in lock.files]
    parts: list[str] = []
    for path in paths:
        if path in files:
            parts.append(path)
            continue
        count = sum(f.startswith(f"{path}/") for f in files)
        noun = "file" if count == 1 else "files"
        parts.append(f"{path}/ ({count} {noun})")
    return ", ".join(parts)


def _os_error(code: str, exc: OSError, path: Path) -> CommandError:
    """Turn an operating system refusal into a `CommandError`."""
    where = str(path) if exc.filename is None else str(exc.filename)
    reason = exc.strerror or str(exc)
    return CommandError(code, f"{where}: {reason}", {"path": where})


def _define(root: Path, name: str, source: str) -> Output:
    """Freeze a spec and its lock manifest."""
    spec = _read_spec(source, name)
    try:
        exclude_state_dir(root)
    except SnapshotError as exc:
        raise CommandError("not_a_repository", str(exc)) from exc
    except OSError as exc:
        git_dir = root / ".git"
        raise _os_error("state_write_failed", exc, git_dir) from exc
    try:
        lock = build_manifest(spec, root)
    except LockError as exc:
        raise CommandError(
            "lock_failed", str(exc), {"spec_hash": spec.spec_hash}
        ) from exc
    record = spec_record_path(root, name, spec.spec_hash)
    try:
        new = not record.exists()
        path = save_spec(root, spec, lock)
    except SpecConflictError as exc:
        raise CommandError(
            "spec_conflict", str(exc), {"spec_hash": spec.spec_hash}
        ) from exc
    except SpecRecordError as exc:
        raise CommandError("invalid_record", str(exc)) from exc
    except OSError as exc:
        raise _os_error("state_write_failed", exc, record) from exc
    result = _record("define", path, spec, lock)
    result["new"] = new
    if new:
        head = f"Spec {spec.spec_hash} locked: {_locked_text(spec, lock)}"
    else:
        head = (
            f"Spec {spec.spec_hash} was already defined with the same "
            "lock; nothing changed."
        )
    return Output(result=result, text=f"{head}\nRecord: {path}")


def _stored(root: Path, name: str) -> list[Path]:
    """List the record files of one benchmark, sorted by hash."""
    directory = spec_record_path(root, name, _ZERO_HASH).parent
    try:
        found = (
            sorted(
                p for p in directory.iterdir() if _HASH_FILE.fullmatch(p.name)
            )
            if directory.is_dir()
            else []
        )
    except OSError as exc:
        raise _os_error("state_read_failed", exc, directory) from exc
    if not found:
        msg = f"no spec is defined for benchmark {name!r} in {root}"
        raise CommandError("not_found", msg, {"name": name})
    return found


def _load(
    root: Path, name: str, path: Path
) -> tuple[BenchmarkSpec, LockManifest]:
    """Load one record, reporting a bad one as ``invalid_record``."""
    try:
        return load_spec(root, name, path.stem)
    except SpecRecordError as exc:
        raise CommandError(
            "invalid_record", str(exc), {"path": str(path)}
        ) from exc
    except OSError as exc:
        raise _os_error("state_read_failed", exc, path) from exc


def _list(root: Path, name: str) -> Output:
    """List a benchmark's stored versions."""
    versions: list[JsonValue] = []
    lines = [f"Benchmark {name}: versions by hash"]
    for path in _stored(root, name):
        _, lock = _load(root, name, path)
        versions.append(
            {
                "spec_hash": path.stem,
                "path": str(path),
                "locked_files": len(lock.files),
            }
        )
        lines.append(f"  {path.stem}  {len(lock.files)} locked files")
    result: dict[str, JsonValue] = {
        "action": "list",
        "name": name,
        "versions": versions,
    }
    return Output(result=result, text="\n".join(lines))


def _show(root: Path, name: str, prefix: str) -> Output:
    """Show one stored version, by full hash or unique prefix."""
    if not _HEX.fullmatch(prefix):
        msg = f"{prefix!r} is not a spec hash or a prefix of one"
        raise CommandError("not_found", msg, {"name": name})
    matches = [p for p in _stored(root, name) if p.stem.startswith(prefix)]
    if not matches:
        msg = f"benchmark {name!r} has no spec with hash {prefix}"
        raise CommandError("not_found", msg, {"name": name})
    if len(matches) > 1:
        candidates: list[JsonValue] = [p.stem for p in matches]
        msg = f"{prefix} matches {len(matches)} specs of {name!r}"
        raise CommandError(
            "ambiguous_hash", msg, {"name": name, "matches": candidates}
        )
    path = matches[0]
    spec, lock = _load(root, name, path)
    text = "\n".join(
        [
            f"Spec {spec.spec_hash} of {name}",
            f"Record: {path}",
            f"Locked: {_locked_text(spec, lock)}",
            json.dumps(spec.model_dump(mode="json"), indent=2, sort_keys=True),
        ]
    )
    return Output(result=_record("show", path, spec, lock), text=text)
