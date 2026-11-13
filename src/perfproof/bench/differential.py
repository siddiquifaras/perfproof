"""Differential checks for Python: old and new code on the same inputs.

Per "Differential checks" in ``docs/method.md``, every function the
candidate changed is run in both versions on the same inputs, and the
results must match. Inputs come from two places:

- **Real calls**, captured while the baseline benchmark runs, in a
  capture pass of its own. The measured rounds never load any of this
  code.
- **Generated inputs** (Hypothesis), where the function's type hints
  allow.

Which functions changed
-----------------------
`changed_functions` lists the ``.py`` files ``git diff`` reports
between the baseline's commit and the candidate's working tree, then
reads ``git diff -U0`` for each one. Every changed line, on the
baseline side (removed lines) and on the candidate side (added lines),
is mapped through each version's syntax tree to the outermost function
that contains it, decorators included, and named by its qualified name
inside the module (``Class.method``). A change inside a nested function
or a lambda counts as a change to the enclosing function, because only
module-level functions and methods can be reached from outside.
Changed lines outside any function (module or class-level code) are
listed in `ChangeSet.not_checked`, as are functions that exist in only
one version and files that are new, deleted or unparseable. Only
functions present in both versions are checked. Non-Python files are
not considered here; the coverage gate reports them.

How calls are captured
----------------------
The capture pass runs the spec's command once in the baseline tree,
with the benchmark environment plus a ``PYTHONPATH`` entry that holds
a generated ``sitecustomize.py``. That hook loads this module by file
path into each Python process the benchmark starts and installs an
import hook: when a module is loaded from one of the changed files in
the baseline tree, its changed functions are replaced by wrappers. A
process whose interpreter ignores ``PYTHONPATH`` (``-I``, ``-E``) never
loads the hook, and that is reported. The hook chains to any
``sitecustomize`` it shadows.

In each process, the first ``capture_limit`` calls to each function
are examined; later ones are only counted. An examined call is
captured when its arguments pickle before the call, the call raised
none of the side-effect audit events below, and the arguments (after
the call) and the result or exception pickle after it. Otherwise it is
skipped with its reason, such as an argument that is an open file, a
method whose ``self`` does not pickle, a generator or coroutine
function (whose results are produced after the call returns), or a
side effect. Side effects are seen through ``sys.addaudithook``, so
only what raises an audit event is caught: a file opened for writing,
a file removed or renamed, a process started through ``subprocess`` or
``os``, a socket connected, a name resolved, and a ``ctypes`` library
load or symbol lookup during the call (``ctypes.dlopen``,
``ctypes.dlsym``). Calling a foreign function raises no event of its
own, so a call through a ``ctypes`` pointer resolved before the call,
at import time for example, is not seen. ``ctypes.call_function`` is
in the list too, but only ctypes' private ``_ctypes.call_function``
helper raises it. Across processes, the earliest ``capture_limit``
captured calls are kept, and the rest are counted as beyond the
capture limit.
Every perfproof process, the capture hook included, sets
``sys.dont_write_bytecode``, so an import inside a call writes no
``.pyc`` file and is not taken for a side effect.

Pickled data is never unpickled in perfproof's own process. The
capture writes metadata as JSON lines and the pickles to separate
files; perfproof reads only the JSON and hands the pickles' paths to
replay processes.

How results are compared
------------------------
Each captured or generated call goes through three fresh processes
running this module as a script, each with the interpreter and
``sys.path`` the benchmark's process had (paths inside the baseline
tree mapped to the tree being run), ``-S`` so no ``.pth`` file or
editable-install hook can import the other tree, the benchmark
environment, and ``PYTHONHASHSEED`` set to ``0`` unless the spec sets
it (in the capture pass too). After import, the module's file must be
the one in that tree, or the process refuses to run.

1. **Baseline replay**, in the baseline tree. A call the baseline does
   not reproduce in a fresh process (it depends on process state,
   randomness or time) is skipped at replay with that reason, so state
   carried in the benchmark process never shows up as a candidate
   mismatch.
2. **Candidate run**, in the candidate tree. It runs each call,
   compares the result with the recording there, and writes the
   pickled arguments after the call and the result or exception.
3. **Comparison**, in the baseline tree again. It unpickles the
   recorded and the candidate's results there, so any class pickled by
   reference resolves to the baseline's own version, and compares
   them. A candidate result whose class or module the baseline lacks
   fails to load, which is a mismatch.

A call mismatches if either comparison finds a difference. The one in
the baseline tree is what code in the candidate cannot reach: patching
the comparison or giving its classes a lenient ``__eq__`` changes only
the candidate's own check. The one in the candidate's process catches
a result that pickling would change, such as a ``Fraction`` built
unnormalised, which unpickles normalised and equal. The candidate's
result is pickled before its own check runs, so that check cannot
change what the baseline tree sees. When the recorded results do not
load in the candidate tree (the candidate gave a class ``__slots__``,
say), its own check is skipped with a note and the baseline tree's
comparison alone decides.

A *library* here is a module that is built in, frozen, in a
``site-packages`` or ``dist-packages`` directory (a virtualenv inside
a tree counts), or anywhere outside both trees; every other module
belongs to a tree, even a tree that lies under the interpreter's own
prefix. Each of these processes first imports, with the guard off, the
libraries the benchmark's processes had loaded when their calls were
captured (the capture hook records each module with its file). It then
imports the module under test, also with the guard off, so that module
and the tree modules it imports at its top level run their
import-time code unguarded in replay processes, as they did in the
benchmark. Then it loads the recorded call, and only then turns the
side-effect guard on: for the call, and for pickling or loading the
candidate's results. Other tree modules import when the code under
test asks for them. While the guard is on, it is suspended for as long
as a library is being imported, and for the first run in the process
of a tree module the benchmark had loaded: the benchmark ran that
code, so an import of it moved into the function (by an ``import``
statement or through a ``LazyLoader``) is not the call's side effect,
even when it creates a directory, sets an environment variable or
registers itself somewhere as it loads. The guard is re-armed for any
other tree module, even one a library or a recorded module imports,
and for a second run of a recorded module (a reload, or a re-import
after removing it from ``sys.modules``), whose side effects are the
call's. So NumPy, which raises ``os.putenv`` and ``ctypes.dlopen`` as
it imports, does not count as a side effect, whether the call's
arguments need it, the candidate moved its import into the function,
or the benchmark never used it and the candidate does. With the guard
on, the side-effect audit events above are blocked, not just seen, and
every call and comparison has a time limit. A process ends with
``os._exit`` once its results are flushed, so nothing the code under
test left behind, such as a ``multiprocessing`` pool, can hold it at
exit, and its whole process group is then killed.

Results are compared exactly, because the schema declares no tolerance
for differential checks: the values must have the same type and be
equal. Floats compare with ``==`` except that NaN equals NaN (so
``-0.0`` equals ``0.0``); lists, tuples and dicts compare element by
element, dict keys in order; NumPy arrays need the same dtype, shape
and elements, NaN equal to NaN; an object whose class keeps
``object.__eq__`` compares by its pickled state; anything else by its
own ``==``. A call that raised must raise the same exception type with
equal ``args`` in the candidate. The arguments must also be equal after
the call, so a function that mutates its input is checked on that too.
A candidate that times out, dies, attempts a side effect the baseline
did not, cannot import or load a call the baseline could, or returns a
result that does not pickle, mismatches.

What this cannot check
----------------------
Side effects that raise no audit event are neither seen nor blocked:
I/O done inside C extensions (database drivers such as libpq, gRPC,
anything that does its own system calls), foreign calls through
``ctypes`` pointers resolved before the call, and work handed to
``multiprocessing`` workers started with ``spawn`` or ``forkserver``,
which never load the capture hook and whose start raises no event.
So a captured call that writes to a database through a C driver
writes again on each replay, in both trees; such benchmarks need the
spec's reset and a local service, as the `side_effects` gate
requires. A library's import-time code runs unguarded, whether it is
imported before the guard is on or during a call: a library that
writes files or opens connections as it imports is not caught, and
neither is a candidate that hides a side effect in a package it
installs outside the trees. The same holds for the first run of a tree
module the benchmark had loaded, in the candidate's version too: a
candidate that adds side effects to such a module's import-time code
is not caught by the guard.
The comparison process unpickles the candidate's output, so a
candidate built to attack perfproof itself through crafted pickles is
not covered, as ``docs/method.md`` puts attacks on perfproof out of
scope for v1.

Functions referenced before the import hook wraps them (stored in a
registry while their module loads) are not captured; side effects
through threads the call starts are not attributed to it; changes to
global state that is not an argument are not compared; capture takes
the first calls in each process, not a sample of the run. Calls whose
results differ only in float rounding mismatch, since no tolerance can
be declared. Generated inputs need Hypothesis importable by the
benchmark's interpreter: the project's own, or else perfproof's, which
is appended to ``sys.path`` but includes a compiled module, so it
loads only on perfproof's own Python version; otherwise generation is
skipped with a note.

This module runs in three kinds of process: perfproof's own, the
benchmark's (as the capture hook) and the replay and generation
processes (as a script), on whatever CPython 3.10 or later the
benchmark uses. So its top level imports only the standard library,
and perfproof's own modules are imported inside the functions that
need them.
"""

from __future__ import annotations

import ast
import atexit
import contextlib
import functools
import importlib
import importlib.util
import inspect
import json
import os
import pickle
import re
import signal
import subprocess
import sys
import threading
import time
import typing
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import (
        Callable,
        Generator,
        Iterable,
        Iterator,
        Mapping,
        Sequence,
    )
    from types import FrameType, ModuleType

    from perfproof.schema import (
        Arm,
        BenchmarkSpec,
        Difference,
        DifferentialCheck,
    )

__all__ = [
    "CAPTURE_TIMEOUT_FACTOR",
    "DEFAULT_CAPTURE_LIMIT",
    "DEFAULT_EXAMPLES",
    "GENERATED_CALL_SECONDS",
    "GIT_TIMEOUT_SECONDS",
    "MAX_DIFFERENCES",
    "MAX_PAYLOAD_BYTES",
    "CaptureError",
    "ChangeSet",
    "ChangedFunction",
    "DifferentialError",
    "DifferentialResult",
    "NotChecked",
    "changed_functions",
    "check",
]

DEFAULT_CAPTURE_LIMIT: Final = 100
"""Calls examined per changed function in each benchmark process, and
captured calls kept per function across processes."""
DEFAULT_EXAMPLES: Final = 100
"""Generated inputs per function, when its type hints allow."""
MAX_DIFFERENCES: Final = 5
"""First differences kept per function."""
MAX_PAYLOAD_BYTES: Final = 16 * 1024 * 1024
"""Largest pickled arguments or result kept for one call, in bytes."""
CAPTURE_TIMEOUT_FACTOR: Final = 4.0
"""The capture pass may take this many times the spec's timeout, since
pickling every examined call slows the benchmark down."""
GENERATED_CALL_SECONDS: Final = 2.0
"""Time limit for the baseline on one generated input, in seconds.
Generation stops at the first input that exceeds it."""
GIT_TIMEOUT_SECONDS: Final = 60.0
"""Time limit for one git query, in seconds."""

