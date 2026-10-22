"""Turn gcov or llvm-cov coverage of a C or C++ program into lcov.

The coverage gate reads one report format from a language's own tool:
lcov (``SF:``, ``DA:``, ``LF:``, ``LH:``, ``end_of_record``). This is
the command a native project declares as its coverage command, or runs
inside it, after its tests ran in a build made with coverage::

    python -m perfproof.bench.native_coverage gcov
        --root . --output "$PERFPROOF_COVERAGE_REPORT" build/
    python -m perfproof.bench.native_coverage llvm
        --root . --output "$PERFPROOF_COVERAGE_REPORT"
        --object build/tests build/

- ``gcov``: a gcc build with ``--coverage`` leaves a ``.gcda`` file per
  object file. Each is read with ``gcov --json-format --stdout`` (gcc 9
  and later), one process at a time.
- ``llvm``: a clang build with ``-fprofile-instr-generate
  -fcoverage-mapping`` leaves ``.profraw`` files, which
  ``llvm-profdata merge`` combines and ``llvm-cov export
  -format=lcov`` reads against the built objects (``--object``). On
  macOS the tools are found through ``xcrun``.

Either way, only files under ``--root`` are written, with paths
relative to it; system headers and dependencies are left out. A line's
count is the sum over every object file that holds it (a header
compiled into several), so it is covered when any of them ran it. Only
line records are written: gcov and llvm-cov also measure functions and
branches, which the gate does not read. A file with no executable line
gets no record. What a count means follows the tool: gcov's is the
times the line's first block ran; llvm-cov's is the times the line's
first region ran.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO, cast

__all__ = ["CoverageError", "Summary", "main", "write_lcov"]

_TOOL_TIMEOUT_S: Final = 1800.0

type Counts = dict[str, dict[int, int]]
"""File (relative to the root) to line number to execution count."""


class CoverageError(ValueError):
    """Coverage that cannot be read or produced.

    Parameters
    ----------
    reason : str
        What is wrong.
    """


@dataclass(frozen=True, slots=True)
class Summary:
    """What a conversion wrote.

    Attributes
    ----------
    files : int
        Files with a record in the report.
    lines : int
        Lines with a ``DA`` record.
    covered : int
        Of those, lines with a count above zero.
    """

    files: int
    lines: int
    covered: int


def _relative(path: str, base: Path, root: Path) -> str | None:
    """Return ``path`` as a path under ``root``, or ``None``."""
    full = Path(path) if os.path.isabs(path) else base / path
    try:
        return full.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return None


def add_gcov_json(document: str, root: Path, counts: Counts) -> None:
    """Add one ``gcov --json-format`` document to the counts.

    Parameters
    ----------
    document : str
        One JSON document, as ``gcov --json-format --stdout`` prints
        for one ``.gcda`` file (``format_version`` 2).
    root : pathlib.Path
        Resolved project root.
    counts : dict
        Updated in place.

    Raises
    ------
    CoverageError
        If the text is not a gcov document.
    """
    try:
        loaded: object = json.loads(document)
    except ValueError as exc:
        raise CoverageError(f"not JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise CoverageError("not a gcov document")
    data = cast("dict[str, object]", loaded)
    files = data.get("files")
    cwd = data.get("current_working_directory")
    if not isinstance(files, list) or not isinstance(cwd, str):
        raise CoverageError("no files or current_working_directory")
    for entry in cast("list[object]", files):
        if not isinstance(entry, dict):
            continue
        item = cast("dict[str, object]", entry)
        name = item.get("file")
        lines = item.get("lines")
        if not isinstance(name, str) or not isinstance(lines, list):
            raise CoverageError("a file entry has no name or lines")
        rel = _relative(name, Path(cwd), root)
        if rel is None:
            continue
        per_file = counts.setdefault(rel, {})
        for row in cast("list[object]", lines):
            if not isinstance(row, dict):
                continue
            record = cast("dict[str, object]", row)
            number, count = record.get("line_number"), record.get("count")
            if isinstance(number, int) and isinstance(count, int):
                per_file[number] = per_file.get(number, 0) + max(count, 0)


def add_lcov(text: Iterable[str], root: Path, counts: Counts) -> None:
    """Add the ``DA`` records of an lcov report to the counts.

    Parameters
    ----------
    text : iterable of str
        The report, read once. ``SF`` names a file (absolute, or
        relative to ``root``); ``DA:line,count[,checksum]`` a line.
    root : pathlib.Path
        Resolved project root.
    counts : dict
        Updated in place.

    Raises
    ------
    CoverageError
        If a ``DA`` record is malformed or comes before any ``SF``.
    """
    current: dict[int, int] | None = None
    for number, raw in enumerate(text, start=1):
        line = raw.strip()
        if line.startswith("SF:"):
            rel = _relative(line[3:], root, root)
            current = None if rel is None else counts.setdefault(rel, {})
        elif line.startswith("DA:"):
            fields = line[3:].split(",")
            if len(fields) < 2 or not all(
                f.lstrip("-").isdigit() for f in fields[:2]
            ):
                raise CoverageError(f"line {number}: bad DA record {line!r}")
            if current is not None:
                at, count = int(fields[0]), max(int(fields[1]), 0)
                current[at] = current.get(at, 0) + count
        elif line == "end_of_record":
            current = None


def write_lcov(counts: Counts, out: TextIO) -> Summary:
    """Write counts as lcov and say what was written.

    Parameters
    ----------
    counts : dict
        File to line to count. Files with no line are left out.
    out : TextIO
        Where the report goes.

    Returns
    -------
    Summary
        Files, lines and covered lines written.
    """
    files = lines = covered = 0
    for name in sorted(counts):
        per_file = counts[name]
        if not per_file:
            continue
        files += 1
        out.write(f"SF:{name}\n")
        for number in sorted(per_file):
            out.write(f"DA:{number},{per_file[number]}\n")
        hit = sum(1 for c in per_file.values() if c > 0)
        out.write(f"LF:{len(per_file)}\nLH:{hit}\nend_of_record\n")
        lines += len(per_file)
        covered += hit
    return Summary(files, lines, covered)


def _run(argv: Sequence[str], cwd: Path) -> str:
    """Run a tool and return its stdout, or raise with its stderr."""
    try:
        done = subprocess.run(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_TOOL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CoverageError(f"{argv[0]} did not run: {exc}") from exc
    if done.returncode != 0:
        raise CoverageError(
            f"{' '.join(argv[:2])} exited {done.returncode}: "
            f"{done.stderr.strip()[-300:]}"
        )
    return done.stdout


def _find(names: Sequence[str]) -> list[str]:
    """Return the argv prefix that runs the first of ``names`` found.

    A tool on the ``PATH`` runs as it is; otherwise, on macOS, the
    Command Line Tools copy found through ``xcrun``.
    """
    for name in names:
        if shutil.which(name) is not None:
            return [name]
    xcrun = shutil.which("xcrun")
    if xcrun is not None:
        for name in names:
            done = subprocess.run(
                [xcrun, "--find", name],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if done.returncode == 0 and done.stdout.strip():
                return [done.stdout.strip()]
    raise CoverageError(f"none of {', '.join(names)} was found")


def _gather(paths: Sequence[Path], suffix: str, what: str) -> list[Path]:
    """List the files with a suffix among ``paths``, searching dirs."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            for directory, _, names in os.walk(path):
                found.extend(
                    Path(directory) / n for n in names if n.endswith(suffix)
                )
        elif path.is_file():
            found.append(path)
        else:
            raise CoverageError(f"{path} does not exist")
    if not found:
        raise CoverageError(
            f"no {suffix} files under {', '.join(map(str, paths))}: the "
            f"program was not built with {what}, or its tests did not run"
        )
    return sorted(found)


