"""The correctness gate: the project's tests and its declared outputs.

This is the part of "Correctness" in ``docs/method.md`` that compares
the two trees through the spec's own checks. Replaying captured calls
and generated inputs (differential checks) and the coverage of changed
lines are separate gates' work.

Tests
-----
The correctness command runs in the baseline tree `BASELINE_RUNS`
times first. A test whose result is not the same in every run is
flaky: it is quarantined and never used to judge the candidate. A test
that fails in every baseline run is a known failure: it is listed and
never used to judge the candidate either, so a candidate that fixes it
gains nothing and one that still fails it loses nothing. The command
then runs once in the candidate tree. A test that fails there after
passing every baseline run (or that passed there and is now skipped or
gone) is run `RERUNS` more times on each tree before the candidate is
blamed: if it changes on the baseline in any run it is quarantined,
otherwise the candidate fails the gate. The reruns are one budget
shared by every such test, so the gate always ends; a test that first
fails on the candidate during them is judged on the baseline runs made
by then. Whether a test is flaky or a known failure is decided over
every baseline run, reruns included.

Per-test results come from a JUnit XML report. Before every run the
environment gets ``--junitxml=<path>`` appended to ``PYTEST_ADDOPTS``,
so pytest writes one wherever the command starts it, and the same path
in `REPORT_ENV_VAR` for any other runner that can write one. A run with
no readable report has no per-test results; its exit status is then the
only thing judged. With a report, the exit status is still judged as a
pseudo-test named `EXIT_STATUS_ID`: it fails when the command exits
non-zero while the report names no failed test, such as a collection
or configuration error. Where a runner writes several reports to the
one path, only the last survives. An exit status that fails in every
baseline run is a known failure like any other test: only the per-test
results judge the candidate, and with no report nothing does.

A run that times out, is killed by a signal or cannot be started from
its tree is not a test result. On the baseline it is a broken
experiment (`BaselineBrokenError`); on the candidate it fails the gate,
with the run's output. Any other failure to start the process, such as
running out of file descriptors, is perfproof's own fault and raises
`OSError`, blamed on neither tree.

Outputs
-------
Each declared output, produced in both trees by the benchmark run, is
compared as exact bytes, as text (line endings and a missing final
newline do not count), or as numbers within ``rtol`` and ``atol``:
the elements of two ``.npy`` arrays, or the numbers in two text files,
whose text between the numbers must match up to runs of whitespace.
A value ``c`` in the candidate is close to ``b`` in the baseline when
``|c - b| <= atol + rtol * |b|``, as in `numpy.isclose`, with the
baseline as the reference; numbers in text compare exactly as
decimals. An output missing from the baseline is a broken experiment;
missing from the candidate, it is a difference. Bytes, arrays and exact
text are read in chunks, so memory stays bounded whatever their size.
Numbers in text are compared a line at a time, so memory grows with the
longest line of the two files, not with the file. Every comparison is
linear in the outputs' size.
"""

import contextlib
import errno
import os
import re
import shlex
import signal
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import zip_longest
from pathlib import Path
from typing import Any, Final, TextIO, cast

import numpy as np
from numpy.typing import NDArray

from perfproof.schema import (
    Arm,
    BenchmarkSpec,
    CommandOutput,
    Difference,
    GateResult,
    OutputCheck,
    QuarantinedTest,
)

__all__ = [
    "BASELINE_RUNS",
    "EXIT_STATUS_ID",
    "MAX_DIFFERENCES",
    "OUTPUT_TAIL_BYTES",
    "REPORT_ENV_VAR",
    "RERUNS",
    "BaselineBrokenError",
    "OutputComparison",
    "SuiteComparison",
    "SuiteRun",
    "check_correctness",
    "check_tests",
    "clear_outputs",
    "compare_outputs",
    "correctness_gate",
    "run_command",
    "run_tests",
]

BASELINE_RUNS: Final = 3
"""Runs of the correctness command on the baseline before judging."""
RERUNS: Final = 5
"""Most further runs on each tree, shared by every test that failed
only on the candidate, before the candidate is blamed."""
MAX_DIFFERENCES: Final = 20
"""Most differences listed per output, and in the gate's result."""
OUTPUT_TAIL_BYTES: Final = 8192
"""Bytes kept from the end of each output stream of a test run."""
REPORT_ENV_VAR: Final = "PERFPROOF_JUNIT_XML"
"""Environment variable naming where a test runner may write its JUnit
XML report."""
EXIT_STATUS_ID: Final = "<exit status>"
"""Test id of the command's exit status, judged like a test."""

_CHUNK_BYTES: Final = 1 << 20
_CHUNK_ELEMENTS: Final = 1 << 18
_CHUNK_CHARS: Final = 1 << 16
_MAX_DIGITS: Final = 4300
_MAX_EXPONENT_DIGITS: Final = 4
_WINDOW_BYTES: Final = 16
_PREVIEW_CHARS: Final = 200
_PASSED: Final = "passed"
_ABSENT: Final = "absent"
_FAILING: Final = frozenset({"failed", "error"})
_RANK: Final = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
# errno values that mean the command cannot start from its tree: a
# missing program or working directory, a bad interpreter line, a file
# that is not executable. Anything else is perfproof's own fault.
_TREE_ERRNOS: Final = frozenset(
    {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EACCES,
        errno.ENOEXEC,
        errno.ELOOP,
        errno.ENAMETOOLONG,
    }
)
_NUMBER: Final = re.compile(
    r"((?<![\w.])[-+]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
    r"|(?i:nan|inf(?:inity)?))(?!\w))"
)
_FINITE: Final = re.compile(r"([-+]?)(\d*)(?:\.(\d*))?(?:[eE]([-+]?\d+))?")
_NAN: Final = re.compile(r"(?<![\w.])[-+]?(?i:nan)(?!\w)")


