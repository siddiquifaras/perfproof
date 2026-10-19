"""``perfproof project``: show, confirm, edit, retire and check entries.

Actions
-------
``show [ID]``
    The model: every entry with its status, and per workload whether it
    may be measured now (the same test ``compare`` applies). Unknowns
    come back as ``questions``.
``confirm ID [ID ...]``
    Record the developer's confirmation, in the order given. A goal
    whose metric, direction, unit or guardrails are unknown is refused
    with the questions to ask (``goal_unknown``); a goal is confirmed
    only after its workload (``workload_not_confirmed``).
``goal WORKLOAD [--metric M] [--direction D] [--unit U] [--bar K:V]``
    Say what faster means for a workload. Fields not given stay as
    they were, or unknown. The target class's default guardrails are
    filled (and shown) when the metric is known, as ``proposed``; add
    ``--confirm`` to confirm the goal in the same step. ``--bar`` is
    ``ratio:1.2`` or ``absolute:0.5``; without one the default bar
    applies and is named so (see `perfproof.project.goals`).
``constraint add --kind K --subject S``
    State a constraint (``public_api``, ``numeric_tolerance`` with
    ``--rtol`` and ``--atol``, ``target_hardware``,
    ``forbidden_dependency``, ``protected_file``).
``retire ID --reason R [--cascade]``
    Retire an entry; nothing is deleted.
``check [--probe]``
    Mark entries stale when a trigger has fired and derive each stale
    entry again (revalidation). ``--probe`` also runs each confirmed
    workload's command once to catch a failing command or a changed
    output shape.
``history [ID]``
    The history lines, oldest first.

``--json`` result
-----------------
Every action returns ``{"action", ...}``. Entries are the JSON dumps of
the `perfproof.schema` entry models. ``confirm``, ``goal``,
``constraint`` and ``retire`` return ``{"action", "records",
"entries"}``: the history events written (``{"event", "id",
"version", "status"}``) and the entries as they now are. ``check``
returns ``{"action", "marked_stale", "revalidated", "reproposed",
"unresolved", "unknown"}``. ``show`` returns ``{"action", "model",
"measurable", "questions"}``; ``measurable`` maps each workload ID to
``{"ok", "problems"}``.

Error codes
-----------
``no_model``, ``not_found``, ``invalid_entry``, ``invalid_model``,
``goal_unknown``, ``workload_not_confirmed``,
``stale_needs_revalidation``, ``retired_entry``,
``already_retired``, ``has_dependents``,
``guardrail_conflict``, ``duplicate``, ``inconsistent_model``,
``host_unknown``, ``not_a_repository``, ``state_write_failed``,
``lock_timeout``.
"""

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, get_args

from pydantic import ValidationError

from perfproof import __version__
from perfproof.commands import JsonValue, Output
from perfproof.commands._project import (
    as_command_error,
    default_session,
    repo_root,
)
from perfproof.project.entries import all_entries, check_consistency, find
from perfproof.project.errors import NotConfirmedError, ProjectError
from perfproof.project.goals import (
    ALIASES,
    parse_bar,
    parse_guardrail,
)
from perfproof.project.guard import current_host_id, require_confirmed
from perfproof.project.lifecycle import (
    GoalUpdate,
    add_constraint,
    confirm,
    retire,
    set_goal,
)
from perfproof.project.provenance import file_hashes, make_provenance
from perfproof.project.service import check_project
from perfproof.project.store import load_model, mutate, read_history
from perfproof.schema import (
    ConstraintEntry,
    ConstraintKind,
    Direction,
    FileHash,
    GoalEntry,
    HowToRunEntry,
    ProjectEntry,
    ProjectHistoryRecord,
    ProjectModel,
    Provenance,
)

__all__ = ["add_arguments", "run"]

