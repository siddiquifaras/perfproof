"""Draw the held-out and development splits from the candidate pool.

This is the selection rule in docs/evaluation.md ("How the held-out
set is chosen"), applied to the manifests under ``eval/tasks/`` and
the hand review recorded in ``eval/tasks/selection.json``:

1. A candidate the review removed (step 1) is never drawn. Its reason
   is in the review. A task a development task file
   (``eval/skill/tasks*.json``) names was used for development and is
   skipped by the held-out draw, with that as the reason.
2. The rest are ordered by ``sha256(salt + task id)``, hex digest
   compared as text.
3. Per target class, five slots are filled in that order: two
   known-change tasks, then one hidden-fix, one nothing-to-keep and one
   published-benchmark task. A slot takes the first candidate of its
   family whose manifest status is ``runs`` or ``reference-fails``. A
   slot whose family has no candidate left takes the next known-change
   task instead. A slot with no candidate left at all is recorded as
   unfilled and the draw goes on to the next slot.
4. A candidate whose status is ``failed`` or ``builds`` (it can't be
   built or run here) is passed over, with its manifest's
   ``status_reason`` as the reason. A reference tree that fails its
   oracle or workload (``reference-fails``) is not a reason: whether
   the change breaks behaviour is an outcome, and docs/evaluation.md
   step 4 allows only buildability to decide.
5. The held-out split is drawn first; the development split is drawn
   from what the held-out split did not take, with its own salt.

A class the review marks as not buildable here (``unbuilt_classes``)
takes its candidates unbuilt, as drawn-but-unbuilt picks, in the
held-out split only. Its development draw waits until those picks are
built, because a pick that later fails to build moves the held-out
draw on to candidates the development draw could otherwise take.

A draw stops at the first candidate that still needs a step-1 review
or a build, and says which.

Usage: ``uv run python eval/mine/draw.py`` prints the draw;
``--write`` also sets each manifest's ``split`` and rewrites
``eval/tasks/SPLITS.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict

from task import (
    TASKS_DIR,
    Family,
    Split,
    TargetClass,
    Task,
    load_tasks,
    write_task,
)

SELECTION = TASKS_DIR / "selection.json"
SKILL_DIR = TASKS_DIR.parent / "skill"
REPORT = TASKS_DIR / "SPLITS.md"
HELD_OUT_TASK_FILES = "tasks-heldout"
"""Task files named with this prefix hold held-out tasks, not
development ones, so the held-out draw's skip does not read them."""
DISCOVERY = Path(__file__).resolve().parent / "heldout-discovery-draw.json"
CLASSES: tuple[TargetClass, ...] = (
    "python",
    "native",
    "web",
    "agentic",
    "serving",
    "gpu",
    "java",
    "rust",
    "dotnet",
)
SLOTS: tuple[Family, ...] = (
    "known-change",
    "known-change",
    "hidden-fix",
    "nothing-to-keep",
    "published-benchmark",
)
DrawnSplit = Literal["held-out", "development"]
SPLITS: tuple[DrawnSplit, ...] = ("held-out", "development")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Review(_Model):
    """The step-1 reading of one candidate's change.

    Attributes
    ----------
    keep : bool
        False when the family or class is wrong on reading the change.
    reason : str
        What the reading found; for a removal, why.
    read : list of str
        The pull requests, commits or rows that were read.
    """

    keep: bool
    reason: str
    read: list[str]


class Selection(_Model):
    """The recorded inputs to the draw.

    Attributes
    ----------
    salts : dict of str to str
        Salt per split, ``held-out`` and ``development``.
    unbuilt_classes : dict of str to str
        Classes that can't be built on this machine, with the reason.
    review : dict of str to Review
        Step-1 reviews by task id.
    """

    salts: dict[DrawnSplit, str]
    unbuilt_classes: dict[TargetClass, str]
    review: dict[str, Review]


@dataclass
class Visit:
    """One candidate the draw looked at, and what it did with it."""

    task_id: str
    family: Family
    outcome: Literal["taken", "unbuilt", "removed", "passed"]
    reason: str
    slot: Family | None = None


@dataclass
class ClassDraw:
    """The draw for one split and class.

    Attributes
    ----------
    visits : list of Visit
        Every candidate looked at, in the order looked at.
    stopped : str or None
        Why the draw could not finish (a candidate needs a review or a
        build), or None when every slot was either filled or recorded
        as unfilled.
    unfilled : list of Family
        Slots no remaining candidate could fill, in slot order.
    """

    visits: list[Visit] = field(default_factory=list[Visit])
    stopped: str | None = None
    unfilled: list[Family] = field(default_factory=list[Family])

    @property
    def picks(self) -> list[Visit]:
        """Visits that filled a slot, drawn or drawn-but-unbuilt."""
        return [v for v in self.visits if v.slot is not None]


