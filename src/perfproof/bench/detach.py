"""Run a compare detached, and read, cancel or resume it.

``docs/cli.md``, "Long runs": a detached compare is one process in its
own session, and its run directory is the only state. There is no
daemon, queue or job service, and the measuring process starts no
thread.

Files in ``.perfproof/runs/<id>/``, besides the journal files of
`perfproof.bench.journal`:

=================  =====================================================
``job.json``       What to run: spec, trees, order seed, timeout, and
                   when each resume happened
``pid``            Process ID and start time of the run's process
``cancelled``      Written by ``cancel`` before it signals anything
``timed_out``      Written by the run when the hard timeout fires
``failed.json``    The code and message of a stop with no verdict
``detached.log``   The process's stdout and stderr
``run.lock``       Held while a resume decides and spawns
=================  =====================================================

State
-----
`run_status` reads the files and checks the process:

``finished``
    ``record.json`` exists. Nothing else decides this.
``cancelled``, ``timed_out``
    The marker exists and there is no record.
``failed``
    The compare stopped with no verdict (`CompareStoppedError`, or a
    fault in perfproof); ``failed.json`` says why. Not in the list of
    ``docs/cli.md``, which has no place for a stop that no resume can
    help.
``running``
    The process in ``pid`` exists, is not a zombie and has the start
    time written in ``pid``.
``interrupted``
    None of the above: the process is gone and left no record, as after
    ``kill -9`` or a reboot. Only this state can be resumed.
``unknown``
    The process table could not be read, so whether the process lives
    is not known. Never resumable; a failed read is not "gone".

A reused PID is never taken for the run: a process counts only when its
start time equals the one in ``pid`` (clock ticks since boot on Linux,
``ps`` ``lstart``, to the second, on macOS), and nothing is ever
signalled that has not just been matched that way.

Stopping
--------
``cancel`` and the hard timeout both end in the same place: the run's
process and every descendant are gone. The run's own process is asked
first (SIGTERM, which makes it unwind and kill the benchmark's process
group as the runner does on any error), then everything still alive is
killed with SIGKILL, found by parent and by process group, since
benchmark children run in groups of their own. A descendant that has
left both (``setsid`` and an exited parent) is beyond reach; the
runner's own module docstring states the same limit.

The timeout is a SIGALRM handler in the run's process, not a thread or
a watchdog process.
"""

import contextlib
import fcntl
import hashlib
import math
import os
import signal
import subprocess
import sys
import time
import traceback
import warnings
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from perfproof.bench.compare import CompareStoppedError, compare
from perfproof.bench.journal import (
    JOURNAL_NAME,
    MACHINE_NAME,
    PROGRESS_NAME,
    Journal,
    JournalError,
    Resumed,
    load_journal,
    read_json,
    write_json,
)
from perfproof.bench.spec import STATE_DIR, load_spec
from perfproof.machine import fingerprint
from perfproof.report import RECORD_NAME
from perfproof.schema import MachineFingerprint

__all__ = [
    "CANCELLED_NAME",
    "DEFAULT_GRACE_SECONDS",
    "FAILED_NAME",
    "JOB_NAME",
    "LOG_NAME",
    "PID_NAME",
    "TIMED_OUT_NAME",
    "DetachError",
    "Identity",
    "Job",
    "RunStatus",
    "Tree",
    "cancel_run",
    "default_timeout",
    "find_run",
    "kill_tree",
    "machine_differences",
    "process_table",
    "resume_run",
    "run_detached",
    "run_status",
    "snapshot_tree",
    "start_run",
]

JOB_NAME: Final = "job.json"
PID_NAME: Final = "pid"
CANCELLED_NAME: Final = "cancelled"
TIMED_OUT_NAME: Final = "timed_out"
FAILED_NAME: Final = "failed.json"
LOG_NAME: Final = "detached.log"
LOCK_NAME: Final = "run.lock"
TREES_NAME: Final = "trees.json"
_SKIP_DIRS: Final = frozenset(
    {
        ".git",
        ".perfproof",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
    }
)
DEFAULT_GRACE_SECONDS: Final = 10.0
"""Time ``cancel`` and the timeout give the run's process to unwind
before SIGKILL, in seconds."""

IDENTITY_FIELDS: Final = (
    "host_id",
    "os",
    "os_version",
    "kernel",
    "arch",
    "cpu_model",
    "physical_cores",
    "logical_cores",
    "memory_bytes",
    "python_version",
)
"""The probes of a `MachineFingerprint` that say which machine it is.
The others (load, power, thermal state, frequency policy, time) change
from minute to minute on one machine; the warnings built from them
describe each attempt, and a resume does not refuse on them."""

