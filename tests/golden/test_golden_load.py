"""Tests of how recorded executions become replay input.

These guard the arm mapping, the order flag, the incomplete-round
check and the join of perfproof's samples to the wrapper's log, none of
which the stored-array replay test exercises.
"""

import csv
import importlib.util
from pathlib import Path
from types import ModuleType

import golden_replay as gr
import numpy as np
import pytest
from golden_stats import BAR

CAMPAIGN = (
    Path(__file__).resolve().parents[2] / "studies" / "golden" / "campaign.py"
)
FIELDS = (
    "workload", "cell", "rep", "round", "pos", "arm", "reported_s", "work_s",
    "spin_s",
)  # fmt: skip


def _campaign() -> ModuleType:
    spec = importlib.util.spec_from_file_location("golden_campaign", CAMPAIGN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, drop: tuple[int, str] | None = None) -> None:
    # Round j: B first when j is even. A reports 2.0 (work 1.0 + spin
    # 1.0), B reports 1.0 (work 1.0, no spin).
    with path.open("w", newline="") as sink:
        writer = csv.writer(sink, lineterminator="\n")
        writer.writerow(FIELDS)
        for j in range(4):
            order = ("B", "A") if j % 2 == 0 else ("A", "B")
            for pos, arm in enumerate(order):
                if drop == (j, arm):
                    continue
                value, spin = (2.0, 1.0) if arm == "A" else (1.0, 0.0)
                writer.writerow(
                    ["w", "1.03", 7, j, pos, arm, value, 1.0, spin]
                )


def test_load_cells_maps_arms_order_and_spin(tmp_path: Path) -> None:
    path = tmp_path / "w.csv"
    _write(path)
    cell = gr.load_cells(path)["1.03"]
    assert cell.reps.tolist() == [7]
    assert cell.base.tolist() == [[2.0] * 4]
    assert cell.cand.tolist() == [[1.0] * 4]
    assert cell.first.tolist() == [[True, False, True, False]]
    assert cell.spin_ratio.tolist() == [[1.0] * 4]
    assert cell.nominal == pytest.approx(1.03)
    assert cell.nominal <= BAR + 1e-12


def test_load_cells_rejects_an_incomplete_round(tmp_path: Path) -> None:
    path = tmp_path / "w.csv"
    _write(path, drop=(2, "B"))
    with pytest.raises(ValueError, match="incomplete rounds"):
        gr.load_cells(path)


def test_join_skips_unsampled_executions_and_pairs_in_order() -> None:
    campaign = _campaign()
    # Two warm-up executions, then four sampled ones, then a stray one.
    log = [
        {"arm": "A", "reported_s": 0.5},
        {"arm": "B", "reported_s": 0.5},
        {"arm": "B", "reported_s": 1.0},
        {"arm": "A", "reported_s": 2.0},
        {"arm": "A", "reported_s": 2.2},
        {"arm": "B", "reported_s": 1.1},
        {"arm": "A", "reported_s": 9.0},
    ]
    samples = {
        "value": np.array([1.0, 2.0, 2.2, 1.1]),
        "candidate": np.array([1, 0, 0, 1]),
    }
    pairs = campaign.join(samples, log)
    assert [i for i, _ in pairs] == [0, 1, 2, 3]
    assert [e["reported_s"] for _, e in pairs] == [1.0, 2.0, 2.2, 1.1]


def test_join_fails_when_a_sample_has_no_log_line() -> None:
    campaign = _campaign()
    samples = {"value": np.array([3.0]), "candidate": np.array([0])}
    with pytest.raises(RuntimeError, match="not in the wrapper log"):
        campaign.join(samples, [{"arm": "A", "reported_s": 2.0}])
