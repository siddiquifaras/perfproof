"""Parser tests.

Tests named ``*_captured`` read files under ``tests/fixtures/machine``,
captured from real commands (see ``SOURCE.md`` there). Tests named
``*_constructed`` use samples written by hand from a documented format
because no real output could be captured on the development machine;
they show the parser follows that reading of the format, not that real
output matches it.
"""

import re
from pathlib import Path

import pytest

from perfproof.machine.parsers import (
    CpuInfo,
    Missing,
    format_state,
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
    parse_state,
    parse_sysctl,
)
from perfproof.schema import GpuInfo

FIXTURES = Path(__file__).parent.parent / "fixtures" / "machine"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# Captured on macOS 15.7.7, Apple M4.


def test_pmset_batt_captured() -> None:
    assert parse_pmset_batt(fixture("pmset_g_batt.txt")) == ("ac", 94.0)


def test_pmset_therm_captured() -> None:
    state = parse_pmset_therm(fixture("pmset_g_therm.txt"))
    assert state == (
        "cpu_power_status=none; performance_warning_level=none; "
        "thermal_warning_level=none"
    )


def test_pmset_settings_captured() -> None:
    assert parse_pmset_settings(fixture("pmset_g.txt")) == "lowpowermode=0"


def test_sysctl_captured() -> None:
    assert parse_sysctl(fixture("sysctl.txt")) == {
        "machdep.cpu.brand_string": "Apple M4",
        "hw.physicalcpu": "10",
        "hw.logicalcpu": "10",
        "hw.memsize": "17179869184",
    }


def test_sysctl_unknown_key_captured() -> None:
    stdout = fixture("sysctl_unknown_key.stdout.txt")
    assert parse_sysctl(stdout) == {
        "machdep.cpu.brand_string": "Apple M4",
        "hw.memsize": "17179869184",
    }
    assert "kern.nope" in fixture("sysctl_unknown_key.stderr.txt")


# pmset outputs not seen on the development machine. Built from the
# printf format strings in /usr/bin/pmset (the "Now drawing from" line,
# the battery line's "discharging" and "remaining" parts, and the
# thermal and performance warning level lines) and the CPU_* key names
# in IOKit's IOPM.h. The spacing of the CPU_* lines is a guess the
# parser is written to tolerate.


def test_pmset_batt_on_battery_constructed() -> None:
    text = (
        "Now drawing from 'Battery Power'\n"
        " -InternalBattery-0 (id=1234567)\t57%; discharging; "
        "3:10 remaining present: true\n"
    )
    assert parse_pmset_batt(text) == ("battery", 57.0)


def test_pmset_batt_without_battery_constructed() -> None:
    source, percent = parse_pmset_batt("Now drawing from 'AC Power'\n")
    assert source == "ac"
    assert percent == Missing("pmset -g batt lists no internal battery")


def test_pmset_batt_ups_constructed() -> None:
    source, _ = parse_pmset_batt("Now drawing from 'UPS Power'\n")
    assert source == "ups"


def test_pmset_batt_unknown_source_constructed() -> None:
    source, _ = parse_pmset_batt("Now drawing from 'Solar'\n")
    assert source == Missing("pmset -g batt reported unknown 'Solar'")


def test_pmset_batt_garbage() -> None:
    source, percent = parse_pmset_batt("unexpected\n")
    assert isinstance(source, Missing)
    assert isinstance(percent, Missing)


def test_pmset_therm_levels_constructed() -> None:
    text = (
        "Thermal Warning Level = 5\n"
        "Performance Warning Level = 0\n"
        "2026-10-15 12:00:00 +0000 CPU Power notify\n"
        "\tCPU_Scheduler_Limit \t= 100\n"
        "\tCPU_Available_CPUs \t= 10\n"
        "\tCPU_Speed_Limit \t= 80\n"
    )
    state = parse_pmset_therm(text)
    assert isinstance(state, str)
    assert parse_state(state) == {
        "thermal_warning_level": "5",
        "performance_warning_level": "0",
        "CPU_Scheduler_Limit": "100",
        "CPU_Available_CPUs": "10",
        "CPU_Speed_Limit": "80",
    }
    assert "12:00" not in state


