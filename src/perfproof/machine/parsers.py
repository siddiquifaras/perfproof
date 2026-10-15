"""Pure parsers for what the machine probes read.

Each parser takes the text a command printed, or the attribute files a
sysfs directory holds, and returns either a value or a `Missing` with
the reason the value could not be read. None of them runs a command or
touches the filesystem, so each is tested on captured output.

Several fields are stored as a *state string*: ``key=value`` items
joined by ``"; "`` in key order, for example
``"lowpowermode=0"``. `format_state` writes one and `parse_state`
reads it back, so warnings can be derived from a stored fingerprint
alone.
"""

import csv
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from perfproof.schema import GpuInfo

__all__ = [
    "CpuInfo",
    "Missing",
    "PowerSource",
    "Probed",
    "format_state",
    "parse_boot_id",
    "parse_cooling_devices",
    "parse_cpuinfo",
    "parse_governors",
    "parse_machine_id",
    "parse_nvidia_smi",
    "parse_pmset_batt",
    "parse_pmset_settings",
    "parse_pmset_therm",
    "parse_power_supplies",
    "parse_state",
    "parse_sysctl",
]

PowerSource = Literal["ac", "battery", "ups"]


@dataclass(frozen=True, slots=True)
class Missing:
    """A value a probe could not read.

    Attributes
    ----------
    reason : str
        Why the value is unknown, in words a reader can act on.
    """

    reason: str


type Probed[T] = T | Missing
"""A probed value, or the reason it is unknown."""


@dataclass(frozen=True, slots=True)
class CpuInfo:
    """CPU identity read from ``/proc/cpuinfo``.

    Attributes
    ----------
    model : Probed[str]
        CPU model name.
    logical_cores : Probed[int]
        Number of ``processor`` entries.
    physical_cores : Probed[int]
        Number of distinct ``(physical id, core id)`` pairs.
    """

    model: Probed[str]
    logical_cores: Probed[int]
    physical_cores: Probed[int]


_STATE_SEPARATOR: Final = "; "
_STATE_TOKEN: Final = re.compile(r"[A-Za-z0-9_,./-]+")


def format_state(items: Mapping[str, str]) -> str:
    """Write ``items`` as a state string.

    Parameters
    ----------
    items : Mapping[str, str]
        Keys and values. Both must be non-empty and use only letters,
        digits and ``_ , . / -``.

    Returns
    -------
    str
        ``key=value`` items in key order, joined by ``"; "``.

    Raises
    ------
    ValueError
        If ``items`` is empty, or a key or value has other characters,
        which would make the string ambiguous to `parse_state`.
    """
    if not items:
        msg = "a state string needs at least one item"
        raise ValueError(msg)
    for key, value in items.items():
        if not (_STATE_TOKEN.fullmatch(key) and _STATE_TOKEN.fullmatch(value)):
            msg = f"cannot write {key!r}={value!r} into a state string"
            raise ValueError(msg)
    return _STATE_SEPARATOR.join(f"{k}={items[k]}" for k in sorted(items))


def parse_state(text: str) -> dict[str, str]:
    """Read a state string written by `format_state`.

    Parameters
    ----------
    text : str
        A state string.

    Returns
    -------
    dict[str, str]
        Its items. Text that is not a state string, such as one written
        by an older perfproof, yields only the items that parse.
    """
    items: dict[str, str] = {}
    for part in text.split(_STATE_SEPARATOR):
        key, sep, value = part.partition("=")
        if (
            sep
            and _STATE_TOKEN.fullmatch(key)
            and _STATE_TOKEN.fullmatch(value)
        ):
            items[key] = value
    return items


def parse_sysctl(text: str) -> dict[str, str]:
    """Parse ``sysctl <name>...`` output into names and values.

    Parameters
    ----------
    text : str
        Standard output of ``sysctl`` run with names and without
        ``-n``: one ``name: value`` line per name it knows. Names it
        does not know go to standard error and are simply absent.

    Returns
    -------
    dict[str, str]
        Value text for each name, stripped.
    """
    values: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, value = line.partition(": ")
        if sep and name and " " not in name:
            values[name] = value.strip()
    return values


_DRAWING: Final = re.compile(r"^Now drawing from '([^']*)'\s*$", re.MULTILINE)
_PMSET_SOURCES: Final[Mapping[str, PowerSource]] = {
    "AC Power": "ac",
    "Battery Power": "battery",
    "UPS Power": "ups",
}
_INTERNAL_BATTERY: Final = re.compile(
    r"^\s*-InternalBattery-\d+\b[^\t\n]*\t(\d+)%;", re.MULTILINE
)


