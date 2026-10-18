"""Interleaved pairs of workload executions, resumable from a log.

Each execution is the workload's argv run without a shell, its output
discarded, timed with ``time.perf_counter_ns`` (the clock pyperf
uses). On Linux, where the probe says perf counts, it runs under
``perf stat`` for its user-space instruction count. Pairs are appended
to a JSON-lines log as they finish, so a run that stops (a VM shut
down, a lost SSH session) continues from the last finished pair with
the same seed and therefore the same order.
"""

from __future__ import annotations

import json
import random
import resource
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import machine
import trees

WARMUP = 2
EXEC_TIMEOUT_S = 1800.0


@dataclass(frozen=True)
class Execution:
    """One workload execution.

    Attributes
    ----------
    wall_s : float
        Wall time, seconds.
    cpu_s : float
        User plus system CPU time of the waited-for children, seconds
        (for a Docker workload, only the docker CLI's).
    exit_code : int
        Exit status; 124 on timeout, 127 when it could not start.
    instructions : int or None
        User-space instructions from ``perf stat``, or None.
    """

    wall_s: float
    cpu_s: float
    exit_code: int
    instructions: int | None


def _children_cpu() -> float:
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


def execute(
    argv: list[str],
    cwd: Path,
    env: dict[str, str],
    perf_out: Path | None,
    timeout_s: float = EXEC_TIMEOUT_S,
) -> Execution:
    """Run the workload once and time it.

    Parameters
    ----------
    argv : list of str
        The workload command.
    cwd : Path
        The tree.
    env : dict of str to str
        Its environment.
    perf_out : Path or None
        Where ``perf stat`` writes its count; None to run without perf.
    timeout_s : float
        Timeout, seconds. A named ``docker run`` has its container
        killed on timeout.

    Returns
    -------
    Execution
        Wall and CPU time, exit status and instruction count.
    """
    run_argv = argv
    if perf_out is not None:
        # A count left by the previous execution must never be read
        # as this one's.
        perf_out.unlink(missing_ok=True)
        run_argv = ["perf", "stat", "-x,", "-e", machine.PERF_EVENT]
        run_argv += ["-o", str(perf_out), "--", *argv]
    cpu0 = _children_cpu()
    t0 = time.perf_counter_ns()
    try:
        code = subprocess.run(
            run_argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_s,
            check=False,
        ).returncode
    except FileNotFoundError:
        code = 127
    except subprocess.TimeoutExpired:
        code = 124
        name = trees.check.container_name(argv)
        if name is not None:
            trees.check._kill_container(name)  # pyright: ignore[reportPrivateUsage]
    wall = (time.perf_counter_ns() - t0) / 1e9
    cpu = _children_cpu() - cpu0
    count = None
    if perf_out is not None and code == 0 and perf_out.exists():
        count, _ = machine.parse_perf_csv(perf_out.read_text())
    return Execution(wall, cpu, code, count)


def orders(seed: int, n: int) -> list[bool]:
    """Return which arm goes first in each pair.

    Parameters
    ----------
    seed : int
        The run's seed.
    n : int
        Number of pairs.

    Returns
    -------
    list of bool
        True where arm A runs first: ``random.Random(seed).random() <
        0.5``, one draw per pair in pair order.
    """
    rng = random.Random(seed)  # noqa: S311 - an order, not a secret
    return [rng.random() < 0.5 for _ in range(n)]


@dataclass(frozen=True)
class Arm:
    """One side of the comparison.

    Attributes
    ----------
    name : str
        ``a`` or ``b``.
    tree : str
        ``task`` or ``reference``.
    path : Path
        The tree's directory.
    env : dict of str to str
        Its environment.
    """

    name: str
    tree: str
    path: Path
    env: dict[str, str]


class LogMismatchError(RuntimeError):
    """A pair log exists for a different run."""


def read_log(
    path: Path, header: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Read a pair log, checking it belongs to this run.

    Parameters
    ----------
    path : Path
        The JSON-lines log.
    header : dict
        What identifies the run, without the seed: task, mode, machine,
        commits, workload command, pairs requested.

    Returns
    -------
    header : dict or None
        The log's header (with its seed), or None if there is no log.
    pairs : list of dict
        The finished pairs, in order. A last line cut off mid-write is
        dropped.

    Raises
    ------
    LogMismatchError
        If the log's header differs from ``header`` apart from the
        seed.
    """
    if not path.exists():
        return None, []
    lines = path.read_text().splitlines()
    if not lines:
        return None, []
    found = json.loads(lines[0])
    if {k: v for k, v in found.items() if k != "seed"} != header:
        msg = f"{path} is the log of a different run; move it aside"
        raise LogMismatchError(msg)
    pairs: list[dict[str, Any]] = []
    for line in lines[1:]:
        try:
            pairs.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return found, pairs


def run_pairs(
    arms: tuple[Arm, Arm],
    workload: str,
    n: int,
    seed: int,
    log: Path,
    done: int,
    perf: bool,
    progress: Callable[[int, int], None] | None = None,
    timeout_s: float = EXEC_TIMEOUT_S,
) -> dict[str, Any]:
    """Warm up, then run pairs ``done .. n - 1``, appending each.

    Parameters
    ----------
    arms : tuple of Arm
        Arm A and arm B.
    workload : str
        The workload command line, split with `shlex.split`.
    n : int
        Pairs wanted in all.
    seed : int
        The run's seed.
    log : Path
        The JSON-lines log to append to, holding its header and the
        ``done`` pairs already finished, and nothing else.
    done : int
        Pairs already finished.
    perf : bool
        Run each execution under ``perf stat``.
    progress : callable, optional
        Called with (pairs done, n) after each pair.
    timeout_s : float
        Timeout per execution, seconds.

    Returns
    -------
    dict
        This segment's warm-up executions, its first pair index, and
        the first failed execution (pair index, arm, exit status), if
        any. Pairs stop at the first failed execution.
    """
    argv = shlex.split(workload)
    perf_dir = log.parent
    perf_out = {arm.name: perf_dir / f"perf.{arm.name}.csv" for arm in arms}

    def one(arm: Arm) -> Execution:
        out = perf_out[arm.name] if perf else None
        return execute(argv, arm.path, arm.env, out, timeout_s)

    warm = [
        {"arm": arm.name, **asdict(one(arm))}
        for _ in range(WARMUP)
        for arm in arms
    ]
    segment: dict[str, Any] = {
        "first_pair": done,
        "warmup": warm,
        "failed": None,
    }
    first = orders(seed, n)
    a, b = arms
    with log.open("a") as fh:
        for i in range(done, n):
            order = (a, b) if first[i] else (b, a)
            result: dict[str, Execution] = {}
            for arm in order:
                result[arm.name] = one(arm)
            pair = {
                "i": i,
                "a_first": first[i],
                "a": asdict(result["a"]),
                "b": asdict(result["b"]),
                "load1": machine.load1(),
            }
            fh.write(json.dumps(pair) + "\n")
            fh.flush()
            bad = [x for x in order if result[x.name].exit_code != 0]
            if bad:
                segment["failed"] = {
                    "pair": i,
                    "arm": bad[0].name,
                    "exit_code": result[bad[0].name].exit_code,
                }
                break
            if progress is not None:
                progress(i + 1, n)
    return segment
