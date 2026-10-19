"""Entry-level building blocks for the lifecycle and staleness code.

Every function is pure: it takes a `ProjectModel` and returns a new
one. Entries are rebuilt through validation, never patched with
``model_copy``, so a status rule of ``perfproof.schema`` cannot be
bypassed.

History events and the statuses they may carry
----------------------------------------------
`perfproof.schema.ProjectHistoryRecord` fixes the statuses of five
events and leaves ``created`` and ``edited`` free. This package fixes
them in `EVENT_STATUS`:

``created``
    ``proposed`` only. An entry is born unconfirmed, whoever made it:
    perfproof's detection, or the developer adding a constraint. A
    developer's statement is then confirmed by a second line.
``edited``
    ``proposed`` only. A changed claim is a new revision nobody has
    confirmed yet, so an edit of a confirmed entry sends it back to
    ``proposed``. Nothing becomes ``confirmed`` except through a
    ``confirmed`` line.
"""

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Final, cast

from perfproof.project.errors import ProjectError
from perfproof.schema import (
    ConstraintEntry,
    GoalEntry,
    HistoryEvent,
    HowToRunEntry,
    Invalidation,
    ProjectEntry,
    ProjectHistoryRecord,
    ProjectModel,
    StaleTrigger,
    TargetEntry,
    WorkloadEntry,
)

__all__ = [
    "EVENT_STATUS",
    "all_entries",
    "check_consistency",
    "dependencies",
    "dependents",
    "find",
    "mark_stale",
    "put",
    "rebuild",
    "record",
    "with_fields",
]

EVENT_STATUS: Final[Mapping[str, frozenset[str]]] = {
    "created": frozenset({"proposed"}),
    "edited": frozenset({"proposed"}),
    "confirmed": frozenset({"confirmed"}),
    "marked_stale": frozenset({"stale"}),
    "reproposed": frozenset({"proposed"}),
    "retired": frozenset({"retired"}),
    "revalidated": frozenset({"proposed", "confirmed"}),
}
"""Statuses each history event may carry, as documented above."""

_FIELD: Final[Mapping[str, str]] = {
    "target": "targets",
    "workload": "workloads",
    "goal": "goals",
    "constraint": "constraints",
    "how_to_run": "how_to_run",
}


def all_entries(model: ProjectModel) -> Iterator[ProjectEntry]:
    """Yield every entry, targets first."""
    yield from model.targets
    yield from model.workloads
    yield from model.goals
    yield from model.constraints
    yield from model.how_to_run


def find(model: ProjectModel, entry_id: str) -> ProjectEntry | None:
    """Return the entry with this ID, or ``None``."""
    return next((e for e in all_entries(model) if e.id == entry_id), None)


def rebuild(model: ProjectModel, **fields: Sequence[Any]) -> ProjectModel:
    """Return the model with whole entry tuples replaced, validated.

    Parameters
    ----------
    model : ProjectModel
        The model to start from.
    **fields : Sequence
        Any of ``targets``, ``workloads``, ``goals``, ``constraints``,
        ``how_to_run``.

    Returns
    -------
    ProjectModel
        A validated copy; ``ValidationError`` if links break.
    """
    current: dict[str, Any] = {
        "updated_at": model.updated_at,
        **{f: getattr(model, f) for f in _FIELD.values()},
    }
    current.update({k: tuple(v) for k, v in fields.items()})
    return ProjectModel(**current)


def put(model: ProjectModel, entry: ProjectEntry) -> ProjectModel:
    """Add the entry, or replace the one with its ID."""
    name = _FIELD[entry.kind]
    existing = cast("tuple[ProjectEntry, ...]", getattr(model, name))
    if any(e.id == entry.id for e in existing):
        items = tuple(entry if e.id == entry.id else e for e in existing)
    else:
        items = (*existing, entry)
    return rebuild(model, **{name: items})


def with_fields[E: ProjectEntry](entry: E, **updates: object) -> E:
    """Rebuild an entry with changed fields, running every validator.

    Parameters
    ----------
    entry : ProjectEntry
        The entry.
    **updates : object
        Fields to change.

    Returns
    -------
    ProjectEntry
        A new entry of the same class.
    """
    data = {name: getattr(entry, name) for name in type(entry).model_fields}
    data.update(updates)
    return cast("E", type(entry).model_validate(data))


def record(
    event: HistoryEvent,
    entry: ProjectEntry,
    now: datetime,
    note: str = "",
) -> ProjectHistoryRecord:
    """Build a history line, enforcing `EVENT_STATUS`.

    Raises
    ------
    ProjectError
        ``internal_error`` if the status is not one the event may
        carry; a bug in the caller, never a user error.
    """
    if entry.status not in EVENT_STATUS[event]:
        msg = f"a {event} line cannot hold a {entry.status} entry"
        raise ProjectError("internal_error", msg)
    return ProjectHistoryRecord(
        recorded_at=now, event=event, entry=entry, note=note
    )


