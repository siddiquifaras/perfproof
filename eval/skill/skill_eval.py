"""Command line of the skill eval: run, score, archive, break-skill.

Examples
--------
Needs ``uv sync`` (the runs use this checkout's CLI and Python) and a
signed-in ``claude``. One task runs at a time. A whole run costs a few
dollars of Sonnet; ``--only`` takes task id patterns::

    uv run python eval/skill/skill_eval.py run --label mine
    uv run python eval/skill/skill_eval.py run --label mine --resume
    uv run python eval/skill/skill_eval.py run --only 'prove-*' 'pos-*'

Score a saved run again, with no model call, optionally against an
earlier ``scores.json``::

    cd eval/skill
    uv run python skill_eval.py score ../../scratch/skill-eval/mine \
        --previous runs/first/scores.json

Measure a skill change: ``break-skill`` copies the skill with its "no
direction words" rule inverted, to show the scorer sees the difference::

    uv run python skill_eval.py break-skill ../../scratch/broken
    uv run python skill_eval.py run --skill ../../scratch/broken

``archive`` keeps a run's transcripts and scores for the repo.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

import reporting
import runner
import scoring
import taskset

DEFAULT_OUT = runner.REPO_ROOT / "scratch" / "skill-eval"
"""Gitignored directory runs are written to by default."""
# The rule the broken copy replaces, from skills/perfproof/SKILL.md
# ("Estimates"). The replacement says the opposite, so the copy is
# worse on wording by construction, not by chance.
_RULE = re.compile(
    r"Never call an\s+estimate faster.*?until perfproof can\s+give a"
    r" verdict\.",
    re.DOTALL,
)
_BROKEN = (
    "Say plainly whether the estimate shows the change is faster or"
    " slower, by how much, and\n    whether it matches the claim."
)


def _select(
    tasks: list[taskset.Task], patterns: list[str] | None
) -> list[taskset.Task]:
    if not patterns:
        return tasks
    return [
        t for t in tasks if any(fnmatch.fnmatch(t.id, p) for p in patterns)
    ]


def break_skill(dest: Path) -> None:
    """Copy the skill with its "no direction words" rule inverted.

    Parameters
    ----------
    dest : Path
        Directory to create.

    Raises
    ------
    ValueError
        If ``SKILL.md`` no longer contains the rule text this edits;
        update ``_RULE`` when the skill's wording changes.
    """
    shutil.copytree(runner.SKILL_SOURCE, dest)
    skill = dest / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    if _RULE.search(text) is None:
        msg = "the direction-word rule is not in SKILL.md as expected"
        raise ValueError(msg)
    skill.write_text(_RULE.sub(_BROKEN, text), encoding="utf-8")


def _cmd_run(args: argparse.Namespace) -> int:
    tasks = taskset.load(args.tasks_file)
    chosen = _select(tasks.tasks, args.only)
    run_id = args.label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=args.resume)
    path_dir = runner.shim_dir(run_dir)
    skill_dir = Path(args.skill)
    sys.stdout.write(f"run {run_dir}\n")
    for task in chosen:
        if args.resume and (run_dir / task.id / "meta.json").exists():
            continue
        for stale in (run_dir / task.id, run_dir / "repos" / task.id):
            shutil.rmtree(stale, ignore_errors=True)
        meta = runner.run_task(task, tasks, run_dir, skill_dir, path_dir)
        sys.stdout.write(
            f"{task.id}: {meta.wall_seconds:.0f}s, "
            f"load {meta.load_start[0]:.1f}\n"
        )
        sys.stdout.flush()
    return _score_and_report(run_dir, args.tasks_file, args.previous)


def _score_and_report(
    run_dir: Path, tasks_file: Path, previous: Path | None
) -> int:
    tasks = taskset.load(tasks_file)
    scores = scoring.score_run(run_dir, tasks)
    reporting.write(run_dir, scores, previous)
    sys.stdout.write((run_dir / "report.md").read_text(encoding="utf-8"))
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    return _score_and_report(
        Path(args.run_dir), args.tasks_file, args.previous
    )


def _cmd_break(args: argparse.Namespace) -> int:
    break_skill(Path(args.dest))
    sys.stdout.write(f"{args.dest}\n")
    return 0


def archive(run_dir: Path, dest: Path) -> None:
    """Copy what scoring reads out of a run, to keep it in the repo.

    Parameters
    ----------
    run_dir : Path
        A run directory.
    dest : Path
        Directory to create. It gets every task's transcripts,
        ``meta.json`` and ``state.json`` plus ``scores.json`` and
        ``report.md``; the built repos, the launchers, the scratch
        trees and the ``claude`` stderr files are left out.
    """
    shutil.copytree(
        run_dir,
        dest,
        ignore=shutil.ignore_patterns("repos", "work", "bin", "*.err"),
    )


def _cmd_archive(args: argparse.Namespace) -> int:
    archive(args.run_dir, args.dest)
    sys.stdout.write(f"{args.dest}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Parameters
    ----------
    argv : list of str, optional
        Arguments; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        Exit status.
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tasks-file", type=Path, default=taskset.TASKS_FILE)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run tasks, then score the run")
    run.add_argument("--only", nargs="+", help="task id patterns")
    run.add_argument("--skill", type=Path, default=runner.SKILL_SOURCE)
    run.add_argument("--label", help="run id; a timestamp by default")
    run.add_argument(
        "--resume",
        action="store_true",
        help="keep finished tasks of an existing run, redo the rest",
    )
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    run.add_argument("--previous", type=Path, help="scores.json to compare")
    run.set_defaults(func=_cmd_run)
    score = sub.add_parser("score", help="score a saved run, no re-run")
    score.add_argument("run_dir", type=Path)
    score.add_argument("--previous", type=Path)
    score.set_defaults(func=_cmd_score)
    keep = sub.add_parser("archive", help="copy a run's scoring inputs")
    keep.add_argument("run_dir", type=Path)
    keep.add_argument("dest", type=Path)
    keep.set_defaults(func=_cmd_archive)
    brk = sub.add_parser("break-skill", help="copy the skill, rule inverted")
    brk.add_argument("dest", type=Path)
    brk.set_defaults(func=_cmd_break)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
