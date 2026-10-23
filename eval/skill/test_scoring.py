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
import shutil
from pathlib import Path
from typing import Any

import pytest

import runner
import scoring
import taskset
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
    "The estimate is 62x. Whether or not, it is faster.",
    # From the PR #49 review: holes a looser rule would open.
    "The estimate is 62x. Whether or not it matters the change is faster.",
    "The estimate is 62x, so I checked whether the change got faster and"
    " it did.",
    "The estimate is 62x, matching the 50x claim, and there is no claim it"
    " is wrong.",
    "The estimate is 72x, well above the claimed 50x; no such claim was"
    " ever checked by me.",
]
BENIGN = [
    # From run skill-3i: a first-person "suggest" and a negated "gain".
    "**What I suggest**\n1. **Workload and goal:** the time the calls take."
    " The bar is that the low end of the range must be above 3%.",
    "The estimate is about 480 (range about 440 to 523), not yet a"
    " verdict. I'm not treating that number as a gain.",
    # From run skill-4b: a metric direction, a stated absence of a
    # claim, and a hypothetical.
    "**Goal:** the time the `held_orders` call takes, in seconds (lower"
    " is better). The bar is that the low end of the range must be"
    " above 3%.",
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


def _optional(task: str) -> taskset.Task:
    base = BY_ID[task]
    return base.model_copy(
        update={
            "expect": base.expect.model_copy(update={"fix": "optional_branch"})
        }
    )


def test_optional_branch_accepts_no_change_or_a_tested_branch() -> None:
    task = _optional("find-rolling-light")
    real = _state("find-rolling-light")
    assert scoring.fix_check(task, real)[0] is True
    kept = [t for t in real.trees if t.ref.startswith("perfproof/")]
    assert kept, "the real run kept a branch"
    bare = real.model_copy(
        update={"trees": [t for t in real.trees if t not in kept]}
    )
    assert scoring.fix_check(task, bare) == (True, "no change kept")


def test_optional_branch_fails_on_a_branch_that_breaks_the_tests() -> None:
    task = _optional("find-rolling-light")
    real = _state("find-rolling-light")
    trees = [
        t.model_copy(update={"tests_ok": False})
        if t.ref.startswith("perfproof/")
        else t
        for t in real.trees
    ]
    fix_ok, why = scoring.fix_check(
        task, real.model_copy(update={"trees": trees})
    )
    assert fix_ok is False
    assert "fails the tests" in why


def _bash(command: str, output: str = "") -> transcript.ToolCall:
    return transcript.ToolCall("Bash", {"command": command}, output)


def test_leak_check_flags_network_lookups_and_the_reference() -> None:
    reference = "e391f131451ca8962106f2a146abd024b91e21e2"
    clean = [_bash("perfproof profile x --json", "total 12")]
    assert scoring.leak_violations(clean, reference, "6107") == []
    fetch = [_bash("git fetch origin")]
    assert scoring.leak_violations(fetch, reference, "6107")[0].rule == "leak"
    seen = [_bash("git log", "commit e391f131451ca8962106")]
    assert scoring.leak_violations(seen, reference, "6107")
    pr = [_bash("curl https://github.com/pydata/xarray/pull/6107")]
    assert scoring.leak_violations(pr, reference, "6107")


def test_leak_check_lets_the_skills_own_install_fallback_pass() -> None:
    command = (
        "perfproof doctor --json || uvx --from "
        "git+https://github.com/siddiquifaras/perfproof perfproof doctor"
    )
    assert (
        scoring.leak_violations([_bash(command)], "e391f131451c", "6107") == []
    )


def test_leak_check_sees_downloads_and_clones() -> None:
    reference = "e391f131451ca8962106f2a146abd024b91e21e2"
    for command in (
        "curl https://api.github.com/repos/pydata/xarray/pulls/6107",
        "git clone https://example.org/x x",
        "pip download xarray==2022.3.0",
    ):
        assert scoring.leak_violations([_bash(command)], reference, "6107")


def _with_hotspot(function: str, top_n: int) -> taskset.Task:
    hot = taskset.Hotspot(file="rolling.py", function=function, top_n=top_n)
    base = BY_ID["find-rolling-light"]
    return base.model_copy(
        update={"expect": base.expect.model_copy(update={"hotspot": hot})}
    )


def test_hotspot_top_n_widens_the_window_and_still_needs_naming() -> None:
    turns = _turns("find-rolling-light")
    ranked, _ = transcript.profile_ranking(turns)
    assert ranked[0] == "rolling_sum"
    assert scoring.hotspot_check(_with_hotspot("rolling_sum", 5), turns)[0]
    absent = _with_hotspot("is_dask_collection", 5)
    found, why = scoring.hotspot_check(absent, turns)
    assert found is False
    assert "top 5" in why


def test_a_file_name_alone_is_not_a_hotspot() -> None:
    task = _with_hotspot("no_such_fn", 5)
    found, _ = scoring.hotspot_check(task, _turns("find-rolling-light"))
    assert found is False


def test_a_kept_branch_that_edits_a_test_file_fails() -> None:
    base = _optional("find-rolling-light")
    task = base.model_copy(
        update={"repo": taskset.RealRepo(manifest="x"), "example": None}
    )
    real = _state("find-rolling-light")
    trees = [
        t.model_copy(update={"changed": [*t.changed, "tests/test_a.py"]})
        if t.ref.startswith("perfproof/")
        else t
        for t in real.trees
    ]
    ok, why = scoring.fix_check(task, real.model_copy(update={"trees": trees}))
    assert ok is False
    assert "locked" in why


def test_leak_check_sees_the_title_and_a_later_release() -> None:
    reference = "e391f131451ca8962106f2a146abd024b91e21e2"
    title = "is_dask_collection: micro optimization"
    grep = transcript.ToolCall("Grep", {"pattern": title}, "")
    assert scoring.leak_violations([grep], reference, "6107", title, "xarray")
    install = [_bash("uv pip install -U xarray")]
    assert scoring.leak_violations(install, reference, "6107", "", "xarray")
    fine = [_bash("uv pip install pytest")]
    assert not scoring.leak_violations(fine, reference, "6107", "", "xarray")


def test_leak_check_reads_every_tool_not_only_bash() -> None:
    reference = "e391f131451ca8962106f2a146abd024b91e21e2"
    read = transcript.ToolCall(
        "Read", {"file_path": "/x"}, "merged as e391f131451ca89"
    )
    assert scoring.leak_violations([read], reference, "6107")


def test_reaching_into_the_eval_task_files_is_flagged() -> None:
    manifest = transcript.ToolCall(
        "Read", {"file_path": "/c/perfproof/eval/tasks/hidden-fix/x.json"}, ""
    )
    grep = transcript.ToolCall(
        "Grep", {"pattern": "x", "path": "eval/mine"}, ""
    )
    listing = _bash("ls", "checks.json SPLITS.md")
    found = scoring.harness_violations([manifest, grep, listing])
    assert [v.rule for v in found] == ["harness_access"] * 3
    assert scoring.harness_violations([_bash("ls", "src tests")]) == []


def _real(fix: str = "optional_branch") -> taskset.Task:
    base = _optional("find-rolling-light")
    return base.model_copy(
        update={"repo": taskset.RealRepo(manifest="x"), "example": None}
    )


def test_test_files_and_their_config_are_locked_in_real_repos() -> None:
    task = _real()
    touched = [
        "xarray/tests/test_x.py",
        "conftest.py",
        "pytest.ini",
        "pyproject.toml",
        "src/FooTest.java",
        "web/a.spec.ts",
        "pkg/a_test.go",
        "Tests/Foo.swift",
        "testing/util.py",
        "web/__tests__/a.js",
        "spec/models/a_spec.rb",
    ]
    assert scoring.locked_edits(task, [*touched, "src/lib.py"]) == sorted(
        touched
    )


def _verdict(outcome: str, run_id: str) -> RunVerdict:
    return RunVerdict(
        run_id=run_id,
        outcome=outcome,
        reason="insufficient_evidence",
        gate=None,
        calibration="uncalibrated",
    )


@pytest.mark.parametrize(
    "order", [("KEEP", "INCONCLUSIVE"), ("INCONCLUSIVE", "KEEP")]
)
def test_a_keep_anywhere_in_the_run_is_a_wrong_keep(
    order: tuple[str, str], tmp_path: Path
) -> None:
    # prove-membership-wrong-output accepts only REJECT; its real
    # transcripts and state are used, with the verdicts swapped in.
    task = BY_ID["prove-membership-wrong-output"]
    state = runner.State.model_validate_json(
        (FIRST / task.id / "state.json").read_text()
    ).model_copy(
        update={
            "run_verdicts": [_verdict(o, f"r{i}") for i, o in enumerate(order)]
        }
    )
    turns = transcript.load_turns(FIRST / task.id)
    assert scoring.any_wrong_keep(task, state, turns) is True
    kept = tmp_path / task.id
    shutil.copytree(FIRST / task.id, kept)
    (kept / "state.json").write_text(state.model_dump_json(), encoding="utf-8")
    score = scoring.score_task(task, kept)
    assert score.wrong_keep is True
    assert score.verdict_ok is False
    assert score.passed is False
    clean = state.model_copy(
        update={"run_verdicts": [_verdict("INCONCLUSIVE", "a")] * 2}
    )
    assert scoring.any_wrong_keep(task, clean, turns) is False


def test_a_task_that_accepts_keep_never_has_a_wrong_keep() -> None:
    task = BY_ID["prove-rolling-fix"]
    assert "KEEP" in task.expect.outcomes
    state = _state("prove-rolling-fix").model_copy(
        update={"run_verdicts": [_verdict("KEEP", "a")]}
    )
    turns = transcript.load_turns(FIRST / task.id)
    assert scoring.any_wrong_keep(task, state, turns) is False


# The output below is what an agent's script printed from a real
# ``perfproof profile`` (runs/sandbox-e2e-5, find-rolling-light); the
# events around it have the schema of the saved transcripts.
_PROFILE_OK = (
    "True None\nrolling_sum rolling.py 51 0.7808993603283747\n"
    "compile <built-in> None 0.13734775965125162\n"
    "make_series bench.py 44 0.008166299641963658\n"
)
_PROFILE_WRONG = (
    "True None\nmake_series bench.py 44 0.9\nrolling_sum rolling.py 51 0.05\n"
)
_PROFILE_FAILED = (
    "(eval):1: operation not permitted: /tmp/prof.json\nTraceback (most "
    "recent call last):\nPermissionError: [Errno 1] Operation not "
    "permitted: '/tmp/prof.json'\n"
)


def _use(n: int, name: str, **args: str) -> transcript.Event:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": f"t{n}",
                    "name": name,
                    "input": args,
                }
            ]
        },
    }