type Mutation = tuple[ProjectModel, list[ProjectHistoryRecord]]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the ``project`` actions.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Show the project model; confirm, edit or retire entries; check "
        "which entries are stale."
    )
    actions = parser.add_subparsers(
        dest="action", required=True, metavar="ACTION"
    )

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub = actions.add_parser(name, help=help_text, description=help_text)
        sub.add_argument(
            "--repo",
            type=Path,
            default=Path(),
            help="any path in the repository (default: .)",
        )
        return sub

    show = add("show", "show the model")
    show.add_argument("entry", nargs="?", help="one entry ID")
    conf = add("confirm", "confirm entries the developer accepted")
    conf.add_argument("ids", nargs="+", metavar="ID")
    _session(conf)
    goal = add("goal", "say what faster means for a workload")
    goal.add_argument("workload")
    goal.add_argument("--metric", help="wall, cpu, p99, memory, ... or a name")
    goal.add_argument(
        "--direction", choices=get_args(Direction), help="for a custom metric"
    )
    goal.add_argument("--unit", help="for a metric whose name fixes none")
    goal.add_argument("--bar", metavar="KIND:VALUE", help="ratio:1.2")
    goal.add_argument(
        "--guardrail",
        action="append",
        default=[],
        metavar="METRIC=ALLOWANCE",
        help="override the default guardrails; repeatable",
    )
    goal.add_argument(
        "--this-machine",
        action="store_true",
        help="bind the goal to this machine",
    )
    goal.add_argument(
        "--confirm", action="store_true", help="confirm it as well"
    )
    _session(goal)
    cons = add("constraint", "state a constraint")
    verbs = cons.add_subparsers(dest="verb", required=True, metavar="VERB")
    new = verbs.add_parser("add", help="add a constraint")
    new.add_argument("--kind", required=True, choices=get_args(ConstraintKind))
    new.add_argument("--subject", required=True)
    new.add_argument("--rtol", type=float)
    new.add_argument("--atol", type=float)
    new.add_argument("--this-machine", action="store_true")
    new.add_argument("--confirm", action="store_true")
    new.add_argument(
        "--repo", type=Path, default=Path(), help=argparse.SUPPRESS
    )
    _session(new)
    ret = add("retire", "retire an entry")
    ret.add_argument("id")
    ret.add_argument("--reason", required=True)
    ret.add_argument("--cascade", action="store_true")
    check = add("check", "mark stale entries and revalidate them")
    check.add_argument("--probe", action="store_true")
    check.add_argument(
        "--probe-timeout", type=float, default=300.0, metavar="SECONDS"
    )
    hist = add("history", "show the history lines")
    hist.add_argument("entry", nargs="?", help="one entry ID")


def _session(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--session",
        help="session the developer confirmed in "
        "(default: $PERFPROOF_SESSION or a fresh ID)",
    )