class BaselineBrokenError(RuntimeError):
    """The baseline failed its own checks, so the experiment is broken.

    The comparison stops with no verdict: nothing is blamed on the
    candidate.

    Parameters
    ----------
    message : str
        What failed.
    output : CommandOutput or None, optional
        Output of the baseline run that failed; ``None`` when no
        command output explains it.
    """

    def __init__(
        self, message: str, output: CommandOutput | None = None
    ) -> None:
        super().__init__(message)
        self.output = output


@dataclass(frozen=True)
class SuiteRun:
    """One run of the correctness command in one tree.

    Attributes
    ----------
    results : mapping of str to str
        Result of each test by id: ``passed``, ``failed``, ``error`` or
        ``skipped``, plus `EXIT_STATUS_ID` (``passed`` or ``failed``).
        Empty when ``abnormal`` is set.
    reported : bool
        Whether a readable JUnit report gave per-test results.
    output : CommandOutput or None
        What the command printed and how it exited; ``None`` when it
        could not be started.
    abnormal : str or None
        How the run ended without a test result (could not start, timed
        out, killed by a signal); ``None`` when it exited by itself.
    """

    results: Mapping[str, str]
    reported: bool
    output: CommandOutput | None
    abnormal: str | None


@dataclass(frozen=True)
class SuiteComparison:
    """The tests' judgement of the candidate.

    Attributes
    ----------
    quarantined : tuple of QuarantinedTest
        Flaky tests, left out of the judgement.
    known_failures : tuple of str
        Ids of tests that failed in every baseline run, left out of the
        judgement.
    failures : tuple of Difference
        Tests blamed on the candidate: the result on the baseline and
        the results on the candidate, in run order.
    problem : str or None
        Why the candidate fails; ``None`` when it does not.
    output : CommandOutput or None
        Output of the first candidate run that failed; ``None`` when
        none did or it could not be started.
    judged : int
        Tests used to judge the candidate (the exit status, when no run
        reported per-test results), neither flaky nor known failures.
    baseline_runs, candidate_runs : int
        Runs of the command on each tree.
    """

    quarantined: tuple[QuarantinedTest, ...]
    known_failures: tuple[str, ...]
    failures: tuple[Difference, ...]
    problem: str | None
    output: CommandOutput | None
    judged: int
    baseline_runs: int
    candidate_runs: int


@dataclass(frozen=True)
class OutputComparison:
    """Declared outputs compared between the two trees.

    Attributes
    ----------
    differences : tuple of Difference
        The first differences of each output, at most the limit per
        output, outputs in declared order.
    truncated : bool
        Whether an output had more differences than are listed.
    problems : tuple of str
        One line per output that differs.
    compared : int
        Outputs compared.
    """

    differences: tuple[Difference, ...]
    truncated: bool
    problems: tuple[str, ...]
    compared: int


class _Collector:
    """First differences of one output, and how many there were."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.items: list[Difference] = []
        self.count = 0

    @property
    def room(self) -> int:
        """How many more differences can still be listed."""
        return self.limit - len(self.items)

    def add(self, difference: Difference) -> None:
        """Count a difference, listing it while there is room."""
        self.count += 1
        if self.room > 0:
            self.items.append(difference)


class _NotTextError(Exception):
    """A text output that is not valid UTF-8."""

    def __init__(self, arm: Arm, reason: str) -> None:
        super().__init__(reason)
        self.arm = arm


def _tail(path: Path) -> tuple[str, bool]:
    """Return a file's tail and whether it was cut to it."""
    size = path.stat().st_size
    with path.open("rb") as stream:
        stream.seek(max(0, size - OUTPUT_TAIL_BYTES))
        data = stream.read()
    return data.decode("utf-8", errors="replace"), size > OUTPUT_TAIL_BYTES


def _kill_group(pgid: int) -> None:
    """Kill what is left of a run's process group.

    Processes a test suite leaves behind would perturb the measured
    rounds that follow.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


def _execute(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    scratch: Path,
) -> tuple[CommandOutput | None, str | None]:
    """Run a command in its own session, keeping its output's tail.

    Returns
    -------
    output : CommandOutput or None
        ``None`` when the command could not be started.
    abnormal : str or None
        How it ended abnormally; ``None`` when it exited by itself.

    Raises
    ------
    OSError
        If the process could not be created for a reason other than
        the command or its working directory.
    """
    out_path, err_path = scratch / "stdout", scratch / "stderr"
    timed_out = False
    with out_path.open("wb") as out, err_path.open("wb") as err:
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
        except OSError as exc:
            if exc.errno in _TREE_ERRNOS:
                return None, f"could not be started: {exc}"
            raise
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process.pid)
            code = process.wait()
        else:
            _kill_group(process.pid)
    stdout, cut_out = _tail(out_path)
    stderr, cut_err = _tail(err_path)
    output = CommandOutput(
        command=tuple(argv),
        exit_code=code,
        timed_out=timed_out,
        stdout=stdout,
        stderr=stderr,
        truncated=cut_out or cut_err,
    )
    if timed_out:
        return output, f"timed out after {timeout:g} s"
    if code < 0:
        try:
            name = signal.Signals(-code).name
        except ValueError:
            name = str(-code)
        return output, f"was killed by signal {name}"
    return output, None


def _case_result(case: ET.Element) -> str:
    """Return a JUnit test case's result, the worst one it records."""
    tags = {child.tag for child in case}
    if "error" in tags:
        return "error"
    if "failure" in tags:
        return "failed"
    if "skipped" in tags:
        return "skipped"
    return _PASSED


