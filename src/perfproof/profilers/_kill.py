"""Kill a command's whole process tree after its timeout.

Shared by the profilers that run a command of their own under a time
limit (`perfproof.profilers.java`, `perfproof.profilers.node`).
``subprocess.run(..., timeout=...)`` kills only the direct child, so a
descendant that outlives it is orphaned.
"""

import os
import signal
import subprocess
from collections.abc import Callable

type Say = Callable[[str], None]
"""Where a profiler says something on stderr, with its own prefix."""


def descendants(pid: int) -> list[int] | None:
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


def signal_process(pid: int, signum: int) -> bool:
    """Send a signal; say whether the process was there to get it."""
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        return False
    return True


def freeze_and_kill(pid: int, say: Say) -> None:
    """Stop a process and all its descendants, then kill them all.

    A live parent can fork after a snapshot of its descendants, so the
    tree is frozen first: the process is stopped (``SIGSTOP``), then
    the descendants are snapshotted and stopped again and again until
    a snapshot shows no process that is not already stopped. Only then
    is every stopped process killed, so nothing can fork in between.
    If ``ps`` fails only the process itself is killed, and that is said
    through ``say``.
    """
    stopped = [pid] if signal_process(pid, signal.SIGSTOP) else []
    while stopped:
        found = descendants(pid)
        if found is None:
            say("ps failed, so only the command itself is killed")
            break
        fresh = [p for p in found if p not in stopped]
        if not fresh:
            break
        stopped.extend(p for p in fresh if signal_process(p, signal.SIGSTOP))
    for victim in stopped:
        signal_process(victim, signal.SIGKILL)
    signal_process(pid, signal.SIGKILL)
