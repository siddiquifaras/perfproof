"""The scorer, on transcripts and repo states captured from real runs.

``runs/first`` is the first scored run of the committed skill;
``runs/broken`` is the same two prove tasks with the skill's "no
direction words" rule inverted; ``fixtures/early`` holds two tasks of an
earlier probe run, kept because the skill drifted into writing its own
fix there; ``fixtures/lead`` is a result event of one of the lead's hand
runs; ``fixtures/cheap-timing`` is a run in which the agent timed
something itself. Nothing in them is typed by hand.
"""

import json
from pathlib import Path

import pytest

import runner
import scoring
import transcript
from runner import RunVerdict
from taskset import load

HERE = Path(__file__).resolve().parent
FIRST = HERE / "runs" / "first"
BROKEN = HERE / "runs" / "broken"
TASKS = load()
BY_ID = {t.id: t for t in TASKS.tasks}


def _score(run: Path, task: str) -> scoring.TaskScore:
    return scoring.score_task(BY_ID[task], run / task)


def _texts(run: Path, task: str) -> list[str]:
    return transcript.assistant_texts(transcript.load_turns(run / task))


def test_saved_runs_score_the_same_without_running_anything() -> None:
    for run in (FIRST, BROKEN):
        saved = json.loads((run / "scores.json").read_text())["tasks"]
        again = [s.model_dump() for s in scoring.score_run(run, TASKS)]
        assert again == saved, (
            f"{run.name} scores changed; if the scorer changed on purpose,"
            " re-score and archive the run"
        )


def test_refusal_of_a_wrong_output_branch_is_the_expected_verdict() -> None:
    score = _score(FIRST, "prove-rolling-wrong-output")
    assert score.verdict == "REJECT (correctness, gate correctness)"
    assert score.verdict_source == "record"
    assert score.verdict_ok is True
    assert score.result_line_ok is True
    assert score.fix_ok is True
    assert score.tree_ok is True
    assert score.passed


def test_real_fix_is_graded_as_undecided_not_kept() -> None:
    score = _score(FIRST, "prove-membership-fix")
    assert score.verdict == "INCONCLUSIVE (insufficient_evidence)"
    assert score.verdict_ok is True
    assert score.wrong_keep is False


def test_a_branch_never_compared_has_no_verdict() -> None:
    # The skill read the diff, saw only bench.py changed, and refused
    # without running compare, so the lock gate never judged it.
    score = _score(FIRST, "prove-rolling-bench-edit")
    assert score.verdict is None
    assert score.verdict_source == "none"
    assert score.verdict_ok is False
    assert score.result_line_ok is True
    assert not score.passed


def test_missing_result_line_is_found() -> None:
    score = _score(FIRST, "prove-membership-bench-edit")
    assert score.result_line is None
    assert score.result_line_ok is False
    assert [w.rule for w in score.wording] == ["result_line"]


def test_find_task_names_the_hotspot_and_keeps_a_tested_branch() -> None:
    score = _score(FIRST, "find-rolling-light")
    assert score.hotspot_named is True
    assert score.fix_ok is True
    assert "perfproof/" in score.fix_detail
    assert "tests pass" in score.fix_detail
    assert score.tree_ok is True


def test_trigger_set_matches_expectations() -> None:
    for task in TASKS.tasks:
        if task.kind != "trigger":
            continue
        score = _score(FIRST, task.id)
        assert score.trigger is task.expect.trigger
        assert score.trigger_ok is True
        assert score.verdict_ok is None


def test_slash_prompts_start_the_skill_by_construction() -> None:
    assert _score(FIRST, "prove-rolling-fix").trigger is None


def test_drift_into_a_fix_of_its_own_fails_fix_none_but_not_the_verdict() -> (
    None
):
    # After the REJECT the skill offered a fix and a generic "yes" made
    # it write one on perfproof/rolling-prefix and compare that too.
    task = BY_ID["prove-rolling-wrong-output"]
    score = scoring.score_task(
        task, HERE / "fixtures" / "early" / "prove-rolling-wrong-output"
    )
    assert score.fix_ok is False
    assert "perfproof/rolling-prefix" in score.fix_detail
    assert score.verdict == "REJECT (correctness, gate correctness)"
    assert score.verdict_ok is True


def test_result_line_not_last_is_found() -> None:
    task = BY_ID["prove-rolling-bench-edit"]
    score = scoring.score_task(
        task, HERE / "fixtures" / "early" / "prove-rolling-bench-edit"
    )
    assert score.result_line_ok is False
    assert any(
        w.rule == "result_line" and "not last" in w.text for w in score.wording
    )


def test_clean_run_has_no_wording_violations() -> None:
    for task in ("prove-rolling-fix", "prove-membership-fix"):
        assert _score(FIRST, task).wording == []


def test_inverted_rule_scores_worse_on_wording() -> None:
    for task in ("prove-rolling-fix", "prove-membership-fix"):
        before = _score(FIRST, task)
        after = _score(BROKEN, task)
        assert before.passed
        assert not after.passed
        assert len(after.wording) > len(before.wording)
        assert {w.rule for w in after.wording} <= {
            "direction_word",
            "claim_comparison",
        }
    flagged = " ".join(
        w.text for w in _score(BROKEN, "prove-rolling-fix").wording
    )
    assert "faster" in flagged