_TIMEOUT_FLOOR_SECONDS: Final = 3600.0
_STATE_NAMES: Final = (
    "running",
    "finished",
    "interrupted",
    "cancelled",
    "timed_out",
    "failed",
)
_PS_TIMEOUT: Final = 10.0
_POLL_SECONDS: Final = 0.05
_SWEEPS: Final = 40
_EXIT_CANCELLED: Final = 143
_EXIT_TIMED_OUT: Final = 124
_EXIT_STOPPED: Final = 1
_EXIT_FAULT: Final = 3


class DetachError(Exception):
    """A detach, status, cancel or resume request was refused.

    Parameters
    ----------
    code : str
        Stable snake_case identifier: ``not_found``, ``not_resumable``,
        ``machine_changed``, ``invalid_journal``, ``invalid_job``,
        ``already_finished``, ``spawn_failed``, ``cancel_incomplete``
        or ``usage``.
    message : str
        What happened and what to do.
    details : Mapping of str to object, optional
        Structured context.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Mapping[str, object] = {} if details is None else details


@dataclass(frozen=True, slots=True)
class Job:
    """What a detached compare runs, from ``job.json``.

    Attributes
    ----------
    repo : str
        Root of the repository.
    name, spec_hash : str
        The frozen spec to load.
    baseline_root, candidate_root : str
        The two trees.
    baseline_commit : str
        Full SHA of the baseline.
    order_seed : int
        Seed of the in-round order.
    timeout_seconds : float
        Hard total timeout of one process of this run, in seconds.
    resumes : tuple of str
        When each resume started, as ISO times.
    deadline : float
        Unix time at which the hard timeout ends the whole run, every
        attempt included; set when the run starts, 0 until then.
    """

    repo: str
    name: str
    spec_hash: str
    baseline_root: str
    candidate_root: str
    baseline_commit: str
    order_seed: int
    timeout_seconds: float
    resumes: tuple[str, ...] = ()
    deadline: float = 0.0

    @property
    def attempt(self) -> int:
        """Number of resumes so far; 0 for the first process."""
        return len(self.resumes)

    def to_json(self) -> dict[str, object]:
        """Return the file's content."""
        return {
            "repo": self.repo,
            "name": self.name,
            "spec_hash": self.spec_hash,
            "baseline_root": self.baseline_root,
            "candidate_root": self.candidate_root,
            "baseline_commit": self.baseline_commit,
            "order_seed": self.order_seed,
            "timeout_seconds": self.timeout_seconds,
            "resumes": list(self.resumes),
            "deadline": self.deadline,
        }


@dataclass(frozen=True, slots=True)
class Identity:
    """A process as ``pid`` records it: its ID and its start time."""

    pid: int
    start: str


@dataclass(frozen=True, slots=True)
class Proc:
    """One row of the process table."""

    pid: int
    ppid: int
    pgid: int
    start: str
    zombie: bool


@dataclass(frozen=True, slots=True)
class RunStatus:
    """The state of one run, as `run_status` reads it.

    Attributes
    ----------
    state : str
        ``running``, ``finished``, ``interrupted``, ``cancelled``,
        ``timed_out`` or ``failed``.
    rounds_done : int
        Completed rounds in ``samples.jsonl``.
    max_rounds : int or None
        The budget, when ``progress.json`` was written.
    looks : int or None
        Looks taken, when ``progress.json`` was written.
    phase : str or None
        ``gates`` or ``rounds``, when ``progress.json`` was written.
    error : tuple of (str, str) or None
        Code and message of a ``failed`` run.
    """

    state: str
    rounds_done: int
    max_rounds: int | None
    looks: int | None
    phase: str | None
    error: tuple[str, str] | None = None


def default_timeout(max_seconds: float) -> float:
    """Return the hard total timeout when none is given.

    Twice the spec's round-time budget plus an hour, which covers the
    gates, the correctness runs and the warmup that the budget does
    not count. A chosen allowance, not a measured one; ``--timeout``
    replaces it.

    Parameters
    ----------
    max_seconds : float
        `perfproof.schema.Budget.max_seconds`.

    Returns
    -------
    float
        Seconds.
    """
    return 2.0 * max_seconds + _TIMEOUT_FLOOR_SECONDS


def find_run(repo: Path, run_id: str) -> Path:
    """Return the run directory named by ``run_id``.

    Parameters
    ----------
    repo : pathlib.Path
        Root of the repository.
    run_id : str
        A full run id (``<UTC time>-<hex>``), or a unique prefix of
        its hex part, or a unique prefix of the whole id.

    Returns
    -------
    pathlib.Path
        The run directory.

    Raises
    ------
    DetachError
        ``not_found`` if nothing or more than one run matches.
    """
    runs = repo / STATE_DIR / "runs"
    exact = runs / run_id
    if run_id and "/" not in run_id and exact.is_dir():
        return exact
    try:
        names = sorted(p.name for p in runs.iterdir() if p.is_dir())
    except OSError:
        names = []
    hits = [
        n
        for n in names
        if run_id
        and (n.startswith(run_id) or n.split("-", 1)[-1].startswith(run_id))
    ]
    if len(hits) != 1:
        what = "no run" if not hits else f"{len(hits)} runs"
        msg = f"{what} in {runs} match {run_id!r}"
        raise DetachError("not_found", msg, {"run": run_id, "matches": hits})
    return runs / hits[0]


