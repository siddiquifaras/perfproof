"""Launch benchmark executions and measure them.

A `Runner` executes one `perfproof.schema.BenchmarkSpec` in the
baseline and candidate trees: the spec's warmup executions first, then
rounds of one baseline and one candidate execution each, in an order
drawn from a recorded seed. Every execution is one child process
launched from an argv list, never through a shell.

What is measured
----------------
``wall``
    ``time.perf_counter_ns`` read just before the child is spawned and
    just after the kernel reports its exit, so it includes process
    creation and startup.
``cpu``
    User plus system time from ``os.wait4``. Both macOS and Linux
    include descendants the child waited for, so a wrapper script that
    runs the real work in a grandchild is counted in full.
``reported``
    The value of the one ``PERFPROOF {"value": <number>}`` line the
    child prints on stdout, read back from the stdout file after the
    child exits, one bounded line at a time. Wall time is recorded
    alongside it, as ``docs/method.md`` requires.
``counter``
    The event count from ``perf stat -x, -e <event>``, Linux only. On
    any other system, or when ``perf`` is missing or cannot count the
    event, the runner refuses to start with the reason; it never makes
    up a value.

Peak resident set size comes from ``os.wait4``'s ``ru_maxrss``, which
is in bytes on macOS and in KiB on Linux; it is normalized to bytes.
On Linux the value is also floored by this process's own resident
memory: the child starts as a copy of (or sharing) the launching
process's address space, and the kernel keeps that address space's
high-water mark when the child calls ``exec``. A Linux value no larger
than this process's own peak (``getrusage(RUSAGE_SELF)``) therefore
cannot be told apart from the floor, so it is ``None`` with the reason
in `Execution.max_rss_missing`. A larger value is the child's own.

What is recorded
----------------
Each execution's `Launch` holds the argv the process was given, the
resolved executable, the absolute working directory, the spec's
declared environment and the `Sample.command_hash` over them. The
executable is resolved before launch with
`perfproof.bench.lock.resolve_executable` (``PATH`` from the child's
environment, relative paths from the child's working directory) and
passed to ``exec`` by absolute path, so the recorded executable is the
one that ran; ``argv[0]`` stays as the spec wrote it. The hash is
`perfproof.bench.spec.command_hash`, with the executable and working
directory spelled by `perfproof.bench.spec.normalize_launch_path`, so
it is exactly what the `procedure` gate expects and the same command
in two trees hashes the same. Under
``perf stat`` the hash covers the benchmark command only, and the
benchmark sees its absolute executable path as ``argv[0]``.

Stdout and stderr go to files in the log directory, never into memory.
Log files are created exclusively: a name that already exists is
refused rather than overwritten.

Failed executions
-----------------
An execution, warmup or measured, that can't be started from its tree,
exits non-zero, is killed by a signal, times out or gives no valid
value raises `ExecutionError` before any `Sample` is built, as "Failed
executions" in ``docs/method.md`` requires: its time never becomes a
sample, and a value it printed before failing is never read. The error
carries the arm, round, cause, exit status and log paths; what the
failure means for the comparison is the caller's decision.

A fault in perfproof's own process is blamed on neither version and
raises a plain `RunnerError` with no arm: running out of file
descriptors or processes while starting the child (any ``Popen``
error that is not about the program itself), failing to create a log
file, or a child reaped by someone else in this process (a SIGCHLD
handler, or SIGCHLD ignored), whose result is then unknown. ``launch``
is kept for the tree's own faults: the working directory or program
can't be found from the tree, or ``exec`` fails on the program with
an error that points at it (ENOENT, ENOEXEC, EACCES, ENOTDIR, ELOOP,
EISDIR, ETXTBSY or ENAMETOOLONG: a bad interpreter line, a missing or
non-executable file). Every other ``exec`` error, such as E2BIG from
an oversized environment, ENOMEM, ENFILE, EMFILE or EAGAIN, is a fault
of the spec or the host and raises the plain `RunnerError`.

Waiting for exit
----------------
macOS waits on a kqueue ``NOTE_EXIT`` event and Linux on a pidfd.
Where ``pidfd_open`` is missing (Linux before 5.3, some emulators) a
SIGCHLD handler and wakeup fd stand in, on the main thread only; any
SIGCHLD that arrives meanwhile is raised again for the caller's own
handler once it is restored.

What is not done
----------------
Nothing runs between rounds except parsing the finished execution's
output and building its `Sample`; there are no threads and no
polling. Garbage collection is disabled for the timed region of each
execution, so a collection in this process never lands inside a
measurement. Progress reporting belongs to the caller, which gets
control after each completed round from `Runner.rounds`, outside any
timed region.

On a timeout the child's whole process group is killed with
``SIGKILL``. Descendants that leave the group (``setsid``) are out of
reach.
"""

