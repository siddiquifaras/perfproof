"""Profile a .NET command with dotnet-trace and write folded stacks.

``perfproof profile --profiler-command`` runs one argv and reads the
folded stacks it writes to ``{output}``. dotnet-trace writes a
``.nettrace`` file and, with ``--format Speedscope``, converts it to
Speedscope JSON, whose frames carry an assembly and a method but no
file, so this module is the process that bridges them::

    python -m perfproof.profilers.dotnet record --output {output}
        -- {command}

It runs ``dotnet-trace collect --format Speedscope -- {command}`` under
a time limit, reads the JSON (an *evented* profile per thread: frames
opened and closed at times) and writes folded stacks weighted in
microseconds::

    Prog.Work.JoinSlow (App/Work.cs:10);...  1204

A frame of an assembly whose source is found under the project root
(`SourceIndex`: the ``.cs`` file that declares the type, and the line of
the method's first mention below it) is shown with that path, so it
counts as project code. Any other frame, the runtime and every package,
is ``Assembly`1Namespace.Type.Method`` with no file, which the summary
treats as native library code unless the assembly is named with
``--own-module`` to ``perfproof profile``.

dotnet-trace traces the one .NET process it launches. ``dotnet run``
launches the SDK's host, which starts the program as a child: that
child is not traced (the trace holds ``dotnet.dll run`` and the SDK's
frames, no frame of the program). Build in ``setup`` and profile
``dotnet bin/App.dll``. The sampling is dotnet-trace's own default
(``dotnet-sampled-thread-time``, a stack of each running thread
about every millisecond); time in the runtime's start-up and in the
SDK is in it.

dotnet-trace is an apphost: it finds the runtime only through
``DOTNET_ROOT`` or ``/usr/local/share/dotnet``. ``perfproof profile``
passes a trimmed environment that drops ``DOTNET_ROOT``, so a .NET
installed elsewhere (``~/.dotnet``, any user-local directory) made it
exit 131 ("You must install .NET"). When the environment has no
``DOTNET_ROOT``, the tool gets one for that process only: the directory
of the ``dotnet`` first on its ``PATH``, symlinks resolved. An explicit
``DOTNET_ROOT`` is kept, and with no ``dotnet`` on the ``PATH`` the
environment is left alone so dotnet-trace's own error stands.

The JSON is read in chunks with a pattern for the fixed layout the tool
writes, so memory does not grow with the length of the trace: it holds
the frame names, one stack per thread, and the distinct stacks since
the last write. The raw ``.nettrace`` and JSON stay in
``{output}.dotnet-trace/``, untouched.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Final, TextIO

from perfproof.profilers._kill import freeze_and_kill

__all__ = [
    "SourceIndex",
    "SpeedscopeFormatError",
    "fold_speedscope",
    "main",
    "record",
    "run_command",
]

CHUNK_CHARS: Final = 1 << 20
"""Characters of the JSON read at a time."""
FLUSH_STACKS: Final = 100_000
"""Distinct stacks held before they are written."""
MAX_SOURCE_FILES: Final = 100_000
"""Most ``.cs`` files the source index reads."""
MAX_SOURCE_BYTES: Final = 4 * 1024 * 1024
"""Largest ``.cs`` file the source index reads."""

_EXIT_NO_TOOL: Final = 4
_EXIT_NO_PROFILE: Final = 5
_EXIT_EMPTY: Final = 6
_EXIT_STALE: Final = 7
_EXIT_TIMEOUT: Final = 124
_NO_RUNTIME: Final = 2
"""dotnet-trace's own status when no .NET runtime connected to it."""