def _out(n: int, text: str) -> transcript.Event:
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": f"t{n}",
                    "content": text,
                    "is_error": False,
                }
            ]
        },
    }


def _say(text: str) -> transcript.Event:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    }


_NAMED = "`rolling_sum` is the cost: about 78% of the profiled run."


def _grade(events: list[transcript.Event]) -> tuple[bool | None, str]:
    base = BY_ID["find-rolling-light"]
    return scoring.hotspot_check(base, [transcript.Turn(events, None)])


def test_no_successful_profile_fails_the_hotspot_check() -> None:
    ok, why = _grade(
        [
            _use(
                1, "Bash", command="perfproof profile rolling --json > /tmp/p"
            ),
            _out(1, _PROFILE_FAILED),
            _say(_NAMED),
        ]
    )
    assert ok is False
    assert "no profile output" in why


def test_a_profile_that_only_succeeds_after_the_first_edit_fails() -> None:
    ok, why = _grade(
        [
            _use(1, "Edit", file_path="/w/rolling.py"),
            _out(1, ""),
            _use(2, "Bash", command="perfproof profile rolling --json"),
            _out(2, _PROFILE_OK),
            _say(_NAMED),
        ]
    )
    assert ok is False
    assert "after the first edit" in why


def test_a_failed_profile_then_a_successful_one_before_the_edit_passes() -> (
    None
):
    ok, why = _grade(
        [
            _use(
                1, "Bash", command="perfproof profile rolling --json > /tmp/p"
            ),
            _out(1, _PROFILE_FAILED),
            # Editing the scratch spec is not an edit of the code.
            _use(2, "Bash", command="sed -i 's/a/b/' .perfproof/specs/r.json"),
            _out(2, ""),
            _use(3, "Write", file_path="/w/.perfproof/specs/r2.json"),
            _out(3, ""),
            _use(4, "Bash", command="perfproof profile rolling --json | py"),
            _out(4, _PROFILE_OK),
            _say(_NAMED),
            _use(5, "Write", file_path="/w/rolling.py"),
        ]
    )
    assert ok is True, why


