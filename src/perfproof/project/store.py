"""Persistence of the project model: atomic writes and the history log.

``.perfproof/project.json`` holds the current model and
``.perfproof/project.history.jsonl`` every revision of every entry,
append-only. A change is one transaction under an exclusive lock
(``project.lock``): the model is read again inside the lock, the change
is computed from it, the history lines are appended and flushed, and
only then is ``project.json`` replaced by a temporary file renamed over
it. A crash at any point leaves the previous ``project.json`` whole. A
crash after the history append and before the rename leaves history
lines for a revision the model does not hold; they are kept, never
removed, because losing a revision is worse than keeping an orphan.
A torn last line is isolated by a newline before the next append and
skipped, with its line number, when the history is read.
"""

import fcntl
import os
import tempfile
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from perfproof.bench.spec import STATE_DIR
from perfproof.project.errors import ProjectError
from perfproof.schema import ProjectHistoryRecord, ProjectModel

__all__ = [
    "HISTORY_FILE",
    "LOCK_TIMEOUT_SECONDS",
    "MODEL_FILE",
    "atomic_write_text",
    "commit",
    "history_path",
    "load_model",
    "locked",
    "model_path",
    "mutate",
    "read_history",
]

MODEL_FILE: Final = "project.json"
HISTORY_FILE: Final = "project.history.jsonl"
_LOCK_FILE: Final = "project.lock"
LOCK_TIMEOUT_SECONDS: Final = 30.0
"""How long a writer waits for another writer, in seconds."""


def model_path(root: Path) -> Path:
    """Return ``root/.perfproof/project.json``."""
    return root / STATE_DIR / MODEL_FILE


def history_path(root: Path) -> Path:
    """Return ``root/.perfproof/project.history.jsonl``."""
    return root / STATE_DIR / HISTORY_FILE


def load_model(root: Path) -> ProjectModel | None:
    """Read the current model.

    Parameters
    ----------
    root : Path
        Repository root.

    Returns
    -------
    ProjectModel or None
        ``None`` when no model has been written.

    Raises
    ------
    ProjectError
        ``invalid_model`` if the file cannot be read or is not a valid
        model: it is never repaired or replaced.
    """
    path = model_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise ProjectError(
            "invalid_model", f"cannot read {path}: {exc}", {"path": str(path)}
        ) from exc
    try:
        return ProjectModel.model_validate_json(text)
    except ValidationError as exc:
        raise ProjectError(
            "invalid_model",
            f"{path} is not a valid project model: {exc}",
            {"path": str(path)},
        ) from exc


def read_history(
    root: Path,
) -> tuple[tuple[ProjectHistoryRecord, ...], tuple[int, ...]]:
    """Read the history log.

    Parameters
    ----------
    root : Path
        Repository root.

    Returns
    -------
    records : tuple of ProjectHistoryRecord
        Every line that parsed, oldest first.
    skipped : tuple of int
        1-based numbers of lines that could not be read. They stay in
        the file.

    Raises
    ------
    ProjectError
        ``invalid_history`` if the file cannot be read at all.
    """
    path = history_path(root)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return (), ()
    except (OSError, UnicodeDecodeError) as exc:
        raise ProjectError(
            "invalid_history",
            f"cannot read {path}: {exc}",
            {"path": str(path)},
        ) from exc
    records: list[ProjectHistoryRecord] = []
    skipped: list[int] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            records.append(ProjectHistoryRecord.model_validate_json(line))
        except ValidationError:
            skipped.append(number)
    return tuple(records), tuple(skipped)


@contextmanager
def locked(root: Path) -> Generator[None]:
    """Hold the project's exclusive writer lock.

    Parameters
    ----------
    root : Path
        Repository root; ``.perfproof/`` is created if needed.

    Yields
    ------
    None
        While the lock is held.

    Raises
    ------
    ProjectError
        ``state_write_failed`` if the state directory cannot be made
        or ``lock_timeout`` if another writer holds the lock for
        `LOCK_TIMEOUT_SECONDS`.
    """
    state = root / STATE_DIR
    try:
        state.mkdir(exist_ok=True)
        handle = (state / _LOCK_FILE).open("a+b")
    except OSError as exc:
        raise ProjectError(
            "state_write_failed",
            f"cannot prepare {state}: {exc}",
            {"path": str(state)},
        ) from exc
    with handle:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ProjectError(
                        "lock_timeout",
                        "another perfproof process is writing the project "
                        "model",
                        {"path": str(state / _LOCK_FILE)},
                    ) from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path`` with ``text`` so no reader sees half of it.

    The text goes to a temporary file in the same directory, is
    flushed to disk, and is renamed over ``path``; the directory is
    flushed afterwards. On failure the temporary file is removed and
    ``path`` is untouched.

    Parameters
    ----------
    path : Path
        Destination; its directory must exist.
    text : str
        New content, written as UTF-8.
    """
    fd, name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    """Flush a directory entry; a no-op where the OS refuses."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _append_history(
    root: Path, records: Sequence[ProjectHistoryRecord]
) -> None:
    """Append ``records`` as one write, flushed to disk."""
    if not records:
        return
    payload = "".join(r.model_dump_json() + "\n" for r in records)
    with history_path(root).open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() > 0:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write(payload.encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())


def commit(
    root: Path,
    model: ProjectModel,
    records: Sequence[ProjectHistoryRecord],
) -> None:
    """Append history lines, then replace the model (under `locked`).

    Parameters
    ----------
    root : Path
        Repository root.
    model : ProjectModel
        The new current model.
    records : Sequence of ProjectHistoryRecord
        The revisions that produced it, oldest first.

    Raises
    ------
    ProjectError
        ``state_write_failed`` on an operating system error. The
        previous ``project.json`` is intact whenever this is raised.
    """
    try:
        _append_history(root, records)
        atomic_write_text(
            model_path(root), model.model_dump_json(indent=2) + "\n"
        )
    except OSError as exc:
        raise ProjectError(
            "state_write_failed",
            f"cannot write the project model: {exc}",
            {"path": str(model_path(root))},
        ) from exc


def mutate(
    root: Path,
    change: Callable[
        [ProjectModel], tuple[ProjectModel, Sequence[ProjectHistoryRecord]]
    ],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ProjectModel:
    """Read, change and write the model as one transaction.

    Parameters
    ----------
    root : Path
        Repository root.
    change : callable
        Receives the current model (an empty one when none exists) and
        returns the new model and the history records of the change.
        It may raise `ProjectError` to refuse, writing nothing.
    now : callable, optional
        Clock, for ``updated_at``.

    Returns
    -------
    ProjectModel
        The model now on disk. Nothing is written when ``change``
        returns no records.
    """
    with locked(root):
        current = load_model(root) or ProjectModel(updated_at=now())
        new, records = change(current)
        if not records:
            return current
        stamped = new.model_copy(update={"updated_at": now()})
        commit(root, stamped, records)
        return stamped
