"""Transcript reading, on stream-json lines captured from real runs.

Every file under ``runs/`` and ``fixtures/`` was written by
``claude -p --output-format stream-json`` driven by ``runner.py`` (or,
for ``fixtures/lead/``, by the lead's own hand runs); none is typed by
hand.
"""

import json
from pathlib import Path

import pytest

import transcript

HERE = Path(__file__).resolve().parent
FIRST = HERE / "runs" / "first"


def _turns(task: str) -> list[transcript.Turn]:
    return transcript.load_turns(FIRST / task)


def test_skill_tool_call_is_the_first_action_on_a_plain_phrasing() -> None:
    turns = _turns("pos-make-faster")
    calls = transcript.tool_calls(turns)
    assert calls[0].name == "Skill"
    assert calls[0].input == {"skill": "perfproof"}
    assert calls[0].output == "Launching skill: perfproof"
    assert transcript.skill_started(turns)


@pytest.mark.parametrize(
    "task", ["neg-typo", "neg-add-test", "neg-explain", "neg-rename"]
)
def test_skill_does_not_start_on_off_topic_requests(task: str) -> None:
    assert not transcript.skill_started(_turns(task))


def test_compare_verdict_is_read_from_a_filtered_print() -> None:
    seen = transcript.seen_verdicts(_turns("prove-rolling-wrong-output"))
    assert seen == [
        transcript.SeenVerdict(
            outcome="REJECT",
            reason="correctness",
            gate="correctness",
            calibration="uncalibrated",
        )
    ]


def test_gate_of_the_verdict_is_not_a_gates_list_entry() -> None:
    # The compare output lists "gate": "side_effects" (status passed)
    # before the verdict's own "gate": null.
    seen = transcript.seen_verdicts(_turns("prove-rolling-fix"))
    assert [(v.outcome, v.reason, v.gate) for v in seen] == [
        ("INCONCLUSIVE", "insufficient_evidence", None)
    ]


def test_no_compare_means_no_verdict() -> None:
    assert transcript.seen_verdicts(_turns("prove-rolling-bench-edit")) == []


def test_final_text_is_the_last_result_event() -> None:
    turns = _turns("prove-rolling-wrong-output")
    last = turns[-1].result
    assert last is not None
    assert transcript.final_text(turns) == last["result"]
    tail = transcript.final_text(turns).rstrip().rstrip("`")
    assert tail.endswith("Result: REJECT (correctness, gate correctness)")


def test_assistant_texts_leave_out_tool_calls() -> None:
    texts = transcript.assistant_texts(_turns("prove-rolling-wrong-output"))
    assert texts
    assert all(isinstance(t, str) and t for t in texts)
    calls = transcript.tool_calls(_turns("prove-rolling-wrong-output"))
    assert all(c.input.get("command", "") not in texts for c in calls)


def test_metrics_sum_the_result_events() -> None:
    turns = _turns("prove-rolling-wrong-output")
    cost, count, seconds = transcript.result_metrics(turns)
    results = [t.result for t in turns if t.result is not None]
    assert len(results) == len(turns) > 1
    assert cost == pytest.approx(
        sum(r["total_cost_usd"] for r in results), abs=1e-9
    )
    assert count == sum(r["num_turns"] for r in results)
    assert seconds == pytest.approx(
        sum(r["duration_ms"] for r in results) / 1000, abs=1e-6
    )


def test_a_task_without_transcripts_has_no_turns(tmp_path: Path) -> None:
    assert transcript.load_turns(tmp_path) == []
    assert transcript.final_text([]) == ""


def test_saved_lines_carry_no_thinking_blocks() -> None:
    for path in FIRST.glob("*/turn-*.jsonl"):
        for line in path.read_text().splitlines():
            event = transcript.as_dict(json.loads(line))
            assert event is not None
            message = transcript.as_dict(event.get("message")) or {}
            blocks = (
                transcript.as_dict(b)
                for b in transcript.as_list(message.get("content"))
            )
            kinds = {b.get("type") for b in blocks if b is not None}
            assert "thinking" not in kinds


def test_a_printed_module_frame_does_not_outrank_the_hotspot() -> None:
    """The agent printed bench.py's ``<module>`` frame (total share near
    1) beside ``held_orders`` (82%, ranked first by the profile)."""
    run = HERE / "runs" / "skill-3e" / "find-membership-light"
    top, _ = transcript.profile_evidence(transcript.load_turns(run))
    assert top == "held_orders"


@pytest.mark.parametrize(
    ("task", "expected"),
    [
        ("find-rolling-light", "rolling_sum"),
        ("find-membership-light", "held_orders"),
    ],
)
def test_hotspots_printed_one_per_line_are_read(
    task: str, expected: str
) -> None:
    """Both agents printed ``result.hotspots[]`` as plain lines."""
    run = HERE / "runs" / "skill-3i" / task
    top, _ = transcript.profile_evidence(transcript.load_turns(run))
    assert top == expected


def test_lines_from_either_hotspot_list_are_read() -> None:
    """Lines of ``frames_by_total`` score as ``hotspots`` lines do.

    The lines are printed from a real py-spy capture's two rankings in
    the documented ``function file line self total`` form.
    """
    from perfproof.profilers.folded import rank_file
    from perfproof.schema import Hotspot

    ranking = rank_file(
        HERE.parent.parent / "tests/fixtures/py-spy/workload-native.txt",
        project_root=Path("/srv/project"),
        top_n=6,
    )

    def printed(frames: tuple[Hotspot, ...]) -> str:
        return "\n".join(
            f"{h.function} {h.file} {h.line} "
            f"{h.self_fraction:.3f} {h.total_fraction:.3f}"
            for h in frames
        )

    by_self = [
        h
        for h in ranking.by_self
        if h.function.isidentifier() and h.file is not None
    ]
    by_total = [h for h in ranking.by_total if h.file is not None]
    assert by_self
    assert any(h.self_fraction == 0 for h in by_total)
    names = transcript._ranked_hotspots(  # pyright: ignore[reportPrivateUsage]
        printed(tuple(by_self)) + "\n" + printed(tuple(by_total))
    )
    assert {h.function for h in by_self} <= set(names)
    assert {h.function for h in by_total} <= set(names)
