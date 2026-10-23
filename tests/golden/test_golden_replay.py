"""Regression test: recorded golden runs replay to the same verdicts.

``data/subset.npz`` holds real recorded rounds (see
``build_subset.py``) and ``data/expected.json`` the verdict of every
method on every schedule when the subset was built. Any change to the
statistics that moves a verdict fails here and needs an explanation.
"""

import json
from pathlib import Path

import golden_replay as gr
import numpy as np
import pytest

DATA = Path(__file__).parent / "data"
pytestmark = pytest.mark.skipif(
    not (DATA / "expected.json").exists(), reason="no subset built yet"
)


def _expected() -> dict[str, dict[str, list[str]]]:
    return json.loads((DATA / "expected.json").read_text())


def _cases() -> list[tuple[str, str]]:
    if not (DATA / "expected.json").exists():
        return []
    return [
        (cell, key) for cell, by_key in _expected().items() for key in by_key
    ]


@pytest.mark.parametrize(("cell", "key"), _cases())
def test_replay_matches_recorded_verdicts(cell: str, key: str) -> None:
    arrays = np.load(DATA / "subset.npz", allow_pickle=False)
    workload, label = cell.split("/")
    base = np.asarray(arrays[f"{cell}/base"], dtype=np.float64)
    sub = gr.Cell(
        workload,
        label,
        np.arange(base.shape[0], dtype=np.int64),
        base,
        np.asarray(arrays[f"{cell}/cand"], dtype=np.float64),
        np.asarray(arrays[f"{cell}/first"], dtype=np.bool_),
        np.zeros_like(base),
    )
    schedule, method = key.split("/")
    assert method in gr.METHODS
    got = gr.replay(sub, schedule, method)  # pyright: ignore[reportArgumentType]
    assert got.outcome.tolist() == _expected()[cell][key]


def test_subset_covers_every_schedule_method_and_a_power_cell() -> None:
    expected = _expected()
    keys = {key for by_key in expected.values() for key in by_key}
    for schedule in gr.SCHEDULES:
        for method in gr.METHODS:
            assert f"{schedule}/{method}" in keys
    assert any(cell.endswith("/1.10") for cell in expected)
    assert any(cell.endswith("/1.03") for cell in expected)
