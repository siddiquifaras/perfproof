import argparse
import ast
import json
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.cli import main
from perfproof.commands import log as log_command
from perfproof.hypotheses import (
    MAX_LINE_BYTES,
    HypothesisLogError,
    SourceCache,
    code_state,
    current_workload_spec_hash,
    definition_hash,
    log_lock,
    log_path,
    read_log,
    record_event,
)
from perfproof.schema import HYPOTHESIS_EVENT, CodeAnchor

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_D = "d" * 64
WORKLOAD = "bench-tile"

SOURCE = """\
import functools


def other() -> int:
    return 1


class Writer:
    @functools.cache
    def write(self, tile: int) -> int:
        return tile * 2

    def flush(self) -> None:
        pass


def resample(row: list[int]) -> list[int]:
    out = []
    for x in row:
        out.append(x + 1)
    return out
"""

type Cli = Callable[..., tuple[int, dict[str, Any]]]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Cli:
    def run(*argv: str) -> tuple[int, dict[str, Any]]:
        status = main([*argv, "--json"])
        out, _ = capsys.readouterr()
        document = cast("dict[str, Any]", json.loads(out))
        return status, document

    return run


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(
        ["git", "init", "-q", str(tmp_path)], check=True, timeout=30
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "tiles.py").write_text(SOURCE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PERFPROOF_SESSION", raising=False)
    return tmp_path


def evidence(kind: str = "measurement", sha: str = HASH_A) -> dict[str, Any]:
    return {"kind": kind, "path": f"scratch/{kind}.json", "sha256": sha}


def source_hash(cli: Cli, name: str = "resample") -> str:
    status, doc = cli("log", "anchor", "pkg/tiles.py", name)
    assert status == 0
    return cast("str", doc["result"]["source_hash"])


def anchors(code_hash: str, spec_hash: str = HASH_B) -> dict[str, Any]:
    return {
        "workload": WORKLOAD,
        "workload_spec_hash": spec_hash,
        "code": [
            {
                "file": "pkg/tiles.py",
                "qualified_name": "resample",
                "source_hash": code_hash,
            }
        ],
    }


def step(name: str, hid: str = "H1", **fields: Any) -> dict[str, Any]:
    bodies: dict[str, dict[str, Any]] = {
        "mechanism": {
            "claim": "the row loop runs in Python per pixel",
            "would_be_false_if": "the loop is already vectorised",
            "evidence": [evidence("profile")],
        },
        "prediction": {
            "cap": {
                "kind": "amdahl",
                "share": 0.38,
                "upper_bound": 1 / (1 - 0.38),
                "derivation": "38% of wall time in the profile",
            },
            "expect_if_true": "stubbing it removes about 38% of time",
            "expect_if_false": "time barely moves",
            "evidence": [evidence("profile")],
        },
        "falsification": {
            "test": "stub_out",
            "description": "ran with resample stubbed",
            "result": "supported",
            "evidence": [evidence("cheap_test", HASH_D)],
        },
        "implementation": {
            "diff_hash": HASH_D,
            "worktree": ".perfproof/worktrees/h1",
            "approach": "vectorise the row loop",
            "evidence": [evidence("diff", HASH_D)],
        },
        "outcome": {
            "verdict": "KEEP",
            "reason": "performance",
            "rule": 2,
            "calibration": "uncalibrated",
            "bar": 1.03,
            "run_record_hash": HASH_D,
            "speedup": {
                "estimate": 1.34,
                "interval": {"lower": 1.29, "upper": 1.4, "confidence": 0.99},
            },
            "evidence": [evidence("run_record", HASH_D)],
        },
        "skipped": {"step": "falsification", "reason": "no cheap test exists"},
    }
    return {"event": name, "hypothesis_id": hid, **bodies[name], **fields}


def observe(code_hash: str, hid: str = "H1", **fields: Any) -> dict[str, Any]:
    return {
        "event": "observation",
        "hypothesis_id": hid,
        "anchors": anchors(code_hash),
        "evidence": [evidence("profile")],
        **fields,
    }


def add(
    cli: Cli, event: dict[str, Any], workload: str = WORKLOAD
) -> tuple[int, dict[str, Any]]:
    return cli("log", "add", workload, "--event", json.dumps(event))


def lines(repo: Path, workload: str = WORKLOAD) -> list[bytes]:
    data = log_path(repo, workload).read_bytes()
    return [x for x in data.split(b"\n") if x]


def no_events(repo: Path) -> bool:
    path = log_path(repo, WORKLOAD)
    return not path.exists() or path.read_bytes() == b""


def error_code(doc: dict[str, Any]) -> str:
    assert doc["ok"] is False
    return cast("str", doc["error"]["code"])


def edit_source(repo: Path, old: str, new: str) -> None:
    path = repo / "pkg" / "tiles.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
    )


