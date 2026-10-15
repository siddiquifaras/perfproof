"""Probe the machine perfproof runs on into a `MachineFingerprint`.

`fingerprint` reads the host once. It is meant to run once per
comparison or session, before the first round and never between
rounds: it starts several short subprocesses (``sysctl``, ``pmset``,
``nvidia-smi``) whose cost would land in the next measurement.

Every probe is bounded. Commands run with an argv list, a timeout, no
stdin and ``LC_ALL=C``, in their own process group so a timeout also
kills anything they started. A probe that fails, times out or prints
something unexpected leaves its fields ``None`` with the reason in
``missing``; nothing is filled in.
"""

import contextlib
import ctypes
import hashlib
import os
import platform
import shutil
import signal
import subprocess
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from perfproof.machine.parsers import (
    NVIDIA_QUERY,
    Missing,
    Probed,
    parse_boot_id,
    parse_cooling_devices,
    parse_cpuinfo,
    parse_governors,
    parse_machine_id,
    parse_nvidia_smi,
    parse_pmset_batt,
    parse_pmset_settings,
    parse_pmset_therm,
    parse_power_supplies,
    parse_sysctl,
)
from perfproof.schema import GpuInfo, MachineFingerprint

__all__ = [
    "DEFAULT_GPU_TIMEOUT_SECONDS",
    "DEFAULT_TIMEOUT_SECONDS",
    "ProbeError",
    "fingerprint",
    "host_id",
    "linux_host_id",
    "run_tool",
]

DEFAULT_TIMEOUT_SECONDS: Final = 5.0
"""Timeout for each OS command and the host UUID call, in seconds."""

DEFAULT_GPU_TIMEOUT_SECONDS: Final = 15.0
"""Timeout for ``nvidia-smi``, in seconds.

Longer than the default because nvidia-smi initialises the driver on
each call when persistence mode is off.
"""

_HOST_ID_DOMAIN: Final = "perfproof-host-v1"
_SYSCTL_NAMES: Final = {
    "cpu_model": "machdep.cpu.brand_string",
    "physical_cores": "hw.physicalcpu",
    "logical_cores": "hw.logicalcpu",
    "memory_bytes": "hw.memsize",
}
_MACHINE_ID_PATHS: Final = (
    Path("/etc/machine-id"),
    Path("/var/lib/dbus/machine-id"),
)
_BOOT_ID: Final = Path("/proc/sys/kernel/random/boot_id")
_SYSFS_CPU: Final = Path("/sys/devices/system/cpu")
_POWER_SUPPLY: Final = Path("/sys/class/power_supply")
_THERMAL: Final = Path("/sys/class/thermal")
_NVIDIA_PROC: Final = Path("/proc/driver/nvidia/gpus")