_TOKEN: Final = re.compile(
    r'\{ "name": (?P<frame>"(?:[^"\\]|\\.)*") \}'
    r'|"type": "evented", "name": "(?:[^"\\]|\\.)*", "unit": '
    r'"(?P<unit>[a-z]+)", "startValue": (?P<start>[-0-9.eE+]+)'
    r'|\{ "type": "(?P<kind>[OC])", "frame": (?P<index>[0-9]+), '
    r'"at": (?P<at>[-0-9.eE+]+) \}'
)
_PROCESS_FRAME: Final = re.compile(
    r"Process[0-9]* .*|\(Non-Activities\)|Threads|Thread \([0-9]+\).*"
)
"""The frames dotnet-trace puts at the root of every thread: the
process, the activity group, the thread group and the thread. None has
an ``!``, which every frame of a method does."""
_SKIPPED_DIRS: Final = frozenset(
    {"bin", "obj", "node_modules", ".git", "packages", "TestResults"}
)
_TYPE: Final = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*"
    r"(?:(?:public|internal|private|protected|static|sealed|abstract|"
    r"partial|readonly|unsafe|file|ref|new)\s+)*"
    r"(?:class|struct|record(?:\s+struct|\s+class)?|interface|enum)\s+"
    r"(?P<name>\w+)"
)
_NAMESPACE: Final = re.compile(r"^\s*namespace\s+(?P<name>[\w.]+)")
_GENERIC_ARGS: Final = re.compile(r"\[[^\[\]]*\]")
_UNMANAGED: Final = "UNMANAGED_CODE_TIME"
_SAMPLE_MARK: Final = "CPU_TIME"


class SpeedscopeFormatError(ValueError):
    """A Speedscope file that is not the layout dotnet-trace writes.

    Parameters
    ----------
    reason : str
        What is wrong.
    """


class SourceIndex:
    """Find the file and line of a .NET type and method in a project.

    The ``.cs`` files under the root are read once, for namespace and
    type declarations (a regular expression per line: braces, directives
    conditions and strings are not parsed, so a type declared inside a
    comment or a verbatim string can be taken for a real one). A type
    maps to its file and line; a method to the first line below the type
    that names it followed by ``(``, or the type's line. Generic and
    nested types are looked up by their outer name. A type declared in
    more than one file (``partial``) maps to the first file found, in
    path order.

    Parameters
    ----------
    root : Path
        The project root; paths are shown relative to it.
    source_roots : sequence of Path, optional
        Further directories to read, shown relative to the root when
        inside it.
    """

    def __init__(self, root: Path, source_roots: Sequence[Path] = ()) -> None:
        self._root = root.resolve()
        self._types: dict[str, tuple[Path, int]] = {}
        self._lines: dict[Path, list[str]] = {}
        files = 0
        for base in (root, *source_roots):
            for path in self._walk(base):
                files += 1
                if files > MAX_SOURCE_FILES:
                    return
                self._read(path)

    def _walk(self, base: Path) -> Iterator[Path]:
        for directory, names, files in os.walk(base):
            names[:] = sorted(n for n in names if n not in _SKIPPED_DIRS)
            for name in sorted(files):
                if name.endswith(".cs"):
                    yield Path(directory, name)

    def _read(self, path: Path) -> None:
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                return
            lines = path.read_text(
                encoding="utf-8-sig", errors="replace"
            ).splitlines()
        except OSError:
            return
        namespace = ""
        for number, line in enumerate(lines, start=1):
            found = _NAMESPACE.match(line)
            if found:
                namespace = found["name"]
                continue
            found = _TYPE.match(line)
            if found:
                name = f"{namespace}.{found['name']}".lstrip(".")
                self._types.setdefault(name, (path, number))

    def shown(self, path: Path) -> str:
        """Return a path relative to the root when it is under it."""
        try:
            return path.resolve().relative_to(self._root).as_posix()
        except ValueError:
            return path.as_posix()

    def locate(self, type_name: str, member: str) -> tuple[str, int] | None:
        """Return ``(file, line)`` of a method, or ``None`` if unknown.

        Parameters
        ----------
        type_name : str
            The type with its namespace, as the runtime names it; a
            nested type is ``Outer+Inner``.
        member : str
            The method's name; ``.ctor`` and ``.cctor`` find the type's
            own name, a generated ``<Name>b__0`` finds ``Name``.
        """
        name = type_name.split("+", 1)[0]
        found = self._types.get(name)
        if found is None:
            return None
        path, line = found
        simple = name.rpartition(".")[2]
        if member.startswith("<") and ">" in member:
            member = member[1 : member.index(">")]
        elif member in {".ctor", ".cctor"}:
            member = simple
        text = self._text(path)
        pattern = re.compile(rf"\b{re.escape(member)}\s*(?:<[^>]*>)?\s*\(")
        for number in range(line, len(text) + 1):
            if pattern.search(text[number - 1]):
                return self.shown(path), number
        return self.shown(path), line

    def _text(self, path: Path) -> list[str]:
        cached = self._lines.get(path)
        if cached is None:
            if len(self._lines) >= 64:
                self._lines.clear()
            try:
                cached = path.read_text(
                    encoding="utf-8-sig", errors="replace"
                ).splitlines()
            except OSError:
                cached = []
            self._lines[path] = cached
        return cached


