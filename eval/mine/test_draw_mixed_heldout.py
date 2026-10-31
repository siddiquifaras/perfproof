"""Tests for the mixed held-out draw and its committed record."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, cast

import draw
import draw_mixed_heldout as mixed
from task import Task, load_tasks

ROOT = Path(draw.__file__).resolve().parents[2]


def _record() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(mixed.RECORD.read_text()))


def _synthetic(name: str, files: list[str], stars: int = 5000) -> Task:
    base = next(t for t in load_tasks() if t.family == "hidden-fix")
    change = base.reference_change.model_copy(update={"files": files})
    return base.model_copy(
        update={
            "id": name,
            "stars": stars,
            "reference_change": change,
            "split": "development-candidate",
        }
    )


def _review(layer: str = "upper") -> dict[str, Any]:
    return {
        "keep": True,
        "reason": "r",
        "read": [],
        "pair": ["Python", "C/C++"],
        "layer": layer,
    }


def test_the_salt_is_the_one_the_spec_fixes() -> None:
    spec = " ".join((ROOT / "docs" / "evaluation.md").read_text().split())
    start = spec.index("**Mixed held-out set")
    section = spec[start : spec.index("## What we publish")]
    assert re.findall(r"salt\s+`([^`]+)`", section) == [mixed.SALT]
    assert mixed.SALT == "perfproof-mixed-heldout-2026-10-30"
    assert mixed.PICKS == 12
    assert mixed.MIN_STARS == 2000


def test_the_committed_record_is_what_the_rule_derives() -> None:
    record = mixed.load_record()
    derived = mixed.derive_current(record)
    assert derived.stopped is None
    assert json.loads(json.dumps(mixed.render(record, derived))) == _record()


def test_every_earlier_task_is_skipped() -> None:
    record = _record()
    earlier = mixed.examined_by_earlier_draws()
    named = mixed.named_by_task_files()
    assert named
    assert any("discovery" in why for why in earlier.values())
    ordered = {o["id"] for o in record["order"]}
    visited = {v["id"] for v in record["walk"]}
    for task_id in [*earlier, *named]:
        if task_id in record["pool"]:
            assert task_id in record["skipped"], task_id
        assert task_id not in ordered | visited | set(record["picks"])
    mined = set(record["mined"]["ids"])
    assert mined.isdisjoint(earlier)
    assert mined.isdisjoint(named)


def test_the_order_is_sha256_of_salt_and_id() -> None:
    record = _record()
    assert record["salt"] == mixed.SALT
    keys = [o["key"] for o in record["order"]]
    assert keys
    assert keys == sorted(keys)
    for entry in record["order"]:
        digest = hashlib.sha256((mixed.SALT + entry["id"]).encode())
        assert entry["key"] == digest.hexdigest()
    walked = [v["id"] for v in record["walk"]]
    assert walked == [o["id"] for o in record["order"]][: len(walked)]


def test_the_picks_are_the_builds_and_the_mix_is_reported() -> None:
    record = _record()
    picks = record["picks"]
    assert 0 < len(picks) <= mixed.PICKS
    for pick in picks:
        review = record["reviews"][pick]
        assert review["keep"] is True
        assert len(review["pair"]) >= 2
        assert record["builds"][pick]["ok"] is True
    assert record["mix"]["picks"] == len(picks)
    assert sum(record["mix"]["layers"].values()) == len(picks)
    assert "quotas" not in record
    # Each walked candidate is taken or says why not.
    walked = {v["id"]: v["outcome"] for v in record["walk"]}
    for pick in picks:
        assert walked[pick] == "taken"
    assert sum(o == "taken" for o in walked.values()) == len(picks)


def test_there_is_no_language_cap_or_quota() -> None:
    tasks = [_synthetic(f"t{i}", [f"a{i}.py"]) for i in range(15)]
    record: dict[str, Any] = {
        "reviews": {t.id: _review("upper") for t in tasks},
        "builds": {t.id: {"ok": True, "reason": ""} for t in tasks},
    }
    out = mixed.derive(tasks, record, salt="s")
    assert len(out.picks) == mixed.PICKS
    assert out.mix["languages"] == {"Python": mixed.PICKS}
    assert out.mix["layers"] == {"upper": mixed.PICKS}


def test_the_walk_follows_the_hash_and_skips_what_did_not_build() -> None:
    tasks = [_synthetic(f"t{i}", [f"a{i}.rs"]) for i in range(4)]
    ordered = sorted(tasks, key=lambda t: draw.order_key("s", t.id))
    record: dict[str, Any] = {
        "reviews": {t.id: _review() for t in tasks},
        "builds": {
            t.id: {"ok": t.id != ordered[0].id, "reason": "setup exited 1"}
            for t in tasks
        },
    }
    out = mixed.derive(tasks, record, salt="s")
    assert [o["id"] for o in out.order] == [t.id for t in ordered]
    assert out.walk[0].outcome == "passed"
    assert "did not build" in out.walk[0].reason
    assert out.picks == [t.id for t in ordered[1:]]


def test_a_candidate_with_no_check_stops_the_walk() -> None:
    tasks = [_synthetic("t0", ["a.rs"])]
    record: dict[str, Any] = {"reviews": {"t0": _review()}, "builds": {}}
    assert "sandboxed setup check" in (
        mixed.derive(tasks, record, salt="s").stopped or ""
    )


def test_a_pool_smaller_than_twelve_gives_fewer_picks() -> None:
    tasks = [_synthetic(f"p{i}", [f"a{i}.py"]) for i in range(8)]
    record: dict[str, Any] = {
        "reviews": {t.id: _review() for t in tasks},
        "builds": {t.id: {"ok": True, "reason": ""} for t in tasks},
    }
    out = mixed.derive(tasks, record, salt="s")
    assert len(out.picks) == 8


def test_a_candidate_under_the_star_floor_is_out() -> None:
    tasks = [_synthetic("small", ["a.rs"], stars=1999)]
    out = mixed.derive(tasks, {"reviews": {}, "builds": {}}, salt="s")
    assert "small" in out.removed
    assert out.stopped is None
    assert out.picks == []


def test_a_candidate_with_no_star_count_is_out() -> None:
    task = _synthetic("nostars", ["a.rs"]).model_copy(update={"stars": None})
    out = mixed.derive([task], {"reviews": {}, "builds": {}}, salt="s")
    assert out.removed == {"nostars": "the manifest records no star count"}
    assert out.picks == []
