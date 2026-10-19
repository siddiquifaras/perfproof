"""The skill eval's task set: what to run and what counts as right.

The set lives in ``tasks.json`` next to this module so it is data, not
code. Each task names the repo to build (an example at baseline, plus
branches made from its candidates), the prompt, the generic replies to
send when the skill asks a question, and the expected outcome.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

TASKS_FILE = Path(__file__).resolve().parent / "tasks.json"
"""The committed task-set file."""

Kind = Literal["prove", "find", "trigger"]
FixKind = Literal["none", "branch"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BranchSpec(_Model):
    """A branch to seed in the task repo from an example candidate.

    Attributes
    ----------
    name : str
        Branch name, cut from the baseline commit.
    candidate : str
        Directory under ``examples/<example>/candidates/``.
    message : str
        Commit message, which is also the change's claim.
    """

    name: str
    candidate: str
    message: str


class Hotspot(_Model):
    """The known bottleneck of an example.

    Attributes
    ----------
    file : str
        Path of the module holding it, relative to the repo root.
    function : str
        Name of the function the time goes to.
    """

    file: str
    function: str


class Expect(_Model):
    """What a correct run looks like.

    Attributes
    ----------
    trigger : bool
        Whether the skill should start on the prompt.
    outcomes : list of str
        Accepted ``verdict.outcome`` values of the last compare. An
        empty list means no compare is expected. ``"NO VERDICT"`` is
        accepted only when listed.
    reason : str or None
        Required ``verdict.reason`` when the outcome is ``REJECT``.
    gate : str or None
        Required ``verdict.gate`` when the outcome is ``REJECT``.
    hotspot : Hotspot or None
        The bottleneck the run must name, if it should find one.
    fix : {"none", "branch"}
        ``"branch"``: a change to the hotspot file is kept on a
        ``perfproof/*`` branch, passes the example's tests and leaves
        the locked files alone. ``"none"``: the repo's code ends as it
        started.
    """

    trigger: bool = True
    outcomes: list[str] = []
    reason: str | None = None
    gate: str | None = None
    hotspot: Hotspot | None = None
    fix: FixKind = "none"


class Task(_Model):
    """One run of the skill.

    Attributes
    ----------
    id : str
        Unique, also the run directory name.
    kind : {"prove", "find", "trigger"}
        What the task exercises.
    example : str
        Example directory the repo is built from.
    branches : list of BranchSpec
        Branches to seed, each one commit on the baseline.
    prompt : str
        The user's first message.
    followups : list of str
        Generic replies, sent in order while the skill is asking a
        question.
    max_turns : int or None
        ``--max-turns`` for the run; ``None`` leaves it unlimited.
    budget_usd : float
        ``--max-budget-usd`` for each ``claude`` call.
    locked : list of str
        Files a change must not touch (harness, oracle, config).
    """

    id: str
    kind: Kind
    example: str
    branches: list[BranchSpec] = []
    prompt: str
    followups: list[str] = []
    max_turns: int | None = None
    budget_usd: float = 1.0
    locked: list[str] = []
    expect: Expect


class TaskSet(_Model):
    """The whole file.

    Attributes
    ----------
    model : str
        Model passed to ``claude --model``.
    tasks : list of Task
        The tasks, in run order.
    """

    model: str
    tasks: list[Task]


def load(path: Path = TASKS_FILE) -> TaskSet:
    """Read and validate a task-set file.

    Parameters
    ----------
    path : Path, optional
        The JSON file; the committed one by default.

    Returns
    -------
    TaskSet
        The validated set.

    Raises
    ------
    pydantic.ValidationError
        If the file does not match the schema.
    ValueError
        If two tasks share an id.
    """
    tasks = TaskSet.model_validate_json(path.read_text(encoding="utf-8"))
    ids = [t.id for t in tasks.tasks]
    if len(ids) != len(set(ids)):
        msg = "duplicate task ids in " + str(path)
        raise ValueError(msg)
    return tasks