def show_args(*, brief: bool) -> argparse.Namespace:
    return argparse.Namespace(
        repo=Path(),
        action="show",
        workload=WORKLOAD,
        hypothesis=None,
        brief=brief,
    )


# Each step of the chain


def test_every_step_of_the_chain_is_recorded(repo: Path, cli: Cli) -> None:
    h = source_hash(cli)
    events = [
        observe(h),
        step("mechanism"),
        step("prediction"),
        step("falsification"),
        step("implementation"),
        step("outcome"),
    ]
    for e in events:
        status, doc = add(cli, e)
        assert status == 0, doc
        assert doc["result"]["recorded"]["event"] == e["event"]
    assert len(lines(repo)) == 6
    status, doc = cli("log", "show", WORKLOAD, "--hypothesis", "H1")
    assert status == 0
    (summary,) = doc["result"]["hypotheses"]
    assert set(summary["steps"].values()) == {"done"}
    assert summary["next_step"] is None
    assert summary["falsification"] == "supported"
    assert summary["cap"] == pytest.approx(1 / 0.62)
    assert summary["measured"]["estimate"] == 1.34
    assert summary["verdict"] == "KEEP"
    assert summary["stale"] == []
    assert [e["event"] for e in doc["result"]["events"]] == [
        e["event"] for e in events
    ]


