"""Write ``record.json`` and ``report.md`` into a run directory.

``record.json`` is the record's JSON form, as Pydantic serializes it,
indented, with one trailing newline. ``report.md`` is rendered from
that text read back, never from the in-memory model, so the report
always shows exactly what the record file holds.

Each file is published atomically: written to a temporary file in the
run directory, flushed to disk, then hard-linked to its final name,
which fails if the name exists. So a final name only ever holds a
complete file, and ``record.json`` is never deleted once linked. A
crash or failure mid-write can leave a temporary file, which no reader
looks at, and can leave ``record.json`` without ``report.md``. `write`
is resumable: a retry with the same record finishes the job. An
existing file with exactly the bytes being written counts as written,
so two writers of the same record both succeed.
"""

import os
import tempfile
from pathlib import Path
from typing import Final

from perfproof.report.render import render
from perfproof.schema import RunRecord

__all__ = ["RECORD_NAME", "REPORT_NAME", "read_record", "record_json", "write"]

RECORD_NAME: Final = "record.json"
"""File name of the machine-readable record in a run directory."""
REPORT_NAME: Final = "report.md"
"""File name of the readable report in a run directory."""


def record_json(record: RunRecord) -> str:
    """Return the exact text of ``record.json`` for ``record``.

    Parameters
    ----------
    record : RunRecord
        A validated run record.

    Returns
    -------
    str
        Indented JSON with one trailing newline. Reading it back with
        `read_record` gives an equal record, and serializing that gives
        the same text.
    """
    return record.model_dump_json(indent=2) + "\n"


def read_record(path: Path) -> RunRecord:
    """Read and validate a ``record.json``.

    Parameters
    ----------
    path : pathlib.Path
        Path of the record file.

    Returns
    -------
    RunRecord
        The validated record.

    Raises
    ------
    OSError
        If the file cannot be read.
    pydantic.ValidationError
        If the file is not a valid record of this schema version.
    """
    return RunRecord.model_validate_json(path.read_bytes())


def _publish(path: Path, data: bytes) -> None:
    """Create ``path`` holding ``data``, completely or not at all.

    A ``path`` that already holds exactly ``data``, written earlier or
    by a concurrent writer, counts as success and is left as it is.

    Raises
    ------
    FileExistsError
        If ``path`` exists with other bytes; it is left untouched.
    OSError
        If writing, flushing or linking fails.
    """
    if path.exists():
        _check_same(path, data)
        return
    fd, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:
            _check_same(path, data)
    finally:
        tmp.unlink(missing_ok=True)


def _check_same(path: Path, data: bytes) -> None:
    """Refuse an existing ``path`` whose bytes are not ``data``."""
    if path.read_bytes() != data:
        msg = f"{path} exists with different content; not replaced"
        raise FileExistsError(msg)


def write(record: RunRecord, run_dir: Path) -> tuple[Path, Path]:
    """Write ``record.json`` and ``report.md`` into ``run_dir``.

    A run's record is written once. Both files are UTF-8 with LF line
    endings on every platform, and each is published atomically.
    ``record.json`` is written first and never deleted; ``report.md``
    is rendered from ``record.json`` as stored.

    The call is resumable, idempotent and safe to run concurrently for
    the same record. An existing file that holds exactly the bytes this
    record gives counts as written, so:

    - neither file exists: write both;
    - ``record.json`` matches and ``report.md`` is missing, as a crash
      or a failed report write leaves it: write the report;
    - both exist and match: write nothing and return their paths;
    - another writer of the same record wins a race to either file:
      both calls succeed.

    Anything else is refused: a ``record.json`` that differs, a
    ``report.md`` without ``record.json``, or a ``report.md`` that
    differs from the stored record's report.

    Parameters
    ----------
    record : RunRecord
        A validated run record.
    run_dir : pathlib.Path
        The run's existing directory, ``.perfproof/runs/<run-id>/``.

    Returns
    -------
    tuple of pathlib.Path
        Paths of the record and the report, in that order.

    Raises
    ------
    FileExistsError
        If an existing file is refused as above; no file is changed.
    OSError
        If a file cannot be read or written. ``record.json`` may then
        exist without ``report.md``; a retry completes it.
    pydantic.ValidationError
        If the stored ``record.json`` no longer validates.
    """
    record_path = run_dir / RECORD_NAME
    report_path = run_dir / REPORT_NAME
    # The record is linked before the report and never deleted, so a
    # report without a record was not written by this function.
    if report_path.exists() and not record_path.exists():
        msg = f"{report_path} exists without {RECORD_NAME}; not replaced"
        raise FileExistsError(msg)
    _publish(record_path, record_json(record).encode("utf-8"))
    report = render(read_record(record_path))
    _publish(report_path, report.encode("utf-8"))
    return record_path, report_path