def _read_report(path: Path) -> dict[str, str] | None:
    """Read per-test results from a JUnit XML report.

    A test's id is ``classname::name``, or ``name`` without a class. A
    test listed twice keeps its worse result. Returns ``None`` when the
    report is missing or unreadable.
    """
    results: dict[str, str] = {}
    try:
        with path.open("rb") as stream:
            # The report is written on this machine by the project's
            # own test runner, which perfproof runs anyway. ElementTree
            # never fetches external entities, and expat 2.4.1 and
            # later limit entity expansion.
            for _, case in ET.iterparse(stream):  # noqa: S314
                if case.tag != "testcase":
                    continue
                _record_case(case, results)
                # Each case's content (messages, captured output) is
                # dropped once read; only an empty element per case
                # stays, so memory grows with the number of cases.
                case.clear()
    except (OSError, ET.ParseError):
        return None
    return results


def _record_case(case: ET.Element, results: dict[str, str]) -> None:
    """Record one JUnit test case's result, keeping the worse one."""
    name = case.get("name")
    if not name:
        return
    classname = case.get("classname")
    test_id = f"{classname}::{name}" if classname else name
    result = _case_result(case)
    previous = results.get(test_id)
    if previous is None or _RANK[result] > _RANK[previous]:
        results[test_id] = result


def run_command(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    scratch: Path,
) -> tuple[CommandOutput | None, str | None]:
    """Run one command in its own session, keeping its output's tail.

    Parameters
    ----------
    argv : sequence of str
        The command, launched without a shell.
    cwd : Path
        Working directory.
    env : mapping of str to str
        The command's whole environment.
    timeout : float
        Time limit in seconds; the process group is killed after it.
    scratch : Path
        An existing directory for the output files.

    Returns
    -------
    output : CommandOutput or None
        ``None`` when the command could not be started.
    abnormal : str or None
        How it ended abnormally (could not start, timed out, killed by
        a signal); ``None`` when it exited by itself.

    Raises
    ------
    OSError
        If the process could not be created for a reason other than the
        command or its working directory.
    """
    return _execute(argv, cwd, env, timeout, scratch)


def _test_env(env: Mapping[str, str], report: Path) -> dict[str, str]:
    """Add the JUnit report path to a test command's environment."""
    flag = shlex.quote(f"--junitxml={report}")
    addopts = env.get("PYTEST_ADDOPTS", "")
    return {
        **env,
        "PYTEST_ADDOPTS": f"{addopts} {flag}".strip(),
        REPORT_ENV_VAR: str(report),
    }


def run_tests(
    spec: BenchmarkSpec, tree_root: Path, env: Mapping[str, str]
) -> SuiteRun:
    """Run the spec's correctness command once in one tree.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force; its correctness command must be set.
    tree_root : Path
        Root of the tree to run in.
    env : mapping of str to str
        Environment the command inherits, before the spec's declared
        variables and the report path are applied.

    Returns
    -------
    SuiteRun
        Per-test results from the run's JUnit report when it wrote one,
        and the exit status as `EXIT_STATUS_ID`.

    Raises
    ------
    ValueError
        If the spec has no correctness command.
    OSError
        If the process cannot be created for a reason other than the
        command or its working directory, such as running out of file
        descriptors.
    """
    correctness = spec.correctness
    if correctness.command is None or correctness.timeout_seconds is None:
        msg = "the spec has no correctness command"
        raise ValueError(msg)
    with tempfile.TemporaryDirectory(prefix="perfproof-tests-") as tmp:
        scratch = Path(tmp)
        report = scratch / "report.xml"
        output, abnormal = _execute(
            correctness.command,
            tree_root / correctness.cwd,
            _test_env({**env, **spec.env}, report),
            correctness.timeout_seconds,
            scratch,
        )
        if output is None or abnormal is not None:
            return SuiteRun(
                {}, reported=False, output=output, abnormal=abnormal
            )
        tests = _read_report(report)
    results = dict(tests or {})
    explained = any(r in _FAILING for r in results.values())
    exit_passed = output.exit_code == 0 or explained
    results[EXIT_STATUS_ID] = _PASSED if exit_passed else "failed"
    return SuiteRun(
        results, reported=tests is not None, output=output, abnormal=None
    )


def _history(runs: Sequence[SuiteRun], test_id: str) -> tuple[str, ...]:
    """Return one test's result in each run, `_ABSENT` where missing."""
    return tuple(run.results.get(test_id, _ABSENT) for run in runs)


def _is_bad(baseline: str, candidate: str) -> bool:
    """Whether a candidate result counts against the candidate.

    Failing counts, and so does a test that passed on the baseline
    being skipped or gone on the candidate.
    """
    if candidate in _FAILING:
        return True
    return baseline == _PASSED and candidate != _PASSED


def _test_ids(runs: Sequence[SuiteRun]) -> list[str]:
    """Return every test id seen in ``runs``, in first-seen order."""
    return list(dict.fromkeys(t for run in runs for t in run.results))


def _baseline_run(
    spec: BenchmarkSpec, root: Path, env: Mapping[str, str]
) -> SuiteRun:
    """Run the tests on the baseline; stop on an abnormal end."""
    run = run_tests(spec, root, env)
    if run.abnormal is not None:
        msg = f"the correctness command {run.abnormal} in the baseline tree"
        raise BaselineBrokenError(msg, run.output)
    return run


