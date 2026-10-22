"""Profile a C or C++ program with ``sample`` or ``perf``.

``perfproof profile --profiler-command`` runs one argv and reads the
folded stacks it writes to ``{output}``. The native profilers write
their own text, so this module is the process that bridges them::

    python -m perfproof.profilers.native record --output {output}
        --root . -- {command}

It runs the command under the profiler the machine has, waits for it,
and converts the profiler's text to the folded form
`perfproof.profilers.folded` reads::

    main (hot.c:25);checksum (work.c:7) 963

- macOS: ``sample <pid> <seconds> 1`` (one sample a millisecond),
  attached to the command a moment after it starts, so the first tens
  of milliseconds are missed. ``sample`` records a thread's stack
  whether it runs or waits, so this is a wall-clock profile, not a CPU
  one: a blocked thread's wait shows as a frame (``__psynch_cvwait``,
  ``mach_msg2_trap``).
- Linux: ``perf record -F 999 -g`` and ``perf script``. perf needs
  ``perf_event_open``; where the kernel refuses it (a container
  without the capability, ``perf_event_paranoid`` too high) the reason
  is reported and nothing is written. A frame-pointer unwind stops at
  code built without frame pointers, so build the program with
  ``-fno-omit-frame-pointer`` and ``-g``.

A frame with source information is written ``name (path:line)``, the
path relative to ``--root`` when one file under it has that name. Both
tools print a file's base name only (the file in the debug info,
without its directory), so two files with the same base name under the
root cannot be told apart: a name that no file under the root has, or
that more than one has, is kept as printed. A frame with none is written
``image`name``. Symbols are the compiler's names; ``perf`` and
``sample`` both print C++ names demangled.

The command is passed to the profiler directly, so give the built
program: ``make`` or a script that starts it would be what is
profiled, not the program. The raw profiler output stays beside
``--output``. Stacks are aggregated as the text is read, so memory
grows with the number of distinct stacks, not with the number of
samples or the size of the raw file.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Final, TextIO

from perfproof.profilers._kill import freeze_and_kill

__all__ = [
    "SourceIndex",
    "fold_perf_script",
    "fold_sample_text",
    "main",
    "record",
]

DEFAULT_FREQUENCY_HZ: Final = 999
"""``perf record -F``: samples a second, per CPU the program runs on."""
DEFAULT_SAMPLE_SECONDS: Final = 30
"""How long ``sample`` watches a process that has not ended."""

_SKIPPED_DIRS: Final = frozenset(
    {"build", "out", "target", "node_modules", "__pycache__"}
)
_MAX_INDEXED_FILES: Final = 500_000
_SAMPLE_LINE: Final = re.compile(
    r"(?P<prefix>[ +!:|]*)(?P<count>\d+) (?P<rest>\S.*)"
)
_SAMPLE_FRAME: Final = re.compile(
    r"(?P<name>.+?)  \(in (?P<image>[^)]*)\)"
    r"(?: \+ [0-9,.]+)?  \[[^\]]*\](?:  (?P<where>\S.*))?"
)
_WHERE: Final = re.compile(r"(?P<file>.+):(?P<line>\d+)")
_PERF_FRAME: Final = re.compile(
    r"\s*(?P<ip>[0-9a-f]+)\s+(?P<sym>.+?)(?:\+0x[0-9a-f]+)?"
    r"(?:\s+\((?P<dso>[^)]*)\))?"
)
_SAMPLE_INDENT: Final = 4
_SAMPLE_STEP: Final = 2

_PRINT_TIMEOUT_S: Final = 1800.0

_EXIT_NO_TOOL: Final = 4
_EXIT_NO_RECORDING: Final = 5
_EXIT_BAD_RECORDING: Final = 6
_EXIT_TIMEOUT: Final = 124


class SourceIndex:
    """Find the path under a root of a source file named by base name.

    Parameters
    ----------
    root : pathlib.Path
        The project root that paths are made relative to.

    Notes
    -----
    The tree is walked once, on the first lookup, skipping hidden
    directories and ``build``, ``out``, ``target`` and ``node_modules``,
    and at most 500,000 files: ``O(files)`` time and memory. When
    several files have the name the location is unknown (``None``), not
    a guess between them.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._by_name: dict[str, list[str]] | None = None

    def locate(self, name: str) -> str | None:
        """Return the root-relative path of a file name, or ``None``."""
        if self._by_name is None:
            self._by_name = self._walk()
        found = self._by_name.get(name)
        return found[0] if found and len(found) == 1 else None

    def _walk(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        seen = 0
        for directory, names, files in os.walk(self._root):
            names[:] = [
                n
                for n in names
                if not n.startswith(".") and n not in _SKIPPED_DIRS
            ]
            for name in files:
                seen += 1
                if seen > _MAX_INDEXED_FILES:
                    return index
                full = Path(directory) / name
                try:
                    rel = full.relative_to(self._root).as_posix()
                except ValueError:
                    continue
                index.setdefault(name, []).append(rel)
        return index


def _frame(
    name: str, image: str, where: str | None, index: SourceIndex
) -> str:
    """Spell one frame in the folded form."""
    name = name.replace(";", ",")
    if where:
        match = _WHERE.fullmatch(where)
        if match is not None and int(match["line"]) > 0:
            base = match["file"].rpartition("/")[2]
            path = index.locate(base) if "/" not in match["file"] else None
            return f"{name} ({path or match['file']}:{match['line']})"
    return f"{image}`{name}" if image else name


def fold_sample_text(
    lines: Iterable[str], index: SourceIndex, counts: dict[str, int]
) -> int:
    """Fold the call graph of a ``sample`` report.

    ``sample`` prints a tree: each line is a count (the samples whose
    stack goes through the frame) and the frame, indented two columns
    below its caller, with ``+ ! : |`` marking the branches. A frame's
    own samples are its count less its children's.

    Parameters
    ----------
    lines : iterable of str
        The report, read once.
    index : SourceIndex
        Resolves source file names to project paths.
    counts : dict of str to int
        Updated in place: folded stack to samples. A thread's own line
        is not a frame, and the threads' stacks are summed.

    Returns
    -------
    int
        Samples added.
    """
    in_graph = False
    # Each open frame: [text, count, samples taken by its children].
    stack: list[list[object]] = []
    added = 0

    def close(depth: int) -> int:
        """Pop frames below ``depth``; return the samples they own."""
        owned = 0
        while len(stack) > depth:
            text_obj, count_obj, kids_obj = stack[-1]
            del text_obj
            own = int(str(count_obj)) - int(str(kids_obj))
            if own > 0 and len(stack) > 1:
                key = ";".join(str(entry[0]) for entry in stack[1:])
                counts[key] = counts.get(key, 0) + own
                owned += own
            stack.pop()
        return owned

    for raw in lines:
        line = raw.rstrip("\n")
        if not in_graph:
            in_graph = line.strip() == "Call graph:"
            continue
        if line.startswith(("Total number in stack", "Sort by top")):
            break
        match = _SAMPLE_LINE.fullmatch(line)
        if match is None:
            continue
        depth = (len(match["prefix"]) - _SAMPLE_INDENT) // _SAMPLE_STEP
        if depth < 0:
            continue
        added += close(depth)
        count = int(match["count"])
        if stack:
            stack[-1][2] = int(str(stack[-1][2])) + count
        rest = match["rest"]
        if depth == 0:
            text = rest
        else:
            parsed = _SAMPLE_FRAME.fullmatch(rest)
            text = (
                _frame(parsed["name"], parsed["image"], parsed["where"], index)
                if parsed is not None
                else rest.replace(";", ",")
            )
        stack.append([text, count, 0])
    added += close(0)
    return added


def _perf_stacks(
    lines: Iterable[str],
) -> Iterator[list[tuple[str, str | None]]]:
    """Yield each ``perf script`` sample, leaf first.

    A frame is a tab-indented line; with ``srcline`` it is followed by
    a line indented with spaces that holds ``file:line``. Samples are
    separated by blank lines.
    """
    frames: list[tuple[str, str | None]] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line.strip():
            if frames:
                yield frames
            frames = []
        elif line[0] == "\t":
            frames.append((line.strip(), None))
        elif frames and frames[-1][1] is None:
            frames[-1] = (frames[-1][0], line.strip())
    if frames:
        yield frames


def fold_perf_script(
    lines: Iterable[str], index: SourceIndex, counts: dict[str, int]
) -> int:
    """Fold ``perf script -F ip,sym,dso,srcline`` output.

    Parameters
    ----------
    lines : iterable of str
        The text, read once.
    index : SourceIndex
        Resolves source file names to project paths.
    counts : dict of str to int
        Updated in place: folded stack to samples.

    Returns
    -------
    int
        Samples added.
    """
    added = 0
    for frames in _perf_stacks(lines):
        key = ";".join(
            _perf_frame(text, where, index) for text, where in reversed(frames)
        )
        counts[key] = counts.get(key, 0) + 1
        added += 1
    return added


def _perf_frame(text: str, where: str | None, index: SourceIndex) -> str:
    """Spell one ``perf script`` frame in the folded form."""
    match = _PERF_FRAME.fullmatch(text)
    if match is None:
        return text.replace(";", ",")
    image = (match["dso"] or "").rpartition("/")[2]
    located = where if where and _WHERE.fullmatch(where) else None
    if located is not None and located.endswith(":0"):
        located = None
    return _frame(match["sym"], image, located, index)


def _say(message: str) -> None:
    sys.stderr.write(f"perfproof.profilers.native: {message}\n")


def _write(counts: dict[str, int], out: TextIO) -> None:
    for stack, count in sorted(counts.items()):
        out.write(f"{stack} {count}\n")


def record(
    command: Sequence[str],
    output: Path,
    *,
    tool: str = "auto",
    root: Path | None = None,
    timeout_seconds: float = 3600.0,
    sample_seconds: int = DEFAULT_SAMPLE_SECONDS,
    frequency_hz: int = DEFAULT_FREQUENCY_HZ,
) -> int:
    """Run a command under ``sample`` or ``perf``; write folded stacks.

    Parameters
    ----------
    command : sequence of str
        The program's argv. Its stdout goes to stderr, so the folded
        stacks are the only thing this writes to stdout.
    output : pathlib.Path
        Folded stacks are written here; the raw profile beside it as
        ``<output>.sample.txt`` or ``<output>.perf.data``.
    tool : {"auto", "sample", "perf"}, optional
        ``auto`` is ``sample`` on macOS and ``perf`` elsewhere.
    root : pathlib.Path, optional
        Project root that source paths are made relative to; the
        working directory when omitted.
    timeout_seconds : float, optional
        Time limit for the command, in seconds. The command and every
        descendant are killed when it is exceeded.
    sample_seconds : int, optional
        How long ``sample`` watches, in seconds, at least 1.
    frequency_hz : int, optional
        ``perf record -F``, at least 1.

    Returns
    -------
    int
        The command's exit status when it is non-zero, 124 on a
        timeout, 4 when the tool is missing or refused, 5 when it wrote
        no profile, 6 when the profile holds no samples, else 0. The
        reason goes to stderr.

    Raises
    ------
    ValueError
        If ``tool`` is not one of the three, or a number is below 1.
    """
    if tool not in {"auto", "sample", "perf"}:
        msg = f"tool must be auto, sample or perf, not {tool!r}"
        raise ValueError(msg)
    if sample_seconds < 1 or frequency_hz < 1:
        msg = "sample_seconds and frequency_hz must be at least 1"
        raise ValueError(msg)
    chosen = tool
    if tool == "auto":
        chosen = "sample" if sys.platform == "darwin" else "perf"
    index = SourceIndex(root or Path.cwd())
    counts: dict[str, int] = {}
    if chosen == "sample":
        status, samples = _record_sample(
            command, output, index, counts, timeout_seconds, sample_seconds
        )
    else:
        status, samples = _record_perf(
            command, output, index, counts, timeout_seconds, frequency_hz
        )
    if status in {_EXIT_NO_TOOL, _EXIT_NO_RECORDING, _EXIT_TIMEOUT}:
        return status
    if samples == 0:
        _say(
            "the profile holds no samples: the program ran too briefly "
            "or only waited; run the workload longer"
        )
        return status or _EXIT_BAD_RECORDING
    with output.open("w", encoding="utf-8") as out:
        _write(counts, out)
    return status


def _wait(process: subprocess.Popen[bytes], timeout_seconds: float) -> int:
    """Wait for a command; on the limit kill its tree and return 124."""
    try:
        return process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        freeze_and_kill(process.pid, _say)
        process.wait()
        _say(f"the command ran past {timeout_seconds:g} s and was killed")
        return _EXIT_TIMEOUT


def _record_sample(
    command: Sequence[str],
    output: Path,
    index: SourceIndex,
    counts: dict[str, int],
    timeout_seconds: float,
    sample_seconds: int,
) -> tuple[int, int]:
    """Run under macOS ``sample``; return ``(status, samples)``."""
    tool = shutil.which("sample")
    if tool is None:
        _say("sample was not found on the PATH (it is macOS-only)")
        return _EXIT_NO_TOOL, 0
    raw = Path(f"{output}.sample.txt")
    try:
        program = subprocess.Popen(list(command), stdout=sys.stderr)
    except OSError as exc:
        _say(f"{command[0]} could not start: {exc}")
        return _EXIT_NO_RECORDING, 0
    watcher = subprocess.Popen(
        [tool, str(program.pid), str(sample_seconds), "1", "-file", str(raw)],
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    status = _wait(program, timeout_seconds)
    try:
        watcher.wait(timeout=sample_seconds + 60)
    except subprocess.TimeoutExpired:
        watcher.kill()
        watcher.wait()
    if status == _EXIT_TIMEOUT:
        return status, 0
    if not raw.is_file():
        _say(
            f"sample wrote no report to {raw}: it could not attach to "
            f"process {program.pid}, or the program had already ended"
        )
        return _EXIT_NO_RECORDING, 0
    with raw.open(encoding="utf-8", errors="replace") as handle:
        samples = fold_sample_text(handle, index, counts)
    return status, samples


def _record_perf(
    command: Sequence[str],
    output: Path,
    index: SourceIndex,
    counts: dict[str, int],
    timeout_seconds: float,
    frequency_hz: int,
) -> tuple[int, int]:
    """Run under ``perf record``; return ``(status, samples)``."""
    tool = shutil.which("perf")
    if tool is None:
        _say("perf was not found on the PATH")
        return _EXIT_NO_TOOL, 0
    raw = Path(f"{output}.perf.data")
    try:
        recorder = subprocess.Popen(
            [
                tool,
                "record",
                "-F",
                str(frequency_hz),
                "-g",
                "-o",
                str(raw),
                "--",
                *command,
            ],
            stdout=sys.stderr,
        )
    except OSError as exc:
        _say(f"perf could not start: {exc}")
        return _EXIT_NO_TOOL, 0
    status = _wait(recorder, timeout_seconds)
    if status == _EXIT_TIMEOUT:
        return status, 0
    if not raw.is_file() or raw.stat().st_size == 0:
        _say(
            f"perf wrote no profile to {raw}: perf_event_open is "
            "probably refused (see perf's message above; a container "
            "needs --cap-add SYS_ADMIN and --security-opt "
            "seccomp=unconfined), or perf could not start the command"
        )
        return _EXIT_NO_TOOL, 0
    # perf script repeats "unwind: get_proc_name unsupported" for every
    # frame in a library it cannot name: that goes to a file, not the
    # terminal.
    notes = Path(f"{output}.perf-script.txt")
    with (
        notes.open("w", encoding="utf-8") as errors,
        subprocess.Popen(
            [tool, "script", "-i", str(raw), "-F", "ip,sym,dso,srcline"],
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            encoding="utf-8",
            errors="replace",
        ) as printer,
    ):
        stream = printer.stdout
        if stream is None:
            return _EXIT_BAD_RECORDING, 0
        timer = threading.Timer(_PRINT_TIMEOUT_S, printer.kill)
        timer.start()
        try:
            samples = fold_perf_script(stream, index, counts)
            printer.wait()
        finally:
            timer.cancel()
    if printer.returncode != 0:
        _say(
            f"perf script of {raw} failed or ran past "
            f"{_PRINT_TIMEOUT_S:g} s (exit {printer.returncode})"
        )
        return _EXIT_BAD_RECORDING, 0
    return status, samples


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``record`` command line.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments after the program name; ``sys.argv[1:]`` when
        omitted.

    Returns
    -------
    int
        The exit status `record` returns.
    """
    parser = argparse.ArgumentParser(
        prog="python -m perfproof.profilers.native",
        description="Run a native program under sample or perf.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    rec = sub.add_parser("record", help="run a command under a profiler")
    rec.add_argument("--output", required=True, type=Path)
    rec.add_argument(
        "--tool", choices=("auto", "sample", "perf"), default="auto"
    )
    rec.add_argument("--root", type=Path, default=None)
    rec.add_argument("--timeout", type=float, default=3600.0)
    rec.add_argument(
        "--sample-seconds", type=int, default=DEFAULT_SAMPLE_SECONDS
    )
    rec.add_argument("--frequency", type=int, default=DEFAULT_FREQUENCY_HZ)
    rec.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command: list[str] = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("give the command after --")
    return record(
        command,
        args.output,
        tool=args.tool,
        root=args.root,
        timeout_seconds=args.timeout,
        sample_seconds=args.sample_seconds,
        frequency_hz=args.frequency,
    )


if __name__ == "__main__":
    sys.exit(main())
