"""Run a profiler on a benchmark and summarise its folded stacks.

`profile_benchmark` runs a spec's benchmark command twice in one tree,
each run on its own:

1. **Unprofiled**, to measure its wall and CPU time as it is. Wall time
   runs from just before the process is spawned to its exit; CPU time
   is user plus system time from ``os.wait4``, summed over every
   thread of the process and the descendants it waited for. CPU time
   well below wall time means the benchmark spent part of its time
   waiting (for I/O, a lock, a sleep, or a CPU on a loaded machine),
   not computing. CPU time above wall time means other threads or
   processes used CPU alongside the main one; a thread pool such as
   OpenBLAS's, which NumPy starts, does this even while idle, because
   its workers spin. Then the gap cannot show waiting.
2. **Profiled**, under one profiler, whose folded stacks
   `perfproof.profilers.folded.summarise_file` ranks.

Both runs get the environment a comparison gives the benchmark,
`perfproof.bench.side_effects.benchmark_environment`, and before
anything runs the `side_effects` gate must pass or be waived, as
"Side effects" in ``docs/method.md`` requires. The spec's reset, when
it has one, runs before each of the two runs. Each run is launched from
an argv list, never through a shell, in its own process group; when it
outlives its timeout the whole group is killed with ``SIGKILL``.

The profiler's raw output and both runs' stdout and stderr are written
to the output directory untouched.

Profilers
---------
``py-spy`` (the default on Linux)
    ``py-spy record --format raw --rate 100 --full-filenames
    --subprocesses -o py-spy.txt -- <command>``. py-spy samples only
    the threads it sees as active (its default, without ``--idle``), so
    a share is of active samples: time a thread spends waiting is not
    in the profile. ``--subprocesses`` follows a launcher such as
    ``sh -c`` into the Python processes it starts, and puts a frame
    ``process <pid>:"<command line>"`` at the root of every stack; a
    sample with no Python frame is that frame's self time. py-spy
    0.4.2 refuses to run on macOS without root
    ("This program requires root on OSX"), so it is refused there
    unless perfproof runs as root. py-spy 0.4.2 exits 0 when the
    command it runs exits non-zero, so whether the profiled run itself
    succeeded is not known; the unprofiled run's exit status is
    checked.
``pyinstrument`` (the default on macOS)
    An in-process sampler that needs no privileges, run as ``uv run
    --no-project --with pyinstrument==5.1.3 --python <interpreter> --
    python -m pyinstrument --renderer pyisession --outfile
    pyinstrument.pyisession <script or -m module> <arguments>``. The
    spec's command must be a Python interpreter followed by a script or
    ``-m module``. uv layers an environment holding pyinstrument over
    the interpreter's own, so the benchmark's packages stay importable;
    uv also puts that environment first on ``PATH`` and sets
    ``VIRTUAL_ENV``, ``UV`` and ``UV_RUN_RECURSION_DEPTH``, so the
    profiled run's environment is not exactly the benchmark's.
    pyinstrument measures the main thread's wall-clock time, waiting
    included. It samples at the first call or return event after each
    1 ms interval and gives that stack all the time since the last
    sample, so a function's share is sound but its line is where the
    event happened, not always the hot line (``examples/membership``'s
    ``in`` test on ``orders.py:53`` shows as line 52).
    `pyinstrument_to_folded` turns its session into folded stacks
    weighted in microseconds.
``command``
    Any other profiler, as an argv template that runs the benchmark
    and emits folded stacks; the playbooks supply one per tool. The
    argument ``{command}`` is replaced by the benchmark's argv (its
    executable resolved to an absolute path) and is required. Every
    ``{output}`` inside an argument is replaced by the path the
    profiler must write its folded stacks to, and at least one is
    required too: the profiled run's stdout also carries whatever the
    benchmark prints, and a printed line such as ``rows 700000`` reads
    as a folded stack. Its basis and weight unit are unknown to
    perfproof.
"""

import contextlib
import json
import math
import os
import posixpath
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, Literal, TextIO, cast

from perfproof.bench.lock import resolve_executable
from perfproof.bench.side_effects import (
    ResetError,
    benchmark_environment,
    run_reset,
    side_effects_gate,
)
from perfproof.profilers.folded import (
    DEFAULT_TOP_N,
    FoldedStackError,
    rank_file,
)
from perfproof.schema import BenchmarkSpec, GateResult, Hotspot