def _kind(history: Sequence[str]) -> str:
    """Classify a test by its baseline results.

    ``known`` when it failed in every run (failed or error), ``flaky``
    when its results otherwise differ, ``stable`` otherwise.
    """
    if all(r in _FAILING for r in history):
        return "known"
    if len(set(history)) > 1:
        return "flaky"
    return "stable"


def _hard_failure(run: SuiteRun, runs: int) -> str:
    """Describe a candidate run that ended without a test result."""
    return f"the correctness command {run.abnormal} in the candidate tree " + (
        "on its first run" if runs == 1 else f"on run {runs}"
    )


def check_tests(
    spec: BenchmarkSpec,
    *,
    baseline_root: Path,
    candidate_root: Path,
    env: Mapping[str, str],
) -> SuiteComparison:
    """Judge the candidate by the tests, minus flaky and known failures.

    Runs the correctness command `BASELINE_RUNS` times on the baseline,
    then once on the candidate. A test that fails only on the candidate
    is a suspect, and the command is rerun on both trees, alternating,
    at most `RERUNS` more times each: the reruns are one budget shared
    by every suspect, so the gate always ends. They stop early once
    every suspect has changed result on the baseline and so is flaky.
    A test that first fails on the candidate during the reruns is a
    suspect too, judged on every baseline run so far.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force; its correctness command must be set.
    baseline_root, candidate_root : Path
        Roots of the two trees.
    env : mapping of str to str
        Environment the command inherits, before the spec's declared
        variables are applied.

    Returns
    -------
    SuiteComparison
        Quarantined tests, known failures, every test blamed on the
        candidate, and the output of the first failing candidate run.

    Raises
    ------
    BaselineBrokenError
        If a baseline run times out, is killed by a signal or cannot be
        started.
    ValueError
        If the spec has no correctness command.
    OSError
        If a process cannot be created for a reason that is
        perfproof's own, such as running out of file descriptors.
    """
    baseline = [
        _baseline_run(spec, baseline_root, env) for _ in range(BASELINE_RUNS)
    ]
    first = run_tests(spec, candidate_root, env)
    candidate = [first]
    if first.abnormal is not None:
        return _judge(baseline, candidate, _hard_failure(first, 1), first)
    suspects = set(_suspects(baseline, first, ()))
    for _ in range(RERUNS if suspects else 0):
        baseline.append(_baseline_run(spec, baseline_root, env))
        rerun = run_tests(spec, candidate_root, env)
        candidate.append(rerun)
        if rerun.abnormal is not None:
            problem = _hard_failure(rerun, len(candidate))
            return _judge(baseline, candidate, problem, rerun)
        suspects.update(_suspects(baseline, rerun, suspects))
        if all(_kind(_history(baseline, t)) != "stable" for t in suspects):
            break
    return _judge(baseline, candidate, None, None)


def _suspects(
    baseline: Sequence[SuiteRun], run: SuiteRun, known: Iterable[str]
) -> list[str]:
    """Return tests stable on the baseline that ``run`` counts against.

    Tests in ``known`` are already suspects and are left out.
    """
    skip = set(known)
    found: list[str] = []
    for test_id in _test_ids([*baseline, run]):
        history = _history(baseline, test_id)
        if test_id in skip or _kind(history) != "stable":
            continue
        if _is_bad(history[0], run.results.get(test_id, _ABSENT)):
            found.append(test_id)
    return found


def _judge(
    baseline: Sequence[SuiteRun],
    candidate: Sequence[SuiteRun],
    hard_failure: str | None,
    failed_run: SuiteRun | None,
) -> SuiteComparison:
    """Classify every test over all runs and build the comparison.

    A test that failed in every baseline run is a known failure and a
    test whose baseline results otherwise differ is quarantined; neither
    is judged. Any other test is blamed when a candidate run gave a
    result that counts against the candidate (`_is_bad`).
    """
    quarantined: list[QuarantinedTest] = []
    known: list[str] = []
    failures: list[Difference] = []
    first_bad: int | None = None
    finished = [run for run in candidate if run.abnormal is None]
    for test_id in _test_ids([*baseline, *finished]):
        history = _history(baseline, test_id)
        kind = _kind(history)
        if kind == "known":
            known.append(test_id)
            continue
        if kind == "flaky":
            quarantined.append(
                QuarantinedTest(test_id=test_id, baseline_results=history)
            )
            continue
        results = _history(finished, test_id)
        bad = [i for i, r in enumerate(results) if _is_bad(history[0], r)]
        if not bad:
            continue
        first_bad = bad[0] if first_bad is None else min(first_bad, bad[0])
        failures.append(
            Difference(
                item=test_id,
                baseline=f"{history[0]} in all {len(history)} runs",
                candidate=", ".join(results),
            )
        )
    reported = any(run.reported for run in [*baseline, *finished])
    units = {
        t for t in _test_ids([*baseline, *finished]) if t != EXIT_STATUS_ID
    }
    if not reported:
        units = {EXIT_STATUS_ID}
    problem = hard_failure
    output = None if failed_run is None else failed_run.output
    if problem is None and failures:
        problem = (
            f"{len(failures)} tests fail on the candidate and never on the "
            f"baseline in {len(baseline)} runs"
        )
        if first_bad is not None:
            output = finished[first_bad].output
    return SuiteComparison(
        quarantined=tuple(quarantined),
        known_failures=tuple(known),
        failures=tuple(failures),
        problem=problem,
        output=output,
        judged=len(units - {q.test_id for q in quarantined} - set(known)),
        baseline_runs=len(baseline),
        candidate_runs=len(candidate),
    )


