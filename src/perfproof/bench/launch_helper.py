"""Launch one benchmark execution from a small process.

The runner starts this file as ``python -S -I launch_helper.py`` on
Linux, once per execution, and the helper starts, times and reaps the
benchmark. It exists for one reason: the kernel gives a new process
the resident-set high-water mark of the address space it was started
from, and keeps it across ``exec``. A benchmark started from perfproof
itself, a NumPy-sized process, would report at least perfproof's own
peak, which hides every smaller benchmark. A benchmark started from this
process starts from this process's high-water mark instead, a few
megabytes, so the peak ``wait4`` reports for it is the benchmark's own
whenever it exceeds that floor. The helper reports the floor (its own
high-water mark, read just before the spawn), so the runner can tell a
measured peak from the floor.

The benchmark is started with ``posix_spawn``, which shares this
process's address space until ``exec`` and runs no Python in the child.
That keeps the cost of the spawn where `subprocess` has it, a fraction
of the 0.3 ms a Python ``fork`` adds to every execution (measured on
Linux arm64); ``fork`` would give a floor about 2 MB lower, which is
not worth that much time in every measurement. ``posix_spawn`` reports
an ``exec`` failure as the ``errno`` and file name ``execve`` gives,
except under user-mode emulation (amd64 containers on Apple silicon),
where the child just exits with status 127. A probe before the
helper's clock starts finds out, and the helper then uses ``fork`` and
``execve`` with an error pipe, as `subprocess` does, so an unstartable
program is a launch failure there too. The floor is the same either
way: the helper's own high-water mark, never below the ``fork`` child's.

This file imports only built-in modules, and never ``perfproof`` or
``typing`` at run time: its own resident size is the floor it exists to
lower. It is a script, not a module of the package: nothing imports it.

Protocol
--------
Standard input carries one `marshal` dictionary (``program``, ``argv``,
``cwd``, ``env``, ``timeout_ns``, ``stdout_fd``, ``stderr_fd``,
``lifeline_fd``, and optionally ``fork_exec``, which forces the
``fork`` path for the tests), then ends. The benchmark's output files
and the read end of a lifeline pipe arrive as inherited descriptors.
Standard output carries one `marshal` dictionary: ``{"error": ...}``
when the program could not be started (``errno``, ``strerror``,
``filename`` of the ``OSError``), ``{"interrupted": True}`` when a
signal or a byte on the lifeline cut the run short (the benchmark's
process group is killed), and otherwise the status, the timings, the
resource usage and ``floor``. The runner writes that byte when it is
interrupted, so a benchmark never outlives an error of the run that
started it. If the runner dies instead the lifeline only reaches end of
file, and the run goes on, as a benchmark did when the runner was its
parent: `perfproof.bench.detach` finds and ends what a dead run left.
`marshal` is safe here: both ends are this interpreter, and the data
is produced by perfproof and by this file.

Timing is the runner's contract unchanged: the clock starts before the
spawn, stops when the exit is noticed, and the exit is noticed through a
``SIGCHLD`` wakeup, so no polling and no thread runs while the
benchmark does.
"""

import marshal
import os
import resource
import select
import signal
import sys
import time

TYPE_CHECKING = False
if TYPE_CHECKING:
    from typing import Any

_HANDLED = tuple(
    getattr(signal, name)
    for name in ("SIGCHLD", "SIGINT", "SIGTERM", "SIGHUP")
)
_RESET = tuple(getattr(signal, name) for name in ("SIGPIPE", "SIGXFSZ"))
_MAX_POLL_MS = 2**31 - 1
_FLOOR_MARGIN_KIB = 64
"""Slack on the floor for what this process allocates between reading
its high-water mark and the ``exec`` that carries it into the benchmark;
a value within it is reported unknown rather than risk calling the floor
a measurement."""


def _noop(signum: int, frame: object) -> None:
    """Let a signal reach the wakeup descriptor and do nothing else."""


def _read_all(fd: int) -> bytes:
    chunks: list[bytes] = []
    while chunk := os.read(fd, 1 << 16):
        chunks.append(chunk)
    return b"".join(chunks)


def _error(exc: OSError) -> "dict[str, Any]":
    return {
        "errno": exc.errno,
        "strerror": exc.strerror,
        "filename": exc.filename if isinstance(exc.filename, str) else None,
    }


def _floor_kib() -> int:
    """Return the peak resident size the benchmark will start from, KiB.

    This process's ``VmHWM``, the high-water mark of its own address
    space, which a ``posix_spawn`` child shares until ``exec``. Not
    ``ru_maxrss``: this process was itself started from perfproof, and
    that figure carries perfproof's peak. Without ``/proc`` (not Linux)
    the floor is unused, and ``ru_maxrss`` stands in so that nothing
    reads it as small.
    """
    try:
        with open("/proc/self/status", "rb") as status:
            for line in status:
                if line.startswith(b"VmHWM:"):
                    return int(line.split()[1]) + _FLOOR_MARGIN_KIB
    except (OSError, ValueError, IndexError):
        pass
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def _spawn_reports_errors() -> bool:
    """Whether ``posix_spawn`` raises when the program cannot start.

    It does on native Linux and macOS. Under user-mode emulation it
    starts a child that exits with status 127 instead, which would pass
    for the benchmark's own exit; that child is reaped here.
    """
    try:
        pid = os.posix_spawn("/nonexistent/perfproof-probe", ["x"], {})
    except OSError:
        return True
    os.wait4(pid, 0)
    return False