def test_a_successful_profile_with_the_wrong_hotspot_fails() -> None:
    ok, why = _grade(
        [
            _use(1, "Bash", command="perfproof profile rolling --json"),
            _out(1, _PROFILE_WRONG),
            _say(_NAMED),
        ]
    )
    assert ok is False
    assert "make_series" in why


def test_what_counts_as_an_edit_of_the_code() -> None:
    root = "/r/repos/t"

    def bash(command: str) -> bool:
        return transcript.is_code_edit("Bash", {"command": command}, root)

    edits = (
        "echo x > rolling.py",
        "cat >> rolling.py <<'EOF'\nx\nEOF",
        "printf x | tee rolling.py",
        "cp new.py rolling.py",
        "mv new.py src/a.py",
        "perl -pi -e 's/a/b/' rolling.py",
        "python3 - <<'EOF'\nopen('rolling.py','w').write('x')\nEOF",
        "python -c \"from pathlib import Path; Path('r.py').write_text('x')\"",
        "git stash pop",
        "git stash apply",
        "git cherry-pick abc",
        "git merge main",
        "git checkout abc123 -- rolling.py",
        "git commit -qm x",
        "cd /r/repos/t && git apply x.patch",
        "sed -i 's/a/b/' rolling.py",
        "sed -i '' 's/a/b/' rolling.py",
        # judged by the file argument, not by a mention of .perfproof
        "sed -i 's/a/b/' rolling.py && echo .perfproof/x",
        "sed -i 's/a/b/' rolling.py # note about .perfproof",
    )
    for command in edits:
        assert bash(command), command
    not_edits = (
        "git checkout -q -b perfproof/x",
        "git switch -c y",
        "git checkout main",
        "echo x > .perfproof/a.json",
        "cat > .perfproof/spec.json <<'EOF'\n{\"a\": 1}\nEOF",
        "sed -i 's/a/b/' .perfproof/specs/r.json",
        "perfproof profile r --json > .perfproof/p.json",
        "cp a.json .perfproof/b.json",
        "echo x > /dev/null",
        "ls > /tmp/x",
        "echo 'a > b'",
        "echo hi; cat rolling.py",
        "python3 -c \"open('.perfproof/x','w')\"",
        "python3 - <<'EOF'\nopen('.perfproof/x','w').write('if 1 > 0')\nEOF",
        "cat > .perfproof/s.py <<'EOF'\nif a > b:\n    pass\nEOF",
    )
    for command in not_edits:
        assert not bash(command), command


