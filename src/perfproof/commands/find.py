"""``perfproof find``: run the mechanical discovery steps.

``find`` understands the project (``init`` if there is no model), picks
a profiler per part, runs the test, benchmark and workload probes the
depth asks for under a time budget, and returns a map of what the
user's workloads reach, leads that only the probes reached, one
question for the user, and the next steps. Each call advances the plan
as far as the budget allows; call it again to resume. The design is in
``docs/find.md``; the interface is in ``docs/cli.md``.

``--json`` result
-----------------
``done``, ``depth``, ``parts``, ``question``, ``next_steps``, ``map``,
``leads``, ``not_reached``, ``skipped`` and ``budget``: the fields of
`perfproof.schema.FindResult`. ``profilers`` is added: each part's
``--profiler-command`` template, an empty list for the default profiler.

Error codes
-----------
``not_a_repository``, ``unknown_part`` and ``no_test_command``
(``--confirm-tests`` and ``--test-command``), ``not_found``
(``--size-arg`` or ``--size-env`` for a workload that is not defined),
``tree_check_failed`` (git could not be read), ``state_read_failed``
and ``state_write_failed`` (the find state,
``.perfproof/find/state.json``), and the project model's own
errors, such as ``invalid_model``.
"""

import argparse
import json
import math
from pathlib import Path
from typing import Final, Literal, cast

from perfproof.bench.conditions import MeasureError
from perfproof.bench.scaling import parse_knob
from perfproof.commands import JsonValue, Output
from perfproof.commands._project import repo_root
from perfproof.find.engine import FindRequest, run_find
from perfproof.find.plan import AGENT_WAIVABLE
from perfproof.schema import FindResult, SizeKnobSpec

__all__ = ["add_arguments", "run"]

DEFAULT_TEST_TIMEOUT: Final = 300.0
_TOP_ROWS: Final = 10

type _Knob = tuple[str, SizeKnobSpec]


