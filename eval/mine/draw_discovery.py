"""Draw the held-out discovery set.

The rule is the "Discovery supplement" of docs/evaluation.md. The pool
is every mined hidden-fix candidate. As applied here:

1. A candidate the step-1 reading removed is out, with its reason. The
   readings are ``eval/tasks/selection.json`` (the pool the per-class
   draw already read) and the ``reviews`` of the record, for the rest.
   A task a development task file (``eval/skill/tasks*.json``) names,
   and a task the per-class held-out draw took, are skipped too.
2. The rest are ordered by ``sha256(salt + task id)``, hex digest
   compared as text.
3. Walking that order, a candidate whose language already has two
   picks is passed over unbuilt. Any other is judged on the task tree
   alone: its setup, workload and oracle must all have exited 0 there.
   What the reference tree did is never read. The first eight that
   build are the picks.

The record ``eval/mine/heldout-discovery-draw.json`` keeps the human
inputs (``mined``, ``reviews``, ``notes``) and everything this module
derives from them and from the manifests (``pool``, ``skipped``,
``order``, ``walk``, ``picks``), so the draw can be re-derived:
``uv run python eval/mine/draw_discovery.py`` prints the derivation and
exits 1 if the record differs, and ``--write`` rewrites the derived
part.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import draw
from mine import source_files
from task import Task, load_tasks

RECORD = Path(__file__).resolve().parent / "heldout-discovery-draw.json"
SALT = "perfproof-heldout-discovery-2026-10-28"
PICKS = 8
PER_LANGUAGE = 2
LANGUAGES = {
    ".py": "Python",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".kt": "Kotlin",
    ".js": "JavaScript/TypeScript",
    ".mjs": "JavaScript/TypeScript",
    ".cjs": "JavaScript/TypeScript",
    ".jsx": "JavaScript/TypeScript",
    ".ts": "JavaScript/TypeScript",
    ".tsx": "JavaScript/TypeScript",
    ".c": "C/C++",
    ".h": "C/C++",
    ".cc": "C/C++",
    ".cpp": "C/C++",
    ".cxx": "C/C++",
    ".hh": "C/C++",
    ".hpp": "C/C++",
    ".cu": "CUDA",
    ".cuh": "CUDA",
    ".cs": "C#",
    ".rb": "Ruby",
}


def language_of(files: list[str]) -> str:
    """Name the language of a change by its source files.

    Parameters
    ----------
    files : list of str
        Paths the reference change touched.

    Returns
    -------
    str
        The language with the most source files (``mine.source_files``)
        among those in `LANGUAGES`; a tie goes to the name that sorts
        first. ``unknown`` when none of them has a known extension.
    """
    counts = Counter(
        LANGUAGES[Path(f).suffix.lower()]
        for f in source_files(files)
        if Path(f).suffix.lower() in LANGUAGES
    )
    if not counts:
        return "unknown"
    top = max(counts.values())
    return sorted(k for k, v in counts.items() if v == top)[0]


def task_tree_failure(task: Task) -> str | None:
    """Say why the task tree did not build and run, or ``None``.

    Only runs on the task tree are read. The reference tree's runs, and
    the manifest's overall status (which looks at both), are not.

    Parameters
    ----------
    task : Task
        A checked manifest.

    Returns
    -------
    str or None
        The first task-tree step that did not exit 0 (step, command,
        exit status), a missing step, or ``None`` when setup, oracle and
        workload all exited 0.
    """
    runs = [r for r in task.runs if r.tree == "task"]
    for run in runs:
        if run.exit_code != 0:
            return f"{run.step} `{run.command}` exited {run.exit_code}"
    seen = {r.step for r in runs}
    for step in ("setup", "oracle", "workload"):
        if step not in seen:
            return f"no {step} step was run on the task tree"
    return None


@dataclass
class Visit:
    """One candidate the walk looked at."""

    task_id: str
    language: str
    outcome: str
    reason: str


@dataclass
class Derived:
    """What the rule gives for the current manifests and record."""

    pool: list[str] = field(default_factory=list[str])
    removed: dict[str, str] = field(default_factory=dict[str, str])
    skipped: dict[str, str] = field(default_factory=dict[str, str])
    order: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    walk: list[Visit] = field(default_factory=list[Visit])
    picks: list[str] = field(default_factory=list[str])
    stopped: str | None = None


def derive(
    tasks: list[Task],
    mined: list[str],
    reviews: dict[str, dict[str, Any]],
) -> Derived:
    """Apply the rule to the manifests.

    Parameters
    ----------
    tasks : list of Task
        Every manifest.
    mined : list of str
        Ids mined for this draw; they are not in the per-class draw's
        pool, which was fixed before they existed.
    reviews : dict
        Step-1 readings of the candidates ``selection.json`` has none
        for: id to ``{"keep": bool, "reason": str, "read": [...]}``.

    Returns
    -------
    Derived
        The pool, removals, skips, order, walk and picks. ``stopped``
        names a candidate that still needs a reading or a build.
    """
    sel = draw.load_selection()
    named = draw.development_named()
    base = [t for t in tasks if t.id not in mined]
    per_class = draw.draw_splits(base, sel, named)["held-out"]
    taken = {v.task_id for d in per_class.values() for v in d.picks}
    out = Derived()
    out.pool = sorted(t.id for t in tasks if t.family == "hidden-fix")
    kept: list[Task] = []
    for task in sorted(
        (t for t in tasks if t.family == "hidden-fix"), key=lambda t: t.id
    ):
        review: Any
        if task.id in sel.review:
            r = sel.review[task.id]
            review = {"keep": r.keep, "reason": r.reason}
        else:
            review = reviews.get(task.id)
        if task.id in named:
            out.skipped[task.id] = (
                f"named in eval/skill/{named[task.id]} (development)"
            )
        elif task.id in taken:
            out.skipped[task.id] = "taken by the per-class held-out draw"
        elif review is None:
            out.stopped = f"{task.id} needs its step-1 reading"
            return out
        elif not review["keep"]:
            out.removed[task.id] = review["reason"]
        else:
            kept.append(task)
    kept.sort(key=lambda t: draw.order_key(SALT, t.id))
    out.order = [{"id": t.id, "key": draw.order_key(SALT, t.id)} for t in kept]
    per_language: Counter[str] = Counter()
    for task in kept:
        if len(out.picks) == PICKS:
            break
        language = language_of(task.reference_change.files)
        if per_language[language] >= PER_LANGUAGE:
            out.walk.append(
                Visit(
                    task.id,
                    language,
                    "passed",
                    f"language cap: {PER_LANGUAGE} {language} tasks "
                    "already taken; not built",
                )
            )
            continue
        if task.status == "candidate":
            out.stopped = f"{task.id} needs a build"
            return out
        failure = task_tree_failure(task)
        if failure is not None:
            out.walk.append(
                Visit(task.id, language, "passed", f"did not build: {failure}")
            )
            continue
        out.walk.append(
            Visit(task.id, language, "taken", "built and ran on the task tree")
        )
        out.picks.append(task.id)
        per_language[language] += 1
    return out


def load_record(path: Path = RECORD) -> dict[str, Any]:
    """Read the record, or an empty one when none is committed yet."""
    if not path.exists():
        return {"mined": {"ids": []}, "reviews": {}}
    return cast(dict[str, Any], json.loads(path.read_text()))


def mined_ids(path: Path = RECORD) -> list[str]:
    """Return the ids mined for this draw (empty before the record)."""
    return list(load_record(path)["mined"]["ids"])


def pick_ids(path: Path = RECORD) -> list[str]:
    """Return the recorded picks (empty before the record)."""
    return list(load_record(path).get("picks", []))


def render(record: dict[str, Any], derived: Derived) -> dict[str, Any]:
    """Return the record with its derived part set from `derived`."""
    tasks = {t.id: t for t in load_tasks()}
    return {
        "rule": "docs/evaluation.md, Discovery supplement",
        "salt": SALT,
        "mined": record["mined"],
        "reviews": record["reviews"],
        "notes": record.get("notes", {}),
        "pool": derived.pool,
        "removed": derived.removed,
        "skipped": derived.skipped,
        "languages": {
            i: language_of(tasks[i].reference_change.files)
            for i in derived.pool
        },
        "order": derived.order,
        "walk": [
            {
                "id": v.task_id,
                "language": v.language,
                "outcome": v.outcome,
                "reason": v.reason,
            }
            for v in derived.walk
        ],
        "picks": derived.picks,
        "stopped": derived.stopped,
    }


def main(argv: list[str] | None = None) -> int:
    """Print the derived draw; check or rewrite the record.

    Parameters
    ----------
    argv : list of str, optional
        Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        0 when the record matches the derivation, else 1.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    record = load_record()
    derived = derive(load_tasks(), record["mined"]["ids"], record["reviews"])
    new = render(record, derived)
    text = json.dumps(new, indent=1) + "\n"
    sys.stdout.write(text)
    if args.write:
        RECORD.write_text(text)
        return 0
    return 0 if RECORD.exists() and RECORD.read_text() == text else 1


if __name__ == "__main__":
    raise SystemExit(main())