import errno
import functools
import gc
import json
import math
import os
import resource
import select
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import (
    Callable,
    Generator,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import BinaryIO, Final, Literal, NoReturn, cast

import numpy as np

from perfproof.bench.lock import resolve_executable
from perfproof.bench.spec import command_hash, normalize_launch_path
from perfproof.schema import Arm, BenchmarkSpec, Sample

__all__ = [
    "MAX_TIMEOUT_SECONDS",
    "Execution",
    "ExecutionError",
    "FailureCause",
    "Launch",
    "MetricUnavailableError",
    "Round",
    "Runner",
    "RunnerError",
    "counter_unavailable_reason",
    "round_orders",
]

_ARMS: Final[tuple[Arm, Arm]] = ("baseline", "candidate")
_PREFIX: Final = b"PERFPROOF "
_MAX_LINE: Final = 4096
_SKIP_CHUNK: Final = 1 << 16
_PROBE_TIMEOUT_SECONDS: Final = 30.0
_PROGRAM_ERRNOS: Final = frozenset(
    {
        errno.ENOENT,
        errno.ENOEXEC,
        errno.EACCES,
        errno.ENOTDIR,
        errno.ELOOP,
        errno.EISDIR,
        errno.ETXTBSY,
        errno.ENAMETOOLONG,
    }
)
"""``execve`` errors that point at the program in the tree. CPython puts
the executable in ``filename`` for every ``execve`` error, so E2BIG,
ENOMEM, ENFILE, EMFILE and EAGAIN carry it too, though they are faults
of the spec's environment or the host, not of the tree."""
MAX_TIMEOUT_SECONDS: Final = 2_147_483.0
"""Longest execution timeout the runner accepts, in seconds (about 24.8
days). ``poll`` takes its timeout as a C ``int`` of milliseconds, and
the same limit applies on every platform so a spec behaves the same
everywhere."""


class RunnerError(Exception):
    """The runner could not run or measure an execution at all.

    Raised directly, with no arm, when the fault is not the tree's: a
    fault in perfproof's own process such as running out of file
    descriptors or processes, the SIGCHLD fallback refusing to run, or
    a child reaped by someone else in this process. The comparison
    stops and blames neither version. `ExecutionError`, a subclass, is
    the per-arm failure.
    """


class MetricUnavailableError(RunnerError):
    """The spec's metric cannot be measured on this machine.

    Parameters
    ----------
    reason : str
        Why, in words fit for the report.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        """Why the metric is unavailable."""


FailureCause = Literal["launch", "exit", "signal", "timeout", "output"]
"""Why an execution gave no sample: the program could not be started
from its tree (its working directory or program can't be found there,
or ``exec`` failed with an error that points at the program, such as
a bad interpreter line; see the module docstring), a non-zero
exit status, death by a signal, the spec's timeout, or output that
gives no valid value."""


class ExecutionError(RunnerError):
    """An execution failed and gives no sample.

    Raised for every failed execution, warmup or measured, before any
    `perfproof.schema.Sample` is built, as "Failed executions" in
    ``docs/method.md`` requires. What a failure means for the
    comparison (a candidate failure is a correctness REJECT, a baseline
    failure stops with no verdict) is the caller's decision.

    Parameters
    ----------
    reason : str
        What went wrong.
    cause : {"launch", "exit", "signal", "timeout", "output"}
        The kind of failure; see `FailureCause`.
    arm : {"baseline", "candidate"}
        Which tree ran.
    label : str
        The execution's label, such as ``"round-3"`` or ``"warmup-0"``.
    round : int or None
        Round index; ``None`` for a warmup execution.
    stdout, stderr : Path
        The execution's output files.
    exit_code : int or None
        Exit status; negative for termination by a signal, ``-9`` after
        a timeout kill. ``None`` for ``launch``: the process never
        started.
    wall_seconds : float or None
        Wall-clock time until exit, in seconds; ``None`` for
        ``launch``.
    """

    def __init__(
        self,
        reason: str,
        *,
        cause: FailureCause,
        arm: Arm,
        label: str,
        round: int | None,  # noqa: A002
        stdout: Path,
        stderr: Path,
        exit_code: int | None,
        wall_seconds: float | None,
    ) -> None:
        super().__init__(
            f"{label} {arm}: {reason} (stdout: {stdout}, stderr: {stderr})"
        )
        self.reason = reason
        """What went wrong."""
        self.cause: FailureCause = cause
        """The kind of failure."""
        self.arm: Arm = arm
        """Which tree ran."""
        self.label = label
        """The execution's label."""
        self.round = round
        """Round index; ``None`` for a warmup execution."""
        self.stdout = stdout
        """Stdout file."""
        self.stderr = stderr
        """Stderr file."""
        self.exit_code = exit_code
        """Exit status; negative for a signal; ``None`` if unknown."""
        self.wall_seconds = wall_seconds
        """Wall-clock time until exit, in s; ``None`` if unknown."""


class _BadOutputError(Exception):
    """The child's output does not give a valid metric value."""


class _LaunchError(Exception):
    """``Popen`` could not start the process; carries the OSError."""


class _ReapedError(Exception):
    """The child was reaped before the runner could collect it."""


@dataclass(frozen=True, slots=True)
class Launch:
    """What was launched for one execution.

    Attributes
    ----------
    argv : tuple of str
        The argv the process was given: the spec's command as written,
        or for a ``counter`` metric the ``perf stat`` wrapper followed
        by the absolute executable and the command's arguments.
    executable : str
        Absolute, normalized path of the benchmark executable, resolved
        before launch. Symlinks are not followed, so a virtualenv's
        ``python`` stays itself.
    cwd : str
        Absolute working directory.
    env : Mapping of str to str
        The spec's declared environment variables. The child also
        inherits the runner's base environment, which is not recorded
        here.
    hashed_executable : str
        ``executable`` as the hash spells it: relative to the tree root
        when inside it, else absolute.
    command_hash : str
        SHA-256 hex digest of the spec's argv, declared environment,
        working directory relative to the tree and
        ``hashed_executable``.
    """

    argv: tuple[str, ...]
    executable: str
    cwd: str
    env: Mapping[str, str]
    hashed_executable: str
    command_hash: str


@dataclass(frozen=True, slots=True)
class Execution:
    """One successful execution, warmup or round.

    Only an execution that exited with status 0 in time and gave a
    valid value exists as an `Execution`; any other raises
    `ExecutionError`.

    Attributes
    ----------
    arm : {"baseline", "candidate"}
        Which tree ran.
    label : str
        ``"warmup-<i>"`` or ``"round-<i>"``.
    launch : Launch
        What was launched.
    value : float
        The spec's metric, in `perfproof.schema.MetricSpec.unit`;
        positive.
    wall_seconds : float
        Wall-clock time of the whole command, in seconds.
    cpu_seconds : float
        User plus system CPU time of the child and its waited-for
        descendants, in seconds.
    max_rss_bytes : int or None
        Peak resident set size of the child or its largest waited-for
        descendant, in bytes; ``None`` when it cannot be told apart
        from the Linux floor described in the module docstring.
    max_rss_missing : str or None
        Why ``max_rss_bytes`` is ``None``; ``None`` when it has a value.
    stdout, stderr : Path
        Output files.
    metric_output : Path or None
        ``perf stat`` output file for a ``counter`` metric; ``None``
        otherwise.
    """

    arm: Arm
    label: str
    launch: Launch
    value: float
    wall_seconds: float
    cpu_seconds: float
    max_rss_bytes: int | None
    max_rss_missing: str | None
    stdout: Path
    stderr: Path
    metric_output: Path | None


@dataclass(frozen=True, slots=True)
class Round:
    """One round: one baseline and one candidate execution.

    Attributes
    ----------
    index : int
        Round index, from 0.
    executions : tuple of Execution
        The two executions in the order they ran.
    samples : tuple of perfproof.schema.Sample
        Their samples, in the same order, so ``samples[i].position``
        is ``i``.
    """

    index: int
    executions: tuple[Execution, Execution]
    samples: tuple[Sample, Sample]


@dataclass(frozen=True, slots=True)
class _Exit:
    """Raw result of one timed child process."""

    wall_ns: int
    status: int
    usage: resource.struct_rusage
    timed_out: bool


def round_orders(seed: int, rounds: int) -> tuple[Arm, ...]:
    """Return which version runs first in each round.

    Round ``i`` runs ``round_orders(seed, n)[i]`` first, for any
    ``n > i``, so a resumed run repeats the order of the rounds it
    already has. The bits are the top bit of each raw PCG64 output,
    which NumPy keeps stable across versions for a given seed.

    Parameters
    ----------
    seed : int
        Order seed, recorded as `perfproof.schema.RunRecord.order_seed`;
        non-negative.
    rounds : int
        Number of rounds; non-negative.

    Returns
    -------
    tuple of {"baseline", "candidate"}
        The first version of each round.

    Raises
    ------
    ValueError
        If ``seed`` or ``rounds`` is not a non-negative int.
    """
    if type(seed) is not int or seed < 0:
        msg = f"order seed must be a non-negative int, not {seed!r}"
        raise ValueError(msg)
    if type(rounds) is not int or rounds < 0:
        msg = f"rounds must be a non-negative int, not {rounds!r}"
        raise ValueError(msg)
    raw = np.random.PCG64(seed).random_raw(rounds)
    bits = cast("list[int]", (raw >> np.uint64(63)).tolist())
    return tuple(_ARMS[bit] for bit in bits)


def counter_unavailable_reason(
    event: str, *, env: Mapping[str, str] | None = None
) -> str | None:
    """Say why a ``counter`` metric cannot be measured here, if so.

    Runs ``perf stat`` once on a trivial command and checks that it
    reports ``event``. Takes up to 30 seconds.

    Parameters
    ----------
    event : str
        ``perf`` event name, such as ``"instructions"``.
    env : Mapping of str to str, optional
        Environment whose ``PATH`` locates ``perf``; defaults to this
        process's environment.

    Returns
    -------
    str or None
        The reason, or ``None`` when ``perf stat`` counted ``event``.
    """
    if sys.platform != "linux":
        return (
            "counter metrics use perf stat, which is Linux-only; this "
            f"host is {sys.platform}"
        )
    environ = dict(os.environ if env is None else env)
    perf = shutil.which("perf", path=environ.get("PATH", os.defpath))
    if perf is None:
        return "perf is not on PATH"
    argv = [perf, "stat", "-x,", "-e", event, "--", sys.executable, "-c", ""]
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=environ,
        process_group=0,
    )
    try:
        _, err = proc.communicate(timeout=_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        proc.communicate()
        return f"perf stat did not finish in {_PROBE_TIMEOUT_SECONDS} s"
    text = err.decode("utf-8", "replace")
    if proc.returncode != 0:
        return f"perf stat exited {proc.returncode}: {text[-500:].strip()}"
    try:
        _counter_value(text.splitlines(), event)
    except _BadOutputError as exc:
        return str(exc)
    return None


class Runner:
    """Run a spec's warmup and rounds in both trees.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark. Its command, working directory, declared
        environment, metric, warmup, round budget and timeout are used.
    trees : Mapping of {"baseline", "candidate"} to Path
        Root directory of each version's tree.
    log_dir : Path
        Directory for each execution's stdout, stderr and ``perf``
        output; created if missing.
    order_seed : int
        Seed for the in-round order; non-negative.
    base_env : Mapping of str to str, optional
        Environment every child inherits before the spec's declared
        variables are applied on top. Defaults to a copy of this
        process's environment, taken now.

    Raises
    ------
    ValueError
        If a tree is missing or not a directory, the seed is invalid,
        or the spec's timeout is not finite or exceeds
        `MAX_TIMEOUT_SECONDS`.
    MetricUnavailableError
        If the metric is ``counter`` and ``perf stat`` cannot count its
        event here.
    RunnerError
        If this operating system is neither macOS nor Linux.
    """

    def __init__(
        self,
        spec: BenchmarkSpec,
        trees: Mapping[Arm, Path],
        log_dir: Path,
        *,
        order_seed: int,
        base_env: Mapping[str, str] | None = None,
    ) -> None:
        if sys.platform not in ("darwin", "linux"):
            msg = f"the runner supports macOS and Linux, not {sys.platform}"
            raise RunnerError(msg)
        missing = [arm for arm in _ARMS if arm not in trees]
        if missing:
            msg = f"trees needs both versions; missing {missing}"
            raise ValueError(msg)
        self._trees: dict[Arm, Path] = {}
        for arm in _ARMS:
            root = Path(trees[arm]).resolve()
            if not root.is_dir():
                msg = f"the {arm} tree {root} is not a directory"
                raise ValueError(msg)
            self._trees[arm] = root
        _check_timeout(spec.timeout_seconds)
        self._spec = spec
        self._order_seed = order_seed
        self._orders = round_orders(order_seed, spec.budget.max_rounds)
        environ = os.environ if base_env is None else base_env
        self._env = {**environ, **spec.env}
        self._wrapper: tuple[str, ...] = ()
        event = spec.metric.event
        if spec.metric.kind == "counter" and event is not None:
            reason = counter_unavailable_reason(event, env=self._env)
            if reason is not None:
                raise MetricUnavailableError(reason)
            perf = shutil.which("perf", path=self._env.get("PATH", os.defpath))
            if perf is None:
                raise MetricUnavailableError("perf is not on PATH")
            self._wrapper = (
                os.path.normpath(os.path.abspath(perf)),
                "stat",
                "-x,",
                "-e",
                event,
            )
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def spec(self) -> BenchmarkSpec:
        """The spec being run."""
        return self._spec

    @property
    def order_seed(self) -> int:
        """Seed the in-round order is drawn from."""
        return self._order_seed

    @property
    def orders(self) -> tuple[Arm, ...]:
        """First version of each round, one per round in the budget."""
        return self._orders

    def warmup(self) -> tuple[Execution, ...]:
        """Run the spec's warmup executions and return them.

        Runs ``spec.warmup`` pairs, baseline then candidate. Their
        values are checked like any other but never become samples.

        Returns
        -------
        tuple of Execution
            The executions in the order they ran.

        Raises
        ------
        ExecutionError
            If an execution can't be started from its tree
            (``launch``: its working directory or program is missing
            there, or ``exec`` fails with an error that points at the
            program, such as ENOENT, ENOEXEC or EACCES), exits
            non-zero, is killed by a signal, times out or gives no
            valid value.
        RunnerError
            With no arm, when the fault is not the tree's: a fault in
            perfproof's own process or the host, such as running out
            of file descriptors or processes; an ``exec`` error that
            does not point at the program, such as E2BIG from an
            oversized environment; the SIGCHLD fallback refusing to
            run; or a child reaped by someone else in this process.
        """
        return tuple(
            self._execute(arm, f"warmup-{i}", None)
            for i in range(self._spec.warmup)
            for arm in _ARMS
        )

    def run_round(self, index: int) -> Round:
        """Run round ``index`` in its seeded order.

        Parameters
        ----------
        index : int
            Round index, from 0 to ``spec.budget.max_rounds - 1``.

        Returns
        -------
        Round
            The two executions and their samples.

        Raises
        ------
        ValueError
            If ``index`` is outside the budget.
        ExecutionError
            If an execution can't be started from its tree
            (``launch``: its working directory or program is missing
            there, or ``exec`` fails with an error that points at the
            program, such as ENOENT, ENOEXEC or EACCES), exits
            non-zero, is killed by a signal, times out or gives no
            valid value. The round then has
            no samples, even if its other execution succeeded.
        RunnerError
            With no arm, when the fault is not the tree's: a fault in
            perfproof's own process or the host, such as running out
            of file descriptors or processes; an ``exec`` error that
            does not point at the program, such as E2BIG from an
            oversized environment; the SIGCHLD fallback refusing to
            run; or a child reaped by someone else in this process.
        """
        if type(index) is not int or not 0 <= index < len(self._orders):
            msg = f"round {index!r} is outside 0 to {len(self._orders) - 1}"
            raise ValueError(msg)
        first = self._orders[index]
        second: Arm = "candidate" if first == "baseline" else "baseline"
        label = f"round-{index}"
        executions = (
            self._execute(first, label, index),
            self._execute(second, label, index),
        )
        a, b = (
            Sample(
                version=ex.arm,
                round=index,
                position=position,
                value=ex.value,
                wall_seconds=ex.wall_seconds,
                cpu_seconds=ex.cpu_seconds,
                max_rss_bytes=ex.max_rss_bytes,
                exit_code=0,
                command_hash=ex.launch.command_hash,
            )
            for position, ex in enumerate(executions)
        )
        return Round(index=index, executions=executions, samples=(a, b))

    def rounds(
        self, start: int = 0, stop: int | None = None
    ) -> Iterator[Round]:
        """Run rounds ``start`` to ``stop - 1``, yielding each one.

        The caller gets control between rounds, outside any timed
        region, which is where looks and progress writing belong. Work
        done there runs before the next round starts, so it should be
        small.

        Parameters
        ----------
        start : int, default 0
            First round index.
        stop : int, optional
            One past the last round; defaults to the budget's
            ``max_rounds``.

        Yields
        ------
        Round
            Each round once both its executions finished.

        Raises
        ------
        ValueError
            If the range is outside the budget.
        ExecutionError
            If an execution can't be started from its tree
            (``launch``: its working directory or program is missing
            there, or ``exec`` fails with an error that points at the
            program, such as ENOENT, ENOEXEC or EACCES), exits
            non-zero, is killed by a signal, times out or gives no
            valid value.
        RunnerError
            With no arm, when the fault is not the tree's: a fault in
            perfproof's own process or the host, such as running out
            of file descriptors or processes; an ``exec`` error that
            does not point at the program, such as E2BIG from an
            oversized environment; the SIGCHLD fallback refusing to
            run; or a child reaped by someone else in this process.
        """
        end = len(self._orders) if stop is None else stop
        if not 0 <= start <= end <= len(self._orders):
            msg = (
                f"rounds {start} to {end} are outside 0 to {len(self._orders)}"
            )
            raise ValueError(msg)
        for index in range(start, end):
            yield self.run_round(index)

    def _execute(
        self, arm: Arm, label: str, round_index: int | None
    ) -> Execution:
        """Launch, time and parse one execution of ``arm``.

        Every failure is checked before the value is read, so a value
        printed before a non-zero exit is never used.
        """
        spec = self._spec
        stdout = self._log_dir / f"{label}.{arm}.stdout"
        stderr = self._log_dir / f"{label}.{arm}.stderr"
        metric_output = (
            self._log_dir / f"{label}.{arm}.perf.csv"
            if self._wrapper
            else None
        )
        if metric_output is not None and metric_output.exists():
            _refuse_existing(metric_output)

        def fail(
            reason: str,
            cause: FailureCause,
            exit_code: int | None = None,
            wall: float | None = None,
        ) -> NoReturn:
            raise ExecutionError(
                reason,
                cause=cause,
                arm=arm,
                label=label,
                round=round_index,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                wall_seconds=wall,
            )

        harness = f"{label} {arm}: a fault in perfproof's own process"
        program = ""
        try:
            with _new_log(stdout) as out, _new_log(stderr) as err:
                launch = self._prepare(arm, metric_output, fail)
                program = (
                    self._wrapper[0] if self._wrapper else launch.executable
                )
                done = _run_timed(
                    launch.argv,
                    program,
                    launch.cwd,
                    self._env,
                    out,
                    err,
                    spec.timeout_seconds,
                )
        except _LaunchError as exc:
            cause = exc.__cause__
            if (
                isinstance(cause, OSError)
                and cause.filename == program
                and cause.errno in _PROGRAM_ERRNOS
            ):
                fail(f"could not start {program}: {cause}", "launch")
            msg = (
                f"{harness}, not the {arm} tree, kept {program} from "
                f"starting: {cause}"
            )
            raise RunnerError(msg) from cause
        except _ReapedError:
            msg = (
                f"{harness}: the child was reaped by someone else (a "
                "SIGCHLD handler, or SIGCHLD ignored); its exit status "
                "and resource use are unknown"
            )
            raise RunnerError(msg) from None
        except OSError as exc:
            msg = f"{harness} stopped the execution: {exc}"
            raise RunnerError(msg) from exc
        wall = done.wall_ns / 1e9
        cpu = done.usage.ru_utime + done.usage.ru_stime
        rss, rss_missing = _rss_bytes(done.usage.ru_maxrss)
        exit_code = os.waitstatus_to_exitcode(done.status)

        if done.timed_out:
            fail(
                f"timed out after {spec.timeout_seconds} s; its process "
                "group was killed",
                "timeout",
                exit_code,
                wall,
            )
        if exit_code < 0:
            fail(
                f"killed by {_signal_name(-exit_code)}",
                "signal",
                exit_code,
                wall,
            )
        if exit_code != 0:
            fail(f"exited with status {exit_code}", "exit", exit_code, wall)
        try:
            value = self._value(wall, cpu, stdout, metric_output)
        except _BadOutputError as exc:
            fail(str(exc), "output", exit_code, wall)
        return Execution(
            arm=arm,
            label=label,
            launch=launch,
            value=value,
            wall_seconds=wall,
            cpu_seconds=cpu,
            max_rss_bytes=rss,
            max_rss_missing=rss_missing,
            stdout=stdout,
            stderr=stderr,
            metric_output=metric_output,
        )

    def _prepare(
        self,
        arm: Arm,
        metric_output: Path | None,
        fail: Callable[[str, FailureCause], NoReturn],
    ) -> Launch:
        """Find the working directory and program in ``arm``'s tree.

        Either one missing is a ``launch`` failure of that arm, as
        "Failed executions" in ``docs/method.md`` requires: the program
        can't be started from its tree.
        """
        spec = self._spec
        tree = self._trees[arm]
        cwd = os.path.normpath(tree / spec.cwd)
        if not os.path.isdir(cwd):
            fail(
                f"working directory {spec.cwd!r} does not exist in the "
                f"{arm} tree {tree}",
                "launch",
            )
        found = resolve_executable(spec, tree, self._env)
        if found is None:
            fail(
                f"{spec.command[0]!r} is not an executable file in {cwd} "
                f"or on PATH {self._env.get('PATH', os.defpath)!r}",
                "launch",
            )
        exe = str(found)
        argv: tuple[str, ...] = spec.command
        if metric_output is not None:
            argv = (
                *self._wrapper,
                "-o",
                str(metric_output),
                "--",
                exe,
                *spec.command[1:],
            )
        hashed_exe = normalize_launch_path(found, tree)
        return Launch(
            argv=argv,
            executable=exe,
            cwd=cwd,
            env=spec.env,
            hashed_executable=hashed_exe,
            command_hash=command_hash(
                spec.command,
                spec.env,
                normalize_launch_path(Path(cwd), tree),
                hashed_exe,
            ),
        )

    def _value(
        self,
        wall: float,
        cpu: float,
        stdout: Path,
        metric_output: Path | None,
    ) -> float:
        """Pick or parse the metric of one finished execution."""
        metric = self._spec.metric
        match metric.kind:
            case "wall":
                value = wall
            case "cpu":
                value = cpu
            case "reported":
                return _reported_value(stdout)
            case "counter":
                if metric_output is None or metric.event is None:
                    msg = "counter metric run without perf stat"
                    raise _BadOutputError(msg)
                if not metric_output.is_file():
                    msg = f"perf stat wrote no output to {metric_output}"
                    raise _BadOutputError(msg)
                with metric_output.open(encoding="utf-8") as f:
                    return _counter_value(f, metric.event)
        if value <= 0:
            msg = (
                f"{metric.kind} time {value} s is not positive; the "
                "command ran below the clock's resolution"
            )
            raise _BadOutputError(msg)
        return value


def _run_timed(
    argv: Sequence[str],
    program: str,
    cwd: str,
    env: Mapping[str, str],
    stdout: BinaryIO,
    stderr: BinaryIO,
    timeout_seconds: float,
) -> _Exit:
    """Exec ``program`` with ``argv`` in its own process group and wait.

    The timed region spans the spawn and the wait. On timeout or on any
    exception while waiting, the process group is killed and the child
    reaped, so no child outlives this call.

    Raises
    ------
    ValueError
        If the timeout is not finite or exceeds `MAX_TIMEOUT_SECONDS`.
    _LaunchError
        If the process could not be started; the ``OSError`` is its
        ``__cause__``.
    _ReapedError
        If the child was reaped by someone else in this process.
    """
    _check_timeout(timeout_seconds)
    deadline_offset = math.ceil(timeout_seconds * 1e9)
    with _exit_waiter() as wait_for_exit:
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            start = time.perf_counter_ns()
            try:
                proc = subprocess.Popen(
                    argv,
                    executable=program,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    process_group=0,
                )
            except OSError as exc:
                raise _LaunchError from exc
            try:
                exited = wait_for_exit(proc.pid, start + deadline_offset)
                end = time.perf_counter_ns()
                if not exited:
                    _kill_group(proc.pid)
                try:
                    _, status, usage = os.wait4(proc.pid, 0)
                except ChildProcessError:
                    raise _ReapedError from None
            except BaseException:
                _kill_group(proc.pid)
                try:
                    _, status, _ = os.wait4(proc.pid, 0)
                except ChildProcessError:
                    status = 0
                proc.returncode = os.waitstatus_to_exitcode(status)
                raise
            proc.returncode = os.waitstatus_to_exitcode(status)
        finally:
            if gc_was_enabled:
                gc.enable()
    return _Exit(
        wall_ns=end - start, status=status, usage=usage, timed_out=not exited
    )


@functools.cache
def _pidfd_available() -> bool:
    """Whether ``pidfd_open`` works here.

    It needs Linux 5.3 or later, and some user-mode emulators (amd64
    containers on Apple silicon, for one) report ``ENOSYS`` even on a
    newer kernel.
    """
    if sys.platform != "linux":
        return False
    try:
        fd = os.pidfd_open(os.getpid())
    except OSError as exc:
        if exc.errno in (errno.ENOSYS, errno.EPERM):
            return False
        raise
    os.close(fd)
    return True


@contextmanager
def _exit_waiter() -> Generator[Callable[[int, int], bool]]:
    """Yield the exit wait for this platform, set up outside timing.

    Without ``pidfd_open`` on Linux, a SIGCHLD handler and wakeup fd are
    installed for the duration, then the previous ones are restored.
    The handler is installed before the child is spawned, so an exit
    can never be missed. If any SIGCHLD arrived meanwhile, it is raised
    again once the previous handler is back, so a caller's own children
    are not lost to it. Python allows this only on the main thread.

    Raises
    ------
    RunnerError
        If the fallback is needed off the main thread, or the current
        SIGCHLD handler was not installed from Python and so could not
        be restored afterwards.
    """
    if sys.platform != "linux" or _pidfd_available():
        yield _wait_for_exit
        return
    if signal.getsignal(signal.SIGCHLD) is None:
        msg = (
            "pidfd_open is unavailable here, and the SIGCHLD fallback "
            "would replace a handler installed outside Python that it "
            "cannot restore"
        )
        raise RunnerError(msg)
    arrived: list[int] = []

    def note_sigchld(signum: int, frame: FrameType | None) -> None:
        arrived.append(signum)

    read_fd, write_fd = os.pipe()
    try:
        os.set_blocking(read_fd, False)
        os.set_blocking(write_fd, False)
        try:
            old_handler = signal.signal(signal.SIGCHLD, note_sigchld)
        except ValueError as exc:
            msg = (
                "pidfd_open is unavailable here, and the SIGCHLD fallback "
                "must run on the main thread"
            )
            raise RunnerError(msg) from exc
        try:
            old_fd = signal.set_wakeup_fd(write_fd, warn_on_full_buffer=False)
            try:
                yield functools.partial(_wait_for_sigchld, read_fd)
            finally:
                signal.set_wakeup_fd(old_fd)
        finally:
            signal.signal(signal.SIGCHLD, old_handler)
            if arrived:
                signal.raise_signal(signal.SIGCHLD)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def _wait_for_sigchld(wakeup_fd: int, pid: int, deadline_ns: int) -> bool:
    """Block on SIGCHLD wakeups until ``pid`` exits or the deadline.

    Any child's exit wakes the wait, so each wakeup checks ``pid`` with
    ``waitid(WNOWAIT)``, which leaves it unreaped. ``poll`` rather than
    ``select`` keeps working for descriptors above 1023.

    Returns
    -------
    bool
        True if the child exited (or is already gone), False if the
        deadline passed first.
    """
    poller = select.poll()
    poller.register(wakeup_fd, select.POLLIN)
    while True:
        if sys.platform == "linux":
            flags = os.WEXITED | os.WNOHANG | os.WNOWAIT
            try:
                if os.waitid(os.P_PID, pid, flags) is not None:
                    return True
            except ChildProcessError:
                return True
        remaining_ms = math.ceil((deadline_ns - time.perf_counter_ns()) / 1e6)
        if remaining_ms <= 0:
            return False
        if poller.poll(remaining_ms):
            with suppress(BlockingIOError):
                while os.read(wakeup_fd, 512):
                    pass


def _wait_for_exit(pid: int, deadline_ns: int) -> bool:
    """Block until ``pid`` exits or the deadline passes; no polling.

    Uses a kqueue ``NOTE_EXIT`` event on macOS and a pidfd on Linux, so
    the wakeup comes from the kernel at exit. The child is left
    unreaped.

    Returns
    -------
    bool
        True if the child exited, False if the deadline passed first.
    """
    remaining = max(0.0, (deadline_ns - time.perf_counter_ns()) / 1e9)
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
                return bool(queue.control([event], 1, remaining))
            except ProcessLookupError:
                return True
        finally:
            queue.close()
    elif sys.platform == "linux":
        try:
            fd = os.pidfd_open(pid)
        except ProcessLookupError:
            return True
        try:
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            return bool(poller.poll(math.ceil(remaining * 1000)))
        finally:
            os.close(fd)
    else:
        msg = f"the runner supports macOS and Linux, not {sys.platform}"
        raise RunnerError(msg)


def _check_timeout(seconds: float) -> None:
    """Refuse a timeout the waits cannot represent, ``inf`` included."""
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_TIMEOUT_SECONDS:
        msg = (
            f"timeout {seconds!r} s must be finite, positive and at most "
            f"{MAX_TIMEOUT_SECONDS} s"
        )
        raise ValueError(msg)


def _signal_name(number: int) -> str:
    """Name a signal, such as ``"SIGABRT (6)"``, or give its number."""
    try:
        return f"{signal.Signals(number).name} ({number})"
    except ValueError:
        return f"signal {number}"


def _kill_group(pgid: int) -> None:
    """Send SIGKILL to a process group that may already be gone."""
    with suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)


