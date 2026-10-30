"""Tests for the second held-out discovery draw and its record."""

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, cast

import draw
import draw_discovery
import draw_discovery2
from draw_discovery import derive, task_tree_failure
from task import load_tasks

ROOT = Path(draw.__file__).resolve().parents[2]


def _first() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(draw.DISCOVERY.read_text()))


def test_the_salt_is_the_one_the_spec_fixes() -> None:
    spec = " ".join((ROOT / "docs" / "evaluation.md").read_text().split())
    start = spec.index("**Discovery supplement 2")
    section = spec[start : spec.index("**Mixed-stack set")]
    assert re.findall(r"salt (?:is )?`([^`]+)`", section) == [
        draw_discovery2.SALT
    ]
    assert draw_discovery2.SALT == "perfproof-heldout-discovery-2026-10-29"
    assert draw_discovery2.SALT != draw_discovery.SALT


def test_the_committed_record_is_what_the_rule_derives() -> None:
    record = draw_discovery2.load_record()
    derived = draw_discovery2.derive_second(record)
    assert derived.stopped is None
    rendered = draw_discovery.render(
        record,
        derived,
        rule=draw_discovery2.RULE,
        salt=draw_discovery2.SALT,
    )
    assert json.loads(json.dumps(rendered)) == json.loads(
        draw_discovery2.RECORD.read_text()
    )


def test_every_task_the_first_draw_examined_is_skipped() -> None:
    walk = [v["id"] for v in _first()["walk"]]
    assert walk
    record = json.loads(draw_discovery2.RECORD.read_text())
    ordered = {o["id"] for o in record["order"]}
    visited = {v["id"] for v in record["walk"]}
    for task_id in walk:
        assert task_id in record["skipped"], task_id
        assert "first discovery draw" in record["skipped"][task_id]
        assert task_id not in ordered | visited | set(record["picks"])
    assert set(record["picks"]).isdisjoint(_first()["picks"])


def test_examined_tasks_are_dropped_from_the_pool_in_a_synthetic_draw() -> (
    None
):
    tasks = [t for t in load_tasks() if t.family == "hidden-fix"]
    record = draw_discovery2.load_record()
    reviews = {
        **draw_discovery2.first_record()["reviews"],
        **record["reviews"],
    }
    kept = derive(
        tasks,
        record["mined"]["ids"],
        reviews,
        record.get("passed_over"),
        salt=draw_discovery2.SALT,
        later_mined=draw_discovery2.mixed_mined(),
    )
    victim = kept.order[0]["id"] if kept.order else None
    assert victim is not None
    out = derive(
        tasks,
        record["mined"]["ids"],
        reviews,
        record.get("passed_over"),
        salt=draw_discovery2.SALT,
        later_mined=draw_discovery2.mixed_mined(),
        examined={victim: "examined"},
    )
    assert out.skipped[victim] == "examined"
    assert victim not in [o["id"] for o in out.order]


def test_the_order_is_sha256_of_salt_and_id() -> None:
    record = json.loads(draw_discovery2.RECORD.read_text())
    assert record["salt"] == draw_discovery2.SALT
    keys = [o["key"] for o in record["order"]]
    assert keys == sorted(keys)
    for entry in record["order"]:
        digest = hashlib.sha256(
            (draw_discovery2.SALT + entry["id"]).encode()
        ).hexdigest()
        assert entry["key"] == digest


def test_the_picks_obey_the_rule() -> None:
    record = json.loads(draw_discovery2.RECORD.read_text())
    tasks = {t.id: t for t in load_tasks()}
    picks = record["picks"]
    assert 0 < len(picks) <= draw_discovery.PICKS
    langs = Counter(record["languages"][i] for i in picks)
    assert max(langs.values()) <= draw_discovery.PER_LANGUAGE
    named = draw.development_named()
    for pick in picks:
        task = tasks[pick]
        assert task.family == "hidden-fix"
        assert pick not in named
        assert task_tree_failure(task) is None
        assert task.split == "held-out"
    walked = [v["id"] for v in record["walk"]]
    order = [o["id"] for o in record["order"]]
    assert walked == order[: len(walked)]


def test_the_mined_candidates_are_in_neither_per_class_draw() -> None:
    record = draw_discovery2.load_record()
    mined = set(record["mined"]["ids"])
    assert mined.isdisjoint(draw_discovery.mined_ids())
    out = draw.draw_splits(load_tasks(), draw.load_selection())
    seen = {
        v.task_id
        for per_split in out.values()
        for d in per_split.values()
        for v in d.visits
    }
    assert not mined & seen
