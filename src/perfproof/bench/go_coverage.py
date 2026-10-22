"""Turn a Go coverage profile into an lcov report.

``go test -coverprofile=cover.out`` writes a text profile: a ``mode:``
line, then one line per basic block::

    example.com/prog/work.go:13.34,15.26 2 1

(file as an import path, ``startLine.startCol,endLine.endCol``, the
number of statements in the block, and how often it ran, or 0 or 1 in
``set`` mode). A program built with ``go build -cover`` writes counter
files instead, which ``go tool covdata textfmt`` turns into the same
text. Both are read here, and written as lcov (``SF:``, ``DA:``,
``LF:``, ``LH:``, ``end_of_record``), the report format the coverage
gate takes from a language's own tool.

Go counts blocks, lcov counts lines, so a line gets the count of the
blocks it holds code of, and the smallest count wins: a line is
covered only when every block with code on it ran. That is deliberate.
A one-line ``if x { y() }`` is one line and two blocks, and a line must
not count as run when its body was never reached. A block's range can
also begin at the ``{`` that ends a line, and end at the ``}`` that
starts one; those two lines hold no statement of the block, and are not
charged to it (read from the source file; when the file cannot be read
every line of the range is charged). Blank and comment-only lines get no
``DA`` record.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO

from perfproof.profilers.go import module_path

__all__ = [
    "CoverageError",
    "CoverageSummary",
    "convert",
    "main",
]

_BLOCK: Final = re.compile(
    r"(?P<file>.+):(?P<sl>\d+)\.(?P<sc>\d+),(?P<el>\d+)\.(?P<ec>\d+) "
    r"(?P<stmts>\d+) (?P<count>\d+)"
)
_MODES: Final = frozenset({"set", "count", "atomic"})
_NO_CODE: Final = ("//", "/*", "*")


class CoverageError(ValueError):
    """A coverage profile that cannot be read.

    Parameters
    ----------
    reason : str
        What is wrong, with the line number where there is one.
    """


@dataclass(frozen=True, slots=True)
class CoverageSummary:
    """What a conversion wrote.

    Attributes
    ----------
    files : int
        Files with a record in the report.
    lines : int
        Lines with a ``DA`` record.
    covered : int
        Of those, lines with a count above zero.
    skipped : tuple of str
        Files of the profile that are not in the module (a dependency
        or the standard library, under ``-coverpkg``), left out.
    """

    files: int
    lines: int
    covered: int
    skipped: tuple[str, ...]


def _source_lines(path: Path) -> list[str] | None:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None


def _charged_lines(
    source: list[str] | None, sl: int, sc: int, el: int, ec: int
) -> list[int]:
    """Return the lines of a block that hold its statements.

    The range is the block's own; its first line is dropped when
    nothing but ``{`` (and space) follows the start column, and its last
    when nothing but ``}`` (and space) precedes the end column. Blank
    lines and lines that start a comment are never charged.
    """
    first, last = sl, el
    if source is not None and el > sl:
        if 0 < sl <= len(source):
            start = source[sl - 1].encode("utf-8")[sc - 1 :]
            if start.decode("utf-8", "replace").strip() in ("", "{"):
                first = sl + 1
        if 0 < el <= len(source):
            end = source[el - 1].encode("utf-8")[: ec - 1]
            if end.decode("utf-8", "replace").strip() in ("", "}"):
                last = el - 1
    lines = range(first, max(first, last + 1))
    if source is None:
        return list(lines)
    return [n for n in lines if n > len(source) or _holds_code(source[n - 1])]


def _holds_code(text: str) -> bool:
    """Say whether a source line is neither blank nor a comment."""
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith(_NO_CODE)


def convert(profile: Path, root: Path, out: TextIO) -> CoverageSummary:
    """Write a Go coverage profile as an lcov report.

    Parameters
    ----------
    profile : pathlib.Path
        A text profile from ``go test -coverprofile`` or ``go tool
        covdata textfmt``.
    root : pathlib.Path
        The module's root, where ``go.mod`` is. File names in the
        profile are import paths under the module path; a name outside
        it is skipped. The report names files relative to ``root``.
    out : TextIO
        Receives the report, files in order of name.

    Returns
    -------
    CoverageSummary
        Counts of what was written, and the files left out.

    Raises
    ------
    CoverageError
        If the profile has no ``mode:`` line first or a line that is not
        a block, or ``root`` has no ``go.mod`` with a module path.

    Notes
    -----
    The profile is read once; memory is one count per instrumented line
    of the module, plus the source file the profile is on (blocks come
    grouped by file; a profile that interleaves files reads each again).
    """
    module = module_path(root)
    if module is None:
        msg = f"{root / 'go.mod'} has no module path"
        raise CoverageError(msg)
    counts: dict[str, dict[int, int]] = {}
    skipped: dict[str, None] = {}
    sources: dict[str, list[str] | None] = {}
    with profile.open(encoding="utf-8") as handle:
        header = handle.readline().strip()
        mode = header.removeprefix("mode:").strip()
        if not header.startswith("mode:") or mode not in _MODES:
            msg = f"line 1: expected 'mode: set|count|atomic', got {header!r}"
            raise CoverageError(msg)
        for number, raw in enumerate(handle, start=2):
            text = raw.strip()
            if not text:
                continue
            block = _BLOCK.fullmatch(text)
            if block is None:
                msg = f"line {number}: not a coverage block: {text[:80]!r}"
                raise CoverageError(msg)
            name = block["file"]
            if not name.startswith(module + "/"):
                skipped[name] = None
                continue
            relative = name[len(module) + 1 :]
            if relative not in sources:
                sources.clear()  # blocks come grouped by file
                sources[relative] = _source_lines(root / relative)
            lines = counts.setdefault(relative, {})
            count = int(block["count"])
            for line in _charged_lines(
                sources[relative],
                int(block["sl"]),
                int(block["sc"]),
                int(block["el"]),
                int(block["ec"]),
            ):
                lines[line] = min(lines.get(line, count), count)
    total = hit = 0
    for relative in sorted(counts):
        lines = counts[relative]
        out.write(f"TN:\nSF:{relative}\n")
        for line in sorted(lines):
            out.write(f"DA:{line},{lines[line]}\n")
        covered = sum(1 for c in lines.values() if c > 0)
        out.write(f"LF:{len(lines)}\nLH:{covered}\nend_of_record\n")
        total += len(lines)
        hit += covered
    return CoverageSummary(
        files=len(counts),
        lines=total,
        covered=hit,
        skipped=tuple(skipped),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments after the program name; ``sys.argv[1:]`` when
        omitted.

    Returns
    -------
    int
        0 on success, 6 when the profile or the module cannot be read.
    """
    parser = argparse.ArgumentParser(
        prog="python -m perfproof.bench.go_coverage",
        description="Convert a Go coverage profile to lcov.",
    )
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        with args.output.open("w", encoding="utf-8") as out:
            summary = convert(args.profile, args.root, out)
    except (CoverageError, OSError) as exc:
        sys.stderr.write(f"perfproof.bench.go_coverage: {exc}\n")
        return 6
    if summary.skipped:
        sys.stderr.write(
            "perfproof.bench.go_coverage: left out "
            f"{len(summary.skipped)} files outside the module, such as "
            f"{summary.skipped[0]}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