def parse_pmset_batt(text: str) -> tuple[Probed[PowerSource], Probed[float]]:
    """Parse ``pmset -g batt`` into power source and battery charge.

    Parameters
    ----------
    text : str
        Standard output of ``pmset -g batt``.

    Returns
    -------
    source : Probed[PowerSource]
        ``"ac"``, ``"battery"`` or ``"ups"`` from the ``Now drawing
        from`` line.
    percent : Probed[float]
        Charge of the internal battery, in percent; `Missing` when the
        machine has no internal battery.
    """
    drawing = _DRAWING.findall(text)
    source: Probed[PowerSource]
    if len(drawing) != 1:
        source = Missing("pmset -g batt printed no 'Now drawing from' line")
    elif drawing[0] in _PMSET_SOURCES:
        source = _PMSET_SOURCES[drawing[0]]
    else:
        source = Missing(f"pmset -g batt reported unknown {drawing[0]!r}")
    batteries = _INTERNAL_BATTERY.findall(text)
    percent: Probed[float]
    if not batteries:
        percent = Missing("pmset -g batt lists no internal battery")
    elif len(batteries) > 1:
        percent = Missing("pmset -g batt lists several internal batteries")
    elif int(batteries[0]) > 100:
        percent = Missing(f"pmset -g batt reported {batteries[0]}%")
    else:
        percent = float(batteries[0])
    return source, percent


_THERM_LINES: Final[tuple[tuple[re.Pattern[str], str | None], ...]] = (
    (
        re.compile(r"Note: No thermal warning level has been recorded"),
        "thermal_warning_level",
    ),
    (
        re.compile(r"Note: No performance warning level has been recorded"),
        "performance_warning_level",
    ),
    (
        re.compile(r"Note: No CPU power status has been recorded"),
        "cpu_power_status",
    ),
    (re.compile(r"Thermal Warning Level = (\d+)"), "thermal_warning_level"),
    (
        re.compile(r"Performance Warning Level = (\d+)"),
        "performance_warning_level",
    ),
    (re.compile(r"(CPU_[A-Za-z_]+)\s*=\s*(\d+)"), None),
)


def parse_pmset_therm(text: str) -> Probed[str]:
    """Parse ``pmset -g therm`` into a thermal state string.

    Parameters
    ----------
    text : str
        Standard output of ``pmset -g therm``.

    Returns
    -------
    Probed[str]
        A state string with ``thermal_warning_level``,
        ``performance_warning_level`` and either ``cpu_power_status``
        or the ``CPU_*`` limits pmset printed. A level pmset has not
        recorded is ``none``. Other lines, such as the timestamped
        ``CPU Power notify`` header, are left out so the string does
        not change between runs on an unchanged machine.
    """
    items: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        for pattern, key in _THERM_LINES:
            match = pattern.fullmatch(stripped)
            if match is None:
                continue
            if key is None:
                items[match[1]] = match[2]
            else:
                items[key] = match[1] if match.lastindex else "none"
            break
    if not items:
        return Missing("pmset -g therm printed no thermal or CPU status")
    return format_state(items)


_PMSET_POLICY: Final = re.compile(
    r"^\s*(lowpowermode|powermode)\s+(\d+)\b", re.MULTILINE
)


def parse_pmset_settings(text: str) -> Probed[str]:
    """Parse ``pmset -g`` into a frequency policy state string.

    Parameters
    ----------
    text : str
        Standard output of ``pmset -g``.

    Returns
    -------
    Probed[str]
        A state string of the ``lowpowermode`` and ``powermode``
        settings in use, whichever pmset printed.
    """
    _, _, in_use = text.partition("Currently in use:")
    items = dict(_PMSET_POLICY.findall(in_use))
    if not items:
        return Missing("pmset -g reports neither lowpowermode nor powermode")
    return format_state(items)


_MACHINE_ID: Final = re.compile(r"[0-9a-f]{32}")


def parse_machine_id(text: str) -> Probed[str]:
    """Validate the contents of a systemd ``machine-id`` file.

    Parameters
    ----------
    text : str
        Contents of ``/etc/machine-id`` or
        ``/var/lib/dbus/machine-id``.

    Returns
    -------
    Probed[str]
        The 32 lowercase hex digits; `Missing` when the file is empty,
        reads ``uninitialized`` (first boot), or holds anything else.
    """
    value = text.strip()
    if _MACHINE_ID.fullmatch(value):
        return value
    if not value:
        return Missing("machine-id is empty")
    if value == "uninitialized":
        return Missing("machine-id is uninitialized")
    return Missing("machine-id is not 32 lowercase hex digits")


_BOOT_ID: Final = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)


