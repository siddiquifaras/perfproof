"""The skill eval's task set: what to run and what counts as right.

The set lives in ``tasks.json`` next to this module so it is data, not
code. Each task names the repo to build (an example at baseline, plus
branches made from its candidates), the prompt, the generic replies to
send when the skill asks a question, and the expected outcome.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

TASKS_FILE = Path(__file__).resolve().parent / "tasks.json"
"""The committed task-set file."""

Kind = Literal["prove", "find", "trigger"]
FixKind = Literal["none", "branch", "optional_branch"]


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


class HotspotName(_Model):
    """One function, in one file, that counts as the known bottleneck.

    Attributes
    ----------
    file : str
        Path of the module holding it, relative to the repo root.
    function : str
        Short name, as a person writes it (``to_chars``,
        ``Echo.ServeHTTP``); matched against the names profilers print
        by `hotspot_names.names_match`.
    why : str
        Why this function stands for the bottleneck; required for an
        entry of ``Hotspot.accept``.
    """

    file: str
    function: str
    why: str = ""


class Hotspot(_Model):
    """The known bottleneck of an example.

    Attributes
    ----------
    file : str
        Path of the module holding it, relative to the repo root.
    function : str
        Name of the function the time goes to.
    top_n : int
        The function must be among the first ``top_n`` hotspots of a
        ``perfproof profile`` output in the transcript (1: it must
        rank first, as for the small examples; 5 for real
        repositories), and be named, without a negation, after it.
    accept : list of HotspotName
        Further functions that count as the bottleneck, for when the
        cost is not where the fix is edited: a function the compiler
        inlines into its caller, or a wrapper that runs per call while
        the named function runs once. Each carries its ``why``.
    """

    file: str
    function: str
    top_n: int = 1
    accept: list[HotspotName] = []

    @model_validator(mode="after")
    def _accepted_say_why(self) -> Hotspot:
        for extra in self.accept:
            if not extra.why:
                msg = f"accepted hotspot {extra.function!r} needs a why"
                raise ValueError(msg)
        return self

    def accepted(self) -> list[HotspotName]:
        """List every accepted function, the primary one first.

        Returns
        -------
        list of HotspotName
            The primary ``file`` and ``function``, then ``accept``.
        """
        return [
            HotspotName(file=self.file, function=self.function),
            *self.accept,
        ]


class RealRepo(_Model):
    """A public repository at a mined task's commit, built locally.

    The repository, commit, build steps and test command come from the
    task's manifest under ``eval/tasks`` and ``eval/mine/checks.json``,
    so there is one source for them.

    Attributes
    ----------
    manifest : str
        Manifest id, for example ``pydata-xarray-pr6107``.
    branch : str or None
        Prove tasks: name of the branch cut at the reference change;
        ``main`` is its parent. ``None`` for find tasks, which get the
        task commit alone and no history after it.
    requires : list of str
        Executables that must be on ``PATH`` (or provided by ``shim``)
        for the task to run; a missing one skips the task.
    shim : {"go", "ruby"} or None
        Toolchain the project runs in a container here: launchers for
        it are put on the run's ``PATH``, as the manifest's commands do.
    """

    manifest: str
    branch: str | None = None
    requires: list[str] = []
    shim: Literal["go", "ruby"] | None = None


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
    example : str or None
        Example directory the repo is built from.
    repo : RealRepo or None
        A real repository to build instead of an example.
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
    task_budget_usd : float or None
        Cap on the whole task: later calls get what is left, and none
        starts once it is spent. ``None`` leaves only the per-call cap.
    timeout_s : float or None
        Wall-time cap for each ``claude`` call; the runner default when
        ``None``.
    locked : list of str
        Files a change must not touch (harness, oracle, config).
    """

    id: str
    kind: Kind
    example: str | None = None
    repo: RealRepo | None = None
    branches: list[BranchSpec] = []
    prompt: str
    followups: list[str] = []
    max_turns: int | None = None
    budget_usd: float = 1.0
    task_budget_usd: float | None = None
    timeout_s: float | None = None
    locked: list[str] = []
    expect: Expect

    @model_validator(mode="after")
    def _one_source(self) -> Task:
        if (self.example is None) == (self.repo is None):
            msg = f"task {self.id}: set exactly one of example and repo"
            raise ValueError(msg)
        return self


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
