"""Run one benchmark execution with an exactly known slowdown.

The spec's command is ``python wrap.py -- CMD ...``. The wrapper
launches ``CMD`` as a child, measures it from spawn to exit (``work``),
then busy-waits on the wall clock until ``fraction * work`` more
seconds have passed (``spin``) and prints ``PERFPROOF {"value": v}``
with ``v = work + spin``, which perfproof reads as a ``reported``
metric. The spin is scaled to this execution's own work, so the log
ratio of a round is ``log(1 + f)`` plus the noise of the two works.

perfproof runs the benchmark with a scrubbed environment, so the
per-replicate settings travel in a file:

``golden.arm`` (file in the working directory)
    ``A`` for the baseline tree, ``B`` for the candidate.
``GOLDEN_CFG`` (declared in the spec's ``env``)
    Path of a JSON file with ``spin_A`` and ``spin_B`` (the fraction
    ``f`` for each arm, default 0), ``log`` (a JSON-lines file, one
    line appended per execution) and ``pin`` (a CPU list for
    ``taskset -c``, default not pinned). The campaign rewrites it
    between comparisons, never during one.

On Linux the child runs under ``perf stat`` so that instructions,
cycles and task clock are recorded; elsewhere those fields are null.
Standard library only, so it runs under any Python 3.9 or later.
"""

import json
import os
import sys
import time
from pathlib import Path

PERF_EVENTS = "instructions:u,cycles:u,task-clock,cpu-migrations"
"""Events counted for the child on Linux."""


def read_ticks() -> tuple[int, int]:
    """Return all-CPU and steal ticks from ``/proc/stat``, or zeros."""
    try:
        with open("/proc/stat", encoding="ascii") as stat:
            fields = [int(v) for v in stat.readline().split()[1:]]
    except (OSError, ValueError):
        return 0, 0
    return sum(fields[:8]), fields[7] if len(fields) > 7 else 0


def read_load() -> float | None:
    """Return the 1-minute load average, or None where unavailable."""
    try:
        return os.getloadavg()[0]
    except OSError:
        return None


def parse_perf(path: str) -> dict[str, float | None]:
    """Parse ``perf stat -x,`` output into event name to value.

    Parameters
    ----------
    path : str
        File written by ``perf stat -o``.

    Returns
    -------
    dict
        Value per event, with the ``:u`` suffix dropped; None for an
        event perf could not count; empty if the file is unreadable.
    """
    found: dict[str, float | None] = {}
    try:
        text = Path(path).read_text(encoding="ascii")
    except OSError:
        return found
    for line in text.splitlines():
        cols = line.split(",")
        if len(cols) < 3 or line.startswith("#"):
            continue
        name = cols[2].split(":")[0]
        try:
            found[name] = float(cols[0])
        except ValueError:
            found[name] = None
    return found


def spawn(argv: list[str]) -> tuple[int, int, float, float]:
    """Run ``argv`` with stdout and stderr discarded; wait for it.

    Returns
    -------
    tuple
        Start and end of the wait in ``perf_counter_ns``, then the
        child's user and system CPU seconds from ``wait4``.

    Raises
    ------
    SystemExit
        With the child's status if it did not exit 0.
    """
    null = os.open(os.devnull, os.O_RDWR)
    try:
        start = time.perf_counter_ns()
        pid = os.posix_spawnp(
            argv[0],
            argv,
            os.environ,
            file_actions=[
                (os.POSIX_SPAWN_DUP2, null, 0),
                (os.POSIX_SPAWN_DUP2, null, 1),
                (os.POSIX_SPAWN_DUP2, null, 2),
            ],
        )
        _, status, usage = os.wait4(pid, 0)
        end = time.perf_counter_ns()
    finally:
        os.close(null)
    code = os.waitstatus_to_exitcode(status)
    if code != 0:
        sys.stderr.write(f"golden wrap: {argv} exited {code}\n")
        raise SystemExit(code if code > 0 else 1)
    return start, end, usage.ru_utime, usage.ru_stime


def main(argv: list[str]) -> int:
    """Run the wrapped command once and report ``work + spin``.

    Parameters
    ----------
    argv : list of str
        ``["--", CMD, ...]`` (the leading ``--`` is optional).

    Returns
    -------
    int
        Exit status, 0 on success.
    """
    cmd = argv[1:] if argv[:1] == ["--"] else argv
    if not cmd:
        sys.stderr.write("golden wrap: no command\n")
        return 2
    arm = Path("golden.arm").read_text(encoding="ascii").strip()
    cfg = json.loads(Path(os.environ["GOLDEN_CFG"]).read_text("ascii"))
    fraction = float(cfg.get(f"spin_{arm}", 0.0))
    log = cfg.get("log")
    pin = cfg.get("pin")
    perf_file = f"/dev/shm/golden-perf-{os.getpid()}"  # noqa: S108
    linux = sys.platform == "linux"
    launch = cmd
    if linux:
        launch = [
            "perf", "stat", "-x,", "-o", perf_file,
            "-e", PERF_EVENTS, "--", *cmd,
        ]  # fmt: skip
    if pin:
        launch = ["taskset", "-c", pin, *launch]
    load = read_load()
    ticks0 = read_ticks()
    start, end, user, system = spawn(launch)
    work = (end - start) / 1e9
    deadline = end + int(fraction * (end - start))
    now = time.perf_counter_ns()
    while now < deadline:
        now = time.perf_counter_ns()
    total = (now - start) / 1e9
    ticks1 = read_ticks()
    counts = parse_perf(perf_file) if linux else {}
    if linux:
        Path(perf_file).unlink(missing_ok=True)
    if log:
        row = {
            "arm": arm,
            "t_ns": time.time_ns(),
            "fraction": fraction,
            "work_s": work,
            "spin_s": (now - end) / 1e9,
            "reported_s": total,
            "user_s": user,
            "sys_s": system,
            "instructions": counts.get("instructions"),
            "cycles": counts.get("cycles"),
            "task_clock_ms": counts.get("task-clock"),
            "cpu_migrations": counts.get("cpu-migrations"),
            "load1": load,
            "ticks": ticks1[0] - ticks0[0],
            "steal_ticks": ticks1[1] - ticks0[1],
            "pinned": pin,
        }
        fd = os.open(log, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(row) + "\n").encode("ascii"))
        finally:
            os.close(fd)
    sys.stdout.write(f'PERFPROOF {{"value": {total:.9f}}}\n')
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