def test_pmset_therm_garbage() -> None:
    assert isinstance(parse_pmset_therm("nothing useful\n"), Missing)


def test_pmset_settings_powermode_constructed() -> None:
    text = (
        "Currently in use:\n lowpowermode         0\n powermode            2\n"
    )
    assert parse_pmset_settings(text) == "lowpowermode=0; powermode=2"


def test_pmset_settings_ignores_lines_before_in_use() -> None:
    text = "lowpowermode 1\nCurrently in use:\n sleep 1\n"
    assert isinstance(parse_pmset_settings(text), Missing)


def test_state_round_trip() -> None:
    items = {"b": "2", "a": "x,y"}
    text = format_state(items)
    assert text == "a=x,y; b=2"
    assert parse_state(text) == items


@pytest.mark.parametrize(
    "items", [{}, {"a": ""}, {"a b": "1"}, {"a": "1; b=2"}, {"a": "x=y"}]
)
def test_format_state_refuses_ambiguous_items(items: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="state string"):
        format_state(items)


def test_parse_state_skips_non_state_text() -> None:
    assert parse_state("performance") == {}


# systemd machine-id(5): 32 lowercase hex digits and a newline, or
# "uninitialized" during first boot. Constructed values.


def test_machine_id_constructed() -> None:
    value = "0123456789abcdef0123456789abcdef"
    assert parse_machine_id(value + "\n") == value


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "machine-id is empty"),
        ("uninitialized\n", "machine-id is uninitialized"),
        (
            "0123456789ABCDEF0123456789ABCDEF\n",
            "machine-id is not 32 lowercase hex digits",
        ),
    ],
)
def test_machine_id_invalid(text: str, reason: str) -> None:
    assert parse_machine_id(text) == Missing(reason)


# x86 /proc/cpuinfo, constructed from the layout documented in proc(5)
# and the field names x86 kernels print. Not captured: that needs a
# Linux x86 host.

CPUINFO_X86_CONSTRUCTED = """\
processor\t: 0
vendor_id\t: GenuineIntel
model name\t: Example CPU @ 2.00GHz
physical id\t: 0
core id\t\t: 0
cpu cores\t: 1

processor\t: 1
vendor_id\t: GenuineIntel
model name\t: Example CPU @ 2.00GHz
physical id\t: 0
core id\t\t: 0
cpu cores\t: 1

"""


def test_cpuinfo_x86_constructed() -> None:
    assert parse_cpuinfo(CPUINFO_X86_CONSTRUCTED) == CpuInfo(
        "Example CPU @ 2.00GHz", 2, 1
    )


# Captured in a linux/arm64 container on Docker Desktop's VM (Linux
# 6.12.54-linuxkit, aarch64); see SOURCE.md.


def test_cpuinfo_arm64_captured() -> None:
    info = parse_cpuinfo(fixture("linux_arm64_proc_cpuinfo.txt"))
    cpus = [
        name
        for name in fixture(
            "linux_arm64_ls_sys_devices_system_cpu.txt"
        ).split()
        if re.fullmatch(r"cpu\d+", name)
    ]
    assert info.logical_cores == len(cpus) == 10
    assert info.model == Missing("/proc/cpuinfo has no model name")
    assert info.physical_cores == Missing(
        "/proc/cpuinfo has no physical id and core id"
    )


def test_boot_id_captured() -> None:
    first = parse_boot_id(fixture("linux_arm64_boot_id_container_1.txt"))
    second = parse_boot_id(fixture("linux_arm64_boot_id_container_2.txt"))
    assert isinstance(first, str)
    assert first == second