_AGENT_MODULE: Final = "_perfproof_differential"
_HASH_SEED: Final = "0"
_REPR_CHARS: Final = 200
_STARTUP_SECONDS: Final = 60.0
_TAIL_BYTES: Final = 2000
_HUNK: Final = re.compile(rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_WRITE_FLAGS: Final = (
    os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
)
_SIDE_EFFECT_EVENTS: Final = frozenset(
    {
        # Only the private _ctypes.call_function helper raises this;
        # an ordinary foreign call raises nothing (module docstring).
        "ctypes.call_function",
        "ctypes.dlopen",
        "ctypes.dlsym",
        "ftplib.connect",
        "http.client.connect",
        "imaplib.open",
        "nntplib.connect",
        "os.chdir",
        "os.chflags",
        "os.chmod",
        "os.chown",
        "os.exec",
        "os.fork",
        "os.forkpty",
        "os.kill",
        "os.killpg",
        "os.link",
        "os.mkdir",
        "os.posix_spawn",
        "os.putenv",
        "os.remove",
        "os.removexattr",
        "os.rename",
        "os.rmdir",
        "os.setxattr",
        "os.spawn",
        "os.startfile",
        "os.symlink",
        "os.system",
        "os.truncate",
        "os.unsetenv",
        "os.utime",
        "poplib.connect",
        "shutil.chown",
        "shutil.copyfile",
        "shutil.copymode",
        "shutil.copystat",
        "shutil.copytree",
        "shutil.make_archive",
        "shutil.move",
        "shutil.rmtree",
        "shutil.unpack_archive",
        "smtplib.connect",
        "socket.bind",
        "socket.connect",
        "socket.getaddrinfo",
        "socket.gethostbyaddr",
        "socket.gethostbyname",
        "socket.getnameinfo",
        "socket.sendmsg",
        "socket.sendto",
        "sqlite3.connect",
        "subprocess.Popen",
        "telnetlib.Telnet.open",
        "urllib.Request",
        "webbrowser.open",
    }
)


class DifferentialError(RuntimeError):
    """The differential check could not run; no version is to blame."""


class CaptureError(DifferentialError):
    """The baseline benchmark failed while its calls were captured.

    Attributes
    ----------
    stdout, stderr : Path
        The capture run's output files.
    """

    def __init__(self, message: str, stdout: Path, stderr: Path) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True, slots=True)
class ChangedFunction:
    """A function present in both versions whose code changed.

    Attributes
    ----------
    path : str
        Source file, relative to the tree root, with ``/`` separators.
    qualname : str
        Qualified name inside the module, such as ``Class.method``.
    """

    path: str
    qualname: str


@dataclass(frozen=True, slots=True)
class NotChecked:
    """A change the differential check cannot cover, and why.

    Attributes
    ----------
    path : str
        Source file, relative to the tree root.
    what : str
        A qualified name, ``"candidate lines 3-5"``, ``"baseline lines
        3-5"`` or ``"whole file"``.
    reason : str
        Why it is not checked.
    """

    path: str
    what: str
    reason: str


@dataclass(frozen=True, slots=True)
class ChangeSet:
    """The changed functions between two trees.

    Attributes
    ----------
    functions : tuple of ChangedFunction
        Functions in both versions whose code changed, sorted by path
        and name.
    not_checked : tuple of NotChecked
        Changes outside such functions.
    """

    functions: tuple[ChangedFunction, ...]
    not_checked: tuple[NotChecked, ...]


@dataclass(frozen=True, slots=True)
class DifferentialResult:
    """Outcome of the differential checks for one comparison.

    Attributes
    ----------
    checks : tuple of perfproof.schema.DifferentialCheck
        One per changed function that a benchmark process imported
        from the baseline tree and wrapped, as ``module:qualname``.
    not_checked : tuple of NotChecked
        Changes no check covered, with the reason.
    notes : tuple of str
        Anything else the report should say, such as generated inputs
        the baseline could not reproduce or a process whose counts are
        incomplete.
    """

    checks: tuple[DifferentialCheck, ...]
    not_checked: tuple[NotChecked, ...]
    notes: tuple[str, ...]

    @property
    def mismatches(self) -> int:
        """Calls whose results differed, over every function."""
        return sum(c.mismatches for c in self.checks)


# --------------------------------------------------------------------
# Changed functions (perfproof's process)
# --------------------------------------------------------------------


def _git(cwd: Path, *args: str, timeout: float, literal: bool = True) -> bytes:
    """Run git in ``cwd`` and return its raw stdout.

    ``literal`` turns pathspec magic off.

    Raises
    ------
    DifferentialError
        If git exits non-zero, cannot be started, or times out.
    """
    flags = ["--literal-pathspecs"] if literal else []
    argv = ["git", *flags, "-C", str(cwd), *args]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"{' '.join(argv)} failed: {exc}"
        raise DifferentialError(msg) from exc
    if done.returncode != 0:
        err = done.stderr.decode(errors="replace").strip()
        msg = f"{' '.join(argv)} exited {done.returncode}: {err}"
        raise DifferentialError(msg)
    return done.stdout


def _statement_lists(node: ast.AST) -> Iterator[list[ast.stmt]]:
    """Yield the statement lists directly inside a compound one."""
    for _, value in ast.iter_fields(node):
        if not isinstance(value, list):
            continue
        items = typing.cast("list[object]", value)
        stmts = [s for s in items if isinstance(s, ast.stmt)]
        if stmts:
            yield stmts
        for item in items:
            if isinstance(item, (ast.ExceptHandler, ast.match_case)):
                yield item.body


def _function_spans(source: bytes) -> list[tuple[str, int, int]]:
    """Return ``(qualname, first, last)`` for each reachable function.

    Reachable means defined at module level or in a class body,
    possibly inside ``if``, ``try`` and other compound statements, but
    not inside another function. Lines are 1-based and inclusive, and
    start at the first decorator.

    Raises
    ------
    SyntaxError, ValueError
        If ``source`` does not parse.
    """
    spans: list[tuple[str, int, int]] = []

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                first = min(
                    [node.lineno, *(d.lineno for d in node.decorator_list)]
                )
                last = node.end_lineno or node.lineno
                spans.append((prefix + node.name, first, last))
            elif isinstance(node, ast.ClassDef):
                visit(node.body, f"{prefix}{node.name}.")
            else:
                for stmts in _statement_lists(node):
                    visit(stmts, prefix)

    visit(ast.parse(source).body, "")
    return spans


def _owner(spans: Sequence[tuple[str, int, int]], line: int) -> str | None:
    """Return the function whose span holds ``line``, if any."""
    for name, first, last in spans:
        if first <= line <= last:
            return name
    return None


def _ranges(lines: Sequence[int]) -> Iterator[str]:
    """Spell sorted line numbers as ``3`` or ``3-5`` ranges."""
    start = prev = -2
    for line in sorted(set(lines)):
        if line != prev + 1:
            if start >= 0:
                yield str(start) if start == prev else f"{start}-{prev}"
            start = line
        prev = line
    if start >= 0:
        yield str(start) if start == prev else f"{start}-{prev}"


def _hunks(patch: bytes) -> Iterator[tuple[range, range]]:
    """Yield the baseline and candidate line ranges of ``-U0`` hunks."""
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


