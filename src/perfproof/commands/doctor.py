"""``perfproof doctor``: this machine, its noise sources and its tools.

Fingerprints the machine with `perfproof.machine.fingerprint`, lists the
noise warnings `perfproof.machine.machine_warnings` derives from it with
what to do about each, and checks the external tools perfproof uses
here. A probe that could not be read is shown as unknown with its
reason, never as a guessed value.

``--json`` result
-----------------
``machine``
    The `perfproof.schema.MachineFingerprint`, as its JSON model dump;
    an unknown probe is ``null`` with its reason in ``machine.missing``.
``warnings``
    List of ``{"code", "message", "advice"}``; ``advice`` is ``null``
    for a warning code this version has no advice for.
``tools``
    Object keyed by tool name (``git``, ``perf``), each
    ``{"available", "used_for", "detail"}``: ``detail`` is the tool's
    version when available, else why it is not.
"""

import argparse
import shutil
import subprocess
from typing import Final

from perfproof.bench.runner import counter_unavailable_reason
from perfproof.commands import JsonValue, Output
from perfproof.machine import fingerprint, machine_warnings
from perfproof.schema import MachineFingerprint, RunWarning

__all__ = ["add_arguments", "run"]

GIT_TIMEOUT_SECONDS: Final = 10.0
"""Time limit for ``git --version``, in seconds."""

COUNTER_EVENT: Final = "instructions"
"""``perf`` event checked for the ``counter`` metric."""

_ADVICE: Final[dict[str, str]] = {
    "on_battery": "Plug the machine into mains power before comparing.",
    "high_load": (
        "Pause other work (builds, indexers, other benchmarks) before "
        "comparing, and run doctor again."
    ),
    "non_performance_governor": (
        "Set the cpufreq governor to performance for the run, for "
        "example with `cpupower frequency-set -g performance` as root."
    ),
    "low_power_mode": (
        "Turn Low Power Mode off in System Settings, Battery, before "
        "comparing."
    ),
    "thermal_limit": (
        "Let the machine cool and keep its vents clear; run doctor again "
        "until the limit clears."
    ),
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Describe ``perfproof doctor``; it takes no options of its own.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Check this machine, its noise sources and the tools perfproof "
        "can use here."
    )


def _git() -> tuple[bool, str]:
    """Check that git runs, returning its version or why not."""
    git = shutil.which("git")
    if git is None:
        return False, "git is not on PATH"
    try:
        done = subprocess.run(
            [git, "--version"],
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (
            False,
            f"git --version did not finish in {GIT_TIMEOUT_SECONDS} s",
        )
    except OSError as exc:
        return False, f"git could not be started: {exc}"
    if done.returncode != 0:
        tail = done.stderr.strip()[-500:]
        return False, f"git --version exited {done.returncode}: {tail}"
    return True, done.stdout.strip()


def _tools() -> dict[str, dict[str, JsonValue]]:
    """Check each external tool perfproof uses here."""
    git_ok, git_detail = _git()
    reason = counter_unavailable_reason(COUNTER_EVENT)
    return {
        "git": {
            "available": git_ok,
            "used_for": "baseline snapshots as git worktrees",
            "detail": git_detail,
        },
        "perf": {
            "available": reason is None,
            "used_for": f"the counter metric ({COUNTER_EVENT})",
            "detail": (
                f"perf stat counts {COUNTER_EVENT}"
                if reason is None
                else reason
            ),
        },
    }


def _value_text(fp: MachineFingerprint, field: str) -> str:
    """Spell one known fingerprint value for a person."""
    if field == "load_average" and fp.load_average is not None:
        one, five, fifteen = fp.load_average
        return f"{one:.2f} {five:.2f} {fifteen:.2f} (1, 5, 15 min)"
    if field == "memory_bytes" and fp.memory_bytes is not None:
        return f"{fp.memory_bytes} bytes ({fp.memory_bytes / 2**30:.1f} GiB)"
    if field == "battery_percent" and fp.battery_percent is not None:
        return f"{fp.battery_percent:g}%"
    if field == "gpus" and fp.gpus is not None:
        names = [f"{g.name} (driver {g.driver_version})" for g in fp.gpus]
        return "; ".join(names) if names else "none"
    return str(getattr(fp, field))


def _machine_text(fp: MachineFingerprint) -> list[str]:
    """Render the fingerprint, one probe per line."""
    lines = ["Machine"]
    for field in MachineFingerprint.model_fields:
        if field == "missing":
            continue
        if getattr(fp, field) is None:
            reason = fp.missing.get(field, "no reason recorded")
            text = f"unknown: {reason}"
        else:
            text = _value_text(fp, field)
        lines.append(f"  {field:<17} {text}")
    return lines


def _warnings_text(warnings: tuple[RunWarning, ...]) -> list[str]:
    """Render the warnings and their advice."""
    lines = ["Warnings"]
    if not warnings:
        lines.append("  none")
    for warning in warnings:
        lines.append(f"  {warning.code}: {warning.message}")
        advice = _ADVICE.get(warning.code)
        if advice is not None:
            lines.append(f"    Advice: {advice}")
    return lines


def _tools_text(tools: dict[str, dict[str, JsonValue]]) -> list[str]:
    """Render the tool checks."""
    lines = ["Tools"]
    for name, tool in tools.items():
        state = "available" if tool["available"] else "unavailable"
        lines.append(
            f"  {name:<6} {state}, for {tool['used_for']}: {tool['detail']}"
        )
    return lines


def run(args: argparse.Namespace) -> Output:
    """Fingerprint the machine and check its tools.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed options; ``doctor`` has none of its own.

    Returns
    -------
    Output
        The result described in this module's docstring. Takes up to
        the probe timeouts of `perfproof.machine.fingerprint`, plus up
        to 30 s for ``perf stat`` on Linux.
    """
    fp = fingerprint()
    warnings = machine_warnings(fp)
    tools = _tools()
    result: dict[str, JsonValue] = {
        "machine": fp.model_dump(mode="json"),
        "warnings": [
            {
                "code": w.code,
                "message": w.message,
                "advice": _ADVICE.get(w.code),
            }
            for w in warnings
        ],
        "tools": tools,
    }
    text = "\n".join(
        [
            *_machine_text(fp),
            "",
            *_warnings_text(warnings),
            "",
            *_tools_text(tools),
        ]
    )
    return Output(result=result, text=text)
