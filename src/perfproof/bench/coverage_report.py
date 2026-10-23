"""Read a standard coverage report: the plug-in point for any language.

The coverage gate (`perfproof.bench.coverage_gate`) measures Python
itself. For any other language, the spec's ``coverage_command`` writes
a report in a standard format, and this module reads it. The contract
is in "Coverage reports" in ``docs/method.md``:

- **Where.** When non-Python files changed, perfproof runs
  ``correctness.coverage_command`` in the candidate tree and puts the
  path to write in the environment variable `REPORT_ENV_VAR`. With no
  such command declared nothing runs, and the gate says so.
- **Formats.** lcov (``SF:``, ``DA:line,hits``, ``end_of_record``) and
  Cobertura XML (``class`` elements with a ``filename``, and ``line``
  elements with ``number`` and ``hits``), told apart by the first
  non-blank character (``<`` is XML). Only line hits are read; branch
  data is ignored.
- **Which file.** A path inside the candidate tree names that file;
  an absolute path outside it is ignored. A relative path is relative
  to the command's working directory (``correctness.cwd``), except in
  Cobertura, where it is relative to each ``<source>`` first.
- **Which lines.** A line listed with hits above zero ran; listed with
  zero hits, it is executable and never ran; not listed, it is not
  executable, as that tool sees it.
"""

import os
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final

__all__ = [
    "MAX_REPORT_BYTES",
    "REPORT_ENV_VAR",
    "ReportError",
    "read_report",
]

REPORT_ENV_VAR: Final = "PERFPROOF_COVERAGE_REPORT"
"""Environment variable naming the file the correctness command may
write its coverage report to, during perfproof's coverage run only."""
MAX_REPORT_BYTES: Final = 64 * 1024 * 1024
"""Largest report read, in bytes. Provisional: an input bound chosen
here, not measured and not a memory bound (parsing uses a multiple of
it); a larger report is refused as unreadable rather than parsed."""

type Hits = dict[str, dict[int, int]]


class ReportError(ValueError):
    """The report exists but cannot be read."""


def read_report(
    report: Path, root: Path, cwd: str = "."
) -> Mapping[str, Mapping[int, int]] | None:
    """Read a coverage report into per-file line hits.

    Parameters
    ----------
    report : Path
        The file the correctness command wrote.
    root : Path
        Root of the candidate tree.
    cwd : str, default "."
        The correctness command's working directory, relative to
        ``root``.

    Returns
    -------
    mapping of str to mapping of int to int, or None
        For each file inside the tree (path relative to ``root`` with
        ``/`` separators), the hit count of each listed line, 1-based.
        A line listed twice keeps its larger count. ``None`` when
        ``report`` does not exist.

    Raises
    ------
    ReportError
        If the report is larger than `MAX_REPORT_BYTES`, is not UTF-8,
        or is not valid lcov or Cobertura.
    OSError
        If the file exists but cannot be read.
    """
    if not report.is_file():
        return None
    if report.stat().st_size > MAX_REPORT_BYTES:
        msg = f"the report is larger than {MAX_REPORT_BYTES} bytes"
        raise ReportError(msg)
    data = report.read_bytes()
    bases = [Path(os.path.realpath(root / cwd))]
    if data.lstrip().startswith(b"<"):
        return _cobertura(data, Path(os.path.realpath(root)), bases)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = f"the report is not UTF-8: {exc}"
        raise ReportError(msg) from exc
    return _lcov(text, Path(os.path.realpath(root)), bases)


def _add(found: Hits, path: str | None, line: int, hits: int) -> None:
    """Record one line's hits, keeping the larger of repeated counts."""
    if path is None:
        return
    lines = found.setdefault(path, {})
    lines[line] = max(lines.get(line, 0), hits)


def _locate(name: str, bases: Iterable[Path], root: Path) -> str | None:
    """Name a report path as a tree-relative path, or None if outside.

    The first base under which a relative path is an existing file wins;
    with none, the first base decides.
    """
    if "\0" in name:
        msg = f"a path holds a NUL byte: {name!r}"
        raise ReportError(msg)
    given = Path(name)
    if given.is_absolute():
        candidates = [given]
    else:
        candidates = [base / given for base in bases]
    chosen = next((c for c in candidates if c.is_file()), candidates[0])
    resolved = Path(os.path.realpath(chosen))
    if not resolved.is_relative_to(root):
        return None
    return resolved.relative_to(root).as_posix()


def _count(raw: str, what: str) -> int:
    """Parse a non-negative integer, or raise `ReportError`."""
    try:
        value = int(raw)
    except ValueError:
        value = -1
    if value < 0:
        msg = f"{what} is not a non-negative integer: {raw!r}"
        raise ReportError(msg)
    return value


def _lcov(text: str, root: Path, bases: Sequence[Path]) -> Hits:
    """Parse lcov ``SF``, ``DA`` and ``end_of_record`` lines."""
    found: Hits = {}
    current: str | None = None
    inside = False
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line.startswith("SF:"):
            current = _locate(line[3:], bases, root)
            inside = True
            if current is not None:
                found.setdefault(current, {})
        elif line.startswith("DA:"):
            if not inside:
                msg = f"line {number}: DA outside an SF record"
                raise ReportError(msg)
            fields = line[3:].split(",")
            if len(fields) < 2:
                msg = f"line {number}: malformed DA record: {line!r}"
                raise ReportError(msg)
            lineno = _count(fields[0], f"line {number} line number")
            if lineno < 1:
                msg = f"line {number}: line numbers start at 1"
                raise ReportError(msg)
            _add(found, current, lineno, _count(fields[1], f"line {number}"))
        elif line == "end_of_record":
            inside = False
            current = None
    return found


def _cobertura(data: bytes, root: Path, bases: Sequence[Path]) -> Hits:
    """Parse Cobertura ``class`` and ``line`` elements."""
    if b"<!ENTITY" in data:
        msg = "the report declares XML entities"
        raise ReportError(msg)
    try:
        tree = ET.fromstring(data)  # noqa: S314 - entities refused above
    except ET.ParseError as exc:
        msg = f"the report is not valid XML: {exc}"
        raise ReportError(msg) from exc
    sources = [
        Path(os.path.realpath(Path(s.text.strip())))
        if Path(s.text.strip()).is_absolute()
        else Path(os.path.realpath(bases[0] / s.text.strip()))
        for s in tree.iter("source")
        if s.text and s.text.strip()
    ]
    found: Hits = {}
    for cls in tree.iter("class"):
        name = cls.get("filename")
        if name is None:
            msg = "a class element has no filename"
            raise ReportError(msg)
        path = _locate(name, [*sources, *bases], root)
        for line in cls.iterfind("lines/line"):
            number, hits = line.get("number"), line.get("hits")
            if number is None or hits is None:
                msg = f"a line of {name} has no number or hits"
                raise ReportError(msg)
            lineno = _count(number, f"{name} line number")
            if lineno < 1:
                msg = f"{name}: line numbers start at 1"
                raise ReportError(msg)
            _add(found, path, lineno, _count(hits, f"{name} hits"))
        if path is not None:
            found.setdefault(path, {})
    return found
