"""``perfproof log``: the hypothesis log of a workload.

The log is `perfproof.hypotheses`' append-only
``.perfproof/hypotheses/<workload>.jsonl``: one event per step of a
hypothesis' chain (observation, mechanism, prediction, falsification,
implementation, outcome), skips with their reason, corrections,
reorders, and stale and revalidated markers. See `perfproof.schema` for
the events and ``docs/discovery.md`` for the chain.

Subcommands
-----------
``log show WORKLOAD [--hypothesis ID] [--brief]``
    One summary per hypothesis, with whether it is stale. With
    ``--hypothesis`` the events of that hypothesis are included.
    ``--brief`` is one line per hypothesis, at most `BRIEF_MAX_BYTES`
    of text and `BRIEF_MAX_HYPOTHESES` hypotheses in JSON.
``log add WORKLOAD (--event JSON | --event-file PATH)``
    Append one event, a JSON object with ``event`` (the step),
    ``hypothesis_id`` and that event's fields. ``event_id`` (a random
    one), ``recorded_at`` (now, UTC), ``schema_version`` and ``session``
    (``--session``, else ``$PERFPROOF_SESSION``, else ``cli-<pid>``)
    are filled in when absent. ``PATH`` ``-`` reads stdin. Recording a
    new observation also returns the earlier hypotheses on the same
    code, from every workload's log. An implementation of a hypothesis
    its cheap test refuted is refused unless ``override_reason`` is set.
``log anchor FILE QUALIFIED_NAME``
    The ``source_hash`` to put in a code anchor: the hash of that
    function or class's source, which does not change when it moves
    within its file.

``--repo PATH`` is any path in the repository (default ``.``).

``--json`` result
-----------------
``show``: ``workload``, ``path``, ``hypotheses`` (list of summaries),
``warnings`` (skipped lines, each with its line number, kept in the
file, and chain violations found on replay), ``omitted`` (brief only)
and, unless brief, ``events`` for ``--hypothesis``.

``add``: ``recorded`` (``event_id``, ``event``, ``hypothesis_id``),
``path``, ``hypothesis`` (its summary), ``past`` (summaries of earlier
hypotheses on the same code, newest first, at most `PAST_MAX`),
``past_omitted``, ``warnings``.

``anchor``: ``file``, ``qualified_name``, ``source_hash``.

A summary is ``hypothesis_id``, ``workload``, ``steps`` (each step
``done``, ``skipped`` or ``missing``), ``next_step``,
``falsification`` (``supported``, ``refuted``, ``inconclusive`` or
``null``), ``cap`` (the predicted upper bound, a bound and not a
speedup, or ``null``), ``measured`` (the outcome's estimate and
interval, or ``null``), ``verdict``, ``rule``, ``stale`` and
``unchecked`` (lists of ``{"anchor", "detail"}``: what changed, and what
could not be checked, so a hypothesis with anything unchecked is not
known to be fresh), ``retry_of``, ``violation_count`` and
``violations`` (events that break the chain's rules), ``code`` (anchored
file and qualified name) and ``events`` (a count). The brief form
leaves out ``steps``, ``rule``, ``code``, ``events`` and ``violations``
and lists only anchor names in ``stale`` and ``unchecked``; the
``result`` member of a brief document is at most
`BRIEF_MAX_JSON_BYTES`, with whole entries dropped and counted in
``omitted`` or ``past_omitted``.

Error codes
-----------
``not_a_repository``, ``invalid_name``, ``invalid_event`` (not JSON,
or not a valid event), ``unknown_hypothesis``, ``duplicate_hypothesis``,
``duplicate_event``, ``unknown_event``, ``unknown_retry``,
``unknown_before``, ``step_missing``, ``refuted_hypothesis``,
``invalid_correction``, ``empty_override_reason``, ``retry_unmarked``,
``line_too_long``, ``log_unreadable``, ``log_write_failed``,
``log_locked``,
``code_not_found`` (``anchor``: no such definition, or not a Python
file), ``not_found`` (``show --hypothesis``: no such hypothesis).
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from pydantic import ValidationError

from perfproof.commands import CommandError, JsonValue, Output
from perfproof.hypotheses import (
    CorruptLine,
    HypothesisLogError,
    HypothesisView,
    SourceCache,
    StaleReason,
    assess_staleness,
    current_workload_spec_hash,
    definition_hash,
    find_past,
    log_path,
    read_log,
    record_event,
    views,
)
from perfproof.schema import (
    HYPOTHESIS_EVENT,
    HypothesisEvent,
    ObservationEvent,
)

__all__ = [
    "BRIEF_MAX_BYTES",
    "BRIEF_MAX_HYPOTHESES",
    "BRIEF_MAX_JSON_BYTES",
    "PAST_MAX",
    "add_arguments",
    "run",
]

BRIEF_MAX_BYTES: Final = 2048
"""Most bytes of text a brief form writes."""

BRIEF_MAX_HYPOTHESES: Final = 8
"""Most hypotheses a brief JSON result lists."""

BRIEF_MAX_JSON_BYTES: Final = 4096
"""Most bytes of the ``result`` member of a brief JSON document."""

PAST_MAX: Final = 5
"""Most earlier hypotheses shown with a new one."""

GIT_TIMEOUT_SECONDS: Final = 10.0
"""Time limit for ``git rev-parse``, in seconds."""

MAX_EVENT_FILE_BYTES: Final = 1024 * 1024
"""Largest event text read from a file or stdin."""


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the subcommands of ``perfproof log``.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Record a hypothesis, its cheap test, its candidate and its "
        "outcome; show the log for a workload."
    )
    sub = parser.add_subparsers(dest="action", metavar="ACTION", required=True)
    show = sub.add_parser(
        "show", help="summarise the hypotheses of a workload"
    )
    show.add_argument("workload", help="workload name")
    show.add_argument(
        "--hypothesis", metavar="ID", help="include this one's events"
    )
    _common(show)
    add = sub.add_parser("add", help="append one event")
    add.add_argument("workload", help="workload name")
    source = add.add_mutually_exclusive_group(required=True)
    source.add_argument("--event", metavar="JSON", help="the event, as JSON")
    source.add_argument(
        "--event-file",
        metavar="PATH",
        help="a file with the event; - is stdin",
    )
    add.add_argument(
        "--session",
        metavar="ID",
        help="session recording it (default: $PERFPROOF_SESSION)",
    )
    _common(add)
    anchor = sub.add_parser("anchor", help="hash a definition for an anchor")
    anchor.add_argument("file", help="source file, relative to the repo root")
    anchor.add_argument("qualified_name", help="such as TileWriter.write")
    anchor.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--brief",
        action="store_true",
        help=f"one line per hypothesis, at most {BRIEF_MAX_BYTES} bytes",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )


def run(args: argparse.Namespace) -> Output:
    """Run one subcommand of ``perfproof log``.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed arguments.

    Returns
    -------
    Output
        The result and its text.

    Raises
    ------
    CommandError
        For any code in the module docstring.
    """
    root = _repo_root(cast("Path", args.repo))
    action = cast("str", args.action)
    try:
        if action == "anchor":
            return _anchor(root, cast("str", args.file), args.qualified_name)
        workload = cast("str", args.workload)
        if action == "show":
            return _show(
                root,
                workload,
                cast("str | None", args.hypothesis),
                brief=bool(args.brief),
            )
        return _add(
            root,
            workload,
            _event_text(args),
            cast("str | None", args.session),
            brief=bool(args.brief),
        )
    except HypothesisLogError as exc:
        details: dict[str, JsonValue] = dict(exc.details)
        raise CommandError(exc.code, exc.message, details) from exc


def _repo_root(repo: Path) -> Path:
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
    except (subprocess.TimeoutExpired, OSError) as exc:
        msg = f"git rev-parse failed: {exc}"
        raise CommandError("not_a_repository", msg) from exc
    if done.returncode != 0:
        msg = (
            f"{repo} is not in a git working tree: {done.stderr.strip()}; "
            "perfproof keeps the hypothesis log in the repository's "
            ".perfproof/"
        )
        raise CommandError("not_a_repository", msg, {"repo": str(repo)})
    return Path(done.stdout.strip())


def _event_text(args: argparse.Namespace) -> str:
    inline = cast("str | None", args.event)
    if inline is not None:
        return inline
    name = cast("str", args.event_file)
    try:
        if name == "-":
            return sys.stdin.read(MAX_EVENT_FILE_BYTES)
        with Path(name).open(encoding="utf-8") as handle:
            return handle.read(MAX_EVENT_FILE_BYTES)
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"cannot read the event from {name}: {exc}"
        raise CommandError("invalid_event", msg) from exc


def _parse_event(text: str, session: str | None) -> HypothesisEvent:
    """Fill in the fields perfproof owns and validate the event."""
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"the event is not JSON: {exc}"
        raise CommandError("invalid_event", msg) from exc
    if not isinstance(raw, dict):
        msg = "the event is a JSON object"
        raise CommandError("invalid_event", msg)
    data = cast("dict[str, object]", raw)
    data.setdefault("event_id", f"e-{uuid.uuid4().hex[:12]}")
    data.setdefault("recorded_at", datetime.now(UTC).isoformat())
    data.setdefault(
        "session",
        session or os.environ.get("PERFPROOF_SESSION") or f"cli-{os.getpid()}",
    )
    try:
        return HYPOTHESIS_EVENT.validate_json(json.dumps(data))
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        msg = f"not a valid event: {where}: {first['msg']}"
        raise CommandError("invalid_event", msg) from exc


def _warnings(corrupt: Sequence[CorruptLine]) -> list[str]:
    return [
        f"line {c.number} skipped ({c.reason}); it stays in the file"
        for c in corrupt
    ]


def _add(
    root: Path,
    workload: str,
    text: str,
    session: str | None,
    *,
    brief: bool,
) -> Output:
    path = log_path(root, workload)
    new = _parse_event(text, session)
    contents = record_event(path, new)
    after = views(workload, (*contents.events, new))
    mine = next(v for v in after if v.hypothesis_id == new.hypothesis_id)
    summary = _summary(root, mine, brief=brief)
    past: list[HypothesisView] = []
    if isinstance(new, ObservationEvent):
        past = find_past(
            path.parent,
            new.anchors,
            exclude=(workload, new.hypothesis_id),
        )
    cache = SourceCache()
    shown = [
        _summary(root, v, brief=brief, cache=cache) for v in past[:PAST_MAX]
    ]
    warnings = [*_warnings(contents.corrupt), *_violations(after)]
    result: dict[str, JsonValue] = {
        "recorded": {
            "event_id": new.event_id,
            "event": new.event,
            "hypothesis_id": new.hypothesis_id,
        },
        "path": str(path),
        "hypothesis": summary,
        "past": cast("list[JsonValue]", shown),
        "past_omitted": max(0, len(past) - PAST_MAX),
        "warnings": cast("list[JsonValue]", warnings),
    }
    past_left = max(0, len(past) - PAST_MAX)
    if brief:
        past_left = _shrink(result, "past", "past_omitted", drop_first=False)
    shown = cast("list[dict[str, JsonValue]]", result["past"])
    lines = [
        f"recorded {new.event} for {new.hypothesis_id} in {path.name}",
        _line(summary, workload),
        *warnings,
    ]
    if shown:
        lines.append("earlier hypotheses on this code:")
        lines.extend(f"  {_line(s, workload)}" for s in shown)
    if past_left:
        lines.append(f"  ... and {past_left} more")
    return Output(result, _fit(lines) if brief else "\n".join(lines))


def _violations(found: Sequence[HypothesisView]) -> list[str]:
    return [f"{v.hypothesis_id}: {m}" for v in found for m in v.violations]


def _show(
    root: Path, workload: str, hypothesis: str | None, *, brief: bool
) -> Output:
    path = log_path(root, workload)
    contents = read_log(path)
    every = views(workload, contents.events)
    chosen = every
    if hypothesis is not None:
        chosen = [v for v in every if v.hypothesis_id == hypothesis]
        if not chosen:
            msg = f"no hypothesis {hypothesis} in the log of {workload}"
            raise CommandError("not_found", msg, {"hypothesis_id": hypothesis})
    cache = SourceCache()
    summaries = [_summary(root, v, brief=brief, cache=cache) for v in chosen]
    warnings = [*_warnings(contents.corrupt), *_violations(chosen)]
    omitted = 0
    if brief and len(summaries) > BRIEF_MAX_HYPOTHESES:
        omitted = len(summaries) - BRIEF_MAX_HYPOTHESES
        summaries = summaries[-BRIEF_MAX_HYPOTHESES:]
    result: dict[str, JsonValue] = {
        "workload": workload,
        "path": str(path),
        "hypotheses": cast("list[JsonValue]", summaries),
        "warnings": cast("list[JsonValue]", warnings),
    }
    if brief:
        result["omitted"] = omitted
        omitted = _shrink(result, "hypotheses", "omitted", drop_first=True)
        summaries = cast("list[dict[str, JsonValue]]", result["hypotheses"])
    if hypothesis is not None and not brief:
        result["events"] = [
            cast("JsonValue", e.model_dump(mode="json"))
            for e in chosen[0].events
        ]
    lines = [f"{workload}: {len(every)} hypotheses", *warnings]
    if omitted:
        lines.append(f"... {omitted} earlier not shown")
    # Newest first, so a cut drops the oldest.
    lines.extend(f"  {_line(s, workload)}" for s in reversed(summaries))
    text = _fit(lines) if brief else "\n".join(lines)
    return Output(result, text)


def _shrink(
    result: dict[str, JsonValue],
    key: str,
    omitted_key: str,
    *,
    drop_first: bool,
) -> int:
    """Drop entries of a list until the JSON fits its byte cap.

    The cap is `BRIEF_MAX_JSON_BYTES`, measured on the ``result``
    member as the CLI writes it.
    """
    items = list(cast("list[JsonValue]", result[key]))
    omitted = cast("int", result[omitted_key])
    while items and _size(result) > BRIEF_MAX_JSON_BYTES:
        items.pop(0 if drop_first else -1)
        omitted += 1
        result[key] = items
        result[omitted_key] = omitted
    result[key] = items
    result[omitted_key] = omitted
    return omitted


def _size(result: Mapping[str, JsonValue]) -> int:
    return len(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True))


def _anchor(root: Path, file: str, name: str) -> Output:
    if not file.endswith(".py"):
        msg = f"{file} is not a Python file; only Python can be hashed"
        raise CommandError("code_not_found", msg, {"file": file})
    target = (root / file).resolve()
    if not target.is_relative_to(root.resolve()):
        msg = f"{file} is outside the repository"
        raise CommandError("code_not_found", msg, {"file": file})
    try:
        source = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"cannot read {file}: {exc}"
        raise CommandError("code_not_found", msg, {"file": file}) from exc
    digest = definition_hash(source, name)
    if digest is None:
        msg = f"no definition {name} in {file}, or the file does not parse"
        raise CommandError(
            "code_not_found", msg, {"file": file, "qualified_name": name}
        )
    result: dict[str, JsonValue] = {
        "file": file,
        "qualified_name": name,
        "source_hash": digest,
    }
    return Output(result, f"{file}:{name} {digest}")


def _summary(
    root: Path,
    view: HypothesisView,
    *,
    brief: bool,
    cache: SourceCache | None = None,
) -> dict[str, JsonValue]:
    """Summarise a hypothesis with the staleness found now.

    The full form lists each stale and unchecked anchor with its detail.
    The brief form lists their names only. Staleness and what could not
    be checked are always present, so an unchecked hypothesis is never
    shown as fresh.
    """
    reasons, unchecked = assess_staleness(
        root,
        view,
        workload_spec_hash=current_workload_spec_hash(root, view.workload),
        cache=cache,
    )
    prediction = view.prediction
    falsification = view.falsification
    outcome = view.outcome
    measured: JsonValue = None
    if outcome is not None and outcome.speedup is not None:
        measured = {
            "estimate": outcome.speedup.estimate,
            "lower": outcome.speedup.interval.lower,
            "upper": outcome.speedup.interval.upper,
        }
    summary: dict[str, JsonValue] = {
        "hypothesis_id": view.hypothesis_id,
        "workload": view.workload,
        "next_step": view.next_step,
        "falsification": None
        if falsification is None
        else falsification.result,
        "cap": None if prediction is None else prediction.cap.upper_bound,
        "measured": measured,
        "verdict": None if outcome is None else outcome.verdict,
        "retry_of": view.retry_of,
        "violation_count": len(view.violations),
    }
    if brief:
        names: list[JsonValue] = [*sorted({r.anchor for r in reasons})]
        summary["stale"] = names
        summary["unchecked"] = [*sorted({r.anchor for r in unchecked})]
        return summary
    summary["rule"] = None if outcome is None else outcome.rule
    summary["steps"] = dict(view.steps)
    summary["stale"] = _reasons(reasons)
    summary["unchecked"] = _reasons(unchecked)
    summary["violations"] = cast("list[JsonValue]", list(view.violations))
    summary["code"] = [
        {"file": a.file, "qualified_name": a.qualified_name}
        for a in view.anchors.code
    ]
    summary["events"] = len(view.events)
    return summary


def _reasons(reasons: Sequence[StaleReason]) -> list[JsonValue]:
    return [{"anchor": r.anchor, "detail": r.detail} for r in reasons]


def _anchor_names(items: JsonValue) -> list[str]:
    """Return anchor names from either form of a staleness list."""
    names: set[str] = set()
    for item in cast("list[JsonValue]", items):
        if isinstance(item, dict):
            names.add(str(item["anchor"]))
        else:
            names.add(str(item))
    return sorted(names)


def _line(summary: Mapping[str, JsonValue], here: str) -> str:
    """Write a summary as a line; a cap is a bound, never a gain."""
    parts = [str(summary["hypothesis_id"])]
    if summary["workload"] != here:
        parts.append(f"[{summary['workload']}]")
    parts.append(f"next: {summary['next_step'] or 'none'}")
    cap = summary["cap"]
    if isinstance(cap, float):
        parts.append(f"cap {cap:.3g}x (bound)")
    if summary["falsification"] is not None:
        parts.append(f"test: {summary['falsification']}")
    if summary["verdict"] is not None:
        parts.append(f"outcome: {summary['verdict']}")
    measured = summary["measured"]
    if isinstance(measured, dict):
        est = cast("float", measured["estimate"])
        lo = cast("float", measured["lower"])
        hi = cast("float", measured["upper"])
        parts.append(f"measured {est:.3g}x [{lo:.3g}, {hi:.3g}]")
    stale = _anchor_names(summary["stale"])
    if stale:
        parts.append("STALE: " + ", ".join(stale))
    unchecked = _anchor_names(summary["unchecked"])
    if unchecked:
        parts.append("UNCHECKED: " + ", ".join(unchecked))
    if summary["violation_count"]:
        parts.append(f"CHAIN INVALID ({summary['violation_count']})")
    if summary["retry_of"]:
        parts.append(f"retry of {summary['retry_of']}")
    return "  ".join(parts)


def _fit(lines: Sequence[str]) -> str:
    """Join whole lines within the cap; say how many are cut."""
    for n in range(len(lines), -1, -1):
        text = "\n".join(lines[:n])
        if n < len(lines):
            marker = f"... {len(lines) - n} more lines"
            text = f"{text}\n{marker}" if text else marker
        if len(text.encode("utf-8")) <= BRIEF_MAX_BYTES:
            return text
    return ""