def _folded_frame(name: str, index: SourceIndex) -> str:
    """Write one Speedscope frame in the form the summary reads."""
    if name == _UNMANAGED:
        return "[unmanaged code]"
    module, bang, rest = name.partition("!")
    if not bang:
        return name.replace(";", ":")
    symbol = rest.split("(", 1)[0]
    symbol = _GENERIC_ARGS.sub("", symbol) if "[" in symbol else symbol
    if ".." in symbol:
        type_name, _, tail = symbol.partition("..")
        member = f".{tail}"
    else:
        type_name, _, member = symbol.rpartition(".")
    clean_type = re.sub(r"`[0-9]+", "", type_name)
    located = index.locate(clean_type, member) if type_name else None
    shown = f"{clean_type}.{member}" if type_name else member
    shown = shown.replace(";", ":")
    if located is not None:
        return f"{shown} ({located[0]}:{located[1]})"
    return f"{module}`{shown}"


def fold_speedscope(
    chunks: Iterator[str], index: SourceIndex, out: TextIO
) -> int:
    """Fold a Speedscope file of dotnet-trace into folded stacks.

    Time between two events of a thread goes to the stack open before
    the second, in whole microseconds. The ``CPU_TIME`` frame the tool
    adds below each sampled stack is dropped; ``UNMANAGED_CODE_TIME``
    is kept as ``[unmanaged code]``; the process, activity and thread
    frames at the roots are dropped. Memory is the frame names, one
    stack per thread and at most `FLUSH_STACKS` distinct stacks.

    Parameters
    ----------
    chunks : iterator of str
        The file's text, in order, in pieces.
    index : SourceIndex
        Where to find the project's source.
    out : TextIO
        Where folded stacks are written; a stack may appear more than
        once (the reader sums them).

    Returns
    -------
    int
        Total microseconds written.

    Raises
    ------
    SpeedscopeFormatError
        If an event refers to a frame that was not defined, closes a
        frame that is not on top of its thread's stack, or the unit is
        not milliseconds.
    """
    frames: list[str] = []
    shown: dict[int, str | None] = {}
    stack: list[int] = []
    previous = 0.0
    counts: dict[str, int] = {}
    total = 0
    buffer = ""

    def flush() -> None:
        for text, count in counts.items():
            out.write(f"{text} {count}\n")
        counts.clear()

    def label(i: int) -> str | None:
        if i not in shown:
            name = frames[i]
            if name == _SAMPLE_MARK or (
                "!" not in name and _PROCESS_FRAME.fullmatch(name)
            ):
                shown[i] = None
            else:
                shown[i] = _folded_frame(name, index)
        return shown[i]

    for chunk in chunks:
        buffer += chunk
        end = 0
        for token in _TOKEN.finditer(buffer):
            end = token.end()
            if token["frame"] is not None:
                frames.append(json.loads(token["frame"]))
            elif token["unit"] is not None:
                if token["unit"] != "milliseconds":
                    msg = f"unit {token['unit']!r}, not milliseconds"
                    raise SpeedscopeFormatError(msg)
                stack.clear()
                previous = float(token["start"])
            else:
                at = float(token["at"])
                index_ = int(token["index"])
                if index_ >= len(frames):
                    msg = f"frame {index_} was not defined"
                    raise SpeedscopeFormatError(msg)
                micros = round((at - previous) * 1000)
                if micros > 0 and stack:
                    parts = [t for t in map(label, stack) if t is not None]
                    key = ";".join(parts)
                    counts[key] = counts.get(key, 0) + micros
                    total += micros
                    if len(counts) >= FLUSH_STACKS:
                        flush()
                previous = at
                if token["kind"] == "O":
                    stack.append(index_)
                elif not stack or stack.pop() != index_:
                    msg = f"frame {index_} closed but not on top"
                    raise SpeedscopeFormatError(msg)
        buffer = buffer[end:]
    flush()
    return total