def _locate(root: Path, rel: str) -> Path | str:
    """Find a declared output in a tree, or say why it can't be used.

    An output must be a regular file inside its tree: a symbolic link
    out of it could point the candidate's output at the baseline's.
    """
    path = root / rel
    if not path.exists():
        return "is missing"
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        return "resolves outside its tree"
    if not resolved.is_file():
        return "is not a regular file"
    return resolved


def _clip(text: str | None) -> str | None:
    """Shorten a value for the record."""
    if text is None or len(text) <= _PREVIEW_CHARS:
        return text
    return text[:_PREVIEW_CHARS] + "..."


def _window(path: Path, offset: int) -> str | None:
    """Hex of the bytes from ``offset``; ``None`` past the end."""
    with path.open("rb") as stream:
        stream.seek(offset)
        data = stream.read(_WINDOW_BYTES)
    return data.hex(" ") if data else None


def _compare_bytes(
    rel: str, baseline: Path, candidate: Path, found: _Collector
) -> None:
    """Find where two files' bytes first differ, and their sizes."""
    offset = 0
    first: int | None = None
    with baseline.open("rb") as fb, candidate.open("rb") as fc:
        while True:
            xb, xc = fb.read(_CHUNK_BYTES), fc.read(_CHUNK_BYTES)
            if xb != xc:
                n = min(len(xb), len(xc))
                at = np.flatnonzero(
                    np.frombuffer(xb[:n], np.uint8)
                    != np.frombuffer(xc[:n], np.uint8)
                )
                first = offset + (int(at[0]) if at.size else n)
                break
            if not xb:
                break
            offset += len(xb)
    if first is None:
        return
    size_b, size_c = baseline.stat().st_size, candidate.stat().st_size
    if first < min(size_b, size_c):
        found.add(
            Difference(
                item=f"{rel} at byte {first}",
                baseline=_window(baseline, first),
                candidate=_window(candidate, first),
            )
        )
    if size_b != size_c:
        found.add(
            Difference(
                item=f"{rel} size",
                baseline=f"{size_b} bytes",
                candidate=f"{size_c} bytes",
            )
        )


class _Pieces:
    """A text file read line by line, in pieces of bounded length.

    Each line comes in pieces of at most `_CHUNK_CHARS` characters cut
    from the start of the line, so the same line gives the same pieces
    in both trees, and a line of any length takes bounded memory.
    """

    def __init__(self, stream: TextIO, arm: Arm) -> None:
        self.stream = stream
        self.arm: Arm = arm
        self.mid_line = False

    def next(self) -> tuple[str, bool] | None:
        """Return the next piece and whether it ends its line.

        Returns ``None`` at the end of the file, between lines.

        Raises
        ------
        _NotTextError
            If the file is not valid UTF-8.
        """
        try:
            piece = self.stream.readline(_CHUNK_CHARS)
        except UnicodeDecodeError as exc:
            raise _NotTextError(self.arm, str(exc)) from exc
        if not piece:
            if not self.mid_line:
                return None
            self.mid_line = False
            return "", True
        if piece.endswith("\n"):
            self.mid_line = False
            return piece[:-1], True
        self.mid_line = True
        return piece, False


def _next_lines(
    rb: _Pieces, rc: _Pieces
) -> tuple[bool, str | None, str | None] | None:
    """Compare the next line of both files piece by piece.

    Returns
    -------
    tuple of (bool, str or None, str or None), or None
        Whether the lines are equal, and the first piece of each
        (``None`` where that file has no more lines); ``None`` when
        both files are at their end.
    """
    pb, pc = rb.next(), rc.next()
    if pb is None and pc is None:
        return None
    first_b = None if pb is None else pb[0]
    first_c = None if pc is None else pc[0]
    equal = first_b == first_c
    ended_b = pb is None or pb[1]
    ended_c = pc is None or pc[1]
    while not (ended_b and ended_c):
        xb = xc = ""
        if not ended_b:
            nb = rb.next()
            xb, ended_b = ("", True) if nb is None else nb
        if not ended_c:
            nc = rc.next()
            xc, ended_c = ("", True) if nc is None else nc
        equal = equal and xb == xc
    return equal, first_b, first_c


def _number_kind(token: str) -> str:
    """Return ``nan``, ``inf`` or ``finite`` for a number literal."""
    bare = token.lstrip("+-").lower()
    if bare == "nan":
        return "nan"
    if bare in ("inf", "infinity"):
        return "inf"
    return "finite"


def _exact(token: str) -> Fraction | None:
    """Return a finite literal's exact value.

    ``None`` when it has more than `_MAX_DIGITS` digits or an exponent
    of more than `_MAX_EXPONENT_DIGITS` digits, too long to evaluate
    in bounded time and memory.
    """
    match = _FINITE.fullmatch(token)
    if match is None:
        return None
    sign, whole, frac, exponent = match.groups(default="")
    digits = whole + frac
    if (
        len(digits) > _MAX_DIGITS
        or len(exponent.lstrip("+-")) > _MAX_EXPONENT_DIGITS
    ):
        return None
    value = Fraction(int(digits or "0")) * Fraction(10) ** (
        int(exponent or "0") - len(frac)
    )
    return -value if sign == "-" else value