# Processes


def process_table(only: int | None = None) -> dict[int, Proc]:
    """Read the process table.

    Parameters
    ----------
    only : int, optional
        Read just this process.

    Returns
    -------
    dict of int to Proc
        Keyed by PID. A process that vanishes while the table is read
        is left out. On Linux each row comes from ``/proc/<pid>/stat``
        and the start time is its ``starttime`` field; elsewhere from
        ``ps``, with ``lstart`` as the start time. O(processes).

    Raises
    ------
    OSError
        If ``ps`` can't run or fails where it is needed.
    """
    if Path("/proc/self/stat").exists():
        pids = (
            [only]
            if only is not None
            else [int(n) for n in os.listdir("/proc") if n.isdigit()]
        )
        rows = (_linux_row(pid) for pid in pids)
        return {p.pid: p for p in rows if p is not None}
    return _ps_table(only)


def _linux_row(pid: int) -> Proc | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(errors="replace")
    except OSError:
        return None
    rest = text[text.rindex(")") + 2 :].split()
    try:
        return Proc(
            pid=pid,
            ppid=int(rest[1]),
            pgid=int(rest[2]),
            start=rest[19],
            zombie=rest[0] in ("Z", "X"),
        )
    except (IndexError, ValueError):
        return None


def _ps_table(only: int | None) -> dict[int, Proc]:
    argv = ["ps", "-o", "pid=,ppid=,pgid=,stat=,lstart="]
    argv += ["-p", str(only)] if only is not None else ["-A"]
    env = {**os.environ, "LC_ALL": "C"}
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_PS_TIMEOUT,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"ps timed out after {_PS_TIMEOUT:g} s"
        raise OSError(msg) from exc
    table: dict[int, Proc] = {}
    for line in done.stdout.splitlines():
        words = line.split()
        if len(words) < 9:
            continue
        try:
            pid, ppid, pgid = int(words[0]), int(words[1]), int(words[2])
        except ValueError:
            continue
        table[pid] = Proc(
            pid=pid,
            ppid=ppid,
            pgid=pgid,
            start=" ".join(words[4:]),
            zombie="Z" in words[3],
        )
    if not table and done.returncode not in (0, 1):
        msg = f"ps failed with status {done.returncode}"
        raise OSError(msg)
    return table


def _alive(identity: Identity, table: Mapping[int, Proc]) -> bool:
    row = table.get(identity.pid)
    return row is not None and row.start == identity.start and not row.zombie


def _probe(identity: Identity) -> bool | None:
    """Tell if ``identity`` is alive; ``None`` if that can't be read.

    A failed read of the process table is not an answer: reading it as
    "gone" would let a live run be resumed a second time.
    """
    try:
        table = process_table(identity.pid)
    except OSError:
        return None
    return _alive(identity, table)


def _sid(pid: int) -> int | None:
    """Return the session of ``pid``, or ``None`` if it has gone."""
    try:
        return os.getsid(pid)
    except OSError:
        return None


@dataclass(slots=True)
class Tree:
    """The processes found under a run's process, by start time.

    Attributes
    ----------
    starts : dict of int to str
        PID to start time, for every member found.
    leaders : dict of int to str
        The members that led a process group of their own when found,
        PID to start time. The group outlives its leader, and a PID is
        not given out while a group still uses it, so members left in
        the group of a dead leader are still found.
    """

    starts: dict[int, str]
    leaders: dict[int, str]

    def __init__(self) -> None:
        self.starts = {}
        self.leaders = {}


def _grow(
    table: Mapping[int, Proc],
    tree: Tree,
    roots: Sequence[int] = (),
    session: int | None = None,
) -> None:
    """Add to ``tree`` every process that belongs under it now.

    A process joins when its parent is a member or it is in the process
    group of a member that led one. Members and ``roots`` must be
    processes that still match their recorded start time; a PID that
    now belongs to another process is not one. The caller's own process
    group and the caller never join. Repeated to a fixed point.

    ``session`` is the run's session, led by its process: everything
    still in it joins, which finds a benchmark child left behind when
    the run's process was killed. The caller has checked that the PID
    is the run's, or free.
    """
    members = {
        pid
        for pid, start in tree.starts.items()
        if pid in table and table[pid].start == start
    }
    members.update(r for r in roots if r in table)
    own = os.getpgrp()
    if session is not None:
        members.update(
            pid for pid in table if pid != os.getpid() and _sid(pid) == session
        )
    grew = True
    while grew:
        grew = False
        groups = {
            g
            for g, start in tree.leaders.items()
            if g not in table or table[g].start == start
        }
        groups.update(g for g in members if table[g].pgid == g)
        groups.discard(own)
        for row in table.values():
            if row.pid in members or row.pid == os.getpid():
                continue
            if row.ppid in members or row.pgid in groups:
                members.add(row.pid)
                grew = True
    for pid in members:
        row = table[pid]
        tree.starts.setdefault(pid, row.start)
        if row.pgid == pid and pid != own:
            tree.leaders.setdefault(pid, row.start)


