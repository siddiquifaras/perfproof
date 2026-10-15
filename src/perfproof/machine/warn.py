"""Warnings about machine state that adds noise to a comparison.

The warnings are a pure function of a `MachineFingerprint`, so the
same fingerprint always yields the same warnings, including one read
back from a record. They never change a verdict. A probe that is
unknown raises no warning; its reason is already in ``missing``.
"""

from typing import Final

from perfproof.machine.parsers import parse_state
from perfproof.schema import MachineFingerprint, RunWarning

__all__ = ["HIGH_LOAD_FRACTION", "machine_warnings"]

HIGH_LOAD_FRACTION: Final = 0.5
"""Load per logical CPU above which the machine counts as busy.

A 1-minute load average above this fraction of the logical CPUs means
other work competes with the benchmark for a large share of the
machine. It is a judgment threshold, not a measured one.
"""

_LIMIT_KEYS: Final = ("CPU_Speed_Limit", "CPU_Scheduler_Limit")
_LEVEL_KEYS: Final = ("thermal_warning_level", "performance_warning_level")


def _load(fp: MachineFingerprint) -> RunWarning | None:
    """Warn when the 1-minute load is high for the CPU count."""
    if fp.load_average is None or fp.logical_cores is None:
        return None
    one_minute = fp.load_average[0]
    if one_minute <= HIGH_LOAD_FRACTION * fp.logical_cores:
        return None
    return RunWarning(
        code="high_load",
        message=(
            f"1-minute load average is {one_minute:.2f} on "
            f"{fp.logical_cores} logical CPUs, above "
            f"{HIGH_LOAD_FRACTION:g} per CPU; other work on the machine "
            "adds noise to every round."
        ),
    )


def _policy(fp: MachineFingerprint) -> RunWarning | None:
    """Warn on a power-saving CPU frequency policy."""
    if fp.frequency_policy is None:
        return None
    items = parse_state(fp.frequency_policy)
    governors = items.get("scaling_governor", "").split(",")
    slow = sorted(g for g in governors if g and g != "performance")
    if slow:
        return RunWarning(
            code="non_performance_governor",
            message=(
                f"CPU frequency governor is {', '.join(slow)}, not "
                "performance; clock speed can change during the run."
            ),
        )
    if items.get("lowpowermode", "0") != "0":
        return RunWarning(
            code="low_power_mode",
            message=("macOS Low Power Mode is on; it lowers CPU clock speed."),
        )
    return None


def _thermal(fp: MachineFingerprint) -> RunWarning | None:
    """Warn when the OS reports thermal pressure or throttling."""
    if fp.thermal_state is None:
        return None
    items = parse_state(fp.thermal_state)
    signs = [
        f"{key}={items[key]}"
        for key in _LEVEL_KEYS
        if items.get(key, "none") not in ("none", "0")
    ]
    signs += [
        f"{key}={items[key]}"
        for key in _LIMIT_KEYS
        if items.get(key, "").isdigit() and int(items[key]) < 100
    ]
    if items.get("processor_cooling_state", "0") != "0":
        signs.append(
            f"processor_cooling_state={items['processor_cooling_state']}"
        )
    if not signs:
        return None
    return RunWarning(
        code="thermal_limit",
        message=(
            f"The OS reports thermal limits ({', '.join(signs)}); "
            "the CPU may be throttled."
        ),
    )


def machine_warnings(fp: MachineFingerprint) -> tuple[RunWarning, ...]:
    """List the noise warnings a fingerprint calls for.

    Parameters
    ----------
    fp : MachineFingerprint
        A fingerprint from `perfproof.machine.fingerprint`.

    Returns
    -------
    tuple[RunWarning, ...]
        In this order, each at most once:

        - ``on_battery``: the power source is a battery.
        - ``high_load``: the 1-minute load average exceeds
          `HIGH_LOAD_FRACTION` times the logical CPUs.
        - ``non_performance_governor``: a Linux CPU uses a cpufreq
          governor other than ``performance``; or ``low_power_mode``:
          macOS Low Power Mode is on.
        - ``thermal_limit``: macOS reports a thermal or performance
          warning level other than 0, or a ``CPU_Speed_Limit`` or
          ``CPU_Scheduler_Limit`` under 100; or a Linux processor
          cooling device is above state 0.
    """
    found: list[RunWarning] = []
    if fp.power_source == "battery":
        percent = (
            ""
            if fp.battery_percent is None
            else f" at {fp.battery_percent:g}%"
        )
        found.append(
            RunWarning(
                code="on_battery",
                message=(
                    f"Running on battery power{percent}; the OS may "
                    "lower CPU clock speed to save power."
                ),
            )
        )
    for check in (_load, _policy, _thermal):
        warning = check(fp)
        if warning is not None:
            found.append(warning)
    return tuple(found)