def _close(baseline: str, candidate: str, check: OutputCheck) -> bool:
    """Whether a number in the candidate is close to the baseline's.

    Finite literals compare exactly as decimals, so large integers and
    long fractions lose nothing to float conversion. An infinity is
    close only to an infinity of the same sign, never to a finite
    literal however large, and NaN only to NaN under
    ``equal_nan``, as in `numpy.isclose`. Two literals too long to
    evaluate (`_exact`) are close only when they are the same text.
    """
    kind_b, kind_c = _number_kind(baseline), _number_kind(candidate)
    if "nan" in (kind_b, kind_c):
        return check.equal_nan and kind_b == kind_c
    if "inf" in (kind_b, kind_c):
        # By kind and sign only: a finite literal too large for a
        # float, such as 1e309, would round to infinity.
        return kind_b == kind_c and (
            baseline.startswith("-") == candidate.startswith("-")
        )
    if baseline == candidate:
        return True
    vb, vc = _exact(baseline), _exact(candidate)
    if vb is None or vc is None:
        return False
    allowed = Fraction(check.atol) + Fraction(check.rtol) * abs(vb)
    return abs(vc - vb) <= allowed


def _same_text(baseline: str, candidate: str) -> bool:
    """Whether text between numbers matches up to whitespace runs."""
    return baseline.split() == candidate.split()


def _compare_number_line(
    where: str,
    baseline: str,
    candidate: str,
    check: OutputCheck,
    found: _Collector,
) -> None:
    """Compare the numbers in one line, and the text between them.

    Number differences are recorded only when all the text between the
    numbers matches; otherwise the whole line is one difference.
    """
    pending: list[Difference] = []
    unlisted = 0
    pos_b = pos_c = 0
    pairs = zip_longest(
        _NUMBER.finditer(baseline), _NUMBER.finditer(candidate)
    )
    for k, (mb, mc) in enumerate(pairs, start=1):
        if (
            mb is None
            or mc is None
            or not _same_text(
                baseline[pos_b : mb.start()], candidate[pos_c : mc.start()]
            )
        ):
            break
        if not _close(mb.group(), mc.group(), check):
            if len(pending) < found.room:
                pending.append(
                    Difference(
                        item=f"{where}, number {k}",
                        baseline=_clip(mb.group()),
                        candidate=_clip(mc.group()),
                    )
                )
            else:
                unlisted += 1
        pos_b, pos_c = mb.end(), mc.end()
    else:
        if _same_text(baseline[pos_b:], candidate[pos_c:]):
            found.items.extend(pending)
            found.count += len(pending) + unlisted
            return
    found.add(
        Difference(
            item=where, baseline=_clip(baseline), candidate=_clip(candidate)
        )
    )


def _compare_text(
    check: OutputCheck, baseline: Path, candidate: Path, found: _Collector
) -> None:
    """Compare two text outputs line by line, exactly or as numbers.

    Exact text is read in pieces of `_CHUNK_CHARS`, so memory stays
    bounded however long a line is. Numbers are compared a line at a
    time, so memory grows with the longest line of either file.

    Raises
    ------
    _NotTextError
        If either file is not valid UTF-8.
    """
    with (
        baseline.open(encoding="utf-8", newline=None) as sb,
        candidate.open(encoding="utf-8", newline=None) as sc,
    ):
        if check.compare == "numbers":
            _compare_number_lines(check, sb, sc, found)
            return
        rb, rc = _Pieces(sb, "baseline"), _Pieces(sc, "candidate")
        number = 0
        while (line := _next_lines(rb, rc)) is not None:
            number += 1
            equal, first_b, first_c = line
            if not equal:
                found.add(
                    Difference(
                        item=f"{check.path} line {number}",
                        baseline=_clip(first_b),
                        candidate=_clip(first_c),
                    )
                )


def _read_lines(stream: TextIO, arm: Arm) -> Iterator[str]:
    """Yield whole lines without their line endings.

    Raises
    ------
    _NotTextError
        If the file is not valid UTF-8.
    """
    try:
        for line in stream:
            yield line.removesuffix("\n")
    except UnicodeDecodeError as exc:
        raise _NotTextError(arm, str(exc)) from exc


def _compare_number_lines(
    check: OutputCheck, sb: TextIO, sc: TextIO, found: _Collector
) -> None:
    """Compare the numbers in two text files, line by line."""
    pairs = zip_longest(
        _read_lines(sb, "baseline"), _read_lines(sc, "candidate")
    )
    for number, (lb, lc) in enumerate(pairs, start=1):
        where = f"{check.path} line {number}"
        if lb is None or lc is None:
            found.add(
                Difference(item=where, baseline=_clip(lb), candidate=_clip(lc))
            )
        elif lb != lc or (not check.equal_nan and _NAN.search(lb)):
            _compare_number_line(where, lb, lc, check, found)


def _load_array(path: Path) -> NDArray[Any]:
    """Load a ``.npy`` file, memory-mapped where possible.

    Raises
    ------
    ValueError
        If the file is not a ``.npy`` array without pickled objects.
    EOFError
        If the file is empty.
    OSError
        If the file cannot be read.
    """
    try:
        loaded: object = np.load(path, mmap_mode="r", allow_pickle=False)
    except ValueError:
        loaded = np.load(path, allow_pickle=False)
    if not isinstance(loaded, np.ndarray):
        msg = "not a .npy array"
        raise ValueError(msg)
    return cast("NDArray[Any]", loaded)


