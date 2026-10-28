"""Tests for the held-out discovery draw and its committed record."""

import json
from collections import Counter

import draw
import draw_discovery
from draw_discovery import derive, language_of, task_tree_failure
from task import Run, load_tasks


def _run(step: str, tree: str, code: int) -> Run:
    return Run.model_validate(
        {
            "step": step,
            "tree": tree,
            "command": "c",
            "exit_code": code,
            "duration_s": None,
            "date": "d",
            "machine": "m",
            "output_tail": "",
        }
    )


def test_language_is_the_most_common_source_language() -> None:
    assert language_of(["a/b.py", "tests/test_b.py", "docs/x.md"]) == "Python"
    assert language_of(["src/a.rs", "src/b.rs", "x.py"]) == "Rust"
    assert language_of(["README.md"]) == "unknown"
    assert language_of(["a.h", "a.cu"]) == "C/C++"


def test_only_the_task_tree_decides_whether_it_builds() -> None:
    steps = [_run(s, "task", 0) for s in ("setup", "oracle", "workload")]
    ref_fail = [_run("setup", "reference", 1)]
    base = next(t for t in load_tasks() if t.family == "hidden-fix")
    ok = base.model_copy(update={"runs": steps + ref_fail})
    assert task_tree_failure(ok) is None
    bad = base.model_copy(update={"runs": [_run("setup", "task", 3)]})
    assert "exited 3" in (task_tree_failure(bad) or "")
    short = base.model_copy(update={"runs": steps[:2]})
    assert "workload" in (task_tree_failure(short) or "")


def test_the_committed_record_is_what_the_rule_derives() -> None:
    record = draw_discovery.load_record()
    derived = derive(load_tasks(), record["mined"]["ids"], record["reviews"])
    assert derived.stopped is None
    assert json.loads(
        json.dumps(draw_discovery.render(record, derived))
    ) == json.loads(draw_discovery.RECORD.read_text())


def test_the_picks_obey_the_rule() -> None:
    record = draw_discovery.load_record()
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
    keys = [o["key"] for o in record["order"]]
    assert keys == sorted(keys)


def test_mined_candidates_are_not_in_the_per_class_draws() -> None:
    record = draw_discovery.load_record()
    mined = set(record["mined"]["ids"])
    sel = draw.load_selection()
    out = draw.draw_splits(load_tasks(), sel)
    seen = {
        v.task_id
        for per_split in out.values()
        for d in per_split.values()
        for v in d.visits
    }
    assert not mined & seen
