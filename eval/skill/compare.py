"""Put the arms and prompt levels of a task file side by side.

Takes the run directories of one task file (one per arm and level, as
``run.json`` records them), scores each on common terms (`arms`) and
renders, per task, one row for each level and arm, then totals per level
that set the skill against the baseline. Nothing is run; every number
comes from the saved runs.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path

import arms
from taskset import ARMS, LEVELS, Arm, Level, TaskSet

Key = tuple[Level, Arm]


@dataclass(frozen=True)
class Totals:
    """Sums over the tasks one arm ran at one level.

    Attributes
    ----------
    tasks : int
        Tasks with a saved run whose agent produced output.
    harness_failures : int
        Tasks left out of every other number because the agent
        produced no output.
    found, found_of : int
        Tasks that found the hotspot, of those with a known hotspot.
    correct, correct_of : int
        Tasks whose final tree passes the tests, of those that changed
        something and ran the tests.
    backed, unbacked : int
        Speed claims of the final answers, by backing.
    faster, slower, no_difference, unchanged, unknown : int
        Harness speed checks by outcome, over tasks that have one.
    median_ratio : float or None
        Median of the measured ratios (above 1: faster).
    cost_usd, wall_seconds : float
        Summed.
    contaminated : int
        Tasks of the baseline arm with signs perfproof was available.
    """

    tasks: int
    found: int
    found_of: int
    correct: int
    correct_of: int
    backed: int
    unbacked: int
    faster: int
    slower: int
    no_difference: int
    unchanged: int
    unknown: int
    median_ratio: float | None
    cost_usd: float
    wall_seconds: float
    contaminated: int
    harness_failures: int = 0
    excluded: tuple[str, ...] = ()


def totals(
    scores: list[arms.ArmScore], exclude: frozenset[str] = frozenset()
) -> Totals:
    """Sum a list of scores of one arm and level.

    Parameters
    ----------
    scores : list of ArmScore
        The tasks of one arm at one level.
    exclude : frozenset of str, optional
        Task ids left out as well (a harness failure of the other arm).

    Returns
    -------
    Totals
        The sums.
    """
    scores_in = scores
    left_out = sorted(
        s.task_id for s in scores if s.harness_failure or s.task_id in exclude
    )
    scores = [s for s in scores if s.task_id not in left_out]
    speeds = [s.speed for s in scores if s.speed is not None]
    ratios = [s.ratio for s in speeds if s.status == "measured" and s.ratio]
    return Totals(
        tasks=len(scores),
        found=sum(s.found is True for s in scores),
        found_of=sum(s.found is not None for s in scores),
        correct=sum(s.correct is True for s in scores),
        correct_of=sum(s.correct is not None for s in scores),
        backed=sum(s.claims_backed for s in scores),
        unbacked=sum(s.claims_unbacked for s in scores),
        faster=sum(s.verdict == "faster" for s in speeds),
        slower=sum(s.verdict == "slower" for s in speeds),
        no_difference=sum(s.verdict == "no_difference" for s in speeds),
        unchanged=sum(s.status == "unchanged" for s in speeds),
        unknown=sum(s.status == "unknown" for s in speeds),
        median_ratio=statistics.median(ratios) if ratios else None,
        cost_usd=round(sum(s.cost_usd for s in scores), 4),
        wall_seconds=round(sum(s.wall_seconds for s in scores), 1),
        contaminated=sum(bool(s.contaminated) for s in scores),
        harness_failures=sum(bool(s.harness_failure) for s in scores_in),
        excluded=tuple(left_out),
    )


def load(
    run_dirs: list[Path], tasks: TaskSet
) -> dict[Key, dict[str, arms.ArmScore]]:
    """Score run directories and file them by level and arm.

    Parameters
    ----------
    run_dirs : list of Path
        Run directories of the task set, each with a ``run.json``
        (none: the skill arm at the vague level).
    tasks : TaskSet
        The task file they were run from.

    Returns
    -------
    dict
        ``(level, arm)`` to task id to score.

    Raises
    ------
    ValueError
        If two directories hold the same arm and level.
    """
    found: dict[Key, dict[str, arms.ArmScore]] = {}
    for run_dir in run_dirs:
        arm, level = arms.read_run_marker(run_dir)
        if (level, arm) in found:
            msg = f"two runs hold {arm}/{level}: {run_dir}"
            raise ValueError(msg)
        found[(level, arm)] = {
            s.task_id: s for s in arms.score_arm_run(run_dir, tasks)
        }
    return found


def _speed(score: arms.ArmScore | None) -> str:
    if score is None or score.speed is None:
        return "n/a"
    s = score.speed
    if s.status == "measured":
        return (
            f"{s.ratio:.2f}x [{s.low:.2f}, {s.high:.2f}] "
            f"{s.verdict} ({s.final_ref})"
        )
    if s.status == "unchanged":
        return "unchanged"
    return f"unknown: {s.reason}"


def _flag(value: bool | None) -> str:
    return "n/a" if value is None else "yes" if value else "no"


def _row(level: Level, arm: Arm, score: arms.ArmScore | None) -> str:
    if score is None:
        return f"| {level} | {arm} | not run | | | | | |"
    if score.harness_failure:
        return (
            f"| {level} | {arm} | HARNESS FAILURE: "
            f"{score.harness_failure} | | | | | "
            f"{score.wall_seconds:.0f} |"
        )
    note = " (contaminated)" if score.contaminated else ""
    return (
        f"| {level} | {arm}{note} | {_flag(score.found)} "
        f"| {_flag(score.correct)} "
        f"| {score.claims_backed} / {score.claims_unbacked} "
        f"| {_speed(score)} | {score.cost_usd:.2f} "
        f"| {score.wall_seconds:.0f} |"
    )


def _total_row(level: Level, arm: Arm, t: Totals | None) -> str:
    if t is None:
        return f"| {level} | {arm} | not run | | | | | | |"
    ratio = "n/a" if t.median_ratio is None else f"{t.median_ratio:.2f}x"
    n = str(t.tasks)
    if t.excluded:
        n += f" ({len(t.excluded)} excluded)"
    return (
        f"| {level} | {arm} | {n} | {t.found}/{t.found_of} "
        f"| {t.correct}/{t.correct_of} | {t.backed} / {t.unbacked} "
        f"| {t.faster} faster, {t.no_difference} none, {t.slower} slower, "
        f"{t.unchanged} unchanged, {t.unknown} unknown (median {ratio}) "
        f"| {t.cost_usd:.2f} | {t.wall_seconds:.0f} |"
    )


def render(found: dict[Key, dict[str, arms.ArmScore]], tasks: TaskSet) -> str:
    """Render the side-by-side report.

    Parameters
    ----------
    found : dict
        From `load`.
    tasks : TaskSet
        The task file; its order is the report's order.

    Returns
    -------
    str
        Markdown: per task a row for each level and arm (found,
        correct, claims backed / unbacked, the harness's measured
        speedup with its interval, cost in dollars, wall seconds),
        then per level the totals of the skill set against the
        baseline. A combination with no run reads ``not run``.
    """
    lines = ["# Arm comparison", ""]
    head = (
        "| level | arm | found | correct | claims backed / unbacked "
        "| speedup (harness) | cost $ | wall s |"
    )
    rule = "|---|---|---|---|---|---|---|---|"
    for task in tasks.tasks:
        if not any(task.id in by for by in found.values()):
            continue
        lines += [f"## {task.id}", "", head, rule]
        lines += [
            _row(level, arm, found.get((level, arm), {}).get(task.id))
            for level in LEVELS
            for arm in ARMS
        ]
        lines.append("")
    lines += [
        "## Totals",
        "",
        "| level | arm | tasks | found | correct | claims backed / unbacked "
        "| speedup checks | cost $ | wall s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    dropped: dict[Level, set[str]] = {}
    for level in LEVELS:
        bad = {
            s.task_id
            for arm in ARMS
            for s in found.get((level, arm), {}).values()
            if s.harness_failure
        }
        dropped[level] = bad
        for arm in ARMS:
            by = found.get((level, arm))
            lines.append(
                _total_row(
                    level,
                    arm,
                    None
                    if by is None
                    else totals(list(by.values()), frozenset(bad)),
                )
            )
    for level in LEVELS:
        if dropped[level]:
            lines += [
                "",
                f"Excluded from both arms' {level} totals (harness failure "
                "in either arm): " + ", ".join(sorted(dropped[level])),
            ]
    lines += [
        "",
        "found: the known function named in the final answer or its file "
        "edited. correct: the tests pass on the final tree (n/a: nothing "
        "changed or not run). Claims are backed by a KEEP verdict (skill) "
        "or by timings of both versions the agent ran (baseline). "
        "Speedup: the harness's own A/B timing of the final tree against "
        "the base tree, ratio of medians with a 95% bootstrap interval.",
        "",
    ]
    return "\n".join(lines)