def _seconds(text: str) -> float:
    """Parse a positive, finite number of seconds, for argparse."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value > 0):
        msg = f"expected a positive number of seconds, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return value


def _knob(kind: Literal["arg", "env"], text: str) -> _Knob:
    """Parse ``WORKLOAD:NAME=BASE``, for argparse."""
    workload, sep, rest = text.partition(":")
    if not (sep and workload and rest):
        msg = f"expected WORKLOAD:NAME=BASE, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    try:
        parsed = parse_knob(kind, rest)
    except MeasureError as exc:
        raise argparse.ArgumentTypeError(exc.message) from exc
    return workload, SizeKnobSpec(
        kind=kind, name=parsed.name, base=parsed.base
    )


def _size_arg(text: str) -> _Knob:
    return _knob("arg", text)


def _size_env(text: str) -> _Knob:
    return _knob("env", text)


def _waiver(text: str) -> tuple[str, str]:
    """Parse ``CODE=REASON``, for argparse."""
    code, sep, reason = text.partition("=")
    if code not in AGENT_WAIVABLE:
        msg = f"CODE is one of {', '.join(AGENT_WAIVABLE)}, got {code!r}"
        raise argparse.ArgumentTypeError(msg)
    if not (sep and reason.strip()):
        msg = f"a waiver needs a reason: {code}=REASON"
        raise argparse.ArgumentTypeError(msg)
    return code, reason.strip()


def _test_command(text: str) -> tuple[str, tuple[str, ...]]:
    """Parse ``PART=JSON``, for argparse."""
    part, sep, body = text.partition("=")
    try:
        value: object = json.loads(body) if sep else None
    except json.JSONDecodeError as exc:
        msg = f"the argv after PART= is not JSON: {exc}"
        raise argparse.ArgumentTypeError(msg) from exc
    if not (
        part
        and isinstance(value, list)
        and value
        and all(isinstance(a, str) for a in cast("list[object]", value))
    ):
        msg = (
            "expected PART=JSON with JSON a non-empty array of strings, "
            f"got {text!r}"
        )
        raise argparse.ArgumentTypeError(msg)
    return part, tuple(cast("list[str]", value))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``find`` options.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Run the mechanical discovery steps: tooling, test, benchmark "
        "and workload probes, a map, leads, one question and the next "
        "steps. Call it again to resume."
    )
    parser.add_argument(
        "--depth",
        choices=("light", "medium", "deep"),
        default="medium",
        help="how much to run (default: medium)",
    )
    parser.add_argument(
        "--budget",
        type=_seconds,
        metavar="SECONDS",
        help="wall-clock seconds for probes in this call (default: 300 "
        "light, 900 medium, 2400 deep)",
    )
    parser.add_argument(
        "--size-arg",
        action="append",
        default=[],
        type=_size_arg,
        metavar="WORKLOAD:FLAG=BASE",
        help="the flag that sets a workload's input size",
    )
    parser.add_argument(
        "--size-env",
        action="append",
        default=[],
        type=_size_env,
        metavar="WORKLOAD:NAME=BASE",
        help="the environment variable that sets a workload's input size",
    )
    parser.add_argument(
        "--no-tests",
        action="store_true",
        help="never run test suites",
    )
    parser.add_argument(
        "--test-timeout",
        type=_seconds,
        default=DEFAULT_TEST_TIMEOUT,
        metavar="SECONDS",
        help="time limit of each test run "
        f"(default: {DEFAULT_TEST_TIMEOUT:g})",
    )
    parser.add_argument(
        "--confirm-tests",
        action="append",
        default=[],
        metavar="PART",
        help="record that the user agrees to run this part's test command",
    )
    parser.add_argument(
        "--test-command",
        action="append",
        default=[],
        type=_test_command,
        metavar="PART=JSON",
        help="the test command of a part, as a JSON array of strings, "
        "for when none was detected; it still needs --confirm-tests",
    )
    parser.add_argument(
        "--waive",
        action="append",
        default=[],
        type=_waiver,
        metavar="CODE=REASON",
        help="record why a next step cannot be met "
        f"({', '.join(AGENT_WAIVABLE)})",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )


def run(args: argparse.Namespace) -> Output:
    """Run one ``find`` call.

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
    knobs = dict(
        [
            *cast("list[_Knob]", args.size_arg),
            *cast("list[_Knob]", args.size_env),
        ]
    )
    request = FindRequest(
        root=repo_root(cast("Path", args.repo)),
        depth=cast("str", args.depth),
        budget_seconds=cast("float | None", args.budget),
        size_knobs=knobs,
        no_tests=cast("bool", args.no_tests),
        test_timeout=cast("float", args.test_timeout),
        confirm_tests=tuple(cast("list[str]", args.confirm_tests)),
        waive=dict(cast("list[tuple[str, str]]", args.waive)),
        test_commands=dict(
            cast("list[tuple[str, tuple[str, ...]]]", args.test_command)
        ),
    )
    found = run_find(request)
    document = cast(
        "dict[str, JsonValue]", found.result.model_dump(mode="json")
    )
    document["profilers"] = {
        part: list(template) for part, template in found.profilers.items()
    }
    return Output(result=document, text=_text(found.result))


def _text(result: FindResult) -> str:
    """Render the result for a person."""
    budget = result.budget
    lines = [
        f"find ({result.depth}): "
        + ("done" if result.done else "not done")
        + f"; {budget.spent_seconds:.0f} s of {budget.limit_seconds:.0f} s"
        + (" (budget used up)" if budget.exhausted else ""),
        "Parts: "
        + (
            ", ".join(f"{p.id} ({p.language})" for p in result.parts) or "none"
        ),
    ]
    if result.question:
        lines.append(f"Question: {result.question}")
    lines.extend(
        f"Next [{step.code}]: {step.message}" for step in result.next_steps
    )
    if result.map:
        lines.append("Map (workloads reach these, by cap):")
        lines.extend(
            f"  {r.function} ({r.file}:{r.line}) layer {r.layer}, cap {r.cap}"
            for r in result.map[:_TOP_ROWS]
        )
    if result.leads:
        lines.append("Leads (only the probes reached these):")
        lines.extend(
            f"  {lead.kind} {lead.name} ({lead.file}) "
            f"seen in {', '.join(lead.seen_in) or 'none'}"
            for lead in result.leads[:_TOP_ROWS]
        )
    if result.not_reached:
        lines.append(f"Never sampled: {len(result.not_reached)} file(s).")
    lines.extend(
        f"Skipped {s.key}: {s.status} ({s.reason})" for s in result.skipped
    )
    return "\n".join(lines)
