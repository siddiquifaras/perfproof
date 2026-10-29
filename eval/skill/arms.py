"""Score a run of either arm on the same terms, for the arm comparison.

``scoring.py`` judges the skill against its own rules (verdicts,
``Result:`` line, wording). Plain Claude Code has none of those, so this
module scores both arms with the checks that make sense without the
tool: did the run find the known bottleneck, is the final tree correct,
are the speed claims in its final answer backed, and how much faster is
the final tree, measured by the harness (`speedcheck`). Nothing here
runs a model or a benchmark; it reads what the runner saved.

Rules
-----
*Found.* The run names a known hotspot function (the primary one or an
accepted one, in the owner-qualified form `scoring.named_in` uses,
without a negation) in its final answer, or edits the hotspot's file
in the final tree: the file is in ``State.base_changed`` or in the
``changed`` list of any tree. Function-level edits are not
distinguished: the state keeps paths, not diffs.

*Correct.* The project's tests (the example's tests or the manifest's
oracle) pass on the final tree, ``State.final_ref``: a kept
``perfproof/*`` branch, else the working tree. ``None`` when nothing
changed (there is no tree to judge) or the tests did not run there.

*Claims.* A claim is a sentence of the final answer that says the code
got faster: a percentage or ``N x`` beside "faster", "speedup" or
"quicker", or one of "faster", "quicker", "speedup", "speeds up",
"sped up", "halved", "twice as fast", "~Nx", "N% reduction",
"performance gain/improvement/boost", "now takes Xs instead of Ys", or
reducing the runtime, time or latency. Not claims, in both arms:
questions; a sentence that reports a REJECT or INCONCLUSIVE verdict or
says the effect is within noise, under or below the bar or not
significant; and a speed phrase the sentence itself negates ("not
faster", "no speedup", "isn't quicker": a negation up to three words
before it, no punctuation between, so "No regressions; 40% faster." is
a claim). Hedged claims ("should be faster") are claims. Fenced code is
skipped.

*Backed.* Skill arm: the run holds a perfproof ``KEEP`` verdict (in the
run records or in the transcript). Baseline arm: the agent timed both
versions itself, meaning a Bash timing command (``timeit``,
``perf_counter``, ``hyperfine``, ``time``, a benchmark runner) with
output ran before its first edit of the code and another ran after its
last edit. Only this structure is checked, not that the sentence's
number matches the timings, so it is generous to the baseline; the
harness's own speed check is the measure of how much faster. A claim
that is not backed is ``unbacked``.

*Contamination.* Baseline only: the init event lists a perfproof skill;
a Bash command runs ``perfproof`` or imports it; the Skill tool is
called for it; or any Read, Glob, Grep or Bash input holds a path with
``perfproof/`` or ``SKILL.md``. The baseline's sandbox cannot read the
perfproof environment or sources and its ``python`` cannot import the
package (`runner.agent_deny`, `runner.baseline_site`); this scan is the
second line.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

import scoring
import speedcheck
import transcript
from runner import Meta, State
from taskset import ARMS, LEVELS, Arm, Level, TaskSet
from taskset import Task as TaskSpec

ARM_SCORES_FILE = "arm-scores.json"
"""Written into a run directory by `write_arm_scores`."""

_FENCE = re.compile(r"```.*?```", re.DOTALL)
# A negation counts only when it modifies the speed phrase itself: up to
# three words before it, no punctuation between ("not faster", "isn't
# quicker", "no speedup"). "No regressions; 40% faster." is a claim.
_NEGATES_PHRASE = re.compile(
    r"(?:\b(?:not|no|never|cannot|without|hardly|barely|unable to)"
    r"|n['" + chr(0x2019) + r"]t)(?:\s+[\w-]+){0,3}\s*$",
    re.IGNORECASE,
)
# Sentences that report a verdict that does not support a speed-up, or
# an effect too small to call: not claims whatever else they say.
_NOT_A_CLAIM = re.compile(
    r"(?-i:\bREJECT(?:ED|S)?\b)|\binconclusive\b"
    r"|\b(?:within|inside|in|below|under)\s+(?:the\s+)?"
    r"(?:run[- ]to[- ]run\s+|measurement\s+)?noise\b"
    r"|\b(?:under|below|beneath|within|inside|short of)\s+the\s+"
    r"(?:\d+(?:\.\d+)?\s*%\s*)?(?:bar|threshold|margin)\b"
    r"|\b(?:not|isn't|aren't|wasn't)\s+(?:statistically\s+)?significant"
    r"|\binsignificant\b|\bnot significant\b"
    r"|\binterval\s+(?:spans|includes|contains|crosses|straddles)\s+1\b"
    r"|\bspans\s+1\b",
    re.IGNORECASE,
)
_NUMBER = r"\d+(?:\.\d+)?"
_TIMES = "(?:x|" + chr(0xD7) + ")"
_UNIT = r"(?:ms|us|ns|sec(?:onds?)?|s|min(?:utes?)?)\b"
_COUNT = r"(?:two|three|four|five|six|seven|eight|nine|ten)"
_CLAIM = re.compile(
    rf"\b{_NUMBER}\s*%\s*(?:faster|quicker|speed|less time)"
    rf"|\b{_NUMBER}\s*(?:{_TIMES}|times)\s*(?:as\s+)?(?:faster|quicker)"
    rf"|\b{_NUMBER}\s*{_TIMES}\s*speed-?ups?"
    rf"|~\s*{_NUMBER}\s*{_TIMES}"
    rf"|\b(?:twice|thrice|{_COUNT}\s+times)\s+as\s+(?:fast|quick)"
    rf"|\b{_COUNT}\s+times\s+(?:faster|quicker)"
    r"|\b(?:halved|halves|halving)\b|\bcut\s+in\s+half\b"
    rf"|\b{_NUMBER}\s*%\s*(?:reduction|decrease|drop)\b"
    r"(?!\s+in\s+(?:memory|size|allocations?|ram|disk))"
    rf"|\btakes?\s+{_NUMBER}\s*{_UNIT}\s+(?:instead of|rather than|vs\.?"
    rf"|down from)\s+{_NUMBER}"
    rf"|\bfrom\s+{_NUMBER}\s*{_UNIT}\s+(?:down\s+)?to\s+{_NUMBER}\s*{_UNIT}"
    r"|\b(?:faster|quicker|speed-?ups?|speeds? up|sped up|speeding up)\b"
    r"|\bperformance\s+(?:gains?|improvements?|boost)\b"
    r"|\b(?:reduc\w+|cut\w*|lower\w*|shorten\w*)\s+(?:the\s+)?"
    r"(?:run\s?time|time|latency|duration)\b",
    re.IGNORECASE,
)
# A command word or module, not a path: run directories live under a
# path that contains the project's name.
_USES_PERFPROOF = re.compile(
    r"(?:^|[\s;&|(`])(?:-m\s+)?perfproof(?=\s|$)"
    r"|\b(?:import|from)\s+perfproof\b"
)
_PERFPROOF_PATH = re.compile(r"perfproof/|SKILL\.md")
_TIMING = re.compile(
    r"timeit|perf_counter|process_time|monotonic\(|time\.time\(|hyperfine"
    r"|/usr/bin/time|(?:^|[;&|(]\s*)time\s|--benchmark|go test[^\n]*-bench"
    r"|cargo bench|\bbench(?:mark)?s?\.(?:py|js|rb|sh)\b"
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Claim(_Model):
    """One speed claim of the final answer.

    Attributes
    ----------
    text : str
        The sentence, shortened.
    backed : bool
        Whether a measurement of the run supports it (see the module
        rules).
    """

    text: str
    backed: bool


class ArmScore(_Model):
    """One task of one arm and level, scored on common terms.

    Attributes
    ----------
    task_id : str
        The task.
    arm : {"skill", "baseline"}
        Which arm ran.
    level : {"vague", "detailed"}
        Which prompt level.
    found : bool or None
        Hotspot named or its file edited; ``None`` without a known
        hotspot.
    found_detail : str
        How it was found, or why not.
    correct : bool or None
        Tests pass on the final tree; ``None`` if nothing changed or
        the tests did not run there.
    correct_detail : str
        Why.
    claims : list of Claim
        Speed claims of the final answer.
    claims_backed, claims_unbacked : int
        Counts of ``claims``.
    speed : SpeedResult or None
        The harness's measurement; ``None`` when the task has no
        ``speed_check``.
    contaminated : list of str
        Baseline arm: signs perfproof was available or used.
    cost_usd : float
        From the ``result`` events.
    wall_seconds : float
        Sum of the calls' wall times.
    calls : int
        ``claude`` calls, follow-ups included.
    """

    task_id: str
    arm: Arm
    level: Level
    found: bool | None
    found_detail: str
    correct: bool | None
    correct_detail: str
    claims: list[Claim]
    claims_backed: int
    claims_unbacked: int
    speed: speedcheck.SpeedResult | None
    contaminated: list[str]
    cost_usd: float
    wall_seconds: float
    calls: int


def extract_claims(final: str) -> list[str]:
    """Find the speed claims in a final answer.

    Parameters
    ----------
    final : str
        The last message the user saw.

    Returns
    -------
    list of str
        The claim sentences, in order, per the module rules.
    """
    text = _FENCE.sub(" ", final)
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    found: list[str] = []
    for part in parts:
        sentence = part.strip(" -*#>|`")
        if (
            sentence
            and not sentence.endswith("?")
            and not _NOT_A_CLAIM.search(sentence)
            and any(
                not _NEGATES_PHRASE.search(sentence[: m.start()][-40:])
                for m in _CLAIM.finditer(sentence)
            )
        ):
            found.append(
                sentence if len(sentence) <= 160 else sentence[:157] + "..."
            )
    return found


def _is_edit(call: transcript.ToolCall, root: str | None) -> bool:
    if call.name == "Bash" and str(
        call.input.get("command", "")
    ).lstrip().startswith("git "):
        return False
    return transcript.is_code_edit(call.name, call.input, root)


def timed_both_versions(
    calls: list[transcript.ToolCall], root: str | None
) -> bool:
    """Tell whether the agent timed the code before and after editing.

    Parameters
    ----------
    calls : list of ToolCall
        The task's tool calls, in order.
    root : str or None
        The project's root, when known.

    Returns
    -------
    bool
        True if a timing command with a number in its output ran before
        the first edit of the code and another ran after the last. A
        ``git`` command is not an edit here (a commit after the last
        timing does not undo it).
    """
    edits = [i for i, c in enumerate(calls) if _is_edit(c, root)]
    if not edits:
        return False
    timings = [
        i
        for i, c in enumerate(calls)
        if c.name == "Bash"
        and _TIMING.search(str(c.input.get("command", "")))
        and re.search(r"\d", c.output)
    ]
    return any(i < edits[0] for i in timings) and any(
        i > edits[-1] for i in timings
    )


def _kept_verdict(state: State, turns: list[transcript.Turn]) -> bool:
    return any(v.outcome == "KEEP" for v in state.run_verdicts) or any(
        v.outcome == "KEEP" for v in transcript.seen_verdicts(turns)
    )


def _contamination(
    turns: list[transcript.Turn], calls: list[transcript.ToolCall]
) -> list[str]:
    found = [
        "the init event lists a perfproof skill"
        for turn in turns
        for event in turn.events
        if event.get("type") == "system"
        and event.get("subtype") == "init"
        and "perfproof" in json.dumps(event.get("skills", []))
    ]
    found += [
        "a tool call uses perfproof: " + json.dumps(c.input, default=str)[:100]
        for c in calls
        if (
            c.name == "Bash"
            and _USES_PERFPROOF.search(str(c.input.get("command", "")))
        )
        or (
            c.name == "Skill"
            and "perfproof" in str(c.input.get("skill", "")).lower()
        )
        or (
            c.name in {"Read", "Glob", "Grep", "Bash"}
            and _PERFPROOF_PATH.search(json.dumps(c.input, default=str))
        )
    ]
    return found


def _found(
    task: TaskSpec, final: str, state: State
) -> tuple[bool | None, str]:
    hotspot = task.expect.hotspot
    if hotspot is None:
        return None, "no known hotspot"
    if scoring.named_in(hotspot, [final]):
        return True, f"{hotspot.function} is named in the final answer"
    touched = {p for t in state.trees for p in t.changed}
    touched.update(state.base_changed)
    files = [e.file for e in hotspot.accepted() if e.file in touched]
    if files:
        return True, f"{files[0]} is edited in the final tree"
    return False, "neither named in the final answer nor edited"


def _correct(state: State) -> tuple[bool | None, str]:
    tree = next((t for t in state.trees if t.ref == state.final_ref), None)
    if tree is None:
        return None, f"no state for the final tree {state.final_ref}"
    changed = (
        bool(state.base_changed)
        if tree.ref == "working"
        else bool(tree.changed)
    )
    if not changed:
        return None, "nothing changed"
    if tree.tests_ok is None:
        return None, f"the tests did not run on {tree.ref}"
    return tree.tests_ok, f"{tree.ref}: " + (
        "tests pass" if tree.tests_ok else tree.tests_tail
    )


def load_speed(task_dir: Path) -> speedcheck.SpeedResult | None:
    """Read the harness's speed measurement saved for a task.

    Parameters
    ----------
    task_dir : Path
        ``<run>/<task id>/``.

    Returns
    -------
    SpeedResult or None
        ``None`` if the task had no ``speed_check``.
    """
    path = task_dir / speedcheck.SPEED_FILE
    if not path.is_file():
        return None
    return speedcheck.SpeedResult.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def score_arm(task: TaskSpec, task_dir: Path) -> ArmScore:
    """Score one saved task directory of either arm.

    Parameters
    ----------
    task : Task
        The task with its expectations.
    task_dir : Path
        ``<run>/<task id>/`` as written by the runner.

    Returns
    -------
    ArmScore
        The common checks; see the module rules.
    """
    turns = transcript.load_turns(task_dir)
    state = State.model_validate_json(
        (task_dir / "state.json").read_text(encoding="utf-8")
    )
    meta = Meta.model_validate_json(
        (task_dir / "meta.json").read_text(encoding="utf-8")
    )
    calls = transcript.tool_calls(turns)
    final = transcript.final_text(turns)
    cost, _, _ = transcript.result_metrics(turns)
    if meta.arm == "skill":
        backed = _kept_verdict(state, turns)
    else:
        root = transcript.infer_project_root(turns, task.id)
        backed = timed_both_versions(calls, root)
    claims = [Claim(text=s, backed=backed) for s in extract_claims(final)]
    found, found_detail = _found(task, final, state)
    correct, correct_detail = _correct(state)
    return ArmScore(
        task_id=task.id,
        arm=meta.arm,
        level=meta.level,
        found=found,
        found_detail=found_detail,
        correct=correct,
        correct_detail=correct_detail,
        claims=claims,
        claims_backed=sum(c.backed for c in claims),
        claims_unbacked=sum(not c.backed for c in claims),
        speed=load_speed(task_dir),
        contaminated=_contamination(turns, calls)
        if meta.arm == "baseline"
        else [],
        cost_usd=round(cost, 4),
        wall_seconds=round(sum(t.seconds for t in meta.turns), 1),
        calls=len(turns),
    )


def score_arm_run(run_dir: Path, tasks: TaskSet) -> list[ArmScore]:
    """Score every task directory of a run on common terms.

    Parameters
    ----------
    run_dir : Path
        A run directory.
    tasks : TaskSet
        The set the run was made from.

    Returns
    -------
    list of ArmScore
        In task-set order, for the tasks that have a ``meta.json``.
    """
    return [
        score_arm(task, run_dir / task.id)
        for task in tasks.tasks
        if (run_dir / task.id / "meta.json").exists()
    ]


RUN_FILE = "run.json"
"""Records the arm and level of a run directory."""


def write_run_marker(run_dir: Path, arm: Arm, level: Level) -> None:
    """Record which arm and level a run directory holds.

    Parameters
    ----------
    run_dir : Path
        The run directory.
    arm : {"skill", "baseline"}
        The arm.
    level : {"vague", "detailed"}
        The prompt level.

    Raises
    ------
    ValueError
        If the directory already holds a different arm or level: runs
        of different arms never share a directory (``--resume`` too).
    """
    path = run_dir / RUN_FILE
    if path.is_file():
        have = read_run_marker(run_dir)
        if have != (arm, level):
            msg = (
                f"{run_dir} holds the {have[0]}/{have[1]} run; refusing to "
                f"mix in {arm}/{level}"
            )
            raise ValueError(msg)
        return
    path.write_text(
        json.dumps({"arm": arm, "level": level}) + "\n", encoding="utf-8"
    )


def read_run_marker(run_dir: Path) -> tuple[Arm, Level]:
    """Read the arm and level of a run directory.

    Parameters
    ----------
    run_dir : Path
        The run directory.

    Returns
    -------
    tuple of (Arm, Level)
        From ``run.json``; ``("skill", "vague")`` for a run made before
        arms existed (no marker).

    Raises
    ------
    ValueError
        If the marker is not a valid arm and level.
    """
    path = run_dir / RUN_FILE
    if not path.is_file():
        return "skill", "vague"
    data = json.loads(path.read_text(encoding="utf-8"))
    arm, level = data.get("arm"), data.get("level")
    if arm not in ARMS or level not in LEVELS:
        msg = f"{path} is not a valid run marker"
        raise ValueError(msg)
    return arm, level


def write_arm_scores(run_dir: Path, scores: list[ArmScore]) -> Path:
    """Save the common scores next to the run's ``scores.json``.

    Parameters
    ----------
    run_dir : Path
        A run directory.
    scores : list of ArmScore
        From `score_arm_run`.

    Returns
    -------
    Path
        The file written.
    """
    path = run_dir / ARM_SCORES_FILE
    payload: dict[str, Any] = {
        "tasks": [s.model_dump(mode="json") for s in scores]
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
