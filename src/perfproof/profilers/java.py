"""Profile a JVM benchmark with Flight Recorder and write folded stacks.

``perfproof profile --profiler-command`` runs one argv and reads the
folded stacks it writes to ``{output}``. A JDK's profiler writes its own
format and its frames carry a class and a line but no file, so this
module is the one process that bridges them::

    python -m perfproof.profilers.java record --output {output}
        -- {command}

It launches the benchmark with Java Flight Recorder switched on through
``JAVA_TOOL_OPTIONS`` (which every JVM the command starts reads, so
``java -jar`` and ``java -cp`` are the forms it has been run with; a
build tool that starts a JVM passes the variable on, but that has not
been run here), waits for it, prints the recorded execution samples with
the JDK's own ``jfr print`` and converts them to folded stacks in the
form `perfproof.profilers.folded` reads::

    pkg.Class.method (src/main/java/pkg/Class.java:42);...  3

A class found in the project's source tree is shown with its path
relative to the project root, so it counts as project code. Any other
class (the JDK, a dependency jar) is shown by its package path, such as
``java/util/HashMap.java:635``, and counts as library code.

A share is a share of the samples of Java threads that were running
Java code, taken at a fixed period per thread. Time in garbage
collection, in native code and while blocked is not in it; the
playbook says how to look at those with their own events.

It has been run on JDK 21 only. The sampling period is passed as a
Flight Recorder event setting on the command line, which an older JDK
may reject. The raw ``.jfr`` recordings stay beside the
output, untouched.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Final, TextIO

__all__ = [
    "JfrFormatError",
    "SourceIndex",
    "fold_jfr_text",
    "main",
    "record",
    "recording_options",
    "run_command",
    "source_path",
]

DEFAULT_INTERVAL_MS: Final = 2
"""Sampling period per thread, in milliseconds."""

DEFAULT_ASYNC_EVENT: Final = "cpu"
"""async-profiler event: CPU time, by ``perf_events`` where the kernel
allows it and by a CPU timer where it does not (as in a container)."""

DEFAULT_STACK_DEPTH: Final = 128
"""Deepest stack recorded and printed."""

_FRAME: Final = re.compile(
    r"(?P<qual>.+?)\.(?P<method><init>|<clinit>|[^.(]+)"
    r"\((?P<params>.*)\)\s+line:\s*(?P<line>-?[0-9]+)\s*"
)
_HIDDEN_SUFFIX: Final = re.compile(r"/0x[0-9a-fA-F]+")
_NATIVE_LIBRARY: Final = re.compile(r".+\.(so(\.[0-9]+)*|dylib|dll)")
_SKIPPED_DIRS: Final = frozenset(
    {"target", "build", "out", "node_modules", "__pycache__"}
)
_SETTING: Final = re.compile(r"[A-Za-z0-9_.]+#[A-Za-z0-9_]+=[A-Za-z0-9_.]+")
_UNKNOWN_FRAME: Final = "[unknown]"
_FLUSH_BYTES: Final = 32 * 1024 * 1024
"""Estimated bytes of distinct stacks held before they are written."""
_MEMO_FRAMES: Final = 20_000
"""Distinct frame texts remembered before the memo is dropped."""
_STACK_OVERHEAD: Final = 120
"""Bytes a held stack costs besides its text (entry, str header)."""
_SAMPLE_START: Final = "jdk.ExecutionSample {"
_STACK_START: Final = "stackTrace = ["
_PRINT_TIMEOUT_S: Final = 1800.0
_OPTIONS_VARIABLE: Final = "JAVA_TOOL_OPTIONS"

_EXIT_NO_TOOL: Final = 4
_EXIT_NO_RECORDING: Final = 5
_EXIT_BAD_RECORDING: Final = 6
_EXIT_STALE: Final = 7
_EXIT_TIMEOUT: Final = 124


class JfrFormatError(ValueError):
    """``jfr print`` output that cannot be read as execution samples.

    Parameters
    ----------
    reason : str
        What is wrong.
    line_number : int
        1-based input line.
    """

    def __init__(self, reason: str, line_number: int) -> None:
        self.line_number = line_number
        super().__init__(f"line {line_number}: {reason}")


class SourceIndex:
    """Find the source file of a class under a project.

    Parameters
    ----------
    project_root : pathlib.Path
        Root that located paths are relative to.
    source_roots : sequence of pathlib.Path, optional
        Directories to search; the project root when empty. Hidden
        directories and ``target``, ``build``, ``out`` and
        ``node_modules`` are not entered.

    Notes
    -----
    The tree is walked once, on the first lookup: ``O(files)`` time and
    memory. When several files fit a class (an Android and a JRE copy
    of the same source, say) the one under a ``main`` directory wins,
    then the shortest path, then the first alphabetically.
    """

    def __init__(
        self, project_root: Path, source_roots: Sequence[Path] = ()
    ) -> None:
        self._root = project_root
        self._roots = tuple(source_roots) or (project_root,)
        self._by_name: dict[str, list[str]] | None = None
        self._cache: dict[str, str | None] = {}

    def locate(self, class_name: str) -> str | None:
        """Return the project-relative path of a class's source.

        Parameters
        ----------
        class_name : str
            Binary class name with dots, as ``jfr print`` writes it,
            such as ``org.example.Outer$Inner``.

        Returns
        -------
        str or None
            Posix path relative to the project root, or ``None`` when
            no ``.java`` file under the source roots has the class's
            package path and outer class name.
        """
        if class_name in self._cache:
            return self._cache[class_name]
        found = self._lookup(class_name)
        self._cache[class_name] = found
        return found

    def _lookup(self, class_name: str) -> str | None:
        suffix = source_path(class_name)
        by_name = self._index()
        candidates = [
            rel
            for rel in by_name.get(posix_basename(suffix), ())
            if rel == suffix or rel.endswith("/" + suffix)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda rel: ("/main/" not in rel, len(rel), rel))
        return candidates[0]

    def _index(self) -> dict[str, list[str]]:
        if self._by_name is None:
            index: dict[str, list[str]] = {}
            for root in self._roots:
                for directory, names, files in os.walk(root):
                    names[:] = [
                        n
                        for n in names
                        if not n.startswith(".") and n not in _SKIPPED_DIRS
                    ]
                    for name in files:
                        if not name.endswith(".java"):
                            continue
                        full = Path(directory) / name
                        try:
                            rel = full.relative_to(self._root).as_posix()
                        except ValueError:
                            rel = full.as_posix()
                        index.setdefault(name, []).append(rel)
            self._by_name = index
        return self._by_name


def posix_basename(path: str) -> str:
    """Return the last component of a ``/``-separated path."""
    return path.rpartition("/")[2]


def source_path(class_name: str) -> str:
    """Return the ``.java`` path a binary class name implies.

    Parameters
    ----------
    class_name : str
        Binary class name with dots; nested classes (``Outer$Inner``)
        and hidden or lambda classes (``Outer$$Lambda/0x1234``) belong
        to their outer class's file.

    Returns
    -------
    str
        Package path and outer class name with a ``.java`` suffix.
    """
    outer = _HIDDEN_SUFFIX.sub("", class_name).split("$", 1)[0]
    return outer.replace(".", "/") + ".java"


def _frames(lines: Iterable[str]) -> Iterator[list[str] | None]:
    """Yield the frames of each execution sample.

    ``frames`` is leaf first, as ``jfr print`` writes it, or ``None``
    when the sample has no stack trace.
    """
    in_sample = False
    in_stack = False
    frames: list[str] | None = None
    number = 0
    for raw in lines:
        number += 1
        text = raw.strip()
        if in_stack:
            if text == "]":
                in_stack = False
            elif text and text != "...":
                if frames is None:
                    frames = []
                frames.append(text)
            continue
        if text == _SAMPLE_START:
            in_sample = True
            frames = None
        elif in_sample and text == _STACK_START:
            in_stack = True
            frames = []
        elif in_sample and text == "}":
            in_sample = False
            yield frames
            frames = None
    if in_sample or in_stack:
        raise JfrFormatError("input ends inside a sample", number)


def _folded_frame(text: str, index: SourceIndex) -> str:
    """Turn one ``jfr print`` frame line into a folded frame."""
    match = _FRAME.fullmatch(text)
    if match is None:
        return _opaque_frame(text)
    qualified = _HIDDEN_SUFFIX.sub("", match["qual"])
    if _NATIVE_LIBRARY.fullmatch(qualified):
        return f"{qualified}`{match['method']}"
    function = f"{qualified}.{match['method']}"
    path = index.locate(qualified) or source_path(qualified)
    line = max(int(match["line"]), 0)
    return f"{function} ({path}:{line})"


def _opaque_frame(text: str) -> str:
    """Show a frame of no known shape as a native symbol.

    async-profiler writes stub frames with no class (``.arraycopy()``),
    which no field of the ``Class.method(params) line: n`` form fits.
    Folded stacks cannot carry ``;`` or `` (`` inside a frame.
    """
    name = re.sub(r"\s+line:\s*-?[0-9]+\s*$", "", text)
    name = name.lstrip(".").replace(";", ":").replace(" (", "(").strip()
    return name or _UNKNOWN_FRAME


def fold_jfr_text(
    lines: Iterable[str],
    index: SourceIndex,
    out: TextIO,
    flush_bytes: int = _FLUSH_BYTES,
) -> int:
    """Convert printed JFR execution samples to folded stacks.

    Parameters
    ----------
    lines : iterable of str
        The printed events, read once and in order.
    index : SourceIndex
        Where to find each class's source file.
    out : TextIO
        Receives folded stacks, root first, frames joined by ``;``,
        then a space and a count; equal stacks are merged within each
        batch of about ``flush_bytes`` of distinct stacks, so a stack
        may appear on more than one line. A sample with no stack trace
        is written as a line with an empty stack, which counts toward
        the total and toward no frame.
    flush_bytes : int, optional
        Estimated bytes of distinct stacks held before a batch is
        written; at least 1.

    Returns
    -------
    int
        Number of samples read.

    Raises
    ------
    JfrFormatError
        If the input ends inside a sample.

    Notes
    -----
    Time is linear in the input. Memory is bounded by ``flush_bytes``
    (the text of the held stacks plus a fixed overhead for each), plus
    the source index, plus a memo of up to ``_MEMO_FRAMES`` frame texts
    (a count, not a byte budget; it is dropped when full), however deep
    the stacks are.
    """
    counts: dict[str, int] = {}
    folded: dict[str, str] = {}
    held = 0
    samples = 0
    for leaf_first in _frames(lines):
        samples += 1
        if leaf_first is None:
            stack = ""
        else:
            if len(folded) >= _MEMO_FRAMES:
                folded.clear()
            for frame in leaf_first:
                if frame not in folded:
                    folded[frame] = _folded_frame(frame, index)
            stack = ";".join(folded[frame] for frame in reversed(leaf_first))
        if stack in counts:
            counts[stack] += 1
        else:
            counts[stack] = 1
            held += len(stack) + _STACK_OVERHEAD
            if held >= flush_bytes:
                _write(counts, out)
                held = 0
    _write(counts, out)
    return samples


def _write(counts: dict[str, int], out: TextIO) -> None:
    for stack, count in counts.items():
        out.write(f"{stack} {count}\n")
    counts.clear()


def _jfr_executable(command: Sequence[str], env: dict[str, str]) -> str | None:
    """Find ``jfr`` in the JDK the command runs, else on the PATH."""
    path = env.get("PATH", os.defpath)
    java = shutil.which(command[0], path=path)
    homes: list[Path] = []
    if java is not None and Path(java).name == "java":
        homes.append(Path(java).resolve().parent)
    if env.get("JAVA_HOME"):
        homes.append(Path(env["JAVA_HOME"]) / "bin")
    for home in homes:
        candidate = home / "jfr"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("jfr", path=path)


def recording_options(
    output: Path,
    interval_ms: int,
    stack_depth: int,
    async_lib: Path | None = None,
    async_event: str = DEFAULT_ASYNC_EVENT,
    jfr_settings: Sequence[str] = (),
) -> str:
    """Return the ``JAVA_TOOL_OPTIONS`` text that starts a recording.

    With ``async_lib`` the recorder is async-profiler's agent writing
    a JFR file, whose execution samples cover every thread, native and
    JIT-compiler frames included; without it, the JDK's own Flight
    Recorder.

    Parameters
    ----------
    output : pathlib.Path
        The folded-stack path; recordings go to ``<output>.<pid>.jfr``.
    interval_ms : int
        Sampling period per thread, in milliseconds.
    stack_depth : int
        Deepest stack kept.
    async_lib : pathlib.Path, optional
        async-profiler's library; Flight Recorder when omitted.
    async_event : str, optional
        async-profiler event.
    jfr_settings : sequence of str, optional
        Extra ``event#key=value`` Flight Recorder settings.

    Returns
    -------
    str
        Options for ``JAVA_TOOL_OPTIONS``, separated by spaces.

    Notes
    -----
    ``-XX:+DebugNonSafepoints`` is on because without it the JVM can
    only name a line at a safepoint, and a compiled loop's samples all
    land on one line (seen once on one loop, with and without the
    flag; that output was not kept). It adds debug information to
    compiled code and nothing else.
    """
    debug = "-XX:+UnlockDiagnosticVMOptions -XX:+DebugNonSafepoints "
    if async_lib is not None:
        return (
            f"{debug}-agentpath:{async_lib}=start,event={async_event},"
            f"interval={interval_ms}ms,jstackdepth={stack_depth},"
            f"file={output}.%p.jfr,jfr"
        )
    extra = "".join(f",{setting}" for setting in jfr_settings)
    return (
        f"{debug}"
        f"-XX:FlightRecorderOptions:stackdepth={stack_depth} "
        f"-XX:StartFlightRecording:filename={output}.%p.jfr,"
        "settings=profile,dumponexit=true,"
        f"jdk.ExecutionSample#period={interval_ms}ms{extra}"
    )


def record(
    command: Sequence[str],
    output: Path,
    *,
    interval_ms: int = DEFAULT_INTERVAL_MS,
    stack_depth: int = DEFAULT_STACK_DEPTH,
    project_root: Path | None = None,
    source_roots: Sequence[Path] = (),
    timeout_seconds: float = 3600.0,
    async_lib: Path | None = None,
    async_event: str = DEFAULT_ASYNC_EVENT,
    jfr_settings: Sequence[str] = (),
) -> int:
    """Run a command under a JVM profiler and write folded stacks.

    Parameters
    ----------
    command : sequence of str
        The benchmark's argv. Its stdout and stderr are inherited.
    output : pathlib.Path
        Folded stacks are written here. Recordings are written beside
        it as ``<output>.<pid>.jfr``, one per JVM the command starts.
    interval_ms : int, optional
        Sampling period per thread, in milliseconds, at least 1.
    stack_depth : int, optional
        Deepest stack kept, at least 1.
    project_root : pathlib.Path, optional
        Root that frames are shown relative to; the working directory
        when omitted.
    source_roots : sequence of pathlib.Path, optional
        Where to look for the project's ``.java`` files; the project
        root when empty.
    timeout_seconds : float, optional
        Time limit for the command, in seconds; its process group is
        killed with ``SIGKILL`` when it is exceeded.
    async_lib : pathlib.Path, optional
        async-profiler's ``libasyncProfiler`` for this operating system
        and architecture. When given, async-profiler records instead of
        Flight Recorder; the JDK's ``jfr`` still prints the recording.
    async_event : str, optional
        async-profiler event, such as ``cpu``, ``itimer`` or ``wall``;
        ignored without ``async_lib``.
    jfr_settings : sequence of str, optional
        Extra Flight Recorder event settings, each ``event#key=value``
        such as ``jdk.JavaMonitorEnter#threshold=0ms``, so that the
        raw recording holds events the profile does not use; ignored
        with ``async_lib``.

    Returns
    -------
    int
        The command's exit status when it is non-zero, 124 on a
        timeout, 4 when no ``jfr`` is found, 5 when no recording was
        written, 6 when a recording cannot be read or holds no
        execution samples, 7 when recordings from an earlier run of the
        same ``output`` exist, else 0. The reason goes to stderr.

    Raises
    ------
    ValueError
        If ``interval_ms`` or ``stack_depth`` is less than 1, or a
        setting is not of the form ``event#key=value``.
    """
    if interval_ms < 1 or stack_depth < 1:
        msg = "interval_ms and stack_depth must be at least 1"
        raise ValueError(msg)
    for setting in jfr_settings:
        if _SETTING.fullmatch(setting) is None:
            msg = f"{setting!r} is not of the form event#key=value"
            raise ValueError(msg)
    for path in (output, async_lib):
        if path is not None and any(c.isspace() for c in str(path)):
            _say(
                f"{path} contains whitespace, which JAVA_TOOL_OPTIONS "
                "cannot carry; use a directory without spaces"
            )
            return _EXIT_NO_TOOL
    pattern = glob.escape(str(output)) + ".*.jfr"
    if glob.glob(pattern):
        _say(
            f"{output}.<pid>.jfr recordings from an earlier run are there; "
            "use a fresh output path"
        )
        return _EXIT_STALE
    if async_lib is not None and not async_lib.is_file():
        _say(f"async-profiler library {async_lib} does not exist")
        return _EXIT_NO_TOOL
    env = dict(os.environ)
    jfr = _jfr_executable(command, env)
    if jfr is None:
        _say(
            "jfr was not found next to the command's java, in JAVA_HOME or "
            "on the PATH; Flight Recorder needs a JDK, not a JRE"
        )
        return _EXIT_NO_TOOL
    options = recording_options(
        output, interval_ms, stack_depth, async_lib, async_event, jfr_settings
    )
    existing = env.get(_OPTIONS_VARIABLE)
    env[_OPTIONS_VARIABLE] = f"{existing} {options}" if existing else options
    status, timed_out = run_command(command, env, timeout_seconds)
    if timed_out:
        return _EXIT_TIMEOUT
    recordings = sorted(glob.glob(pattern))
    if not recordings:
        _say(
            f"no recording was written to {output}.<pid>.jfr: the command "
            "started no JVM, or the JVM was killed before it could dump"
        )
        return status or _EXIT_NO_RECORDING
    root = project_root or Path.cwd()
    index = SourceIndex(root, source_roots)
    samples = 0
    with output.open("w", encoding="utf-8") as out:
        for recording in recordings:
            code, count = _convert(
                jfr, Path(recording), index, out, stack_depth
            )
            if code:
                return code
            samples += count
    if samples == 0:
        _say(
            "the recordings hold no execution samples: the JVM ran too "
            "briefly, or only waited; run the workload longer"
        )
        return _EXIT_BAD_RECORDING
    return status


def _say(message: str) -> None:
    sys.stderr.write(f"perfproof.profilers.java: {message}\n")


def _descendants(pid: int) -> list[int] | None:
    """Return the pids of all descendants of a process, via ``ps``.

    Returns ``None`` when ``ps`` cannot be run or does not finish.
    """
    try:
        done = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid="],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    children: dict[int, list[int]] = {}
    for row in done.stdout.splitlines():
        fields = row.split()
        if len(fields) == 2 and all(f.isdigit() for f in fields):
            children.setdefault(int(fields[1]), []).append(int(fields[0]))
    found: list[int] = []
    stack = [pid]
    while stack:
        for child in children.get(stack.pop(), ()):
            found.append(child)
            stack.append(child)
    return found


def _signal(pid: int, signum: int) -> bool:
    """Send a signal; say whether the process was there to get it."""
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        return False
    return True


def _freeze_and_kill(pid: int) -> None:
    """Stop a process and all its descendants, then kill them all.

    A live parent can fork after a snapshot of its descendants, so the
    tree is frozen first: the process is stopped (``SIGSTOP``), then
    the descendants are snapshotted and stopped again and again until
    a snapshot shows no process that is not already stopped. Only then
    is every stopped process killed, so nothing can fork in between.
    If ``ps`` fails only the process itself is killed, and that is said
    on stderr.
    """
    stopped = [pid] if _signal(pid, signal.SIGSTOP) else []
    while stopped:
        found = _descendants(pid)
        if found is None:
            _say("ps failed, so only the command itself is killed")
            break
        fresh = [p for p in found if p not in stopped]
        if not fresh:
            break
        stopped.extend(p for p in fresh if _signal(p, signal.SIGSTOP))
    for victim in stopped:
        _signal(victim, signal.SIGKILL)
    _signal(pid, signal.SIGKILL)


def run_command(
    command: Sequence[str], env: dict[str, str], timeout_seconds: float
) -> tuple[int, bool]:
    """Run a command; return ``(status, timed_out)``.

    The command stays in the wrapper's own process group, so a kill of
    that group by the caller reaches it. On the wrapper's own timeout
    the command and every descendant it started are killed with
    ``SIGKILL`` after the whole tree has been stopped, so a process
    that keeps forking cannot get away. Descendants are found through
    ``ps``: if ``ps`` fails or times out, that is said on stderr and
    only the direct child is killed. A descendant that has already
    reparented to init cannot be found.

    Parameters
    ----------
    command : sequence of str
        The argv; stdout and stderr are inherited.
    env : dict of str to str
        The environment.
    timeout_seconds : float
        Time limit in seconds.

    Returns
    -------
    tuple of (int, bool)
        The exit status, and whether the limit was exceeded (then the
        status is 124).
    """
    process = subprocess.Popen(list(command), env=env)
    try:
        return process.wait(timeout=timeout_seconds), False
    except subprocess.TimeoutExpired:
        _freeze_and_kill(process.pid)
        process.wait()
        _say(f"the command ran past {timeout_seconds:g} s and was killed")
        return _EXIT_TIMEOUT, True


def _convert(
    jfr: str, recording: Path, index: SourceIndex, out: TextIO, depth: int
) -> tuple[int, int]:
    """Fold one recording; return ``(exit code, samples)``."""
    argv = [
        jfr,
        "print",
        "--events",
        "jdk.ExecutionSample",
        "--stack-depth",
        str(depth),
        str(recording),
    ]
    with (
        tempfile.TemporaryFile(
            mode="w+", dir=recording.parent, encoding="utf-8"
        ) as errors,
        subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=errors, text=True
        ) as printer,
    ):
        stream = printer.stdout
        if stream is None:
            return _EXIT_BAD_RECORDING, 0
        timer = threading.Timer(_PRINT_TIMEOUT_S, printer.kill)
        timer.start()
        try:
            samples = fold_jfr_text(stream, index, out)
            printer.wait()
        except JfrFormatError as exc:
            printer.kill()
            _say(f"{recording}: {exc}")
            return _EXIT_BAD_RECORDING, 0
        finally:
            timer.cancel()
        if printer.returncode != 0:
            errors.seek(0)
            detail = errors.read().strip()[:300]
            _say(
                f"jfr print of {recording} failed or ran past "
                f"{_PRINT_TIMEOUT_S:g} s: {detail}"
            )
            return _EXIT_BAD_RECORDING, 0
    return 0, samples


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
        prog="python -m perfproof.profilers.java",
        description="Run a JVM command under Flight Recorder.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    rec = sub.add_parser("record", help="run a command under a profiler")
    rec.add_argument("--output", required=True, type=Path)
    rec.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS)
    rec.add_argument("--stack-depth", type=int, default=DEFAULT_STACK_DEPTH)
    rec.add_argument("--project-root", type=Path, default=None)
    rec.add_argument("--source-root", type=Path, action="append", default=[])
    rec.add_argument("--timeout", type=float, default=3600.0)
    rec.add_argument("--async-lib", type=Path, default=None)
    rec.add_argument("--async-event", default=DEFAULT_ASYNC_EVENT)
    rec.add_argument("--jfr-setting", action="append", default=[])
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
        interval_ms=args.interval_ms,
        stack_depth=args.stack_depth,
        project_root=args.project_root,
        source_roots=args.source_root,
        timeout_seconds=args.timeout,
        async_lib=args.async_lib,
        async_event=args.async_event,
        jfr_settings=args.jfr_setting,
    )


if __name__ == "__main__":
    sys.exit(main())