def test_fields_perfproof_owns_are_filled_in(
    repo: Path, cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PERFPROOF_SESSION", "s-env")
    add(cli, observe(source_hash(cli)))
    stored = json.loads(lines(repo)[0])
    assert stored["session"] == "s-env"
    assert stored["event_id"].startswith("e-")
    assert stored["recorded_at"].endswith(("Z", "+00:00"))
    assert stored["schema_version"] == 1


def test_a_skipped_step_needs_a_recorded_reason(repo: Path, cli: Cli) -> None:
    add(cli, observe(source_hash(cli)))
    status, doc = add(cli, step("prediction"))
    assert status == 1
    assert error_code(doc) == "step_missing"
    assert doc["error"]["details"]["step"] == "mechanism"
    skip = step("skipped", step="mechanism", reason="obvious from profile")
    assert add(cli, skip)[0] == 0
    assert add(cli, step("prediction"))[0] == 0
    assert len(lines(repo)) == 3


def test_no_cheap_test_may_be_recorded_with_its_reason(
    repo: Path, cli: Cli
) -> None:
    add(cli, observe(source_hash(cli)))
    for name in ("mechanism", "prediction"):
        assert add(cli, step(name))[0] == 0
    assert add(cli, step("implementation"))[0] == 1
    assert add(cli, step("skipped"))[0] == 0
    assert add(cli, step("implementation"))[0] == 0


def test_event_for_an_unobserved_hypothesis_is_refused(
    repo: Path, cli: Cli
) -> None:
    status, doc = add(cli, step("mechanism", "H9"))
    assert status == 1
    assert error_code(doc) == "unknown_hypothesis"
    assert no_events(repo)


def test_repeated_observation_and_event_id_are_refused(
    repo: Path, cli: Cli
) -> None:
    h = source_hash(cli)
    assert add(cli, observe(h, event_id="e-1"))[0] == 0
    _, doc = add(cli, observe(h))
    assert error_code(doc) == "duplicate_hypothesis"
    _, doc = add(cli, observe(h, "H2", event_id="e-1"))
    assert error_code(doc) == "duplicate_event"
    assert len(lines(repo)) == 1


def test_corrections_and_retries_must_name_what_exists(
    repo: Path, cli: Cli
) -> None:
    h = source_hash(cli)
    add(cli, observe(h))
    _, doc = add(cli, step("mechanism", corrects="e-nope"))
    assert error_code(doc) == "unknown_event"
    retry = observe(h, "H2", retry_of="H7", what_differs="new code path")
    _, doc = add(cli, retry)
    assert error_code(doc) == "unknown_retry"
    reorder = {
        "event": "reordered",
        "hypothesis_id": "H1",
        "before": "H7",
        "reason": "cheaper",
    }
    _, doc = add(cli, reorder)
    assert error_code(doc) == "unknown_before"


def test_invalid_events_are_refused_before_anything_is_written(
    repo: Path, cli: Cli
) -> None:
    status, doc = cli("log", "add", WORKLOAD, "--event", "{nope")
    assert status == 1
    assert error_code(doc) == "invalid_event"
    _, doc = add(cli, {"event": "observation", "hypothesis_id": "H1"})
    assert error_code(doc) == "invalid_event"
    _, doc = cli("log", "add", "../escape", "--event", "{}")
    assert error_code(doc) == "invalid_name"
    assert not (repo / ".perfproof").exists()


def test_a_line_over_the_bound_is_refused(repo: Path, cli: Cli) -> None:
    big = observe(source_hash(cli))
    big["anchors"]["code"][0]["qualified_name"] = "x" * MAX_LINE_BYTES
    _, doc = add(cli, big)
    assert error_code(doc) == "line_too_long"
    assert no_events(repo)


# Refuted hypotheses


def refuted_hypothesis(cli: Cli) -> None:
    add(cli, observe(source_hash(cli)))
    for name in ("mechanism", "prediction"):
        add(cli, step(name))
    add(cli, step("falsification", result="refuted", event_id="e-test"))


def test_implementing_a_refuted_hypothesis_is_refused(
    repo: Path, cli: Cli
) -> None:
    refuted_hypothesis(cli)
    before = log_path(repo, WORKLOAD).read_bytes()
    status, doc = add(cli, step("implementation"))
    assert status == 1
    assert error_code(doc) == "refuted_hypothesis"
    assert doc["error"]["details"]["falsification"] == "e-test"
    assert log_path(repo, WORKLOAD).read_bytes() == before


def test_a_reason_overrides_the_refusal_and_is_recorded(
    repo: Path, cli: Cli
) -> None:
    refuted_hypothesis(cli)
    status, _ = add(
        cli, step("implementation", override_reason="stub hid a real cost")
    )
    assert status == 0
    stored = json.loads(lines(repo)[-1])
    assert stored["override_reason"] == "stub hid a real cost"


@pytest.mark.parametrize("result", ["supported", "inconclusive"])
def test_other_cheap_test_results_do_not_block(
    repo: Path, cli: Cli, result: str
) -> None:
    add(cli, observe(source_hash(cli)))
    for name in ("mechanism", "prediction"):
        add(cli, step(name))
    add(cli, step("falsification", result=result))
    assert add(cli, step("implementation"))[0] == 0


def test_a_corrected_refutation_no_longer_blocks(repo: Path, cli: Cli) -> None:
    refuted_hypothesis(cli)
    add(cli, step("falsification", result="supported", corrects="e-test"))
    assert add(cli, step("implementation"))[0] == 0


def test_a_refuted_hypothesis_is_retried_as_a_new_one(
    repo: Path, cli: Cli
) -> None:
    refuted_hypothesis(cli)
    retry = observe(
        source_hash(cli),
        "H2",
        retry_of="H1",
        what_differs="the loop is now over a numpy view",
    )
    status, _ = add(cli, retry)
    assert status == 0


# Anchors and staleness


def test_a_definition_that_moves_keeps_its_hash() -> None:
    moved = (
        "def inserted() -> None:\n    pass\n\n\n"
        + SOURCE.replace("def other", "def renamed_other")
        + "\n\nVALUE = 1\n"
    )
    for name in ("resample", "Writer.write", "Writer.flush"):
        assert definition_hash(moved, name) == definition_hash(SOURCE, name)
    assert definition_hash(SOURCE, "Writer.write") != definition_hash(
        SOURCE, "Writer.flush"
    )
    assert definition_hash(SOURCE, "Writer.missing") is None


def test_matching_after_a_move_within_the_file(repo: Path, cli: Cli) -> None:
    h = source_hash(cli)
    add(cli, observe(h))
    path = repo / "pkg" / "tiles.py"
    head, tail = path.read_text(encoding="utf-8").split("def resample", 1)
    path.write_text(
        "def resample" + tail + "\n\n" + head.rstrip() + "\n",
        encoding="utf-8",
    )
    assert source_hash(cli) == h
    _, doc = cli("log", "show", WORKLOAD)
    assert doc["result"]["hypotheses"][0]["stale"] == []


def test_a_hypothesis_is_stale_after_its_code_changes(
    repo: Path, cli: Cli
) -> None:
    add(cli, observe(source_hash(cli)))
    edit_source(repo, "x + 1", "x + 2")
    _, doc = cli("log", "show", WORKLOAD)
    (summary,) = doc["result"]["hypotheses"]
    assert [s["anchor"] for s in summary["stale"]] == ["code"]
    assert "resample" in summary["stale"][0]["detail"]
    # Staleness is derived: reading never appends.
    assert len(lines(repo)) == 1


def test_a_removed_definition_makes_it_stale(repo: Path, cli: Cli) -> None:
    add(cli, observe(source_hash(cli)))
    (repo / "pkg" / "tiles.py").write_text("X = 1\n", encoding="utf-8")
    _, doc = cli("log", "show", WORKLOAD)
    assert doc["result"]["hypotheses"][0]["stale"][0]["anchor"] == "code"


def test_a_redefined_workload_makes_it_stale(repo: Path, cli: Cli) -> None:
    add(cli, observe(source_hash(cli)))
    store = repo / ".perfproof" / "benchmarks" / WORKLOAD
    store.mkdir(parents=True)
    (store / f"{HASH_B}.json").write_text("{}", encoding="utf-8")
    _, doc = cli("log", "show", WORKLOAD)
    (summary,) = doc["result"]["hypotheses"]
    assert summary["stale"] == []
    assert summary["unchecked"] == []
    # With the anchored spec the only one stored and equal, it is fresh;
    # another spec as the only one stored means it was redefined.
    (store / f"{HASH_B}.json").rename(store / f"{HASH_D}.json")
    _, doc = cli("log", "show", WORKLOAD)
    now = [s["anchor"] for s in doc["result"]["hypotheses"][0]["stale"]]
    assert now == ["workload"]


def test_a_real_redefinition_leaves_both_specs_and_is_unchecked(
    repo: Path, cli: Cli
) -> None:
    add(cli, observe(source_hash(cli)))
    store = repo / ".perfproof" / "benchmarks" / WORKLOAD
    store.mkdir(parents=True)
    for h in (HASH_B, HASH_D):
        (store / f"{h}.json").write_text("{}", encoding="utf-8")
    assert current_workload_spec_hash(repo, WORKLOAD) is None
    _, doc = cli("log", "show", WORKLOAD)
    (summary,) = doc["result"]["hypotheses"]
    assert summary["stale"] == []
    assert [u["anchor"] for u in summary["unchecked"]] == ["workload"]
    assert (
        "UNCHECKED: workload" in log_command.run(show_args(brief=False)).text
    )


def test_unchecked_is_shown_in_every_form(repo: Path, cli: Cli) -> None:
    _, doc = add(cli, observe(source_hash(cli)))
    assert doc["result"]["hypothesis"]["unchecked"][0]["anchor"] == "workload"
    _, doc = cli(
        "log",
        "add",
        WORKLOAD,
        "--brief",
        "--event",
        json.dumps(
            observe(source_hash(cli), "H2", anchors=anchors(source_hash(cli)))
        ),
    )
    assert doc["result"]["hypothesis"]["unchecked"] == ["workload"]
    _, doc = cli("log", "show", WORKLOAD, "--brief")
    assert all(
        h["unchecked"] == ["workload"] for h in doc["result"]["hypotheses"]
    )
    assert "UNCHECKED: workload" in log_command.run(show_args(brief=True)).text


def test_unknowns_are_reported_not_called_fresh(repo: Path, cli: Cli) -> None:
    foreign = CodeAnchor(
        file="src/x.rs", qualified_name="f", source_hash=HASH_A
    )
    assert code_state(repo, foreign)[0] == "unchecked"


def test_revalidation_clears_a_recorded_stale_event(
    repo: Path, cli: Cli
) -> None:
    h = source_hash(cli)
    add(cli, observe(h))
    stale = {
        "event": "stale",
        "hypothesis_id": "H1",
        "anchor": "workload",
        "detail": "benchmark spec redefined",
        "evidence": [evidence()],
    }
    add(cli, stale)
    _, doc = cli("log", "show", WORKLOAD)
    assert doc["result"]["hypotheses"][0]["stale"][0]["anchor"] == "workload"
    revalidated = {
        "event": "revalidated",
        "hypothesis_id": "H1",
        "anchors": anchors(h),
        "what_differs": "nothing; measured again",
        "evidence": [evidence()],
    }
    add(cli, revalidated)
    _, doc = cli("log", "show", WORKLOAD)
    assert doc["result"]["hypotheses"][0]["stale"] == []


# Past hypotheses on the same code


def test_past_hypotheses_on_the_same_code_are_shown_with_a_new_one(
    repo: Path, cli: Cli
) -> None:
    h = source_hash(cli)
    refuted_hypothesis(cli)
    add(cli, observe(h, "A1"), workload="other-bench")
    unrelated = observe(source_hash(cli, "other"), "U1")
    unrelated["anchors"]["code"][0]["qualified_name"] = "other"
    add(cli, unrelated)
    edit_source(repo, "x + 1", "x + 2")
    status, doc = add(cli, observe(source_hash(cli), "H3"))
    assert status == 0
    past = {p["hypothesis_id"]: p for p in doc["result"]["past"]}
    assert set(past) == {"H1", "A1"}
    assert past["H1"]["falsification"] == "refuted"
    assert past["H1"]["workload"] == WORKLOAD
    assert past["A1"]["workload"] == "other-bench"
    assert [s["anchor"] for s in past["H1"]["stale"]] == ["code"]
    assert doc["result"]["past_omitted"] == 0


def test_past_list_is_capped(repo: Path, cli: Cli) -> None:
    h = source_hash(cli)
    for i in range(log_command.PAST_MAX + 3):
        add(cli, observe(h, f"P{i}"))
    _, doc = add(cli, observe(h, "NEW"))
    assert len(doc["result"]["past"]) == log_command.PAST_MAX
    assert doc["result"]["past_omitted"] == 3


# Corrupt lines


def test_a_corrupt_line_is_skipped_and_kept(repo: Path, cli: Cli) -> None:
    add(cli, observe(source_hash(cli)))
    path = log_path(repo, WORKLOAD)
    good = lines(repo)[0]
    junk = [
        b"not json at all",
        b"\xff\xfe\x00 bad bytes",
        b'{"event": "observation", "hypothesis_id": "H5"}',
        good,
    ]
    with path.open("ab") as handle:
        handle.write(b"\n".join(junk) + b"\n" + good[:40])
    before = path.read_bytes()
    contents = read_log(path)
    assert len(contents.events) == 1
    assert [c.number for c in contents.corrupt] == [2, 3, 4, 5, 6]
    assert path.read_bytes() == before
    status, doc = cli("log", "show", WORKLOAD)
    assert status == 0
    assert len(doc["result"]["warnings"]) == 5
    assert "line 2 skipped" in doc["result"]["warnings"][0]
    # The next append starts on its own line and the old bytes stay.
    assert add(cli, step("mechanism"))[0] == 0
    after = path.read_bytes()
    assert after.startswith(before)
    assert read_log(path).events[-1].event == "mechanism"
    assert len(read_log(path).corrupt) == 5


# Two sessions at once

WRITER = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from perfproof.hypotheses import append_event, log_path
from perfproof.schema import HYPOTHESIS_EVENT

root, who, count = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
pad = "p" * 6000
for i in range(count):
    event = HYPOTHESIS_EVENT.validate_python({
        "event": "mechanism",
        "hypothesis_id": f"H-{who}",
        "event_id": f"e-{who}-{i}",
        "recorded_at": datetime.now(UTC),
        "session": who,
        "claim": pad + str(i),
        "would_be_false_if": "never",
        "evidence": [{"kind": "profile", "path": "p.json",
                      "sha256": "a" * 64}],
    })
    append_event(log_path(root, "shared"), event)
"""


def test_two_sessions_appending_at_once_lose_and_split_nothing(
    tmp_path: Path,
) -> None:
    count = 60
    who = ["s1", "s2", "s3", "s4"]
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WRITER, str(tmp_path), name, str(count)]
        )
        for name in who
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    contents = read_log(log_path(tmp_path, "shared"))
    assert contents.corrupt == ()
    assert len(contents.events) == count * len(who)
    for name in who:
        ids = [e.event_id for e in contents.events if e.session == name]
        assert ids == [f"e-{name}-{i}" for i in range(count)]


# Output stays small


def test_brief_output_has_a_byte_cap(
    repo: Path, cli: Cli, capsys: pytest.CaptureFixture[str]
) -> None:
    h = source_hash(cli)
    for i in range(40):
        add(cli, observe(h, f"H{i}"))
        add(cli, step("mechanism", f"H{i}"))
        add(cli, step("prediction", f"H{i}"))
    _, doc = cli("log", "show", WORKLOAD, "--brief")
    result = doc["result"]
    shown = len(result["hypotheses"])
    assert 0 < shown <= log_command.BRIEF_MAX_HYPOTHESES
    assert result["omitted"] == 40 - shown
    assert len(json.dumps(result, indent=2, sort_keys=True)) <= (
        log_command.BRIEF_MAX_JSON_BYTES
    )
    text = log_command.run(show_args(brief=True)).text
    assert len(text.encode()) <= log_command.BRIEF_MAX_BYTES
    assert main(["log", "show", WORKLOAD, "--brief"]) == 0
    out, _ = capsys.readouterr()
    assert len(out.encode()) <= log_command.BRIEF_MAX_BYTES + 1


def test_brief_cut_drops_whole_lines_and_json_has_a_cap(
    repo: Path, cli: Cli
) -> None:
    h = source_hash(cli)
    ids = [f"{'long-hypothesis-id-' * 20}{i}" for i in range(12)]
    for hid in ids:
        add(cli, observe(h, hid))
        add(cli, step("mechanism", hid))
        add(cli, step("prediction", hid))
    text = log_command.run(show_args(brief=True)).text
    body = text.split("\n")
    assert len(text.encode()) <= log_command.BRIEF_MAX_BYTES
    assert body[-1].startswith("... ")
    assert body[-1].endswith("more lines")
    kept = [x for x in body if x.startswith("  ")]
    assert kept
    assert all(x.endswith("(bound)  retry of None") is False for x in kept)
    assert all("cap 1.61x (bound)" in x for x in kept)
    _, doc = cli("log", "show", WORKLOAD, "--brief")
    result = doc["result"]
    assert len(result["hypotheses"]) < log_command.BRIEF_MAX_HYPOTHESES
    assert result["omitted"] == 12 - len(result["hypotheses"])
    assert len(json.dumps(result, indent=2, sort_keys=True)) <= (
        log_command.BRIEF_MAX_JSON_BYTES
    )


def test_a_cap_is_never_worded_as_a_gain(repo: Path, cli: Cli) -> None:
    add(cli, observe(source_hash(cli)))
    add(cli, step("mechanism"))
    add(cli, step("prediction"))
    text = log_command.run(show_args(brief=False)).text
    assert "(bound)" in text
    assert "faster" not in text


def test_evidence_files_of_measure_and_profile_can_be_cited(
    repo: Path, cli: Cli
) -> None:
    store = repo / ".perfproof" / "evidence" / WORKLOAD
    store.mkdir(parents=True)
    file = store / "20261019T101500Z-profile-a1b2c3.json"
    file.write_text("{}", encoding="utf-8")
    cited = {
        "kind": "profile",
        "path": file.relative_to(repo).as_posix(),
        "sha256": HASH_A,
    }
    status, _ = add(cli, observe(source_hash(cli), evidence=[cited]))
    assert status == 0
    assert json.loads(lines(repo)[0])["evidence"] == [cited]


def test_anchor_refuses_what_it_cannot_hash(repo: Path, cli: Cli) -> None:
    _, doc = cli("log", "anchor", "pkg/tiles.py", "nope")
    assert error_code(doc) == "code_not_found"
    _, doc = cli("log", "anchor", "pkg/tiles.rs", "f")
    assert error_code(doc) == "code_not_found"
    _, doc = cli("log", "anchor", "../x.py", "f")
    assert error_code(doc) == "code_not_found"


# Review findings


def test_a_correction_must_match_the_step_and_type(
    repo: Path, cli: Cli
) -> None:
    refuted_hypothesis(cli)
    for wrong in (
        step("mechanism", corrects="e-test"),
        step("skipped", step="falsification", corrects="e-test"),
        step("prediction", corrects="e-test"),
    ):
        _, doc = add(cli, wrong)
        assert error_code(doc) == "invalid_correction"
    assert add(cli, step("implementation"))[0] == 1
    skip = step("skipped", step="mechanism", reason="r", event_id="e-skip")
    assert add(cli, skip)[0] == 0
    wrong_step = step("skipped", step="prediction", corrects="e-skip")
    assert error_code(add(cli, wrong_step)[1]) == "invalid_correction"


def test_a_blank_override_reason_is_refused(repo: Path, cli: Cli) -> None:
    refuted_hypothesis(cli)
    _, doc = add(cli, step("implementation", override_reason="  \t"))
    assert error_code(doc) == "empty_override_reason"


OBSERVE_ONCE = """
import sys
from datetime import UTC, datetime
from pathlib import Path
from perfproof.hypotheses import HypothesisLogError, log_path, record_event
from perfproof.schema import HYPOTHESIS_EVENT

root, who = Path(sys.argv[1]), sys.argv[2]
event = HYPOTHESIS_EVENT.validate_python({
    "event": "observation", "hypothesis_id": "SAME",
    "event_id": f"e-{who}", "recorded_at": datetime.now(UTC),
    "session": who,
    "anchors": {"workload": "shared", "workload_spec_hash": "b" * 64},
    "evidence": [{"kind": "profile", "path": "p.json", "sha256": "a" * 64}],
})
try:
    record_event(log_path(root, "shared"), event)
except HypothesisLogError as exc:
    sys.exit(7 if exc.code == "duplicate_hypothesis" else 1)
"""


def test_checks_and_append_are_one_step_across_processes(
    tmp_path: Path,
) -> None:
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", OBSERVE_ONCE, str(tmp_path), f"s{i}"]
        )
        for i in range(8)
    ]
    codes = sorted(proc.wait(timeout=120) for proc in procs)
    assert codes == [0, *[7] * 7]
    assert len(read_log(log_path(tmp_path, "shared")).events) == 1


def test_a_chain_broken_by_a_racing_writer_is_flagged_on_read(
    repo: Path, cli: Cli
) -> None:
    refuted_hypothesis(cli)
    # What a session that read the log before the refutation appends.
    racing = json.dumps(
        {
            "event_id": "e-race",
            "recorded_at": "2026-10-19T12:00:00+00:00",
            "session": "s-late",
            **step("implementation"),
        }
    )
    with log_path(repo, WORKLOAD).open("a", encoding="utf-8") as handle:
        handle.write(racing + "\n")
    _, doc = cli("log", "show", WORKLOAD)
    (summary,) = doc["result"]["hypotheses"]
    assert summary["violation_count"] == 1
    assert "e-race" in summary["violations"][0]
    assert any("e-race" in w for w in doc["result"]["warnings"])
    assert "CHAIN INVALID" in log_command.run(show_args(brief=True)).text


def test_a_log_lock_times_out_instead_of_hanging(tmp_path: Path) -> None:
    path = log_path(tmp_path, "locked")
    with (
        log_lock(path),
        pytest.raises(HypothesisLogError) as err,
        log_lock(path, timeout=0.05),
    ):
        pass
    assert err.value.code == "log_locked"


def test_the_hash_ignores_characters_ast_does_not_break_lines_at() -> None:
    plain = "def f():\n    return 1\n"
    odd = "X = '\u2028'  # a\u2029b\x0b\x1c\x85\n\x0c\n" + plain
    assert definition_hash(odd, "f") == definition_hash(plain, "f")
    assert definition_hash(odd, "f") != definition_hash(
        plain.replace("1", "2"), "f"
    )
    crlf = odd.replace("\n", "\r\n")
    assert definition_hash(crlf, "f") == definition_hash(plain, "f")


PROPERTY = """\
class A:
    @property
    def x(self) -> int:
        return 1

    @x.setter
    def x(self, value: int) -> None:
        pass
"""

OVERLOAD = """\
from typing import overload


@overload
def f(a: int) -> int: ...
@overload
def f(a: str) -> str: ...
def f(a):
    return a
"""


def test_every_same_name_definition_is_hashed() -> None:
    base = definition_hash(PROPERTY, "A.x")
    assert base is not None
    assert (
        definition_hash(PROPERTY.replace("return 1", "return 2"), "A.x")
        != base
    )
    assert definition_hash(PROPERTY.replace("pass", "print(1)"), "A.x") != base
    over = definition_hash(OVERLOAD, "f")
    assert over is not None
    for old, new in (
        ("a: int) -> int", "a: bytes) -> int"),
        ("return a", "return 1"),
    ):
        assert definition_hash(OVERLOAD.replace(old, new), "f") != over


def test_unchanged_code_of_a_refuted_hypothesis_must_be_marked_a_retry(
    repo: Path, cli: Cli
) -> None:
    refuted_hypothesis(cli)
    _, doc = add(cli, observe(source_hash(cli), "H2"))
    assert error_code(doc) == "retry_unmarked"
    assert doc["error"]["details"]["hypothesis_id"] == "H1"
    marked = observe(
        source_hash(cli), "H2", retry_of="H1", what_differs="new input size"
    )
    assert add(cli, marked)[0] == 0


def test_changed_code_of_a_refuted_hypothesis_is_not_a_retry(
    repo: Path, cli: Cli
) -> None:
    refuted_hypothesis(cli)
    edit_source(repo, "x + 1", "x + 2")
    assert add(cli, observe(source_hash(cli), "H2"))[0] == 0


def test_record_event_waits_for_the_log_lock(tmp_path: Path) -> None:
    path = log_path(tmp_path, "locked")
    event = HYPOTHESIS_EVENT.validate_python(
        {
            "event": "observation",
            "hypothesis_id": "WAIT",
            "event_id": "e-wait",
            "recorded_at": "2026-10-19T12:00:00+00:00",
            "session": "s-wait",
            "anchors": {"workload": "locked", "workload_spec_hash": "b" * 64},
            "evidence": [
                {"kind": "profile", "path": "p.json", "sha256": "a" * 64}
            ],
        }
    )
    done = threading.Event()

    def write() -> None:
        record_event(path, event)
        done.set()

    with log_lock(path):
        worker = threading.Thread(target=write)
        worker.start()
        assert not done.wait(0.3)
    worker.join(timeout=10)
    assert done.is_set()
    assert len(read_log(path).events) == 1


def test_a_new_cheap_test_after_a_refutation_must_correct_it(
    repo: Path, cli: Cli
) -> None:
    refuted_hypothesis(cli)
    status, doc = add(cli, step("falsification", result="supported"))
    assert status != 0
    assert error_code(doc) == "rerun_unmarked"
    status, _ = add(
        cli,
        step(
            "falsification",
            result="supported",
            event_id="e-rerun",
            corrects="e-test",
        ),
    )
    assert status == 0


def test_a_file_is_parsed_once_for_every_anchor_in_it(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = ["Writer.write", "Writer.missing", "nope"]
    anchors = [
        CodeAnchor(
            file="pkg/tiles.py",
            qualified_name=names[i % 3],
            source_hash=HASH_A,
        )
        for i in range(30)
    ]
    expected = [code_state(repo, a) for a in anchors]
    parses: list[int] = []
    real = ast.parse

    def counting(*args: Any, **kwargs: Any) -> ast.Module:
        parses.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(ast, "parse", counting)
    cache = SourceCache()

    assert [code_state(repo, a, cache) for a in anchors] == expected
    assert len(parses) == 1
    parses.clear()
    code_state(repo, anchors[0])
    assert len(parses) == 1
