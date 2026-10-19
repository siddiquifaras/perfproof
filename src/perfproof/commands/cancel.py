"""``perfproof cancel``: stop a detached compare and its process tree.

``cancel RUN`` marks the run ``cancelled`` and ends its process and
every descendant: SIGTERM first, SIGKILL after ``--grace`` seconds (see
`perfproof.bench.detach`). A cancelled run never yields a verdict. A
run that is already ``cancelled`` is reported as such; a ``finished``
run is refused.

``--json`` result
-----------------
``{"run_id", "state"}``, with ``state`` ``cancelled``.

Error codes
-----------
``not_a_repository``, ``not_found``, ``already_finished``,
``cancel_incomplete`` (a process survived SIGKILL; ``details.pids``),
``usage`` (a bad ``--grace``).
"""

import argparse
import math
from pathlib import Path
from typing import cast

from perfproof.bench.detach import (
    DEFAULT_GRACE_SECONDS,
    DetachError,
    cancel_run,
)
from perfproof.commands import CommandError, Output
from perfproof.commands._runs import as_command_error, run_directory

__all__ = ["add_arguments", "run"]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``cancel``'s options.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = "Stop a detached compare and its whole process tree."
    parser.add_argument("run", help="run id, or a unique prefix of it")
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )
    parser.add_argument(
        "--grace",
        type=float,
        default=DEFAULT_GRACE_SECONDS,
        metavar="SECONDS",
        help=(
            "time for SIGTERM before SIGKILL "
            f"(default: {DEFAULT_GRACE_SECONDS:g})"
        ),
    )


def run(args: argparse.Namespace) -> Output:
    """Cancel one run.

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
    grace = cast("float", args.grace)
    if not math.isfinite(grace) or grace < 0:
        raise CommandError("usage", "--grace must be 0 or more seconds")
    directory = run_directory(cast("Path", args.repo), cast("str", args.run))
    try:
        status = cancel_run(directory, grace_seconds=grace)
    except DetachError as exc:
        raise as_command_error(exc) from exc
    except OSError as exc:
        msg = f"could not cancel {directory.name}: {exc}"
        raise CommandError("cancel_incomplete", msg) from exc
    return Output(
        result={"run_id": directory.name, "state": status.state},
        text=f"{directory.name}  {status.state}; no verdict",
    )
