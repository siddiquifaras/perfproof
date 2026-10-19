"""Typed errors of the project model."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["NotConfirmedError", "Problem", "ProjectError"]


class ProjectError(Exception):
    """A refusal or failure of a project model operation.

    Parameters
    ----------
    code : str
        Stable snake_case identifier; the commands pass it on as the
        error code.
    message : str
        What went wrong and what to do about it.
    details : Mapping of str to object, optional
        Structured context; values must be JSON-serializable.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Mapping[str, object] = {} if details is None else details


@dataclass(frozen=True)
class Problem:
    """Why one entry cannot be used for a measurement.

    Attributes
    ----------
    entry_id : str
        The entry, or the workload ID asked for when it has no entry.
    kind : str
        Entry kind, or ``"workload"`` for a missing one.
    status : str or None
        The entry's status; ``None`` when it does not exist.
    reason : str
        ``missing``, ``not_confirmed``, ``stale``, ``retired``,
        ``changed_since_confirmation``, ``spec_changed``,
        ``machine_differs`` or ``machine_unknown``.
    detail : str
        Words for the developer.
    """

    entry_id: str
    kind: str
    status: str | None
    reason: str
    detail: str

    def as_json(self) -> dict[str, str | None]:
        """Return the problem as a JSON-ready mapping."""
        return {
            "entry_id": self.entry_id,
            "kind": self.kind,
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
        }


class NotConfirmedError(ProjectError):
    """A measurement was refused: the project model does not back it.

    Raised by `perfproof.project.require_confirmed` when a workload,
    its goal or a constraint is unconfirmed, stale, retired, changed
    since it was confirmed, or bound to another machine. Nothing is
    measured against a workload that raises this.

    Parameters
    ----------
    workload_id : str
        The workload that was asked for.
    problems : Sequence of Problem
        Every reason found, not only the first.
    """

    def __init__(self, workload_id: str, problems: Sequence[Problem]) -> None:
        listed = "; ".join(
            f"{p.kind} {p.entry_id}: {p.detail}" for p in problems
        )
        super().__init__(
            "not_confirmed",
            f"workload {workload_id!r} cannot be measured: {listed}",
            {
                "workload": workload_id,
                "problems": [p.as_json() for p in problems],
            },
        )
        self.workload_id = workload_id
        self.problems = tuple(problems)