def _rss_bytes(maxrss: int) -> tuple[int | None, str | None]:
    """Convert a child's ``ru_maxrss`` to bytes, or say why it can't.

    macOS reports bytes. Linux reports KiB, floored by this process's
    own resident memory (see the module docstring), so a Linux value
    at or below this process's peak is unknown.
    """
    if sys.platform == "linux":
        own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if maxrss <= own:
            return None, (
                f"Linux ru_maxrss {maxrss * 1024} bytes does not exceed "
                f"the launching process's own peak of {own * 1024} bytes, "
                "which the kernel counts for the child too"
            )
        return maxrss * 1024, None
    return maxrss, None


def _refuse_existing(path: Path) -> NoReturn:
    """Refuse to overwrite an earlier execution's log."""
    msg = f"log file {path} exists; logs are never overwritten"
    raise RunnerError(msg)


@contextmanager
def _new_log(path: Path) -> Generator[BinaryIO]:
    """Create ``path`` exclusively and yield it open for writing."""
    try:
        f = path.open("xb")
    except FileExistsError:
        _refuse_existing(path)
    with f:
        yield f


def _line_heads(f: BinaryIO) -> Iterator[tuple[bytes, bool]]:
    """Yield each line's first ``_MAX_LINE`` bytes and if it was longer.

    Memory stays bounded however long the lines are: the rest of an
    overlong line is skipped in fixed-size chunks.
    """
    while True:
        head = f.readline(_MAX_LINE + 1)
        if not head:
            return
        if head.endswith(b"\n") or len(head) <= _MAX_LINE:
            yield head.removesuffix(b"\n").removesuffix(b"\r"), False
            continue
        while True:
            rest = f.readline(_SKIP_CHUNK)
            if not rest or rest.endswith(b"\n"):
                break
        yield head[:_MAX_LINE], True