def _session_of(root: Identity, table: Mapping[int, Proc]) -> int | None:
    """Return the session to sweep for ``root``, if it can be trusted.

    The PID must be free or still be the process recorded, and the
    session must not be the caller's own.
    """
    row = table.get(root.pid)
    if row is not None and row.start != root.start:
        return None
    if root.pid == _sid(os.getpid()) and root.pid != os.getpid():
        return None
    return root.pid


def snapshot_tree(root_pid: int, tree: Tree | None = None) -> Tree:
    """Find a process and its descendants now.

    A descendant that is orphaned later can't be found from its parent
    any more, so whoever is about to unwind a tree takes this first and
    hands it to `kill_tree`.

    Parameters
    ----------
    root_pid : int
        The tree's root, which the caller knows to be the right
        process (its own, or one just matched by start time).
    tree : Tree, optional
        A snapshot to add to; a new one by default.

    Returns
    -------
    Tree
        The snapshot, the root included when it exists.

    Raises
    ------
    OSError
        If the process table can't be read.
    """
    tree = Tree() if tree is None else tree
    _grow(process_table(), tree, (root_pid,), root_pid)
    return tree


def _live(
    table: Mapping[int, Proc], tree: Tree, skip: int | None
) -> list[int]:
    """List the members that are still the processes they were."""
    return [
        pid
        for pid, start in tree.starts.items()
        if pid != skip
        and pid != os.getpid()
        and pid in table
        and table[pid].start == start
        and not table[pid].zombie
    ]