def test_machine_id_absent_in_container_captured() -> None:
    for name in ("etc", "dbus"):
        stderr = fixture(f"linux_arm64_{name}_machine_id.stderr.txt")
        assert "No such file or directory" in stderr


def test_no_cpufreq_in_container_captured() -> None:
    stderr = fixture("linux_arm64_scaling_governor.stderr.txt")
    assert "No such file or directory" in stderr
    listing = fixture("linux_arm64_ls_sys_devices_system_cpu.txt").split()
    assert "cpufreq" not in listing


def test_no_power_supply_or_thermal_in_container_captured() -> None:
    assert fixture("linux_arm64_ls_power_supply.txt") == ""
    assert "No such file or directory" in fixture("linux_arm64_ls_thermal.txt")
    nothing = Missing("no power supply in /sys/class/power_supply")
    assert parse_power_supplies({}) == (nothing, nothing)
    assert parse_cooling_devices({}) == Missing(
        "no Processor cooling device in /sys/class/thermal"
    )


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "boot_id is empty"),
        ("not-a-uuid\n", "boot_id is not a lowercase UUID"),
        (
            "0000000A-0000-4000-8000-000000000000\n",
            "boot_id is not a lowercase UUID",
        ),
    ],
)
def test_boot_id_invalid(text: str, reason: str) -> None:
    assert parse_boot_id(text) == Missing(reason)


def test_cpuinfo_empty() -> None:
    missing = Missing("/proc/cpuinfo lists no processor")
    assert parse_cpuinfo("") == CpuInfo(missing, missing, missing)


# cpufreq scaling_governor holds one governor name and a newline
# (kernel admin-guide/pm/cpufreq). Constructed values.


def test_governors_constructed() -> None:
    assert (
        parse_governors(["performance\n", "performance\n"])
        == "scaling_governor=performance"
    )
    assert (
        parse_governors(["schedutil\n", "performance\n", "schedutil\n"])
        == "scaling_governor=performance,schedutil"
    )


def test_governors_none() -> None:
    assert parse_governors([]) == Missing(
        "no cpufreq scaling_governor in sysfs"
    )


# /sys/class/power_supply attributes as documented in the kernel's
# ABI sysfs-class-power. Constructed values.


def test_power_mains_online_constructed() -> None:
    supplies = {
        "AC": {"type": "Mains", "online": "1"},
        "BAT0": {"type": "Battery", "status": "Charging", "capacity": "80"},
    }
    assert parse_power_supplies(supplies) == ("ac", 80.0)


def test_power_mains_offline_constructed() -> None:
    supplies = {
        "AC": {"type": "Mains", "online": "0"},
        "BAT0": {"type": "Battery", "status": "Unknown", "capacity": "41"},
    }
    assert parse_power_supplies(supplies) == ("battery", 41.0)


def test_power_battery_discharging_constructed() -> None:
    supplies = {
        "BAT0": {"type": "Battery", "status": "Discharging", "capacity": "9"}
    }
    assert parse_power_supplies(supplies) == ("battery", 9.0)


def test_power_battery_full_constructed() -> None:
    supplies = {
        "BAT0": {"type": "Battery", "status": "Full", "capacity": "100"}
    }
    assert parse_power_supplies(supplies) == ("ac", 100.0)


def test_power_ignores_device_scope_constructed() -> None:
    supplies = {
        "AC": {"type": "Mains", "online": "1"},
        "hid-mouse": {
            "type": "Battery",
            "scope": "Device",
            "status": "Discharging",
            "capacity": "5",
        },
    }
    assert parse_power_supplies(supplies) == (
        "ac",
        Missing("no system battery"),
    )


def test_power_two_batteries_constructed() -> None:
    supplies = {
        "BAT0": {"type": "Battery", "status": "Discharging", "capacity": "50"},
        "BAT1": {"type": "Battery", "status": "Discharging", "capacity": "70"},
    }
    assert parse_power_supplies(supplies) == (
        "battery",
        Missing("several system batteries"),
    )