__all__ = [
    "COMMAND_PLACEHOLDER",
    "OUTPUT_PLACEHOLDER",
    "PROFILERS",
    "PYINSTRUMENT_VERSION",
    "PY_SPY_RATE_HZ",
    "Profile",
    "ProfileError",
    "ProfilerName",
    "SessionFormatError",
    "Timing",
    "default_profiler",
    "profile_benchmark",
    "pyinstrument_to_folded",
]

type ProfilerName = Literal["py-spy", "pyinstrument", "command"]
"""How the profiled run is launched; see the module docstring."""
type Basis = Literal["active", "wall", "unknown"]
type WeightUnit = Literal["samples", "microseconds", "unknown"]

PROFILERS: Final[tuple[ProfilerName, ...]] = (
    "py-spy",
    "pyinstrument",
    "command",
)
"""Every value of `ProfilerName`."""
PYINSTRUMENT_VERSION: Final = "5.1.3"
"""The pyinstrument release uv installs for a profiled run."""
PY_SPY_RATE_HZ: Final = 100
"""py-spy's sampling rate, its own default, in samples per second."""
COMMAND_PLACEHOLDER: Final = "{command}"
"""Template argument replaced by the benchmark's argv."""
OUTPUT_PLACEHOLDER: Final = "{output}"
"""Replaced, inside any template argument, by the folded-stack path."""

_VERSION_TIMEOUT_SECONDS: Final = 30.0
_TAIL_BYTES: Final = 4096
_CHUNK_CHARS: Final = 1 << 16
_MICROSECONDS: Final = 1_000_000
_PYTHON_NAME: Final = re.compile(r"python[0-9]*(\.[0-9]+)*")
_SYNTHETIC_FILE: Final = "<pyinstrument>"
"""Pseudo-file of pyinstrument's own frames, ``[await]`` and
``[out-of-context]``, which name no code."""
_THREAD_FILE: Final = "<thread>"
_WHITESPACE: Final = frozenset(" \t\n\r")
_NUMBER_TAIL: Final = frozenset("0123456789.eE+-")

_UNPROFILED_STDOUT: Final = "unprofiled.stdout"
_UNPROFILED_STDERR: Final = "unprofiled.stderr"
_PROFILED_STDOUT: Final = "profiled.stdout"
_PROFILED_STDERR: Final = "profiled.stderr"
_PY_SPY_RAW: Final = "py-spy.txt"
_PYINSTRUMENT_RAW: Final = "pyinstrument.pyisession"
_PYINSTRUMENT_FOLDED: Final = "pyinstrument.folded.txt"
_COMMAND_FOLDED: Final = "command.folded.txt"