def _refuse_constant(name: str) -> NoReturn:
    """Reject ``NaN`` and ``Infinity``, which JSON does not allow."""
    msg = f"{name} is not a JSON number"
    raise ValueError(msg)


class _RepeatedKeyError(ValueError):
    """A JSON object names the same key twice."""


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object, refusing repeated keys.

    ``json`` keeps the last of repeated keys, so ``{"value": 1,
    "value": 2}`` would silently report 2.
    """
    record = dict(pairs)
    if len(record) != len(pairs):
        keys = [key for key, _ in pairs]
        msg = f"repeated key in {keys}"
        raise _RepeatedKeyError(msg)
    return record


def _reported_value(path: Path) -> float:
    """Read the one ``PERFPROOF {"value": ...}`` line from stdout."""
    found = 0
    line = b""
    with path.open("rb") as f:
        for head, overlong in _line_heads(f):
            if not head.startswith(_PREFIX):
                continue
            if overlong:
                msg = f"a PERFPROOF line is longer than {_MAX_LINE} bytes"
                raise _BadOutputError(msg)
            found += 1
            if found == 1:
                line = head
    if found != 1:
        msg = (
            "no PERFPROOF line on stdout"
            if found == 0
            else f"{found} PERFPROOF lines on stdout; an execution "
            "reports one value"
        )
        raise _BadOutputError(msg)
    body = line.removeprefix(_PREFIX)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        msg = f"PERFPROOF line is not UTF-8: {body[:80]!r}"
        raise _BadOutputError(msg) from None
    try:
        parsed: object = json.loads(
            text,
            parse_constant=_refuse_constant,
            object_pairs_hook=_unique_keys,
        )
    except _RepeatedKeyError:
        msg = f"PERFPROOF line repeats a key: {text[:80]!r}"
        raise _BadOutputError(msg) from None
    except ValueError:
        msg = f"PERFPROOF line is not valid JSON: {text[:80]!r}"
        raise _BadOutputError(msg) from None
    if not isinstance(parsed, dict):
        parsed = None
    record = cast("dict[object, object] | None", parsed)
    if record is None or set(record) != {"value"}:
        msg = (
            'PERFPROOF needs an object with exactly one key, "value": '
            f"{text[:80]!r}"
        )
        raise _BadOutputError(msg)
    return _positive(record["value"], "PERFPROOF value")


def _counter_value(lines: Iterator[str] | Sequence[str], event: str) -> float:
    """Read ``event``'s count from ``perf stat -x,`` output lines.

    Fields are ``value,unit,event,...``. The event field is matched as
    a prefix of the rest of the line, because an event name may itself
    contain commas.
    """
    counts: list[str] = []
    for raw in lines:
        line = raw.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        parts = line.split(",", 2)
        if len(parts) < 3:
            continue
        value, _, rest = parts
        if rest == event or rest.startswith(f"{event},"):
            counts.append(value)
    if len(counts) != 1:
        msg = (
            f"perf stat reported event {event!r} {len(counts)} times, not once"
        )
        raise _BadOutputError(msg)
    value = counts[0]
    if value.startswith("<"):
        msg = f"perf stat gave {value} for {event!r}"
        raise _BadOutputError(msg)
    try:
        count = float(value)
    except ValueError:
        msg = f"perf stat count for {event!r} is not a number: {value!r}"
        raise _BadOutputError(msg) from None
    return _positive(count, f"perf stat count for {event!r}")


def _positive(value: object, what: str) -> float:
    """Check ``value`` is a finite positive number; return a float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{what} must be a number, not {value!r}"
        raise _BadOutputError(msg)
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number):
        msg = f"{what} must be finite, not {value!r}"
        raise _BadOutputError(msg)
    if number <= 0:
        msg = f"{what} must be positive, not {value!r}"
        raise _BadOutputError(msg)
    return number