def changed_functions(
    baseline: Path,
    candidate: Path,
    *,
    timeout: float = GIT_TIMEOUT_SECONDS,
) -> ChangeSet:
    """List the Python functions the candidate changed.

    Parameters
    ----------
    baseline : Path
        Root of the baseline worktree, checked out at the snapshot
        commit.
    candidate : Path
        Root of the candidate's working tree, in the same repository.
        Uncommitted changes count; untracked files do not.
    timeout : float, default `GIT_TIMEOUT_SECONDS`
        Time limit for each git query, in seconds.

    Returns
    -------
    ChangeSet
        The changed functions and every change they leave out.

    Raises
    ------
    DifferentialError
        If git fails.
    """
    from perfproof.bench.coverage_gate import (
        _split_patch,  # pyright: ignore[reportPrivateUsage]
    )
    from perfproof.bench.snapshot import SnapshotError, worktree_commit

    try:
        commit = worktree_commit(baseline)
    except SnapshotError as exc:
        raise DifferentialError(str(exc)) from exc
    listing = _git(
        candidate,
        "diff",
        "--name-status",
        "-z",
        "--no-renames",
        commit,
        "--",
        timeout=timeout,
    )
    tokens = listing.split(b"\0")
    entries = [
        (tokens[i].decode(), os.fsdecode(tokens[i + 1]))
        for i in range(0, len(tokens) - 1, 2)
    ]
    functions: list[ChangedFunction] = []
    skipped: list[NotChecked] = []
    patches: dict[str, bytes] = {}
    if any(p.endswith(".py") and s not in ("A", "D") for s, p in entries):
        # One diff, not one git call per file.
        patches = _split_patch(
            _git(
                candidate,
                "diff",
                "-U0",
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
    for status, path in sorted(entries, key=lambda e: e[1]):
        if not path.endswith(".py"):
            continue
        if status == "A":
            reason = "new in the candidate; no baseline version to compare"
            skipped.append(NotChecked(path, "whole file", reason))
            continue
        if status == "D":
            skipped.append(
                NotChecked(path, "whole file", "deleted in the candidate")
            )
            continue
        found, missed = _changed_in_file(
            baseline, candidate, path, patches.get(path, b"")
        )
        functions.extend(found)
        skipped.extend(missed)
    return ChangeSet(tuple(functions), tuple(skipped))


def _changed_in_file(
    baseline: Path, candidate: Path, path: str, patch: bytes
) -> tuple[list[ChangedFunction], list[NotChecked]]:
    """Map one file's hunks to the functions they touch."""
    spans: dict[str, list[tuple[str, int, int]]] = {}
    for arm, root in (("baseline", baseline), ("candidate", candidate)):
        try:
            spans[arm] = _function_spans((root / path).read_bytes())
        except (OSError, SyntaxError, ValueError) as exc:
            reason = f"the {arm} version could not be parsed: {exc}"
            return [], [NotChecked(path, "whole file", reason)]
    touched: set[str] = set()
    loose: dict[str, list[int]] = {"baseline": [], "candidate": []}
    for old, new in _hunks(patch):
        for arm, lines in (("baseline", old), ("candidate", new)):
            for line in lines:
                name = _owner(spans[arm], line)
                if name is None:
                    loose[arm].append(line)
                else:
                    touched.add(name)
    base_names = {s[0] for s in spans["baseline"]}
    cand_names = {s[0] for s in spans["candidate"]}
    found: list[ChangedFunction] = []
    missed: list[NotChecked] = []
    for name in sorted(touched):
        if name in base_names and name in cand_names:
            found.append(ChangedFunction(path, name))
        elif name in cand_names:
            reason = "only in the candidate; no baseline version to compare"
            missed.append(NotChecked(path, name, reason))
        else:
            missed.append(NotChecked(path, name, "removed in the candidate"))
    for arm in ("candidate", "baseline"):
        missed.extend(
            NotChecked(
                path,
                f"{arm} lines {span}",
                "module or class level code, outside any function",
            )
            for span in _ranges(loose[arm])
        )
    return found, missed


# --------------------------------------------------------------------
# Shared by the capture hook and the replay and generation processes
# --------------------------------------------------------------------


class _TimeoutError(BaseException):
    """A replayed or generated call ran past its time limit."""


class _BlockedError(BaseException):
    """A side effect was blocked inside a replayed or generated call.

    A ``BaseException`` so that ``except Exception`` in the code under
    test does not swallow it.
    """


def _side_effect(event: str, args: tuple[Any, ...]) -> str | None:
    """Describe an audit event that changes the outside world."""
    if event == "open":
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else None
        if isinstance(flags, int) and flags & _WRITE_FLAGS:
            return "open for writing"
        if isinstance(mode, str) and any(c in mode for c in "wax+"):
            return "open for writing"
        return None
    return event if event in _SIDE_EFFECT_EVENTS else None


def _type_name(value: object) -> str:
    """Name ``value``'s type, with its module unless it is a builtin."""
    kind = type(value)
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def _short_repr(value: object) -> str:
    """Return ``repr(value)``, cut to a readable length."""
    try:
        text = repr(value)
    except Exception as exc:
        text = f"<{_type_name(value)}; repr failed: {type(exc).__name__}>"
    if len(text) > _REPR_CHARS:
        text = text[: _REPR_CHARS - 3] + "..."
    return text


def _positional_names(fn: Callable[..., object]) -> list[str]:
    """Return the names of ``fn``'s positional parameters."""
    code = getattr(fn, "__code__", None)
    if code is not None:
        names: tuple[str, ...] = code.co_varnames
        return list(names[: code.co_argcount])
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return []
    return [
        p.name
        for p in params
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]


def _resolve(
    module: ModuleType, qualname: str
) -> tuple[object, str, Callable[..., object]]:
    """Find a function by qualified name, as stored on its owner.

    Returns
    -------
    owner : object
        The module or class the function is stored on.
    kind : str
        ``"function"``, ``"method"``, ``"staticmethod"`` or
        ``"classmethod"``.
    fn : callable
        The underlying function: what the capture hook wraps and what
        a replay calls with the recorded arguments, including ``self``
        or ``cls``.

    Raises
    ------
    LookupError
        If it is missing or not callable.
    """
    owner: object = module
    parts = qualname.split(".")
    for part in parts[:-1]:
        owner = getattr(owner, part, None)
        if not isinstance(owner, type):
            msg = f"{part} in {qualname} is not a class at runtime"
            raise LookupError(msg)
    namespace: Mapping[str, object] = vars(owner)
    if parts[-1] not in namespace:
        msg = f"{qualname} is not defined at runtime"
        raise LookupError(msg)
    stored = namespace[parts[-1]]
    kind = "method" if isinstance(owner, type) else "function"
    fn: object = stored
    if isinstance(stored, staticmethod):
        kind, fn = "staticmethod", typing.cast("Any", stored).__func__
    elif isinstance(stored, classmethod):
        kind, fn = "classmethod", typing.cast("Any", stored).__func__
    if not callable(fn) or isinstance(fn, type):
        msg = f"{qualname} is a {_type_name(fn)}, not a function"
        raise LookupError(msg)
    return owner, kind, fn


def _module_file(module: object) -> str | None:
    """Read a module's ``__file__`` without loading a lazy module.

    Plain attribute access on a module made by
    ``importlib.util.LazyLoader`` runs its body; reading the instance
    dictionary directly does not.
    """
    try:
        namespace = object.__getattribute__(module, "__dict__")
    except AttributeError:
        return None
    file = namespace.get("__file__")
    return file if isinstance(file, str) else None


def _append(fd: int, data: bytes) -> None:
    """Write all of ``data`` to a file descriptor."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


# --------------------------------------------------------------------
# Capture hook (the benchmark's processes, capture pass only)
# --------------------------------------------------------------------


@dataclass(slots=True)
class _Call:
    """A call being captured."""

    function: str
    before: bytes
    side_effect: str | None = None


class _Recorder:
    """Wraps changed functions and writes what their calls did.

    Each process writes ``events-<pid>.jsonl`` (one JSON object per
    line) and ``blobs-<pid>.bin`` (the pickles the events point at).
    """

    def __init__(self, config: Mapping[str, Any]) -> None:
        self._out = str(config["out"])
        self._limit = int(config["limit"])
        self._tree = str(config["tree"])
        self._targets: dict[str, list[str]] = {
            os.path.realpath(os.path.join(self._tree, path)): list(names)
            for path, names in config["targets"].items()
        }
        self._protocol = pickle.HIGHEST_PROTOCOL
        self._open()

    def _open(self) -> None:
        """Start this process's files and counters."""
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._local = threading.local()
        self._examined: Counter[str] = Counter()
        self._beyond: Counter[str] = Counter()
        self._seq = 0
        self._offset = 0
        self._modules: set[str] = set()
        self._modules_count = -1
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        self._events = os.open(
            os.path.join(self._out, f"events-{self._pid}.jsonl"), flags, 0o600
        )
        self._blobs = os.open(
            os.path.join(self._out, f"blobs-{self._pid}.bin"), flags, 0o600
        )
        self._event({"type": "process", "executable": sys.executable})

    def after_fork(self) -> None:
        """Give a forked child its own files and counters.

        Calls in progress at the fork are dropped in the child: the
        parent saw ``os.fork`` as a side effect of them.
        """
        os.close(self._events)
        os.close(self._blobs)
        self._open()

    def close(self) -> None:
        """Write the counts of calls beyond the limit, at exit."""
        if os.getpid() == self._pid:
            self._event({"type": "exit", "beyond": dict(self._beyond)})

    def _event(self, event: dict[str, Any]) -> None:
        """Append one event line; the caller need not hold the lock."""
        event["pid"] = self._pid
        line = json.dumps(event).encode() + b"\n"
        with self._lock:
            _append(self._events, line)

    def adopt(self, spec: Any) -> None:
        """Hand a changed file's module to `_Loader` as it is found."""
        origin = spec.origin
        if origin is not None and os.path.realpath(origin) in self._targets:
            spec.loader = _Loader(spec.loader, self)

    def patch(self, module: ModuleType) -> None:
        """Wrap the changed functions of a module that just loaded."""
        origin = os.path.realpath(str(module.__file__))
        statuses: dict[str, str] = {}
        for qualname in self._targets.get(origin, []):
            function = f"{module.__name__}:{qualname}"
            try:
                statuses[qualname] = self._wrap(module, qualname, function)
            except Exception as exc:
                statuses[qualname] = f"not wrapped: {exc}"
        self._event(
            {
                "type": "module",
                "module": module.__name__,
                "file": os.path.relpath(origin, os.path.realpath(self._tree)),
                "executable": sys.executable,
                "path": list(sys.path),
                "functions": statuses,
                "t": time.monotonic_ns(),
            }
        )

    def _wrap(self, module: ModuleType, qualname: str, function: str) -> str:
        """Replace one function with a capturing wrapper."""
        if module.__name__ == "__main__":
            return "defined in __main__, which a replay cannot import"
        owner, kind, fn = _resolve(module, qualname)
        if inspect.isasyncgenfunction(fn):
            return "an async generator function; its calls are not counted"
        wrapper = self._wrapper(fn, function)
        stored: object = wrapper
        if kind == "staticmethod":
            stored = staticmethod(wrapper)
        elif kind == "classmethod":
            stored = classmethod(wrapper)
        setattr(owner, qualname.rsplit(".", 1)[-1], stored)
        return "wrapped"

    def _wrapper(
        self, fn: Callable[..., Any], function: str
    ) -> Callable[..., Any]:
        """Build the wrapper that captures calls to ``fn``."""
        lazy = (
            "a generator or coroutine function; its results are produced "
            "after the call returns"
        )
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def coroutine(*args: Any, **kwargs: Any) -> Any:
                self.skip(function, lazy)
                return await fn(*args, **kwargs)

            return coroutine
        if inspect.isgeneratorfunction(fn):

            @functools.wraps(fn)
            def generator(*args: Any, **kwargs: Any) -> Any:
                self.skip(function, lazy)
                return fn(*args, **kwargs)

            return generator
        names = _positional_names(fn)

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            call = self.begin(function, names, args, kwargs)
            if call is None:
                return fn(*args, **kwargs)
            try:
                result = fn(*args, **kwargs)
            except BaseException as exc:
                self.end(call, args, kwargs, "raise", exc)
                raise
            self.end(call, args, kwargs, "return", result)
            return result

        return wrapper

    def _admit(self, function: str) -> bool:
        """Count a call; whether it is among the first examined."""
        with self._lock:
            if self._examined[function] >= self._limit:
                self._beyond[function] += 1
                return False
            self._examined[function] += 1
            return True

    def skip(self, function: str, reason: str) -> None:
        """Record a call that cannot be captured."""
        if self._admit(function):
            self._skipped(function, reason)

    def _skipped(self, function: str, reason: str) -> None:
        """Write a skip event for an examined call."""
        self._event({"type": "skip", "function": function, "reason": reason})

    def _stack(self) -> list[_Call]:
        """Return the calls being captured on this thread."""
        stack: list[_Call] | None = getattr(self._local, "stack", None)
        if stack is None:
            stack = []
            self._local.stack = stack
        return stack

    def begin(
        self,
        function: str,
        names: Sequence[str],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> _Call | None:
        """Pickle a call's arguments; ``None`` if it is not captured."""
        if getattr(self._local, "busy", False) or not self._admit(function):
            return None
        self._local.busy = True
        try:
            reason = None
            try:
                before = pickle.dumps((args, kwargs), protocol=self._protocol)
            except Exception:
                before = b""
                reason = self._unpicklable(names, args, kwargs)
            if reason is None and len(before) > MAX_PAYLOAD_BYTES:
                reason = f"arguments exceed {MAX_PAYLOAD_BYTES} bytes pickled"
        finally:
            self._local.busy = False
        if reason is not None:
            self._skipped(function, reason)
            return None
        call = _Call(function, before)
        self._stack().append(call)
        return call

    def _unpicklable(
        self,
        names: Sequence[str],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> str:
        """Say which argument could not be pickled."""
        labelled = [
            (names[i] if i < len(names) else f"*args[{i - len(names)}]", a)
            for i, a in enumerate(args)
        ]
        for name, value in [*labelled, *kwargs.items()]:
            try:
                pickle.dumps(value, protocol=self._protocol)
            except Exception:
                return (
                    f"argument {name!r} could not be pickled "
                    f"({_type_name(value)})"
                )
        return "the arguments could not be pickled together"

    def end(
        self,
        call: _Call,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        outcome: str,
        value: object,
    ) -> None:
        """Pickle a finished call's results and write it."""
        stack = self._stack()
        if call not in stack:
            return
        del stack[stack.index(call) :]
        if call.side_effect is not None:
            reason = f"side effect during the call ({call.side_effect})"
            self._skipped(call.function, reason)
            return
        self._local.busy = True
        try:
            reason = None
            try:
                after = pickle.dumps(
                    (args, kwargs, outcome, value), protocol=self._protocol
                )
            except Exception:
                after = b""
                reason = self._unpicklable_after(args, kwargs, outcome, value)
            if reason is None and len(after) > MAX_PAYLOAD_BYTES:
                reason = f"results exceed {MAX_PAYLOAD_BYTES} bytes pickled"
        finally:
            self._local.busy = False
        if reason is not None:
            self._skipped(call.function, reason)
            return
        with self._lock:
            offset = self._offset
            _append(self._blobs, call.before)
            _append(self._blobs, after)
            self._offset += len(call.before) + len(after)
            self._seq += 1
            seq = self._seq
        self._event(
            {
                "type": "call",
                "function": call.function,
                "seq": seq,
                "t": time.monotonic_ns(),
                "blob": f"blobs-{self._pid}.bin",
                "offset": offset,
                "before": len(call.before),
                "after": len(after),
            }
        )
        self._note_modules()

    def _note_modules(self) -> None:
        """Record modules loaded since the last captured call.

        Each is recorded with its file, so replay processes can import
        the libraries among them before turning their guard on, and a
        library's import-time events are not taken for the call's.
        """
        loaded = list(sys.modules.items())
        with self._lock:
            if len(loaded) == self._modules_count:
                return
            self._modules_count = len(loaded)
            new = {
                name: _module_file(module)
                for name, module in loaded
                if name not in self._modules
            }
            self._modules.update(new)
        if new:
            self._event({"type": "modules", "names": new})

    def _unpicklable_after(
        self,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        outcome: str,
        value: object,
    ) -> str:
        """Say what of a finished call could not be pickled."""
        try:
            pickle.dumps((args, kwargs), protocol=self._protocol)
        except Exception:
            return "the arguments could not be pickled after the call"
        what = "exception" if outcome == "raise" else "result"
        return f"the {what} could not be pickled ({_type_name(value)})"

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        """Mark this thread's calls in progress on a side effect."""
        stack: list[_Call] | None = getattr(self._local, "stack", None)
        if not stack:
            return
        what = _side_effect(event, args)
        if what is None:
            return
        for call in stack:
            if call.side_effect is None:
                call.side_effect = what


class _Loader:
    """Delegates to a module's loader, then wraps changed functions."""

    def __init__(self, inner: Any, recorder: _Recorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def create_module(self, spec: Any) -> Any:
        """Delegate module creation."""
        create = getattr(self._inner, "create_module", None)
        return None if create is None else create(spec)

    def exec_module(self, module: ModuleType) -> None:
        """Run the module, then wrap its changed functions."""
        self._inner.exec_module(module)
        self._recorder.patch(module)

    def __getattr__(self, name: str) -> Any:
        """Delegate everything else, such as ``get_source``."""
        return getattr(self._inner, name)


class _Finder:
    """Meta path finder that lets ``adopt`` wrap each module's loader.

    It finds the module with the other finders, then passes the spec to
    ``adopt``, which may replace ``spec.loader``.
    """

    def __init__(self, adopt: Callable[[Any], None]) -> None:
        self._adopt = adopt
        self._local = threading.local()

    def find_spec(
        self, fullname: str, path: Any = None, target: Any = None
    ) -> Any:
        """Find a module with the other finders, then adopt it."""
        if getattr(self._local, "busy", False):
            return None
        self._local.busy = True
        try:
            spec = None
            for finder in sys.meta_path:
                find = getattr(finder, "find_spec", None)
                if finder is self or find is None:
                    continue
                spec = find(fullname, path, target)
                if spec is not None:
                    break
        finally:
            self._local.busy = False
        if spec is not None and spec.loader is not None:
            self._adopt(spec)
        return spec


def _install_capture(config_path: str) -> None:
    """Install the capture hook in this process (called by the hook)."""
    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)
    recorder = _Recorder(config)
    # An import inside a captured call would otherwise write a .pyc
    # file, which the audit hook cannot tell from the call's own write.
    sys.dont_write_bytecode = True
    sys.addaudithook(recorder.audit)
    sys.meta_path.insert(0, _Finder(recorder.adopt))
    atexit.register(recorder.close)
    os.register_at_fork(after_in_child=recorder.after_fork)


_SITECUSTOMIZE: Final = '''\
"""perfproof differential capture hook, for the capture pass only."""


def _perfproof_capture():
    import importlib.machinery
    import importlib.util
    import os
    import sys
    import traceback

    here = os.path.dirname(os.path.abspath(__file__))
    try:
        spec = importlib.util.spec_from_file_location({name!r}, {source!r})
        module = importlib.util.module_from_spec(spec)
        sys.modules[{name!r}] = module
        spec.loader.exec_module(module)
        getattr(module, {install!r})({config!r})
    except BaseException:
        error = os.path.join({out!r}, "hook-error-%d.txt" % os.getpid())
        with open(error, "w") as f:
            traceback.print_exc(file=f)
    rest = [p for p in sys.path if os.path.abspath(p or ".") != here]
    found = importlib.machinery.PathFinder.find_spec("sitecustomize", rest)
    if found is not None and found.loader is not None:
        found.loader.exec_module(importlib.util.module_from_spec(found))


_perfproof_capture()
'''


# --------------------------------------------------------------------
# Replay and generation (fresh processes, run as a script)
# --------------------------------------------------------------------


class _Guard:
    """Audit hook that blocks side effects inside a call.

    ``suspended`` is above zero, in the importing thread only, while a
    library (a module outside both trees) is being imported, or while a
    module the benchmark had loaded (in ``recorded``) runs for the
    first time in this process, so their import-time events pass; see
    `_GatedLoader`. Being per thread, an import in one thread never
    unguards another, the call's own thread included. ``executed``
    holds the recorded modules that have run here, so running one again
    (a reload, or a re-import after popping it) stays guarded; it is
    claimed under ``lock``, so only one thread gets the unguarded first
    run.
    """

    def __init__(
        self, trees: Sequence[str], recorded: Iterable[str] = ()
    ) -> None:
        self.active = False
        self.effect: str | None = None
        self.recorded = frozenset(recorded)
        self.executed: set[str] = set()
        self.lock = threading.Lock()
        self.trees = list(trees)
        self._local = threading.local()

    @property
    def suspended(self) -> int:
        """Nested suspensions in the current thread."""
        count: int = getattr(self._local, "suspended", 0)
        return count

    @suspended.setter
    def suspended(self, count: int) -> None:
        self._local.suspended = count

    def claim_first_run(self, name: str) -> bool:
        """Mark a recorded module as run; whether this is its first run.

        The check and the mark happen together under ``lock``, before
        the module runs, so a first run that raises leaves later runs
        guarded.
        """
        with self.lock:
            if name not in self.recorded or name in self.executed:
                return False
            self.executed.add(name)
            return True

    def is_first_run(self, name: str) -> bool:
        """Whether a recorded module has yet to run; claims nothing."""
        with self.lock:
            return name in self.recorded and name not in self.executed

    def install(self) -> None:
        """Install the audit hook and the import gate."""
        sys.addaudithook(self.audit)
        sys.meta_path.insert(0, _Finder(self.adopt))

    def adopt(self, spec: Any) -> None:
        """Gate a module's loading by whether it is a library."""
        library = _is_library(spec.origin, self.trees)
        if spec.origin is None and spec.submodule_search_locations:
            library = all(
                _is_library(p, self.trees)
                for p in spec.submodule_search_locations
            )
        spec.loader = _GatedLoader(
            spec.loader, self, name=str(spec.name), library=library
        )

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        """Block a side-effect event while a call is active."""
        if not self.active or self.suspended:
            return
        what = _side_effect(event, args)
        if what is None:
            return
        if self.effect is None:
            self.effect = what
        raise _BlockedError(what)


class _GatedLoader:
    """Delegates to a module's loader with the guard set for it.

    A library's import suspends the guard: its import-time code (NumPy
    raises ``os.putenv`` and ``ctypes.dlopen``) is not the call's doing.
    So does the first run in this process of a tree module the
    benchmark had loaded: the benchmark ran that code too, so an import
    of it moved into the function, by any means (a ``LazyLoader``
    included), is not the call's side effect. Any other tree module
    re-arms the guard, even when a library or a recorded module imports
    it, and so does a second run of a recorded module.
    """

    def __init__(
        self, inner: Any, guard: _Guard, *, name: str, library: bool
    ) -> None:
        self._inner = inner
        self._guard = guard
        self._name = name
        self._library = library

    @contextlib.contextmanager
    def _gate(self, *, suspend: bool) -> Generator[None]:
        """Suspend or re-arm the guard while the module loads."""
        guard = self._guard
        saved = guard.suspended
        guard.suspended = saved + 1 if suspend else 0
        try:
            yield
        finally:
            guard.suspended = saved

    def create_module(self, spec: Any) -> Any:
        """Delegate module creation, gated."""
        create = getattr(self._inner, "create_module", None)
        if create is None:
            return None
        suspend = self._library or self._guard.is_first_run(self._name)
        with self._gate(suspend=suspend):
            return create(spec)

    def exec_module(self, module: ModuleType) -> None:
        """Run the module, gated."""
        first = self._guard.claim_first_run(self._name)
        with self._gate(suspend=self._library or first):
            self._inner.exec_module(module)

    def __getattr__(self, name: str) -> Any:
        """Delegate everything else, such as ``get_source``."""
        return getattr(self._inner, name)


def _is_library(path: str | None, trees: Sequence[str]) -> bool:
    """Whether a module file belongs to a library, not to a tree.

    A library is built in or frozen, in a ``site-packages`` or
    ``dist-packages`` directory (a virtualenv inside a tree included),
    or anywhere outside both trees. Anything else inside a tree is the
    tree's code, even when the tree lies under the interpreter's own
    prefix (a project in ``/usr/src/app`` with the system Python).
    """
    if not path or not os.path.isabs(path):
        return True
    real = os.path.realpath(path)
    parts = real.split(os.sep)
    if "site-packages" in parts or "dist-packages" in parts:
        return True
    return all(_within(real, t) is None for t in trees)


def _on_alarm(signum: int, frame: FrameType | None) -> None:
    """Stop a call that ran past its time limit."""
    raise _TimeoutError


def _invoke(
    fn: Callable[..., object],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    guard: _Guard,
    seconds: float,
) -> tuple[str, object, str | None]:
    """Call ``fn`` with side effects blocked and a time limit.

    Returns
    -------
    outcome : {"return", "raise", "timeout"}
    value : object
        The result or the exception.
    effect : str or None
        The side effect the call attempted, if any.
    """
    guard.effect = None
    guard.active = True
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            outcome, value = "return", fn(*args, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    except _TimeoutError:
        outcome, value = "timeout", None
    except BaseException as exc:
        outcome, value = "raise", exc
    finally:
        guard.active = False
    return outcome, value, guard.effect


def _recorded_names(job: Mapping[str, Any]) -> list[str]:
    """Names of the modules the benchmark had loaded, from a job."""
    return [str(pair[0]) for pair in job.get("preload", ())]


def _state(value: object) -> object:
    """Return what pickle would store for an object without ``__eq__``.

    Raises
    ------
    Exception
        Whatever ``__reduce_ex__`` raises.
    """
    reduced: Any = value.__reduce_ex__(pickle.HIGHEST_PROTOCOL)
    if isinstance(reduced, str):
        return reduced
    parts = list(typing.cast("tuple[Any, ...]", reduced))
    for i in (3, 4):
        if len(parts) > i and parts[i] is not None:
            parts[i] = list(parts[i])
    return tuple(parts[1:])


def _is_ndarray(value: object) -> bool:
    """Whether ``value`` is a NumPy array, without importing NumPy."""
    kind = type(value)
    return kind.__name__ == "ndarray" and kind.__module__ == "numpy"


def _first_difference(
    expected: object,
    actual: object,
    where: str,
    seen: set[tuple[int, int]],
) -> tuple[str, str, str] | None:
    """Return where and how two values first differ, or ``None``.

    The rules are those in the module docstring under "How results are
    compared". Cycles are followed once.
    """
    if expected is actual:
        return None
    if type(expected) is not type(actual):
        return (
            f"{where} type",
            f"{_type_name(expected)}: {_short_repr(expected)}",
            f"{_type_name(actual)}: {_short_repr(actual)}",
        )
    key = (id(expected), id(actual))
    if key in seen:
        return None
    a: Any = expected
    b: Any = actual
    differ = (where, _short_repr(expected), _short_repr(actual))
    if isinstance(a, (float, complex)):
        same = a == b or (a != a and b != b)
        return None if same else differ
    if isinstance(a, (list, tuple)):
        seen.add(key)
        xs = typing.cast("Sequence[object]", a)
        ys = typing.cast("Sequence[object]", b)
        if len(xs) != len(ys):
            return (f"{where} length", str(len(xs)), str(len(ys)))
        for i, (x, y) in enumerate(zip(xs, ys, strict=True)):
            found = _first_difference(x, y, f"{where}[{i}]", seen)
            if found is not None:
                return found
        return None
    if isinstance(a, dict):
        seen.add(key)
        da = typing.cast("dict[object, object]", a)
        db = typing.cast("dict[object, object]", b)
        if list(da) != list(db):
            return (
                f"{where} keys",
                _short_repr(list(da)),
                _short_repr(list(db)),
            )
        for k in da:
            found = _first_difference(da[k], db[k], f"{where}[{k!r}]", seen)
            if found is not None:
                return found
        return None
    if _is_ndarray(a):
        return _array_difference(a, b, where)
    if type(a).__eq__ is object.__eq__:
        seen.add(key)
        try:
            states = (_state(a), _state(b))
        except Exception:
            return (f"{where} (not comparable)", differ[1], differ[2])
        return _first_difference(*states, where, seen)
    return _eq_difference(a, b, differ)


def _array_difference(
    a: Any, b: Any, where: str
) -> tuple[str, str, str] | None:
    """Compare two NumPy arrays exactly, NaN equal to NaN."""
    if a.dtype != b.dtype or a.shape != b.shape:
        return (
            f"{where} dtype and shape",
            f"{a.dtype} {a.shape}",
            f"{b.dtype} {b.shape}",
        )
    if a.dtype.kind == "O":
        return _first_difference(a.tolist(), b.tolist(), where, set())
    numpy = sys.modules["numpy"]
    equal = numpy.array_equal(a, b, equal_nan=a.dtype.kind in "fc")
    return None if equal else (where, _short_repr(a), _short_repr(b))


def _eq_difference(
    a: Any, b: Any, differ: tuple[str, str, str]
) -> tuple[str, str, str] | None:
    """Compare with the values' own ``==``."""
    try:
        equal = a == b
        if equal is True:
            return None
        if equal is False:
            both_nan = bool(a != a) and bool(b != b)
            return None if both_nan else differ
        return None if bool(equal) else differ
    except Exception:
        equals = getattr(a, "equals", None)
        try:
            if callable(equals) and equals(b) is True:
                return None
        except Exception:  # noqa: S110 - incomparable
            pass
        return (f"{differ[0]} (not comparable)", differ[1], differ[2])


def _compare(
    recorded: tuple[Any, ...],
    outcome: str,
    value: object,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[str, str, str] | None:
    """Compare a replayed call with the recorded one."""
    exp_args, exp_kwargs, exp_outcome, expected = recorded
    if exp_outcome != outcome:
        return (
            "outcome",
            f"{exp_outcome} {_short_repr(expected)}",
            f"{outcome} {_short_repr(value)}",
        )
    if outcome == "raise":
        if type(expected) is not type(value):
            return (
                "exception type",
                _type_name(expected),
                _type_name(value),
            )
        found = _first_difference(
            typing.cast("BaseException", expected).args,
            typing.cast("BaseException", value).args,
            "exception args",
            set(),
        )
    else:
        found = _first_difference(expected, value, "return value", set())
    pairs = [
        (f"argument {i} after the call", x, y)
        for i, (x, y) in enumerate(zip(exp_args, args, strict=True))
    ] + [
        (f"argument {k!r} after the call", exp_kwargs[k], kwargs[k])
        for k in kwargs
    ]
    for where, x, y in pairs:
        if found is not None:
            break
        found = _first_difference(x, y, where, set())
    return found


_NOT_PRELOADED: Final = frozenset(
    {
        "__main__",
        "__mp_main__",
        "__parents_main__",
        _AGENT_MODULE,
        "sitecustomize",
        "usercustomize",
    }
)


def _preload(
    modules: Iterable[Sequence[str | None]], trees: Sequence[str]
) -> None:
    """Import the libraries the benchmark had loaded, guard off.

    ``modules`` holds ``(name, file)`` pairs as the capture recorded
    them. Only libraries are imported (see `_is_library`): a module in
    either tree imports when the code under test asks for it, under the
    guard, and its import-time code never runs here otherwise. A module
    that fails to import here is left alone: the call will import it,
    or fail, under the guard.
    """
    for name, file in modules:
        if (
            name is None
            or name in sys.modules
            or name in _NOT_PRELOADED
            or not _is_library(file, trees)
        ):
            continue
        with contextlib.suppress(BaseException):
            importlib.import_module(name)


def _enter_tree(
    job: Mapping[str, Any],
) -> tuple[object, str, Callable[..., object]]:
    """Import the job's module from its tree and find the function.

    Raises
    ------
    Exception
        If the import fails, the module comes from another file, or
        the function cannot be found.
    """
    sys.path[:] = list(job["sys_path"])
    importlib.invalidate_caches()
    _preload(job.get("preload", ()), job["trees"])
    module = importlib.import_module(job["module"])
    loaded = os.path.realpath(str(getattr(module, "__file__", "")))
    expected = os.path.realpath(job["file"])
    if loaded != expected:
        msg = f"{job['module']} was imported from {loaded}, not {expected}"
        raise ImportError(msg)
    return _resolve(module, job["qualname"])


def _write_line(f: Any, record: Mapping[str, Any]) -> None:
    """Write one JSON line and flush it, so a crash loses no result."""
    f.write(json.dumps(record) + "\n")
    f.flush()


def _replay(job: Mapping[str, Any], out_path: str) -> None:
    """Run the job's calls and write one result line per call.

    ``job["mode"]`` is ``"replay"`` (baseline: run each call and
    compare it with the recording), ``"run"`` (candidate: run each call,
    append its pickled results to ``job["results"]``, then compare it
    with the recording when that loads here, as ``self_check``) or
    ``"compare"`` (baseline: compare the candidate's pickled results
    with the recording, running nothing).
    """
    guard = _Guard(job["trees"], _recorded_names(job))
    mode = str(job["mode"])
    seconds = float(job["timeout"])
    with (
        open(out_path, "a", encoding="utf-8") as out,
        open(job.get("results", os.devnull), "ab") as results,
    ):
        guard.install()
        signal.signal(signal.SIGALRM, _on_alarm)
        try:
            _, _, fn = _enter_tree(job)
        except BaseException as exc:
            _write_line(
                out,
                {
                    "id": None,
                    "status": "setup_error",
                    "message": f"{type(exc).__name__}: {exc}",
                },
            )
            return
        stopped = False
        offset = 0
        for ref in job["calls"]:
            if stopped:
                result: dict[str, Any] = {"status": "not_run"}
            else:
                # The recorded call loads with the guard off: its
                # arguments may need a library the guard would block.
                loaded = _load_call(ref, need_recorded=mode != "run")
                if isinstance(loaded, dict):
                    result = loaded
                elif mode == "compare":
                    result = _compare_one(loaded, ref, guard, seconds)
                elif mode == "replay":
                    result = _replay_one(fn, loaded, guard, seconds)
                else:
                    result = _run_one(fn, loaded, guard, seconds)
                if mode == "run":
                    data = result.pop("data", None)
                    if isinstance(data, bytes):
                        results.write(data)
                        results.flush()
                        result["offset"], result["length"] = offset, len(data)
                        offset += len(data)
                stopped = result["status"] == "timeout"
            result["id"] = ref["id"]
            _write_line(out, result)


def _read_pickles(path: str, offset: int, *lengths: int) -> list[bytes]:
    """Read consecutive pickles from a blob file."""
    with open(path, "rb") as f:
        f.seek(offset)
        return [f.read(n) for n in lengths]


def _call_text(qualname: str, args: tuple[Any, ...], kwargs: Any) -> str:
    """Render a call for a report."""
    shown = ", ".join(
        [_short_repr(a) for a in args]
        + [f"{k}={_short_repr(v)}" for k, v in dict(kwargs).items()]
    )
    return _short_repr(f"{qualname}({shown})")[1:-1]


@dataclass(slots=True)
class _Loaded:
    """A recorded call, unpickled in a replay process."""

    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    recorded: tuple[Any, ...] | None
    """The recorded arguments after the call, outcome and value;
    ``None`` when they did not load here."""
    recorded_error: str | None
    """Why the recorded results did not load, if they did not."""
    call: str


def _load_call(
    ref: Mapping[str, Any], *, need_recorded: bool
) -> _Loaded | dict[str, Any]:
    """Load a recorded call's arguments and results.

    Returns a ``load_error`` result instead if the arguments cannot be
    loaded, or the results cannot and ``need_recorded`` is set. In the
    candidate tree the results may legitimately fail to load, for
    example when the candidate gives a class ``__slots__``; they only
    feed its own extra comparison there.
    """
    try:
        before, after = _read_pickles(
            ref["blob"],
            int(ref["offset"]),
            int(ref["before"]),
            int(ref["after"]),
        )
        # Unpickling the benchmark's data happens only in replay
        # processes, each running one tree's code.
        args, kwargs = pickle.loads(before)  # noqa: S301
    except BaseException as exc:
        return {
            "status": "load_error",
            "message": f"{type(exc).__name__}: {exc}",
        }
    call = _call_text(ref["qualname"], args, kwargs)
    try:
        expected = pickle.loads(after)  # noqa: S301
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        if need_recorded:
            return {"status": "load_error", "message": error}
        return _Loaded(args, kwargs, None, error, call)
    return _Loaded(args, kwargs, expected, None, call)


def _replay_one(
    fn: Callable[..., object],
    loaded: _Loaded,
    guard: _Guard,
    seconds: float,
) -> dict[str, Any]:
    """Replay one recorded call in the baseline and compare it."""
    args, kwargs, call = loaded.args, loaded.kwargs, loaded.call
    outcome, value, effect = _invoke(fn, args, kwargs, guard, seconds)
    if effect is not None:
        return {"status": "side_effect", "message": effect, "call": call}
    if outcome == "timeout":
        return {"status": "timeout", "call": call}
    found = _compare(loaded.recorded or (), outcome, value, args, kwargs)
    if found is None:
        return {"status": "match", "call": call}
    where, expected, actual = found
    return {
        "status": "differ",
        "where": where,
        "expected": expected,
        "actual": actual,
        "call": call,
    }


def _run_one(
    fn: Callable[..., object],
    loaded: _Loaded,
    guard: _Guard,
    seconds: float,
) -> dict[str, Any]:
    """Run one call in the candidate; return its pickled results.

    The pickle goes in ``data``. The result is then also compared here,
    in ``self_check``, because pickling can change a value (a
    ``Fraction`` built unnormalised unpickles normalised). The
    comparison in the baseline tree is the one the candidate's code
    cannot reach; this one only adds mismatches. It is skipped, with
    ``self_check_skipped`` saying why, when the recorded results do not
    load in this tree. The result is pickled first, so a comparison
    that mutates it cannot change what the baseline tree sees.
    """
    args, kwargs = loaded.args, loaded.kwargs
    outcome, value, effect = _invoke(fn, args, kwargs, guard, seconds)
    if effect is not None:
        return {"status": "side_effect", "message": effect}
    if outcome == "timeout":
        return {"status": "timeout"}
    # Pickling runs the candidate's own reduce methods, so it gets the
    # guard and the time limit too.
    done, data, effect = _invoke(
        pickle.dumps,
        ((args, kwargs, outcome, value), pickle.HIGHEST_PROTOCOL),
        {},
        guard,
        seconds,
    )
    if effect is not None:
        return {"status": "side_effect", "message": effect}
    if done == "timeout":
        return {"status": "timeout"}
    if done == "raise" or not isinstance(data, bytes):
        what = "exception" if outcome == "raise" else "result"
        return {
            "status": "unpicklable",
            "message": f"its {what} could not be pickled "
            f"({_type_name(value)}: {_type_name(data)})",
        }
    if loaded.recorded is None:
        return {
            "status": "ran",
            "data": data,
            "self_check": None,
            "self_check_skipped": loaded.recorded_error,
        }
    checked, found, effect = _invoke(
        _compare,
        (loaded.recorded, outcome, value, args, kwargs),
        {},
        guard,
        seconds,
    )
    where = "comparison in the candidate's process"
    self_check: Any = found
    if effect is not None:
        self_check = (where, "no side effect", f"attempted {effect}")
    elif checked == "timeout":
        self_check = (where, "finished", f"did not finish in {seconds} s")
    elif checked == "raise":
        self_check = (where, "completed", f"raised {_type_name(found)}")
    return {
        "status": "ran",
        "data": data,
        "self_check": None if self_check is None else list(self_check),
    }


def _compare_one(
    loaded: _Loaded, ref: Mapping[str, Any], guard: _Guard, seconds: float
) -> dict[str, Any]:
    """Compare a candidate's pickled results with the recording.

    Runs in the baseline tree, so the candidate's results unpickle
    into the baseline's classes. The recording was loaded before, with
    the guard off; loading the candidate's results and comparing run
    under the side-effect guard and the time limit.
    """
    holder: dict[str, Any] = {"call": loaded.call}

    def load_and_compare() -> None:
        try:
            (data,) = _read_pickles(
                ref["result_blob"],
                int(ref["result_offset"]),
                int(ref["result_length"]),
            )
            got = pickle.loads(data)  # noqa: S301 - baseline tree
            args, kwargs, outcome, value = got
        except BaseException as exc:
            holder["status"] = "load_error"
            holder["message"] = (
                "its result could not be loaded against the baseline: "
                f"{type(exc).__name__}: {exc}"
            )
            return
        found = _compare(loaded.recorded or (), outcome, value, args, kwargs)
        if found is None:
            holder["status"] = "match"
            return
        holder["status"] = "differ"
        holder["where"], holder["expected"], holder["actual"] = found

    outcome, value, effect = _invoke(load_and_compare, (), {}, guard, seconds)
    if effect is not None:
        return {
            "status": "side_effect",
            "message": f"comparing its result attempted {effect}",
        }
    if outcome == "timeout":
        return {"status": "timeout"}
    if outcome == "raise":
        return {
            "status": "load_error",
            "message": "its result could not be compared: "
            f"{type(value).__name__}: {value}",
        }
    return holder


def _generate(job: Mapping[str, Any], out_dir: str) -> None:
    """Run Hypothesis inputs through the baseline and record them.

    Writes ``events.jsonl`` and ``blobs.bin`` in ``out_dir``, in the
    capture's format, plus ``note`` events for anything that stopped
    generation.
    """
    events = open(  # noqa: SIM115 - closed at the end
        os.path.join(out_dir, "events.jsonl"), "a", encoding="utf-8"
    )
    blobs = open(os.path.join(out_dir, "blobs.bin"), "ab")  # noqa: SIM115
    try:
        _generate_into(job, events, blobs, out_dir)
    finally:
        events.close()
        blobs.close()


def _generate_into(
    job: Mapping[str, Any], events: Any, blobs: Any, out_dir: str
) -> None:
    """Body of `_generate`."""
    guard = _Guard(job["trees"], _recorded_names(job))
    guard.install()
    signal.signal(signal.SIGALRM, _on_alarm)

    def note(message: str) -> None:
        _write_line(events, {"type": "note", "message": message})

    try:
        owner, kind, fn = _enter_tree(job)
    except BaseException as exc:
        note(f"the baseline could not be set up: {type(exc).__name__}: {exc}")
        return
    plan = _plan_inputs(owner, kind, fn)
    if isinstance(plan, str):
        note(plan)
        return
    try:
        from hypothesis import (
            HealthCheck,
            Phase,
            given,
            settings,
        )
        from hypothesis import strategies as st
    except Exception as exc:
        note(f"Hypothesis could not be imported: {type(exc).__name__}: {exc}")
        return
    prefix, positional, hints = plan
    strategy = st.fixed_dictionaries(
        {name: st.from_type(hint) for name, hint in hints.items()}
    )
    seconds = float(job["timeout"])
    state = {"seq": 0, "offset": 0, "stopped": False}

    def record(drawn: dict[str, Any]) -> None:
        if state["stopped"]:
            return
        args = (*prefix, *(drawn[n] for n in positional))
        kwargs = {k: v for k, v in drawn.items() if k not in positional}
        try:
            before = pickle.dumps((args, kwargs), pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            skip(f"generated arguments could not be pickled ({exc})")
            return
        outcome, value, effect = _invoke(fn, args, kwargs, guard, seconds)
        if effect is not None:
            skip(f"the baseline attempted a side effect ({effect})")
            return
        if outcome == "timeout":
            note(
                f"generation stopped: the baseline did not finish within "
                f"{seconds} s on a generated input"
            )
            state["stopped"] = True
            return
        try:
            after = pickle.dumps(
                (args, kwargs, outcome, value), pickle.HIGHEST_PROTOCOL
            )
        except Exception:
            skip(f"the baseline's {outcome} value could not be pickled")
            return
        blobs.write(before + after)
        blobs.flush()
        state["seq"] += 1
        _write_line(
            events,
            {
                "type": "call",
                "seq": state["seq"],
                "t": state["seq"],
                "blob": os.path.join(out_dir, "blobs.bin"),
                "offset": state["offset"],
                "before": len(before),
                "after": len(after),
            },
        )
        state["offset"] += len(before) + len(after)

    def skip(reason: str) -> None:
        _write_line(events, {"type": "skip", "reason": reason})

    run = settings(
        max_examples=int(job["examples"]),
        derandomize=True,
        database=None,
        deadline=None,
        phases=[Phase.generate],
        suppress_health_check=typing.cast("Any", list(HealthCheck)),
    )(given(strategy)(record))
    try:
        run()
    except Exception as exc:
        note(f"generation stopped: {type(exc).__name__}: {exc}")


def _plan_inputs(
    owner: object, kind: str, fn: Callable[..., object]
) -> str | tuple[tuple[object, ...], list[str], dict[str, Any]]:
    """Work out how to generate inputs for ``fn`` from its type hints.

    Returns
    -------
    str or tuple
        Why inputs cannot be generated; or the leading arguments
        (``cls`` for a class method), the positional-only parameter
        names, and the type hint of each parameter to generate.
    """
    if kind == "method":
        return "a method needs an instance, so no inputs are generated"
    try:
        hints = typing.get_type_hints(fn)
        params = list(inspect.signature(fn).parameters.values())
    except Exception as exc:
        return f"type hints could not be read: {type(exc).__name__}: {exc}"
    prefix: tuple[object, ...] = ()
    if kind == "classmethod":
        prefix, params = (owner,), params[1:]
    wanted: dict[str, Any] = {}
    positional: list[str] = []
    for p in params:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if p.name not in hints:
            if p.default is not p.empty:
                continue
            return f"parameter {p.name!r} has no type hint"
        wanted[p.name] = hints[p.name]
        if p.kind is p.POSITIONAL_ONLY:
            positional.append(p.name)
    if not wanted:
        return "no parameter to generate inputs for"
    return prefix, positional, wanted


def _main(argv: Sequence[str]) -> int:
    """Entry point of replay and generation processes."""
    sys.dont_write_bytecode = True
    mode, job_path, out = argv[1:4]
    with open(job_path, encoding="utf-8") as f:
        job = json.load(f)
    if mode == "replay":
        _replay(job, out)
    elif mode == "generate":
        _generate(job, out)
    else:
        return 2
    return 0


# --------------------------------------------------------------------
# Orchestration (perfproof's process)
# --------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Ref:
    """A recorded call: where its pickles are. Never unpickled here."""

    id: int
    source: str
    blob: str
    offset: int
    before: int
    after: int
    order: tuple[int, int, int]


@dataclass(slots=True)
class _Target:
    """What the capture learned about one function."""

    function: str
    changed: ChangedFunction
    module: str
    executable: str
    sys_path: list[str]
    calls: list[_Ref] = field(default_factory=list[_Ref])
    capture_skips: Counter[str] = field(default_factory=Counter[str])
    generated: list[_Ref] = field(default_factory=list[_Ref])


def _run(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: float,
    logs: Path,
) -> tuple[int, bool]:
    """Run a process with output to ``logs``.stdout/.stderr.

    Returns
    -------
    exit_code : int
        Negative for a signal.
    timed_out : bool
        Whether its process group was killed at the time limit.

    Raises
    ------
    OSError
        If it cannot be started.
    """
    with (
        open(f"{logs}.stdout", "wb") as out,
        open(f"{logs}.stderr", "wb") as err,
    ):
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            code, timed_out = proc.wait(timeout=timeout), False
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            code, timed_out = proc.wait(), True
        # Whatever the process left running in its group, such as a
        # multiprocessing pool's workers, goes with it.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        return code, timed_out


def _tail(path: Path) -> str:
    """Return the end of a log file."""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    return data[-_TAIL_BYTES:].decode(errors="replace")


def _read_events(path: Path, notes: list[str]) -> Iterator[dict[str, Any]]:
    """Yield the JSON events of one file; note a torn last line."""
    with path.open("rb") as f:
        for raw in f:
            try:
                event = json.loads(raw)
            except ValueError:
                notes.append(f"{path.name}: an incomplete event was ignored")
                continue
            if isinstance(event, dict):
                yield typing.cast("dict[str, Any]", event)


def _within(path: str, root: str) -> str | None:
    """Return ``path`` relative to ``root`` if it lies inside it."""
    real, base = os.path.realpath(path), os.path.realpath(root)
    if real == base:
        return "."
    if real.startswith(base + os.sep):
        return os.path.relpath(real, base)
    return None


def _map(path: str, src: Path, dst: Path) -> str:
    """Map a path inside ``src`` to the same place inside ``dst``."""
    rel = _within(path, str(src))
    return path if rel is None else str(dst / rel)


def _capture(
    spec: BenchmarkSpec,
    baseline: Path,
    changes: ChangeSet,
    out: Path,
    *,
    env: Mapping[str, str],
    limit: int,
    timeout: float,
) -> Path:
    """Run the capture pass; return the directory the events are in.

    Raises
    ------
    CaptureError
        If the benchmark fails or times out under capture.
    DifferentialError
        If it cannot be started.
    """
    hook = out / "hook"
    events = out / "events"
    hook.mkdir(parents=True)
    events.mkdir()
    targets: dict[str, list[str]] = {}
    for fn in changes.functions:
        targets.setdefault(fn.path, []).append(fn.qualname)
    config = out / "config.json"
    config.write_text(
        json.dumps(
            {
                "out": str(events),
                "limit": limit,
                "tree": str(baseline),
                "targets": targets,
            }
        ),
        encoding="utf-8",
    )
    (hook / "sitecustomize.py").write_text(
        _SITECUSTOMIZE.format(
            name=_AGENT_MODULE,
            install=_install_capture.__name__,
            source=str(Path(__file__).resolve()),
            config=str(config),
            out=str(events),
        ),
        encoding="utf-8",
    )
    capture_env = dict(env)
    existing = capture_env.get("PYTHONPATH")
    capture_env["PYTHONPATH"] = (
        str(hook) if not existing else f"{hook}{os.pathsep}{existing}"
    )
    logs = out / "benchmark"
    try:
        code, timed_out = _run(
            spec.command,
            cwd=baseline / spec.cwd,
            env=capture_env,
            timeout=timeout,
            logs=logs,
        )
    except OSError as exc:
        msg = f"the benchmark could not start under capture: {exc}"
        raise DifferentialError(msg) from exc
    stdout, stderr = Path(f"{logs}.stdout"), Path(f"{logs}.stderr")
    if timed_out or code != 0:
        why = f"timed out after {timeout} s" if timed_out else f"exited {code}"
        msg = (
            f"the baseline benchmark {why} under capture; stderr ends: "
            f"{_tail(stderr)}"
        )
        raise CaptureError(msg, stdout, stderr)
    return events


def _collect(
    events: Path, changes: ChangeSet, limit: int
) -> tuple[
    list[_Target], list[NotChecked], list[str], list[tuple[str, str | None]]
]:
    """Read the capture's events into one `_Target` per function.

    Also returns the modules the benchmark's processes had loaded by
    the time their calls were captured.
    """
    notes: list[str] = []
    modules: dict[str, str | None] = {}
    for error in sorted(events.glob("hook-error-*.txt")):
        lines = _tail(error).strip().splitlines() or ["(empty)"]
        notes.append(f"the capture hook failed in {error.stem}: {lines[-1]}")
    by_path = {(f.path, f.qualname): f for f in changes.functions}
    targets: dict[str, _Target] = {}
    statuses: dict[tuple[str, str], str] = {}
    calls: dict[str, list[_Ref]] = {}
    skips: dict[str, Counter[str]] = {}
    beyond: Counter[str] = Counter()
    examined: Counter[tuple[int, str]] = Counter()
    processes: set[int] = set()
    exited: set[int] = set()
    for path in sorted(events.glob("events-*.jsonl")):
        for event in _read_events(path, notes):
            kind, pid = event.get("type"), int(event.get("pid", 0))
            if kind == "process":
                processes.add(pid)
            elif kind == "exit":
                exited.add(pid)
                beyond.update(event.get("beyond", {}))
            elif kind == "module":
                _module_event(event, by_path, targets, statuses)
            elif kind == "call":
                fid = str(event["function"])
                examined[pid, fid] += 1
                calls.setdefault(fid, []).append(
                    _Ref(
                        id=0,
                        source="captured",
                        blob=str(events / str(event["blob"])),
                        offset=int(event["offset"]),
                        before=int(event["before"]),
                        after=int(event["after"]),
                        order=(int(event["t"]), pid, int(event["seq"])),
                    )
                )
            elif kind == "modules":
                for name, file in dict(event["names"]).items():
                    modules.setdefault(
                        str(name), None if file is None else str(file)
                    )
            elif kind == "skip":
                fid = str(event["function"])
                examined[pid, fid] += 1
                skips.setdefault(fid, Counter())[str(event["reason"])] += 1
    full = {pid for (pid, _), n in examined.items() if n >= limit}
    notes.extend(
        f"process {pid} ended without writing its final counts, so its "
        "calls beyond the capture limit were not counted"
        for pid in sorted((processes - exited) & full)
    )
    over = f"beyond the capture limit of {limit} calls"
    for fid, target in targets.items():
        ordered = sorted(calls.get(fid, []), key=lambda r: r.order)
        target.calls = [
            _Ref(i, r.source, r.blob, r.offset, r.before, r.after, r.order)
            for i, r in enumerate(ordered[:limit])
        ]
        target.capture_skips = skips.get(fid, Counter())
        extra = len(ordered) - len(target.calls) + beyond.get(fid, 0)
        if extra:
            target.capture_skips[over] += extra
    not_checked = _unwrapped(changes, statuses, processes, notes)
    return list(targets.values()), not_checked, notes, sorted(modules.items())


def _module_event(
    event: Mapping[str, Any],
    by_path: Mapping[tuple[str, str], ChangedFunction],
    targets: dict[str, _Target],
    statuses: dict[tuple[str, str], str],
) -> None:
    """Record the first import of a changed module under each name."""
    path = Path(str(event["file"])).as_posix()
    for qualname, status in dict(event["functions"]).items():
        statuses.setdefault((path, qualname), str(status))
        fid = f"{event['module']}:{qualname}"
        if status != "wrapped" or fid in targets:
            continue
        targets[fid] = _Target(
            function=fid,
            changed=by_path[path, qualname],
            module=str(event["module"]),
            executable=str(event["executable"]),
            sys_path=[str(p) for p in event["path"]],
        )


def _unwrapped(
    changes: ChangeSet,
    statuses: Mapping[tuple[str, str], str],
    processes: set[int],
    notes: list[str],
) -> list[NotChecked]:
    """List the changed functions the capture never wrapped."""
    if not processes:
        notes.append(
            "no benchmark process loaded the capture hook; a Python "
            "benchmark run with -I or -E ignores PYTHONPATH"
        )
    missed: list[NotChecked] = []
    for fn in changes.functions:
        status = statuses.get((fn.path, fn.qualname))
        if status == "wrapped":
            continue
        if status is None:
            status = (
                "no benchmark process loaded the capture hook"
                if not processes
                else "its module was never imported from the baseline "
                "tree while the benchmark ran"
            )
        missed.append(NotChecked(fn.path, fn.qualname, status))
    return missed


@dataclass(frozen=True, slots=True)
class _Context:
    """What every replay and generation process needs."""

    spec: BenchmarkSpec
    trees: Mapping[str, Path]
    env: Mapping[str, str]
    work: Path
    hook: Path
    extra_path: str | None
    preload: list[tuple[str, str | None]]


def _job(ctx: _Context, target: _Target, arm: str) -> dict[str, Any]:
    """Build the parts of a job that place it in one tree."""
    base, root = ctx.trees["baseline"], ctx.trees[arm]
    sys_path = [
        _map(p, base, root)
        for p in target.sys_path
        if _within(p or ".", str(ctx.hook)) is None
    ]
    if ctx.extra_path is not None:
        sys_path.append(ctx.extra_path)
    module, qualname = target.function.split(":", 1)
    return {
        "module": module,
        "qualname": qualname,
        "file": str(root / target.changed.path),
        "sys_path": sys_path,
        "timeout": ctx.spec.timeout_seconds,
        "preload": ctx.preload,
        "trees": [str(ctx.trees["baseline"]), str(ctx.trees["candidate"])],
    }


def _launch(
    ctx: _Context,
    target: _Target,
    arm: str,
    mode: str,
    job: Mapping[str, Any],
    out: str,
    timeout: float,
) -> tuple[int, bool]:
    """Run this module as a script for one job in one tree."""
    stem = f"{_slug(target.function)}.{arm}.{mode}.{time.monotonic_ns()}"
    job_path = ctx.work / f"{stem}.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    executable = _map(target.executable, ctx.trees["baseline"], ctx.trees[arm])
    argv = [executable, "-S", str(Path(__file__).resolve()), mode]
    try:
        return _run(
            [*argv, str(job_path), out],
            cwd=ctx.trees[arm] / ctx.spec.cwd,
            env=ctx.env,
            timeout=timeout,
            logs=ctx.work / stem,
        )
    except OSError as exc:
        msg = f"{executable} could not start for {arm} {mode}: {exc}"
        raise DifferentialError(msg) from exc


def _slug(function: str) -> str:
    """Make a function's name safe as part of a file name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", function)


def _replay_all(
    ctx: _Context,
    target: _Target,
    arm: str,
    mode: str,
    refs: Sequence[_Ref],
    extra: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[int, dict[str, Any]]:
    """Run calls in one tree; return a result for every call.

    ``mode`` is as for `_replay`; ``extra`` adds fields to each call's
    entry in the job, such as where the candidate's results are. In
    ``run`` mode each result that ran gets ``result_blob``.

    A process that dies is blamed on the call it was running, and the
    rest go to a new process. After a call times out, the rest are not
    run, so a version that hangs costs one time limit, not one per
    call.
    """
    results: dict[int, dict[str, Any]] = {}
    remaining = list(refs)
    seconds = ctx.spec.timeout_seconds
    while remaining:
        job = _job(ctx, target, arm)
        job["mode"] = mode
        job["calls"] = [
            {
                "id": r.id,
                "qualname": job["qualname"],
                "blob": r.blob,
                "offset": r.offset,
                "before": r.before,
                "after": r.after,
                **(extra or {}).get(r.id, {}),
            }
            for r in remaining
        ]
        stem = f"{_slug(target.function)}.{arm}.{mode}.{len(results)}"
        out = ctx.work / f"{stem}.out"
        if mode == "run":
            job["results"] = str(ctx.work / f"{stem}.bin")
        code, timed_out = _launch(
            ctx,
            target,
            arm,
            "replay",
            job,
            str(out),
            seconds * len(remaining) + _STARTUP_SECONDS,
        )
        notes: list[str] = []
        lines = list(_read_events(out, notes)) if out.exists() else []
        if lines and lines[0].get("status") == "setup_error":
            for r in remaining:
                results[r.id] = lines[0]
            return results
        for line in lines:
            if line.get("status") == "ran":
                line["result_blob"] = job["results"]
            results[int(line["id"])] = line
        remaining = [r for r in remaining if r.id not in results]
        if not remaining:
            break
        how = "was killed at its time limit" if timed_out else f"exited {code}"
        results[remaining[0].id] = {
            "status": "timeout" if timed_out else "died",
            "message": f"the replay process {how} during this call",
        }
        remaining = remaining[1:]
        if timed_out:
            for r in remaining:
                results[r.id] = {"status": "not_run"}
            break
    return results


def _generate_for(
    ctx: _Context, target: _Target, examples: int, notes: list[str]
) -> list[_Ref]:
    """Generate inputs in the baseline tree; return the calls."""
    out = ctx.work / f"{_slug(target.function)}.generated"
    out.mkdir()
    job = _job(ctx, target, "baseline")
    job["examples"] = examples
    job["timeout"] = GENERATED_CALL_SECONDS
    _, timed_out = _launch(
        ctx,
        target,
        "baseline",
        "generate",
        job,
        str(out),
        GENERATED_CALL_SECONDS * examples + _STARTUP_SECONDS * 2,
    )
    if timed_out:
        notes.append(f"{target.function}: input generation timed out")
    refs: list[_Ref] = []
    skipped: Counter[str] = Counter()
    events = out / "events.jsonl"
    if not events.exists():
        notes.append(f"{target.function}: input generation wrote nothing")
        return refs
    for event in _read_events(events, notes):
        if event.get("type") == "note":
            notes.append(f"{target.function}: {event['message']}")
        elif event.get("type") == "skip":
            skipped[str(event["reason"])] += 1
        elif event.get("type") == "call":
            refs.append(
                _Ref(
                    id=len(target.calls) + len(refs),
                    source="generated",
                    blob=str(event["blob"]),
                    offset=int(event["offset"]),
                    before=int(event["before"]),
                    after=int(event["after"]),
                    order=(0, 0, int(event["seq"])),
                )
            )
    notes.extend(
        f"{target.function}: {n} generated inputs not used: {reason}"
        for reason, n in sorted(skipped.items())
    )
    return refs


def _skip_reason(result: Mapping[str, Any], arm: str) -> str:
    """Why a recorded call was not compared on the candidate."""
    status = result.get("status")
    if status == "not_run":
        return f"not replayed: an earlier call timed out on the {arm}"
    if status == "differ":
        return (
            f"the baseline gave a different {result['where']} when "
            "replayed in a fresh process"
        )
    if status == "side_effect":
        return (
            "the baseline attempted a side effect on replay "
            f"({result['message']})"
        )
    if status == "timeout":
        return "the baseline did not finish in time on replay"
    if status == "setup_error":
        return (
            f"the baseline could not be set up for replay: {result['message']}"
        )
    if status == "load_error":
        return (
            "the captured call could not be loaded in a fresh process: "
            f"{result['message']}"
        )
    return str(result.get("message", status))


def _difference(
    target: _Target,
    ref: _Ref,
    call: str,
    result: Mapping[str, Any],
    seconds: float,
) -> Difference:
    """Describe one candidate mismatch.

    ``call`` is the call as the baseline replay rendered it, so no text
    from the candidate's process names the call.
    """
    from perfproof.schema import Difference

    label = "call" if ref.source == "captured" else "generated input"
    status = result["status"]
    compared = result.get("stage") == "compare"
    if status == "differ":
        return Difference(
            item=f"{target.function} {label} {call}: {result['where']}",
            baseline=str(result["expected"]),
            candidate=str(result["actual"]),
        )
    if status == "side_effect" and not compared:
        what = f"attempted a side effect ({result['message']})"
    elif status == "timeout":
        what = (
            f"comparing its result did not finish within {seconds} s"
            if compared
            else f"did not finish within {seconds} s"
        )
    elif status == "load_error" and not compared:
        what = f"could not load the call: {result['message']}"
    elif status == "setup_error":
        stage = "the comparison" if compared else "replay"
        what = f"could not be set up for {stage}: {result['message']}"
    else:
        what = str(result.get("message", status))
    return Difference(
        item=f"{target.function} {label} {call}",
        baseline="completed",
        candidate=what,
    )


def _candidate_results(
    ctx: _Context, target: _Target, refs: Sequence[_Ref], notes: list[str]
) -> dict[int, dict[str, Any]]:
    """Run calls in the candidate, then compare in the baseline tree.

    The candidate's process runs the calls, compares them itself and
    pickles what they returned; a separate process in the baseline tree
    loads and compares those results. A call mismatches if either
    comparison differs, so the candidate's code can only switch off its
    own comparison, never the baseline's. Calls whose recorded results
    did not load in the candidate tree are judged by the baseline-tree
    comparison alone, with a note.
    """
    ran = _replay_all(ctx, target, "candidate", "run", refs)
    done = [r for r in refs if ran[r.id].get("status") == "ran"]
    unchecked = Counter(
        str(ran[r.id]["self_check_skipped"])
        for r in done
        if ran[r.id].get("self_check_skipped")
    )
    notes.extend(
        f"{target.function}: {n} calls compared only in the baseline "
        f"tree; the recorded results did not load in the candidate tree "
        f"({reason})"
        for reason, n in sorted(unchecked.items())
    )
    locations = {
        r.id: {
            "result_blob": ran[r.id]["result_blob"],
            "result_offset": ran[r.id]["offset"],
            "result_length": ran[r.id]["length"],
        }
        for r in done
    }
    compared = _replay_all(ctx, target, "baseline", "compare", done, locations)
    for key, result in compared.items():
        result["stage"] = "compare"
        own = ran[key].get("self_check")
        if result.get("status") == "match" and own:
            where, expected, actual = (str(part) for part in own)
            compared[key] = {
                "status": "differ",
                "stage": "candidate",
                "where": f"{where} (as compared in the candidate's process)",
                "expected": expected,
                "actual": actual,
            }
    return {**ran, **compared}


def _check_one(
    ctx: _Context, target: _Target, notes: list[str]
) -> DifferentialCheck:
    """Replay one function's calls in both trees and compare."""
    from perfproof.schema import DifferentialCheck, SkippedCalls

    refs = target.calls + target.generated
    base = _replay_all(ctx, target, "baseline", "replay", refs)
    reproduced = [r for r in refs if base[r.id].get("status") == "match"]
    replay_skips: Counter[str] = Counter()
    unused: Counter[str] = Counter()
    for r in refs:
        if base[r.id].get("status") != "match":
            skips = replay_skips if r.source == "captured" else unused
            skips[_skip_reason(base[r.id], "baseline")] += 1
    cand = _candidate_results(ctx, target, reproduced, notes)
    differences: list[Difference] = []
    mismatches = replayed = generated = 0
    for r in reproduced:
        result = cand[r.id]
        if result.get("status") == "not_run":
            arm = "comparison" if result.get("stage") else "candidate"
            skips = replay_skips if r.source == "captured" else unused
            skips[_skip_reason(result, arm)] += 1
            continue
        if r.source == "captured":
            replayed += 1
        else:
            generated += 1
        if result.get("status") == "match":
            continue
        mismatches += 1
        if len(differences) < MAX_DIFFERENCES:
            call = str(base[r.id].get("call", f"#{r.id}"))
            differences.append(
                _difference(target, r, call, result, ctx.spec.timeout_seconds)
            )
    notes.extend(
        f"{target.function}: {n} generated inputs not used: {reason}"
        for reason, n in sorted(unused.items())
    )
    skipped = [
        SkippedCalls(stage="capture", reason=reason, count=n)
        for reason, n in sorted(target.capture_skips.items())
    ] + [
        SkippedCalls(stage="replay", reason=reason, count=n)
        for reason, n in sorted(replay_skips.items())
    ]
    return DifferentialCheck(
        function=target.function,
        captured=len(target.calls),
        replayed=replayed,
        generated=generated,
        skipped=tuple(skipped),
        mismatches=mismatches,
        differences=tuple(differences),
    )


def check(
    spec: BenchmarkSpec,
    trees: Mapping[Arm, Path],
    work_dir: Path,
    *,
    base_env: Mapping[str, str] | None = None,
    changes: ChangeSet | None = None,
    capture_limit: int = DEFAULT_CAPTURE_LIMIT,
    examples: int = DEFAULT_EXAMPLES,
    capture_timeout: float | None = None,
) -> DifferentialResult:
    """Check the candidate's changed functions against the baseline.

    Runs the capture pass (the spec's command once in the baseline
    tree), generates inputs where type hints allow, and replays every
    call in both trees, as the module docstring describes. Nothing here
    runs during a measured round; the caller runs it before the rounds,
    after the spec's reset.

    Parameters
    ----------
    spec : perfproof.schema.BenchmarkSpec
        The benchmark. Its command, working directory, declared
        environment and timeout are used.
    trees : Mapping of {"baseline", "candidate"} to Path
        Root of each version's tree; the baseline is a git worktree at
        the snapshot commit.
    work_dir : Path
        Directory for the hook, the captured calls and the replay logs;
        it must not exist yet.
    base_env : Mapping of str to str, optional
        Environment every process gets before the spec's declared
        variables; the same one the benchmark runs with. Defaults to a
        copy of this process's environment.
    changes : ChangeSet, optional
        The changed functions; found with `changed_functions` if not
        given.
    capture_limit : int, default `DEFAULT_CAPTURE_LIMIT`
        Calls examined per function per process, and captured calls
        kept per function.
    examples : int, default `DEFAULT_EXAMPLES`
        Generated inputs per function; 0 turns generation off.
    capture_timeout : float, optional
        Time limit for the capture pass, in seconds; defaults to
        `CAPTURE_TIMEOUT_FACTOR` times the spec's timeout.

    Returns
    -------
    DifferentialResult
        One check per changed function the benchmark imported, what was
        not checked, and notes.

    Raises
    ------
    ValueError
        If a tree is missing, ``work_dir`` exists, or a limit is not
        positive.
    CaptureError
        If the baseline benchmark fails or times out under capture.
    DifferentialError
        If git fails or a process cannot be started.
    """
    if capture_limit < 1 or examples < 0:
        msg = f"capture_limit {capture_limit} and examples {examples}"
        raise ValueError(msg)
    roots = {arm: Path(trees[arm]).resolve() for arm in trees}
    if set(roots) != {"baseline", "candidate"} or not all(
        p.is_dir() for p in roots.values()
    ):
        msg = f"trees needs a baseline and a candidate directory: {trees}"
        raise ValueError(msg)
    work = Path(work_dir)
    if work.exists():
        msg = f"work_dir {work} already exists"
        raise ValueError(msg)
    work.mkdir(parents=True)
    if changes is None:
        changes = changed_functions(roots["baseline"], roots["candidate"])
    if not changes.functions:
        return DifferentialResult((), changes.not_checked, ())
    env = {**(os.environ if base_env is None else base_env), **spec.env}
    env.setdefault("PYTHONHASHSEED", _HASH_SEED)
    events = _capture(
        spec,
        roots["baseline"],
        changes,
        work / "capture",
        env=env,
        limit=capture_limit,
        timeout=capture_timeout
        or spec.timeout_seconds * CAPTURE_TIMEOUT_FACTOR,
    )
    targets, not_checked, notes, preload = _collect(
        events, changes, capture_limit
    )
    replay = work / "replay"
    replay.mkdir()
    found = importlib.util.find_spec("hypothesis")
    extra = None
    if found is not None and found.origin is not None:
        extra = str(Path(found.origin).parent.parent)
    ctx = _Context(
        spec=spec,
        trees=roots,
        env={**env, "HYPOTHESIS_STORAGE_DIRECTORY": str(work / "hypothesis")},
        work=replay,
        hook=work / "capture" / "hook",
        extra_path=extra,
        preload=preload,
    )
    checks: list[DifferentialCheck] = []
    for target in sorted(targets, key=lambda t: t.function):
        if examples:
            target.generated = _generate_for(ctx, target, examples, notes)
        checks.append(_check_one(ctx, target, notes))
    return DifferentialResult(
        tuple(checks), (*changes.not_checked, *not_checked), tuple(notes)
    )


if __name__ == "__main__":
    _status = _main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    # The results are flushed and closed. Skip interpreter teardown,
    # where atexit handlers and finalizers of the code under test (a
    # multiprocessing pool joining its workers) could hold the process.
    os._exit(_status)
