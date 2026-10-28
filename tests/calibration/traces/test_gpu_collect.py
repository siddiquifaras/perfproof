"""Tests of the GPU trace recorder, run with a CPU stand-in command.

``nvidia-smi`` is replaced by a stub script that prints what the real
tool's CSV mode prints in shape; that proves this module's parsing and
failure handling, not what a driver really answers (see GPU.md, "What
the CPU dry run did not prove").
"""

import json
import os
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import collect
import gpu_collect as gc
import pytest

OK_CMD = (
    f"{sys.executable} -c "
    '\'print("PERFPROOF {\\"value\\": 0.01, \\"peak_gpu_mem_bytes\\": 7}")\''
)
SEED = 5


@pytest.fixture(autouse=True)
def _restore_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collect, "TIMEOUT", collect.TIMEOUT)


def _argv(tmp: Path, rounds: int, cmd: str = OK_CMD, *more: str) -> list[str]:
    for name in ("a", "b"):
        (tmp / name).mkdir(exist_ok=True)
    return [
        "--out", str(tmp / "t"),
        "--dir-a", str(tmp / "a"),
        "--dir-b", str(tmp / "b"),
        "--cmd", cmd,
        "--rounds", str(rounds),
        "--seed", str(SEED),
        "--label", "test",
        "--target", "noop",
        "--no-gpu", "--probes", "2",
        *more,
    ]  # fmt: skip


def _rows(tmp: Path) -> list[dict[str, Any]]:
    text = (tmp / "t.jsonl").read_text()
    return [json.loads(x) for x in text.split("\n") if x]


def _row(pair: int, arm: str, pos: int, segment: int = 0) -> dict[str, object]:
    return {
        "pair": pair, "warmup": pair < 0, "pos": pos, "arm": arm,
        "reported_s": 0.5, "wall_s": 1.0, "load1": 0.0, "segment": segment,
    }  # fmt: skip


def _write(path: Path, rows: list[dict[str, object]], tail: str = "") -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + tail)


def _pair(
    pair: int, orders: list[str], segment: int = 0
) -> list[dict[str, object]]:
    first = orders[pair] if pair >= 0 else "A"
    second = "B" if first == "A" else "A"
    return [_row(pair, first, 0, segment), _row(pair, second, 1, segment)]


def test_trace_has_the_collect_format_plus_gpu_fields(tmp_path: Path) -> None:
    assert gc.main(_argv(tmp_path, 4)) == 0
    rows = _rows(tmp_path)
    assert len(rows) == 10
    assert [r["pair"] for r in rows] == [-1, -1, 0, 0, 1, 1, 2, 2, 3, 3]
    assert [r["warmup"] for r in rows[:3]] == [True, True, False]
    firsts = [r["arm"] for r in rows if not r["warmup"] and r["pos"] == 0]
    assert firsts == collect.round_orders(SEED, 4)
    for key in ("wall_s", "reported_s", "load1", "user_s", "sys_s", "t_utc"):
        assert all(key in r for r in rows)
    assert all(r["reported_s"] == 0.01 for r in rows)
    assert rows[0]["metrics"] == {"value": 0.01, "peak_gpu_mem_bytes": 7}
    assert all("gpu_sm_mhz" in r and "segment" in r for r in rows)
    header = json.loads((tmp_path / "t.header.json").read_text())
    assert header["order_seed"] == SEED
    assert header["standin"] is True
    assert "finished_utc" in header
    assert gc.verify_trace(tmp_path / "t.jsonl", 4) == []


def test_resume_repairs_and_never_duplicates(tmp_path: Path) -> None:
    assert gc.main(_argv(tmp_path, 4)) == 0
    path = tmp_path / "t.jsonl"
    lines = path.read_text().split("\n")[:-1]
    path.write_text("\n".join(lines[:-3]) + "\n" + lines[-3][:40])
    assert gc.main(_argv(tmp_path, 6)) == 0
    rows = _rows(tmp_path)
    keys = [(r["segment"], r["pair"], r["arm"]) for r in rows]
    assert len(keys) == len(set(keys))
    real = sorted({r["pair"] for r in rows if not r["warmup"]})
    assert real == [0, 1, 2, 3, 4, 5]
    assert [r["segment"] for r in rows if r["warmup"]] == [0, 0, 1, 1]
    header = json.loads((tmp_path / "t.header.json").read_text())
    assert header["resumed_from_pair"] == 2
    assert header["resumes"][0]["repair"]["torn_tail"] is True
    assert gc.verify_trace(path, 6) == []


