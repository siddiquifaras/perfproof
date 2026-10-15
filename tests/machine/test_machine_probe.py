"""Probe tests that run real processes on the machine running pytest.

These are not marked ``integration``: they need only what the OS ships
(``sysctl``, ``pmset``, ``ioreg`` on macOS; ``/proc`` and ``/sys`` on
Linux), so CI runs them on both its Linux and macOS runners.
"""

import os
import platform
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from perfproof.machine import fingerprint
from perfproof.machine.parsers import NVIDIA_QUERY
from perfproof.machine.probe import (
    ProbeError,
    host_id,
    linux_host_id,
    run_tool,
)
from perfproof.schema import GpuInfo, MachineFingerprint

SYSTEM = platform.system()
darwin_only = pytest.mark.skipif(
    SYSTEM != "Darwin", reason="exercises the macOS probes"
)
linux_only = pytest.mark.skipif(
    SYSTEM != "Linux", reason="exercises the Linux probes"
)

ALWAYS_KNOWN = (
    "host_id",
    "os",
    "os_version",
    "kernel",
    "arch",
    "cpu_model",
    "physical_cores",
    "logical_cores",
    "load_average",
    "memory_bytes",
    "python_version",
    "gpus",
)


def script(directory: Path, name: str, body: str) -> Path:
    path = directory / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_fingerprint_of_this_machine() -> None:
    fp = fingerprint()
    assert MachineFingerprint.model_validate_json(fp.model_dump_json()) == fp
    assert fp.captured_at.tzinfo is not None
    assert fp.os == SYSTEM
    assert fp.kernel == platform.release()
    assert fp.arch == platform.machine()
    assert fp.python_version == platform.python_version()
    if SYSTEM in ("Darwin", "Linux"):
        unknown = {f: fp.missing[f] for f in ALWAYS_KNOWN if f in fp.missing}
        assert unknown == {}
    assert fp.logical_cores == os.cpu_count()
    if shutil.which("nvidia-smi") is None:
        assert fp.gpus == ()


def test_host_id_is_stable_across_runs() -> None:
    first, second = fingerprint(), fingerprint()
    assert first.host_id is not None
    assert re.fullmatch(r"[0-9a-f]{64}", first.host_id)
    assert first.host_id == second.host_id


@darwin_only
def test_darwin_host_id_matches_ioreg() -> None:
    out = subprocess.run(
        ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    ).stdout
    match = re.search(r'"IOPlatformUUID" = "([0-9A-F-]{36})"', out)
    assert match is not None
    assert fingerprint().host_id == host_id("Darwin", match[1])


@darwin_only
def test_darwin_memory_matches_sysconf() -> None:
    pages = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    assert fingerprint().memory_bytes == pages


@linux_only
def test_linux_host_id_matches_machine_and_boot_id() -> None:
    machine = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8")
    assert fingerprint().host_id == host_id("Linux", machine, boot.strip())


MACHINE_ID = "0123456789abcdef0123456789abcdef"
BOOT_A = "00000000-0000-4000-8000-00000000000a"
BOOT_B = "00000000-0000-4000-8000-00000000000b"


def linux_ids(directory: Path, machine: str, boot: str) -> tuple[Path, Path]:
    directory.mkdir()
    machine_path, boot_path = directory / "machine-id", directory / "boot_id"
    machine_path.write_text(machine + "\n", encoding="utf-8")
    boot_path.write_text(boot + "\n", encoding="utf-8")
    return machine_path, boot_path


def test_cloned_machine_id_with_other_boot_id_is_another_host(
    tmp_path: Path,
) -> None:
    m_a, b_a = linux_ids(tmp_path / "a", MACHINE_ID, BOOT_A)
    m_b, b_b = linux_ids(tmp_path / "b", MACHINE_ID, BOOT_B)
    assert linux_host_id([m_a], b_a) != linux_host_id([m_b], b_b)


def test_same_machine_id_and_boot_id_is_the_same_host(
    tmp_path: Path,
) -> None:
    m_a, b_a = linux_ids(tmp_path / "a", MACHINE_ID, BOOT_A)
    m_b, b_b = linux_ids(tmp_path / "b", MACHINE_ID, BOOT_A)
    assert linux_host_id([m_a], b_a) == linux_host_id([m_b], b_b)
    assert linux_host_id([m_a], b_a) == host_id("Linux", MACHINE_ID, BOOT_A)


def test_unreadable_boot_id_leaves_no_host_id(tmp_path: Path) -> None:
    machine, _ = linux_ids(tmp_path / "a", MACHINE_ID, BOOT_A)
    with pytest.raises(ProbeError, match=r"cannot read .*absent"):
        linux_host_id([machine], tmp_path / "absent")


def test_malformed_boot_id_leaves_no_host_id(tmp_path: Path) -> None:
    machine, boot = linux_ids(tmp_path / "a", MACHINE_ID, "not-a-uuid")
    with pytest.raises(ProbeError, match="boot_id is not a lowercase UUID"):
        linux_host_id([machine], boot)


def boot_only(directory: Path, boot: str) -> tuple[list[Path], Path]:
    directory.mkdir()
    boot_path = directory / "boot_id"
    boot_path.write_text(boot + "\n", encoding="utf-8")
    absent = [directory / "etc-machine-id", directory / "dbus-machine-id"]
    return absent, boot_path


def test_no_machine_id_uses_boot_id_alone(tmp_path: Path) -> None:
    absent, boot = boot_only(tmp_path / "a", BOOT_A)
    assert linux_host_id(absent, boot) == host_id("Linux", BOOT_A)


