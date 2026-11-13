"""The find state, ``.perfproof/find/state.json``.

One file holds everything a later call needs to resume: the probes
run and how each ended, the user's confirmations, the size knobs,
the waivers and the cost of every call. It is written to a temporary
file in the same directory and renamed, so a reader sees all of it or
none. A call to ``find`` holds `state_lock` from its first read to its
last write, so two calls on one checkout never interleave.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
import time
from collections.abc import Generator, Iterable, Sequence
from pathlib import Path
from typing import Final, cast

from pydantic import ValidationError

from perfproof.bench.spec import STATE_DIR
from perfproof.commands import CommandError
from perfproof.schema import FindState, Part, ProbeRecord

__all__ = [
    "LOCK_TIMEOUT_SECONDS",
    "STATE_FILE",
    "STICKY_STATUSES",
    "TERMINAL_STATUSES",
    "current_record",
    "evolve",
    "fresh_state",
    "hash_parts",
    "load_state",
    "load_test_commands",
    "reason_code",
    "save_state",
    "save_test_commands",
    "state_lock",
    "state_path",
    "was_blocked",
    "with_probe",
]

STATE_FILE: Final = "state.json"
LOCK_FILE: Final = "state.lock"
LOCK_TIMEOUT_SECONDS: Final = 5.0
"""Seconds a second ``find`` call waits before it fails."""
TERMINAL_STATUSES: Final = frozenset({"done", "failed", "timeout"})
"""Statuses of a probe that ran and is not run again on the same spec
and tree."""
STICKY_STATUSES: Final = frozenset({"tree_modified"})
"""Statuses that hold whatever the tree is: the probe changed it."""


def state_path(root: Path) -> Path:
    """Return ``root/.perfproof/find/state.json``."""
    return root / STATE_DIR / "find" / STATE_FILE


@contextlib.contextmanager
def state_lock(
    root: Path, timeout: float = LOCK_TIMEOUT_SECONDS
) -> Generator[None]:
    """Hold the exclusive find lock of ``root`` while the block runs.

    The lock is an ``flock`` on ``.perfproof/find/state.lock``, so it
    is released when the process dies. It is not re-entrant.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    timeout : float, optional
        Seconds to wait for another holder before giving up.

    Raises
    ------
    CommandError
        ``find_busy`` if another ``find`` call still holds the lock
        after ``timeout`` seconds; ``find_lock_unavailable`` if the
        file system refuses ``flock`` (the call never runs unlocked);
        ``state_write_failed`` if the lock file cannot be opened.
    """
    path = state_path(root).with_name(LOCK_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
    except OSError as exc:
        msg = f"cannot open {path}: {exc}"
        raise CommandError(
            "state_write_failed", msg, {"path": str(path)}
        ) from exc
    with handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise CommandError(
                        "find_busy",
                        "another perfproof find call is using this "
                        "checkout; wait for it and call again",
                        {"path": str(path)},
                    ) from None
                time.sleep(0.05)
            except OSError as exc:
                msg = (
                    f"cannot lock {path}: {exc}; this file system "
                    "doesn't support locks"
                )
                raise CommandError(
                    "find_lock_unavailable", msg, {"path": str(path)}
                ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def hash_parts(parts: Iterable[Part]) -> str:
    """Return the SHA-256 of the parts' JSON, in order.

    Parameters
    ----------
    parts : iterable of Part
        The parts `detect_parts` returned.

    Returns
    -------
    str
        64 hex digits.
    """
    payload = [p.model_dump(mode="json") for p in parts]
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def fresh_state(parts_hash: str) -> FindState:
    """Return an empty state for ``parts_hash``."""
    return FindState.model_validate(
        {
            "parts_hash": parts_hash,
            "probes": [],
            "size_knobs": {},
            "confirmed_tests": {},
            "waivers": {},
            "calls": [],
        }
    )


def evolve(state: FindState, **changes: object) -> FindState:
    """Return ``state`` with some fields replaced, validated.

    Parameters
    ----------
    state : FindState
        The state to start from.
    **changes : object
        Field values, as models or JSON-like values.

    Returns
    -------
    FindState
        A new state.
    """
    data = state.model_dump(mode="json")
    for key, value in changes.items():
        data[key] = _plain(value)
    return FindState.model_validate(data)


def _plain(value: object) -> object:
    """Turn models, mappings and tuples into JSON-like values."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        return {str(k): _plain(v) for k, v in mapping.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in cast("Sequence[object]", value)]
    return value


def with_probe(state: FindState, record: ProbeRecord) -> FindState:
    """Return ``state`` with ``record`` replacing the one of its key."""
    kept = [p for p in state.probes if p.key != record.key]
    return evolve(state, probes=[*kept, record])


