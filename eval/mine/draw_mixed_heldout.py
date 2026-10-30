"""Draw the mixed-language held-out set.

The rule is the "Mixed held-out set" of docs/evaluation.md, fixed on
2026-10-30 before any task was built for it. As applied here:

1. The pool is every hidden-fix manifest, the ones mined for this draw
   (``seeds-heldout-mixed.json``) and the earlier ones. A task is
   skipped, with the reason recorded, when a task file
   (``eval/skill/tasks*.json``) names it, a per-class draw or a
   discovery draw examined it, or a discovery draw took it. A candidate
   under 2000 stars is out. Each remaining candidate has a reading in
   the record's ``reviews``: the change is an optimization (step 1 of
   the held-out rule), its user path mixes at least two languages
   (``pair``), and its fix is in the upper layer or the lower layer
   reached through it (``layer``). A candidate read as not mixed, or
   not an optimization, is out with that reason.
2. The rest are ordered by ``sha256(salt + task id)``, hex digest
   compared as text.
3. Walking that order, a candidate is passed over when its language
   already has three picks, when it would leave too few slots for the
   quotas (below), or when the sandboxed setup check in ``builds``
   says it did not build and pass its oracle on the untouched tree.
   The first twelve that pass are the picks. The check is judged on
   the task tree alone; what the reference change does is never read.
4. The quotas are minimums: at least 3 picks whose fix language is not
   Python and at least 4 whose fix is in the lower layer. A candidate is
   passed over when taking it would leave fewer slots than the larger
   of the two shortfalls still to fill, so the quotas are met whenever
   the pool allows it. A pool too small gives fewer than twelve picks
   and says which quota failed.

The fix language is `draw_discovery.language_of` over the files the
reference change touched.

The record ``eval/mine/heldout-mixed-draw.json`` keeps the human inputs
(``mined``, ``reviews``, ``builds``, ``notes``) and everything this
module derives (``pool``, ``removed``, ``skipped``, ``languages``,
``order``, ``walk``, ``picks``, ``quotas``), so the draw can be
re-derived: ``uv run python eval/mine/draw_mixed_heldout.py`` prints the
derivation and exits 1 if the record differs, and ``--write`` rewrites
the derived part.
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
from draw_discovery import Visit, language_of
from task import Task, load_tasks

RECORD = draw.MIXED
SALT = "perfproof-mixed-heldout-2026-10-30"
RULE = "docs/evaluation.md, Mixed held-out set"
PICKS = 12
PER_LANGUAGE = 3
MIN_NON_PYTHON = 3
MIN_LOWER = 4
MIN_STARS = 2000
LAYERS = ("upper", "lower")


@dataclass
class Derived:
    """What the rule gives for the current manifests and record."""

    pool: list[str] = field(default_factory=list[str])
    removed: dict[str, str] = field(default_factory=dict[str, str])
    skipped: dict[str, str] = field(default_factory=dict[str, str])
    languages: dict[str, str] = field(default_factory=dict[str, str])
    order: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    walk: list[Visit] = field(default_factory=list[Visit])
    picks: list[str] = field(default_factory=list[str])
    quotas: dict[str, Any] = field(default_factory=dict[str, Any])
    stopped: str | None = None


def load_record(path: Path = RECORD) -> dict[str, Any]:
    """Read the record, or an empty one when none is committed yet."""
    if not path.exists():
        return {"mined": {"ids": []}, "reviews": {}, "builds": {}}
    return cast(dict[str, Any], json.loads(path.read_text()))


def examined_by_earlier_draws() -> dict[str, str]:
    """Return the tasks an earlier draw examined or took, with why.

    Returns
    -------
    dict of str to str
        Task id to the reason this draw skips it: every candidate in a
        discovery draw's walk or picks, and every candidate the
        per-class draws visited (the per-class pool leaves out every
        discovery draw's and this draw's mined candidates, which did
        not exist when it was drawn).
    """
    out: dict[str, str] = {}
    names = {
        "heldout-discovery-draw.json": "the first discovery draw",
        "heldout-discovery-2-draw.json": "the second discovery draw",
    }
    for name, label in names.items():
        data = json.loads((draw.MINE_DIR / name).read_text())
        for v in data["walk"]:
            out.setdefault(v["id"], f"examined by {label} ({v['outcome']})")
        for pick in data["picks"]:
            out.setdefault(pick, f"taken by {label}")
    mined = set(draw.discovery_record()["mined"]["ids"])
    base = [t for t in load_tasks() if t.id not in mined]
    per_class = draw.draw_splits(
        base, draw.load_selection(), draw.development_named()
    )
    for by_class in per_class.values():
        for d in by_class.values():
            for v in d.visits:
                out.setdefault(v.task_id, "examined by a per-class draw")
    return out


def named_by_task_files(skill_dir: Path = draw.SKILL_DIR) -> dict[str, str]:
    """Return every manifest a task file names, held-out files included.

    Parameters
    ----------
    skill_dir : Path
        Directory holding the ``tasks*.json`` files.

    Returns
    -------
    dict of str to str
        Manifest id to the first file that names it. This draw's own
        file is left out: it is written from the picks.
    """
    named: dict[str, str] = {}
    for path in sorted(skill_dir.glob("tasks*.json")):
        if path.name == "tasks-mixed-heldout.json":
            continue
        data = json.loads(path.read_text())
        for entry in data.get("tasks", []):
            repo = entry.get("repo")
            if isinstance(repo, dict):
                manifest = cast(dict[str, Any], repo).get("manifest")
                if isinstance(manifest, str):
                    named.setdefault(manifest, path.name)
    return named


def _is_python(language: str) -> bool:
    return language == "Python"


def derive(
    tasks: list[Task],
    record: dict[str, Any],
    *,
    salt: str = SALT,
    examined: dict[str, str] | None = None,
    named: dict[str, str] | None = None,
    later_mined: list[str] | None = None,
) -> Derived:
    """Apply the rule to the manifests and the record.

    Parameters
    ----------
    tasks : list of Task
        Every manifest.
    record : dict
        The human inputs: ``reviews`` (id to ``keep``, ``reason``,
        ``read``, ``pair``, ``layer``) and ``builds`` (id to ``ok`` and
        ``reason``, the sandboxed setup check).
    salt : str, optional
        The draw's salt.
    examined : dict, optional
        Id to reason for each task an earlier draw examined or took.
    named : dict, optional
        Id to file name for each task a task file names.
    later_mined : list of str, optional
        Ids mined after this draw; left out of its manifests.

    Returns
    -------
    Derived
        The pool, removals, skips, order, walk and picks. ``stopped``
        names a candidate that still needs a reading or a check.
    """
    examined = {} if examined is None else examined
    named = {} if named is None else named
    later = set(later_mined or [])
    reviews: dict[str, dict[str, Any]] = record["reviews"]
    builds: dict[str, dict[str, Any]] = record.get("builds", {})
    out = Derived()
    hidden = sorted(
        (t for t in tasks if t.family == "hidden-fix" and t.id not in later),
        key=lambda t: t.id,
    )
    out.pool = [t.id for t in hidden]
    kept: list[Task] = []
    for task in hidden:
        review = reviews.get(task.id)
        if task.id in named:
            out.skipped[task.id] = f"named in eval/skill/{named[task.id]}"
        elif task.id in examined:
            out.skipped[task.id] = examined[task.id]
        elif task.stars < MIN_STARS:
            out.removed[task.id] = f"{task.stars} stars, under {MIN_STARS}"
        elif review is None:
            out.stopped = f"{task.id} needs its reading"
            return out
        elif not review["keep"]:
            out.removed[task.id] = review["reason"]
        else:
            kept.append(task)
    kept.sort(key=lambda t: draw.order_key(salt, t.id))
    out.order = [{"id": t.id, "key": draw.order_key(salt, t.id)} for t in kept]
    for task in hidden:
        out.languages[task.id] = language_of(task.reference_change.files)
    per_language: Counter[str] = Counter()
    non_python = lower = 0
    for task in kept:
        if len(out.picks) == PICKS:
            break
        language = out.languages[task.id]
        layer = reviews[task.id]["layer"]
        gives_non_python = not _is_python(language)
        gives_lower = layer == "lower"
        left = PICKS - len(out.picks) - 1
        short = max(
            MIN_NON_PYTHON - non_python - int(gives_non_python),
            MIN_LOWER - lower - int(gives_lower),
            0,
        )
        reason: str | None = None
        if per_language[language] >= PER_LANGUAGE:
            reason = (
                f"language cap: {PER_LANGUAGE} {language} tasks already "
                "taken; not built"
            )
        elif left < short:
            reason = (
                f"quota: {left} slots would be left for {short} still "
                "needed (non-Python fix or lower layer); not built"
            )
        else:
            build = builds.get(task.id)
            if build is None:
                out.stopped = f"{task.id} needs a sandboxed setup check"
                return out
            if not build["ok"]:
                reason = f"did not build: {build['reason']}"
        if reason is not None:
            out.walk.append(Visit(task.id, language, "passed", reason))
            continue
        out.walk.append(
            Visit(
                task.id,
                language,
                "taken",
                "built, and its oracle passed on the untouched tree, "
                "through the sandboxed setup",
            )
        )
        out.picks.append(task.id)
        per_language[language] += 1
        non_python += int(gives_non_python)
        lower += int(gives_lower)
    out.quotas = {
        "picks": len(out.picks),
        "non_python": non_python,
        "lower": lower,
        "max_per_language": max(per_language.values(), default=0),
        "met": len(out.picks) == PICKS
        and non_python >= MIN_NON_PYTHON
        and lower >= MIN_LOWER,
    }
    return out


def render(record: dict[str, Any], derived: Derived) -> dict[str, Any]:
    """Return the record with its derived part set from `derived`."""
    return {
        "rule": RULE,
        "salt": SALT,
        "mined": record["mined"],
        "reviews": record["reviews"],
        "builds": record.get("builds", {}),
        "notes": record.get("notes", {}),
        "pool": derived.pool,
        "removed": derived.removed,
        "skipped": derived.skipped,
        "languages": derived.languages,
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
        "quotas": derived.quotas,
        "stopped": derived.stopped,
    }


def derive_current(record: dict[str, Any]) -> Derived:
    """Apply the rule to every committed manifest and `record`."""
    return derive(
        load_tasks(),
        record,
        examined=examined_by_earlier_draws(),
        named=named_by_task_files(),
    )


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
    text = json.dumps(render(record, derive_current(record)), indent=1) + "\n"
    sys.stdout.write(text)
    if args.write:
        RECORD.write_text(text)
        return 0
    return 0 if RECORD.exists() and RECORD.read_text() == text else 1


if __name__ == "__main__":
    raise SystemExit(main())