_OWN_FILE_SCRIPTS = (
    # r7-2: the agent rewrites its own harness; the text it injects
    # holds an ``open('out/result.txt', 'w')`` that is data, not a call.
    "python - <<'EOF'\np=\".perfproof/harness/work.py\"\ns=open(p).read()\n"
    "s=s.replace('a',\"open('out/result.txt','w').write('x')\")\n"
    'open(p,"w").write(s)\nEOF',
    # r7-3: a spec edited through a variable path, with test paths in
    # the data it writes.
    "python3 - <<'EOF'\nimport json\np='.perfproof/specs/r.json'\n"
    "s=json.load(open(p))\ns['c']=['pytest','xarray/tests/test_a.py']\n"
    "json.dump(s,open(p,'w'))\nEOF",
    "python3 - <<'EOF'\nfrom pathlib import Path\n"
    "q=Path('.perfproof/harness/h.py')\nq.write_text('x = 1')\nEOF",
)
_CODE_SCRIPTS = (
    "python3 - <<'EOF'\np='xarray/core/rolling.py'\ns=open(p).read()\n"
    "open(p,'w').write(s)\nEOF",
    "python3 - <<'EOF'\nfrom pathlib import Path\n"
    "Path('src/a.py').write_text('x')\nEOF",
    # one scratch write does not hide a code write beside it
    "python3 - <<'EOF'\nopen('.perfproof/a','w')\nopen('src/a.py','w')\nEOF",
    # a variable that may be either
    "python3 - <<'EOF'\np='.perfproof/a'\np='src/a.py'\nopen(p,'w')\nEOF",
    # computed paths and text run as code cannot be shown to be scratch
    "python3 - <<'EOF'\nimport sys\nopen(sys.argv[1] + '.py','w')\nEOF",
    "python3 - <<'EOF'\nexec(\"open('src/a.py','w')\")\nEOF",
    "python3 - <<'EOF'\nimport subprocess\n"
    "subprocess.run(['python','-c',\"open('src/a.py','w')\"])\nEOF",
    # a name rebound some other way than a literal assignment
    "python3 - <<'EOF'\np='.perfproof/a'\nfor p in ['src/a.py']:\n"
    "    open(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\np: str = 'src/a.py'\n"
    "open(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\np,q='src/a.py',1\nopen(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\nif 1:\n    p='src/a.py'\n"
    "q=p\nopen(q,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\ndef f(p):\n    open(p,'w')\n"
    "f('src/a.py')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\n"
    "[open(p,'w') for p in ['src/a.py']]\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\n"
    "(lambda p: open(p,'w'))('src/a.py')\nEOF",
    # other callables named open take the path first
    "python3 - <<'EOF'\nimport io\nio.open('src/b.py','w')\nEOF",
    "python3 - <<'EOF'\nimport codecs\ncodecs.open('src/b.py','w')\nEOF",
    "python3 - <<'EOF'\nimport builtins\nbuiltins.open('src/b.py','w')\nEOF",
    # Python must read its program from the one here-document
    "python3 -c \"open('src/a.py','w')\" <<'EOF'\nx = 1\nEOF",
    "python3 - <<'A' <<'B'\nx = 1\nA\nopen('src/a.py','w')\nB",
    # names reached through match patterns or by string
    "python3 - <<'EOF'\np='.perfproof/a'\nmatch 'src/a.py':\n"
    "    case p:\n        open(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\nvars()['p']='src/a.py'\n"
    "open(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\nglobals()['p']='src/a.py'\n"
    "open(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\nsetattr(__import__('sys'),'x',1)\n"
    "open('src/a.py','w')\nEOF",
    # names rebound through the module, its dict or the frame
    "python3 - <<'EOF'\np='.perfproof/a'\nimport sys\n"
    "sys.modules['__main__'].p='src/a.py'\nopen(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\nimport __main__\n"
    "__main__.p='src/a.py'\nopen(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\nimport sys\n"
    "sys.modules[__name__].__dict__['p']='src/a.py'\nopen(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\nimport sys\n"
    "sys._getframe().f_globals['p']='src/a.py'\nopen(p,'w')\nEOF",
    # routes to the module's names that hide behind an alias or a
    # string: only the listed imports and calls are trusted
    "python3 - <<'EOF'\np='.perfproof/a'\nimport importlib\n"
    "importlib.import_module('__main__').p='src/a.py'\nopen(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\ng=globals\n"
    "g()['p']='src/a.py'\nopen(p,'w')\nEOF",
    "python3 - <<'EOF'\np='.perfproof/a'\nfrom builtins import exec as e\n"
    "e('p=\"src/a.py\"')\nopen(p,'w')\nEOF",
    # a listed name rebound, or a writer passed rather than called
    "python3 - <<'EOF'\nfrom pathlib import Path\n"
    "q = Path('.perfproof/x')\nq.write_text('y')\n"
    "print = Path('src/a.py').write_text\nprint('z')\nEOF",
    "python3 - <<'EOF'\nfrom pathlib import Path\n"
    "q = Path('.perfproof/x')\nq.write_text('y')\n"
    "open = Path('src/a.py').open\nopen('w').write('z')\nEOF",
    "python3 - <<'EOF'\nfrom pathlib import Path\n"
    "q = Path('.perfproof/x')\nq.write_text('y')\n"
    "sorted(['z'], key=Path('src/a.py').write_text)\nEOF",
    # a script that does not parse falls back to the pattern match
    "python3 - <<'EOF'\nopen('src/a.py','w') ??\nEOF",
)


