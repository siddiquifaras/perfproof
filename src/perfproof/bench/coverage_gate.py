"""The coverage gate: every changed executable line must have run.

This is "Coverage gate" in ``docs/method.md``: the lines the candidate
changed are taken from ``git diff``, the correctness command runs once
more in the candidate tree under coverage.py, and every changed
executable line must have run under a check that judged the candidate.
A change in code no check ran is unverified: the gate is
``not_available`` (never failed, since the change may be right and
perfproof only cannot show it) with the unverified lines listed, and
the verdict is INCONCLUSIVE.

Which lines changed
-------------------
`changed_lines` lists what ``git diff --text`` reports between the
baseline's commit and the candidate's working tree (``--text``, so a
``-diff`` or ``binary`` attribute cannot hide a change), plus every
untracked file git does not ignore, except the spec's declared
outputs: an untracked file is part of the candidate, a ``.py`` one as
a new module and any other one (an untracked ``.gitattributes``
included) as a non-Python change, except validated Python bytecode
(a timestamp-based or checked-hash ``.pyc`` in ``__pycache__``). The
candidate is the tree as it was handed to perfproof, so the untracked
files are listed (`untracked_files`) before anything runs in it, and
files the runs create (test caches, coverage data) are never taken for
changes. ``compare`` must list them before its first run and pass
them in; the gate lists them itself, before its own run, only when it
is the first thing to run. For each changed ``.py`` file it reads one
``git diff -U0``:

- **Changed lines.** A line the candidate added or changed counts when
  it is part of an executable statement in the candidate, as
  coverage.py's own parser finds them: a continuation line of a
  multi-line statement stands for that statement, which must have run.
  Comments, blank lines and docstrings are not executable and are left
  out. No exclusion pragma is honoured, so ``# pragma: no cover`` does
  not hide a changed line. Every line of a new file counts the same way.
- **Lambdas and generator expressions.** A changed line inside one
  needs its own code to have run, not just the statement that defines
  it: coverage.py's entry arc into the code object that starts at the
  lambda's line. Where another code object starts on the same line, so
  the arc cannot tell them apart, the line cannot be checked.
- **Deleted lines.** A hunk that only removes executable lines leaves
  nothing in the candidate to run, so the gate asks that the block the
  deleted lines were in still ran. The block is the exact statement
  list of the baseline's syntax tree that held them (a body, an
  ``else`` or ``finally`` clause, an ``except`` handler), found in the
  candidate through its header's line, which must be unchanged. The
  site is the nearest remaining statement of that list, the one before
  the deletion or else the one after, and it is reported as the
  unverified line when it did not run. Deleting a whole function so
  asks that a neighbour at module level ran, which importing the module
  does. A block with no statement left, or one that cannot be found,
  cannot be checked. Removed lines that were not executable (comments,
  blank lines, docstrings) ask for nothing.
- **Deleted files** ask for nothing, since nothing of them is left to
  run; they are named in the detail.
- **Unparseable files.** A version that coverage.py's parser or
  perfproof's interpreter cannot parse (newer syntax, a bad encoding)
  means the file cannot be checked.
- **Non-Python changes** are measured only through a coverage report
  (`perfproof.bench.coverage_report`): the spec's
  ``correctness.coverage_command``, run once when such files changed,
  writes lcov or Cobertura to the path in `REPORT_ENV_VAR`. A changed
  line of a file the report lists counts as `OtherChange` says; a line
  the report does not list is not executable. With no coverage command
  declared (nothing is run then), no report, or for a file it does not
  list, the gate reports "coverage not checked", which is
  ``not_available`` unless the spec sets ``allow_unchecked_coverage``,
  and then ``waived``.

A change that cannot be checked makes the gate ``not_available``,
whatever the opt-in says.

How coverage is measured
------------------------
The correctness command runs in the candidate tree with its time limit
multiplied by `COVERAGE_TIMEOUT_FACTOR`, and with the environment the
caller gives plus a ``PYTHONPATH`` entry holding a generated
``sitecustomize.py``. In every Python process the command starts,
children included, that hook starts coverage.py through
``coverage.process_startup``, with branch measurement on, and chains to
any ``sitecustomize`` it shadows. Where the interpreter's environment
has no coverage.py of its own, the hook loads perfproof's by file path.
Where coverage.py's own start-up file (``a1_coverage.pth``, which
perfproof's own environment has) started it first, that copy measures,
with perfproof's configuration. coverage.py runs inside the spec's
interpreter: its C tracer loads when that interpreter is the one the
copy was built for, and otherwise coverage.py falls back to its slower
Python tracer. ``sys.monitoring`` is not used, because it cannot label
lines with the test that ran them. Every ``COVERAGE_*`` variable in the
environment is replaced by ``COVERAGE_PROCESS_START`` naming
perfproof's configuration, so the target's own coverage settings do
not apply. Data perfproof cannot read is reported.

``PYTEST_ADDOPTS`` gets ``-p`` with a generated pytest plugin that, in
the pytest process the command starts, labels the coverage data with
the test that ran each line, by the id the JUnit report gives it
(`perfproof.bench.correctness`), from the start of the test's setup to
the end of its teardown, and records when each test started and ended.
Outside any test the label is `OUTSIDE_TESTS`. The label is also put in
the environment as `CONTEXT_ENV_VAR`. A Python process started or
forked with it labels its lines as a child of that label and records
when it started and exited.

Which runs count
----------------
A changed line is verified when the code it needs ran under a trusted
label:

- a test's label, when ``judged_tests`` holds the test (so it is
  neither quarantined as flaky nor a known failure) and it passed in
  the run under coverage;
- a child of a trusted test's label, when the child started and exited
  within one run of that test. A child that outlived its test (a
  server a session fixture started, say), or one started outside any
  test, counts as unlabelled;
- `OUTSIDE_TESTS` (imports, collection, and other work pytest does
  outside a test), when at least one such test passed;
- no label, when the correctness comparison left no test out of its
  judgement and the run under coverage exited 0. Where any test was
  left out, lines nobody labelled may have run only for those tests,
  so they are unverified.

The run under coverage is not the run the correctness gate judged.
When it disagrees (a judged test failed under coverage, the run timed
out or could not start, a process could not start coverage.py, a data
file was unreadable) and some changed line is unverified, the gate
cannot tell whether the change ran and is ``not_available``, which
blocks KEEP without blaming the candidate. When every changed line ran
under a trusted label, the gate passes whatever else went wrong.

What this cannot check
----------------------
Coverage says a line ran, not that anything inspected its result, and
it works by line:

- a changed default argument or decorator of a function no check calls
  passes, because it runs when the function is defined;
- the untaken half of a one-line conditional expression, or of a
  one-line ``and``/``or``, passes when the line ran;
- a line that starts a lambda counts as the lambda's, so a change
  before the lambda on that line needs the lambda called;
- module-level code that runs while pytest imports test modules counts
  under `OUTSIDE_TESTS`, even when only a quarantined test's module
  imports it.

A test command that starts coverage.py itself (``pytest --cov``)
measures with its own tracer as well, which this gate does not
support; under pytest-xdist every worker's lines count as unlabelled.
Processes killed before they exit save no data. The differential
checks do not run under coverage yet: their replay processes run with
``-S`` and end with ``os._exit``, so the coverage hook cannot reach
them, and lines only they reached count as unverified. The candidate
tree must be the repository's top level, since ``git diff`` names
paths from there.
"""