def run(args: argparse.Namespace) -> Output:
    """Run one ``project`` action.

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
    root = repo_root(cast("Path", args.repo))
    action = cast("str", args.action)
    try:
        if action == "show":
            return _show(root, cast("str | None", args.entry))
        if action == "history":
            return _history(root, cast("str | None", args.entry))
        if action == "check":
            return _check(root, args)
        return _mutating(root, action, args)
    except NotConfirmedError as exc:
        raise as_command_error(exc) from exc
    except ProjectError as exc:
        raise as_command_error(exc) from exc


def _dump(entry: ProjectEntry) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", entry.model_dump(mode="json"))


def _event_rows(records: Sequence[ProjectHistoryRecord]) -> list[JsonValue]:
    return [
        {
            "event": r.event,
            "id": r.entry.id,
            "version": r.entry.version,
            "status": r.entry.status,
        }
        for r in records
    ]


def _require(root: Path) -> ProjectModel:
    model = load_model(root)
    if model is None:
        raise ProjectError(
            "no_model", "there is no project model: run `perfproof init`"
        )
    return model


def _questions(model: ProjectModel) -> list[dict[str, JsonValue]]:
    out: list[dict[str, JsonValue]] = []
    for g in model.goals:
        if g.status == "retired":
            continue
        unknown = sorted(n for n in g.missing if n != "bar")
        if unknown:
            out.append(
                {
                    "id": f"goal:{g.workload_id}",
                    "entry": g.id,
                    "text": (
                        f"What should get faster for {g.workload_id}? "
                        f"Unknown: {', '.join(unknown)}."
                    ),
                }
            )
    out.extend(
        {
            "id": f"how-to-run:{e.id}",
            "entry": e.id,
            "text": f"Unknown for {e.id}: {', '.join(sorted(e.missing))}.",
        }
        for e in model.how_to_run
        if e.status != "retired" and e.missing
    )
    if not model.constraints:
        out.append(
            {
                "id": "constraints",
                "entry": None,
                "text": "Anything that must not change (public APIs, "
                "numeric tolerance, off-limits dependencies, protected "
                "files)?",
            }
        )
    return out


def _measurable(root: Path, model: ProjectModel) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {}
    for w in model.workloads:
        if w.status == "retired":
            continue
        try:
            require_confirmed(root, w.id)
        except NotConfirmedError as exc:
            out[w.id] = {
                "ok": False,
                "problems": [p.as_json() for p in exc.problems],
            }
        else:
            out[w.id] = {"ok": True, "problems": []}
    return out


def _show(root: Path, entry_id: str | None) -> Output:
    model = _require(root)
    if entry_id is not None:
        entry = find(model, entry_id)
        if entry is None:
            raise ProjectError(
                "not_found", f"no entry {entry_id!r}", {"entry": entry_id}
            )
        return Output(
            result={"action": "show", "entry": _dump(entry)},
            text=_entry_text(entry),
        )
    measurable = _measurable(root, model)
    questions = _questions(model)
    payload: dict[str, JsonValue] = {
        "action": "show",
        "model": cast("dict[str, JsonValue]", model.model_dump(mode="json")),
        "measurable": measurable,
        "questions": cast("list[JsonValue]", questions),
    }
    lines = [f"Project model (updated {model.updated_at.isoformat()}):"]
    lines.extend(_entry_text(e) for e in all_entries(model))
    for wid, state in measurable.items():
        ok = cast("dict[str, JsonValue]", state)["ok"]
        lines.append(
            f"  {wid}: {'ready to measure' if ok else 'not measurable yet'}"
        )
    if questions:
        lines.append("Open questions:")
        lines.extend(f"  - {q['text']}" for q in questions)
    return Output(result=payload, text="\n".join(lines))


def _entry_text(e: ProjectEntry) -> str:
    extra = ""
    if e.status == "stale" and e.invalidation is not None:
        extra = f" ({e.invalidation.trigger}: {e.invalidation.detail})"
    elif e.status == "retired":
        extra = f" ({e.retired_reason})"
    if isinstance(e, GoalEntry):
        known = f"{e.metric or '?'} {e.bar.kind if e.bar else 'default bar'}"
        extra += f" [{known}]"
    elif isinstance(e, HowToRunEntry) and e.missing:
        extra += f" [unknown: {', '.join(sorted(e.missing))}]"
    return f"  {e.kind:<10} {e.id}  v{e.version}  {e.status}{extra}"


def _history(root: Path, entry_id: str | None) -> Output:
    model = _require(root)
    records, skipped = read_history(root)
    chosen = [r for r in records if entry_id in (None, r.entry.id)]
    rows = [
        {
            "recorded_at": r.recorded_at.isoformat(),
            "event": r.event,
            "id": r.entry.id,
            "version": r.entry.version,
            "status": r.entry.status,
            "note": r.note,
            "orphan": r.recorded_at > model.updated_at,
        }
        for r in chosen
    ]
    text = "\n".join(
        f"{r['recorded_at']}  {r['event']:<12} {r['id']} "
        f"v{r['version']} {r['status']}"
        + ("  [orphan: not in the model]" if r["orphan"] else "")
        + (f"  {r['note']}" if r["note"] else "")
        for r in rows
    )
    return Output(
        result={
            "action": "history",
            "records": cast("list[JsonValue]", rows),
            "skipped_lines": list(skipped),
        },
        text=text or "No history.",
    )


def _check(root: Path, args: argparse.Namespace) -> Output:
    result = check_project(
        root,
        now=datetime.now(UTC),
        version=__version__,
        host_id=current_host_id,
        probe=cast("bool", args.probe),
        probe_timeout=cast("float", args.probe_timeout),
    )
    marked: list[JsonValue] = [
        {"id": f.entry_id, "trigger": f.trigger, "detail": f.detail}
        for f in result.marked_stale
    ]
    unresolved: list[JsonValue] = [
        {"id": i, "why": w} for i, w in result.unresolved
    ]
    payload: dict[str, JsonValue] = {
        "action": "check",
        "marked_stale": marked,
        "revalidated": list(result.revalidated),
        "reproposed": list(result.reproposed),
        "unresolved": unresolved,
        "unknown": list(result.unknown),
    }
    lines: list[str] = []
    lines.extend(
        f"stale: {f.entry_id} ({f.trigger}: {f.detail})"
        for f in result.marked_stale
    )
    lines.extend(f"revalidated, no change: {i}" for i in result.revalidated)
    lines.extend(
        f"re-derived and different, needs confirmation: {i}"
        for i in result.reproposed
    )
    lines.extend(f"still stale: {i}: {w}" for i, w in result.unresolved)
    lines.extend(f"not checked: {u}" for u in result.unknown)
    return Output(result=payload, text="\n".join(lines) or "Nothing is stale.")


def _mutating(root: Path, action: str, args: argparse.Namespace) -> Output:
    now = datetime.now(UTC)
    session = cast("str | None", getattr(args, "session", None))
    session = session or default_session()
    chosen: list[ProjectHistoryRecord] = []

    def change(model: ProjectModel) -> Mutation:
        if not model.targets and not model.workloads:
            raise ProjectError(
                "no_model", "there is no project model: run `perfproof init`"
            )
        result = _apply(model, root, action, args, session, now)
        chosen.extend(result[1])
        check_consistency(result[0])
        return result

    model = mutate(root, change, now=lambda: now)
    touched = list(dict.fromkeys(r.entry.id for r in chosen))
    entries: list[JsonValue] = [
        _dump(e) for i in touched if (e := find(model, i)) is not None
    ]
    lines = [
        f"{r.event}: {r.entry.id} v{r.entry.version} -> {r.entry.status}"
        for r in chosen
    ] or ["No change."]
    return Output(
        result={
            "action": action,
            "records": _event_rows(chosen),
            "entries": entries,
        },
        text="\n".join(lines),
    )


def _machine() -> str:
    host = current_host_id()
    if host is None:
        raise ProjectError(
            "host_unknown", "this machine could not be identified"
        )
    return host


def _protected(
    root: Path, entry: ProjectEntry, now: datetime
) -> Provenance | None:
    """Return a protected file's current hash as a provenance."""
    if (
        isinstance(entry, ConstraintEntry)
        and entry.constraint == "protected_file"
    ):
        return _protected_provenance(root, entry.subject, now)
    return None