def parse_boot_id(text: str) -> Probed[str]:
    """Validate the contents of the kernel's boot ID file.

    Parameters
    ----------
    text : str
        Contents of ``/proc/sys/kernel/random/boot_id``: a lowercase
        UUID, new on every boot, and a newline.

    Returns
    -------
    Probed[str]
        The UUID; `Missing` when the text is anything else.
    """
    value = text.strip()
    if _BOOT_ID.fullmatch(value):
        return value
    if not value:
        return Missing("boot_id is empty")
    return Missing("boot_id is not a lowercase UUID")


def _cpuinfo_blocks(text: str) -> list[dict[str, str]]:
    """Split ``/proc/cpuinfo`` into one key-value dict per block."""
    blocks: list[dict[str, str]] = [{}]
    for line in text.splitlines():
        if not line.strip():
            if blocks[-1]:
                blocks.append({})
            continue
        key, sep, value = line.partition(":")
        if sep:
            blocks[-1][key.strip()] = value.strip()
    return [block for block in blocks if block]


def parse_cpuinfo(text: str) -> CpuInfo:
    """Parse ``/proc/cpuinfo`` into model and core counts.

    Parameters
    ----------
    text : str
        Contents of ``/proc/cpuinfo``: blocks of ``key : value`` lines
        separated by blank lines, one block per logical CPU.

    Returns
    -------
    CpuInfo
        The model from ``model name`` (absent on arm64 kernels, which
        print only implementer and part numbers), the count of
        ``processor`` blocks, and the count of distinct
        ``(physical id, core id)`` pairs (absent on arm64 and in some
        virtual machines).
    """
    cpus = [b for b in _cpuinfo_blocks(text) if "processor" in b]
    if not cpus:
        missing = Missing("/proc/cpuinfo lists no processor")
        return CpuInfo(missing, missing, missing)
    models = sorted({b["model name"] for b in cpus if b.get("model name")})
    model: Probed[str] = (
        ", ".join(models)
        if models
        else Missing("/proc/cpuinfo has no model name")
    )
    physical: Probed[int]
    if all("physical id" in b and "core id" in b for b in cpus):
        physical = len({(b["physical id"], b["core id"]) for b in cpus})
    else:
        physical = Missing("/proc/cpuinfo has no physical id and core id")
    return CpuInfo(model, len(cpus), physical)


def parse_governors(values: Iterable[str]) -> Probed[str]:
    """Combine per-CPU ``scaling_governor`` values into a policy.

    Parameters
    ----------
    values : Iterable[str]
        Contents of each CPU's
        ``/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor``.

    Returns
    -------
    Probed[str]
        A state string ``scaling_governor=<names>``, with the distinct
        governor names sorted and joined by commas.
    """
    names = sorted({v.strip() for v in values if v.strip()})
    if not names:
        return Missing("no cpufreq scaling_governor in sysfs")
    if not all(_STATE_TOKEN.fullmatch(n) for n in names):
        return Missing("scaling_governor holds an unexpected name")
    return format_state({"scaling_governor": ",".join(names)})


_MAINS_TYPES: Final = frozenset({"Mains", "USB", "USB_C", "USB_PD"})
_EXTERNAL_STATUSES: Final = frozenset({"Charging", "Full", "Not charging"})


def parse_power_supplies(
    supplies: Mapping[str, Mapping[str, str]],
) -> tuple[Probed[PowerSource], Probed[float]]:
    """Derive power source and battery charge from Linux power supplies.

    Parameters
    ----------
    supplies : Mapping[str, Mapping[str, str]]
        For each directory in ``/sys/class/power_supply``, the stripped
        contents of the attribute files it has, keyed by file name
        (``type``, ``online``, ``status``, ``capacity``, ``scope``).
        Supplies with ``scope`` ``Device``, such as a wireless mouse,
        are ignored.

    Returns
    -------
    source : Probed[PowerSource]
        ``"ac"`` when a mains or USB supply is online, or a battery is
        charging, full or not charging; ``"battery"`` when every mains
        supply is offline or a battery is discharging.
    percent : Probed[float]
        ``capacity`` of the one system battery, in percent; `Missing`
        with no battery or with several.
    """
    system = {
        name: attrs
        for name, attrs in supplies.items()
        if attrs.get("scope", "").lower() != "device"
    }
    if not system:
        nothing = Missing("no power supply in /sys/class/power_supply")
        return nothing, nothing
    mains = [a for a in system.values() if a.get("type") in _MAINS_TYPES]
    batteries = [
        system[name]
        for name in sorted(system)
        if system[name].get("type") == "Battery"
    ]
    statuses = {b.get("status") for b in batteries}
    source: Probed[PowerSource]
    if any(m.get("online") == "1" for m in mains):
        source = "ac"
    elif "Discharging" in statuses or (
        batteries and mains and all(m.get("online") == "0" for m in mains)
    ):
        source = "battery"
    elif statuses & _EXTERNAL_STATUSES:
        source = "ac"
    else:
        source = Missing("power supplies show no online mains or status")
    percent: Probed[float]
    if not batteries:
        percent = Missing("no system battery")
    elif len(batteries) > 1:
        percent = Missing("several system batteries")
    else:
        capacity = batteries[0].get("capacity", "")
        if capacity.isdigit() and int(capacity) <= 100:
            percent = float(capacity)
        else:
            percent = Missing("battery capacity is not a percentage")
    return source, percent