def kill_tree(
    root: Identity,
    *,
    grace_seconds: float = DEFAULT_GRACE_SECONDS,
    kill_root: bool = True,
    tree: Tree | None = None,
) -> list[int]:
    """End a run's process and every descendant.

    With ``kill_root`` the process is first sent SIGTERM, as a group,
    and given ``grace_seconds`` to unwind. Then everything found, by
    parent and by process group, is sent SIGKILL, and the table is read
    again until no member is left or `_SWEEPS` rounds have passed. A
    process is signalled only if its start time is the one seen when it
    was found, so a reused PID is never hit; the root is looked for
    only if it matches ``root``.

    Parameters
    ----------
    root : Identity
        The run's process, as recorded. If it is not alive (or its PID
        is now another process) the descendants it left are still
        looked for through ``tree``.
    grace_seconds : float, default `DEFAULT_GRACE_SECONDS`
        Time for SIGTERM to work; ignored unless ``kill_root``.
    kill_root : bool, default True
        False when the caller is the root: its descendants are killed
        and it is left to exit.
    tree : Tree, optional
        Members already found by `snapshot_tree`.

    Returns
    -------
    list of int
        PIDs still alive afterwards; empty on success.

    Raises
    ------
    OSError
        If the process table can't be read.
    """
    tree = Tree() if tree is None else tree
    table = process_table()
    roots = [root.pid] if _alive(root, table) else []
    _grow(table, tree, roots, _session_of(root, table))
    if kill_root and roots:
        with contextlib.suppress(ProcessLookupError):
            if table[root.pid].pgid == root.pid:
                os.killpg(root.pid, signal.SIGTERM)
            else:
                os.kill(root.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline and _probe(root) is not False:
            time.sleep(_POLL_SECONDS)
    skip = None if kill_root else root.pid
    for _ in range(_SWEEPS):
        table = process_table()
        alive = [root.pid] if _alive(root, table) else []
        _grow(table, tree, alive, _session_of(root, table))
        live = _live(table, tree, skip)
        if not live:
            return []
        for pid in live:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        time.sleep(_POLL_SECONDS)
    return _live(process_table(), tree, skip)


# Files


def _read_identity(run_dir: Path) -> Identity | None:
    doc = read_json(run_dir / PID_NAME)
    if doc is None:
        return None
    pid, start = doc.get("pid"), doc.get("start")
    if type(pid) is not int or not isinstance(start, str):
        return None
    return Identity(pid, start)


def _read_job(run_dir: Path) -> Job:
    doc = read_json(run_dir / JOB_NAME)
    if doc is None:
        msg = f"{run_dir / JOB_NAME} is missing or is not a job"
        raise DetachError("invalid_job", msg, {"run_dir": str(run_dir)})
    try:
        return Job(
            repo=cast("str", doc["repo"]),
            name=cast("str", doc["name"]),
            spec_hash=cast("str", doc["spec_hash"]),
            baseline_root=cast("str", doc["baseline_root"]),
            candidate_root=cast("str", doc["candidate_root"]),
            baseline_commit=cast("str", doc["baseline_commit"]),
            order_seed=cast("int", doc["order_seed"]),
            timeout_seconds=float(cast("float", doc["timeout_seconds"])),
            resumes=tuple(cast("list[str]", doc.get("resumes", []))),
            deadline=float(cast("float", doc.get("deadline", 0.0))),
        )
    except (KeyError, TypeError, ValueError) as exc:
        msg = f"{run_dir / JOB_NAME} is not a valid job: {exc!r}"
        raise DetachError("invalid_job", msg) from exc


def _journal_rounds(run_dir: Path) -> int:
    """Count the whole lines of the journal without parsing them."""
    try:
        return (run_dir / JOURNAL_NAME).read_bytes().count(b"\n")
    except OSError:
        return 0


def run_status(run_dir: Path) -> RunStatus:
    """Read the state of a run from its directory and its process.

    Parameters
    ----------
    run_dir : pathlib.Path
        The run directory.

    Returns
    -------
    RunStatus
        See the module docstring for the states and their order of
        precedence: finished, cancelled, timed out, failed, running,
        interrupted.
    """
    progress = read_json(run_dir / PROGRESS_NAME) or {}

    def number(key: str) -> int | None:
        value = progress.get(key)
        return value if type(value) is int else None

    phase = progress.get("phase")
    error: tuple[str, str] | None = None
    if (run_dir / RECORD_NAME).exists():
        state = "finished"
    elif (run_dir / CANCELLED_NAME).exists():
        state = "cancelled"
    elif (run_dir / TIMED_OUT_NAME).exists():
        state = "timed_out"
    elif (failed := read_json(run_dir / FAILED_NAME)) is not None:
        state = "failed"
        error = (str(failed.get("code")), str(failed.get("message")))
    else:
        identity = _read_identity(run_dir)
        seen = None if identity is None else _probe(identity)
        if identity is not None and seen is None:
            state = "unknown"
        else:
            state = "running" if seen else "interrupted"
    return RunStatus(
        state=state,
        rounds_done=_journal_rounds(run_dir),
        max_rounds=number("max_rounds"),
        looks=number("looks"),
        phase=phase if isinstance(phase, str) else None,
        error=error,
    )


def machine_differences(
    first: MachineFingerprint, now: MachineFingerprint
) -> list[str]:
    """List the identity probes that differ between two fingerprints.

    A probe known on one side and unknown on the other counts as
    different: the machine can't be shown to be the same.

    Parameters
    ----------
    first, now : perfproof.schema.MachineFingerprint
        The fingerprint taken at the start of the run and the one
        taken now.

    Returns
    -------
    list of str
        Field names from `IDENTITY_FIELDS`; empty if the machine is the
        same as far as they say.
    """
    return [f for f in IDENTITY_FIELDS if getattr(first, f) != getattr(now, f)]


# Start, cancel and resume


def start_run(run_dir: Path, job: Job) -> Identity:
    """Create the run directory and start its process.

    Parameters
    ----------
    run_dir : pathlib.Path
        ``.perfproof/runs/<id>/``; it must not exist.
    job : Job
        What to run.

    Returns
    -------
    Identity
        The process, as written to ``pid``.

    Raises
    ------
    DetachError
        ``spawn_failed`` if the process could not be started or
        identified.
    FileExistsError
        If ``run_dir`` exists.
    OSError
        If a file can't be written.
    """
    run_dir.mkdir(parents=True)
    job = replace(job, deadline=time.time() + job.timeout_seconds)
    write_json(run_dir / JOB_NAME, job.to_json())
    return _spawn(run_dir, job)


def _spawn(run_dir: Path, job: Job) -> Identity:
    argv = [sys.executable, "-m", "perfproof.bench.detach", str(run_dir)]
    with (run_dir / LOG_NAME).open("ab") as log:
        proc = subprocess.Popen(
            argv,
            cwd=job.repo,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    pid = proc.pid
    # The child is on its own; this process never waits for it, and a
    # reaped-late zombie is handled by `run_status`.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        del proc
    try:
        row = process_table(pid).get(pid)
    except OSError as exc:
        row = None
        reason = str(exc)
    else:
        reason = "it exited before it could be identified"
    if row is None or row.zombie:
        tail = _log_tail(run_dir)
        msg = f"the detached process did not start: {reason}. {tail}"
        raise DetachError(
            "spawn_failed", msg.strip(), {"run_dir": str(run_dir)}
        )
    identity = Identity(pid, row.start)
    write_json(
        run_dir / PID_NAME, {"pid": identity.pid, "start": identity.start}
    )
    return identity


def _log_tail(run_dir: Path, size: int = 600) -> str:
    try:
        data = (run_dir / LOG_NAME).read_bytes()[-size:]
    except OSError:
        return ""
    return data.decode(errors="replace")


def cancel_run(
    run_dir: Path, *, grace_seconds: float = DEFAULT_GRACE_SECONDS
) -> RunStatus:
    """Cancel a run: no verdict, no process left.

    For a run that is running, interrupted or of unknown state the
    ``cancelled`` marker is written first, so the state reads
    ``cancelled`` from then on; then the process tree is ended with
    `kill_tree`.

    Parameters
    ----------
    run_dir : pathlib.Path
        The run directory.
    grace_seconds : float, default `DEFAULT_GRACE_SECONDS`
        Time for SIGTERM before SIGKILL.

    Returns
    -------
    RunStatus
        The state afterwards: ``cancelled``, or the state the run was in
        if it had already ``timed_out``, ``failed`` or been
        ``cancelled``, which cancel keeps. Whatever the state, every
        process of the run is ended, including a benchmark child that a
        ``kill -9`` of the run's process left behind (found by the
        run's session).

    Raises
    ------
    DetachError
        ``already_finished`` if a record exists, ``cancel_incomplete``
        if a process of the run survived SIGKILL.
    OSError
        If the marker can't be written or the process table read.
    """
    before = run_status(run_dir)
    if before.state == "finished":
        msg = f"run {run_dir.name} has finished; there is nothing to cancel"
        raise DetachError("already_finished", msg)
    if before.state in ("running", "interrupted", "unknown"):
        # Fails before any marker if the table can't be read.
        process_table()
        write_json(
            run_dir / CANCELLED_NAME,
            {"at": datetime.now(UTC).isoformat(), "was": before.state},
        )
    identity = _read_identity(run_dir)
    if identity is not None:
        left = kill_tree(identity, grace_seconds=grace_seconds)
        if left:
            msg = f"processes {left} of run {run_dir.name} survived SIGKILL"
            raise DetachError("cancel_incomplete", msg, {"pids": left})
    return run_status(run_dir)


def resume_run(run_dir: Path) -> Identity:
    """Continue an interrupted run in a new detached process.

    Everything is checked before anything starts: the run is
    ``interrupted``, its journal is whole, its trees hold the files they
    held when it began and the machine is the one it began on. Any
    process of the run left by a ``kill -9`` is ended first. The new
    process then checks every gate again
    (`perfproof.bench.compare.compare`) and continues from the journal.
    Two resumes at once are serialized by a lock; the second finds the
    first running and is refused.

    Parameters
    ----------
    run_dir : pathlib.Path
        The run directory.

    Returns
    -------
    Identity
        The new process, as written to ``pid``.

    Raises
    ------
    DetachError
        ``not_resumable`` (the state is not ``interrupted``, or the
        files a resume needs are missing, the code of either tree or a
        locked file changed since the run started, or its hard timeout
        has passed), ``machine_changed`` (details list the probes that
        differ), ``invalid_journal``, ``orphan_running`` (a benchmark
        child left by a ``kill -9`` could not be ended),
        ``state_unknown`` or ``spawn_failed``.
    OSError
        If a file can't be written.
    """
    lock = os.open(run_dir / LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        job = check_resumable(run_dir)
        identity = _read_identity(run_dir)
        if identity is not None:
            try:
                left = kill_tree(identity, kill_root=False)
            except OSError as exc:
                msg = f"the process table can't be read: {exc}"
                raise DetachError("state_unknown", msg) from exc
            if left:
                msg = (
                    f"processes {left} of run {run_dir.name} are still "
                    "running and could not be ended; not resuming beside them"
                )
                raise DetachError("orphan_running", msg, {"pids": left})
        # Cut a torn last line now, while no other process can append.
        load_journal(run_dir, repair=True)
        resumes = (*job.resumes, datetime.now(UTC).isoformat())
        job = replace(job, resumes=resumes)
        write_json(run_dir / JOB_NAME, job.to_json())
        return _spawn(run_dir, job)
    finally:
        os.close(lock)


def check_resumable(run_dir: Path) -> Job:
    """Check that ``run_dir`` can be resumed here and now.

    Parameters
    ----------
    run_dir : pathlib.Path
        The run directory.

    Returns
    -------
    Job
        The run's job.

    Raises
    ------
    DetachError
        As `resume_run`.
    """
    state = run_status(run_dir).state
    if state != "interrupted":
        msg = (
            f"run {run_dir.name} is {state}; only an interrupted run "
            "can be resumed"
        )
        raise DetachError("not_resumable", msg, {"state": state})
    job = _read_job(run_dir)
    if job.deadline and time.time() >= job.deadline:
        msg = (
            f"run {run_dir.name} has used its hard timeout "
            f"({job.timeout_seconds:g} s); start a new compare"
        )
        raise DetachError("not_resumable", msg)
    saved = _saved_machine(run_dir)
    for label, tree in (
        ("baseline", job.baseline_root),
        ("candidate", job.candidate_root),
    ):
        if not Path(tree).is_dir():
            msg = f"the {label} tree {tree} no longer exists"
            raise DetachError("not_resumable", msg, {"tree": tree})
    differing = machine_differences(saved, fingerprint())
    if differing:
        msg = (
            f"this is not the machine run {run_dir.name} began on "
            f"({', '.join(differing)} differ); rounds from two machines "
            "are never mixed, so start a new compare"
        )
        raise DetachError("machine_changed", msg, {"differs": differing})
    try:
        load_journal(run_dir)
    except JournalError as exc:
        raise DetachError("invalid_journal", str(exc)) from exc
    _check_trees(run_dir, job)
    return job


def tree_manifest(root: Path, exclude: Collection[str]) -> dict[str, str]:
    """Hash every file of a tree.

    Parameters
    ----------
    root : pathlib.Path
        Root of the tree.
    exclude : collection of str
        Paths relative to ``root`` (POSIX form) left out, such as the
        outputs a run rewrites.

    Returns
    -------
    dict of str to str
        Relative path to SHA-256 of the content (a symlink's target
        text, ``unreadable`` for a file that can't be read). Version
        control and cache directories are skipped. O(bytes in the tree).
    """
    out: dict[str, str] = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
        for name in sorted(files):
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if rel in exclude:
                continue
            try:
                if full.is_symlink():
                    out[rel] = f"link:{os.readlink(full)}"
                else:
                    with full.open("rb") as f:
                        out[rel] = hashlib.file_digest(f, "sha256").hexdigest()
            except OSError:
                out[rel] = "unreadable"
    return out


def _manifests(job: Job) -> dict[str, dict[str, str]]:
    spec, _ = load_spec(Path(job.repo), job.name, job.spec_hash)
    exclude = {o.path for o in spec.correctness.outputs}
    return {
        "baseline": tree_manifest(Path(job.baseline_root), exclude),
        "candidate": tree_manifest(Path(job.candidate_root), exclude),
    }


def _check_trees(run_dir: Path, job: Job) -> None:
    """Refuse a resume if a file held at the start has changed.

    Rounds measured before and after an edit would be two candidates in
    one verdict, and a changed locked file would turn an interrupted
    run into a REJECT. A file added since is a change too, as a module
    the benchmark could import; declared outputs and cache directories
    are not hashed on either side.
    """
    saved = read_json(run_dir / TREES_NAME)
    if saved is None:
        msg = (
            f"run {run_dir.name} has no {TREES_NAME}, so the code it "
            "measured can't be shown unchanged; start a new compare"
        )
        raise DetachError("not_resumable", msg)
    try:
        now = _manifests(job)
    except (OSError, ValueError, KeyError) as exc:
        msg = f"the trees could not be read: {exc}"
        raise DetachError("not_resumable", msg) from exc
    changed: list[str] = []
    for arm, before in saved.items():
        files = cast("dict[str, str]", before)
        current = now.get(arm, {})
        for rel, digest in files.items():
            if current.get(rel) != digest:
                changed.append(f"{arm}:{rel}")
        changed.extend(
            f"{arm}:+{rel}" for rel in sorted(current.keys() - files.keys())
        )
    if changed:
        listed = ", ".join(changed[:5]) + (", ..." if len(changed) > 5 else "")
        msg = (
            "the code changed since this run started; start a new "
            f"compare ({listed})"
        )
        raise DetachError("not_resumable", msg, {"changed": changed[:20]})


def _saved_machine(run_dir: Path) -> MachineFingerprint:
    return _read_machine(run_dir)[0]


def _read_machine(run_dir: Path) -> tuple[MachineFingerprint, datetime]:
    doc = read_json(run_dir / MACHINE_NAME)
    try:
        if doc is None:
            raise KeyError(MACHINE_NAME)
        machine = MachineFingerprint.model_validate(doc["machine"])
        started = datetime.fromisoformat(cast("str", doc["started_at"]))
    except (KeyError, ValueError, TypeError) as exc:
        msg = (
            f"run {run_dir.name} has no usable {MACHINE_NAME} ({exc!r}); "
            "without its first fingerprint it can't be resumed"
        )
        raise DetachError("not_resumable", msg) from exc
    return machine, started


# The detached process


class _CancelledError(BaseException):
    """SIGTERM arrived: unwind, killing the benchmark's group."""


class _TimedOutError(BaseException):
    """The hard total timeout fired."""


def run_detached(run_dir: Path) -> int:
    """Run the compare of ``run_dir`` here: the detached main.

    Installs the SIGTERM and timeout handlers, then runs
    `perfproof.bench.compare.compare` with the journal. Whatever ends
    it, the descendants of this process are killed before it exits.

    Parameters
    ----------
    run_dir : pathlib.Path
        The run directory written by `start_run` or `resume_run`.

    Returns
    -------
    int
        Exit status: 0 with a record, 1 for a stop with no verdict,
        124 on timeout, 143 on cancel, 3 on a fault in perfproof.
    """
    me = _self()
    status = _EXIT_FAULT
    seen = Tree()
    try:
        job = _read_job(run_dir)
        remaining = (
            job.deadline - time.time() if job.deadline else job.timeout_seconds
        )
        if remaining <= 0:
            _mark_timed_out(run_dir, job.timeout_seconds)
            return _EXIT_TIMED_OUT
        _install_handlers(run_dir, job.timeout_seconds, seen, remaining)
        status = _run(run_dir, job)
    except _CancelledError:
        status = _EXIT_CANCELLED
    except _TimedOutError:
        status = _EXIT_TIMED_OUT
    except DetachError as exc:
        _mark_failed(run_dir, exc.code, exc.message)
        status = _EXIT_STOPPED
    except CompareStoppedError as exc:
        _mark_failed(run_dir, exc.code, exc.message)
        status = _EXIT_STOPPED
    except Exception as exc:
        traceback.print_exc()
        _mark_failed(run_dir, "internal_fault", f"{type(exc).__name__}: {exc}")
        status = _EXIT_FAULT
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        with contextlib.suppress(OSError):
            kill_tree(me, kill_root=False, tree=seen)
    return status


def _self() -> Identity:
    """Return this process as `Identity`, as the table knows it."""
    pid = os.getpid()
    try:
        row = process_table(pid).get(pid)
    except OSError:
        row = None
    return Identity(pid, "" if row is None else row.start)


def _run(run_dir: Path, job: Job) -> int:
    spec, lock = load_spec(Path(job.repo), job.name, job.spec_hash)
    resumed: Resumed | None = None
    if job.attempt == 0:
        write_json(run_dir / TREES_NAME, _manifests(job))
    if job.attempt > 0:
        saved, started = _read_machine(run_dir)
        differing = machine_differences(saved, fingerprint())
        if differing:
            msg = f"the machine changed ({', '.join(differing)})"
            raise DetachError("machine_changed", msg)
        resumed = Resumed(
            rounds=tuple(load_journal(run_dir, repair=True)),
            machine=saved,
            started=started,
            attempt=job.attempt,
        )
    journal = Journal(
        run_dir,
        spec.budget.max_rounds,
        resume_after=0 if resumed is None else len(resumed.rounds),
    )
    try:
        compare(
            spec,
            lock,
            baseline_root=Path(job.baseline_root),
            baseline_commit=job.baseline_commit,
            candidate_root=Path(job.candidate_root),
            run_dir=run_dir,
            process_env=os.environ,
            order_seed=job.order_seed,
            journal=journal,
            resume=resumed,
        )
    finally:
        journal.close()
    return 0


def _install_handlers(
    run_dir: Path, timeout_seconds: float, seen: Tree, remaining: float
) -> None:
    """Install the SIGTERM and timeout handlers.

    Each notes the tree as it is before the stack unwinds and the
    runner kills the benchmark's group, so a grandchild that is
    orphaned by that is still known to the final sweep.
    """
    fired: list[bool] = []

    def note() -> None:
        with contextlib.suppress(OSError):
            snapshot_tree(os.getpid(), seen)

    def on_term(signum: int, frame: object) -> None:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        note()
        raise _CancelledError

    def on_alarm(signum: int, frame: object) -> None:
        note()
        if fired:
            # Unwinding did not finish: end it all now.
            _mark_timed_out(run_dir, timeout_seconds)
            with contextlib.suppress(OSError):
                kill_tree(_self(), kill_root=False, tree=seen)
            os._exit(_EXIT_TIMED_OUT)
        fired.append(True)
        _mark_timed_out(run_dir, timeout_seconds)
        signal.alarm(math.ceil(DEFAULT_GRACE_SECONDS))
        raise _TimedOutError

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGALRM, on_alarm)
    signal.alarm(max(1, math.ceil(remaining)))


def _mark_timed_out(run_dir: Path, timeout_seconds: float) -> None:
    doc: dict[str, object] = {
        "at": datetime.now(UTC).isoformat(),
        "timeout_seconds": timeout_seconds,
    }
    with contextlib.suppress(OSError):
        write_json(run_dir / TIMED_OUT_NAME, doc)


def _mark_failed(run_dir: Path, code: str, message: str) -> None:
    with contextlib.suppress(OSError):
        write_json(run_dir / FAILED_NAME, {"code": code, "message": message})


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of ``python -m perfproof.bench.detach RUN_DIR``.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments; ``sys.argv[1:]`` by default. One: the run directory.

    Returns
    -------
    int
        See `run_detached`; 2 for a wrong argument list.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        sys.stderr.write("usage: python -m perfproof.bench.detach RUN_DIR\n")
        return 2
    os.umask(0o022)
    return run_detached(Path(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
