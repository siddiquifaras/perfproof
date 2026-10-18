"""What machine a label was measured on, how loaded it was, and perf.

Everything here is a probe: a value that can't be read is ``None``
with the reason beside it, never a plausible default.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROBE_TIMEOUT_S = 30.0
QUIET_FRACTION = 0.25
QUIET_WAIT_S = 900.0
QUIET_POLL_S = 30.0
PERF_EVENT = "instructions:u"


def load1() -> float | None:
    """Return the 1-minute load average, or None where there is none.

    Returns
    -------
    float or None
        ``os.getloadavg()[0]``; None if the platform has no load
        average.
    """
    try:
        return os.getloadavg()[0]
    except OSError:
        return None


def _read(argv: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = proc.stdout.strip()
    return out if proc.returncode == 0 and out else None


def cpu_model() -> str | None:
    """Return the CPU model name.

    Returns
    -------
    str or None
        From ``sysctl machdep.cpu.brand_string`` on macOS or the first
        ``model name`` in ``/proc/cpuinfo`` on Linux; None if neither
        could be read.
    """
    if sys.platform == "darwin":
        return _read(["sysctl", "-n", "machdep.cpu.brand_string"])
    try:
        text = Path("/proc/cpuinfo").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "model name" and value.strip():
            return value.strip()
    return None


def memory_bytes() -> int | None:
    """Return total physical memory in bytes, or None if unreadable.

    Returns
    -------
    int or None
        ``hw.memsize`` on macOS, ``MemTotal`` on Linux.
    """
    if sys.platform == "darwin":
        out = _read(["sysctl", "-n", "hw.memsize"])
        return int(out) if out and out.isdigit() else None
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
    return None


def parse_perf_csv(
    text: str, event: str = PERF_EVENT
) -> tuple[int | None, str]:
    """Read one event's count from ``perf stat -x,`` output.

    Parameters
    ----------
    text : str
        The output file of ``perf stat -x, -o <file>``.
    event : str
        The event name to look for.

    Returns
    -------
    count : int or None
        The count, or None when perf did not count it.
    reason : str
        Empty when counted; otherwise what perf printed instead, such
        as ``<not supported>``.
    """
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split(",")
        if len(fields) < 3 or fields[2] != event:
            continue
        value = fields[0].strip()
        if value.isdigit():
            return int(value), ""
        return None, value or "empty count"
    return None, f"no {event} line in perf output"


@dataclass(frozen=True)
class PerfProbe:
    """Whether ``perf stat`` counts instructions here.

    Attributes
    ----------
    works : bool
        True when a probe run gave a count.
    reason : str
        Why not, when it doesn't.
    version : str or None
        ``perf --version``.
    """

    works: bool
    reason: str
    version: str | None


def probe_perf() -> PerfProbe:
    """Run ``perf stat`` once on a trivial child to see if it counts.

    Returns
    -------
    PerfProbe
        ``works`` is False off Linux, without perf, or when the count
        is missing (``perf_event_paranoid``, a VM without a PMU).
    """
    if not sys.platform.startswith("linux"):
        return PerfProbe(False, f"not Linux ({sys.platform})", None)
    perf = shutil.which("perf")
    if perf is None:
        return PerfProbe(False, "perf is not installed", None)
    version = _read([perf, "--version"])
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "perf.csv"
        argv = [perf, "stat", "-x,", "-e", PERF_EVENT, "-o", str(out)]
        argv += ["--", sys.executable, "-c", "pass"]
        try:
            proc = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PerfProbe(False, f"perf stat failed: {exc}", version)
        if proc.returncode != 0:
            tail = proc.stderr.strip().splitlines()[-3:]
            reason = f"perf stat exit {proc.returncode}: " + " ".join(tail)
            return PerfProbe(False, reason, version)
        count, reason = parse_perf_csv(out.read_text())
    if count is None or count == 0:
        return PerfProbe(False, reason or "count was 0", version)
    return PerfProbe(True, "", version)


@dataclass(frozen=True)
class Machine:
    """The machine a result was measured on.

    Attributes
    ----------
    name : str
        The name given on the command line (``--machine``).
    cpu_model : str or None
        CPU model name.
    arch : str
        ``platform.machine()``.
    system : str
        ``platform.system()``.
    kernel : str
        ``platform.release()``.
    kernel_version : str
        ``platform.version()``.
    platform : str
        ``platform.platform()``.
    logical_cpus : int or None
        ``os.cpu_count()``.
    memory_bytes : int or None
        Total physical memory.
    python : str
        The labeller's Python version.
    perf : PerfProbe
        Whether perf counters work.
    """

    name: str
    cpu_model: str | None
    arch: str
    system: str
    kernel: str
    kernel_version: str
    platform: str
    logical_cpus: int | None
    memory_bytes: int | None
    python: str
    perf: PerfProbe

    def to_json(self) -> dict[str, Any]:
        """Return the machine as JSON-ready data.

        Returns
        -------
        dict
            Field names to values.
        """
        return asdict(self)


def describe(name: str) -> Machine:
    """Probe this machine.

    Parameters
    ----------
    name : str
        The machine's name for results and ``combine``.

    Returns
    -------
    Machine
        What could be read; unreadable values are None.
    """
    return Machine(
        name=name,
        cpu_model=cpu_model(),
        arch=platform.machine(),
        system=platform.system(),
        kernel=platform.release(),
        kernel_version=platform.version(),
        platform=platform.platform(),
        logical_cpus=os.cpu_count(),
        memory_bytes=memory_bytes(),
        python=platform.python_version(),
        perf=probe_perf(),
    )


def _no_samples() -> list[tuple[float, float | None]]:
    return []


@dataclass
class Quiet:
    """The quiet check's outcome.

    Attributes
    ----------
    passed : bool
        Whether the load fell to the threshold within the wait.
    threshold : float or None
        ``QUIET_FRACTION`` times the logical CPU count.
    samples : list of tuple of (float, float or None)
        Seconds since the check began, and the 1-minute load average.
    reason : str
        Why it failed, when it did.
    """

    passed: bool
    threshold: float | None
    samples: list[tuple[float, float | None]] = field(
        default_factory=_no_samples
    )
    reason: str = ""


def quiet_check(
    wait_s: float = QUIET_WAIT_S,
    poll_s: float = QUIET_POLL_S,
    fraction: float = QUIET_FRACTION,
    read_load: Callable[[], float | None] = load1,
    sleep: Callable[[float], None] = time.sleep,
) -> Quiet:
    """Wait for the 1-minute load average to fall to the threshold.

    Parameters
    ----------
    wait_s : float
        Longest wait, seconds.
    poll_s : float
        Seconds between samples.
    fraction : float
        The threshold as a fraction of the logical CPU count.
    read_load : callable
        Returns the 1-minute load average (for tests).
    sleep : callable
        Sleeps for the given seconds (for tests).

    Returns
    -------
    Quiet
        ``passed`` is False when the load stayed above the threshold
        for the whole wait, or the load or CPU count can't be read.
    """
    cpus = os.cpu_count()
    if cpus is None:
        return Quiet(False, None, reason="the CPU count can't be read")
    threshold = fraction * cpus
    result = Quiet(False, threshold)
    waited = 0.0
    while True:
        load = read_load()
        result.samples.append((waited, load))
        if load is None:
            result.reason = "the load average can't be read"
            return result
        if load <= threshold:
            result.passed = True
            return result
        if waited + poll_s > wait_s:
            result.reason = (
                f"load {load:.2f} stayed above {threshold:.2f} "
                f"({fraction} x {cpus} CPUs) for {waited:.0f} s"
            )
            return result
        sleep(poll_s)
        waited += poll_s