class ProfileError(Exception):
    """A profile could not be made; nothing is summarised.

    Parameters
    ----------
    code : str
        Stable snake_case identifier of the failure:
        ``profiler_unavailable`` (the tool is missing or cannot run
        here, or cannot wrap this command), ``invalid_command`` (a
        profiler template without ``{command}`` or ``{output}``),
        ``side_effects`` (the gate refused the run), ``reset_failed``,
        ``workload_failed`` (the unprofiled run could not start, failed
        or timed out), ``profiler_failed`` (the profiled run could not
        start, failed, timed out or wrote no output) or
        ``invalid_profile`` (its output could not be read as a
        profile).
    message : str
        What went wrong; for a failed run, with the tail of its stderr.
    gate : perfproof.schema.GateResult or None, optional
        The failed `side_effects` gate, for ``side_effects`` and
        ``reset_failed``.
    """

    def __init__(
        self, code: str, message: str, gate: GateResult | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.gate = gate


class SessionFormatError(ValueError):
    """A pyinstrument session that cannot be read."""


@dataclass(frozen=True, slots=True)
class Timing:
    """Wall and CPU time of one finished execution.

    Attributes
    ----------
    wall_seconds : float
        From just before the process was spawned to its exit, in
        seconds.
    cpu_seconds : float
        User plus system time summed over every thread of the process
        and of the descendants it waited for, in seconds; it exceeds
        ``wall_seconds`` when other threads used CPU.
    """

    wall_seconds: float
    cpu_seconds: float


@dataclass(frozen=True, slots=True)
class Profile:
    """A summarised profile of one benchmark.

    Attributes
    ----------
    profiler : ProfilerName
        How the profiled run was launched.
    tool_version : str or None
        Version of the profiler that ran: as ``py-spy --version``
        printed it, the pinned `PYINSTRUMENT_VERSION`, or ``None`` when
        unknown (a ``command`` profiler, or a version probe that
        failed).
    basis : {"active", "wall", "unknown"}
        What the shares are shares of: py-spy's samples of active
        threads, pyinstrument's wall-clock time of the main thread, or
        unknown for a ``command`` profiler.
    weight_unit : {"samples", "microseconds", "unknown"}
        Unit of the folded stacks' counts.
    argv : tuple of str
        The profiled run's argv.
    hotspots : tuple of perfproof.schema.Hotspot
        Frames ranked by self share, as
        `perfproof.profilers.folded.summarise` returns them.
    frames_by_total : tuple of perfproof.schema.Hotspot
        Frames ranked by total share, including frames with no self
        time, without synthetic or entry frames, as
        `perfproof.profilers.folded.rank` returns them.
    unprofiled : Timing
        The unprofiled run.
    profiled_wall_seconds : float
        Wall time of the profiled run, profiler start-up included.
    load_average : float or None
        One-minute load average just before the unprofiled run;
        ``None`` if the system gives none.
    cpu_count : int or None
        Logical CPUs of this machine; ``None`` if unknown.
    side_effects : perfproof.schema.GateResult
        The `side_effects` gate, passed or waived.
    raw_path : pathlib.Path
        The profiler's output, untouched.
    folded_path : pathlib.Path
        The folded stacks summarised; ``raw_path`` itself unless the
        output was converted.
    log_paths : tuple of pathlib.Path
        Stdout and stderr of both runs.
    """

    profiler: ProfilerName
    tool_version: str | None
    basis: Basis
    weight_unit: WeightUnit
    argv: tuple[str, ...]
    hotspots: tuple[Hotspot, ...]
    frames_by_total: tuple[Hotspot, ...]
    unprofiled: Timing
    profiled_wall_seconds: float
    load_average: float | None
    cpu_count: int | None
    side_effects: GateResult
    raw_path: Path
    folded_path: Path
    log_paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _Plan:
    """How to launch one profiled run and where its stacks land."""

    argv: tuple[str, ...]
    tool_version: str | None
    basis: Basis
    weight_unit: WeightUnit
    raw_path: Path


@dataclass(frozen=True, slots=True)
class _Exit:
    """How one execution ended."""

    timing: Timing
    exit_code: int
    timed_out: bool


def default_profiler(
    platform: str = sys.platform,
) -> Literal["py-spy", "pyinstrument"]:
    """Return the built-in profiler for a platform.

    Parameters
    ----------
    platform : str, optional
        A `sys.platform` value; this one when omitted.

    Returns
    -------
    {"py-spy", "pyinstrument"}
        py-spy on Linux, pyinstrument on macOS.

    Raises
    ------
    ProfileError
        ``profiler_unavailable`` on any other platform.
    """
    if platform.startswith("linux"):
        return "py-spy"
    if platform == "darwin":
        return "pyinstrument"
    msg = f"perfproof profile runs on Linux and macOS, not {platform}"
    raise ProfileError("profiler_unavailable", msg)


def profile_benchmark(
    spec: BenchmarkSpec,
    tree_root: Path,
    out_dir: Path,
    *,
    profiler: ProfilerName,
    process_env: Mapping[str, str],
    template: Sequence[str] = (),
    timeout_seconds: float | None = None,
    top_n: int = DEFAULT_TOP_N,
    own_modules: Collection[str] = (),
) -> Profile:
    """Run a benchmark unprofiled, then under a profiler, and rank it.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark: its command, working directory, environment,
        reset and, unless ``timeout_seconds`` is given, timeout.
    tree_root : pathlib.Path
        Root of the tree to run in; it is also the project root that
        frames are shown relative to.
    out_dir : pathlib.Path
        Existing directory for the profiler's output and the runs'
        logs. A file perfproof would write that already exists there
        is refused, never overwritten.
    profiler : ProfilerName
        Which profiler to run.
    process_env : Mapping of str to str
        perfproof's own environment, usually ``os.environ``; the runs
        get ``benchmark_environment(spec, process_env)``.
    template : sequence of str, optional
        The argv template of a ``command`` profiler; ignored otherwise.
    timeout_seconds : float, optional
        Time limit for each run, in seconds; the spec's own when
        omitted.
    top_n : int, optional
        Most hotspots to return; at least 1.
    own_modules : collection of str, optional
        Native binaries that are project code, as for
        `perfproof.profilers.folded.summarise`.

    Returns
    -------
    Profile
        The ranked hotspots, both runs' timings and every file written.
        Summarising is linear in the size of the folded stacks, in
        memory proportional to their distinct frames.

    Raises
    ------
    ProfileError
        With one of the codes it documents. Nothing runs when the
        profiler is unavailable, the template is invalid or the
        `side_effects` gate fails.
    ValueError
        If ``top_n`` is less than 1 or ``timeout_seconds`` is not a
        positive finite number.
    """
    if top_n < 1:
        msg = f"top_n must be at least 1, got {top_n}"
        raise ValueError(msg)
    timeout = (
        spec.timeout_seconds if timeout_seconds is None else timeout_seconds
    )
    if not (math.isfinite(timeout) and timeout > 0):
        msg = f"timeout_seconds must be positive and finite, got {timeout}"
        raise ValueError(msg)
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        msg = f"perfproof profile runs on Linux and macOS, not {sys.platform}"
        raise ProfileError("profiler_unavailable", msg)
    env = benchmark_environment(spec, process_env)
    cwd = (tree_root / spec.cwd).absolute()
    if not cwd.is_dir():
        msg = f"working directory {spec.cwd!r} does not exist in {tree_root}"
        raise ProfileError("workload_failed", msg)
    executable = resolve_executable(spec, tree_root, env)
    if executable is None:
        msg = (
            f"{spec.command[0]!r} is not an executable file in {cwd} or on "
            f"PATH {env.get('PATH', os.defpath)!r}"
        )
        raise ProfileError("workload_failed", msg)
    workload = (str(executable), *spec.command[1:])
    plan = _plan(profiler, workload, out_dir, env, template)
    gate = side_effects_gate(spec, tree_root, env)
    if gate.status == "failed":
        msg = f"the side_effects gate refused the run: {gate.detail}"
        raise ProfileError("side_effects", msg, gate)
    load = _load_average()
    _reset(spec, tree_root, process_env)
    plain = _run(
        spec.command,
        str(executable),
        cwd,
        env,
        out_dir / _UNPROFILED_STDOUT,
        out_dir / _UNPROFILED_STDERR,
        timeout,
        "workload_failed",
        "the unprofiled run",
    )
    _reset(spec, tree_root, process_env)
    profiled = _run(
        plan.argv,
        plan.argv[0],
        cwd,
        env,
        out_dir / _PROFILED_STDOUT,
        out_dir / _PROFILED_STDERR,
        timeout,
        "profiler_failed",
        f"the {profiler} run",
    )
    if not plan.raw_path.is_file():
        msg = f"the {profiler} run wrote no output to {plan.raw_path}"
        raise ProfileError("profiler_failed", msg)
    folded = plan.raw_path
    if profiler == "pyinstrument":
        folded = out_dir / _PYINSTRUMENT_FOLDED
        _convert(plan.raw_path, folded, cwd)
    try:
        ranking = rank_file(
            folded,
            project_root=tree_root,
            top_n=top_n,
            own_modules=own_modules,
        )
    except FoldedStackError as exc:
        msg = f"{folded} is not folded stacks: {exc}"
        raise ProfileError("invalid_profile", msg) from exc
    logs = [
        out_dir / name
        for name in (
            _UNPROFILED_STDOUT,
            _UNPROFILED_STDERR,
            _PROFILED_STDOUT,
            _PROFILED_STDERR,
        )
    ]
    return Profile(
        profiler=profiler,
        tool_version=plan.tool_version,
        basis=plan.basis,
        weight_unit=plan.weight_unit,
        argv=plan.argv,
        hotspots=ranking.by_self,
        frames_by_total=ranking.by_total,
        unprofiled=plain,
        profiled_wall_seconds=profiled.wall_seconds,
        load_average=load,
        cpu_count=os.cpu_count(),
        side_effects=gate,
        raw_path=plan.raw_path,
        folded_path=folded,
        log_paths=tuple(logs),
    )


def _plan(
    profiler: ProfilerName,
    workload: tuple[str, ...],
    out_dir: Path,
    env: Mapping[str, str],
    template: Sequence[str],
) -> _Plan:
    """Build the profiled run's argv, or say why it cannot run."""
    path = env.get("PATH", os.defpath)
    if profiler == "py-spy":
        return _py_spy_plan(workload, out_dir, path)
    if profiler == "pyinstrument":
        return _pyinstrument_plan(workload, out_dir, path)
    return _command_plan(workload, out_dir, template, path)


def _py_spy_plan(workload: tuple[str, ...], out_dir: Path, path: str) -> _Plan:
    """Plan a py-spy run of the workload."""
    if sys.platform == "darwin" and os.geteuid() != 0:
        msg = (
            "py-spy needs root on macOS (py-spy 0.4.2: 'This program "
            "requires root on OSX'); use --profiler pyinstrument"
        )
        raise ProfileError("profiler_unavailable", msg)
    found = shutil.which("py-spy", path=path)
    if found is None:
        msg = (
            f"py-spy is not on PATH {path!r}; install it, for example "
            "with `uv tool install py-spy`"
        )
        raise ProfileError("profiler_unavailable", msg)
    py_spy = os.path.abspath(found)
    raw = out_dir / _PY_SPY_RAW
    argv = (
        py_spy,
        "record",
        "--format",
        "raw",
        "--rate",
        str(PY_SPY_RATE_HZ),
        "--full-filenames",
        "--subprocesses",
        "-o",
        str(raw),
        "--",
        *workload,
    )
    return _Plan(argv, _py_spy_version(py_spy), "active", "samples", raw)


def _py_spy_version(py_spy: str) -> str | None:
    """Ask py-spy for its version; ``None`` if it does not say."""
    try:
        done = subprocess.run(
            [py_spy, "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = done.stdout.split()
    if done.returncode != 0 or len(words) != 2 or words[0] != "py-spy":
        return None
    return words[1]


def _pyinstrument_plan(
    workload: tuple[str, ...], out_dir: Path, path: str
) -> _Plan:
    """Plan a pyinstrument run of a Python workload through uv."""
    python, *rest = workload
    if _PYTHON_NAME.fullmatch(Path(python).name) is None:
        msg = (
            f"pyinstrument wraps a Python interpreter, and {python} is not "
            "one; use py-spy or --profiler-command"
        )
        raise ProfileError("profiler_unavailable", msg)
    if rest[:1] == ["-m"] and len(rest) >= 2:
        target = rest[:2]
        arguments = rest[2:]
    elif rest and not rest[0].startswith("-"):
        target = rest[:1]
        arguments = rest[1:]
    else:
        given = rest[0] if rest else "nothing"
        msg = (
            "pyinstrument can wrap `python SCRIPT ...` or `python -m "
            f"MODULE ...`; this command gives the interpreter {given!r}"
        )
        raise ProfileError("profiler_unavailable", msg)
    found = shutil.which("uv", path=path)
    if found is None:
        msg = (
            f"uv is not on PATH {path!r}; perfproof runs pyinstrument with "
            "`uv run --with`"
        )
        raise ProfileError("profiler_unavailable", msg)
    raw = out_dir / _PYINSTRUMENT_RAW
    argv = (
        os.path.abspath(found),
        "run",
        "--no-project",
        "--with",
        f"pyinstrument=={PYINSTRUMENT_VERSION}",
        "--python",
        python,
        "--",
        "python",
        "-m",
        "pyinstrument",
        "--renderer",
        "pyisession",
        "--outfile",
        str(raw),
        *target,
        *arguments,
    )
    return _Plan(argv, PYINSTRUMENT_VERSION, "wall", "microseconds", raw)


def _command_plan(
    workload: tuple[str, ...],
    out_dir: Path,
    template: Sequence[str],
    path: str,
) -> _Plan:
    """Expand a profiler template around the workload.

    The profiler's executable is resolved on the benchmark's ``PATH``,
    relative to perfproof's working directory when it has a slash.
    """
    if COMMAND_PLACEHOLDER not in template:
        msg = (
            f"a profiler command must contain the argument "
            f"{COMMAND_PLACEHOLDER}, where the benchmark's argv goes"
        )
        raise ProfileError("invalid_command", msg)
    if not any(OUTPUT_PLACEHOLDER in arg for arg in template):
        msg = (
            f"a profiler command must contain {OUTPUT_PLACEHOLDER}, the "
            "file it writes its folded stacks to: its stdout also carries "
            "what the benchmark prints, which could read as folded stacks"
        )
        raise ProfileError("invalid_command", msg)
    raw = out_dir / _COMMAND_FOLDED
    argv: list[str] = []
    for arg in template:
        if arg == COMMAND_PLACEHOLDER:
            argv.extend(workload)
            continue
        argv.append(arg.replace(OUTPUT_PLACEHOLDER, str(raw)))
    if template[0] != COMMAND_PLACEHOLDER:
        found = shutil.which(argv[0], path=path)
        if found is None:
            msg = f"{argv[0]!r} is not an executable file or on PATH {path!r}"
            raise ProfileError("profiler_unavailable", msg)
        argv[0] = os.path.abspath(found)
    return _Plan(tuple(argv), None, "unknown", "unknown", raw)


def _load_average() -> float | None:
    """Return the one-minute load average, or ``None``."""
    try:
        return os.getloadavg()[0]
    except OSError:
        return None


def _reset(
    spec: BenchmarkSpec, tree_root: Path, process_env: Mapping[str, str]
) -> None:
    """Run the spec's reset, reporting a failure as a `ProfileError`."""
    try:
        run_reset(spec, tree_root, process_env)
    except ResetError as exc:
        gate: GateResult = exc.gate
        raise ProfileError("reset_failed", gate.detail, gate) from exc


def _run(
    argv: Sequence[str],
    executable: str,
    cwd: Path,
    env: Mapping[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
    code: str,
    what: str,
) -> Timing:
    """Execute one run and fail with ``code`` unless it exits 0."""
    try:
        with (
            stdout_path.open("xb") as stdout,
            stderr_path.open("xb") as stderr,
        ):
            ended = _execute(
                argv, executable, cwd, env, stdout, stderr, timeout_seconds
            )
    except FileExistsError as exc:
        msg = f"{exc.filename} already exists; perfproof never overwrites"
        raise ProfileError(code, msg) from exc
    except OSError as exc:
        msg = f"{what} could not be started: {exc.strerror or exc}"
        raise ProfileError(code, msg) from exc
    if ended.timed_out:
        detail = f"timed out after {timeout_seconds} s and was killed"
    elif ended.exit_code < 0:
        detail = f"was killed by signal {-ended.exit_code}"
    elif ended.exit_code != 0:
        detail = f"exited {ended.exit_code}"
    else:
        return ended.timing
    tail = _tail(stderr_path)
    msg = f"{what} {detail}; stderr ({stderr_path}) ends:\n{tail}"
    raise ProfileError(code, msg.rstrip())


def _execute(
    argv: Sequence[str],
    executable: str,
    cwd: Path,
    env: Mapping[str, str],
    stdout: BinaryIO,
    stderr: BinaryIO,
    timeout_seconds: float,
) -> _Exit:
    """Run a process in its own group until it exits or times out.

    The child is waited for without being reaped first, so its process
    ID, which is also its group's, cannot be reused while a timeout
    may still signal the group.
    """
    start = time.perf_counter_ns()
    process = subprocess.Popen(
        list(argv),
        executable=executable,
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        process_group=0,
    )
    try:
        exited = _wait_unreaped(process.pid, timeout_seconds)
        end = time.perf_counter_ns()
        if not exited:
            _kill_group(process.pid)
        _, status, usage = os.wait4(process.pid, 0)
    except BaseException:
        _kill_group(process.pid)
        with contextlib.suppress(ChildProcessError):
            os.wait4(process.pid, 0)
        process.returncode = -signal.SIGKILL
        raise
    process.returncode = os.waitstatus_to_exitcode(status)
    return _Exit(
        timing=Timing(
            wall_seconds=(end - start) / 1e9,
            cpu_seconds=usage.ru_utime + usage.ru_stime,
        ),
        exit_code=process.returncode,
        timed_out=not exited,
    )


def _wait_unreaped(pid: int, timeout_seconds: float) -> bool:
    """Wait for a child to exit, leaving it unreaped.

    macOS waits on a kqueue ``NOTE_EXIT`` event. Linux waits in
    ``waitid`` with ``WNOWAIT`` while a timer thread kills the group on
    the deadline; Python has no ``waitid`` on macOS.

    Returns
    -------
    bool
        Whether the child exited before the deadline.
    """
    if sys.platform == "darwin":
        queue = select.kqueue()
        try:
            event = select.kevent(
                pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT,
            )
            try:
                return bool(queue.control([event], 1, timeout_seconds))
            except ProcessLookupError:
                return True
        finally:
            queue.close()
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        _kill_group(pid)

    timer = threading.Timer(timeout_seconds, expire)
    timer.daemon = True
    timer.start()
    try:
        os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
    finally:
        timer.cancel()
        timer.join()
    return not expired.is_set()


def _kill_group(pgid: int) -> None:
    """Kill every process in a group that may already be gone."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def _tail(path: Path) -> str:
    """Return the last `_TAIL_BYTES` of a log, decoded leniently."""
    try:
        with path.open("rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - _TAIL_BYTES))
            return f.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return f"(unreadable: {exc.strerror or exc})"


def _convert(session: Path, folded: Path, cwd: Path) -> None:
    """Write a pyinstrument session's folded stacks next to it."""
    try:
        with (
            session.open(encoding="utf-8", errors="replace") as source,
            folded.open("x", encoding="utf-8") as out,
        ):
            pyinstrument_to_folded(source, out, cwd=str(cwd))
    except SessionFormatError as exc:
        msg = f"{session} is not a pyinstrument session: {exc}"
        raise ProfileError("invalid_profile", msg) from exc
    except FileExistsError as exc:
        msg = f"{folded} already exists; perfproof never overwrites"
        raise ProfileError("invalid_profile", msg) from exc


def pyinstrument_to_folded(
    session: TextIO,
    out: TextIO,
    *,
    cwd: str,
    chunk_chars: int = _CHUNK_CHARS,
) -> int:
    r"""Turn a pyinstrument session into folded stacks.

    A session (``--renderer pyisession``) is one JSON object whose
    ``frame_records`` member lists ``[stack, seconds]`` pairs, root
    first. Each pair becomes one folded line whose count is its time in
    whole microseconds, rounded. A frame
    ``function\x00file\x00first_line`` with attributes after ``\x01``
    (``c`` class name, ``l`` current line) becomes
    ``Class.function (file:line)`` in py-spy's form, with the current
    line, or the first line when there is no current one and it is not
    0. A relative file is made absolute against ``cwd``. Pseudo-files
    such as ``<frozen runpy>`` and ``<built-in>`` stay as they are; the
    thread at the root of each stack is ``Name (<thread>)``, its
    identifier dropped, and pyinstrument's own frames without a file
    (``[await]``, ``[out-of-context]``) get the pseudo-file
    ``<pyinstrument>``. A ``;`` in a frame is written ``\x3b`` and a
    line break ``\n``, so they cannot split a frame or a line.

    Parameters
    ----------
    session : TextIO
        The session file, read once, in chunks.
    out : TextIO
        Where the folded lines are written.
    cwd : str
        Working directory of the profiled process, absolute.
    chunk_chars : int, optional
        Characters read at a time; at least 1.

    Returns
    -------
    int
        Number of frame records converted.

    Raises
    ------
    SessionFormatError
        If the input is not one JSON object with exactly one
        ``frame_records`` list of ``[list of str, non-negative finite
        number]`` pairs, or a frame is not in the form above.
    ValueError
        If ``chunk_chars`` is less than 1.

    Notes
    -----
    Linear in the session's size. Memory is bounded by the largest
    single member other than ``frame_records`` plus the distinct
    frames, never by the number of records.
    """
    if chunk_chars < 1:
        msg = f"chunk_chars must be at least 1, got {chunk_chars}"
        raise ValueError(msg)
    reader = _JsonReader(session, chunk_chars)
    frames: dict[str, str] = {}
    records: int | None = None
    reader.take("{")
    if reader.peek() == "}":
        reader.take("}")
    else:
        while True:
            key = reader.value()
            if not isinstance(key, str):
                msg = f"a session key must be a string, got {key!r}"
                raise SessionFormatError(msg)
            reader.take(":")
            if key == "frame_records":
                if records is not None:
                    msg = "frame_records appears more than once"
                    raise SessionFormatError(msg)
                records = _convert_records(reader, out, frames, cwd)
            else:
                reader.value()
            if reader.peek() == ",":
                reader.take(",")
                continue
            reader.take("}")
            break
    if reader.peek() != "":
        msg = "data after the session object"
        raise SessionFormatError(msg)
    if records is None:
        msg = "the session has no frame_records"
        raise SessionFormatError(msg)
    return records


def _convert_records(
    reader: "_JsonReader", out: TextIO, frames: dict[str, str], cwd: str
) -> int:
    """Convert the ``frame_records`` list, one record at a time."""
    reader.take("[")
    if reader.peek() == "]":
        reader.take("]")
        return 0
    count = 0
    while True:
        stack, seconds = _record(reader.value(), count)
        parts: list[str] = []
        for info in stack:
            text = frames.get(info)
            if text is None:
                text = _frame(info, cwd)
                frames[info] = text
            parts.append(text)
        out.write(f"{';'.join(parts)} {round(seconds * _MICROSECONDS)}\n")
        count += 1
        if reader.peek() == ",":
            reader.take(",")
            continue
        reader.take("]")
        return count


def _record(value: object, index: int) -> tuple[list[str], float]:
    """Check one ``[stack, seconds]`` record."""
    record = value
    if isinstance(value, list):
        pair = cast("list[object]", value)
        if len(pair) == 2:
            stack, seconds = pair
            if (
                isinstance(stack, list)
                and all(
                    isinstance(f, str) for f in cast("list[object]", stack)
                )
                and isinstance(seconds, int | float)
                and not isinstance(seconds, bool)
                and math.isfinite(seconds)
                and seconds >= 0
            ):
                return cast("list[str]", stack), float(seconds)
    msg = (
        f"frame record {index} is not [list of frames, non-negative "
        f"seconds]: {repr(record)[:200]}"
    )
    raise SessionFormatError(msg)


def _frame(info: str, cwd: str) -> str:
    """Write one pyinstrument frame in py-spy's form."""
    identifier, _, attributes = info.partition("\x01")
    parts = identifier.split("\x00")
    if len(parts) == 1:
        return _escape(f"{identifier} ({_SYNTHETIC_FILE})")
    if len(parts) != 3:
        msg = f"frame {info!r} is not function, file and line"
        raise SessionFormatError(msg)
    function, path, first = parts
    if path == _THREAD_FILE:
        return _escape(f"{function} ({_THREAD_FILE})")
    class_name: str | None = None
    current: str | None = None
    for attribute in attributes.split("\x01") if attributes else ():
        if attribute.startswith("c"):
            class_name = attribute[1:]
        elif attribute.startswith("l"):
            current = attribute[1:]
    line = _line(current if current is not None else first, info)
    name = f"{class_name}.{function}" if class_name else function
    if not path:
        path = _SYNTHETIC_FILE
    elif not (path.startswith("<") and path.endswith(">")):
        path = posixpath.normpath(posixpath.join(cwd, path))
    location = path if line is None else f"{path}:{line}"
    return _escape(f"{name} ({location})")


def _line(text: str, info: str) -> int | None:
    """Read a frame's line number; 0 means none."""
    if not (text.isascii() and text.isdigit()):
        msg = f"frame {info!r} has line number {text!r}"
        raise SessionFormatError(msg)
    return int(text) or None


def _escape(frame: str) -> str:
    """Keep a frame from splitting a folded stack or line."""
    return (
        frame.replace(";", "\\x3b").replace("\n", "\\n").replace("\r", "\\r")
    )


def _reject_constant(name: str) -> object:
    """Refuse JSON's non-standard ``NaN`` and ``Infinity``."""
    msg = f"{name} is not a JSON number"
    raise SessionFormatError(msg)


class _JsonReader:
    """Read JSON values one at a time from a stream of text.

    Only what the caller asks for is decoded; a value is kept in memory
    only while it is decoded. A value that ends at the end of the
    buffered text, or right before a character that could continue a
    number (a digit, ``.``, ``e``, ``E``, ``+`` or ``-``), is decoded
    again once more text is read, because a number cut by a chunk
    boundary (``0.00`` of ``0.0012``, or ``17`` of ``17.5``) would
    otherwise decode as a shorter, valid number.
    """

    def __init__(self, stream: TextIO, chunk_chars: int) -> None:
        self._stream = stream
        self._chunk = chunk_chars
        self._text = ""
        self._pos = 0
        self._eof = False
        self._decoder = json.JSONDecoder(parse_constant=_reject_constant)

    def _read(self) -> bool:
        """Append more text, at least as much as is buffered."""
        if self._eof:
            return False
        pending = len(self._text) - self._pos
        data = self._stream.read(max(self._chunk, pending))
        if not data:
            self._eof = True
            return False
        self._text = self._text[self._pos :] + data
        self._pos = 0
        return True

    def peek(self) -> str:
        """Return the next character after whitespace; ``""`` at end."""
        while True:
            text, pos = self._text, self._pos
            while pos < len(text) and text[pos] in _WHITESPACE:
                pos += 1
            self._pos = pos
            if pos < len(text):
                return text[pos]
            if not self._read():
                return ""

    def take(self, char: str) -> None:
        """Consume one structural character, or fail."""
        found = self.peek()
        if found != char:
            shown = repr(found) if found else "the end of the input"
            msg = f"expected {char!r}, found {shown}"
            raise SessionFormatError(msg)
        self._pos += 1

    def value(self) -> object:
        """Decode the next JSON value."""
        self.peek()
        while True:
            try:
                value, end = self._decoder.raw_decode(self._text, self._pos)
            except json.JSONDecodeError as exc:
                if self._read():
                    continue
                msg = f"invalid JSON: {exc.msg}"
                raise SessionFormatError(msg) from exc
            cut = end == len(self._text) or self._text[end] in _NUMBER_TAIL
            if cut and self._read():
                continue
            self._pos = end
            return cast("object", value)