def _close_mask(
    baseline: NDArray[Any], candidate: NDArray[Any], check: OutputCheck
) -> NDArray[np.bool_]:
    """Elementwise closeness of two equal-dtype chunks.

    Integers with no tolerance compare exactly. With a tolerance, they
    compare as float64, so magnitudes above 2**53 lose precision.
    ``equal_nan`` applies to floating and complex values; booleans,
    strings, dates and records compare exactly.
    """
    kind = baseline.dtype.kind
    exact = np.asarray(baseline == candidate, dtype=np.bool_)
    if kind not in "iufc" or (kind in "iu" and not check.rtol + check.atol):
        return exact
    with np.errstate(all="ignore"):
        near = np.isclose(
            candidate,
            baseline,
            rtol=check.rtol,
            atol=check.atol,
            equal_nan=check.equal_nan,
        )
    return exact | near


def _index(flat: int, shape: tuple[int, ...], order: str) -> str:
    """Format a flat index as an index into ``shape``."""
    index = np.unravel_index(flat, shape, order="F" if order == "F" else "C")
    return (
        "[" + ", ".join(str(int(i)) for i in index) + "]" if index else "[()]"
    )


def _compare_arrays(
    check: OutputCheck,
    baseline: NDArray[Any],
    candidate: NDArray[Any],
    found: _Collector,
) -> None:
    """Compare two arrays elementwise in chunks of bounded size."""
    if baseline.shape != candidate.shape:
        found.add(
            Difference(
                item=f"{check.path} shape",
                baseline=str(baseline.shape),
                candidate=str(candidate.shape),
            )
        )
        return
    if baseline.dtype != candidate.dtype:
        found.add(
            Difference(
                item=f"{check.path} dtype",
                baseline=str(baseline.dtype),
                candidate=str(candidate.dtype),
            )
        )
        return
    both_f = baseline.flags.f_contiguous and candidate.flags.f_contiguous
    both_c = baseline.flags.c_contiguous and candidate.flags.c_contiguous
    order = "F" if both_f and not both_c else "C"
    flat_b = baseline.ravel(order=order)
    flat_c = candidate.ravel(order=order)
    for start in range(0, flat_b.size, _CHUNK_ELEMENTS):
        stop = start + _CHUNK_ELEMENTS
        xb, xc = np.asarray(flat_b[start:stop]), np.asarray(flat_c[start:stop])
        bad = np.flatnonzero(~_close_mask(xb, xc, check))
        found.items.extend(
            Difference(
                item=check.path + _index(start + i, baseline.shape, order),
                baseline=repr(xb[i].item()),
                candidate=repr(xc[i].item()),
            )
            for i in bad[: found.room].tolist()
        )
        found.count += bad.size


def _compare_one(
    check: OutputCheck, baseline: Path, candidate: Path, found: _Collector
) -> str | None:
    """Compare one declared output; return why it can't be read, if so.

    Raises
    ------
    BaselineBrokenError
        If the baseline's file cannot be read as declared.
    """
    if check.compare == "bytes":
        _compare_bytes(check.path, baseline, candidate, found)
        return None
    if check.compare == "numbers" and check.path.lower().endswith(".npy"):
        try:
            array_b = _load_array(baseline)
        except (ValueError, OSError, EOFError) as exc:
            msg = f"declared output {check.path} in the baseline tree: {exc}"
            raise BaselineBrokenError(msg) from exc
        try:
            array_c = _load_array(candidate)
        except (ValueError, OSError, EOFError) as exc:
            return f"cannot be read as an array: {exc}"
        _compare_arrays(check, array_b, array_c, found)
        return None
    try:
        _compare_text(check, baseline, candidate, found)
    except _NotTextError as exc:
        if exc.arm == "baseline":
            msg = f"declared output {check.path} in the baseline tree: {exc}"
            raise BaselineBrokenError(msg) from exc
        return f"is not UTF-8 text: {exc}"
    return None


def compare_outputs(
    outputs: Sequence[OutputCheck],
    *,
    baseline_root: Path,
    candidate_root: Path,
    limit: int = MAX_DIFFERENCES,
) -> OutputComparison:
    """Compare declared outputs between the two trees.

    Call after the benchmark has produced them in both trees, and
    before anything else (such as the tests) can overwrite them.

    Parameters
    ----------
    outputs : sequence of OutputCheck
        The spec's declared outputs.
    baseline_root, candidate_root : Path
        Roots of the two trees.
    limit : int, optional
        Most differences listed per output.

    Returns
    -------
    OutputComparison
        The first differences of each output and a line per output
        that differs. Linear in the outputs' size. Memory is bounded
        by the chunk size, except for numbers in text, where it grows
        with the longest line.

    Raises
    ------
    BaselineBrokenError
        If an output is missing from the baseline tree, is not a
        regular file inside it, or cannot be read as declared.
    """
    differences: list[Difference] = []
    problems: list[str] = []
    truncated = False
    for check in outputs:
        baseline = _locate(baseline_root, check.path)
        if isinstance(baseline, str):
            msg = (
                f"declared output {check.path} {baseline} in the baseline tree"
            )
            raise BaselineBrokenError(msg)
        found = _Collector(limit)
        candidate = _locate(candidate_root, check.path)
        if isinstance(candidate, str):
            reason: str | None = candidate
            found.add(
                Difference(
                    item=check.path,
                    baseline=f"{baseline.stat().st_size} bytes",
                    candidate=None,
                )
            )
        else:
            reason = _compare_one(check, baseline, candidate, found)
            if reason is not None:
                found.add(
                    Difference(
                        item=check.path,
                        baseline=f"{baseline.stat().st_size} bytes",
                        candidate=reason,
                    )
                )
        if reason is not None:
            problems.append(
                f"output {check.path} {reason} in the candidate tree"
            )
        elif found.count:
            problems.append(
                f"output {check.path} differs ({check.compare}): "
                f"{found.count} differences"
            )
        differences.extend(found.items)
        truncated = truncated or found.count > len(found.items)
    return OutputComparison(
        differences=tuple(differences),
        truncated=truncated,
        problems=tuple(problems),
        compared=len(outputs),
    )