def test_a_python_script_that_writes_only_perfproof_files_is_no_edit() -> None:
    root = "/r/repos/t"
    for command in _OWN_FILE_SCRIPTS:
        assert not transcript.is_code_edit("Bash", {"command": command}, root)


def test_a_python_script_that_writes_code_is_still_an_edit() -> None:
    root = "/r/repos/t"
    for command in _CODE_SCRIPTS:
        assert transcript.is_code_edit("Bash", {"command": command}, root), (
            command
        )


def test_a_script_too_deep_to_parse_falls_back_to_the_pattern_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deep(*_: Any, **__: Any) -> None:
        raise RecursionError

    monkeypatch.setattr(transcript.ast, "parse", deep)
    command = "python3 - <<'EOF'\nopen('src/a.py','w')\nEOF"
    assert transcript.is_code_edit("Bash", {"command": command}, "/r/repos/t")

    def full(*_: Any, **__: Any) -> None:
        raise MemoryError

    monkeypatch.setattr(transcript.ast, "parse", full)
    assert transcript.is_code_edit("Bash", {"command": command}, "/r/repos/t")


def test_a_profile_after_a_python_edit_of_code_still_fails() -> None:
    ok, why = _grade(
        [
            _use(1, "Bash", command=_CODE_SCRIPTS[0]),
            _out(1, ""),
            _use(2, "Bash", command="perfproof profile rolling --json"),
            _out(2, _PROFILE_OK),
            _say(_NAMED),
        ]
    )
    assert ok is False
    assert "after the first edit" in why