def dependents(model: ProjectModel, entry: ProjectEntry) -> list[ProjectEntry]:
    """Return the live entries that depend on ``entry``.

    The edges, documented in ``docs/discovery.md``:

    - a workload depends on its target;
    - a goal and a workload-bound how-to-run depend on their workload;
    - a workload that has a frozen spec (``spec_hash``) depends on its
      goals and on every constraint, because the spec was built from
      them.

    Retired entries are never returned.
    """
    live: list[ProjectEntry]
    if isinstance(entry, TargetEntry):
        live = [w for w in model.workloads if w.target_id == entry.id]
    elif isinstance(entry, WorkloadEntry):
        live = [g for g in model.goals if g.workload_id == entry.id]
        live += [h for h in model.how_to_run if h.workload_id == entry.id]
    elif isinstance(entry, GoalEntry):
        live = [
            w
            for w in model.workloads
            if w.id == entry.workload_id and w.spec_hash is not None
        ]
    elif isinstance(entry, ConstraintEntry):
        live = [w for w in model.workloads if w.spec_hash is not None]
    else:
        live = []
    return [e for e in live if e.status != "retired"]


def dependencies(
    model: ProjectModel, entry: ProjectEntry
) -> list[ProjectEntry]:
    """Return the live entries ``entry`` depends on."""
    live: list[ProjectEntry] = []
    if isinstance(entry, WorkloadEntry):
        live = [t for t in model.targets if t.id == entry.target_id]
        if entry.spec_hash is not None:
            live += [g for g in model.goals if g.workload_id == entry.id]
            live += list(model.constraints)
    elif isinstance(entry, (GoalEntry, HowToRunEntry)) and entry.workload_id:
        live = [w for w in model.workloads if w.id == entry.workload_id]
    return [e for e in live if e.status != "retired"]


def mark_stale(
    entry: ProjectEntry,
    trigger: StaleTrigger,
    detail: str,
    now: datetime,
) -> ProjectEntry:
    """Return ``entry`` stale, remembering where it came from.

    Parameters
    ----------
    entry : ProjectEntry
        A ``proposed`` or ``confirmed`` entry.
    trigger : StaleTrigger
        Which invalidation fired.
    detail : str
        What changed.
    now : datetime
        When it was noticed.

    Raises
    ------
    ProjectError
        ``internal_error`` for an entry that is already stale or
        retired.
    """
    if entry.status not in ("proposed", "confirmed"):
        msg = f"{entry.id} is {entry.status} and cannot become stale"
        raise ProjectError("internal_error", msg)
    return with_fields(
        entry,
        status="stale",
        previous_status=entry.status,
        invalidation=Invalidation(
            trigger=trigger, detail=detail, detected_at=now
        ),
    )


def check_consistency(model: ProjectModel) -> None:
    """Refuse a model that breaks a cross-entry rule.

    The rules, documented in ``docs/discovery.md``:

    1. A live (not retired) goal or how-to-run has a live workload, and
       a live workload has a live target: retiring an entry retires
       what depends on it, so a confirmed goal on a retired workload
       cannot exist.
    2. A workload has at most one live goal.
    3. A goal's guardrails never include its own metric: a metric is the
       primary or a guardrail, never both.

    A confirmed goal whose workload is not confirmed is allowed to
    exist, because a workload goes stale on its own; the guard refuses
    to measure it until the workload is revalidated.

    Raises
    ------
    ProjectError
        ``inconsistent_model`` naming the entries.
    """
    live_targets = {t.id for t in model.targets if t.status != "retired"}
    live_workloads = {w.id for w in model.workloads if w.status != "retired"}
    problems: list[str] = []
    problems.extend(
        f"workload {w.id} is live on a retired target"
        for w in model.workloads
        if w.status != "retired" and w.target_id not in live_targets
    )
    seen: set[str] = set()
    for g in model.goals:
        if g.status == "retired":
            continue
        if g.workload_id not in live_workloads:
            problems.append(f"goal {g.id} is live on a retired workload")
        if g.workload_id in seen:
            problems.append(f"workload {g.workload_id} has two live goals")
        seen.add(g.workload_id)
        if g.metric is not None and any(
            r.metric == g.metric for r in g.guardrails or ()
        ):
            problems.append(f"goal {g.id} guards its own metric {g.metric}")
    problems.extend(
        f"{h.id} is live on a retired workload"
        for h in model.how_to_run
        if h.status != "retired"
        and h.workload_id is not None
        and h.workload_id not in live_workloads
    )
    if problems:
        raise ProjectError(
            "inconsistent_model", "; ".join(problems), {"problems": problems}
        )