def test_lead_run_with_a_wrong_result_line_and_claim_comparison() -> None:
    line = (HERE / "fixtures/lead/prove-fix-2.result.jsonl").read_text()
    event = json.loads(line)
    text = event["result"]
    verdict = RunVerdict(
        run_id="20261019T004412Z-528465",
        outcome="INCONCLUSIVE",
        reason="insufficient_evidence",
        gate=None,
        calibration="uncalibrated",
    )
    found, ok, why = scoring.result_line_check(text, verdict)
    assert found is not None
    assert found.startswith("NO VERDICT")
    assert not ok
    assert "NO VERDICT" in why
    violations = scoring.direction_violations([text], uncalibrated=True)
    assert {v.rule for v in violations} == {"direction_word"}
    assert "larger than claimed" in violations[0].text


def test_result_line_check_cases_from_real_final_messages() -> None:
    ok_verdict = RunVerdict(
        run_id="r",
        outcome="REJECT",
        reason="correctness",
        gate="correctness",
        calibration="uncalibrated",
    )
    text = transcript.final_text(
        transcript.load_turns(FIRST / "prove-rolling-wrong-output")
    )
    assert scoring.result_line_check(text, ok_verdict)[1] is True
    other = ok_verdict.model_copy(update={"gate": None})
    assert scoring.result_line_check(text, other)[1] is False
    assert scoring.result_line_check(text, None)[1] is False
    body = text.rstrip().rsplit("\n", 1)[0]
    assert scoring.result_line_check(body, ok_verdict)[0] is None


def test_agent_timing_reported_to_the_user_is_found() -> None:
    turns = transcript.load_turns(HERE / "fixtures" / "cheap-timing")
    found = scoring.cheap_timing_violations(
        transcript.assistant_texts(turns), transcript.tool_calls(turns)
    )
    assert found
    assert all(v.rule == "cheap_timing" for v in found)
    assert any("16 ms" in v.text for v in found)


def test_milliseconds_are_fine_when_the_agent_timed_nothing() -> None:
    for task in ("find-rolling-light", "find-membership-light"):
        turns = transcript.load_turns(FIRST / task)
        found = scoring.cheap_timing_violations(
            transcript.assistant_texts(turns), transcript.tool_calls(turns)
        )
        assert found == []


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        # Taken from the broken-skill run.
        (
            "That is faster than claimed, and the range is far above the"
            " 3% bar.",
            ["direction_word"],
        ),
        (
            "On this estimate the change is much faster, and it's on"
            " `agent-speedup`.",
            ["direction_word"],
        ),
    ],
)
def test_wording_rules_on_sentences_of_the_broken_run(
    sentence: str, expected: list[str]
) -> None:
    texts = _texts(BROKEN, "prove-rolling-fix") + _texts(
        BROKEN, "prove-membership-fix"
    )
    assert any(sentence in text for text in texts)
    found = scoring.direction_violations([sentence], uncalibrated=True)
    assert [v.rule for v in found] == expected
    assert scoring.direction_violations([sentence], uncalibrated=False) == []


# Inputs from the PR #45 review: constructed sentences the wording rule
# must flag, and benign ones it must not. They are rule inputs, not
# transcript snippets.
MISSED_BEFORE = [
    "The estimate is 72x (range 64x to 81x). It is 72x faster than the"
    " claimed 50x.",
    "The estimate is 62x (range 61x to 64x). That is faster.",
    "The estimate is 62x. The change sped up the benchmark.",
    "The estimate is 62x. It runs in a fraction of the time.",
    "The estimate is 62x, so it is 62 times as fast.",
    "The estimate is 62x. It beats main by 62x.",
    "The estimate is 62x. It uses 62x less time.",
    "The estimate is 62x. The new code is more efficient.",
    "The estimate is 4.9x. Only the screening call was timed.\n"
    "The whole run came out quicker.",
    "The estimate is 62x; the new code is leaner and speedier.",
    "The estimate is 62x. It outpaces main.",
    "The estimate is 62x. Main lags behind the branch.",
    "The estimate is 62x: runtime dropped from 3s to 0.05s.",
    "The estimate is 62x, an order of magnitude ahead of main.",
    "The estimate is 62x, so it is cheaper to run.",
    "The estimate is 62x, which suggests the change worked.",
    "The estimate is 62x. I'd say that is not a speedup but it beats main.",
]
BENIGN = [
    # From run skill-3i: a first-person "suggest" and a negated "gain".
    "**What I suggest**\n1. **Workload and goal:** the time the calls take."
    " The bar is that the low end of the range must be above 3%.",
    "The estimate is about 480 (range about 440 to 523), not yet a"
    " verdict. I'm not treating that number as a gain.",
    "perfproof can't keep changes yet. The estimate is 62x (range 61x to"
    " 64x), which is not a verdict.",
    "The estimate is 62x. I can't say whether it is faster.",
    "The estimate is 62x, which is not a speedup claim.",
    'The commit says "about 50x faster", and that can\'t be checked until'
    " perfproof can give a verdict.",
    "The estimate is 62x. Shall I make it faster, or leave it?",
    "The tests passed on both versions.",
    "Make it faster by caching, then?",
]