def test_power_none() -> None:
    nothing = Missing("no power supply in /sys/class/power_supply")
    assert parse_power_supplies({}) == (nothing, nothing)


def test_power_unknown_status_constructed() -> None:
    supplies = {"BAT0": {"type": "Battery", "status": "Unknown"}}
    source, percent = parse_power_supplies(supplies)
    assert isinstance(source, Missing)
    assert percent == Missing("battery capacity is not a percentage")


# /sys/class/thermal/cooling_device* attributes as documented in the
# kernel's driver-api/thermal/sysfs-api. Constructed values.


def test_cooling_idle_constructed() -> None:
    devices = {
        "cooling_device0": {
            "type": "Processor",
            "cur_state": "0",
            "max_state": "3",
        },
        "cooling_device1": {"type": "Fan", "cur_state": "1", "max_state": "1"},
    }
    assert parse_cooling_devices(devices) == (
        "processor_cooling_max_state=3; processor_cooling_state=0"
    )


def test_cooling_throttled_constructed() -> None:
    devices = {
        f"cooling_device{i}": {
            "type": "Processor",
            "cur_state": str(cur),
            "max_state": "3",
        }
        for i, cur in enumerate((0, 2))
    }
    state = parse_cooling_devices(devices)
    assert isinstance(state, str)
    assert parse_state(state)["processor_cooling_state"] == "2"


def test_cooling_none() -> None:
    assert isinstance(parse_cooling_devices({}), Missing)


def test_cooling_bad_state_constructed() -> None:
    devices = {"cooling_device0": {"type": "Processor", "cur_state": "x"}}
    assert parse_cooling_devices(devices) == Missing(
        "a Processor cooling device has no integer state"
    )


# nvidia-smi --query-gpu=name,driver_version,clocks.sm,clocks.mem
# --format=csv,noheader,nounits, as documented in nvidia-smi(1): one
# comma-separated row per GPU, unreadable values in brackets.
# Constructed rows; no NVIDIA GPU was available to capture from.


def test_nvidia_two_gpus_constructed() -> None:
    text = (
        "Example GPU A, 999.99.99, 1410, 1215\n"
        "Example GPU B, 999.99.99, [N/A], [Not Supported]\n"
    )
    assert parse_nvidia_smi(text) == (
        GpuInfo(
            name="Example GPU A",
            driver_version="999.99.99",
            sm_clock_mhz=1410.0,
            memory_clock_mhz=1215.0,
        ),
        GpuInfo.model_validate(
            {
                "name": "Example GPU B",
                "driver_version": "999.99.99",
                "missing": {
                    "sm_clock_mhz": "nvidia-smi reported '[N/A]'",
                    "memory_clock_mhz": (
                        "nvidia-smi reported '[Not Supported]'"
                    ),
                },
            }
        ),
    )


def test_nvidia_zero_clock_constructed() -> None:
    gpus = parse_nvidia_smi("Example GPU, 999.99.99, 0, 1215\n")
    assert isinstance(gpus, tuple)
    assert gpus[0].sm_clock_mhz is None
    assert gpus[0].missing["sm_clock_mhz"] == "nvidia-smi reported '0'"


def test_nvidia_no_rows() -> None:
    assert parse_nvidia_smi("") == ()


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (
            "Example GPU, 999.99.99, 1410\n",
            "nvidia-smi row 0 has 3 fields, expected 4",
        ),
        ("[N/A], 999.99.99, 1410, 1215\n", "nvidia-smi gave GPU 0 no name"),
        (
            "Example GPU, [N/A], 1410, 1215\n",
            "nvidia-smi gave GPU 0 no driver version",
        ),
    ],
)
def test_nvidia_unusable_constructed(text: str, reason: str) -> None:
    assert parse_nvidia_smi(text) == Missing(reason)