def test_a_profile_after_editing_its_own_files_in_python_passes() -> None:
    ok, why = _grade(
        [
            *[
                event
                for n, command in enumerate(_OWN_FILE_SCRIPTS, 1)
                for event in (_use(n, "Bash", command=command), _out(n, ""))
            ],
            _use(9, "Bash", command="perfproof profile rolling --json"),
            _out(9, _PROFILE_OK),
            _say(_NAMED),
        ]
    )
    assert ok is True, why


def test_edit_tools_are_judged_by_the_project_path() -> None:
    root = "/r/repos/t"
    edit = transcript.is_code_edit
    assert edit("Write", {"file_path": "/r/repos/t/rolling.py"}, root)
    assert edit("Edit", {"file_path": "/r/repos/t/src/.claude/x.py"}, root)
    assert edit("Edit", {"file_path": "rolling.py"}, root)
    # A new file under the project's .perfproof or .claude is scratch.
    assert not edit(
        "Write", {"file_path": "/r/repos/t/.perfproof/s.json"}, root
    )
    assert not edit("Write", {"file_path": ".perfproof/s.json"}, root)
    assert not edit("Edit", {"file_path": "/r/repos/t/.claude/x"}, root)
    # Outside the project is not the project.
    assert not edit("Write", {"file_path": "/elsewhere/notes.txt"}, root)
    assert not edit("Read", {"file_path": "/r/repos/t/rolling.py"}, root)
    # Without a known root, a .perfproof component anywhere is scratch.
    assert not edit("Write", {"file_path": "/w/.perfproof/s.json"})
    assert edit("Write", {"file_path": "/w/rolling.py"})


def test_a_profile_named_only_in_text_is_not_a_profile_call() -> None:
    ok, why = _grade(
        [
            _use(
                1,
                "Bash",
                command="echo 'perfproof profile'; "
                "echo 'rolling_sum rolling.py 51 0.9'",
            ),
            _out(1, "perfproof profile\nrolling_sum rolling.py 51 0.9\n"),
            _say(_NAMED),
        ]
    )
    assert ok is False
    assert "no profile output" in why
    heredoc = "cat <<'EOF'\nperfproof profile x\nEOF"
    assert transcript.profile_targets(heredoc) == []


def test_a_profile_of_another_target_does_not_count() -> None:
    define = _use(1, "Bash", command="perfproof bench define rolling --spec s")
    other = [
        define,
        _out(1, "ok"),
        _use(2, "Bash", command="perfproof profile other --json"),
        _out(2, _PROFILE_OK),
        _say(_NAMED),
    ]
    ok, why = _grade(other)
    assert ok is False
    assert "no profile output" in why
    right = [
        define,
        _out(1, "ok"),
        _use(2, "Bash", command="perfproof profile rolling --json"),
        _out(2, _PROFILE_OK),
        _say(_NAMED),
    ]
    assert _grade(right)[0] is True


def test_profile_commands_are_found_by_their_simple_command() -> None:
    found = transcript.profile_targets
    assert found("cd /w && perfproof profile rolling --json | head") == [
        "rolling"
    ]
    assert found("H=ab perfproof profile bench --spec-hash $H") == ["bench"]
    assert found("cd /w; (perfproof profile bench)") == ["bench"]
    assert found("uvx --from git+https://x perfproof profile b") == ["b"]
    assert found("echo perfproof profile x") == []
    assert found("grep 'perfproof profile' README") == []


def _bash_edit(command: str, root: str = "/r/repos/t") -> bool:
    return transcript.is_code_edit("Bash", {"command": command}, root)


