"""The task manifest schema, one JSON file per task under eval/tasks/.

`eval/README.md` documents these fields for readers; this module is
the definition the miner writes and `mine.py validate` checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

TASKS_DIR = Path(__file__).resolve().parents[1] / "tasks"

Family = Literal[
    "known-change", "published-benchmark", "hidden-fix", "nothing-to-keep"
]
TargetClass = Literal[
    "python", "native", "web", "serving", "gpu", "agentic", "java"
]
ChangeKind = Literal[
    "improvement",
    "after-landed",
    "reverted-no-gain",
    "reverted-breaking",
    "reverted-unknown-reason",
    "regression",
    "kernel-version",
    "benchmark-instance",
]
Status = Literal["candidate", "builds", "runs", "reference-fails", "failed"]
Split = Literal["development-candidate", "held-out", "development"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Link(_Model):
    """A source a manifest field was taken from.

    Attributes
    ----------
    url : str
        Where the evidence can be read.
    note : str
        What it shows, including any text matched by the miner.
    """

    url: str
    note: str


class Change(_Model):
    """A pull request, commit or benchmark instance.

    Attributes
    ----------
    kind : {"pull-request", "commit", "benchmark-instance"}
        What `url` points at.
    url : str
        The change on GitHub, or the benchmark's instance record.
    title : str
        Title or first line of the commit message, as returned by the
        API.
    commit : str or None
        The commit that contains the change: a pull request's merge
        commit, or the commit itself. None when the source has none.
    merged_at : str or None
        ISO 8601 merge or commit time from the API.
    files : list of str
        Paths the change touched, from the API.
    files_truncated : bool
        True if the API's listing limit may have cut `files` short.
    """

    kind: Literal["pull-request", "commit", "benchmark-instance"]
    url: str
    title: str
    commit: str | None
    merged_at: str | None
    files: list[str]
    files_truncated: bool = False


class Command(_Model):
    """A workload or oracle command and where it came from.

    Attributes
    ----------
    command : str or None
        The command, or None when none could be identified.
    source : str
        URL or description of where the command was found, or
        ``"unknown"``.
    verified : bool
        Whether the command was run here (see `Task.runs`).
    """

    command: str | None
    source: str
    verified: bool


class Run(_Model):
    """One command run on this machine while checking a task.

    Attributes
    ----------
    step : {"clone", "setup", "oracle", "workload"}
        Which part of the task the command checks.
    tree : {"task", "reference"}
        Whether it ran at the task commit or with the reference change.
    command : str
        The command as run.
    exit_code : int
        Its exit status.
    duration_s : float or None
        Wall time in seconds, or None where it is not recorded (the
        reference tree's workload of a held-out task).
    date : str
        ISO 8601 UTC time the run started.
    machine : str
        Platform description from `platform.platform()`.
    output_tail : str
        Last lines of its combined output.
    """

    step: Literal["clone", "setup", "oracle", "workload"]
    tree: Literal["task", "reference"]
    command: str
    exit_code: int
    duration_s: float | None
    date: str
    machine: str
    output_tail: str


class Task(_Model):
    """One evaluation task.

    Attributes
    ----------
    id : str
        Unique id, also the file name without ``.json``.
    repository : str
        ``owner/name`` on GitHub.
    stars : int or None
        Stargazer count at mining time, from `gh repo view`.
    pushed_at : str or None
        Last push time at mining time, from `gh repo view`.
    task_commit : str
        The commit an agent starts from.
    task_commit_basis : str
        How `task_commit` was obtained.
    reference_change : Change
        The known change the task is built around.
    revert : Change or None
        The pull request that reverted the change, if any.
    family : Family
        Task family from docs/evaluation.md.
    change_kind : ChangeKind
        What the reference change is, as classified by the miner.
    target_class : TargetClass
        Target class from docs/targets.md.
    workload : Command
        Command that exercises the change.
    oracle : Command
        Independent correctness check.
    evidence : list of Link
        Sources for every field above.
    audit : str or None
        Cross-machine audit result for published-benchmark tasks, or
        why none is recorded.
    status : Status
        ``candidate`` until it is checked here; then ``runs``,
        ``reference-fails`` (runs, but the reference tree's oracle or
        workload fails: a result for labelling), ``builds`` or
        ``failed`` (see `check`).
    status_reason : str
        What was or wasn't checked.
    runs : list of Run
        Commands run here to check the task.
    split : Split
        ``held-out`` or ``development`` when `draw` picked the task,
        else ``development-candidate``.
    mined_at : str
        When the miner produced the manifest.
    miner : str
        The miner command that produced it.
    """

    id: str
    repository: str
    stars: int | None
    pushed_at: str | None
    task_commit: str
    task_commit_basis: str
    reference_change: Change
    revert: Change | None
    family: Family
    change_kind: ChangeKind
    target_class: TargetClass
    workload: Command
    oracle: Command
    evidence: list[Link]
    audit: str | None
    status: Status
    status_reason: str
    runs: list[Run]
    split: Split
    mined_at: str
    miner: str


def task_path(task: Task) -> Path:
    """Return where a task's manifest lives.

    Parameters
    ----------
    task : Task
        The task.

    Returns
    -------
    Path
        ``eval/tasks/<family>/<id>.json``.
    """
    return TASKS_DIR / task.family / f"{task.id}.json"


def write_task(task: Task) -> Path:
    """Write a manifest, replacing any file with the same id.

    Parameters
    ----------
    task : Task
        The task to write.

    Returns
    -------
    Path
        The file written.
    """
    path = task_path(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(task.model_dump_json(indent=2) + "\n")
    return path


def load_tasks() -> list[Task]:
    """Read and validate every manifest under eval/tasks/.

    Returns
    -------
    list of Task
        Tasks sorted by file path.

    Raises
    ------
    pydantic.ValidationError
        If a manifest does not match the schema.
    ValueError
        If a file name does not match its task id or family.
    """
    tasks: list[Task] = []
    for path in sorted(TASKS_DIR.glob("*/*.json")):
        task = Task.model_validate_json(path.read_text())
        if path != task_path(task):
            msg = f"{path} holds task {task.id} of family {task.family}"
            raise ValueError(msg)
        tasks.append(task)
    return tasks
