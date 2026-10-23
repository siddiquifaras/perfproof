"""Light checks of the trace collector and the characterisation code."""

import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import collect
import numpy as np
import pytest
from characterise import (
    acf,
    characterise,
    effective_sample_size,
    load_trace,
    loaded_rows,
    two_component,
)
from collect import main as collect_main
from collect import round_orders

from perfproof.bench.runner import round_orders as runner_orders

DATA = Path(__file__).parent / "data"
TRACE = DATA / "linux-membership.jsonl.gz"


def test_collector_orders_match_the_runner() -> None:
    mine = round_orders(20261102, 41)
    theirs = ["AB"[a == "candidate"] for a in runner_orders(20261102, 41)]
    assert mine == theirs


def test_collector_records_paired_rounds(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
    out = tmp_path / "t"
    status = collect_main(
        [
            "--out", str(out),
            "--dir-a", str(tmp_path / "a"),
            "--dir-b", str(tmp_path / "b"),
            "--cmd", f"{sys.executable} -c pass",
            "--rounds", "4",
            "--seed", "7",
            "--label", "test",
            "--target", "noop",
            "--no-wait",
        ]
    )  # fmt: skip
    rows = [
        json.loads(x)
        for x in out.with_suffix(".jsonl").read_text().split("\n")
        if x
    ]
    assert status == 0
    assert len(rows) == 10
    assert [r["pair"] for r in rows if not r["warmup"]] == [
        0, 0, 1, 1, 2, 2, 3, 3
    ]  # fmt: skip
    orders = round_orders(7, 4)
    firsts = [r["arm"] for r in rows if not r["warmup"] and r["pos"] == 0]
    assert firsts == orders
    header = json.loads(out.with_suffix(".header.json").read_text())
    assert header["order_seed"] == 7
    assert "finished_utc" in header


def test_acf_and_ess_of_white_noise() -> None:
    x = np.random.default_rng(1).normal(size=2000)
    assert abs(acf(x, 5)[1]) < 0.1
    assert 0.8 * x.size < effective_sample_size(x) < 1.3 * x.size


def test_characterise_runs_on_a_committed_trace() -> None:
    trace = load_trace(TRACE)
    result = characterise(trace)
    assert result["pairs"] == 1000
    assert result["wall"]["arm_A"]["cv"] > 0
    lo, hi = result["wall"]["d"]["lag1_ci95_block_bootstrap"]
    assert lo < result["wall"]["d"]["lag1"] < hi
    assert set(result["diagnostic_exceedance_on_windows"]) == {
        "30",
        "60",
        "120",
    }
    assert result["instructions_vs_wall"]["sd_d_ratio_wall_over_instr"] > 1
    assert result["changepoints_pair_mean_logtime"]["method"]


def test_loaded_rows_pair_a_loaded_trace_with_its_quiet_twin() -> None:
    result = characterise(load_trace(TRACE))
    quiet = {**result, "label": "mac-x"}
    loaded = {**result, "label": "mac-loaded-x"}
    assert len(loaded_rows([quiet, loaded])) == 1
    assert loaded_rows([quiet]) == []


def _stub_pmset(monkeypatch: pytest.MonkeyPatch, out: str | Exception) -> None:
    def fake(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess([], 0, stdout=out)

    monkeypatch.setattr(collect.subprocess, "run", fake)


AC = "Now drawing from 'AC Power'\n -InternalBattery-0\t100%; charged\n"
BATT = "Now drawing from 'Battery Power'\n -InternalBattery-0\t99%\n"


def test_ac_power_is_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pmset(monkeypatch, AC)
    assert collect.power_source() == "AC Power"
    assert collect.on_ac_power()


def test_battery_is_not_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_pmset(monkeypatch, BATT)
    assert collect.power_source() == "Battery Power"
    assert not collect.on_ac_power()


@pytest.mark.parametrize(
    "out", ["", "garbage", FileNotFoundError(), OSError("x")]
)
def test_unparseable_power_fails_closed(
    monkeypatch: pytest.MonkeyPatch, out: str | Exception
) -> None:
    _stub_pmset(monkeypatch, out)
    assert collect.power_source() is None
    assert not collect.on_ac_power()


def test_battery_during_a_pass_aborts_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(collect.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(collect, "on_ac_power", lambda: False)
    monkeypatch.setattr(collect, "power_source", lambda: "Battery Power")
    monkeypatch.setattr(collect, "host_state", lambda: (100, "Battery Power"))
    monkeypatch.setattr(collect, "busy_processes", list)
    for arm in "AB":
        (tmp_path / arm).mkdir()
    out = tmp_path / "t"
    argv = [
        "--out", str(out), "--dir-a", str(tmp_path / "A"),
        "--dir-b", str(tmp_path / "B"), "--cmd", "true",
        "--rounds", "3", "--seed", "1", "--label", "x",
        "--target", "t", "--no-wait", "--guard", "quiet", "--no-time",
    ]  # fmt: skip
    assert collect_main(argv) == collect.ABORT_EXIT
    header = json.loads(out.with_suffix(".header.json").read_text())
    assert "not on AC power" in header["aborted"]
    assert header["power_source_at_start"] == "Battery Power"


def test_ess_sees_negative_dependence() -> None:
    rng = np.random.default_rng(5)
    ratios: list[float] = []
    for _ in range(50):
        e = rng.normal(size=1000)
        x = np.empty(1000)
        x[0] = e[0]
        for i in range(1, 1000):
            x[i] = -0.5 * x[i - 1] + e[i]
        ratios.append(effective_sample_size(x) / 1000)
    # The true ratio is 3 for phi = -0.5; Geyer's estimator sees it.
    assert np.mean(ratios) > 2.0


def test_bimodality_coefficient_matches_sarle() -> None:
    rng = np.random.default_rng(6)
    normal = two_component(rng.normal(size=4000))
    uniform = two_component(rng.uniform(size=4000))
    modes = two_component(
        np.concatenate([rng.normal(-3, 1, 2000), rng.normal(3, 1, 2000)])
    )
    assert abs(normal["bimodality_coefficient"] - 1 / 3) < 0.03
    assert abs(uniform["bimodality_coefficient"] - 5 / 9) < 0.03
    assert modes["bimodality_coefficient"] > 5 / 9
    assert modes["delta_bic"] > 100


def test_collector_survives_a_chatty_target(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "big.py").write_text(
            "import sys\nsys.stdout.write('x' * 300000)\n"
            "sys.stderr.write('y' * 300000)\n"
        )
    status = collect_main(
        [
            "--out", str(tmp_path / "t"),
            "--dir-a", str(tmp_path / "a"),
            "--dir-b", str(tmp_path / "b"),
            "--cmd", f"{sys.executable} big.py",
            "--rounds", "2",
            "--seed", "1",
            "--label", "chatty",
            "--target", "big",
            "--no-wait",
        ]
    )  # fmt: skip
    assert status == 0


def test_ess_is_nan_when_the_autocorrelation_time_is_not_positive() -> None:
    x = np.tile([1.0, -1.0], 500)  # lag-1 autocorrelation of -1
    assert np.isnan(effective_sample_size(x))


@pytest.mark.parametrize("wrapped", [False, True])
def test_timeout_kills_the_whole_process_group(
    wrapped: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if wrapped and not (
        platform.system() == "Darwin" and Path(collect.TIME_BIN).exists()
    ):
        pytest.skip("the wrapper is macOS /usr/bin/time -l")
    monkeypatch.setattr(collect, "TIMEOUT", 1.5)
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="ran over"):
        collect.execute(
            [sys.executable, "-c", "import time; time.sleep(20)"],
            tmp_path,
            use_time=wrapped,
        )
    assert time.monotonic() - start < 8