def test_complete_trace_is_not_extended(tmp_path: Path) -> None:
    assert gc.main(_argv(tmp_path, 3)) == 0
    before = (tmp_path / "t.jsonl").read_text()
    assert gc.main(_argv(tmp_path, 3)) == 0
    assert (tmp_path / "t.jsonl").read_text() == before


def test_resume_refuses_another_seed_or_command(tmp_path: Path) -> None:
    assert gc.main(_argv(tmp_path, 2)) == 0
    other = _argv(tmp_path, 4)
    other[other.index("--seed") + 1] = "99"
    assert gc.main(other) == gc.USAGE_EXIT
    assert gc.main(_argv(tmp_path, 4, OK_CMD + " ")) == 0


def test_repair_drops_partial_duplicate_and_gapped_pairs(
    tmp_path: Path,
) -> None:
    orders = collect.round_orders(SEED, 10)
    path = tmp_path / "t.jsonl"
    rows = [
        *_pair(-1, orders),
        *_pair(0, orders),
        *_pair(1, orders),
        *_pair(1, orders, segment=1),
        _row(2, orders[2], 0),
        *_pair(4, orders),
    ]
    _write(path, rows)
    report = gc.repair_trace(path, orders)
    assert report["kept_pairs"] == 2
    assert report["dropped_rows"] == 2 + 1 + 2
    kept = [json.loads(x) for x in path.read_text().split("\n") if x]
    assert [(r["pair"], r["pos"]) for r in kept] == [
        (-1, 0), (-1, 1), (0, 0), (0, 1), (1, 0), (1, 1),
    ]  # fmt: skip


def test_repair_without_a_whole_pair_removes_the_file(tmp_path: Path) -> None:
    orders = collect.round_orders(SEED, 4)
    path = tmp_path / "t.jsonl"
    _write(path, _pair(-1, orders))
    assert gc.repair_trace(path, orders)["kept_pairs"] == 0
    assert not path.exists()


def test_repair_refuses_wrong_seed_and_corruption(tmp_path: Path) -> None:
    orders = collect.round_orders(SEED, 8)
    flipped = ["B" if o == "A" else "A" for o in orders]
    path = tmp_path / "t.jsonl"
    _write(path, [*_pair(-1, orders), *_pair(0, flipped)])
    with pytest.raises(ValueError, match="wrong seed"):
        gc.repair_trace(path, orders)
    path.write_text('{"pair": 0\n' + json.dumps(_row(0, "A", 0)) + "\n")
    with pytest.raises(ValueError, match="corrupt"):
        gc.repair_trace(path, orders)


def test_health_failure_exits_before_recording(tmp_path: Path) -> None:
    bad = f"{sys.executable} -c 'raise SystemExit(3)'"
    assert gc.main(_argv(tmp_path, 3, bad)) == gc.HEALTH_EXIT
    assert not (tmp_path / "t.jsonl").exists()
    health = json.loads((tmp_path / "t.health.json").read_text())
    assert health["ok"] is False


def test_health_requires_a_reported_value(tmp_path: Path) -> None:
    silent = f"{sys.executable} -c pass"
    assert gc.main(_argv(tmp_path, 3, silent)) == gc.HEALTH_EXIT


def test_health_only_writes_report_and_no_trace(tmp_path: Path) -> None:
    assert gc.main(_argv(tmp_path, 3, OK_CMD, "--health-only")) == 0
    health = json.loads((tmp_path / "t.health.json").read_text())
    assert health["ok"]
    assert health["pair_s"] > 0
    assert health["projected_s"] == pytest.approx(health["pair_s"] * 3)
    assert not (tmp_path / "t.jsonl").exists()


def test_time_budget_stops_resumably(tmp_path: Path) -> None:
    status = gc.main(_argv(tmp_path, 50, OK_CMD, "--time-budget-s", "0.3"))
    assert status == gc.BUDGET_EXIT
    done = len({r["pair"] for r in _rows(tmp_path) if not r["warmup"]})
    assert 0 <= done < 50
    header = json.loads((tmp_path / "t.header.json").read_text())
    assert header["stopped"]["reason"] == "time budget"
    assert "finished_utc" not in header
    assert gc.main(_argv(tmp_path, 50)) == 0
    assert gc.verify_trace(tmp_path / "t.jsonl", 50) == []


