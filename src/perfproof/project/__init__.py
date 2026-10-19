"""The project model: proposal, confirmation, staleness and history.

``perfproof init`` detects the project and proposes a model;
``perfproof project`` shows it, confirms, edits and retires entries,
and checks which are stale. The model is
``.perfproof/project.json``, its history ``project.history.jsonl``
(see `perfproof.project.store`). The contract is in
``docs/discovery.md``.

`require_confirmed` is the refusal ``compare`` and ``measure`` will
call (after T3.6): no measurement against a workload, goal or
constraint that is unconfirmed or stale.
"""

from perfproof.project.detect import Proposal, Question, detect
from perfproof.project.errors import NotConfirmedError, Problem, ProjectError
from perfproof.project.goals import bar_label
from perfproof.project.guard import (
    ConfirmedWorkload,
    current_host_id,
    require_confirmed,
)

__all__ = [
    "ConfirmedWorkload",
    "NotConfirmedError",
    "Problem",
    "ProjectError",
    "Proposal",
    "Question",
    "bar_label",
    "current_host_id",
    "detect",
    "require_confirmed",
]