def order_key(salt: str, task_id: str) -> str:
    """Return a candidate's place in the draw order.

    Parameters
    ----------
    salt : str
        The split's salt.
    task_id : str
        The manifest id.

    Returns
    -------
    str
        Hex digest of ``sha256(salt + task_id)``; lower comes first.
    """
    return hashlib.sha256((salt + task_id).encode()).hexdigest()


def load_selection(path: Path = SELECTION) -> Selection:
    """Read and validate the recorded selection inputs.

    Parameters
    ----------
    path : Path
        The selection file.

    Returns
    -------
    Selection
        Its contents.

    Raises
    ------
    pydantic.ValidationError
        If the file does not match the schema.
    """
    return Selection.model_validate_json(path.read_text())


def discovery_record(path: Path = DISCOVERY) -> dict[str, Any]:
    """Read the held-out discovery draw's record, if there is one.

    The discovery supplement (``draw_discovery.py``) mines candidates
    after this draw was fixed and picks its own tasks. This draw must
    not see the mined ones, and its ``--write`` must keep the picks
    held-out.

    Parameters
    ----------
    path : Path
        The record.

    Returns
    -------
    dict
        The record, or one with no mined ids and no picks.
    """
    if not path.exists():
        return {"mined": {"ids": []}, "picks": []}
    return cast(dict[str, Any], json.loads(path.read_text()))


def development_named(skill_dir: Path = SKILL_DIR) -> dict[str, str]:
    """Return the tasks the skill eval's development task files name.

    A task used for development never moves to the held-out set
    (docs/evaluation.md), so the held-out draw skips every manifest
    id a ``tasks*.json`` file names under ``repo.manifest``.

    Parameters
    ----------
    skill_dir : Path
        Directory holding the ``tasks*.json`` files.

    Returns
    -------
    dict of str to str
        Manifest id to the name of the first file that names it.
    """
    named: dict[str, str] = {}
    for path in sorted(skill_dir.glob("tasks*.json")):
        if path.name.startswith(HELD_OUT_TASK_FILES):
            continue
        data = json.loads(path.read_text())
        for entry in data.get("tasks", []):
            repo = entry.get("repo")
            if isinstance(repo, dict):
                manifest = cast(dict[str, Any], repo).get("manifest")
                if isinstance(manifest, str):
                    named.setdefault(manifest, path.name)
    return named


def _draw_class(
    klass: TargetClass,
    tasks: list[Task],
    salt: str,
    sel: Selection,
    taken: set[str],
    skip: dict[str, str],
) -> ClassDraw:
    unbuilt = klass in sel.unbuilt_classes
    queues: dict[Family, list[Task]] = {}
    for task in sorted(tasks, key=lambda t: order_key(salt, t.id)):
        if task.target_class == klass and task.id not in taken:
            queues.setdefault(task.family, []).append(task)
    draw = ClassDraw()

    def fill(slot: Family, family: Family) -> bool | None:
        # True: slot filled; False: family exhausted; None: stopped.
        queue = queues.get(family, [])
        while queue:
            task = queue.pop(0)
            if task.id in skip:
                draw.visits.append(
                    Visit(
                        task.id,
                        family,
                        "passed",
                        f"named in eval/skill/{skip[task.id]} (development)",
                    )
                )
                continue
            review = sel.review.get(task.id)
            if review is None:
                draw.stopped = f"{task.id} needs its step-1 review"
                return None
            if not review.keep:
                draw.visits.append(
                    Visit(task.id, family, "removed", review.reason)
                )
                continue
            if task.status in ("failed", "builds"):
                draw.visits.append(
                    Visit(task.id, family, "passed", task.status_reason)
                )
                continue
            if task.status in ("runs", "reference-fails"):
                # A reference tree that fails its oracle or workload is
                # a result for labelling, not a build failure: passing
                # it over would bias the set against breaking changes.
                reason = review.reason
                if task.status == "reference-fails":
                    reason += (
                        ". The reference tree fails here; see the manifest"
                    )
                draw.visits.append(
                    Visit(task.id, family, "taken", reason, slot)
                )
                return True
            if unbuilt:
                reason = sel.unbuilt_classes[klass]
                draw.visits.append(
                    Visit(task.id, family, "unbuilt", reason, slot)
                )
                return True
            draw.stopped = f"{task.id} needs a build"
            return None
        return False

    for slot in SLOTS:
        filled = fill(slot, slot)
        if filled is False and slot != "known-change":
            filled = fill(slot, "known-change")
        if filled is None:
            break
        if filled is False:
            draw.unfilled.append(slot)
    return draw