def test_sync_hook_gets_the_files_and_failure_is_not_fatal(
    tmp_path: Path,
) -> None:
    log = tmp_path / "sync.log"
    code = "import sys;open(sys.argv[1],'a').write(' '.join(sys.argv[2:]))"
    hook = f'{sys.executable} -c "{code}" {log} {{files}}'
    args = _argv(tmp_path, 4, OK_CMD, "--sync-every", "2", "--sync-cmd", hook)
    assert gc.main(args) == 0
    text = log.read_text()
    assert "t.jsonl" in text
    assert "t.header.json" in text
    assert text.count("t.jsonl") >= 2
    fail = _argv(tmp_path, 6, OK_CMD, "--sync-cmd", "false {files}")
    assert gc.main(fail) == 0
    header = json.loads((tmp_path / "t.header.json").read_text())
    assert header["sync_failures"] >= 1


def test_background_load_runs_and_is_stopped(tmp_path: Path) -> None:
    marker = tmp_path / "bg.pid"
    code = (
        f"import os,time;open('{marker}','w').write(str(os.getpid()));"
        "time.sleep(60)"
    )
    bg = f'{sys.executable} -c "{code}"'
    args = _argv(
        tmp_path, 2, OK_CMD, "--background", bg, "--background-settle-s", "0.5"
    )
    assert gc.main(args) == 0
    pid = int(marker.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_background_that_exits_fails_the_health_check(tmp_path: Path) -> None:
    args = _argv(
        tmp_path,
        2,
        OK_CMD,
        "--background",
        "false",
        "--background-settle-s",
        "0.5",
    )
    assert gc.main(args) == gc.HEALTH_EXIT


def _stub_smi(tmp: Path, body: str) -> None:
    script = tmp / "nvidia-smi"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def test_query_gpu_parses_absent_values_and_checks_the_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_smi(tmp_path, 'echo "Stub GPU, [N/A], 1500"\n')
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    got = gc.query_gpu(["name", "x", "clocks.sm"])
    assert got == [{"name": "Stub GPU", "x": None, "clocks.sm": "1500"}]
    with pytest.raises(gc.GpuError, match="expected 2"):
        gc.query_gpu(["name", "x"])


def test_sample_gpu_reads_numbers_and_degrades_to_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_smi(
        tmp_path, 'echo "1500, 6250, 61, 70.5, 99, 0x0000000000000000"\n'
    )
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    row = gc.sample_gpu("clocks_event_reasons.active")
    assert row["gpu_sm_mhz"] == 1500.0
    assert row["gpu_power_w"] == 70.5
    assert row["gpu_throttle"] == "0x0000000000000000"
    _stub_smi(tmp_path, "echo boom >&2; exit 2\n")
    bad = gc.sample_gpu("clocks_event_reasons.active")
    assert bad["gpu_sm_mhz"] is None
    assert "boom" in bad["gpu_sample_error"]


def test_throttle_field_falls_back_to_the_older_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_smi(
        tmp_path,
        'case "$1" in *clocks_throttle_reasons*) echo 0x0;;'
        " *) echo bad >&2; exit 2;; esac\n",
    )
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    assert gc.pick_throttle_field() == "clocks_throttle_reasons.active"


def test_missing_nvidia_smi_fails_the_health_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    argv = [a for a in _argv(tmp_path, 2, OK_CMD) if a != "--no-gpu"]
    argv[argv.index("--cmd") + 1] = OK_CMD
    assert gc.main(argv) == gc.HEALTH_EXIT


def test_verify_flags_duplicates_and_bad_times(tmp_path: Path) -> None:
    orders = collect.round_orders(SEED, 4)
    path = tmp_path / "t.jsonl"
    bad = _pair(1, orders)
    bad[0]["reported_s"] = None
    _write(
        path, [*_pair(-1, orders), *_pair(0, orders), *bad, *_pair(0, orders)]
    )
    problems = gc.verify_trace(path)
    assert any("duplicate" in p for p in problems)
    assert any("bad reported_s" in p for p in problems)
    assert "no header file" in problems


def test_plan_fits_targets_when_time_allows() -> None:
    pair_s = {
        "matmul": 10.0,
        "sgemm": 4.0,
        "membound": 10.0,
        "attention": 10.0,
    }
    plan, notes = gc.plan_pairs(pair_s, 14400)
    assert plan["sgemm"] >= 500
    assert plan["attention"] >= 300
    assert plan["matmul_loaded"] == 0
    spent = sum(plan[n] * pair_s[n] * gc.PLAN_MARGIN for n in pair_s)
    assert spent <= 14400
    assert all(n % 2 == 0 and n <= gc.MAX_PAIRS for n in plan.values())
    assert notes == []


def test_plan_adds_the_loaded_variant_and_raises_counts() -> None:
    names = ["matmul", "sgemm", "membound", "attention", "matmul_loaded"]
    pair_s: dict[str, float] = dict.fromkeys(names, 2.0)
    plan, _ = gc.plan_pairs(pair_s, 14400)
    assert plan["matmul_loaded"] >= gc.MIN_PAIRS
    assert plan["matmul"] == gc.MAX_PAIRS


def test_plan_cuts_sgemm_to_the_minimum_then_drops_in_order() -> None:
    base: dict[str, float] = {
        "matmul": 10.5,
        "sgemm": 10.5,
        "membound": 10.5,
        "attention": 10.5,
    }
    plan, notes = gc.plan_pairs(base, 14400)
    assert plan["sgemm"] == gc.MIN_PAIRS
    assert plan["attention"] == 300
    assert any("every workload at" in n for n in notes)
    slow = dict.fromkeys(base, 12.0)
    plan, notes = gc.plan_pairs(slow, 14400)
    assert plan["attention"] == 0
    assert plan["membound"] >= 300
    slower = dict.fromkeys(base, 18.0)
    plan, notes = gc.plan_pairs(slower, 14400)
    assert (plan["attention"], plan["membound"]) == (0, 0)
    assert plan["matmul"] >= 300
    assert plan["sgemm"] >= 300
    assert any("dropped membound" in n for n in notes)


def test_plan_is_empty_when_matmul_and_sgemm_cannot_fit() -> None:
    plan, notes = gc.plan_pairs({"matmul": 24.0, "sgemm": 24.0}, 14400)
    assert not any(plan.values())
    assert any("do not fit" in n for n in notes)


def test_plan_command_prints_env_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, ps in (("matmul", 12.0), ("sgemm", 4.0)):
        path = tmp_path / f"{name}.health.json"
        path.write_text(
            json.dumps({"workload": name, "ok": True, "pair_s": ps})
        )
    broken = tmp_path / "attention.health.json"
    broken.write_text(json.dumps({"workload": "attention", "ok": False}))
    argv = ["plan", "--budget-s", "14400", *map(str, tmp_path.glob("*.json"))]
    assert gc.main(argv) == 0
    out = capsys.readouterr().out
    assert "PAIRS_matmul=" in out
    assert "PAIRSEC_sgemm=4.00" in out
    assert "PAIRS_attention=0" in out


STUB_SMI = """#!/bin/sh
case "$1" in
--query-compute-apps*) ;;
--query-gpu=*)
  fields=${1#--query-gpu=}
  n=$(echo "$fields" | awk -F, '{print NF}')
  out=""
  i=0
  while [ "$i" -lt "$n" ]; do
    out="$out${out:+, }$STUB_VAL"
    i=$((i + 1))
  done
  echo "$out" ;;
*) echo "| NVIDIA-SMI 000 Driver Version: 000 CUDA Version: 12.4 |" ;;
esac
"""


def _gpu_argv(tmp: Path, rounds: int, *more: str) -> list[str]:
    return [a for a in _argv(tmp, rounds, OK_CMD, *more) if a != "--no-gpu"]


def test_gpu_path_records_header_and_samples_with_a_stub_smi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_smi(tmp_path, STUB_SMI)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("STUB_VAL", "7")
    assert gc.main(_gpu_argv(tmp_path, 3)) == 0
    rows = _rows(tmp_path)
    assert all(r["gpu_sm_mhz"] == 7.0 for r in rows)
    assert all(r["gpu_throttle"] == "7" for r in rows)
    header = json.loads((tmp_path / "t.header.json").read_text())
    assert header["gpu"]["static"]["uuid"] == "7"
    assert header["gpu"]["cuda_driver_supports"] == "12.4"
    assert header["gpu"]["mps"]["daemon_running"] is False
    assert gc.verify_trace(tmp_path / "t.jsonl", 3) == []


def test_resume_refuses_a_different_gpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_smi(tmp_path, STUB_SMI)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("STUB_VAL", "7")
    assert gc.main(_gpu_argv(tmp_path, 2)) == 0
    monkeypatch.setenv("STUB_VAL", "8")
    assert gc.main(_gpu_argv(tmp_path, 4)) == gc.USAGE_EXIT
    assert gc.main(_gpu_argv(tmp_path, 4, "--allow-new-host")) == 0


def test_plan_is_empty_without_a_healthy_matmul_or_sgemm() -> None:
    pair_s: dict[str, float] = {
        "sgemm": 10.0,
        "membound": 10.0,
        "attention": 10.0,
    }
    plan, notes = gc.plan_pairs(pair_s, 14400)
    assert not any(plan.values())
    assert any("matmul" in n for n in notes)


def test_plan_command_fails_when_matmul_is_not_healthy(tmp_path: Path) -> None:
    for name in ("sgemm", "membound"):
        path = tmp_path / f"{name}.health.json"
        path.write_text(
            json.dumps({"workload": name, "ok": True, "pair_s": 5.0})
        )
    argv = ["plan", "--budget-s", "14400", *map(str, tmp_path.glob("*.json"))]
    assert gc.main(argv) == gc.HEALTH_EXIT


def test_missing_final_newline_is_a_torn_tail_and_is_repaired(
    tmp_path: Path,
) -> None:
    assert gc.main(_argv(tmp_path, 3)) == 0
    path = tmp_path / "t.jsonl"
    path.write_text(path.read_text().rstrip("\n"))
    assert gc.main(_argv(tmp_path, 5)) == 0
    assert gc.verify_trace(path, 5) == []


def test_fewer_rounds_than_on_disk_neither_crashes_nor_changes_the_file(
    tmp_path: Path,
) -> None:
    assert gc.main(_argv(tmp_path, 6)) == 0
    path = tmp_path / "t.jsonl"
    before = path.read_text()
    assert gc.main(_argv(tmp_path, 4, OK_CMD, "--health-only")) == 0
    assert path.read_text() == before
    assert gc.main(_argv(tmp_path, 4)) == 0
    assert path.read_text() == before


def test_second_recorder_is_refused_and_the_first_trace_verifies(
    tmp_path: Path,
) -> None:
    code = "import time;time.sleep(0.05);print('PERFPROOF {\"value\": 0.01}')"
    slow = shlex.join([sys.executable, "-c", code])
    argv = _argv(tmp_path, 30, slow)
    first = subprocess.Popen(
        [sys.executable, gc.__file__, *argv],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        jsonl = tmp_path / "t.jsonl"
        while not (jsonl.exists() and jsonl.stat().st_size):
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert gc.main(argv) == gc.USAGE_EXIT
        assert gc.main([*argv, "--health-only"]) == gc.USAGE_EXIT
        assert first.wait(timeout=60) == 0
    finally:
        if first.poll() is None:
            first.kill()
    assert gc.verify_trace(tmp_path / "t.jsonl", 30) == []


def test_lock_is_released_when_the_recorder_ends(tmp_path: Path) -> None:
    assert gc.main(_argv(tmp_path, 2)) == 0
    assert gc.main(_argv(tmp_path, 4)) == 0


def test_one_unknown_sample_field_costs_only_its_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = STUB_SMI.replace(
        "--query-gpu=*)",
        '--query-gpu=*power.draw*) echo "bad field" >&2; exit 2 ;;\n'
        "--query-gpu=*)",
    )
    _stub_smi(tmp_path, body)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("STUB_VAL", "7")
    accepted, rejected = gc.usable_sample_fields()
    assert "power.draw" not in accepted
    assert set(rejected) == {"power.draw"}
    assert gc.main(_gpu_argv(tmp_path, 2)) == 0
    rows = _rows(tmp_path)
    assert all(r["gpu_power_w"] is None for r in rows)
    assert all(r["gpu_sm_mhz"] == 7.0 for r in rows)
    assert all(r["gpu_sample_error"] is None for r in rows)
