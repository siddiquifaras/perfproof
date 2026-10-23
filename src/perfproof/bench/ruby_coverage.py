"""Turn a SimpleCov result into an lcov report.

SimpleCov writes ``coverage/.resultset.json``: one entry per test suite
that ran, each holding ``coverage``, which maps an absolute file path to
its line counts. SimpleCov 0.18 and later write ``{"lines": [...]}`` per
file, older versions the bare list; both are read. Element ``i`` is the
count for line ``i + 1`` and ``null`` marks a line with no code. The
report is lcov (``SF:``, ``DA:``, ``LF:``, ``LH:``,
``end_of_record``), the format the coverage gate takes from a language's
own tool.

Several suites in one result (a parallel run, or a run split by test
framework) are merged: a line's count is the sum over suites, so a line
that any suite ran is covered. Paths under ``root`` become relative to
it; with no ``root`` they are written as SimpleCov wrote them.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Final, cast

__all__ = ["CoverageError", "convert", "main"]

MAX_RESULT_BYTES: Final = 256 * 1024 * 1024
"""Largest result read, in bytes: the whole file is parsed."""


class CoverageError(ValueError):
    """A SimpleCov result that cannot be read.

    Parameters
    ----------
    reason : str
        What is wrong.
    """


def _counts(entry: object) -> list[int | None]:
    """Return the line counts of one file's entry, or raise."""
    lines = (
        cast("dict[str, object]", entry).get("lines")
        if isinstance(entry, dict)
        else entry
    )
    if not isinstance(lines, list):
        msg = "a file's coverage is neither a list nor an object with lines"
        raise CoverageError(msg)
    out: list[int | None] = []
    for item in cast("list[object]", lines):
        if item is None:
            out.append(None)
        elif (
            isinstance(item, int) and not isinstance(item, bool) and item >= 0
        ):
            out.append(item)
        else:
            msg = f"a line count is {item!r}, not null or a count"
            raise CoverageError(msg)
    return out


def _shown(path: str, root: PurePosixPath | None) -> str:
    if root is not None:
        try:
            return PurePosixPath(path).relative_to(root).as_posix()
        except ValueError:
            return path
    return path


def convert(result: Path, root: Path | None = None) -> str:
    """Return the lcov text for a SimpleCov ``.resultset.json``.

    Parameters
    ----------
    result : Path
        The result file.
    root : Path, optional
        Project root as SimpleCov saw it (inside a container, the
        container's path); paths below it are written relative to it.

    Returns
    -------
    str
        The lcov report, files in path order, empty when no file was
        measured.

    Raises
    ------
    CoverageError
        If the file is over `MAX_RESULT_BYTES`, is not JSON of the
        expected shape, or holds a count that is not an integer.
    OSError
        If the file cannot be read.
    """
    if result.stat().st_size > MAX_RESULT_BYTES:
        msg = f"{result} is over {MAX_RESULT_BYTES} bytes"
        raise CoverageError(msg)
    try:
        data: object = json.loads(result.read_text(encoding="utf-8"))
    except ValueError as exc:
        msg = f"{result} is not JSON: {exc}"
        raise CoverageError(msg) from exc
    if not isinstance(data, dict):
        msg = "the result is not an object of suites"
        raise CoverageError(msg)
    merged: dict[str, list[int | None]] = {}
    for suite in cast("dict[str, object]", data).values():
        files = (
            cast("dict[str, object]", suite).get("coverage")
            if isinstance(suite, dict)
            else None
        )
        if not isinstance(files, dict):
            msg = "a suite has no coverage object"
            raise CoverageError(msg)
        for path, entry in cast("dict[str, object]", files).items():
            counts = _counts(entry)
            have = merged.setdefault(path, [None] * len(counts))
            if len(counts) != len(have):
                msg = f"{path} has different line counts in two suites"
                raise CoverageError(msg)
            for i, count in enumerate(counts):
                if count is not None:
                    have[i] = (have[i] or 0) + count
    base = PurePosixPath(root.as_posix()) if root is not None else None
    out: list[str] = []
    for path in sorted(merged, key=lambda p: _shown(p, base)):
        rows = [
            (i + 1, c) for i, c in enumerate(merged[path]) if c is not None
        ]
        out.append(f"SF:{_shown(path, base)}")
        out.extend(f"DA:{line},{count}" for line, count in rows)
        out.append(f"LF:{len(rows)}")
        out.append(f"LH:{sum(1 for _, c in rows if c > 0)}")
        out.append("end_of_record")
    return "\n".join(out) + ("\n" if out else "")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line: ``convert <resultset> --output <lcov>``.

    Returns
    -------
    int
        0 on success; 2 when the result cannot be read (the reason is
        on stderr).
    """
    parser = argparse.ArgumentParser(
        prog="python -m perfproof.bench.ruby_coverage",
        description="Convert a SimpleCov result to lcov.",
    )
    parser.add_argument("result", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        text = convert(args.result, args.root)
    except (CoverageError, OSError) as exc:
        sys.stderr.write(f"perfproof.bench.ruby_coverage: {exc}\n")
        return 2
    args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