import ast
import bisect
import json
import os
import re
import shlex
import string
import subprocess
import tempfile
import tokenize
from collections import Counter
from collections.abc import (
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, Literal

import coverage
from coverage.exceptions import CoverageException
from coverage.parser import PythonParser
from pydantic import ValidationError

from perfproof.bench.correctness import (
    EXIT_STATUS_ID,
    SuiteComparison,
    SuiteRun,
    run_command,
    run_tests,
)
from perfproof.bench.coverage_report import (
    REPORT_ENV_VAR,
    ReportError,
    read_report,
)
from perfproof.bench.snapshot import SnapshotError, worktree_commit
from perfproof.schema import BenchmarkSpec, GateResult, UnverifiedLines

__all__ = [
    "ALL_JUDGED",
    "CHILD_LABEL_PREFIX",
    "CONTEXT_ENV_VAR",
    "COVERAGE_TIMEOUT_FACTOR",
    "GIT_TIMEOUT_SECONDS",
    "OUTSIDE_TESTS",
    "TEST_LABEL_PREFIX",
    "ChangedLines",
    "CoverageGateError",
    "Deletion",
    "FileChange",
    "Need",
    "OtherChange",
    "changed_lines",
    "coverage_gate",
    "untracked_files",
]

ALL_JUDGED: Final = "all"
"""Value of ``judged_tests`` for a caller that has no ids: every test
the run under coverage reports counts, except those left out by the
`SuiteComparison` (quarantined, known failures)."""
COVERAGE_TIMEOUT_FACTOR: Final = 4.0
"""The correctness command may take this many times its time limit
under coverage, since tracing every line slows it down."""
GIT_TIMEOUT_SECONDS: Final = 60.0
"""Time limit for one git query, in seconds."""
CONTEXT_ENV_VAR: Final = "PERFPROOF_COVERAGE_CONTEXT"
"""Environment variable holding the label of the running test, so a
Python process a test starts records its lines as that test's child."""
OUTSIDE_TESTS: Final = "<pytest>"
"""Label of lines pytest ran outside any test: imports, collection."""
TEST_LABEL_PREFIX: Final = "test "
"""Prefix of a test's label; the rest is its JUnit report id."""
CHILD_LABEL_PREFIX: Final = "child "
"""Prefix of a child process's label: ``child <token> <label>``, where
``<label>`` is the label it was started with."""

_PLUGIN: Final = "_perfproof_coverage_labels"
_UNNAMED_PREFIX: Final = "unnamed "
_HUNK: Final = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_HEADER: Final = b"diff --git "
_LINE_BREAK: Final = re.compile(r"\r\n|\r|\n")
_CHUNK_BYTES: Final = 1 << 20
_FAILING: Final = frozenset({"failed", "error"})
_ERROR_CHARS: Final = 300
_TAIL_CHARS: Final = 500
_ESCAPES: Final = {
    ord("a"): 7,
    ord("b"): 8,
    ord("t"): 9,
    ord("n"): 10,
    ord("v"): 11,
    ord("f"): 12,
    ord("r"): 13,
    ord('"'): ord('"'),
    ord("\\"): ord("\\"),
}
# Nodes compiled to a code object of their own. Comprehensions are
# inlined from Python 3.12 but have their own code object before it.
_CODE_NODES: Final = (
    ast.Lambda,
    ast.GeneratorExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)
_STATEMENT_LISTS: Final = ("body", "orelse", "finalbody")

_SITECUSTOMIZE: Final = string.Template('''\
"""perfproof coverage gate hook, for the coverage run only."""


def _perfproof_coverage():
    import atexit
    import importlib.machinery
    import importlib.util
    import json
    import os
    import sys
    import time
    import traceback

    here = os.path.dirname(os.path.abspath(__file__))
    try:
        module = sys.modules.get("coverage")
        if module is None:
            spec = importlib.util.spec_from_file_location(
                "coverage", $init, submodule_search_locations=[$package]
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules["coverage"] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                del sys.modules["coverage"]
                raise
        # coverage.py's own start-up file (a1_coverage.pth) may have
        # started it already, before this file ran.
        module.process_startup()
        state = {"token": None, "start": 0}

        def label_child():
            current = module.Coverage.current()
            inherited = os.environ.get($var)
            if current is None or not inherited:
                return
            state["token"] = "%d-%s" % (os.getpid(), os.urandom(6).hex())
            state["start"] = time.monotonic_ns()
            current.switch_context($child + state["token"] + " " + inherited)

        def record_exit():
            if state["token"] is None:
                return
            path = os.path.join($records, "child-" + state["token"] + ".json")
            exited = time.monotonic_ns()
            with open(path, "w") as f:
                json.dump({"start": state["start"], "exit": exited}, f)
            state["token"] = None

        if module.Coverage.current() is not None:
            label_child()
            # Registered after coverage.py's own fork handler, which
            # restarts it in the child without a label.
            os.register_at_fork(after_in_child=label_child)
            atexit.register(record_exit)
            real_exit = os._exit

            def exit_recorded(status):
                try:
                    record_exit()
                finally:
                    real_exit(status)

            os._exit = exit_recorded
    except BaseException:
        path = os.path.join($errors, "hook-error-%d.txt" % os.getpid())
        with open(path, "w") as f:
            traceback.print_exc(file=f)
    rest = [p for p in sys.path if os.path.abspath(p or ".") != here]
    found = importlib.machinery.PathFinder.find_spec("sitecustomize", rest)
    if found is not None and found.loader is not None:
        found.loader.exec_module(importlib.util.module_from_spec(found))


_perfproof_coverage()
''')

_PLUGIN_SOURCE: Final = string.Template('''\
"""perfproof coverage gate: label coverage data with the running test."""

import json
import os
import sys
import time

# Only the pytest process the correctness command starts labels tests;
# a pytest started inside a test is that test's child.
_ACTIVE = not os.environ.get($var)
_configs = []
_started = {}
_log = []


def _switch(label):
    module = sys.modules.get("coverage")
    cov = None if module is None else module.Coverage.current()
    if not _ACTIVE or cov is None:
        return
    cov.switch_context(label)
    os.environ[$var] = label


def _junit_id(nodeid):
    try:
        from _pytest.junitxml import mangle_test_address
    except ImportError:
        return $unnamed + nodeid
    names = mangle_test_address(nodeid)
    classnames = names[:-1]
    prefix = _configs[0].getoption("junitprefix", None) if _configs else None
    if prefix:
        classnames.insert(0, prefix)
    classname = ".".join(classnames)
    return classname + "::" + names[-1] if classname else names[-1]


def pytest_configure(config):
    _configs.append(config)
    if _ACTIVE:
        path = os.path.join($records, "tests-%d.jsonl" % os.getpid())
        _log.append(open(path, "a"))


def pytest_runtest_logstart(nodeid, location):
    _started[nodeid] = time.monotonic_ns()
    _switch($test + _junit_id(nodeid))


def pytest_runtest_logfinish(nodeid, location):
    _switch($outside)
    if _log:
        start = _started.pop(nodeid, 0)
        end = time.monotonic_ns()
        record = {"id": _junit_id(nodeid), "start": start, "end": end}
        _log[0].write(json.dumps(record) + "\\n")
        _log[0].flush()


def pytest_unconfigure(config):
    while _log:
        _log.pop().close()


_switch($outside)
''')


class CoverageGateError(RuntimeError):
    """The gate could not run (git failed); no version is to blame."""


@dataclass(frozen=True, slots=True)
class Need:
    """What must have run for one changed line to count as verified.

    Attributes
    ----------
    line : int
        The line reported when it did not run, 1-based in the
        candidate.
    kind : {"statement", "entry", "unchecked"}
        ``statement``: the statement starting at ``target`` ran.
        ``entry``: the code object starting at line ``target`` (a
        lambda or generator expression) was entered. ``unchecked``:
        line coverage cannot tell, for the reason in ``why``.
    target : int
        The statement's first line or the code object's first line;
        ``line`` itself for ``unchecked``.
    why : str
        Why it cannot be checked; empty unless ``unchecked``.
    """

    line: int
    kind: Literal["statement", "entry", "unchecked"]
    target: int
    why: str = ""


@dataclass(frozen=True, slots=True)
class Deletion:
    """Executable baseline lines a hunk removed, and where they were.

    Attributes
    ----------
    baseline_lines : tuple of int
        The removed executable lines, 1-based in the baseline, sorted.
    needs : tuple of Need
        What must have run in the candidate: the nearest remaining
        statement of each block the lines were in, or why that block
        cannot be checked.
    """

    baseline_lines: tuple[int, ...]
    needs: tuple[Need, ...]


@dataclass(frozen=True, slots=True)
class FileChange:
    """The changed executable lines of one Python file.

    Attributes
    ----------
    path : str
        Source file, relative to the tree root, with ``/`` separators.
    needs : tuple of Need
        One per changed executable line of the candidate, sorted.
    deletions : tuple of Deletion
        Hunks that only removed executable lines.
    unparsed : str or None
        Why a version could not be parsed, so the file cannot be
        checked; ``None`` when both parsed.
    """

    path: str
    needs: tuple[Need, ...]
    deletions: tuple[Deletion, ...]
    unparsed: str | None


@dataclass(frozen=True, slots=True)
class OtherChange:
    """A changed non-Python file that still exists.

    Its changed lines are found only when a coverage report lists it
    (`_other_lines`), so a large data file is never read for nothing.

    Attributes
    ----------
    path : str
        File, relative to the tree root, with ``/`` separators.
    added : bool
        Whether the file is new in the candidate (every line counts),
        rather than modified (the lines ``git diff`` adds or changes).
    """

    path: str
    added: bool


@dataclass(frozen=True, slots=True)
class ChangedLines:
    """What the candidate changed, as the coverage gate sees it.

    Attributes
    ----------
    files : tuple of FileChange
        Python files with changed or deleted executable lines, sorted
        by path.
    not_checked : tuple of str
        Changed paths that are not Python, tracked or untracked,
        sorted; coverage is not checked for them.
    deleted : tuple of str
        Python files the candidate deleted, sorted.
    other : tuple of OtherChange
        Each path in ``not_checked`` that still exists, for the
        coverage report to judge; sorted by path.
    """

    files: tuple[FileChange, ...]
    not_checked: tuple[str, ...]
    deleted: tuple[str, ...]
    other: tuple[OtherChange, ...] = ()


class _Source:
    """One version of a file: its statements, blocks and code objects.

    ``unparsed`` says why the file could not be parsed, by coverage.py's
    parser or by this interpreter's; the rest is then empty.
    """

    def __init__(self, path: Path) -> None:
        self.unparsed: str | None = None
        self.statements: list[int] = []
        self._statement_set: frozenset[int] = frozenset()
        self._first_lines: Mapping[int, int] = {}
        self._tree: ast.Module | None = None
        self._line_count = 0
        try:
            with tokenize.open(path) as stream:
                text = stream.read()
            # Only these end a line in Python source; str.splitlines
            # also splits on form feeds and Unicode separators.
            self._line_count = len(_LINE_BREAK.split(text.removesuffix("\n")))
            tree = ast.parse(text, filename=str(path))
            parser = PythonParser(text=text, filename=str(path))
            parser.parse_source()
            statements = sorted(parser.statements)
            first_lines = {
                n: parser.first_line(n) for n in range(1, self._line_count + 1)
            }
        # coverage.parser is coverage.py's internal API, so any error
        # from it, or from parsing at all, leaves the file unchecked.
        except Exception as exc:
            self.unparsed = f"could not be parsed: {type(exc).__name__}: {exc}"
            return
        self.statements = statements
        self._statement_set = frozenset(statements)
        self._first_lines = first_lines
        self._tree = tree
        self._code_spans = self._find_code_spans(tree)

    @staticmethod
    def _find_code_spans(
        tree: ast.Module,
    ) -> list[tuple[int, int, int, bool]]:
        """List lambdas and generator expressions, innermost last.

        Each is ``(first line, last line, code start, ambiguous)``,
        where ``ambiguous`` says another code object starts on the same
        line, module included.
        """
        starts: Counter[int] = Counter({1: 1})
        spans: list[tuple[int, int, int]] = []
        for node in ast.walk(tree):
            if not isinstance(node, _CODE_NODES):
                continue
            start = (
                _first_line(node)
                if isinstance(node, ast.stmt)
                else node.lineno
            )
            starts[start] += 1
            if isinstance(node, ast.Lambda | ast.GeneratorExp):
                end = node.end_lineno or node.lineno
                spans.append((node.lineno, end, start))
        spans.sort(key=lambda s: (s[0], -s[1]))
        return [(a, b, c, starts[c] > 1) for a, b, c in spans]

    @property
    def line_count(self) -> int:
        """Number of lines in the file."""
        return self._line_count

    def first_line(self, line: int) -> int:
        """Return the first line of the statement holding ``line``."""
        return self._first_lines.get(line, line)

    def executable(self, line: int) -> bool:
        """Whether ``line`` is part of an executable statement."""
        return self.first_line(line) in self._statement_set

    def need(self, line: int) -> Need | None:
        """Say what must run for a changed ``line``, if anything.

        A line inside a lambda or generator expression needs its
        innermost one entered.
        """
        if not self.executable(line):
            return None
        inner = None
        for span in self._code_spans:
            if span[0] > line:
                break
            if span[0] <= line <= span[1]:
                inner = span
        if inner is None:
            return Need(line, "statement", self.first_line(line))
        if inner[3]:
            why = (
                f"line {line} is in a lambda or generator expression whose "
                "first line starts another code object too"
            )
            return Need(line, "unchecked", line, why)
        return Need(line, "entry", inner[2])

    def block(self, line: int) -> tuple[ast.AST, str] | None:
        """Return the owner and field of the list holding ``line``.

        That is the innermost ``body``, ``orelse`` or ``finalbody``
        (of a statement, an ``except`` handler or a ``case``) holding a
        statement that spans ``line``; ``None`` when no statement does.
        """
        if self._tree is None:
            return None
        found: tuple[ast.AST, str] | None = None
        owner: ast.AST = self._tree
        field = "body"
        while True:
            stmts: list[ast.stmt] = getattr(owner, field)
            spanning = [
                s
                for s in stmts
                if _first_line(s) <= line <= (s.end_lineno or s.lineno)
            ]
            if not spanning:
                return found
            found = (owner, field)
            inner = _inner_list(spanning[0], line)
            if inner is None:
                return found
            owner, field = inner

    def node_at(self, kind: type[ast.AST], line: int) -> ast.AST | None:
        """Find the ``kind`` node whose header is on ``line``."""
        if self._tree is None:
            return None
        if kind is ast.Module:
            return self._tree
        for node in ast.walk(self._tree):
            if type(node) is kind and _header_line(node) == line:
                return node
        return None

    def site(self, stmts: Sequence[ast.stmt], anchor: int) -> int | None:
        """Pick the statement of ``stmts`` nearest a gap at ``anchor``.

        The last executable one at or before ``anchor``, else the first
        after it; ``None`` when none is executable.
        """
        lines = [s.lineno for s in stmts if s.lineno in self._statement_set]
        before = bisect.bisect_right(lines, anchor)
        if before:
            return lines[before - 1]
        return lines[0] if lines else None


def _first_line(stmt: ast.stmt) -> int:
    """Return a statement's first line, its decorators included."""
    decorators: list[ast.expr] = getattr(stmt, "decorator_list", [])
    return min([stmt.lineno, *(d.lineno for d in decorators)])


def _header_line(node: ast.AST) -> int | None:
    """Return the line a block owner starts on, as the AST gives it."""
    if isinstance(node, ast.match_case):
        return node.pattern.lineno
    return getattr(node, "lineno", None)


def _inner_list(stmt: ast.stmt, line: int) -> tuple[ast.AST, str] | None:
    """Return the statement list inside ``stmt`` that spans ``line``."""
    owners: list[ast.AST] = [stmt]
    owners.extend(getattr(stmt, "handlers", ()))
    owners.extend(getattr(stmt, "cases", ()))
    for owner in owners:
        for field in _STATEMENT_LISTS:
            stmts: list[ast.stmt] | None = getattr(owner, field, None)
            if not stmts:
                continue
            first = stmts[0].lineno
            last = stmts[-1].end_lineno or stmts[-1].lineno
            if first <= line <= last:
                return owner, field
    return None


def _git(cwd: Path, *args: str, timeout: float, literal: bool = True) -> bytes:
    """Run git in ``cwd`` and return its raw stdout.

    ``literal`` turns pathspec magic off.

    Raises
    ------
    CoverageGateError
        If git exits non-zero, cannot be started, or times out.
    """
    argv = [
        "git",
        *(["--literal-pathspecs"] if literal else []),
        "-c",
        "core.quotePath=false",
        "-C",
        str(cwd),
        *args,
    ]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"{shlex.join(argv)} failed: {exc}"
        raise CoverageGateError(msg) from exc
    if done.returncode != 0:
        err = done.stderr.decode(errors="replace").strip()
        msg = f"{shlex.join(argv)} exited {done.returncode}: {err}"
        raise CoverageGateError(msg)
    return done.stdout


def _unquote(data: bytes) -> tuple[bytes, int]:
    """Decode the C-style quoted string git wrote at ``data``'s start.

    Returns the bytes and the index just past the closing quote.

    Raises
    ------
    CoverageGateError
        If the string does not end.
    """
    out = bytearray()
    i = 1
    while i < len(data):
        byte = data[i]
        if byte == ord('"'):
            return bytes(out), i + 1
        if byte == ord("\\") and i + 1 < len(data):
            nxt = data[i + 1]
            if ord("0") <= nxt <= ord("7"):
                out.append(int(data[i + 1 : i + 4], 8))
                i += 4
                continue
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(byte)
        i += 1
    msg = f"unterminated path in git diff header: {data!r}"
    raise CoverageGateError(msg)


def _header_path(header: bytes) -> str:
    """Return the path a ``diff --git a/P b/P`` header names."""
    rest = header[len(_DIFF_HEADER) :]
    if rest.startswith(b'"'):
        path, _ = _unquote(rest)
        return os.fsdecode(path[2:])
    # Without renames both sides are the same path: "a/P b/P".
    return os.fsdecode(rest[2 : (len(rest) - 1) // 2])


def _split_patch(patch: bytes) -> dict[str, bytes]:
    """Split one ``git diff`` output into each file's part."""
    parts: dict[str, list[bytes]] = {}
    current: list[bytes] | None = None
    for raw in patch.splitlines(keepends=True):
        if raw.startswith(_DIFF_HEADER):
            current = parts.setdefault(_header_path(raw.rstrip(b"\n")), [])
        if current is not None:
            current.append(raw)
    return {path: b"".join(lines) for path, lines in parts.items()}


def _hunks(patch: bytes) -> Iterator[tuple[range, range]]:
    """Yield the baseline and candidate line ranges of ``-U0`` hunks.

    A side with no lines is an empty range starting at the line the
    change follows, as git numbers it.
    """
    for raw in patch.splitlines():
        match = _HUNK.match(raw)
        if match is None:
            continue
        a, b, c, d = match.groups()
        old_start, new_start = int(a), int(c)
        old_len = 1 if b is None else int(b)
        new_len = 1 if d is None else int(d)
        yield (
            range(old_start, old_start + old_len),
            range(new_start, new_start + new_len),
        )


def _ranges(lines: Iterable[int]) -> str:
    """Spell line numbers as ``3, 5-7``."""
    spans: list[str] = []
    start = prev = -2
    for line in sorted(set(lines)):
        if line != prev + 1:
            if start >= 0:
                spans.append(
                    str(start) if start == prev else f"{start}-{prev}"
                )
            start = line
        prev = line
    if start >= 0:
        spans.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(spans)


def untracked_files(
    candidate: Path, *, timeout: float = GIT_TIMEOUT_SECONDS
) -> tuple[str, ...]:
    """List the candidate's untracked files that git does not ignore.

    Call it before anything runs in the candidate tree: the files the
    runs create (test caches, coverage data) are then never taken for
    the candidate's changes. Python bytecode that Python validates
    against its source (a ``.pyc`` directly in a ``__pycache__`` whose
    header is timestamp-based or checked-hash) is never listed, run
    before or after: the interpreter builds it from source, so a tree
    that does not ignore it is not changed by it. Anything else there
    is listed.

    Parameters
    ----------
    candidate : Path
        Root of the candidate's working tree, the repository's top
        level.
    timeout : float, default `GIT_TIMEOUT_SECONDS`
        Time limit for the git query, in seconds.

    Returns
    -------
    tuple of str
        Paths relative to the tree root, with ``/`` separators.

    Raises
    ------
    CoverageGateError
        If git fails.
    """
    listing = _git(
        candidate,
        "ls-files",
        "-z",
        "--others",
        "--exclude-standard",
        "--full-name",
        timeout=timeout,
    ).split(b"\0")
    return tuple(
        name
        for name in (os.fsdecode(path) for path in listing if path)
        if not _is_validated_bytecode(candidate, name)
    )


_PYC_HEADER_BYTES: Final = 8
"""Bytes of a ``.pyc`` that hold its magic number and flags."""
_PYC_HASH_BASED: Final = 0b01
_PYC_CHECK_SOURCE: Final = 0b10


def _is_validated_bytecode(root: Path, name: str) -> bool:
    """Say whether an untracked file is bytecode Python checks.

    Only a ``.pyc`` directly in a ``__pycache__`` whose header says it
    is timestamp-based or checked-hash qualifies: Python compares both
    with the source it was built from, so it cannot carry a change the
    source does not. Any other file there (a ``.py`` module, data), an
    unchecked-hash ``.pyc`` and a file whose header cannot be read
    stay changes.
    """
    parts = name.split("/")
    if len(parts) < 2 or parts[-2] != "__pycache__":
        return False
    if not parts[-1].endswith(".pyc"):
        return False
    try:
        with (root / name).open("rb") as handle:
            header = handle.read(_PYC_HEADER_BYTES)
    except OSError:
        return False
    if len(header) < _PYC_HEADER_BYTES or header[2:4] != b"\r\n":
        return False
    flags = int.from_bytes(header[4:8], "little")
    if flags & ~(_PYC_HASH_BASED | _PYC_CHECK_SOURCE):
        return False
    return not flags & _PYC_HASH_BASED or bool(flags & _PYC_CHECK_SOURCE)


def changed_lines(
    baseline: Path,
    candidate: Path,
    *,
    untracked: Collection[str] | None = None,
    outputs: Collection[str] = (),
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> ChangedLines:
    """List the executable lines the candidate changed or deleted.

    Parameters
    ----------
    baseline : Path
        Root of the baseline worktree, checked out at the snapshot
        commit.
    candidate : Path
        Root of the candidate's working tree, the top level of the same
        repository. Uncommitted changes count, and so do untracked
        files git does not ignore.
    untracked : collection of str, optional
        The candidate's untracked files, from `untracked_files` called
        before anything ran in the tree; listed now if not given, which
        takes files earlier runs created for changes.
    outputs : collection of str, optional
        The spec's declared outputs, relative to the tree; left out
        when untracked, since the benchmark writes them.
    timeout : float, default `GIT_TIMEOUT_SECONDS`
        Time limit for each git query, in seconds.

    Returns
    -------
    ChangedLines
        The changed Python lines, as the module docstring defines them,
        the non-Python paths and the deleted Python files.

    Raises
    ------
    CoverageGateError
        If git fails or the baseline is not a git worktree.
    """
    try:
        commit = worktree_commit(baseline)
    except SnapshotError as exc:
        raise CoverageGateError(str(exc)) from exc
    listing = _git(
        candidate,
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        commit,
        "--",
        timeout=timeout,
    ).split(b"\0")
    entries = [
        (listing[i].decode(), os.fsdecode(listing[i + 1]))
        for i in range(0, len(listing) - 1, 2)
    ]
    if untracked is None:
        untracked = untracked_files(candidate, timeout=timeout)
    declared = set(outputs)
    entries.extend(("A", path) for path in untracked if path not in declared)
    modified = [
        p for s, p in entries if p.endswith(".py") and s not in ("A", "D")
    ]
    patches: dict[str, bytes] = {}
    if modified:
        patches = _split_patch(
            _git(
                candidate,
                "diff",
                "-U0",
                "--text",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                commit,
                "--",
                ":(glob)**/*.py",
                timeout=timeout,
                literal=False,
            )
        )
    files: list[FileChange] = []
    not_checked: list[str] = []
    deleted: list[str] = []
    other: list[OtherChange] = []
    for status, path in sorted(entries, key=lambda e: e[1]):
        if not path.endswith(".py"):
            not_checked.append(path)
            if status == "D":
                continue
            other.append(OtherChange(path, status == "A"))
        elif status == "D":
            deleted.append(path)
        elif status == "A":
            files.append(_new_file(candidate, path))
        else:
            files.append(
                _changed_file(
                    baseline, candidate, path, patches.get(path, b"")
                )
            )
    return ChangedLines(
        files=tuple(f for f in files if f.needs or f.deletions or f.unparsed),
        not_checked=tuple(not_checked),
        deleted=tuple(deleted),
        other=tuple(other),
    )


def _other_lines(
    baseline: Path,
    candidate: Path,
    changes: Collection[OtherChange],
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> dict[str, tuple[int, ...]]:
    """Find the changed lines of non-Python files, for these files only.

    A new file counts every line, found by counting newlines in
    fixed-size chunks; a modified one counts the lines one ``git diff
    -U0`` over the listed files adds or changes.

    Raises
    ------
    CoverageGateError
        If git fails.
    """
    found: dict[str, tuple[int, ...]] = {}
    modified = [c.path for c in changes if not c.added]
    patches: dict[str, bytes] = {}
    if modified:
        try:
            commit = worktree_commit(baseline)
        except SnapshotError as exc:
            raise CoverageGateError(str(exc)) from exc
        patches = _split_patch(
            _git(
                candidate,
                "diff",
                "-U0",
                "--text",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--no-renames",
                "--src-prefix=a/",
                "--dst-prefix=b/",
                commit,
                "--",
                *modified,
                timeout=timeout,
            )
        )
    for change in changes:
        if change.added:
            found[change.path] = _count_lines(candidate / change.path)
        else:
            found[change.path] = tuple(
                n
                for _, added in _hunks(patches.get(change.path, b""))
                for n in added
            )
    return found


def _count_lines(path: Path) -> tuple[int, ...]:
    """List every line number of a file, reading it in chunks."""
    count = 0
    last = b"\n"
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK_BYTES):
                count += chunk.count(b"\n")
                last = chunk[-1:]
    except OSError:
        return ()
    if last != b"\n":
        count += 1
    return tuple(range(1, count + 1))


def _new_file(candidate: Path, path: str) -> FileChange:
    """Count every executable line of a file new in the candidate."""
    source = _Source(candidate / path)
    if source.unparsed is not None:
        return FileChange(path, (), (), f"the candidate {source.unparsed}")
    needs = [source.need(n) for n in range(1, source.line_count + 1)]
    return FileChange(path, tuple(n for n in needs if n), (), None)


def _changed_file(
    baseline: Path, candidate: Path, path: str, patch: bytes
) -> FileChange:
    """Map one file's hunks to changed lines and deletions."""
    new = _Source(candidate / path)
    old = _Source(baseline / path)
    for arm, source in (("candidate", new), ("baseline", old)):
        if source.unparsed is not None:
            return FileChange(path, (), (), f"the {arm} {source.unparsed}")
    hunks = list(_hunks(patch))
    needs: dict[int, Need] = {}
    deletions: list[Deletion] = []
    for gone_from, added in hunks:
        changed = [n for n in (new.need(line) for line in added) if n]
        for need in changed:
            needs[need.line] = need
        if changed:
            continue
        gone = [n for n in gone_from if old.executable(n)]
        if gone:
            anchor = added.start - 1 if len(added) else added.start
            sited = _site_deletion(old, new, hunks, gone_from, anchor)
            deletions.append(Deletion(tuple(gone), sited))
    return FileChange(
        path, tuple(needs[n] for n in sorted(needs)), tuple(deletions), None
    )


def _to_candidate(hunks: Sequence[tuple[range, range]], line: int) -> int:
    """Map an unchanged baseline line into the candidate; -1 if not."""
    shift = 0
    for old, new in hunks:
        end = old.stop if len(old) else old.start + 1
        if end <= line:
            shift += len(new) - len(old)
        elif len(old) and old.start <= line:
            return -1
    return line + shift


def _site_deletion(
    old: _Source,
    new: _Source,
    hunks: Sequence[tuple[range, range]],
    removed: range,
    anchor: int,
) -> tuple[Need, ...]:
    """Find, for each block the deleted lines were in, what must run.

    A block whose header was removed with it went as a whole, so only
    the block that held its header counts.
    """
    needs: dict[int, Need] = {}
    seen: set[tuple[int, str]] = set()
    for line in removed:
        if not old.executable(line):
            continue
        found = old.block(line)
        if found is None:
            why = f"no block of the baseline holds deleted line {line}"
            needs[-line] = Need(anchor, "unchecked", anchor, why)
            continue
        owner, field = found
        header = _header_line(owner)
        if (id(owner), field) in seen or (
            header is not None and header in removed
        ):
            continue
        seen.add((id(owner), field))
        need = _site_in_candidate(new, hunks, owner, field, anchor)
        needs.setdefault(
            need.target if need.kind == "statement" else -line, need
        )
    return tuple(needs.values())


def _site_in_candidate(
    new: _Source,
    hunks: Sequence[tuple[range, range]],
    owner: ast.AST,
    field: str,
    anchor: int,
) -> Need:
    """Find the remaining statement of one block in the candidate."""
    if isinstance(owner, ast.Module):
        target: ast.AST | None = new.node_at(ast.Module, 0)
    else:
        header = _header_line(owner)
        mapped = -1 if header is None else _to_candidate(hunks, header)
        target = None if mapped < 0 else new.node_at(type(owner), mapped)
    where = f"the {field} of the block at candidate line {anchor}"
    if target is None:
        why = f"{where} changed its header, so it cannot be found"
        return Need(anchor, "unchecked", anchor, why)
    site = new.site(getattr(target, field), anchor)
    if site is None:
        why = f"no statement is left in {where} to show it ran"
        return Need(anchor, "unchecked", anchor, why)
    return Need(site, "statement", site)


def _hook_files(work: Path) -> tuple[Path, Path, Path, Path]:
    """Write the hook, the plugin and the coverage.py configuration.

    Returns the hook directory, the configuration file, and the
    directories hook errors and process records go to.
    """
    hook, data = work / "hook", work / "data"
    errors, records = work / "errors", work / "records"
    for directory in (hook, data, errors, records):
        directory.mkdir()
    package = Path(coverage.__file__).resolve().parent
    (hook / "sitecustomize.py").write_text(
        _SITECUSTOMIZE.substitute(
            init=repr(str(package / "__init__.py")),
            package=repr(str(package)),
            var=repr(CONTEXT_ENV_VAR),
            child=repr(CHILD_LABEL_PREFIX),
            errors=repr(str(errors)),
            records=repr(str(records)),
        ),
        encoding="utf-8",
    )
    (hook / f"{_PLUGIN}.py").write_text(
        _PLUGIN_SOURCE.substitute(
            var=repr(CONTEXT_ENV_VAR),
            unnamed=repr(_UNNAMED_PREFIX),
            test=repr(TEST_LABEL_PREFIX),
            outside=repr(OUTSIDE_TESTS),
            records=repr(str(records)),
        ),
        encoding="utf-8",
    )
    # coverage.py expands $VAR in configuration values; $$ is a $.
    data_file = str(data / "cov").replace("$", "$$")
    rcfile = work / "coveragerc"
    rcfile.write_text(
        "[run]\n"
        f"data_file = {data_file}\n"
        "parallel = True\n"
        "branch = True\n"
        "core = ctrace\n"
        "patch =\n    _exit\n    fork\n",
        encoding="utf-8",
    )
    return hook, rcfile, errors, records


def _covered_env(
    env: Mapping[str, str],
    spec: BenchmarkSpec,
    hook: Path,
    rcfile: Path,
) -> dict[str, str]:
    """Build the run's environment: the caller's, the spec's, a hook."""
    merged = {
        name: value
        for name, value in {**env, **spec.env}.items()
        if not name.startswith("COVERAGE_") and name != CONTEXT_ENV_VAR
    }
    existing = merged.get("PYTHONPATH")
    merged["PYTHONPATH"] = (
        str(hook) if not existing else f"{hook}{os.pathsep}{existing}"
    )
    addopts = merged.get("PYTEST_ADDOPTS", "")
    merged["PYTEST_ADDOPTS"] = f"{addopts} -p {_PLUGIN}".strip()
    merged["COVERAGE_PROCESS_START"] = str(rcfile)
    return merged


@dataclass(frozen=True, slots=True)
class _Records:
    """When each test ran and each labelled child lived, in ns.

    Times are ``time.monotonic_ns`` of the processes, which share the
    system's monotonic clock.
    """

    tests: Mapping[str, tuple[tuple[int, int], ...]]
    children: Mapping[str, tuple[int, int]]


@dataclass(frozen=True, slots=True)
class _Measurement:
    """The correctness command's run under coverage.

    Attributes
    ----------
    run : SuiteRun
        Its per-test results and output.
    lines : mapping of str to mapping of int to frozenset of str
        For each changed file, each executed line's labels, keyed by
        the first line of its statement.
    entries : mapping of str to mapping of int to frozenset of str
        For each changed file, the labels under which the code object
        starting at each line was entered.
    records : _Records
        Test and child process times.
    problems : tuple of str
        Anything that may have kept a line from being recorded.
    """

    run: SuiteRun
    lines: Mapping[str, Mapping[int, frozenset[str]]]
    entries: Mapping[str, Mapping[int, frozenset[str]]]
    records: _Records
    problems: tuple[str, ...]


def _measure(
    spec: BenchmarkSpec,
    candidate_root: Path,
    env: Mapping[str, str],
    sources: Mapping[str, tuple[_Source, frozenset[int]]],
    timeout: float,
) -> _Measurement:
    """Run the correctness command under coverage and read the data.

    ``sources`` maps each changed file to its parsed candidate and the
    code-object start lines whose entry matters; ``timeout`` is the
    run's time limit, in seconds.

    Raises
    ------
    OSError
        If the process cannot be created for a reason other than the
        command or its working directory.
    """
    covered = spec.model_copy(
        update={
            "env": {},
            "correctness": spec.correctness.model_copy(
                update={"timeout_seconds": timeout}
            ),
        }
    )
    with tempfile.TemporaryDirectory(prefix="perfproof-coverage-") as tmp:
        work = Path(tmp)
        hook, rcfile, errors, records = _hook_files(work)
        run = run_tests(
            covered, candidate_root, _covered_env(env, spec, hook, rcfile)
        )
        wanted = {
            os.path.realpath(candidate_root / path): path for path in sources
        }
        lines, entries, problems = _read_data(work / "data", wanted, sources)
        problems.extend(_hook_errors(errors))
        times, more = _read_records(records)
        problems.extend(more)
    return _Measurement(run, lines, entries, times, tuple(problems))


type _Labels = dict[str, dict[int, set[str]]]


def _read_data(
    data_dir: Path,
    wanted: Mapping[str, str],
    sources: Mapping[str, tuple[_Source, frozenset[int]]],
) -> tuple[
    dict[str, dict[int, frozenset[str]]],
    dict[str, dict[int, frozenset[str]]],
    list[str],
]:
    """Collect each changed file's executed lines and code entries.

    Lines are keyed by the first line of their statement. Entries need
    one query per label for each file that has any, linear in the
    labels. A data file that cannot be read is reported and skipped.
    """
    lines: _Labels = {}
    entries: _Labels = {}
    problems: list[str] = []
    for file in sorted(data_dir.iterdir()):
        try:
            _read_one(file, wanted, sources, lines, entries)
        except CoverageException as exc:
            problems.append(f"coverage data {file.name} is unreadable: {exc}")
    return _freeze(lines), _freeze(entries), problems


def _read_one(
    file: Path,
    wanted: Mapping[str, str],
    sources: Mapping[str, tuple[_Source, frozenset[int]]],
    lines: _Labels,
    entries: _Labels,
) -> None:
    """Add one data file's lines and entries to the collection.

    Raises
    ------
    coverage.exceptions.CoverageException
        If the file cannot be read.
    """
    data = coverage.CoverageData(basename=str(file))
    data.read()
    for name in data.measured_files():
        path = wanted.get(os.path.realpath(name))
        if path is None:
            continue
        source, starts = sources[path]
        into = lines.setdefault(path, {})
        for line, labels in data.contexts_by_lineno(name).items():
            into.setdefault(source.first_line(line), set()).update(labels)
        if not starts:
            continue
        found = entries.setdefault(path, {})
        for label in sorted(data.measured_contexts()):
            data.set_query_context(label)
            for start, _ in data.arcs(name) or ():
                if -start in starts:
                    found.setdefault(-start, set()).add(label)
        data.set_query_contexts(None)


def _freeze(
    found: Mapping[str, Mapping[int, set[str]]],
) -> dict[str, dict[int, frozenset[str]]]:
    """Make collected labels immutable."""
    return {
        path: {line: frozenset(labels) for line, labels in lines.items()}
        for path, lines in found.items()
    }


def _hook_errors(errors: Path) -> list[str]:
    """Report processes in which coverage.py could not start."""
    problems: list[str] = []
    for file in sorted(errors.iterdir()):
        lines = file.read_text(errors="replace").strip().splitlines()
        last = lines[-1] if lines else "no traceback"
        problems.append(
            "coverage.py could not start in a process: " + last[-_ERROR_CHARS:]
        )
    return problems


def _read_records(records: Path) -> tuple[_Records, list[str]]:
    """Read when each test ran and each labelled child lived."""
    tests: dict[str, list[tuple[int, int]]] = {}
    children: dict[str, tuple[int, int]] = {}
    problems: list[str] = []
    for file in sorted(records.iterdir()):
        try:
            text = file.read_text(encoding="utf-8")
            if file.name.startswith("child-"):
                record = json.loads(text)
                token = file.name.removeprefix("child-").removesuffix(".json")
                children[token] = (int(record["start"]), int(record["exit"]))
                continue
            for raw in text.splitlines():
                record = json.loads(raw)
                tests.setdefault(str(record["id"]), []).append(
                    (int(record["start"]), int(record["end"]))
                )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            problems.append(f"process record {file.name} is unreadable: {exc}")
    frozen = {t: tuple(spans) for t, spans in tests.items()}
    return _Records(frozen, children), problems


def _trust(
    run: SuiteRun,
    tests: SuiteComparison,
    judged_tests: Collection[str],
    records: _Records,
) -> tuple[Callable[[str], bool], list[str]]:
    """Decide which labels count as a passing, judged check.

    Returns the predicate and the judged tests that did not pass under
    coverage, which the correctness gate saw pass.
    """
    excluded = {q.test_id for q in tests.quarantined} | set(
        tests.known_failures
    )
    judged = {
        t: r
        for t, r in run.results.items()
        if (judged_tests == ALL_JUDGED or t in judged_tests)
        and t not in excluded
    }
    passed = {t for t, r in judged.items() if r == "passed"}
    failing = sorted(t for t, r in judged.items() if r in _FAILING)
    some_test = bool(passed - {EXIT_STATUS_ID})
    exited = run.output is not None and run.output.exit_code == 0
    unlabelled = not excluded and exited

    def within_test(token: str, test: str) -> bool:
        life = records.children.get(token)
        if life is None:
            return False
        return any(
            start <= life[0] and life[1] <= end
            for start, end in records.tests.get(test, ())
        )

    def trusted(label: str) -> bool:
        if label.startswith(TEST_LABEL_PREFIX):
            return label.removeprefix(TEST_LABEL_PREFIX) in passed
        if label == OUTSIDE_TESTS:
            return some_test
        if label.startswith(CHILD_LABEL_PREFIX):
            token, _, inherited = label.removeprefix(
                CHILD_LABEL_PREFIX
            ).partition(" ")
            test = inherited.removeprefix(TEST_LABEL_PREFIX)
            if (
                inherited.startswith(TEST_LABEL_PREFIX)
                and test in passed
                and within_test(token, test)
            ):
                return True
            return unlabelled
        if not label:
            return unlabelled
        return False

    return trusted, failing


def _tail(text: str) -> str:
    """Return the end of a command's output, on one line."""
    flat = " ".join(text.split())
    return flat[-_TAIL_CHARS:]


def _remaining_result(
    spec: BenchmarkSpec,
    changes: ChangedLines,
    unchecked: Sequence[str],
    summary: Sequence[str],
) -> GateResult:
    """Judge a change all of whose checkable lines ran."""
    if unchecked:
        return GateResult(
            gate="coverage",
            status="not_available",
            detail="; ".join(
                [
                    "line coverage cannot check every change: "
                    + "; ".join(unchecked),
                    *summary,
                ]
            ),
        )
    if not changes.not_checked:
        return GateResult(
            gate="coverage", status="passed", detail="; ".join(summary)
        )
    paths = ", ".join(changes.not_checked)
    detail = f"coverage not checked for non-Python changes: {paths}"
    if spec.allow_unchecked_coverage:
        return GateResult(
            gate="coverage",
            status="waived",
            detail="; ".join(
                [f"{detail}; waived by allow_unchecked_coverage", *summary]
            ),
        )
    return GateResult(
        gate="coverage",
        status="not_available",
        detail="; ".join(
            [
                f"{detail}; a KEEP needs the spec's allow_unchecked_coverage",
                *summary,
            ]
        ),
    )


def _needs(change: FileChange) -> list[Need]:
    """Every need of one file: changed lines, then deletion sites."""
    found = {n.line: n for n in change.needs}
    for deletion in change.deletions:
        for need in deletion.needs:
            found.setdefault(need.line, need)
    return [found[n] for n in sorted(found)]


def _describe_changes(changes: ChangedLines) -> list[str]:
    """Summarise what the change set asks for and leaves out."""
    lines = sum(len(f.needs) for f in changes.files)
    removals = sum(len(f.deletions) for f in changes.files)
    parts = [
        f"{lines} changed executable lines and {removals} deletions in "
        f"{len(changes.files)} Python files"
    ]
    if changes.deleted:
        parts.append(
            "deleted, nothing left to run: " + ", ".join(changes.deleted)
        )
    return parts


def _unchecked(changes: ChangedLines) -> list[str]:
    """Describe each change line coverage cannot check."""
    found: list[str] = []
    for change in changes.files:
        if change.unparsed is not None:
            found.append(f"{change.path}: {change.unparsed}")
            continue
        found.extend(
            f"{change.path}: {n.why}"
            for n in _needs(change)
            if n.kind == "unchecked"
        )
    return found


def coverage_gate(
    spec: BenchmarkSpec,
    *,
    baseline_root: Path,
    candidate_root: Path,
    env: Mapping[str, str],
    tests: SuiteComparison | None,
    judged_tests: Collection[str] | None,
    untracked: Collection[str] | None = None,
    changes: ChangedLines | None = None,
    timeout_factor: float = COVERAGE_TIMEOUT_FACTOR,
) -> GateResult:
    """Check that every changed executable line ran under a check.

    Call after the correctness gate passed: it reruns the correctness
    command in the candidate tree under coverage.py, as the module
    docstring describes.

    Parameters
    ----------
    spec : BenchmarkSpec
        The spec in force.
    baseline_root, candidate_root : Path
        Roots of the two trees; the baseline is a git worktree at the
        snapshot commit and the candidate the repository's top level.
    env : mapping of str to str
        Environment the correctness command inherits, before the spec's
        declared variables are applied; the benchmark environment.
    tests : SuiteComparison or None
        The correctness gate's judgement of the tests
        (`perfproof.bench.correctness.check_tests`). ``None`` only when
        the spec has no correctness command.
    judged_tests : collection of str, ALL_JUDGED or None
        Ids of the tests that judgement used: neither quarantined nor
        known failures. Only they verify lines. `ALL_JUDGED` for every
        test the run under coverage reports (the quarantined and known
        failures of ``tests`` are still left out). ``None`` only when
        the spec has no correctness command.
    untracked : collection of str, optional
        The candidate's untracked files, from `untracked_files` called
        before anything ran in the candidate tree. If not given, they
        are listed when the gate starts, before its own run, so files
        any earlier run created count as the candidate's changes: a
        caller that ran anything in the tree first must pass them.
        Unused when ``changes`` is given.
    changes : ChangedLines, optional
        The changed lines; found with `changed_lines` if not given.
    timeout_factor : float, default `COVERAGE_TIMEOUT_FACTOR`
        Multiple of the correctness command's time limit allowed under
        coverage.

    Returns
    -------
    GateResult
        Gate ``coverage``: ``not_available``, with the unverified lines
        by file, when a changed executable line did not run under a
        passing, judged check, and also when coverage could not tell or
        a change cannot be checked by line (see the module docstring),
        when the spec has no correctness command to measure, or when
        non-Python files changed without the spec's
        ``allow_unchecked_coverage``; ``waived`` when they changed with
        it; passed otherwise.

    Raises
    ------
    ValueError
        If ``tests`` or ``judged_tests`` is ``None`` while the spec has
        a correctness command, or ``timeout_factor`` is not positive.
    CoverageGateError
        If git fails.
    OSError
        If a process cannot be created for a reason that is
        perfproof's own, such as running out of file descriptors.
    """
    if timeout_factor <= 0:
        msg = f"timeout_factor must be positive, not {timeout_factor}"
        raise ValueError(msg)
    command = spec.correctness.command
    if command is not None and (tests is None or judged_tests is None):
        msg = "the correctness command's test judgement is required"
        raise ValueError(msg)
    if changes is None:
        changes = changed_lines(
            baseline_root,
            candidate_root,
            untracked=untracked,
            outputs=[o.path for o in spec.correctness.outputs],
        )
    summary = _describe_changes(changes)
    unchecked = _unchecked(changes)
    required = {
        f.path: [n for n in _needs(f) if n.kind != "unchecked"]
        for f in changes.files
        if f.unparsed is None
    }
    required = {path: needs for path, needs in required.items() if needs}
    limit = spec.correctness.timeout_seconds
    if not required and not changes.not_checked:
        return _remaining_result(spec, changes, unchecked, summary)
    unverified: list[UnverifiedLines] = []
    story: list[str] = []
    problems: list[str] = []
    unrecorded: list[tuple[str, tuple[int, ...]]] = []
    if required:
        if (
            command is None
            or limit is None
            or tests is None
            or judged_tests is None
        ):
            return GateResult(
                gate="coverage",
                status="not_available",
                detail="; ".join(
                    [
                        "the spec has no correctness command, and only "
                        "the correctness command runs under coverage, so "
                        "nothing measured the changed lines",
                        *summary,
                    ]
                ),
            )
        sources = {
            path: (
                _Source(candidate_root / path),
                frozenset(n.target for n in needs if n.kind == "entry"),
            )
            for path, needs in required.items()
        }
        measured = _measure(
            spec, candidate_root, env, sources, limit * timeout_factor
        )
        run = measured.run
        summary.append(
            f"measured with coverage.py {coverage.__version__} running "
            f"{shlex.join(command)}"
        )
        if run.abnormal is not None:
            tail = "" if run.output is None else _tail(run.output.stderr)
            return GateResult(
                gate="coverage",
                status="not_available",
                detail="; ".join(
                    [
                        f"the correctness command {run.abnormal} under "
                        "coverage in the candidate tree",
                        *summary,
                        *measured.problems,
                        *([f"stderr ends: {tail}"] if tail else []),
                    ]
                ),
            )
        trusted, failing = _trust(run, tests, judged_tests, measured.records)
        unverified, story = _unverified(
            changes, required, measured, trusted, unrecorded
        )
        problems = list(measured.problems)
        if failing:
            problems.append(
                f"{len(failing)} judged tests did not pass under coverage: "
                + ", ".join(failing)
            )
    left_out = tests is not None and bool(
        tests.quarantined or tests.known_failures
    )
    reported = _judge_report(
        changes,
        spec,
        candidate_root,
        env,
        lines_of=lambda listed: _other_lines(
            baseline_root, candidate_root, listed
        ),
        left_out=left_out,
    )
    unverified = sorted([*unverified, *reported.unverified], key=_by_path)
    unrecorded.extend(reported.unrecorded)
    story = [*story, *reported.story]
    story.extend(
        f"{path} lines {_ranges(lines)} cannot be recorded: the file name "
        "is not a plain relative path"
        for path, lines in unrecorded
    )
    summary.extend(reported.summary)
    problems.extend(reported.problems)
    if not unverified and not unrecorded:
        remaining = replace(changes, not_checked=reported.not_checked)
        return _remaining_result(
            spec, remaining, unchecked, [*summary, *problems]
        )
    count = sum(len(u.lines) for u in unverified) + sum(
        len(lines) for _, lines in unrecorded
    )
    head = (
        "a run under coverage may have missed what the judged run did, "
        f"so it cannot tell whether {count} changed lines ran"
        if problems
        else f"{count} changed lines ran under no passing check"
    )
    return GateResult(
        gate="coverage",
        status="not_available",
        detail="; ".join([head, *problems, *story, *unchecked, *summary]),
        unverified=() if problems else tuple(unverified),
    )


def _record(
    into: list[UnverifiedLines],
    unrecorded: list[tuple[str, tuple[int, ...]]],
    path: str,
    lines: Sequence[int],
) -> None:
    """Add unverified lines, or note a file name the record refuses."""
    try:
        into.append(UnverifiedLines(path=path, lines=tuple(lines)))
    except ValidationError:
        unrecorded.append((path, tuple(lines)))


def _by_path(lines: UnverifiedLines) -> str:
    """Sort key: the file."""
    return lines.path


@dataclass(frozen=True, slots=True)
class _Reported:
    """What a coverage report says about the non-Python changes.

    Attributes
    ----------
    unverified : tuple of UnverifiedLines
        Changed lines that never ran, or that ran only where the run
        cannot be trusted to say which checks ran them.
    story : tuple of str
        Why, by file.
    not_checked : tuple of str
        Non-Python changed paths the report does not cover.
    summary : tuple of str
        What was read, for the gate's detail.
    problems : tuple of str
        What went wrong running the coverage command or reading its
        report.
    unrecorded : tuple of tuple
        ``(path, lines)`` of unverified lines in a file whose name the
        record cannot hold (not a plain relative path); they still
        withhold the KEEP.
    """

    unverified: tuple[UnverifiedLines, ...]
    story: tuple[str, ...]
    not_checked: tuple[str, ...]
    summary: tuple[str, ...]
    problems: tuple[str, ...] = ()
    unrecorded: tuple[tuple[str, tuple[int, ...]], ...] = ()


def _run_coverage_command(
    spec: BenchmarkSpec,
    candidate_root: Path,
    env: Mapping[str, str],
) -> tuple[Mapping[str, Mapping[int, int]] | None, bool, list[str]]:
    """Run the spec's coverage command and read the report it wrote.

    Returns the report (``None`` when none was written or it cannot be
    read), whether the command exited 0, and what went wrong.

    Raises
    ------
    ValueError
        If the spec declares no coverage command.
    OSError
        If the process cannot be created for a reason that is
        perfproof's own.
    """
    correctness = spec.correctness
    command = correctness.coverage_command
    limit = correctness.coverage_timeout_seconds
    if command is None or limit is None:
        msg = "the spec declares no coverage command"
        raise ValueError(msg)
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="perfproof-coverage-") as tmp:
        work = Path(tmp)
        scratch = work / "scratch"
        scratch.mkdir()
        report_file = work / "report"
        merged = {
            name: value
            for name, value in {**env, **spec.env}.items()
            if name != REPORT_ENV_VAR
        }
        merged[REPORT_ENV_VAR] = str(report_file)
        output, abnormal = run_command(
            command, candidate_root / correctness.cwd, merged, limit, scratch
        )
        exited = output is not None and output.exit_code == 0
        if abnormal is not None:
            problems.append(f"the coverage command {abnormal}")
        elif not exited:
            problems.append("the coverage command exited non-zero")
        if output is not None and not exited:
            tail = _tail(output.stderr)
            if tail:
                problems.append(f"its stderr ends: {tail}")
        report = None
        if abnormal is None:
            try:
                report = read_report(
                    report_file, candidate_root, correctness.cwd
                )
            except (ReportError, OSError) as exc:
                problems.append(f"the coverage report is unreadable: {exc}")
            else:
                if report is None:
                    problems.append(
                        "the coverage command wrote no report to "
                        f"${REPORT_ENV_VAR}"
                    )
    return report, exited, problems


def _judge_report(
    changes: ChangedLines,
    spec: BenchmarkSpec,
    candidate_root: Path,
    env: Mapping[str, str],
    *,
    lines_of: Callable[
        [Collection[OtherChange]], Mapping[str, tuple[int, ...]]
    ],
    left_out: bool,
) -> _Reported:
    """Match the non-Python changed lines against a coverage report.

    Runs the spec's coverage command, when one is declared and
    non-Python files changed. A path the report lists is judged by line:
    zero hits is a line that never ran, a line the report does not list
    is not executable and asks nothing. A report has no per-test labels,
    so hits count only when the command exited 0 and no test was left
    out of the judgement (``left_out``).
    """
    if not changes.not_checked:
        return _Reported((), (), (), ())
    if spec.correctness.coverage_command is None:
        note = (
            "no coverage command declared (correctness.coverage_command), "
            "so no coverage tool covers the non-Python changes",
        )
        return _Reported((), (), changes.not_checked, note)
    report, exited, problems = _run_coverage_command(spec, candidate_root, env)
    reliable = exited and not left_out
    if report is None:
        return _Reported((), (), changes.not_checked, (), tuple(problems))
    listed = [c for c in changes.other if c.path in report]
    covered = {c.path for c in listed}
    all_lines = lines_of(listed)
    unverified: list[UnverifiedLines] = []
    unrecorded: list[tuple[str, tuple[int, ...]]] = []
    story: list[str] = []
    for change in listed:
        hits = report[change.path]
        change_lines = all_lines[change.path]
        never = [n for n in change_lines if hits.get(n) == 0]
        maybe = [] if reliable else [n for n in change_lines if hits.get(n, 0)]
        if never:
            story.append(f"{change.path} lines {_ranges(never)} never ran")
        if maybe:
            story.append(
                f"{change.path} lines {_ranges(maybe)} ran, but the "
                "coverage command failed or tests were left out of the "
                "judgement, so they may have run only for those"
            )
        lines = sorted({*never, *maybe})
        if lines:
            _record(unverified, unrecorded, change.path, lines)
    remaining = tuple(p for p in changes.not_checked if p not in covered)
    summary = (
        f"{len(covered)} non-Python files checked from the coverage report",
    )
    return _Reported(
        tuple(unverified),
        tuple(story),
        remaining,
        summary if covered else (),
        tuple(problems),
        tuple(unrecorded),
    )


def _unverified(
    changes: ChangedLines,
    required: Mapping[str, Sequence[Need]],
    measured: _Measurement,
    trusted: Callable[[str], bool],
    unrecorded: list[tuple[str, tuple[int, ...]]],
) -> tuple[list[UnverifiedLines], list[str]]:
    """Find the needs no trusted label met, and say why."""
    unverified: list[UnverifiedLines] = []
    story: list[str] = []
    deletions = {f.path: f.deletions for f in changes.files}
    for path, needs in sorted(required.items()):
        ran = measured.lines.get(path, {})
        entered = measured.entries.get(path, {})
        never: list[int] = []
        untrusted: list[int] = []
        for need in needs:
            seen = entered if need.kind == "entry" else ran
            labels = seen.get(need.target, frozenset[str]())
            if any(trusted(label) for label in labels):
                continue
            (untrusted if labels else never).append(need.line)
        missed = sorted(never + untrusted)
        if not missed:
            continue
        _record(unverified, unrecorded, path, missed)
        if never:
            story.append(f"{path} lines {_ranges(never)} never ran")
        if untrusted:
            story.append(
                f"{path} lines {_ranges(untrusted)} ran only under tests "
                "left out of the judgement (quarantined or known failures), "
                "that did not pass, or in processes that outlived them"
            )
        story.extend(
            f"{path}: baseline lines {_ranges(d.baseline_lines)} were "
            f"deleted in the block of line {n.line}, which did not run under "
            "a passing check"
            for d in deletions[path]
            for n in d.needs
            if n.kind == "statement" and n.line in missed
        )
    return unverified, story
