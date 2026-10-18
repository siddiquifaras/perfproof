"""Executions, pair order and resuming, against real child processes."""

import json
import sys
from pathlib import Path

import pytest

import measure

PY = sys.executable


def _arm(name: str, path: Path) -> measure.Arm:
    return measure.Arm(name, "task", path, {"PATH": "/usr/bin:/bin"})


def test_orders_come_from_the_seed_alone() -> None:
    first = measure.orders(123, 400)
    assert first == measure.orders(123, 400)
    assert first[:10] == measure.orders(123, 10)
    assert first != measure.orders(124, 400)
    # A fair coin: 400 draws land well inside 150..250.
    assert 150 < sum(first) < 250


def test_execute_times_a_real_child(tmp_path: Path) -> None:
    run = measure.execute(
        [PY, "-c", "import time; time.sleep(0.05)"], tmp_path, {}, None
    )
    assert run.exit_code == 0
    assert run.wall_s >= 0.05
    assert run.instructions is None


def test_execute_records_failures(tmp_path: Path) -> None:
    code = measure.execute(
        [PY, "-c", "raise SystemExit(3)"], tmp_path, {}, None
    )
    assert code.exit_code == 3
    missing = measure.execute(
        [str(tmp_path / "no-such-tool")], tmp_path, {}, None
    )
    assert missing.exit_code == 127
    slow = measure.execute(
        [PY, "-c", "import time; time.sleep(5)"],
        tmp_path,
        {},
        None,
        timeout_s=0.2,
    )
    assert slow.exit_code == 124


def _header(n: int) -> dict[str, object]:
    return {"task_id": "t", "mode": "ab", "pairs": n}


def test_pairs_resume_after_a_cut_off_line(tmp_path: Path) -> None:
    n, seed = 6, 99
    log = tmp_path / "pairs.jsonl"
    header = _header(n)
    log.write_text(json.dumps({**header, "seed": seed}) + "\n")
    arms = (_arm("a", tmp_path), _arm("b", tmp_path))
    workload = f"{PY} -c pass"
    measure.run_pairs(arms, workload, 3, seed, log, 0, perf=False)
    # A crash mid-write leaves part of a line.
    with log.open("a") as fh:
        fh.write('{"i": 3, "a_fi')
    found, pairs = measure.read_log(log, header)
    assert found is not None
    assert found["seed"] == seed
    assert [p["i"] for p in pairs] == [0, 1, 2]
    log.write_text("".join(json.dumps(x) + "\n" for x in [found, *pairs]))
    segment = measure.run_pairs(arms, workload, n, seed, log, 3, perf=False)
    assert segment["first_pair"] == 3
    assert segment["failed"] is None
    assert len(segment["warmup"]) == 2 * measure.WARMUP
    _, pairs = measure.read_log(log, header)
    assert [p["i"] for p in pairs] == list(range(n))
    assert [p["a_first"] for p in pairs] == measure.orders(seed, n)
    assert all(p["a"]["exit_code"] == p["b"]["exit_code"] == 0 for p in pairs)


def test_pairs_stop_at_the_first_failure(tmp_path: Path) -> None:
    log = tmp_path / "pairs.jsonl"
    log.write_text(json.dumps({**_header(5), "seed": 1}) + "\n")
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "w.py").write_text("raise SystemExit(2)\n")
    (tmp_path / "w.py").write_text("pass\n")
    arms = (_arm("a", tmp_path), _arm("b", bad))
    segment = measure.run_pairs(arms, f"{PY} w.py", 5, 1, log, 0, perf=False)
    assert segment["failed"] == {"pair": 0, "arm": "b", "exit_code": 2}
    _, pairs = measure.read_log(log, _header(5))
    assert len(pairs) == 1


def test_a_log_from_another_run_is_refused(tmp_path: Path) -> None:
    log = tmp_path / "pairs.jsonl"
    log.write_text(json.dumps({**_header(5), "seed": 1}) + "\n")
    with pytest.raises(measure.LogMismatchError):
        measure.read_log(log, _header(6))
    assert measure.read_log(tmp_path / "none.jsonl", _header(5)) == (
        None,
        [],
    )