def _fork_exec(request: "dict[str, Any]") -> int:
    """`_spawn` with ``fork`` and ``execve``, for emulated hosts.

    The child reports a failure to start through a close-on-exec pipe,
    which stays empty when ``exec`` succeeds, and this raises it as the
    ``OSError`` the child saw.
    """
    info_r, info_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            for signum in (*_HANDLED, *_RESET):
                signal.signal(signum, signal.SIG_DFL)
            signal.set_wakeup_fd(-1)
            os.setpgid(0, 0)
            devnull = os.open(os.devnull, os.O_RDONLY)
            os.dup2(devnull, 0)
            os.dup2(request["stdout_fd"], 1)
            os.dup2(request["stderr_fd"], 2)
            for fd in {
                devnull,
                request["stdout_fd"],
                request["stderr_fd"],
                request["lifeline_fd"],
            }:
                if fd > 2:
                    os.close(fd)
            os.execve(request["program"], request["argv"], request["env"])  # noqa: S606
        except OSError as exc:
            os.write(info_w, marshal.dumps(_error(exc)))
        except BaseException:
            os.write(info_w, marshal.dumps({"errno": None}))
        os._exit(127)
    os.close(info_w)
    fault = _read_all(info_r)
    os.close(info_r)
    if fault:
        os.wait4(pid, 0)
        found = marshal.loads(fault)  # noqa: S302
        raise OSError(
            found["errno"] or 0, found.get("strerror"), found.get("filename")
        )
    return pid


def _spawn(request: "dict[str, Any]", fork_exec: bool) -> int:
    """Start the benchmark in its own process group and return its pid.

    Descriptors 0 to 2 become ``/dev/null`` and the output files, and
    the ones this process inherited for its own use are closed in the
    child, so a benchmark never holds the lifeline open. ``SIGPIPE``
    and ``SIGXFSZ``, which Python ignores, are default again, as
    `subprocess` leaves them.
    """
    if fork_exec:
        return _fork_exec(request)
    actions = [
        (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
        (os.POSIX_SPAWN_DUP2, request["stdout_fd"], 1),
        (os.POSIX_SPAWN_DUP2, request["stderr_fd"], 2),
        *(
            (os.POSIX_SPAWN_CLOSE, fd)
            for fd in {
                request["stdout_fd"],
                request["stderr_fd"],
                request["lifeline_fd"],
            }
        ),
    ]
    return os.posix_spawn(
        request["program"],
        request["argv"],
        request["env"],
        file_actions=actions,
        setpgroup=0,
        setsigdef=_RESET,
    )


def _run(request: "dict[str, Any]") -> "dict[str, Any]":
    life_fd: int = request["lifeline_fd"]
    wake_r, wake_w = os.pipe()
    os.set_blocking(wake_r, False)
    os.set_blocking(wake_w, False)
    signal.set_wakeup_fd(wake_w, warn_on_full_buffer=False)
    for signum in _HANDLED:
        signal.signal(signum, _noop)
    poller = select.poll()
    poller.register(wake_r, select.POLLIN)
    poller.register(life_fd, select.POLLIN)
    try:
        os.chdir(request["cwd"])
    except OSError as exc:
        return {"error": _error(exc)}
    floor = _floor_kib()
    fork_exec = bool(request.get("fork_exec")) or not _spawn_reports_errors()
    start = time.perf_counter_ns()
    try:
        pid = _spawn(request, fork_exec)
    except OSError as exc:
        return {"error": _error(exc)}
    deadline = start + request["timeout_ns"]
    reaped: tuple[int, int, resource.struct_rusage] | None = None
    interrupted = False
    end = start
    while reaped is None and not interrupted:
        remaining = deadline - time.perf_counter_ns()
        if remaining <= 0:
            end = time.perf_counter_ns()
            break
        events = poller.poll(min(-(-remaining // 1_000_000), _MAX_POLL_MS))
        end = time.perf_counter_ns()
        for fd, _ in events:
            woken = os.read(wake_r, 1 << 12) if fd == wake_r else b""
            if fd == life_fd:
                # A byte is the runner saying stop; end of file is the
                # runner dying, and the run is left to finish and be
                # found by the detach code, as before the helper.
                if os.read(life_fd, 1):
                    interrupted = True
                else:
                    poller.unregister(life_fd)
            elif any(b != signal.SIGCHLD for b in woken):
                interrupted = True
        if events and not interrupted:
            found = os.wait4(pid, os.WNOHANG)
            if found[0] == pid:
                reaped = found
    timed_out = reaped is None and not interrupted
    if reaped is None:
        try:  # noqa: SIM105
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        reaped = os.wait4(pid, 0)
    if interrupted:
        return {"interrupted": True}
    _, status, usage = reaped
    return {
        "wall_ns": end - start,
        "status": status,
        "timed_out": timed_out,
        "utime": usage.ru_utime,
        "stime": usage.ru_stime,
        "maxrss": usage.ru_maxrss,
        "floor": floor,
    }


def main() -> None:
    """Read the request, run it, write the result."""
    request = marshal.loads(_read_all(0))  # noqa: S302
    os.write(1, marshal.dumps(_run(request)))


if __name__ == "__main__":
    main()
    sys.exit(0)