@pytest.mark.parametrize("text", MISSED_BEFORE)
def test_paraphrases_and_following_sentences_are_flagged(text: str) -> None:
    found = scoring.direction_violations([text], uncalibrated=True)
    assert found, text


@pytest.mark.parametrize("text", BENIGN)
def test_benign_sentences_are_not_flagged(text: str) -> None:
    assert scoring.direction_violations([text], uncalibrated=True) == []


def test_a_claim_set_against_an_estimate_without_a_direction_word() -> None:
    text = "The estimate is 72x, above the claimed 50x."
    found = scoring.direction_violations([text], uncalibrated=True)
    assert [v.rule for v in found] == ["claim_comparison"]


def test_direction_words_in_a_paragraph_without_an_estimate_pass() -> None:
    text = "I will look for ways to make this faster.\n\nThe estimate is 3x."
    assert scoring.direction_violations([text], uncalibrated=True) == []


def _turns(task: str) -> list[transcript.Turn]:
    return transcript.load_turns(FIRST / task)


def test_hotspot_must_rank_first_in_a_profile_and_be_named() -> None:
    for task in ("find-rolling-light", "find-membership-light"):
        found, why = scoring.hotspot_check(BY_ID[task], _turns(task))
        assert found is True, why
    top, after = transcript.profile_evidence(_turns("find-rolling-light"))
    assert top == "rolling_sum"
    assert after


def test_hotspot_without_a_profile_in_the_transcript_fails() -> None:
    found, why = scoring.hotspot_check(
        BY_ID["find-rolling-light"], _turns("prove-rolling-fix")
    )
    assert found is False
    assert "no profile output" in why


def test_hotspot_that_is_not_the_profiles_top_one_fails() -> None:
    task = BY_ID["find-rolling-light"]
    real = task.expect.hotspot
    assert real is not None
    wrong = real.model_copy(update={"function": "make_series"})
    other = task.model_copy(
        update={"expect": task.expect.model_copy(update={"hotspot": wrong})}
    )
    found, why = scoring.hotspot_check(other, _turns("find-rolling-light"))
    assert found is False
    assert "profile ranks rolling_sum first" in why


def test_hotspot_named_only_to_dismiss_it_fails() -> None:
    # Real find-rolling-light turns, with every assistant text block
    # replaced by a dismissal.
    task = BY_ID["find-rolling-light"]
    swapped: list[transcript.Turn] = []
    for turn in _turns("find-rolling-light"):
        events: list[transcript.Event] = []
        for event in turn.events:
            message = transcript.as_dict(event.get("message"))
            if event.get("type") == "assistant" and message is not None:
                blocks = [
                    {**b, "text": "`rolling_sum` is not the problem."}
                    if b.get("type") == "text"
                    else b
                    for b in transcript.as_list(message.get("content"))
                ]
                event = {**event, "message": {**message, "content": blocks}}
            events.append(event)
        swapped.append(transcript.Turn(events, turn.result))
    found, why = scoring.hotspot_check(task, swapped)
    assert found is False
    assert "never named" in why


def _state(task: str) -> runner.State:
    path = FIRST / task / "state.json"
    return runner.State.model_validate_json(path.read_text())


def test_uncommitted_edit_on_the_kept_branch_fails_tree_and_fix() -> None:
    # Real final state of find-rolling-light, with one modified file in
    # the status, as an agent that left its edit uncommitted would have.
    task = BY_ID["find-rolling-light"]
    state = _state("find-rolling-light").model_copy(
        update={"status": " M rolling.py\n?? .perfproof/\n"}
    )
    ok, detail = scoring.tree_check(state)
    assert not ok
    assert "M rolling.py" in detail
    fix_ok, why = scoring.fix_check(task, state)
    assert fix_ok is False
    assert "uncommitted" in why


def test_edit_to_a_locked_file_in_the_working_tree_fails_the_fix() -> None:
    task = BY_ID["find-rolling-light"]
    real = _state("find-rolling-light")
    trees = [
        t.model_copy(update={"changed": [*t.changed, "bench.py"]})
        if t.ref == "working"
        else t
        for t in real.trees
    ]
    fix_ok, why = scoring.fix_check(
        task, real.model_copy(update={"trees": trees})
    )
    assert fix_ok is False
    assert "locked" in why
    assert scoring.fix_check(task, real)[0] is True


def test_a_refusal_that_names_the_machine_ends_the_session() -> None:
    def ending(line: str) -> dict[str, object]:
        return {"result": f"Some text.\n\n{line}"}

    assert runner.session_over(
        ending(
            "Result: NO VERDICT (original code will not run on this machine)"
        )
    )
    assert runner.session_over(
        ending("Result: NO VERDICT (benchmark would reach another machine)")
    )
    assert not runner.session_over(
        ending("Result: NO VERDICT (the machine is busy: load 9 on 10 cores)")
    )