def clear_outputs(outputs: Sequence[OutputCheck], tree_root: Path) -> None:
    """Remove declared outputs from a tree before the benchmark runs.

    An output left from an earlier run, or committed to the tree, would
    otherwise be compared in place of what the code produces now.

    Parameters
    ----------
    outputs : sequence of OutputCheck
        The spec's declared outputs.
    tree_root : Path
        Root of the tree.

    Raises
    ------
    OSError
        If an existing output file or link cannot be removed.
    """
    for check in outputs:
        path = tree_root / check.path
        if path.is_symlink() or path.is_file():
            path.unlink()


def correctness_gate(
    spec: BenchmarkSpec,
    *,
    baseline_root: Path,
    candidate_root: Path,
    env: Mapping[str, str],
) -> GateResult:
    """Run the correctness gate's tests and output comparisons.

    The gate of `check_correctness`, without the tests' judgement.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force.
    baseline_root, candidate_root : Path
        Roots of the two trees.
    env : mapping of str to str
        Environment the test command inherits, before the spec's
        declared variables are applied.

    Returns
    -------
    GateResult
        See `check_correctness`.

    Raises
    ------
    BaselineBrokenError
        If the baseline fails its own checks.
    OSError
        If a process cannot be created for a reason that is
        perfproof's own.
    """
    return check_correctness(
        spec,
        baseline_root=baseline_root,
        candidate_root=candidate_root,
        env=env,
    )[0]


def check_correctness(
    spec: BenchmarkSpec,
    *,
    baseline_root: Path,
    candidate_root: Path,
    env: Mapping[str, str],
) -> tuple[GateResult, SuiteComparison | None]:
    """Run the correctness gate's tests and output comparisons.

    Call after the benchmark has produced its declared outputs in both
    trees. Outputs are compared first, before the tests run and can
    touch them; then `check_tests` judges the candidate.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force.
    baseline_root, candidate_root : Path
        Roots of the two trees.
    env : mapping of str to str
        Environment the test command inherits, before the spec's
        declared variables are applied.

    Returns
    -------
    GateResult
        Gate ``correctness``: failed when an output differs, a test
        fails only on the candidate, or a candidate test run times out,
        is killed or cannot start, with the failing run's output;
        ``not_available`` when no output is declared and every test
        is flaky or a known failure, so nothing judged the candidate;
        passed otherwise. Quarantined tests are listed whatever the
        status, and known failures are named in ``detail``. Test
        failures are listed before output differences, at most
        `MAX_DIFFERENCES` in all.

        A spec with no test command and no declared output has
        nothing to check: ``not_available``, never passed.

    Raises
    ------
    BaselineBrokenError
        If the baseline fails its own checks: a declared output is
        missing or unreadable, or a baseline test run times out, is
        killed or cannot start.
    OSError
        If a process cannot be created for a reason that is
        perfproof's own.
    """
    if spec.correctness.command is None and not spec.correctness.outputs:
        return (
            GateResult(
                gate="correctness",
                status="not_available",
                detail=(
                    "the spec declares no correctness check (no test "
                    "command and no output to compare), so nothing was "
                    "checked and a KEEP is withheld"
                ),
            ),
            None,
        )
    outputs = compare_outputs(
        spec.correctness.outputs,
        baseline_root=baseline_root,
        candidate_root=candidate_root,
    )
    tests = None
    if spec.correctness.command is not None:
        tests = check_tests(
            spec,
            baseline_root=baseline_root,
            candidate_root=candidate_root,
            env=env,
        )
    problems = list(outputs.problems)
    differences = list(outputs.differences)
    quarantined: tuple[QuarantinedTest, ...] = ()
    output = None
    summary: list[str] = []
    if tests is not None:
        quarantined = tests.quarantined
        differences = [*tests.failures, *differences]
        if tests.problem is not None:
            problems.insert(0, tests.problem)
            output = tests.output
        summary.append(
            f"{tests.judged} tests judged over {tests.baseline_runs} "
            f"baseline and {tests.candidate_runs} candidate runs"
        )
        if quarantined:
            names = ", ".join(q.test_id for q in quarantined)
            summary.append(f"{len(quarantined)} quarantined as flaky: {names}")
        if tests.known_failures:
            summary.append(
                f"{len(tests.known_failures)} known failures, failing in "
                f"every baseline run: {', '.join(tests.known_failures)}"
            )
    if outputs.compared:
        summary.append(f"{outputs.compared} declared outputs compared")
    if problems:
        failed = GateResult(
            gate="correctness",
            status="failed",
            detail="; ".join(problems + summary),
            differences=tuple(differences[:MAX_DIFFERENCES]),
            truncated=outputs.truncated or len(differences) > MAX_DIFFERENCES,
            quarantined=quarantined,
            output=output,
        )
        return failed, tests
    if tests is not None and not tests.judged and not outputs.compared:
        unjudged = GateResult(
            gate="correctness",
            status="not_available",
            detail="every test is flaky or a known failure and no output "
            "is declared, so nothing judged the candidate; "
            + "; ".join(summary),
            quarantined=quarantined,
        )
        return unjudged, tests
    passed = GateResult(
        gate="correctness",
        status="passed",
        detail="; ".join(summary),
        quarantined=quarantined,
    )
    return passed, tests