BLOCKED_REASONS: Final = (
    "failed: profiler unavailable",
    "recorder_unavailable",
)
"""Leading text of the reason of a probe that never started because a
tool was missing."""


def was_blocked(record: ProbeRecord) -> bool:
    """Return whether ``record`` failed only for want of a tool."""
    return record.status == "failed" and (record.reason or "").startswith(
        BLOCKED_REASONS
    )


def current_record(
    state: FindState,
    key: str,
    spec_hash: str,
    tree: str,
    *,
    tool_present: bool = False,
) -> ProbeRecord | None:
    """Return the record that is still current for a probe, if any.

    Parameters
    ----------
    state : FindState
        The saved state.
    key : str
        The probe's key.
    spec_hash : str
        The spec's hash now.
    tree : str
        The working tree's id now.
    tool_present : bool, optional
        True when the profiler or recorder the probe needs is there
        now. A record of a probe that failed only for want of that
        tool is then not current, so it runs again.

    Returns
    -------
    ProbeRecord or None
        The record when its spec hash matches and either its tree
        matches or the probe changed the tree (it is never run again).
        A skipped record is never current: each call decides it again.
    """
    for record in state.probes:
        if record.key != key or record.spec_hash != spec_hash:
            continue
        if tool_present and was_blocked(record):
            continue
        if record.status in STICKY_STATUSES:
            return record
        if record.status in TERMINAL_STATUSES and record.tree_id == tree:
            return record
    return None


def reason_code(record: ProbeRecord) -> str:
    """Return the leading code of a record's reason.

    The reason is written ``<code>: <detail>`` or just ``<code>``; the
    codes are the mechanical waiver reasons ``no_tests``,
    ``recorder_unavailable``, ``tests_modified_tree``, ``timeout`` and
    ``failed``.
    """
    text = record.reason or ""
    return text.split(":", 1)[0].strip()


def load_state(root: Path) -> FindState | None:
    """Read the saved state.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.

    Returns
    -------
    FindState or None
        ``None`` when no state was written.

    Raises
    ------
    CommandError
        ``state_read_failed`` if the file cannot be read or is not a
        valid state; it is never repaired or replaced.
    """
    path = state_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise CommandError(
            "state_read_failed", msg, {"path": str(path)}
        ) from exc
    try:
        return FindState.model_validate_json(text)
    except ValidationError as exc:
        msg = f"{path} is not a valid find state: {exc}"
        raise CommandError(
            "state_read_failed", msg, {"path": str(path)}
        ) from exc


def save_state(root: Path, state: FindState) -> None:
    """Write the state whole or not at all.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    state : FindState
        The state to keep.

    Raises
    ------
    CommandError
        ``state_write_failed`` if it cannot be written.
    """
    _write_whole(state_path(root), state.model_dump_json(indent=2) + "\n")


def _write_whole(path: Path, data: str) -> None:
    """Write ``data`` to ``path`` through a unique temporary file."""
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    except OSError as exc:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
        msg = f"cannot write {path}: {exc}"
        raise CommandError(
            "state_write_failed", msg, {"path": str(path)}
        ) from exc


TEST_COMMANDS_FILE: Final = "test-commands.json"


def load_test_commands(root: Path) -> dict[str, tuple[str, ...]]:
    """Read the test commands the user gave with ``--test-command``.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.

    Returns
    -------
    dict
        ``Part.id`` to argv; empty when none were given.

    Raises
    ------
    CommandError
        ``state_read_failed`` if the file is unreadable or malformed.
    """
    path = state_path(root).with_name(TEST_COMMANDS_FILE)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise CommandError(
            "state_read_failed", msg, {"path": str(path)}
        ) from exc
    try:
        loaded: object = json.loads(text)
        if not isinstance(loaded, dict):
            raise TypeError("not an object")
        found = cast("dict[str, object]", loaded)
        return {
            part: tuple(cast("list[str]", argv))
            for part, argv in found.items()
            if isinstance(argv, list)
            and argv
            and all(isinstance(a, str) for a in cast("list[object]", argv))
        }
    except (ValueError, TypeError) as exc:
        msg = f"{path} is not a map of part to argv: {exc}"
        raise CommandError(
            "state_read_failed", msg, {"path": str(path)}
        ) from exc


def save_test_commands(
    root: Path, commands: dict[str, tuple[str, ...]]
) -> None:
    """Write the test commands whole or not at all.

    Raises
    ------
    CommandError
        ``state_write_failed`` if it cannot be written.
    """
    data = json.dumps({k: list(v) for k, v in commands.items()}, indent=2)
    _write_whole(state_path(root).with_name(TEST_COMMANDS_FILE), data + "\n")