def parse_cooling_devices(
    devices: Mapping[str, Mapping[str, str]],
) -> Probed[str]:
    """Derive a thermal state from Linux processor cooling devices.

    Parameters
    ----------
    devices : Mapping[str, Mapping[str, str]]
        For each ``/sys/class/thermal/cooling_device*`` directory, the
        stripped contents of its ``type``, ``cur_state`` and
        ``max_state`` files, keyed by file name.

    Returns
    -------
    Probed[str]
        A state string with ``processor_cooling_state``, the highest
        ``cur_state`` of any ``Processor`` device (0 means no
        throttling), and ``processor_cooling_max_state``, the highest
        ``max_state``.
    """
    current: list[int] = []
    maximum: list[int] = []
    for attrs in devices.values():
        if attrs.get("type") != "Processor":
            continue
        cur, top = attrs.get("cur_state", ""), attrs.get("max_state", "")
        if not (cur.isdigit() and top.isdigit()):
            return Missing("a Processor cooling device has no integer state")
        current.append(int(cur))
        maximum.append(int(top))
    if not current:
        return Missing("no Processor cooling device in /sys/class/thermal")
    return format_state(
        {
            "processor_cooling_state": str(max(current)),
            "processor_cooling_max_state": str(max(maximum)),
        }
    )


NVIDIA_QUERY: Final = ("name", "driver_version", "clocks.sm", "clocks.mem")
"""Fields `parse_nvidia_smi` expects, in ``--query-gpu`` order."""

_NVIDIA_UNREAD: Final = re.compile(r"\[[^\]]*\]")


def _clock(text: str, field: str, missing: dict[str, str]) -> float | None:
    """Read one nvidia-smi clock in MHz, or record why it is unknown."""
    try:
        value = float(text)
    except ValueError:
        missing[field] = f"nvidia-smi reported {text!r}"
        return None
    if not value > 0:
        missing[field] = f"nvidia-smi reported {text!r}"
        return None
    return value


def parse_nvidia_smi(text: str) -> Probed[tuple[GpuInfo, ...]]:
    """Parse ``nvidia-smi --query-gpu`` CSV into GPU records.

    Parameters
    ----------
    text : str
        Standard output of ``nvidia-smi
        --query-gpu=name,driver_version,clocks.sm,clocks.mem
        --format=csv,noheader,nounits``: one comma-separated row per
        GPU. A value the driver cannot read is printed in brackets,
        such as ``[N/A]`` or ``[Not Supported]``.

    Returns
    -------
    Probed[tuple[GpuInfo, ...]]
        One `GpuInfo` per row, in nvidia-smi's order. An unreadable
        clock is ``None`` with the printed text as its reason. A row
        with the wrong number of fields, or without a name or driver
        version, makes the whole result `Missing`.
    """
    rows = [
        row
        for row in csv.reader(text.splitlines(), skipinitialspace=True)
        if row
    ]
    gpus: list[GpuInfo] = []
    for index, row in enumerate(rows):
        fields = [field.strip() for field in row]
        if len(fields) != len(NVIDIA_QUERY):
            return Missing(
                f"nvidia-smi row {index} has {len(fields)} fields, "
                f"expected {len(NVIDIA_QUERY)}"
            )
        name, driver, sm, mem = fields
        for label, value in (("name", name), ("driver version", driver)):
            if not value or _NVIDIA_UNREAD.fullmatch(value):
                return Missing(f"nvidia-smi gave GPU {index} no {label}")
        missing: dict[str, str] = {}
        gpus.append(
            GpuInfo.model_validate(
                {
                    "name": name,
                    "driver_version": driver,
                    "sm_clock_mhz": _clock(sm, "sm_clock_mhz", missing),
                    "memory_clock_mhz": _clock(
                        mem, "memory_clock_mhz", missing
                    ),
                    "missing": missing,
                }
            )
        )
    return tuple(gpus)