def draw_splits(
    tasks: list[Task],
    sel: Selection,
    named: dict[str, str] | None = None,
) -> dict[DrawnSplit, dict[TargetClass, ClassDraw]]:
    """Draw both splits.

    Parameters
    ----------
    tasks : list of Task
        The whole candidate pool.
    sel : Selection
        Salts, unbuildable classes and step-1 reviews.
    named : dict of str to str, optional
        Tasks named in a development task file, which the held-out
        draw skips; defaults to `development_named`.

    Returns
    -------
    dict
        Per split, per class, the draw. The development split has no
        entry for a class in ``sel.unbuilt_classes``.
    """
    if named is None:
        named = development_named()
    mined = set(discovery_record()["mined"]["ids"])
    tasks = [t for t in tasks if t.id not in mined]
    out: dict[DrawnSplit, dict[TargetClass, ClassDraw]] = {}
    taken: set[str] = set()
    for split in SPLITS:
        out[split] = {}
        for klass in CLASSES:
            if split == "development" and klass in sel.unbuilt_classes:
                continue
            draw = _draw_class(
                klass,
                tasks,
                sel.salts[split],
                sel,
                taken,
                named if split == "held-out" else {},
            )
            out[split][klass] = draw
            taken.update(v.task_id for v in draw.picks)
    return out


def assigned_splits(
    draws: dict[DrawnSplit, dict[TargetClass, ClassDraw]],
) -> dict[str, Split]:
    """Return the split of every task the draws picked.

    Parameters
    ----------
    draws : dict
        Output of `draw_splits`.

    Returns
    -------
    dict of str to Split
        Task id to split; tasks not picked are absent.
    """
    return {
        v.task_id: split
        for split, by_class in draws.items()
        for draw in by_class.values()
        for v in draw.picks
    }


def final_splits(
    draws: dict[DrawnSplit, dict[TargetClass, ClassDraw]],
) -> dict[str, Split]:
    """Return the split every picked task is written with.

    The per-class picks (`assigned_splits`) plus the held-out discovery
    set, which its own draw picked and this one must not move back to
    ``development-candidate``.

    Parameters
    ----------
    draws : dict
        Output of `draw_splits`.

    Returns
    -------
    dict of str to Split
        Task id to split; tasks not picked by either draw are absent.
    """
    splits = assigned_splits(draws)
    for task_id in discovery_record()["picks"]:
        splits[task_id] = "held-out"
    return splits


def report(
    draws: dict[DrawnSplit, dict[TargetClass, ClassDraw]], sel: Selection
) -> str:
    """Render the draws as Markdown, every visited candidate included.

    Parameters
    ----------
    draws : dict
        Output of `draw_splits`.
    sel : Selection
        The inputs the draws used.

    Returns
    -------
    str
        The text of ``eval/tasks/SPLITS.md``.
    """
    lines = [
        "# Splits",
        "",
        "Generated by `uv run python eval/mine/draw.py --write` from",
        "the manifests and `selection.json`; do not edit by hand. The",
        "rule is in docs/evaluation.md and `mine/draw.py`.",
        "",
    ]
    for split in SPLITS:
        lines += [
            f"## {split}",
            "",
            f"Salt `{sel.salts[split]}`.",
            "",
        ]
        for klass in CLASSES:
            draw = draws[split].get(klass)
            lines += [f"### {split}: {klass}", ""]
            if draw is None:
                lines += [
                    "Not drawn yet: the held-out picks of this class are "
                    f"unbuilt ({sel.unbuilt_classes[klass]}).",
                    "",
                ]
                continue
            lines += [
                "| # | Task | Family | Slot | Outcome | Reason |",
                "|---:|---|---|---|---|---|",
            ]
            for i, v in enumerate(draw.visits, 1):
                reason = " ".join(v.reason.split()).replace("|", "\\|")
                lines.append(
                    f"| {i} | `{v.task_id}` | {v.family} | {v.slot or ''} "
                    f"| {v.outcome} | {reason} |"
                )
            done = len(draw.picks)
            lines += ["", f"{done} of {len(SLOTS)} slots filled."]
            lines += [
                f"Slot unfilled: no candidate ({slot})."
                for slot in draw.unfilled
            ]
            if draw.stopped:
                lines.append(f"Stopped: {draw.stopped}.")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """Print the draw, and with ``--write`` apply it.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        0 when every drawn class filled its five slots, else 1.
    """
    parser = argparse.ArgumentParser(description="Draw the splits.")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    tasks = load_tasks()
    sel = load_selection()
    draws = draw_splits(tasks, sel)
    text = report(draws, sel)
    sys.stdout.write(text)
    if args.write:
        splits = final_splits(draws)
        for task in tasks:
            split = splits.get(task.id, "development-candidate")
            if task.split != split:
                write_task(task.model_copy(update={"split": split}))
        REPORT.write_text(text)
    complete = all(
        d.stopped is None for by in draws.values() for d in by.values()
    )
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
