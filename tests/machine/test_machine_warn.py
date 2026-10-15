from datetime import UTC, datetime

import pytest

from perfproof.machine import HIGH_LOAD_FRACTION, machine_warnings
from perfproof.schema import MachineFingerprint

T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)

QUIET: dict[str, object] = {
    "host_id": "a" * 64,
    "os": "Linux",
    "logical_cores": 8,
    "load_average": (0.5, 0.5, 0.5),
    "power_source": "ac",
    "battery_percent": 100.0,
    "frequency_policy": "scaling_governor=performance",
    "thermal_state": (
        "processor_cooling_max_state=3; processor_cooling_state=0"
    ),
}


def make(**values: object) -> MachineFingerprint:
    fields = set(MachineFingerprint.model_fields) - {"captured_at", "missing"}
    merged = {**QUIET, **values}
    present = {k: v for k, v in merged.items() if v is not None}
    missing = dict.fromkeys(fields - set(present), "not set in this test")
    return MachineFingerprint.model_validate(
        {"captured_at": T0, **present, "missing": missing}
    )


def codes(fp: MachineFingerprint) -> list[str]:
    return [w.code for w in machine_warnings(fp)]


def test_quiet_machine_has_no_warnings() -> None:
    assert machine_warnings(make()) == ()


def test_unknown_probes_raise_no_warnings() -> None:
    unknown = dict.fromkeys(QUIET)
    assert machine_warnings(make(**unknown)) == ()


def test_on_battery() -> None:
    (warning,) = machine_warnings(
        make(power_source="battery", battery_percent=57.0)
    )
    assert warning.code == "on_battery"
    assert "57%" in warning.message


def test_on_battery_with_unknown_charge() -> None:
    assert codes(make(power_source="battery", battery_percent=None)) == [
        "on_battery"
    ]


def test_ups_power_is_not_battery() -> None:
    assert codes(make(power_source="ups", battery_percent=None)) == []


@pytest.mark.parametrize(
    ("load", "warned"),
    [
        (HIGH_LOAD_FRACTION * 8, False),
        (HIGH_LOAD_FRACTION * 8 + 0.01, True),
    ],
)
def test_high_load_boundary(load: float, warned: bool) -> None:
    fp = make(load_average=(load, 0.0, 0.0))
    assert codes(fp) == (["high_load"] if warned else [])


def test_high_load_uses_one_minute_average() -> None:
    assert codes(make(load_average=(0.0, 50.0, 50.0))) == []


def test_high_load_needs_core_count() -> None:
    fp = make(load_average=(50.0, 50.0, 50.0), logical_cores=None)
    assert codes(fp) == []


@pytest.mark.parametrize(
    "policy",
    ["scaling_governor=powersave", "scaling_governor=performance,schedutil"],
)
def test_non_performance_governor(policy: str) -> None:
    (warning,) = machine_warnings(make(frequency_policy=policy))
    assert warning.code == "non_performance_governor"
    assert "performance" in warning.message


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("lowpowermode=1", ["low_power_mode"]),
        ("lowpowermode=0", []),
        ("lowpowermode=0; powermode=2", []),
    ],
)
def test_macos_low_power_mode(policy: str, expected: list[str]) -> None:
    assert codes(make(os="Darwin", frequency_policy=policy)) == expected


@pytest.mark.parametrize(
    ("state", "warned"),
    [
        (
            "cpu_power_status=none; performance_warning_level=none; "
            "thermal_warning_level=none",
            False,
        ),
        (
            "CPU_Available_CPUs=10; CPU_Scheduler_Limit=100; "
            "CPU_Speed_Limit=100; performance_warning_level=0; "
            "thermal_warning_level=0",
            False,
        ),
        ("CPU_Speed_Limit=80; thermal_warning_level=0", True),
        ("CPU_Scheduler_Limit=50", True),
        ("thermal_warning_level=5", True),
        ("performance_warning_level=1", True),
        ("CPU_Available_CPUs=4", False),
        ("processor_cooling_max_state=3; processor_cooling_state=2", True),
    ],
)
def test_thermal_limit(state: str, warned: bool) -> None:
    assert codes(make(thermal_state=state)) == (
        ["thermal_limit"] if warned else []
    )


def test_warning_order() -> None:
    fp = make(
        power_source="battery",
        load_average=(100.0, 0.0, 0.0),
        frequency_policy="scaling_governor=powersave",
        thermal_state="processor_cooling_state=1",
    )
    assert codes(fp) == [
        "on_battery",
        "high_load",
        "non_performance_governor",
        "thermal_limit",
    ]