def _protected_provenance(
    root: Path, subject: str, now: datetime
) -> Provenance:
    try:
        FileHash(path=subject, sha256="0" * 64)
    except ValidationError as exc:
        raise ProjectError(
            "invalid_entry", f"{subject!r} is not a project-relative path"
        ) from exc
    if not (root / subject).is_file():
        raise ProjectError(
            "not_found",
            f"protected file {subject!r} does not exist",
            {"path": subject},
        )
    return make_provenance(file_hashes(root, [subject]), (), __version__, now)


def _apply(
    model: ProjectModel,
    root: Path,
    action: str,
    args: argparse.Namespace,
    session: str,
    now: datetime,
) -> Mutation:
    if action == "confirm":
        return confirm(
            model,
            cast("list[str]", args.ids),
            session,
            now,
            reprovenance=lambda e: _protected(root, e, now),
        )
    if action == "retire":
        return retire(
            model,
            cast("str", args.id),
            cast("str", args.reason),
            now,
            cascade=cast("bool", args.cascade),
        )
    if action == "goal":
        return _goal(model, args, session, now)
    return _constraint(model, root, args, session, now)


def _goal(
    model: ProjectModel,
    args: argparse.Namespace,
    session: str,
    now: datetime,
) -> Mutation:
    metric = cast("str | None", args.metric)
    try:
        bar = parse_bar(cast("str", args.bar)) if args.bar else None
        rails = tuple(
            parse_guardrail(g) for g in cast("list[str]", args.guardrail)
        )
    except ValueError as exc:
        raise ProjectError("invalid_entry", str(exc)) from exc
    update = GoalUpdate(
        metric=ALIASES.get(metric, metric) if metric else None,
        direction=cast("Direction | None", args.direction),
        unit=cast("str | None", args.unit),
        bar=bar,
        guardrails=rails or None,
        host_id=_machine() if args.this_machine else None,
    )
    workload = cast("str", args.workload)
    new, records = set_goal(model, workload, update, now, __version__)
    if args.confirm:
        new, more = confirm(new, [f"goal-{workload}"], session, now)
        records = [*records, *more]
    return new, records


def _constraint(
    model: ProjectModel,
    root: Path,
    args: argparse.Namespace,
    session: str,
    now: datetime,
) -> Mutation:
    kind = cast("ConstraintKind", args.kind)
    subject = cast("str", args.subject)
    provenance = (
        _protected_provenance(root, subject, now)
        if kind == "protected_file"
        else make_provenance((), (), __version__, now)
    )
    new, records = add_constraint(
        model,
        constraint=kind,
        subject=subject,
        rtol=cast("float | None", args.rtol),
        atol=cast("float | None", args.atol),
        host_id=_machine() if args.this_machine else None,
        provenance=provenance,
        now=now,
    )
    if args.confirm:
        created = next(r.entry.id for r in records if r.event == "created")
        new, more = confirm(new, [created], session, now)
        records = [*records, *more]
    return new, records