class ProbeError(Exception):
    """A probe could not produce a value.

    Parameters
    ----------
    reason : str
        Why, in words that go into ``missing`` unchanged.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def run_tool(
    argv: Sequence[str], timeout_seconds: float, *, partial_ok: bool = False
) -> str:
    """Run one probe command and return its standard output.

    Parameters
    ----------
    argv : Sequence[str]
        Command and arguments. A bare command name is looked up on
        ``PATH``; reasons name the command by its file name.
    timeout_seconds : float
        Wall-clock limit, in seconds. On expiry the command's whole
        process group is killed.
    partial_ok : bool, optional
        Return standard output after a non-zero exit when there is
        any. ``sysctl`` needs this: it exits 1 when one of several
        names is unknown and still prints the rest.

    Returns
    -------
    str
        Standard output, decoded as UTF-8 with replacement.

    Raises
    ------
    ProbeError
        If the command is not found, cannot start, times out, or exits
        non-zero (unless ``partial_ok`` and it printed something). The
        reason quotes the first line of its standard error, or of its
        standard output when standard error is empty, as
        ``nvidia-smi`` reports driver failures there.
    """
    tool = Path(argv[0]).name
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "LC_ALL": "C"},
            start_new_session=True,
        )
    except FileNotFoundError:
        raise ProbeError(f"{tool} not found") from None
    except OSError as error:
        raise ProbeError(f"{tool} could not start: {error}") from None
    try:
        out, err = proc.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        raise ProbeError(
            f"{tool} timed out after {timeout_seconds:g} s"
        ) from None
    stdout = out.decode("utf-8", errors="replace")
    if proc.returncode == 0 or (partial_ok and stdout.strip()):
        return stdout
    detail = err.decode("utf-8", errors="replace").strip() or stdout.strip()
    first = detail.splitlines()[0] if detail else "no output"
    raise ProbeError(f"{tool} exited {proc.returncode}: {first}")


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    """Kill a timed-out probe's process group and reap it.

    Killing only the child would leave grandchildren holding its pipes
    open, and the final ``communicate`` would wait for them.
    """
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)
    try:
        proc.communicate(timeout=DEFAULT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def host_id(system: str, *raw_ids: str) -> str:
    """Turn OS host identifiers into perfproof's host identity.

    The raw identifiers (the macOS hardware UUID; the Linux machine-id
    and boot ID) are hashed so a record that is shared does not carry
    them.

    Parameters
    ----------
    system : str
        OS name, as `platform.system` returns it.
    *raw_ids : str
        The identifiers the OS reports, in a fixed order.

    Returns
    -------
    str
        Lowercase hex SHA-256 of the domain tag, ``system`` and
        ``raw_ids``, separated by NUL bytes.
    """
    payload = "\0".join((_HOST_ID_DOMAIN, system, *raw_ids))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class _Timespec(ctypes.Structure):
    _fields_ = (("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long))


def _darwin_hardware_uuid(timeout_seconds: float) -> str:
    """Read the hardware UUID with ``gethostuuid(2)``.

    It is the value ``ioreg`` shows as ``IOPlatformUUID``, fixed for
    the machine across reboots, and the call takes its own timeout, so
    no subprocess is needed.
    """
    try:
        gethostuuid = ctypes.CDLL(None, use_errno=True).gethostuuid
    except (OSError, AttributeError) as error:
        raise ProbeError(f"gethostuuid unavailable: {error}") from None
    buffer = (ctypes.c_ubyte * 16)()
    seconds = int(timeout_seconds)
    wait = _Timespec(seconds, int((timeout_seconds - seconds) * 1e9))
    if gethostuuid(buffer, ctypes.byref(wait)) != 0:
        errno = ctypes.get_errno()
        raise ProbeError(f"gethostuuid failed: {os.strerror(errno)}")
    value = uuid.UUID(bytes=bytes(buffer))
    if value.int == 0:
        raise ProbeError("gethostuuid returned the null UUID")
    return str(value).upper()


def _read(path: Path) -> str:
    """Read a small text file, raising `ProbeError` when it cannot."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        reason = error.strerror or type(error).__name__
        raise ProbeError(f"cannot read {path}: {reason}") from None


def _attributes(directory: Path, names: Sequence[str]) -> dict[str, str]:
    """Read whichever of the named sysfs files are readable."""
    values: dict[str, str] = {}
    for name in names:
        try:
            path = directory / name
            values[name] = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
    return values


def _probe[T](probe: Callable[[], Probed[T]]) -> Probed[T]:
    """Call one probe, turning a `ProbeError` into `Missing`."""
    try:
        return probe()
    except ProbeError as error:
        return Missing(error.reason)


def _positive_int(text: str | None, name: str) -> Probed[int]:
    """Read a positive integer reported under ``name``."""
    if text is None:
        return Missing(f"sysctl did not report {name}")
    if not text.isdigit() or int(text) < 1:
        return Missing(f"sysctl reported {name} as {text!r}")
    return int(text)


def _darwin(timeout_seconds: float) -> dict[str, Probed[object]]:
    """Run the macOS probes."""
    found: dict[str, Probed[object]] = {}
    found["host_id"] = _probe(
        lambda: host_id("Darwin", _darwin_hardware_uuid(timeout_seconds))
    )
    found["os_version"] = platform.mac_ver()[0] or Missing(
        "platform.mac_ver reported no version"
    )
    try:
        sysctl = parse_sysctl(
            run_tool(
                ("sysctl", *_SYSCTL_NAMES.values()),
                timeout_seconds,
                partial_ok=True,
            )
        )
    except ProbeError as error:
        for field in _SYSCTL_NAMES:
            found[field] = Missing(error.reason)
    else:
        brand = sysctl.get(_SYSCTL_NAMES["cpu_model"])
        found["cpu_model"] = brand or Missing(
            f"sysctl did not report {_SYSCTL_NAMES['cpu_model']}"
        )
        for field in ("physical_cores", "logical_cores", "memory_bytes"):
            name = _SYSCTL_NAMES[field]
            found[field] = _positive_int(sysctl.get(name), name)
    try:
        source, percent = parse_pmset_batt(
            run_tool(("pmset", "-g", "batt"), timeout_seconds)
        )
    except ProbeError as error:
        source = percent = Missing(error.reason)
    found["power_source"] = source
    found["battery_percent"] = percent
    found["thermal_state"] = _probe(
        lambda: parse_pmset_therm(
            run_tool(("pmset", "-g", "therm"), timeout_seconds)
        )
    )
    found["frequency_policy"] = _probe(
        lambda: parse_pmset_settings(
            run_tool(("pmset", "-g"), timeout_seconds)
        )
    )
    return found


