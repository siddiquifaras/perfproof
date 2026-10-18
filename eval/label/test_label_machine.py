"""Machine description, perf output and the quiet check."""

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

import machine

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_perf_not_supported_is_no_count() -> None:
    text = (FIXTURES / "perf-stat-not-supported.csv").read_text()
    assert machine.parse_perf_csv(text) == (None, "<not supported>")


def test_perf_output_without_the_event_is_no_count() -> None:
    # What perf writes when it is refused permission: the header only.
    header = (FIXTURES / "perf-stat-not-supported.csv").read_text()
    first = header.splitlines()[0]
    count, reason = machine.parse_perf_csv(first + "\n")
    assert count is None
    assert reason == "no instructions:u line in perf output"


@pytest.mark.skipif(
    sys.platform.startswith("linux"), reason="checks the non-Linux path"
)
def test_perf_probe_off_linux() -> None:
    probe = machine.probe_perf()
    assert not probe.works
    assert probe.reason.startswith("not Linux")


def test_describe_reads_this_machine() -> None:
    got = machine.describe("here").to_json()
    assert got["name"] == "here"
    assert got["logical_cpus"] is not None
    assert got["arch"]
    assert got["kernel"]
    assert set(got["perf"]) == {"works", "reason", "version"}


def _loads(*values: float | None) -> Callable[[], float | None]:
    it = iter(values)
    return lambda: next(it)


def test_quiet_passes_at_once_when_idle() -> None:
    slept: list[float] = []
    got = machine.quiet_check(
        wait_s=60, poll_s=30, read_load=_loads(0.0), sleep=slept.append
    )
    assert got.passed
    assert slept == []
    assert got.samples == [(0.0, 0.0)]


def test_quiet_waits_for_the_load_to_fall() -> None:
    slept: list[float] = []
    high = 10.0 * (machine.os.cpu_count() or 1)
    got = machine.quiet_check(
        wait_s=60,
        poll_s=30,
        read_load=_loads(high, high, 0.0),
        sleep=slept.append,
    )
    assert got.passed
    assert slept == [30, 30]
    assert [t for t, _ in got.samples] == [0.0, 30.0, 60.0]


def test_quiet_refuses_after_the_wait() -> None:
    high = 10.0 * (machine.os.cpu_count() or 1)
    got = machine.quiet_check(
        wait_s=60,
        poll_s=30,
        read_load=_loads(high, high, high, high),
        sleep=lambda _: None,
    )
    assert not got.passed
    assert "stayed above" in got.reason
    assert len(got.samples) == 3


def test_quiet_refuses_without_a_load_average() -> None:
    got = machine.quiet_check(read_load=_loads(None), sleep=lambda _: None)
    assert not got.passed
    assert got.reason == "the load average can't be read"
