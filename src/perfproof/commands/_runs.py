"""Helpers the commands on detached runs share (not a command)."""

import subprocess
from pathlib import Path
from typing import cast

from perfproof.bench.detach import DetachError, find_run
from perfproof.bench.snapshot import GIT_TIMEOUT_SECONDS
from perfproof.commands import CommandError, JsonValue
from perfproof.schema import KEEP_BLOCKING_WARNINGS, RunRecord

__all__ = [
    "as_command_error",
    "not_verified",
    "repo_root",
    "run_directory",
    "warnings_json",
]


def not_verified(record: RunRecord) -> list[JsonValue]:
    """List what a record could not verify, for the skill to relay.

    The skill reads only the ``--json`` result, so what withholds a
    KEEP is spelled out there: gates ``not_available`` with their
    reasons, guardrails ``not_declared``, ``unmeasurable`` or
    ``not_established``, and the warnings that withhold a KEEP (a
    counter measured as wall time, a value of unknown resolution).

    Parameters
    ----------
    record : RunRecord
        The record of a finished comparison.

    Returns
    -------
    list of JsonValue
        Objects ``{"kind", "name", "status", "reason"}`` with ``kind``
        ``gate``, ``guardrail`` or ``warning``; empty when nothing was
        left unverified.
    """
    gates: list[JsonValue] = [
        {
            "kind": "gate",
            "name": g.gate,
            "status": g.status,
            "reason": g.detail,
        }
        for g in record.gates
        if g.status == "not_available"
    ]
    guardrails: list[JsonValue] = [
        {
            "kind": "guardrail",
            "name": r.metric,
            "status": r.status,
            "reason": r.reason or "measured, but the data can't tell yet",
        }
        for r in record.guardrails
        if r.status in ("not_declared", "unmeasurable", "not_established")
    ]
    warnings: list[JsonValue] = [
        {
            "kind": "warning",
            "name": w.code,
            "status": "withholds_keep",
            "reason": w.message,
        }
        for w in record.warnings
        if w.code in KEEP_BLOCKING_WARNINGS
    ]
    return [*gates, *guardrails, *warnings]


def warnings_json(record: RunRecord) -> list[JsonValue]:
    """Return the warnings as ``{"code", "message"}`` objects."""
    return [{"code": w.code, "message": w.message} for w in record.warnings]


def as_command_error(exc: DetachError) -> CommandError:
    """Turn a refused detach request into the command's error.

    Parameters
    ----------
    exc : DetachError
        The refusal.

    Returns
    -------
    CommandError
        Same code and message, with the details as JSON values.
    """
    details: dict[str, JsonValue] = {}
    for key, value in exc.details.items():
        if isinstance(value, list):
            details[key] = [str(v) for v in cast("list[object]", value)]
        elif isinstance(value, (bool, int, float, str)):
            details[key] = value
        else:
            details[key] = str(value)
    return CommandError(exc.code, exc.message, details)


def repo_root(repo: Path) -> Path:
    """Return the root of the git working tree containing ``repo``.

    Parameters
    ----------
    repo : pathlib.Path
        Any path in the repository.

    Returns
    -------
    pathlib.Path
        The root.

    Raises
    ------
    CommandError
        ``not_a_repository`` if ``repo`` is in no git working tree.
    """
    argv = ["git", "-C", str(repo), "rev-parse", "--show-toplevel"]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"git rev-parse failed: {exc}"
        raise CommandError("not_a_repository", msg) from exc
    if done.returncode != 0:
        msg = f"{repo} is not in a git working tree: {done.stderr.strip()}"
        raise CommandError("not_a_repository", msg, {"repo": str(repo)})
    return Path(done.stdout.strip())


def run_directory(repo: Path, run_id: str) -> Path:
    """Return the directory of run ``run_id``.

    Parameters
    ----------
    repo : pathlib.Path
        Any path in the repository.
    run_id : str
        A run id or a unique prefix; see
        `perfproof.bench.detach.find_run`.

    Returns
    -------
    pathlib.Path
        The run directory.

    Raises
    ------
    CommandError
        ``not_a_repository`` or ``not_found``.
    """
    try:
        return find_run(repo_root(repo), run_id)
    except DetachError as exc:
        raise as_command_error(exc) from exc