def collect_gcov(
    paths: Sequence[Path], root: Path, gcov: Sequence[str] = ("gcov",)
) -> Counts:
    """Read the ``.gcda`` files among ``paths`` with ``gcov``.

    Each file is read by its own ``gcov --json-format --stdout``, run in
    ``root``, so memory is that of one document.
    """
    counts: Counts = {}
    for gcda in _gather(paths, ".gcda", "--coverage"):
        out = _run([*gcov, "--json-format", "--stdout", str(gcda)], root)
        for document in out.splitlines():
            if document.strip():
                add_gcov_json(document, root, counts)
    return counts


def collect_llvm(
    paths: Sequence[Path], objects: Sequence[Path], root: Path
) -> Counts:
    """Merge ``.profraw`` files and export them as lcov with llvm-cov.

    ``llvm-cov export`` reads the counters against the ``objects`` the
    profile came from, and prints lcov, of which the ``DA`` records of
    files under ``root`` are kept.
    """
    if not objects:
        raise CoverageError("give the built program or test with --object")
    raws = _gather(paths, ".profraw", "-fprofile-instr-generate")
    profdata = _find(["llvm-profdata"])
    cov = _find(["llvm-cov"])
    with tempfile.TemporaryDirectory(prefix="perfproof-cov-") as scratch:
        merged = Path(scratch) / "merged.profdata"
        _run(
            [*profdata, "merge", "-sparse", "-o", str(merged)]
            + [str(r) for r in raws],
            root,
        )
        first, *rest = (str(o) for o in objects)
        report = _run(
            [
                *cov,
                "export",
                "-format=lcov",
                f"-instr-profile={merged}",
                first,
                *[arg for o in rest for arg in ("-object", o)],
            ],
            root,
        )
    counts: Counts = {}
    add_lcov(report.splitlines(), root, counts)
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; return its exit status.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments after the program name; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        0 when a report was written, 2 when the coverage could not be
        read or made (the reason goes to stderr and no report is
        written).
    """
    parser = argparse.ArgumentParser(
        prog="python -m perfproof.bench.native_coverage",
        description="Write gcov or llvm-cov coverage as lcov.",
    )
    sub = parser.add_subparsers(dest="tool", required=True)
    for name in ("gcov", "llvm"):
        one = sub.add_parser(name)
        one.add_argument("--root", type=Path, required=True)
        one.add_argument("--output", type=Path, required=True)
        one.add_argument("paths", nargs="+", type=Path)
    sub.choices["gcov"].add_argument("--gcov", default="gcov")
    sub.choices["llvm"].add_argument(
        "--object", action="append", default=[], type=Path
    )
    args = parser.parse_args(argv)
    root = cast("Path", args.root).resolve()
    try:
        if args.tool == "gcov":
            counts = collect_gcov(args.paths, root, [str(args.gcov)])
        else:
            counts = collect_llvm(args.paths, args.object, root)
    except CoverageError as exc:
        sys.stderr.write(f"perfproof.bench.native_coverage: {exc}\n")
        return 2
    with args.output.open("w", encoding="utf-8") as out:
        summary = write_lcov(counts, out)
    sys.stderr.write(
        f"perfproof.bench.native_coverage: {summary.covered} of "
        f"{summary.lines} lines ran, in {summary.files} files\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