def _graded_after(define: str, profile: str) -> bool:
    ok, _ = _grade(
        [
            _use(1, "Bash", command=define),
            _out(1, "ok"),
            _use(2, "Bash", command=profile),
            _out(2, _PROFILE_OK),
            _say(_NAMED),
        ]
    )
    return ok is True


def test_git_commands_that_only_read_are_not_edits() -> None:
    for command in (
        "git merge-base origin/main HEAD",
        "git merge-tree a b c",
        "git commit --dry-run",
        "git commit -m x --dry-run",
        "git apply --check x.patch",
        "git apply --stat x.patch",
        "git restore --staged rolling.py",
    ):
        assert not _bash_edit(command), command
    assert _bash_edit("git merge main")
    assert _bash_edit("git apply --apply --check x.patch")
    assert _bash_edit("git restore --staged --worktree rolling.py")


def test_profile_and_define_skip_option_values() -> None:
    profile = transcript.profile_targets
    for line in (
        "perfproof profile --top 5 rolling",
        "perfproof profile --profiler pyinstrument rolling",
        "perfproof profile --repo . rolling",
        "perfproof profile --spec-hash ab --timeout 9 rolling --json",
        "perfproof profile --own-module x --hypothesis h1 rolling",
        "perfproof profile --profiler-command '[\"x\"]' rolling",
        "perfproof profile --top=5 rolling",
        "perfproof profile rolling --json > prof.json",
    ):
        assert profile(line) == ["rolling"], line
    for define in (
        "perfproof bench define --spec s.json rolling",
        "perfproof bench define --repo . --spec - rolling",
    ):
        assert _graded_after(define, "perfproof profile rolling --json")


def test_defined_benchmarks_strip_uv_prefixes_like_profile() -> None:
    for define in (
        "uv run perfproof bench define rolling --spec s",
        "uvx --from git+https://x perfproof bench define rolling --spec s",
    ):
        assert _graded_after(define, "perfproof profile rolling --json")
        assert not _graded_after(define, "perfproof profile other --json")


def test_redirects_are_judged_by_who_writes_and_where() -> None:
    assert not _bash_edit("perfproof profile rolling --json > prof.json")
    assert not _bash_edit("uv run perfproof compare a b --json >out.json")
    assert not _bash_edit("perfproof profile r --json 2>&1 > prof.json")
    assert _bash_edit("echo x > prof.json")
    assert _bash_edit("python3 gen.py > rolling.py")
    assert _bash_edit("echo x>rolling.py")
    assert _bash_edit("echo x >>rolling.py")
    assert not _bash_edit("echo x > ../notes.txt")
    assert not _bash_edit("echo x > a/../../notes.txt")
    assert not _bash_edit("echo x > /r/repos/t/../other/f.py")
    assert _bash_edit("echo x > a/../rolling.py")
    assert _bash_edit("echo x > .perfproof/../rolling.py")
    assert not _bash_edit("echo 'a>b'")
    assert not _bash_edit("echo hi 2>&1")
    assert (
        transcript.is_code_edit(
            "Write", {"file_path": "../x.py"}, "/r/repos/t"
        )
        is False
    )


def test_cheap_missed_edits_are_seen() -> None:
    for command in (
        "sed -ri 's/a/b/' rolling.py",
        "sed -Ei 's/a/b/' rolling.py",
        "sed -ni 's/a/b/p' rolling.py",
        "sed -i.bak 's/a/b/' rolling.py",
        "sed --in-place 's/a/b/' rolling.py",
        "sed --in-place=.bak 's/a/b/' rolling.py",
        "perl -pi.bak -e 's/a/b/' rolling.py",
        "git restore rolling.py",
        "git restore --source=HEAD~1 rolling.py",
        "git reset --hard HEAD~1",
        "git reset -q --hard origin/main",
        "git rebase main",
        "git am x.mbox",
        "git pull",
        "git checkout rolling.py",
        "git checkout HEAD src/a.py",
        "git checkout .",
    ):
        assert _bash_edit(command), command
    for command in (
        "git reset HEAD rolling.py",
        "git reset --soft HEAD~1",
        "git checkout -b fix.1",
        "git checkout main",
        "git checkout v1.2",
        "perl -MList::Util=sum -e 'print 1' rolling.py",
        "sed -n 's/a/b/p' rolling.py",
        "sed -i 's/a/b/' .perfproof/specs/r.json",
    ):
        assert not _bash_edit(command), command