def linux_host_id(
    machine_id_paths: Sequence[Path] = _MACHINE_ID_PATHS,
    boot_id_path: Path = _BOOT_ID,
) -> str:
    """Derive a Linux host identity: this kernel boot.

    The identity is the kernel's boot ID, combined with the systemd
    machine-id when one exists. The machine-id alone is not enough:
    containers built from one image and VMs cloned from one snapshot
    share it. The boot ID is random per kernel boot, so two machines or
    VMs cannot share it, and every container on one kernel reads the
    same one, so they agree, as processes on one host should. A reboot
    changes the boot ID and so the identity; a comparison spanning a
    reboot then fails the ``same_host`` gate, which fails closed.

    Parameters
    ----------
    machine_id_paths : Sequence[Path], optional
        Files to try for the systemd machine-id, in order; the first
        valid one is used. When none of them exists, as in many
        container images, the identity is the boot ID alone.
    boot_id_path : Path, optional
        The kernel's boot ID file.

    Returns
    -------
    str
        `host_id` of the machine-id and the boot ID, or of the boot ID
        alone when no machine-id file exists.

    Raises
    ------
    ProbeError
        If the boot ID is unreadable or malformed, or a machine-id file
        exists but none is readable and valid (such as
        ``uninitialized`` during first boot).
    """
    reasons: list[str] = []
    machine_id: str | None = None
    for path in machine_id_paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        except OSError as error:
            reasons.append(f"cannot read {path}: {error.strerror}")
            continue
        parsed = parse_machine_id(text)
        if isinstance(parsed, Missing):
            reasons.append(f"{path}: {parsed.reason}")
            continue
        machine_id = parsed
        break
    if machine_id is None and reasons:
        raise ProbeError("; ".join(reasons))
    boot_id = parse_boot_id(_read(boot_id_path))
    if isinstance(boot_id, Missing):
        raise ProbeError(f"{boot_id_path}: {boot_id.reason}")
    if machine_id is None:
        return host_id("Linux", boot_id)
    return host_id("Linux", machine_id, boot_id)


def _linux_os_version() -> str:
    """Read the distribution name from ``os-release``."""
    try:
        release = platform.freedesktop_os_release()
    except OSError as error:
        raise ProbeError(f"no os-release: {error.strerror}") from None
    name = release.get("PRETTY_NAME") or " ".join(
        part
        for part in (release.get("NAME"), release.get("VERSION_ID"))
        if part
    )
    if not name:
        raise ProbeError("os-release has no PRETTY_NAME, NAME or VERSION_ID")
    return name


def _linux_memory() -> int:
    """Physical memory from ``sysconf``, in bytes."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError) as error:
        raise ProbeError(f"sysconf failed: {error}") from None
    if pages < 1 or size < 1:
        raise ProbeError(f"sysconf reported {pages} pages of {size} bytes")
    return pages * size


def _linux_governors() -> Probed[str]:
    """Read every CPU's cpufreq governor."""
    paths = sorted(_SYSFS_CPU.glob("cpu[0-9]*/cpufreq/scaling_governor"))
    return parse_governors(_read(path) for path in paths)


def _sysfs_classes(
    root: Path, pattern: str, names: Sequence[str]
) -> dict[str, dict[str, str]]:
    """Read attribute files of every device directory under ``root``."""
    return {
        path.name: _attributes(path, names)
        for path in sorted(root.glob(pattern))
    }