def test_no_machine_id_different_boot_ids_differ(tmp_path: Path) -> None:
    absent_a, boot_a = boot_only(tmp_path / "a", BOOT_A)
    absent_b, boot_b = boot_only(tmp_path / "b", BOOT_B)
    assert linux_host_id(absent_a, boot_a) != linux_host_id(absent_b, boot_b)


def test_no_machine_id_same_boot_id_matches(tmp_path: Path) -> None:
    absent_a, boot_a = boot_only(tmp_path / "a", BOOT_A)
    absent_b, boot_b = boot_only(tmp_path / "b", BOOT_A)
    assert linux_host_id(absent_a, boot_a) == linux_host_id(absent_b, boot_b)


def test_no_machine_id_and_unreadable_boot_id(tmp_path: Path) -> None:
    absent, _ = boot_only(tmp_path / "a", BOOT_A)
    with pytest.raises(ProbeError, match=r"cannot read .*absent-boot"):
        linux_host_id(absent, tmp_path / "absent-boot")


def test_invalid_machine_id_does_not_fall_back(tmp_path: Path) -> None:
    machine, boot = linux_ids(tmp_path / "a", "uninitialized", BOOT_A)
    with pytest.raises(ProbeError, match="machine-id is uninitialized"):
        linux_host_id([machine], boot)


def test_machine_id_fallback_path(tmp_path: Path) -> None:
    machine, boot = linux_ids(tmp_path / "a", MACHINE_ID, BOOT_A)
    assert linux_host_id([tmp_path / "absent", machine], boot) == host_id(
        "Linux", MACHINE_ID, BOOT_A
    )


@darwin_only
def test_darwin_missing_tools_leave_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    fp = fingerprint()
    for field in ("cpu_model", "physical_cores", "memory_bytes"):
        assert fp.missing[field] == "sysctl not found"
    for field in ("power_source", "thermal_state", "frequency_policy"):
        assert fp.missing[field] == "pmset not found"
    assert fp.host_id is not None
    assert fp.gpus == ()


@darwin_only
def test_darwin_hung_tool_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script(tmp_path, "pmset", "exec sleep 30\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    start = time.monotonic()
    fp = fingerprint(timeout_seconds=0.3)
    assert time.monotonic() - start < 10
    assert fp.missing["thermal_state"] == "pmset timed out after 0.3 s"
    assert fp.cpu_model is not None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_run_tool_kills_the_process_group(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    tool = script(
        tmp_path, "hang", f"sleep 30 &\necho $! > '{pid_file}'\nsleep 30\n"
    )
    start = time.monotonic()
    with pytest.raises(ProbeError, match=r"timed out after 2 s"):
        run_tool([str(tool)], 2.0)
    assert time.monotonic() - start < 10
    child = int(pid_file.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 5
    while alive(child) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not alive(child)


def test_run_tool_not_found(tmp_path: Path) -> None:
    with pytest.raises(ProbeError, match="not found"):
        run_tool([str(tmp_path / "absent")], 1.0)


def test_run_tool_exit_without_output(tmp_path: Path) -> None:
    tool = script(tmp_path, "fails", "echo broken >&2\nexit 3\n")
    with pytest.raises(ProbeError, match=r"exited 3: broken$"):
        run_tool([str(tool)], 5.0)


def test_run_tool_partial_failure(tmp_path: Path) -> None:
    tool = script(tmp_path, "partial", "echo 'a: 1'\nexit 1\n")
    assert run_tool([str(tool)], 5.0, partial_ok=True) == "a: 1\n"
    with pytest.raises(ProbeError, match=r"exited 1: a: 1$"):
        run_tool([str(tool)], 5.0)


def test_run_tool_sets_c_locale(tmp_path: Path) -> None:
    tool = script(tmp_path, "locale", 'printf %s "$LC_ALL"\n')
    assert run_tool([str(tool)], 5.0) == "C"


# The nvidia-smi on PATH here is a stub printing a constructed row in
# the documented CSV format; it tests the plumbing, not real output.


def test_gpus_from_nvidia_smi_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    query = "--query-gpu=" + ",".join(NVIDIA_QUERY)
    script(
        tmp_path,
        "nvidia-smi",
        f'[ "$1" = "{query}" ] || exit 2\n'
        '[ "$2" = "--format=csv,noheader,nounits" ] || exit 2\n'
        'echo "Example GPU, 999.99.99, 1410, 1215"\n',
    )
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert fingerprint().gpus == (
        GpuInfo(
            name="Example GPU",
            driver_version="999.99.99",
            sm_clock_mhz=1410.0,
            memory_clock_mhz=1215.0,
        ),
    )


def test_failing_nvidia_smi_stub_leaves_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script(tmp_path, "nvidia-smi", "echo 'driver gone'\nexit 9\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    fp = fingerprint()
    assert fp.gpus is None
    assert fp.missing["gpus"] == "nvidia-smi exited 9: driver gone"


def test_container_capture_gets_boot_id_host(tmp_path: Path) -> None:
    fixtures = Path(__file__).parent.parent / "fixtures" / "machine"
    boots = [
        fixtures / f"linux_arm64_boot_id_container_{n}.txt" for n in (1, 2)
    ]
    absent = [tmp_path / "etc-machine-id", tmp_path / "dbus-machine-id"]
    first, second = (linux_host_id(absent, boot) for boot in boots)
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{64}", first)