def _say(message: str) -> None:
    sys.stderr.write(f"perfproof.profilers.dotnet: {message}\n")


def tool_environment(env: Mapping[str, str]) -> dict[str, str]:
    """Return ``env`` with ``DOTNET_ROOT`` set when it can be found.

    dotnet-trace cannot find a runtime outside its default location
    without ``DOTNET_ROOT``, and the profiling environment drops it.

    Parameters
    ----------
    env : mapping of str to str
        The environment the tool would get.

    Returns
    -------
    dict of str to str
        A copy of ``env``. When it has no ``DOTNET_ROOT`` and a
        ``dotnet`` is on its ``PATH``, ``DOTNET_ROOT`` is the directory
        of that host with symlinks resolved; otherwise it is unchanged.
    """
    result = dict(env)
    if "DOTNET_ROOT" in result:
        return result
    host = shutil.which("dotnet", path=result.get("PATH", ""))
    if host is not None:
        result["DOTNET_ROOT"] = str(Path(host).resolve().parent)
    return result


def run_command(
    command: Sequence[str],
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> tuple[int, bool]:
    """Run a command; return ``(status, timed_out)``.

    On the time limit the command and every descendant are stopped and
    then killed (`perfproof.profilers._kill.freeze_and_kill`), and the
    status is 124.

    Parameters
    ----------
    command : sequence of str
        The argv; stdout and stderr are inherited.
    timeout_seconds : float
        Time limit in seconds.
    env : mapping of str to str, optional
        The child's environment; the current one when omitted.
    """
    process = subprocess.Popen(list(command), env=env)
    try:
        return process.wait(timeout=timeout_seconds), False
    except subprocess.TimeoutExpired:
        freeze_and_kill(process.pid, _say)
        process.wait()
        _say(f"the command ran past {timeout_seconds:g} s and was killed")
        return _EXIT_TIMEOUT, True


def _chunks(path: Path) -> Iterator[str]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        while chunk := handle.read(CHUNK_CHARS):
            yield chunk


def find_dotnet_trace(explicit: Path | None) -> str | None:
    """Return the ``dotnet-trace`` to run, or ``None`` if there is none.

    Parameters
    ----------
    explicit : Path, optional
        A path given on the command line; used as is.

    Notes
    -----
    Looked up on the ``PATH``, then in ``~/.dotnet/tools``, where
    ``dotnet tool install --global dotnet-trace`` puts it.
    """
    if explicit is not None:
        return str(explicit) if explicit.is_file() else None
    found = shutil.which("dotnet-trace")
    if found is not None:
        return found
    candidate = Path.home() / ".dotnet" / "tools" / "dotnet-trace"
    return str(candidate) if candidate.is_file() else None


def record(
    command: Sequence[str],
    output: Path,
    *,
    project_root: Path | None = None,
    source_roots: Sequence[Path] = (),
    dotnet_trace: Path | None = None,
    timeout_seconds: float = 3600.0,
) -> int:
    """Run ``command`` under dotnet-trace and write folded stacks.

    Parameters
    ----------
    command : sequence of str
        The .NET command, as an argv (``dotnet bin/App.dll``).
    output : Path
        Folded stacks are written here; the raw trace and its JSON go
        to ``<output>.dotnet-trace/``.
    project_root : Path, optional
        Root whose ``.cs`` files give frames a file and line; the
        current directory when omitted.
    source_roots : sequence of Path, optional
        Further source directories.
    dotnet_trace : Path, optional
        The ``dotnet-trace`` executable; found on the ``PATH`` or in
        ``~/.dotnet/tools`` when omitted.
    timeout_seconds : float, optional
        Time limit for the command, in seconds.

    Returns
    -------
    int
        The command's exit status when it is non-zero (dotnet-trace
        passes it on), 124 on a timeout, 4 when there is no
        ``dotnet-trace``, 5 when no trace was written (2 from
        dotnet-trace means no .NET runtime connected: the command
        started none, or ``dotnet run`` started the program as a child
        that is not traced), 6 when the trace holds no samples or is not
        the expected layout, 7 when files of an earlier run of the same
        ``output`` are there, else 0. The reason goes to stderr.
    """
    tool = find_dotnet_trace(dotnet_trace)
    if tool is None:
        _say(
            "dotnet-trace was not found on the PATH or in ~/.dotnet/tools; "
            "install it with: dotnet tool install --global dotnet-trace"
        )
        return _EXIT_NO_TOOL
    directory = Path(f"{output}.dotnet-trace")
    if directory.exists() and any(directory.iterdir()):
        _say(f"{directory} has files of an earlier run; use a fresh output")
        return _EXIT_STALE
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory / "trace"
    argv = [
        tool,
        "collect",
        "--format",
        "Speedscope",
        "-o",
        str(raw),
        "--",
        *command,
    ]
    status, timed_out = run_command(
        argv, timeout_seconds, tool_environment(os.environ)
    )
    if timed_out:
        return _EXIT_TIMEOUT
    speedscope = directory / "trace.speedscope.json"
    if not speedscope.is_file():
        _say(
            f"no trace was written to {directory}: the command started no "
            ".NET runtime that dotnet-trace could attach to (dotnet-trace "
            "traces the process it launches; `dotnet run` starts the "
            "program as a child, so profile `dotnet bin/App.dll`)"
        )
        return status or _EXIT_NO_PROFILE
    index = SourceIndex(project_root or Path.cwd(), source_roots)
    try:
        with output.open("w", encoding="utf-8") as out:
            total = fold_speedscope(_chunks(speedscope), index, out)
    except SpeedscopeFormatError as exc:
        _say(f"{speedscope} is not the layout read here: {exc}")
        return _EXIT_EMPTY
    if total == 0:
        _say(
            "the trace holds no samples: the program ran too briefly; run "
            "the workload longer"
        )
        return _EXIT_EMPTY
    return status


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
        prog="python -m perfproof.profilers.dotnet",
        description="Run a .NET command under dotnet-trace.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    rec = sub.add_parser("record", help="run a command under dotnet-trace")
    rec.add_argument("--output", required=True, type=Path)
    rec.add_argument("--project-root", type=Path, default=None)
    rec.add_argument("--source-root", type=Path, action="append", default=[])
    rec.add_argument("--dotnet-trace", type=Path, default=None)
    rec.add_argument("--timeout", type=float, default=3600.0)
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
        project_root=args.project_root,
        source_roots=args.source_root,
        dotnet_trace=args.dotnet_trace,
        timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
