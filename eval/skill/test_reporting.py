"""Report totals, files and the diff against a previous run.

Scores come from the saved runs under ``runs/``, never re-run.
"""

import json
import shutil
from pathlib import Path

import reporting
import runner
import scoring
import transcript
from taskset import load

HERE = Path(__file__).resolve().parent
FIRST = HERE / "runs" / "first"
BROKEN = HERE / "runs" / "broken"
TASKS = load()


def test_summary_matches_the_saved_one() -> None:
    saved = json.loads((FIRST / "scores.json").read_text())["summary"]
    summary = reporting.summarize(scoring.score_run(FIRST, TASKS))
    assert summary.model_dump() == saved
    assert summary.tasks == 20
    assert summary.trigger_total == 12
    assert summary.wrong_keeps == 0


def test_write_makes_json_and_markdown_and_compares(tmp_path: Path) -> None:
    run = tmp_path / "broken"
    shutil.copytree(BROKEN, run)
    (run / "scores.json").unlink()
    (run / "report.md").unlink()
    reporting.write(run, scoring.score_run(run, TASKS), FIRST / "scores.json")
    data = json.loads((run / "scores.json").read_text())
    assert [t["task_id"] for t in data["tasks"]] == [
        "prove-rolling-fix",
        "prove-membership-fix",
    ]
    assert (data["skill_sha"] == data["tasks"] and False) or data["skill_sha"]
    report = (run / "report.md").read_text()
    assert "## Against the previous run" in report
    assert "`prove-rolling-fix`: ok to FAIL" in report
    assert "0 before, 4 now" in report
    assert "18 tasks of the previous run were not run here" in report


def test_broken_skill_has_a_different_hash_from_the_committed_one() -> None:
    first = json.loads((FIRST / "scores.json").read_text())
    broken = json.loads((BROKEN / "scores.json").read_text())
    assert first["skill_sha"] != broken["skill_sha"]


def test_question_left_open_is_detected_from_real_final_messages() -> None:
    asks = transcript.load_turns(FIRST / "prove-rolling-fix")
    first = asks[0].result
    assert first is not None
    assert runner.asks_question(first)
    done = transcript.load_turns(FIRST / "neg-typo")[0].result
    assert done is not None
    assert not runner.asks_question(done)


def test_session_over_follows_the_result_line() -> None:
    def result(task: str, turn: int) -> dict[str, object]:
        found = transcript.load_turns(FIRST / task)[turn].result
        assert found is not None
        return found

    # A correct refusal ends the session: no reply may follow it.
    assert runner.session_over(result("prove-membership-bench-edit", 0))
    assert runner.session_over(result("prove-rolling-wrong-output", -1))
    # NO VERDICT because the machine is busy is a question to answer.
    assert not runner.session_over(result("prove-rolling-fix", 0))
    # No Result line at all: not over.
    assert not runner.session_over(result("neg-typo", 0))
