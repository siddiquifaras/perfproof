"""``perfproof status``: the state and progress of a detached compare.

``status RUN`` reads the run directory and checks the run's process
(see `perfproof.bench.detach`); it changes nothing. ``RUN`` is a run
id or a unique prefix of it or of its random part.

``--json`` result
-----------------
``run_id``
    The full run id.
``state``
    ``running``, ``finished``, ``interrupted``, ``cancelled``,
    ``timed_out``, ``failed`` or ``unknown`` (the process table could
    not be read).
``rounds_done``
    Completed rounds, counted in ``samples.jsonl``.
``max_rounds``, ``looks``, ``phase``
    From ``progress.json``; ``null`` before it was written. ``phase``
    is ``gates`` or ``rounds``.
``verdict``
    Only when ``finished``: ``outcome``, ``reason``, ``rule``,
    ``gate``, ``rounds``, ``looks``, ``estimate``, ``interval``
    (``[lower, upper]`` or ``null``) and ``record_path``. The rest is
    in the record.
``error``
    Only when ``failed``: ``code`` and ``message``.

A run that is not ``finished`` has no verdict, however many rounds it
completed.

Error codes
-----------
``not_a_repository``, ``not_found``, ``invalid_record`` (the record
does not read back).
"""

import argparse
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from perfproof.bench.detach import RunStatus, run_status
from perfproof.commands import CommandError, JsonValue, Output
from perfproof.commands._runs import run_directory
from perfproof.report import RECORD_NAME, read_record
from perfproof.schema import RunRecord

__all__ = ["add_arguments", "run"]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``status``'s options.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = "State and progress of a detached compare."
    parser.add_argument("run", help="run id, or a unique prefix of it")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )


def run(args: argparse.Namespace) -> Output:
    """Report the state of one run.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed options.

    Returns
    -------
    Output
        The result described in this module's docstring.

    Raises
    ------
    CommandError
        With one of the codes in this module's docstring.
    """
    directory = run_directory(cast("Path", args.repo), cast("str", args.run))
    status = run_status(directory)
    result: dict[str, JsonValue] = {
        "run_id": directory.name,
        "state": status.state,
        "rounds_done": status.rounds_done,
        "max_rounds": status.max_rounds,
        "looks": status.looks,
        "phase": status.phase,
    }
    text = _line(directory.name, status)
    if status.state == "finished":
        path = directory / RECORD_NAME
        v = _record(path).verdict
        interval: JsonValue = (
            None
            if v.interval is None
            else [v.interval.lower, v.interval.upper]
        )
        result["verdict"] = {
            "outcome": v.outcome,
            "reason": v.reason,
            "rule": v.rule,
            "gate": v.gate,
            "rounds": v.rounds,
            "looks": v.looks,
            "estimate": v.estimate,
            "interval": interval,
            "record_path": str(path),
        }
        text = f"{text}  {v.outcome} {v.reason}"
    if status.error is not None:
        code, message = status.error
        result["error"] = {"code": code, "message": message}
        text = f"{text}  {code}: {message}"
    return Output(result=result, text=text)


def _line(run_id: str, status: RunStatus) -> str:
    rounds = (
        f"{status.rounds_done}"
        if status.max_rounds is None
        else f"{status.rounds_done}/{status.max_rounds}"
    )
    parts = [f"{run_id}  {status.state}  rounds {rounds}"]
    if status.looks is not None:
        parts.append(f"looks {status.looks}")
    return "  ".join(parts)


def _record(path: Path) -> RunRecord:
    try:
        return read_record(path)
    except (OSError, ValidationError) as exc:
        msg = f"{path} does not read back as a record: {exc}"
        raise CommandError("invalid_record", msg) from exc