def _linux() -> dict[str, Probed[object]]:
    """Run the Linux probes."""
    found: dict[str, Probed[object]] = {}
    found["host_id"] = _probe(linux_host_id)
    found["os_version"] = _probe(_linux_os_version)
    try:
        cpu = parse_cpuinfo(_read(Path("/proc/cpuinfo")))
    except ProbeError as error:
        found["cpu_model"] = Missing(error.reason)
        found["logical_cores"] = Missing(error.reason)
        found["physical_cores"] = Missing(error.reason)
    else:
        found["cpu_model"] = cpu.model
        found["logical_cores"] = cpu.logical_cores
        found["physical_cores"] = cpu.physical_cores
    found["memory_bytes"] = _probe(_linux_memory)
    found["frequency_policy"] = _probe(_linux_governors)
    source, percent = parse_power_supplies(
        _sysfs_classes(
            _POWER_SUPPLY,
            "*",
            ("type", "online", "status", "capacity", "scope"),
        )
    )
    found["power_source"] = source
    found["battery_percent"] = percent
    found["thermal_state"] = parse_cooling_devices(
        _sysfs_classes(
            _THERMAL, "cooling_device*", ("type", "cur_state", "max_state")
        )
    )
    return found


def _gpus(timeout_seconds: float) -> Probed[tuple[GpuInfo, ...]]:
    """Query NVIDIA GPUs through ``nvidia-smi`` when it is installed.

    No ``nvidia-smi`` on ``PATH`` and no NVIDIA kernel driver loaded
    means no NVIDIA GPU perfproof could use, so the answer is an empty
    tuple. A loaded driver without ``nvidia-smi`` is unknown.
    """
    executable = shutil.which("nvidia-smi")
    if executable is None:
        try:
            driver = any(_NVIDIA_PROC.iterdir())
        except FileNotFoundError:
            return ()
        except OSError as error:
            return Missing(f"nvidia-smi not on PATH; {error}")
        if driver:
            return Missing("NVIDIA driver loaded but nvidia-smi not on PATH")
        return ()
    query = "--query-gpu=" + ",".join(NVIDIA_QUERY)
    argv = (executable, query, "--format=csv,noheader,nounits")
    try:
        return parse_nvidia_smi(run_tool(argv, timeout_seconds))
    except ProbeError as error:
        return Missing(error.reason)


def _load_average() -> Probed[tuple[float, float, float]]:
    """Read the 1, 5 and 15 minute load averages."""
    try:
        return os.getloadavg()
    except (OSError, AttributeError):
        return Missing("os.getloadavg is unavailable")


def _text(value: str, what: str) -> Probed[str]:
    """Return ``value``, or `Missing` when it is empty."""
    return value or Missing(f"platform module reported no {what}")


def fingerprint(
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    gpu_timeout_seconds: float = DEFAULT_GPU_TIMEOUT_SECONDS,
) -> MachineFingerprint:
    """Fingerprint the machine this process runs on.

    Run it once per comparison or session, before the first round;
    never between rounds.

    Parameters
    ----------
    timeout_seconds : float, optional
        Timeout for each OS command and for the host UUID call, in
        seconds.
    gpu_timeout_seconds : float, optional
        Timeout for ``nvidia-smi``, in seconds.

    Returns
    -------
    MachineFingerprint
        Every probe's value, or ``None`` with the reason in
        ``missing``. On an OS other than macOS and Linux, only the
        OS-independent probes (OS, kernel, architecture, load average,
        Python version, GPUs) are attempted.

    Notes
    -----
    ``host_id`` is the SHA-256 (see `host_id`) of the macOS hardware
    UUID, which is fixed across reboots, or of the Linux
    kernel boot ID, combined with the machine-id when there is one
    (see `linux_host_id`). On Linux the identity is this kernel boot:
    cloned VMs and containers from one image are told apart, containers
    on one kernel share it, and a reboot changes it, which fails the
    ``same_host`` gate closed.
    """
    captured_at = datetime.now(UTC)
    system = platform.system()
    found: dict[str, Probed[object]] = {
        "os": _text(system, "OS name"),
        "kernel": _text(platform.release(), "kernel release"),
        "arch": _text(platform.machine(), "machine architecture"),
        "python_version": _text(platform.python_version(), "Python version"),
        "load_average": _load_average(),
    }
    if system == "Darwin":
        found |= _darwin(timeout_seconds)
    elif system == "Linux":
        found |= _linux()
    else:
        reason = f"not probed on {system or 'this OS'}"
        for field in MachineFingerprint.model_fields:
            if field not in found and field not in ("captured_at", "missing"):
                found[field] = Missing(reason)
    found["gpus"] = _gpus(gpu_timeout_seconds)
    values: dict[str, object] = {"captured_at": captured_at}
    missing: dict[str, str] = {}
    for field, probed in found.items():
        if isinstance(probed, Missing):
            missing[field] = probed.reason
        else:
            values[field] = probed
    values["missing"] = missing
    return MachineFingerprint.model_validate(values)
