"""The refusal: nothing is measured against an unconfirmed workload.

`require_confirmed` is what ``compare`` and ``measure`` will call (after
T3.6) before they touch a workload. It fails closed: a workload, its
goal or a constraint that is ``proposed``, ``stale`` or ``retired``, a
goal that does not exist, an entry whose recorded files changed on
disk since it was derived (even if nobody has run ``project check``
yet), an entry bound to another machine, and a machine that cannot be
identified are all refusals, listed together in one
`NotConfirmedError`.

Wiring it in ``compare``
------------------------
After the spec is loaded and before the baseline is checked out::

    require_confirmed(root, name, spec_hash=spec.spec_hash)

``name`` is the benchmark name, which is the workload ID by the
convention of ``docs/cli.md`` (``bench define bench-tile``,
``compare bench-tile``). The call raises `NotConfirmedError`, a
`ProjectError` with code ``not_confirmed`` and the problems in
``details``; the command turns it into a `CommandError` with the same
code and details.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from perfproof.project.entries import find
from perfproof.project.errors import NotConfirmedError, Problem
from perfproof.project.goals import bar_label
from perfproof.project.staleness import find_stale
from perfproof.project.store import load_model
from perfproof.schema import (
    ConstraintEntry,
    EntryKind,
    GoalBar,
    GoalEntry,
    ProjectEntry,
    WorkloadEntry,
)

__all__ = ["ConfirmedWorkload", "current_host_id", "require_confirmed"]


@dataclass(frozen=True)
class ConfirmedWorkload:
    """A workload the project model backs for measurement.

    Attributes
    ----------
    workload : WorkloadEntry
        The confirmed workload.
    goal : GoalEntry
        Its confirmed goal.
    constraints : tuple of ConstraintEntry
        Every live constraint, all confirmed.
    """

    workload: WorkloadEntry
    goal: GoalEntry
    constraints: tuple[ConstraintEntry, ...]

    @property
    def bar(self) -> GoalBar | None:
        """The developer's bar, or ``None`` when none was stated."""
        return self.goal.bar

    def bar_label(self, min_effect: float) -> str:
        """Name the bar a comparison applies; see `bar_label`.

        Parameters
        ----------
        min_effect : float
            The benchmark spec's ``min_effect``.

        Returns
        -------
        str
            ``"goal bar (...)"`` or ``"default bar (min_effect ...)"``.
        """
        return bar_label(self.goal.bar, min_effect)


def current_host_id() -> str | None:
    """Return this machine's ``host_id``, or ``None`` if unknown."""
    from perfproof.machine import fingerprint

    return fingerprint().host_id


def _problem(entry: ProjectEntry, reason: str, detail: str) -> Problem:
    return Problem(entry.id, entry.kind, entry.status, reason, detail)


def require_confirmed(
    root: Path,
    workload_id: str,
    *,
    spec_hash: str | None = None,
    host_id: Callable[[], str | None] = current_host_id,
    revisions: dict[EntryKind, str] | None = None,
) -> ConfirmedWorkload:
    """Refuse unless the project model backs measuring this workload.

    Parameters
    ----------
    root : Path
        Repository root.
    workload_id : str
        The workload, which is the benchmark name.
    spec_hash : str, optional
        `BenchmarkSpec.spec_hash` about to be measured. Refused when
        the workload recorded a different frozen spec.
    host_id : callable, optional
        Returns this machine's host ID; called only when a used entry
        is bound to a machine.
    revisions : dict, optional
        Derivation revisions, for tests.

    Returns
    -------
    ConfirmedWorkload
        The workload, its goal and the constraints, all confirmed and
        unchanged on disk.

    Raises
    ------
    NotConfirmedError
        With every problem found.
    perfproof.project.ProjectError
        ``invalid_model`` for an unreadable model file.
    """
    model = load_model(root)
    if model is None:
        raise NotConfirmedError(
            workload_id,
            [
                Problem(
                    workload_id,
                    "workload",
                    None,
                    "missing",
                    "there is no project model: run `perfproof init`",
                )
            ],
        )
    problems: list[Problem] = []
    entry = find(model, workload_id)
    if not isinstance(entry, WorkloadEntry):
        raise NotConfirmedError(
            workload_id,
            [
                Problem(
                    workload_id,
                    "workload",
                    None,
                    "missing",
                    "no such workload in the project model",
                )
            ],
        )
    used: list[ProjectEntry] = [entry]
    target = find(model, entry.target_id)
    if target is None or target.status == "retired":
        problems.append(
            Problem(
                entry.target_id,
                "target",
                None,
                "missing",
                "the workload's target is missing or retired",
            )
        )
    else:
        used.append(target)
    goals = [
        g
        for g in model.goals
        if g.workload_id == workload_id and g.status != "retired"
    ]
    if not goals:
        problems.append(
            Problem(
                f"goal-{workload_id}",
                "goal",
                None,
                "missing",
                "no goal: ask the developer what should get faster",
            )
        )
    used += goals
    constraints = tuple(c for c in model.constraints if c.status != "retired")
    used += constraints
    problems.extend(
        _problem(
            e,
            "stale" if e.status == "stale" else "not_confirmed",
            _why(e),
        )
        for e in used
        if e.status != "confirmed"
    )
    if (
        spec_hash is not None
        and entry.spec_hash is not None
        and entry.spec_hash != spec_hash
    ):
        problems.append(
            _problem(
                entry,
                "spec_changed",
                f"it recorded spec {entry.spec_hash[:12]}, not "
                f"{spec_hash[:12]}",
            )
        )
    confirmed = {e.id for e in used if e.status == "confirmed"}
    bound = any(e.host_id is not None for e in used if e.id in confirmed)
    findings, unknown = find_stale(
        root,
        model,
        host_id=host_id() if bound else None,
        revisions=revisions,
        only=confirmed,
    )
    by_id = {e.id: e for e in used}
    for f in findings:
        reason = (
            "machine_differs"
            if f.trigger == "machine_differs"
            else "changed_since_confirmation"
        )
        problems.append(
            _problem(by_id[f.entry_id], reason, f"{f.trigger}: {f.detail}")
        )
    problems.extend(
        _problem(by_id[entry_id], "machine_unknown", text)
        for entry_id, text in unknown
    )
    if problems:
        raise NotConfirmedError(workload_id, problems)
    goal = goals[0]
    return ConfirmedWorkload(entry, goal, constraints)


def _why(entry: ProjectEntry) -> str:
    if entry.status == "stale" and entry.invalidation is not None:
        return (
            f"stale ({entry.invalidation.trigger}: "
            f"{entry.invalidation.detail}); run `perfproof project check`"
        )
    return f"{entry.status}: the developer has not confirmed it"
