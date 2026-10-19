"""Write ``scores.json`` and ``report.md`` for a run; diff two runs."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from runner import Meta
from scoring import TaskScore


class Summary(BaseModel):
    """Totals over a run.

    Attributes
    ----------
    tasks, passed : int
        Tasks scored and tasks with every applicable check true.
    trigger_hits, trigger_total : int
        Trigger tasks whose observed start matched the expectation, and
        all trigger tasks.
    verdict_ok, verdict_total : int
        Tasks whose verdict is accepted, and tasks that expect one.
    wrong_keeps : int
        KEEP verdicts the task does not accept. Must be zero.
    result_ok, result_total : int
        Correct ``Result:`` lines, over prove and find tasks.
    hotspot_ok, hotspot_total : int
        Known function named, over tasks with a known hotspot.
    fix_ok, fix_total : int
        Correct fix outcome, over prove and find tasks.
    tree_ok, tree_total : int
        Clean trees, over prove and find tasks.
    wording : dict of str to int
        Violations by rule.
    cost_usd, wall_seconds, api_seconds : float
        Summed over tasks.
    agent_turns : int
        Summed ``num_turns``.
    """

    model_config = ConfigDict(extra="forbid")

    tasks: int
    passed: int
    trigger_hits: int
    trigger_total: int
    verdict_ok: int
    verdict_total: int
    wrong_keeps: int
    result_ok: int
    result_total: int
    hotspot_ok: int
    hotspot_total: int
    fix_ok: int
    fix_total: int
    tree_ok: int
    tree_total: int
    wording: dict[str, int]
    cost_usd: float
    wall_seconds: float
    api_seconds: float
    agent_turns: int


def _count(values: list[bool | None]) -> tuple[int, int]:
    applicable = [v for v in values if v is not None]
    return sum(applicable), len(applicable)


def summarize(scores: list[TaskScore]) -> Summary:
    """Total a run's task scores.

    Parameters
    ----------
    scores : list of TaskScore
        The run's scores.

    Returns
    -------
    Summary
        Counts of passing checks over the tasks they apply to, and the
        summed cost, time and turns.
    """
    trig = _count([s.trigger_ok for s in scores if s.kind == "trigger"])
    verdict = _count([s.verdict_ok for s in scores])
    result = _count([s.result_line_ok for s in scores])
    hotspot = _count([s.hotspot_named for s in scores])
    fix = _count([s.fix_ok for s in scores])
    tree = _count([s.tree_ok for s in scores])
    wording = Counter(w.rule for s in scores for w in s.wording)
    return Summary(
        tasks=len(scores),
        passed=sum(s.passed for s in scores),
        trigger_hits=trig[0],
        trigger_total=trig[1],
        verdict_ok=verdict[0],
        verdict_total=verdict[1],
        wrong_keeps=sum(s.wrong_keep for s in scores),
        result_ok=result[0],
        result_total=result[1],
        hotspot_ok=hotspot[0],
        hotspot_total=hotspot[1],
        fix_ok=fix[0],
        fix_total=fix[1],
        tree_ok=tree[0],
        tree_total=tree[1],
        wording=dict(sorted(wording.items())),
        cost_usd=round(sum(s.cost_usd for s in scores), 2),
        wall_seconds=round(sum(s.wall_seconds for s in scores), 1),
        api_seconds=round(sum(s.api_seconds for s in scores), 1),
        agent_turns=sum(s.agent_turns for s in scores),
    )


def _mark(value: bool | None) -> str:
    if value is None:
        return "-"
    return "ok" if value else "FAIL"


def _table(scores: list[TaskScore]) -> list[str]:
    head = (
        "| Task | Pass | Trigger | Verdict | Result line | Hotspot "
        "| Fix tests/locks | Tree | Wording | Turns | Cost | Wall | Load |"
    )
    rows = [head, "|" + "---|" * 13]
    for s in scores:
        trigger = (
            "-"
            if s.trigger is None
            else ("started" if s.trigger else "no start")
        )
        if s.trigger_ok is False:
            trigger += " FAIL"
        verdict = s.verdict or "none"
        if s.verdict_ok is False:
            verdict += " FAIL"
        wording = (
            "ok"
            if not s.wording and s.kind != "trigger"
            else "-"
            if s.kind == "trigger"
            else f"{len(s.wording)} FAIL"
        )
        rows.append(
            f"| {s.task_id} | {_mark(s.passed)} | {trigger} | "
            f"{verdict if s.kind != 'trigger' else '-'} | "
            f"{_mark(s.result_line_ok)} | {_mark(s.hotspot_named)} | "
            f"{_mark(s.fix_ok)} | {_mark(s.tree_ok)} | {wording} | "
            f"{s.agent_turns} | ${s.cost_usd:.2f} | "
            f"{s.wall_seconds:.0f}s | {s.load_start:.1f} |"
        )
    return rows


def _summary_lines(m: Summary) -> list[str]:
    wording = ", ".join(f"{k} {v}" for k, v in m.wording.items()) or "none"
    return [
        f"- Tasks passed: {m.passed} of {m.tasks}",
        f"- Trigger set: {m.trigger_hits} of {m.trigger_total} matched",
        f"- Verdict accepted: {m.verdict_ok} of {m.verdict_total}; "
        f"wrong KEEP: {m.wrong_keeps}",
        f"- Result line correct: {m.result_ok} of {m.result_total}",
        f"- Hotspot ranked first by the profile and named: "
        f"{m.hotspot_ok} of {m.hotspot_total}",
        f"- Fix passes tests, no locked file touched (prove: no code "
        f"changed): {m.fix_ok} of {m.fix_total}",
        f"- Tree clean: {m.tree_ok} of {m.tree_total}",
        f"- Wording violations: {wording}",
        f"- Cost ${m.cost_usd:.2f}, {m.agent_turns} agent turns, "
        f"{m.wall_seconds:.0f}s of `claude` wall time "
        f"({m.api_seconds:.0f}s API)",
    ]


def _details(scores: list[TaskScore]) -> list[str]:
    lines: list[str] = []
    for s in scores:
        notes: list[str] = []
        if s.hotspot_named is False:
            notes.append(f"hotspot: {s.hotspot_detail}")
        if s.fix_ok is False:
            notes.append(f"fix: {s.fix_detail}")
        if s.tree_ok is False:
            notes.append(f"tree: {s.tree_detail}")
        if s.result_line_ok is False:
            notes.append(f"Result line: {s.result_line!r}")
        notes += [f"{w.rule}: {w.text}" for w in s.wording]
        if s.wrong_keep:
            notes.append("WRONG KEEP")
        if notes:
            lines.append(f"- `{s.task_id}`")
            lines += [f"  - {n}" for n in notes]
    return lines


def compare(previous: dict[str, Any], current: list[TaskScore]) -> list[str]:
    """Describe what changed since a previous ``scores.json``.

    Parameters
    ----------
    previous : dict
        A parsed ``scores.json``.
    current : list of TaskScore
        This run's scores.

    Returns
    -------
    list of str
        Markdown lines: tasks whose pass state changed, tasks only in
        one run, and the change in cost and wall time over the tasks
        both runs scored.
    """
    old = {t["task_id"]: t for t in previous.get("tasks", [])}
    now = {s.task_id: s for s in current}
    shared = [t for t in now if t in old]
    lines = [f"Previous run: `{previous.get('run_id', '?')}`", ""]
    changed = [
        f"- `{t}`: {_mark(old[t]['passed'])} to {_mark(now[t].passed)}"
        for t in shared
        if old[t]["passed"] != now[t].passed
    ]
    lines += changed or ["- no task changed its pass state"]
    w_old = sum(len(old[t]["wording"]) for t in shared)
    w_new = sum(len(now[t].wording) for t in shared)
    lines.append(
        f"- wording violations over the {len(shared)} shared tasks: "
        f"{w_old} before, {w_new} now"
    )
    skipped = len(set(old) - set(now))
    added = len(set(now) - set(old))
    if skipped or added:
        lines.append(
            f"- {skipped} tasks of the previous run were not run here, "
            f"{added} tasks here were not in it"
        )
    d_cost = sum(now[t].cost_usd - old[t]["cost_usd"] for t in shared)
    d_wall = sum(now[t].wall_seconds - old[t]["wall_seconds"] for t in shared)
    lines.append(
        f"- cost over the shared tasks {d_cost:+.2f} USD, wall {d_wall:+.0f}s"
    )
    return lines


def write(
    run_dir: Path, scores: list[TaskScore], previous: Path | None
) -> None:
    """Write ``scores.json`` and ``report.md`` into a run directory.

    Parameters
    ----------
    run_dir : Path
        The run directory.
    scores : list of TaskScore
        The scored tasks.
    previous : Path or None
        An earlier ``scores.json`` to compare against.
    """
    metas = [
        Meta.model_validate_json(p.read_text(encoding="utf-8"))
        for p in sorted(run_dir.glob("*/meta.json"))
    ]
    summary = summarize(scores)
    header = {
        "run_id": run_dir.name,
        "skill_sha": metas[0].skill_sha if metas else "",
        "claude_version": metas[0].claude_version if metas else "",
    }
    payload = {
        **header,
        "summary": summary.model_dump(),
        "tasks": [s.model_dump() for s in scores],
    }
    (run_dir / "scores.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"# Skill eval `{run_dir.name}`",
        "",
        f"Skill `{header['skill_sha']}`, "
        f"Claude Code {header['claude_version']}.",
        "",
        *_summary_lines(summary),
        "",
        *_table(scores),
        "",
    ]
    details = _details(scores)
    if details:
        lines += ["## Failures", "", *details, ""]
    if previous is not None:
        old = json.loads(previous.read_text(encoding="utf-8"))
        lines += ["## Against the previous run", "", *compare(old, scores)]
        lines.append("")
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
