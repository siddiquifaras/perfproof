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
``install``
    ``{"cli_on_path", "python", "clients", "problems"}``. ``python`` is
    the running ``major.minor``, listed in ``problems`` unless tested.
    ``cli_on_path`` is whether ``perfproof`` resolves on PATH.
    ``clients`` is keyed by
    ``claude`` and ``codex``, each ``{"found", "version", "skill",
    "skill_path"}``: ``found`` is whether the client's command is on
    PATH, ``skill`` is ``missing``, ``current``, ``stale`` or
    ``foreign`` (see `perfproof.commands._skills`). ``problems`` lists
    ``{"what", "fix"}`` for everything that stops ``/perfproof`` from
    being found; empty when nothing is missing. A client that is not
    on PATH is not a problem: it may run elsewhere.
"""

import argparse
import shutil
import subprocess
import sys
from typing import Final

from perfproof.bench.runner import counter_unavailable_reason
from perfproof.commands import JsonValue, Output
from perfproof.commands import _skills as skills
from perfproof.machine import fingerprint, machine_warnings
from perfproof.schema import MachineFingerprint, RunWarning

__all__ = ["add_arguments", "run"]

GIT_TIMEOUT_SECONDS: Final = 10.0
"""Time limit for ``git --version``, in seconds."""

TESTED_PYTHON: Final = ("3.12",)
"""Python versions perfproof is tested on; doctor warns on others."""

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


_FIX_SKILL: Final = "perfproof install-skills"


def _client_version(exe: str | None) -> str | None:
    """Return a client's ``--version`` line, or None if absent."""
    if exe is None:
        return None
    try:
        done = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return "version unknown"
    lines = done.stdout.strip().splitlines()
    return lines[0] if done.returncode == 0 and lines else "version unknown"


def _install() -> dict[str, JsonValue]:
    """Check that perfproof and its skill are findable."""
    source = skills.skill_source()
    problems: list[JsonValue] = []
    on_path = shutil.which("perfproof") is not None
    if not on_path:
        problems.append(
            {
                "what": "perfproof is not on PATH (ignore this if you "
                "run it with uvx; the skill falls back to uvx)",
                "fix": "uv tool install <perfproof source>; then "
                "`uv tool update-shell` if the shell still can't find it",
            }
        )
    if source is None:
        problems.append(
            {
                "what": "this perfproof has no skill files to install",
                "fix": "uv tool install --reinstall <perfproof source>",
            }
        )
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    free_threaded = not getattr(sys, "_is_gil_enabled", lambda: True)()
    tested = python in TESTED_PYTHON and not free_threaded
    if not tested:
        problems.append(
            {
                "what": f"perfproof runs on Python {python}"
                f"{' (free-threaded)' if free_threaded else ''}, which "
                f"is not tested; tested: {', '.join(TESTED_PYTHON)}",
                "fix": "uv tool install --reinstall --python 3.12 "
                "<perfproof source>",
            }
        )
    clients: dict[str, JsonValue] = {}
    for client in skills.CLIENTS:
        state = skills.check(client, source)
        exe = shutil.which(client)
        clients[client] = {
            "found": exe is not None,
            "version": _client_version(exe),
            "skill": state.state,
            "skill_path": state.path,
        }
        if state.state in ("missing", "stale") and source is not None:
            problems.append(
                {
                    "what": f"the /perfproof skill is {state.state} for "
                    f"{client} ({state.path})",
                    "fix": f"{_FIX_SKILL} --client {client}",
                }
            )
        elif state.state == "foreign":
            problems.append(
                {
                    "what": f"{state.path} exists and perfproof did not "
                    "create it",
                    "fix": f"move it away, then {_FIX_SKILL} --client "
                    f"{client}",
                }
            )
    return {
        "cli_on_path": on_path,
        "python": python,
        "clients": clients,
        "problems": problems,
    }


def _install_text(install: dict[str, JsonValue]) -> list[str]:
    """Render the install check."""
    path = "on PATH" if install["cli_on_path"] else "NOT on PATH"
    lines = ["Install", f"  perfproof {path}"]
    clients = install["clients"]
    problems = install["problems"]
    if isinstance(clients, dict):
        for name, info in clients.items():
            if not isinstance(info, dict):
                continue
            found = "found" if info["found"] else "not on PATH"
            lines.append(
                f"  {name:<6} {found} ({info['version'] or 'no version'})"
                f"; skill {info['skill']}: {info['skill_path']}"
            )
    if isinstance(problems, list) and problems:
        for problem in problems:
            if isinstance(problem, dict):
                lines.append(f"  Missing: {problem['what']}")
                lines.append(f"    Fix: {problem['fix']}")
    else:
        lines.append("  nothing missing")
    return lines


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
    install = _install()
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
        "install": install,
    }
    text = "\n".join(
        [
            *_machine_text(fp),
            "",
            *_warnings_text(warnings),
            "",
            *_tools_text(tools),
            "",
            *_install_text(install),
        ]
    )
    return Output(result=result, text=text)
