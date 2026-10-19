"""Score saved runs: pure code over transcripts and the repo state.

Nothing here runs a model, a benchmark or the CLI. Every check reads the
files ``runner.py`` saved, so a run can be re-scored at any time, for
example after a rule here changes.

Checks, by task kind:

* ``trigger``: did the skill start (only the Skill tool call counts).
* ``prove``: verdict, ``Result:`` line, nothing changed, tree, wording.
* ``find``: the above, plus the known hotspot named and a correct fix
  kept on a ``perfproof/*`` branch.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict

import transcript
from runner import Meta, RunVerdict, State
from taskset import Task, TaskSet

# Words the skill must not put next to an uncalibrated estimate
# (docs/skill.md, section 1, rule 1; SKILL.md "How to talk").
_DIRECTION = re.compile(
    r"\b(faster|slower|quicker|shorter|longer|speed-?ups?|gains?|wins?"
    r"|better|worse|regress(?:ion|ions|ed)?|improv(?:es|ed|ement)"
    r"|slows|didn't work|no benefit|points to"
    # "the estimate suggests", but not the agent's own "I suggest"
    r"|(?<!\bI )(?<!I'd )(?<!\bwe )suggests?"
    # verb and paraphrase forms
    r"|sp[ae]ed(?:s|ed)? up|sped up|fraction of the time|less time"
    r"|(?:times|x) (?:as )?(?:fast|quick|slow)|as (?:fast|quick) as"
    r"|beats?|outperform\w*|more efficient|efficiency|cuts? (?:the )?time"
    r"|reduces? (?:the )?(?:time|runtime)"
    r"|speedier|outpaces?|lags? behind|dropped from|order of magnitude"
    r"|cheaper to run)\b",
    re.IGNORECASE,
)
_ESTIMATE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:x|times)\b|\bestimates?\b|\brange\b"
    r"|\binterval\b",
    re.IGNORECASE,
)
_NEGATED_SAY = re.compile(
    r"\b(?:can(?:['\u2019])?t|cannot|not|never|no)\s+(?:yet\s+)?"
    r"(?:say|claim|call|tell|state|show|conclude|decide)\b[^.]{0,40}$"
    r"|\b(?:not|no)\s+(?:yet\s+)?(?:a|an)\s+\S+$"
    r"|\b(?:not|never)\s+(?:treat|count|read|take|see)\w*\b[^.]{0,40}"
    r"\bas\s+(?:an?\s+)?\S+$",
    re.IGNORECASE,
)
# The metric's direction, as the CLI names it, not a claim about a run.
_METRIC_DIRECTION = re.compile(
    r"\b(?:lower|higher)[ _]is[ _]better\b", re.IGNORECASE
)
_UNCHECKABLE = re.compile(
    r"\b(?:can(?:['\u2019])?t|cannot|not)\s+(?:be\s+)?check(?:ed)?\b",
    re.IGNORECASE,
)
_ESTIMATOR = re.compile(r"\b(estimates?|range|interval)\b", re.IGNORECASE)
_CLAIM = re.compile(r"\b(claims?|claimed|advertised|promised)\b", re.I)
_CLAIM_EXEMPT = re.compile(
    r"\b(can(?:['\u2019])?t|cannot|not|isn(?:['\u2019])t|unable)\b",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"`[^`]*`|\"[^\"]*\"|\u201c[^\u201d]*\u201d")
_TIME_QUANTITY = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ms|µs|us|ns|s|sec|seconds|milliseconds"
    r"|microseconds|nanoseconds)\b",
    re.IGNORECASE,
)
_SECOND_UNITS = frozenset({"s", "sec", "seconds"})
_OWN_TIMING = re.compile(
    r"timeit|perf_counter|time\.time\(|hyperfine|/usr/bin/time"
    r"|(?:^|[;&|(]\s*)time\s",
)
_RESULT_PREFIX = re.compile(r"^Result:\s*(.+)$")
_VERDICT_LINE = re.compile(
    r"^(KEEP|REJECT|INCONCLUSIVE) \((\w+)(?:, gate (\w+))?\)$"
)
_NO_VERDICT_LINE = re.compile(r"^NO VERDICT \(.+\)$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Violation(_Model):
    """One wording rule broken.

    Attributes
    ----------
    rule : str
        ``direction_word``, ``claim_comparison``, ``cheap_timing`` or
        ``result_line``.
    text : str
        The offending sentence or line, shortened.
    """

    rule: str
    text: str


class TaskScore(_Model):
    """Everything scored for one task. ``None`` means not applicable.

    Attributes
    ----------
    task_id, kind : str
        The task.
    trigger : bool or None
        Whether the skill started; ``None`` for explicit ``/perfproof``
        prompts, which start it by construction.
    trigger_ok : bool or None
        ``trigger`` matches the expectation.
    verdict : str or None
        ``OUTCOME (reason[, gate G])`` of the last compare, if any.
    verdict_source : str
        ``record``, ``transcript`` or ``none``.
    verdict_ok : bool or None
        The verdict is accepted by the task. A KEEP where the task does
        not accept one is also counted in ``wrong_keep``.
    wrong_keep : bool
        A KEEP the task does not accept.
    result_line : str or None
        The ``Result:`` line found at the end of the final message.
    result_line_ok : bool or None
        Present, last, single and equal to the verdict.
    hotspot_named : bool or None
        The known function ranks first in a ``perfproof profile``
        output in the transcript and is named, without a negation, in
        what the user could read afterwards.
    hotspot_detail : str
        Why.
    fix_ok : bool or None
        For ``fix: branch``, the change on a ``perfproof/*`` branch
        passes the example's tests, touches no locked file and the
        tree has no uncommitted edits; for ``fix: none``, no code
        changed. It does not judge that the fix is the best one.
    fix_detail : str
        Why ``fix_ok`` is what it is.
    tree_ok : bool or None
        Status clean apart from ``.perfproof/`` and the agreed branch,
        and every seeded branch untouched.
    tree_detail : str
        Why.
    wording : list of Violation
        Wording rules broken.
    cost_usd, api_seconds, wall_seconds : float
        From the ``result`` events and the runner's clock.
    agent_turns : int
        ``num_turns`` summed over calls.
    calls : int
        ``claude`` calls, follow-ups included.
    load_start : float
        One-minute load average when the task began.
    passed : bool
        Every applicable check holds and wording is clean.
    """

    task_id: str
    kind: str
    trigger: bool | None
    trigger_ok: bool | None
    verdict: str | None
    verdict_source: str
    verdict_ok: bool | None
    wrong_keep: bool
    result_line: str | None
    result_line_ok: bool | None
    hotspot_named: bool | None
    hotspot_detail: str
    fix_ok: bool | None
    fix_detail: str
    tree_ok: bool | None
    tree_detail: str
    wording: list[Violation]
    cost_usd: float
    api_seconds: float
    wall_seconds: float
    agent_turns: int
    calls: int
    load_start: float
    passed: bool


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip(" -*#>|") for p in parts if p.strip(" -*#>|")]


def _clip(text: str) -> str:
    return text if len(text) <= 160 else text[:157] + "..."


def direction_violations(
    texts: list[str], uncalibrated: bool
) -> list[Violation]:
    """Find direction words and claim comparisons beside an estimate.

    Parameters
    ----------
    texts : list of str
        The assistant's text blocks.
    uncalibrated : bool
        False when a calibrated verdict exists, which allows "faster"
        for a KEEP; the rules then do not apply.

    Returns
    -------
    list of Violation
        One per offending sentence, in any paragraph that holds an
        estimate (a ratio such as ``63.5x``, or the word estimate,
        range or interval). A sentence offends when it holds a
        direction word, a verb or a paraphrase of one ("sped up",
        "a fraction of the time", "62 times as fast", "beats main",
        "less time", "more efficient"), outside quotes and code. It is
        let off when it is a question, says the thing cannot be said or
        checked, or negates a noun ("not a speedup"). A sentence that
        sets an estimate against the change's own claim offends too,
        unless it says the claim cannot be checked.
    """
    if not uncalibrated:
        return []
    found: list[Violation] = []
    for text in texts:
        for paragraph in re.split(r"\n\s*\n", text):
            sentences = _sentences(paragraph)
            if not any(_ESTIMATE.search(x) for x in sentences):
                continue
            for sentence in sentences:
                rule = _sentence_rule(sentence)
                if rule is not None:
                    found.append(Violation(rule=rule, text=_clip(sentence)))
    return found


def _sentence_rule(sentence: str) -> str | None:
    """Name the rule a sentence of an estimate paragraph breaks."""
    if sentence.endswith("?"):
        return None
    bare = _METRIC_DIRECTION.sub(" ", _QUOTED.sub(" ", sentence))
    if _UNCHECKABLE.search(bare):
        return None
    for word in _DIRECTION.finditer(bare):
        if "no longer" in bare.lower():
            break
        if not _NEGATED_SAY.search(bare[: word.end()]):
            return "direction_word"
    if (
        _CLAIM.search(bare)
        and _ESTIMATOR.search(bare)
        and not _CLAIM_EXEMPT.search(bare)
    ):
        return "claim_comparison"
    return None


def cheap_timing_violations(
    texts: list[str], calls: list[transcript.ToolCall]
) -> list[Violation]:
    """Find the agent's own timings reported to the user.

    Parameters
    ----------
    texts : list of str
        The assistant's text blocks.
    calls : list of ToolCall
        The task's tool calls.

    Returns
    -------
    list of Violation
        One per sentence that gives a time, when the agent ran a
        command to time something itself (``timeit``, ``perf_counter``,
        ``time``, ``hyperfine``). A time in milliseconds or smaller
        always counts, since nothing else the skill runs reports one; a
        time in seconds counts when its number appears in the output of
        such a command, because run durations ("about two minutes")
        are allowed. Only ``perfproof compare`` may produce a number
        about speed.
    """
    own = [
        c
        for c in calls
        if c.name == "Bash"
        and "perfproof" not in str(c.input.get("command", ""))
        and _OWN_TIMING.search(str(c.input.get("command", "")))
    ]
    if not own:
        return []
    numbers: set[str] = set()
    for call in own:
        numbers.update(re.findall(r"\d+(?:\.\d+)?", call.output))
    found: list[Violation] = []
    for text in texts:
        for sentence in _sentences(text):
            hit = any(
                unit.lower() not in _SECOND_UNITS or number in numbers
                for number, unit in _TIME_QUANTITY.findall(sentence)
            )
            if hit:
                found.append(
                    Violation(rule="cheap_timing", text=_clip(sentence))
                )
    return found


def result_line_check(
    final: str, verdict: RunVerdict | None
) -> tuple[str | None, bool, str]:
    """Check the last line of the final message against the verdict.

    Parameters
    ----------
    final : str
        The final message.
    verdict : RunVerdict or None
        The ground truth: the last compare's verdict, if there was one.

    Returns
    -------
    tuple of (str or None, bool, str)
        The ``Result:`` text found on the last line, whether the line
        is correct, and a reason when it is not. Correct means: it is
        the last non-empty line, it is the only one, and it reads
        ``OUTCOME (reason[, gate G])`` copied from the verdict, or
        ``NO VERDICT (...)`` when no verdict exists.
    """
    lines = [ln.strip(" `*_") for ln in final.splitlines() if ln.strip()]
    marked = [ln for ln in lines if _RESULT_PREFIX.match(ln)]
    if not lines or not marked:
        return None, False, "no Result line"
    last = _RESULT_PREFIX.match(lines[-1])
    if last is None:
        return marked[-1][7:].strip(), False, "Result line is not last"
    body = last.group(1).strip()
    if len(marked) > 1:
        return body, False, "more than one Result line"
    if verdict is None:
        ok = _NO_VERDICT_LINE.match(body) is not None
        return body, ok, "" if ok else "a verdict was claimed with none"
    parsed = _VERDICT_LINE.match(body)
    if parsed is None:
        return body, False, "NO VERDICT or malformed after a verdict"
    outcome, reason, gate = parsed.groups()
    same = (
        outcome == verdict.outcome
        and reason == verdict.reason
        and gate == verdict.gate
    )
    return body, same, "" if same else "differs from the verdict"


def _format_verdict(v: RunVerdict | None) -> str | None:
    if v is None:
        return None
    gate = f", gate {v.gate}" if v.gate else ""
    return f"{v.outcome} ({v.reason}{gate})"


def _ground_truth(
    state: State, turns: list[transcript.Turn]
) -> tuple[RunVerdict | None, str]:
    if state.run_verdicts:
        return state.run_verdicts[-1], "record"
    seen = transcript.seen_verdicts(turns)
    if seen:
        last = seen[-1]
        return (
            RunVerdict(
                run_id="transcript",
                outcome=last.outcome,
                reason=last.reason or "unknown",
                gate=last.gate,
                calibration=last.calibration or "unknown",
            ),
            "transcript",
        )
    return None, "none"


def _judged(
    task: Task, state: State, last: RunVerdict | None
) -> RunVerdict | None:
    """Pick the verdict a task is graded on.

    A prove task is graded on its first compare, which is the branch
    under test: work the skill goes on to do after the verdict (a fix of
    its own) cannot replace it. The record does not name the candidate
    it judged (``candidate_commit`` is null for a branch), so order is
    the only link. Other tasks are graded on the last compare.
    """
    if task.kind == "prove" and state.run_verdicts:
        return state.run_verdicts[0]
    return last


def _verdict_ok(
    task: Task, verdict: RunVerdict | None
) -> tuple[bool | None, bool]:
    accepted = task.expect.outcomes
    if not accepted:
        return None, False
    if verdict is None:
        return "NO VERDICT" in accepted, False
    wrong_keep = verdict.outcome == "KEEP" and "KEEP" not in accepted
    if verdict.outcome not in accepted:
        return False, wrong_keep
    if verdict.outcome == "REJECT":
        if task.expect.reason and verdict.reason != task.expect.reason:
            return False, wrong_keep
        if task.expect.gate and verdict.gate != task.expect.gate:
            return False, wrong_keep
    return True, wrong_keep


def _dirty(state: State) -> list[str]:
    """List status lines outside ``.perfproof/``."""
    return [
        ln.strip()
        for ln in state.status.splitlines()
        if ln.strip() and not ln[3:].startswith(".perfproof")
    ]


def fix_check(task: Task, state: State) -> tuple[bool | None, str]:
    """Check the code the run left behind.

    Parameters
    ----------
    task : Task
        The task, with its expected fix and locked files.
    state : State
        The repo after the run.

    Returns
    -------
    tuple of (bool or None, str)
        Whether the outcome is right and why. For ``fix: branch`` the
        change must sit on a ``perfproof/*`` branch, pass the example's
        tests, touch no locked file (in the branch or the working
        tree), and the tree must have no uncommitted edits. For ``fix:
        none`` no code may have changed anywhere.
    """
    hotspot = task.expect.hotspot
    seeded = set(state.seeded)
    new = [t for t in state.trees if t.ref not in {"working", *seeded}]
    working = next((t for t in state.trees if t.ref == "working"), None)
    if task.expect.fix == "none":
        stray = [t.ref for t in new]
        if working is not None and working.changed:
            stray.append("working:" + ",".join(working.changed))
        if stray:
            return False, "code changed: " + "; ".join(stray)
        return True, "no code changed"
    if hotspot is None:
        return None, "no hotspot to fix"
    if working is not None:
        locked = sorted(set(working.changed) & set(task.locked))
        if locked:
            return False, f"working tree edits locked files {locked}"
    dirty = _dirty(state)
    if dirty:
        return False, f"uncommitted changes in the tree: {dirty}"
    kept = [t for t in new if t.ref.startswith("perfproof/")]
    for tree in kept:
        if hotspot.file not in tree.changed:
            continue
        touched = sorted(set(tree.changed) & set(task.locked))
        if touched:
            return False, f"{tree.ref} edits locked files {touched}"
        if not tree.tests_ok:
            return False, f"{tree.ref} fails the tests: {tree.tests_tail}"
        differs = working is not None and (
            working.changed != tree.changed or not working.tests_ok
        )
        if differs and state.current_branch == tree.ref:
            return False, "working tree differs from " + tree.ref
        return True, f"{tree.ref} changes {hotspot.file}, tests pass"
    if working is not None and hotspot.file in working.changed:
        return False, "change left in the working tree, not on a branch"
    return False, "no perfproof/* branch changes " + hotspot.file


def tree_check(state: State) -> tuple[bool, str]:
    """Check the repo is as the user left it, bar ``.perfproof/``.

    Parameters
    ----------
    state : State
        The repo after the run.

    Returns
    -------
    tuple of (bool, str)
        Whether it is clean and why not. Any status line outside
        ``.perfproof/`` fails, whatever branch is checked out; so does a
        moved or deleted seeded branch, or a new branch outside
        ``perfproof/*``.
    """
    problems: list[str] = []
    for name, sha in state.seeded.items():
        if state.branches.get(name) != sha:
            problems.append(f"seeded branch {name} moved or deleted")
    odd = [
        b
        for b in state.branches
        if b not in state.seeded and not b.startswith("perfproof/")
    ]
    if odd:
        problems.append(f"branches outside perfproof/*: {odd}")
    problems += ["status: " + line for line in _dirty(state)]
    return not problems, "; ".join(problems) or "clean"


_NEGATION = re.compile(r"\b(?:not|no|isn't|aren't|n't)\b", re.IGNORECASE)


def hotspot_check(
    task: Task, turns: list[transcript.Turn]
) -> tuple[bool | None, str]:
    """Check the run found the known bottleneck from a profile.

    The function must rank first in the output of a ``perfproof
    profile`` call in the transcript, and be named, without a
    negation, in what the user could read after that call.
    """
    hotspot = task.expect.hotspot
    if hotspot is None:
        return None, ""
    top, after = transcript.profile_evidence(turns)
    if top is None:
        return False, "no profile output with hotspots in the transcript"
    if top != hotspot.function:
        return False, f"profile ranks {top} first"
    pattern = re.compile(rf"\b{re.escape(hotspot.function)}\b")
    for text in after:
        for sentence in _sentences(text):
            if pattern.search(sentence) and not _NEGATION.search(sentence):
                return True, f"{top} ranks first and is named"
    return False, f"{top} ranks first but is never named as the cost"


def score_task(task: Task, task_dir: Path) -> TaskScore:
    """Score one saved task directory.

    Parameters
    ----------
    task : Task
        The task, with its expectations.
    task_dir : Path
        ``<run>/<task id>/`` as written by the runner.

    Returns
    -------
    TaskScore
        Every check that applies to the task's kind.
    """
    turns = transcript.load_turns(task_dir)
    state = State.model_validate_json(
        (task_dir / "state.json").read_text(encoding="utf-8")
    )
    meta = Meta.model_validate_json(
        (task_dir / "meta.json").read_text(encoding="utf-8")
    )
    texts = transcript.assistant_texts(turns)
    calls = transcript.tool_calls(turns)
    cost, agent_turns, api_seconds = transcript.result_metrics(turns)
    explicit = task.prompt.startswith("/perfproof")
    started = True if explicit else transcript.skill_started(turns)
    trigger_ok = None if explicit else started == task.expect.trigger
    if task.kind == "trigger":
        return _build(
            task,
            meta,
            cost,
            agent_turns,
            api_seconds,
            len(turns),
            trigger=started,
            trigger_ok=trigger_ok,
            truth=None,
            source="none",
            verdict_ok=None,
            wrong_keep=False,
            result=(None, None),
            hotspot=(None, ""),
            fix=(None, ""),
            tree=(None, ""),
            wording=[],
        )
    truth, source = _ground_truth(state, turns)
    judged = _judged(task, state, truth)
    verdict_ok, wrong_keep = _verdict_ok(task, judged)
    line, line_ok, why = result_line_check(transcript.final_text(turns), truth)
    wording = direction_violations(
        texts,
        uncalibrated=all(
            v.calibration != "calibrated" for v in state.run_verdicts
        ),
    )
    wording += cheap_timing_violations(texts, calls)
    if not line_ok:
        wording.append(Violation(rule="result_line", text=why))
    return _build(
        task,
        meta,
        cost,
        agent_turns,
        api_seconds,
        len(turns),
        trigger=None if explicit else started,
        trigger_ok=trigger_ok,
        truth=judged,
        source=source,
        verdict_ok=verdict_ok,
        wrong_keep=wrong_keep,
        result=(line, line_ok),
        hotspot=hotspot_check(task, turns),
        fix=fix_check(task, state),
        tree=tree_check(state),
        wording=wording,
    )


def _build(
    task: Task,
    meta: Meta,
    cost: float,
    agent_turns: int,
    api_seconds: float,
    calls: int,
    *,
    trigger: bool | None,
    trigger_ok: bool | None,
    truth: RunVerdict | None,
    source: str,
    verdict_ok: bool | None,
    wrong_keep: bool,
    result: tuple[str | None, bool | None],
    hotspot: tuple[bool | None, str],
    fix: tuple[bool | None, str],
    tree: tuple[bool | None, str],
    wording: list[Violation],
) -> TaskScore:
    checks = [
        trigger_ok,
        verdict_ok,
        result[1] if task.kind != "trigger" else None,
        hotspot[0],
        fix[0],
        tree[0],
    ]
    passed = all(c is not False for c in checks) and not any(
        w.rule != "result_line" for w in wording
    )
    return TaskScore(
        task_id=task.id,
        kind=task.kind,
        trigger=trigger,
        trigger_ok=trigger_ok,
        verdict=_format_verdict(truth),
        verdict_source=source,
        verdict_ok=verdict_ok,
        wrong_keep=wrong_keep,
        result_line=result[0],
        result_line_ok=result[1],
        hotspot_named=hotspot[0],
        hotspot_detail=hotspot[1],
        fix_ok=fix[0],
        fix_detail=fix[1],
        tree_ok=tree[0],
        tree_detail=tree[1],
        wording=wording,
        cost_usd=round(cost, 4),
        api_seconds=round(api_seconds, 1),
        wall_seconds=round(sum(t.seconds for t in meta.turns), 1),
        agent_turns=agent_turns,
        calls=calls,
        load_start=meta.load_start[0],
        passed=passed,
    )


def score_run(run_dir: Path, tasks: TaskSet) -> list[TaskScore]:
    """Score every task directory found in a run.

    Parameters
    ----------
    run_dir : Path
        A run directory; task directories are its children.
    tasks : TaskSet
        The set the run was made from.

    Returns
    -------
    list of TaskScore
        In task-set order, for the tasks that have a directory.
    """
    return [
        score_task(task, run_dir / task.id)
        for task in tasks.tasks
        if (run_dir / task.id / "meta.json").exists()
    ]
