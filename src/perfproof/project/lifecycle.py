"""Changes to the project model: propose, confirm, edit and retire.

Every function takes a `ProjectModel` and returns the new model with
the history lines of the change; none touches the disk, and each
raises `ProjectError` to refuse, so a refused change writes nothing
(`perfproof.project.store.mutate` runs them in one transaction).

Rules decided here, each documented in ``docs/discovery.md``:

- **Version.** An entry's ``version`` bumps exactly when its claim (see
  `perfproof.project.provenance.claim`) changes. Confirming, going
  stale, being revalidated unchanged, being retired and a refreshed
  provenance move the status or the provenance only.
- **History statuses.** ``created`` and ``edited`` lines carry
  ``proposed`` entries only (`perfproof.project.entries.EVENT_STATUS`).
- **Consistency.** `perfproof.project.entries.check_consistency` runs
  after every change here.
- **Dependents.** A change of claim to an entry makes the confirmed
  entries that depend on it stale with ``dependency_edited``
  (`perfproof.project.entries.dependents`).
- **Unknowns.** A goal field the developer has not given stays
  ``None`` with a reason; confirming a goal with an unknown metric,
  direction, unit or guardrails is refused with the questions to ask.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast

from pydantic import ValidationError

from perfproof.project.detect import Proposal
from perfproof.project.entries import (
    all_entries,
    check_consistency,
    dependents,
    find,
    mark_stale,
    put,
    record,
    with_fields,
)
from perfproof.project.errors import ProjectError
from perfproof.project.goals import KNOWN_METRICS, default_guardrails
from perfproof.project.provenance import claim, make_provenance
from perfproof.schema import (
    Confirmation,
    ConstraintEntry,
    ConstraintKind,
    Direction,
    GoalBar,
    GoalEntry,
    GuardrailSpec,
    ProjectEntry,
    ProjectHistoryRecord,
    ProjectModel,
    Provenance,
    TargetEntry,
    WorkloadEntry,
)

__all__ = [
    "GoalUpdate",
    "Merge",
    "add_constraint",
    "add_workload",
    "bind_spec",
    "claim_changed",
    "confirm",
    "propagate",
    "propose",
    "retire",
    "set_goal",
]

type Change = tuple[ProjectModel, list[ProjectHistoryRecord]]
_DERIVED: Final = ("target", "workload", "how_to_run")
_UNSAID: Final = "the developer has not said"
_GOAL_FIELDS: Final = ("metric", "direction", "unit", "bar", "guardrails")


@dataclass(frozen=True)
class Merge:
    """Result of merging a proposal into a model.

    Attributes
    ----------
    model : ProjectModel
        The model with the new entries.
    records : list of ProjectHistoryRecord
        One ``created`` line per added entry.
    added : tuple of str
        IDs added.
    skipped : tuple of tuple of str
        ``(id, reason)`` for every proposed entry not added.
    """

    model: ProjectModel
    records: list[ProjectHistoryRecord]
    added: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]


def claim_changed(old: ProjectEntry, new: ProjectEntry) -> bool:
    """Return whether two revisions differ in their claim."""
    return claim(old) != claim(new)


def _refuse_invalid(exc: ValidationError, what: str) -> ProjectError:
    return ProjectError(
        "invalid_entry", f"{what} is not valid: {exc}", {"what": what}
    )


def propose(model: ProjectModel, proposal: Proposal, now: datetime) -> Merge:
    """Add what a proposal found that the model does not have yet.

    An entry whose ID exists in any status, retired included, is left
    alone and reported: re-running ``init`` never overwrites a
    confirmation or revives something the developer retired.

    Parameters
    ----------
    model : ProjectModel
        Current model.
    proposal : Proposal
        Output of `perfproof.project.detect.detect`.
    now : datetime
        Time of the ``created`` lines.

    Returns
    -------
    Merge
        The merged model and what was added or skipped.
    """
    records: list[ProjectHistoryRecord] = []
    added: list[str] = []
    skipped: list[tuple[str, str]] = []

    def live(entry_id: str) -> bool:
        found = find(model, entry_id)
        return found is not None and found.status != "retired"

    ordered: Sequence[ProjectEntry] = (
        *proposal.targets,
        *proposal.workloads,
        *proposal.goals,
        *proposal.how_to_run,
    )
    for entry in ordered:
        existing = find(model, entry.id)
        if existing is not None:
            skipped.append((entry.id, f"already {existing.status}"))
            continue
        if isinstance(entry, WorkloadEntry) and not live(entry.target_id):
            skipped.append((entry.id, f"target {entry.target_id} is retired"))
            continue
        if isinstance(entry, GoalEntry) and (
            not live(entry.workload_id)
            or any(
                g.workload_id == entry.workload_id and g.status != "retired"
                for g in model.goals
            )
        ):
            skipped.append((entry.id, "its workload has a goal or is retired"))
            continue
        model = put(model, entry)
        caveat = proposal.caveats.get(entry.id, "")
        records.append(record("created", entry, now, caveat))
        added.append(entry.id)
    check_consistency(model)
    return Merge(model, records, tuple(added), tuple(skipped))


def add_workload(
    model: ProjectModel,
    *,
    name: str,
    target_id: str,
    command: tuple[str, ...],
    cwd: str,
    spec_hash: str,
    evidence: str,
    replace: bool,
    version: str,
    now: datetime,
) -> Change:
    """Register a workload the agent defined as a benchmark spec.

    The entry is ``proposed`` with source ``agent_defined``: the
    developer confirms it like any other (`confirm`). It records the
    spec's command, working directory and ``spec_hash``, and no files
    (the spec's lock manifest pins the harness), so a changed spec is
    caught by the guard's spec check, not by a file hash.

    Parameters
    ----------
    model : ProjectModel
        Current model.
    name : str
        The benchmark name, which becomes the workload ID.
    target_id : str
        A live target.
    command : tuple of str
        The spec's command, as an argv list.
    cwd : str
        The spec's working directory, relative to the project.
    spec_hash : str
        `BenchmarkSpec.spec_hash` of the spec being registered.
    evidence : str
        Where the workload came from, in words; kept in the history
        line's note.
    replace : bool
        Allow replacing a workload of this name that has another spec
        or target, or that was retired.
    version : str
        Perfproof version, recorded in the provenance.
    now : datetime
        Time of the change.

    Returns
    -------
    tuple of ProjectModel and list of ProjectHistoryRecord
        No line when the same spec is already registered for the same
        target. A replacement is an ``edited`` line, and makes the
        confirmed goal that depends on it stale.

    Raises
    ------
    ProjectError
        ``not_found`` for a missing target, ``retired_entry`` for a
        retired one, ``duplicate`` for another kind of entry with this
        ID, or a workload that needs ``replace``.
    """
    target = find(model, target_id)
    if not isinstance(target, TargetEntry):
        raise ProjectError(
            "not_found",
            f"no target {target_id!r} in the project model",
            {"target": target_id},
        )
    if target.status == "retired":
        raise ProjectError(
            "retired_entry",
            f"target {target_id} is retired",
            {"entry": target_id},
        )
    existing = find(model, name)
    if existing is not None and not isinstance(existing, WorkloadEntry):
        raise ProjectError(
            "duplicate",
            f"{name!r} is already a {existing.kind} in the project model",
            {"entry": name},
        )
    if (
        existing is not None
        and existing.status != "retired"
        and existing.spec_hash == spec_hash
        and existing.target_id == target_id
    ):
        return model, []
    if existing is not None and not replace:
        why = (
            "retired"
            if existing.status == "retired"
            else "a workload with another spec or target"
        )
        raise ProjectError(
            "duplicate",
            f"{name!r} is already {why}: pass --replace to register this "
            "spec instead",
            {"entry": name},
        )
    try:
        entry = WorkloadEntry(
            id=name,
            version=1 if existing is None else existing.version + 1,
            status="proposed",
            provenance=make_provenance((), (command,), version, now),
            target_id=target_id,
            source="agent_defined",
            command=command,
            cwd=cwd,
            spec_hash=spec_hash,
        )
    except ValidationError as exc:
        raise _refuse_invalid(exc, f"workload {name}") from exc
    model = put(model, entry)
    records = [
        record(
            "created" if existing is None else "edited", entry, now, evidence
        )
    ]
    model, extra = propagate(model, entry, now, "was replaced")
    check_consistency(model)
    return model, [*records, *extra]


def bind_spec(
    model: ProjectModel,
    workload_id: str,
    spec_hash: str,
    session: str,
    now: datetime,
) -> Change:
    """Bind a confirmed workload with no frozen spec to its benchmark.

    ``compare`` keeps a KEEP for a workload confirmed on a spec, so a
    workload confirmed before its benchmark was defined is confirmed
    again here, now naming the spec: a new revision (the claim gains
    ``spec_hash``) with a new ``confirmed`` line. Its dependents are not
    made stale: nothing they were derived from changed.

    Parameters
    ----------
    model : ProjectModel
        Current model.
    workload_id : str
        A ``confirmed`` workload with no ``spec_hash``.
    spec_hash : str
        `BenchmarkSpec.spec_hash` of its benchmark.
    session : str
        Session the developer confirmed in.
    now : datetime
        Time of the confirmation.

    Returns
    -------
    tuple of ProjectModel and list of ProjectHistoryRecord
        No line when the entry is not a confirmed, unbound workload.
    """
    entry = find(model, workload_id)
    if (
        not isinstance(entry, WorkloadEntry)
        or entry.status != "confirmed"
        or entry.spec_hash is not None
    ):
        return model, []
    bound = with_fields(
        entry,
        spec_hash=spec_hash,
        version=entry.version + 1,
        confirmation=Confirmation(session=session, confirmed_at=now),
    )
    return put(model, bound), [
        record("confirmed", bound, now, f"bound to spec {spec_hash[:12]}")
    ]


def propagate(
    model: ProjectModel,
    changed: ProjectEntry,
    now: datetime,
    why: str,
    skip: frozenset[str] = frozenset(),
) -> Change:
    """Make the confirmed dependents of a changed entry stale.

    Parameters
    ----------
    model : ProjectModel
        Model that already holds ``changed``.
    changed : ProjectEntry
        The entry whose claim changed or that was retired.
    now : datetime
        When it was noticed.
    why : str
        What happened to it, for the invalidation's detail.
    skip : frozenset of str, optional
        IDs not to touch, such as entries being retired together.

    Returns
    -------
    tuple of ProjectModel and list of ProjectHistoryRecord
        The model and one ``marked_stale`` line per dependent.
    """
    records: list[ProjectHistoryRecord] = []
    for dep in dependents(model, changed):
        if dep.status != "confirmed" or dep.id in skip:
            continue
        stale = mark_stale(
            dep, "dependency_edited", f"{changed.id} {why}", now
        )
        model = put(model, stale)
        records.append(record("marked_stale", stale, now))
    return model, records


def _confirmed(
    model: ProjectModel,
    entry: ProjectEntry,
    session: str,
    now: datetime,
    reprovenance: Callable[[ProjectEntry], Provenance | None] | None,
) -> ProjectEntry | None:
    """Return ``entry`` confirmed, ``None`` if it already is.

    Raises
    ------
    ProjectError
        ``retired_entry``, ``stale_needs_revalidation``,
        ``goal_unknown`` or ``workload_not_confirmed``.
    """
    if entry.status == "retired":
        raise ProjectError(
            "retired_entry", f"{entry.id} is retired", {"entry": entry.id}
        )
    if entry.status == "confirmed":
        return None
    if entry.status == "stale" and entry.kind in _DERIVED:
        raise ProjectError(
            "stale_needs_revalidation",
            f"{entry.id} is stale: run `perfproof project check` so it is "
            "derived again, then confirm the result",
            {"entry": entry.id},
        )
    if entry.status == "stale" and entry.invalidation is not None:
        _check_stale_confirmable(entry, entry.invalidation.trigger)
    if isinstance(entry, GoalEntry):
        _check_goal_confirmable(model, entry)
    if isinstance(entry, WorkloadEntry):
        target = find(model, entry.target_id)
        if target is None or target.status != "confirmed":
            status = "missing" if target is None else target.status
            raise ProjectError(
                "target_not_confirmed",
                f"workload {entry.id} needs target {entry.target_id} "
                f"confirmed first (it is {status})",
                {"entry": entry.id, "target": entry.target_id},
            )
    fresh = reprovenance(entry) if reprovenance else None
    return with_fields(
        entry,
        status="confirmed",
        confirmation=Confirmation(session=session, confirmed_at=now),
        invalidation=None,
        previous_status=None,
        **({"provenance": fresh} if fresh is not None else {}),
    )


def _check_stale_confirmable(entry: ProjectEntry, trigger: str) -> None:
    """Refuse a confirmation that cannot clear what made it stale."""
    if trigger == "machine_differs":
        if isinstance(entry, GoalEntry):
            hint = (
                f"set it again on this machine: `perfproof project goal "
                f"{entry.workload_id} --this-machine`"
            )
        else:
            hint = "retire it and state it again for this machine"
        raise ProjectError(
            "stale_needs_edit",
            f"{entry.id} is bound to another machine, so confirming it "
            f"cannot make it current: {hint}",
            {"entry": entry.id, "trigger": trigger},
        )


def _check_goal_confirmable(model: ProjectModel, goal: GoalEntry) -> None:
    unknown = [n for n in _GOAL_FIELDS if getattr(goal, n) is None]
    if goal.bar is None and "bar" in unknown:
        unknown.remove("bar")
    if unknown:
        raise ProjectError(
            "goal_unknown",
            f"goal {goal.id} has unknown {', '.join(unknown)}: ask the "
            "developer, never default them",
            {"entry": goal.id, "unknown": unknown},
        )
    workload = find(model, goal.workload_id)
    awaiting = (
        workload is not None
        and workload.status == "stale"
        and workload.invalidation is not None
        and workload.invalidation.trigger == "dependency_edited"
    )
    if workload is None or (workload.status != "confirmed" and not awaiting):
        status = "missing" if workload is None else workload.status
        raise ProjectError(
            "workload_not_confirmed",
            f"goal {goal.id} needs workload {goal.workload_id} confirmed "
            f"first (it is {status})",
            {"entry": goal.id, "workload": goal.workload_id},
        )


def confirm(
    model: ProjectModel,
    ids: Sequence[str],
    session: str,
    now: datetime,
    reprovenance: Callable[[ProjectEntry], Provenance | None] | None = None,
) -> Change:
    """Record the developer's confirmation of entries, in order.

    A goal's ``bar`` may stay unknown (the default bar applies, see
    `perfproof.project.goals.bar_label`); its metric, direction, unit
    and guardrails may not.

    Parameters
    ----------
    model : ProjectModel
        Current model.
    ids : Sequence of str
        Entry IDs. A goal is confirmed after its workload when both
        are named.
    session : str
        Session the developer confirmed in.
    now : datetime
        Confirmation time, with time zone.
    reprovenance : callable, optional
        Returns a fresh provenance for an entry being confirmed (the
        file hashes of a protected file that changed), or ``None``.

    Returns
    -------
    tuple of ProjectModel and list of ProjectHistoryRecord
        An entry that is already confirmed produces no line.

    Raises
    ------
    ProjectError
        ``not_found``, or one of the refusals of confirming one entry.
    """
    records: list[ProjectHistoryRecord] = []
    for entry_id in dict.fromkeys(ids):
        entry = find(model, entry_id)
        if entry is None:
            raise ProjectError(
                "not_found", f"no entry {entry_id!r}", {"entry": entry_id}
            )
        confirmed = _confirmed(model, entry, session, now, reprovenance)
        if confirmed is None:
            continue
        model = put(model, confirmed)
        records.append(record("confirmed", confirmed, now))
    check_consistency(model)
    return model, records


@dataclass(frozen=True)
class GoalUpdate:
    """What the developer said about a goal; ``None`` means not said.

    Attributes
    ----------
    metric : str or None
        Metric name, an alias of `perfproof.project.goals.ALIASES`
        already resolved.
    direction, unit : optional
        Needed when the metric is not one whose name fixes them.
    bar : GoalBar or None
        The bar.
    guardrails : tuple of GuardrailSpec or None
        Project-chosen guardrails; ``None`` keeps or defaults them.
    host_id : str or None
        Set when the goal is bound to this machine.
    """

    metric: str | None = None
    direction: Direction | None = None
    unit: str | None = None
    bar: GoalBar | None = None
    guardrails: tuple[GuardrailSpec, ...] | None = None
    host_id: str | None = None


def set_goal(
    model: ProjectModel,
    workload_id: str,
    update: GoalUpdate,
    now: datetime,
    version: str,
) -> Change:
    """Create or edit the goal of a workload.

    Fields not in ``update`` keep their value, or stay unknown with a
    reason. When the metric is known and no guardrails are known, the
    target class's default guardrails (``docs/method.md``) are filled
    with source ``default`` and the metric itself left out; they are
    shown to the developer and confirmed with the goal. The result is
    ``proposed``; confirm it with `confirm`.

    Parameters
    ----------
    model : ProjectModel
        Current model.
    workload_id : str
        A live workload.
    update : GoalUpdate
        What was said.
    now : datetime
        Time of the revision.
    version : str
        Perfproof version, recorded in a new goal's provenance.

    Returns
    -------
    tuple of ProjectModel and list of ProjectHistoryRecord
        A ``created`` or ``edited`` line, plus ``marked_stale`` lines
        for confirmed dependents. Nothing changes when the claim does
        not.

    Raises
    ------
    ProjectError
        ``not_found``, ``invalid_entry``, or ``guardrail_conflict``.
    """
    workload = find(model, workload_id)
    if not isinstance(workload, WorkloadEntry) or workload.status == "retired":
        raise ProjectError(
            "not_found",
            f"no live workload {workload_id!r}",
            {"workload": workload_id},
        )
    old = next(
        (
            g
            for g in model.goals
            if g.workload_id == workload_id and g.status != "retired"
        ),
        None,
    )
    metric = update.metric or (old.metric if old else None)
    changed_metric = update.metric is not None and (
        old is None or update.metric != old.metric
    )
    known = KNOWN_METRICS.get(metric) if metric else None
    direction = update.direction or (
        known[0] if changed_metric and known else None
    )
    unit = update.unit or (known[1] if changed_metric and known else None)
    if old and not changed_metric:
        direction = direction or old.direction
        unit = unit or old.unit
    guardrails = update.guardrails
    if guardrails is None and old is not None:
        guardrails = old.guardrails
        if changed_metric and all(
            g.allowance_source == "default" for g in guardrails or ()
        ):
            # Defaults were derived from the old metric (which they
            # leave out); derive them again from the new one.
            guardrails = None
    if guardrails is not None and metric:
        if update.guardrails is not None and any(
            g.metric == metric for g in guardrails
        ):
            raise ProjectError(
                "guardrail_conflict",
                f"{metric} is the goal's metric and cannot also be a "
                "guardrail",
                {"metric": metric},
            )
        guardrails = tuple(g for g in guardrails if g.metric != metric)
    if guardrails is None and metric:
        guardrails = default_guardrails(
            cast("TargetEntry", find(model, workload.target_id)).target_class,
            metric,
        )
    bar = update.bar or (old.bar if old else None)
    values: dict[str, object] = {
        "metric": metric,
        "direction": direction,
        "unit": unit,
        "bar": bar,
        "guardrails": guardrails,
    }
    missing = {
        name: (
            f"the unit of {metric} is not fixed by its name"
            if name == "unit" and metric and known and known[1] is None
            else _UNSAID
        )
        for name, value in values.items()
        if value is None
    }
    host_id = update.host_id or (old.host_id if old else None)
    provenance: Provenance = (
        old.provenance if old else make_provenance((), (), version, now)
    )
    try:
        new = GoalEntry.model_validate(
            {
                "id": old.id if old else f"goal-{workload_id}",
                "workload_id": workload_id,
                "version": old.version if old else 1,
                "status": "proposed",
                "provenance": provenance,
                "host_id": host_id,
                "missing": missing,
                **values,
            }
        )
    except ValidationError as exc:
        raise _refuse_invalid(exc, "the goal") from exc
    if old is not None and not claim_changed(old, new):
        return model, []
    if old is not None:
        new = with_fields(
            new,
            version=old.version + 1,
            provenance=make_provenance((), (), version, now),
        )
    model = put(model, new)
    records = [record("edited" if old else "created", new, now)]
    model, extra = propagate(model, new, now, "was edited")
    check_consistency(model)
    return model, records + extra


def add_constraint(
    model: ProjectModel,
    *,
    constraint: ConstraintKind,
    subject: str,
    rtol: float | None,
    atol: float | None,
    host_id: str | None,
    provenance: Provenance,
    now: datetime,
) -> Change:
    """Add a constraint the developer stated, as ``proposed``.

    Parameters
    ----------
    model : ProjectModel
        Current model.
    constraint : ConstraintKind
        What sort of constraint.
    subject : str
        The API symbol, dependency, hardware or path.
    rtol, atol : float or None
        Tolerances of a ``numeric_tolerance``.
    host_id : str or None
        Machine of a ``target_hardware`` constraint.
    provenance : Provenance
        Files the constraint is about (a protected file's hash) and
        the perfproof version.
    now : datetime
        Time of the line.

    Returns
    -------
    tuple of ProjectModel and list of ProjectHistoryRecord
        A ``created`` line and ``marked_stale`` lines for confirmed
        dependents.

    Raises
    ------
    ProjectError
        ``invalid_entry`` for a constraint the schema refuses, or
        ``duplicate`` for one already live with the same kind and
        subject.
    """
    for c in model.constraints:
        if (
            c.status != "retired"
            and c.constraint == constraint
            and c.subject == subject
        ):
            raise ProjectError(
                "duplicate",
                f"{c.id} already states this {constraint}: {subject}",
                {"entry": c.id},
            )
    n = 1
    ids = {e.id for e in all_entries(model)}
    while f"constraint-{n}" in ids:
        n += 1
    try:
        new = ConstraintEntry(
            id=f"constraint-{n}",
            status="proposed",
            provenance=provenance,
            constraint=constraint,
            subject=subject,
            rtol=rtol,
            atol=atol,
            host_id=host_id,
        )
    except ValidationError as exc:
        raise _refuse_invalid(exc, "the constraint") from exc
    model = put(model, new)
    records = [record("created", new, now)]
    model, extra = propagate(model, new, now, "was added")
    check_consistency(model)
    return model, records + extra


def _children(model: ProjectModel, entry: ProjectEntry) -> list[ProjectEntry]:
    """Return the live entries that exist only for ``entry``."""
    if isinstance(entry, TargetEntry):
        return [
            w
            for w in model.workloads
            if w.target_id == entry.id and w.status != "retired"
        ]
    if isinstance(entry, WorkloadEntry):
        return [
            e
            for e in (*model.goals, *model.how_to_run)
            if e.workload_id == entry.id and e.status != "retired"
        ]
    return []


def retire(
    model: ProjectModel,
    entry_id: str,
    reason: str,
    now: datetime,
    *,
    cascade: bool = False,
) -> Change:
    """Retire an entry with a reason; nothing is deleted.

    Retiring a target or workload needs ``cascade`` when live entries
    depend on it (workloads of a target; goals and how-to-run of a
    workload); they are retired with it, each with its own line and a
    reason naming this one.

    Parameters
    ----------
    model : ProjectModel
        Current model.
    entry_id : str
        The entry.
    reason : str
        Why it no longer applies; not empty.
    now : datetime
        Time of the lines.
    cascade : bool, optional
        Retire its dependents too.

    Returns
    -------
    tuple of ProjectModel and list of ProjectHistoryRecord
        One ``retired`` line per entry retired, dependents first, plus
        ``marked_stale`` lines for confirmed spec dependents.

    Raises
    ------
    ProjectError
        ``not_found``, ``already_retired``, ``has_dependents`` or
        ``invalid_entry`` for an empty reason.
    """
    entry = find(model, entry_id)
    if entry is None:
        raise ProjectError(
            "not_found", f"no entry {entry_id!r}", {"entry": entry_id}
        )
    if entry.status == "retired":
        raise ProjectError(
            "already_retired", f"{entry_id} is already retired", {}
        )
    if not reason.strip():
        raise ProjectError("invalid_entry", "a retirement needs a reason")
    order: list[ProjectEntry] = []

    def collect(e: ProjectEntry) -> None:
        for child in _children(model, e):
            collect(child)
        order.append(e)

    collect(entry)
    retiring = frozenset(e.id for e in order)
    if len(order) > 1 and not cascade:
        names = [e.id for e in order[:-1]]
        raise ProjectError(
            "has_dependents",
            f"{entry_id} has live dependents {names}; retire them first or "
            "pass --cascade",
            {"dependents": names},
        )
    records: list[ProjectHistoryRecord] = []
    for e in order:
        why = reason if e is entry else f"{entry_id} retired: {reason}"
        retired = with_fields(
            e,
            status="retired",
            retired_reason=why,
            invalidation=None,
            previous_status=None,
        )
        model = put(model, retired)
        records.append(record("retired", retired, now))
        model, extra = propagate(
            model, retired, now, "was retired", skip=retiring
        )
        records += extra
    check_consistency(model)
    return model, records
