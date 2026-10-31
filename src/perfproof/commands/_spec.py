"""Spec lookup shared by the commands that name a stored benchmark."""

import re
from pathlib import Path
from typing import Final

from perfproof.bench.spec import SpecRecordError, load_spec, spec_record_path
from perfproof.commands import CommandError, JsonValue
from perfproof.schema import BenchmarkSpec, LockManifest

__all__ = ["select_spec"]

_HASH_FILE: Final = re.compile(r"^[0-9a-f]{64}\.json$")
_HEX: Final = re.compile(r"^[0-9a-f]+$")
_ZERO_HASH: Final = "0" * 64


def select_spec(
    root: Path, name: str, prefix: str | None
) -> tuple[BenchmarkSpec, LockManifest]:
    """Load the one spec version of ``name`` that ``prefix`` selects.

    Parameters
    ----------
    root : Path
        Repository root.
    name : str
        `BenchmarkSpec.name`.
    prefix : str or None
        A spec hash or a prefix of one; ``None`` when the benchmark has
        exactly one stored spec.

    Returns
    -------
    tuple of BenchmarkSpec and LockManifest
        The spec and the lock manifest frozen with it.

    Raises
    ------
    CommandError
        ``not_found`` for no such benchmark or hash, ``ambiguous_hash``
        for several, ``invalid_record`` for a stored spec that does not
        load.
    """
    try:
        directory = spec_record_path(root, name, _ZERO_HASH).parent
    except ValueError as exc:
        raise CommandError("not_found", str(exc), {"name": name}) from exc
    if prefix is not None and not _HEX.fullmatch(prefix):
        msg = f"{prefix!r} is not a spec hash or a prefix of one"
        raise CommandError("not_found", msg, {"name": name})
    try:
        found = (
            sorted(
                p.stem for p in directory.iterdir() if _HASH_FILE.match(p.name)
            )
            if directory.is_dir()
            else []
        )
    except OSError as exc:
        raise CommandError("invalid_record", str(exc)) from exc
    matches = [h for h in found if prefix is None or h.startswith(prefix)]
    if not matches:
        msg = f"no spec of benchmark {name!r} matches in {root}" + (
            "" if prefix is None else f" hash {prefix}"
        )
        raise CommandError("not_found", msg, {"name": name})
    if len(matches) > 1:
        listed: list[JsonValue] = list(matches)
        msg = (
            f"benchmark {name!r} has {len(matches)} matching specs; pick "
            "one with --spec-hash"
        )
        raise CommandError(
            "ambiguous_hash", msg, {"name": name, "matches": listed}
        )
    try:
        return load_spec(root, name, matches[0])
    except (SpecRecordError, OSError) as exc:
        raise CommandError("invalid_record", str(exc)) from exc
