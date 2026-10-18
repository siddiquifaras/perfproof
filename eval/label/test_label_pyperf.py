"""pyperf's side of the analysis (needs eval/label/requirements.txt)."""

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyperf")

import analyse
import pyperf_stats


def test_compare_writes_pyperf_files_and_tests(tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    a = (1.0 + rng.normal(0, 0.01, 200)).tolist()
    b = (0.9 + rng.normal(0, 0.01, 200)).tolist()
    got = pyperf_stats.compare(a, b, tmp_path, "t.ab")
    assert got.significant
    assert got.ratio == pytest.approx(np.mean(a) / np.mean(b))
    assert "faster" in got.compare_to
    for name in got.files.values():
        data = json.loads((tmp_path / name).read_text())
        values = data["benchmarks"][0]["runs"][0]["values"]
        assert len(values) == 200


def test_same_values_are_not_significant(tmp_path: Path) -> None:
    a = (1.0 + np.random.default_rng(2).normal(0, 0.01, 200)).tolist()
    got = pyperf_stats.compare(a, a, tmp_path, "same")
    assert not got.significant
    assert got.ratio == 1.0


def _pairs(a: list[float], b: list[float]) -> list[dict[str, object]]:
    return [
        {
            "a": {"wall_s": x, "cpu_s": x, "instructions": None},
            "b": {"wall_s": y, "cpu_s": y, "instructions": None},
        }
        for x, y in zip(a, b, strict=True)
    ]


def test_analyse_labels_a_clear_improvement(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    a = np.exp(rng.normal(0.0, 0.02, 200)).tolist()
    b = (np.exp(rng.normal(0.0, 0.02, 200)) / 1.2).tolist()
    got = analyse.analyse(_pairs(a, b), 4, tmp_path, "imp")
    assert got["machine_label"]["label"] == "improvement"
    assert got["instructions"]["interval"] is None


def test_analyse_of_identical_arms_is_not_an_improvement(
    tmp_path: Path,
) -> None:
    rng = np.random.default_rng(5)
    a = np.exp(rng.normal(0.0, 0.05, 200)).tolist()
    b = np.exp(rng.normal(0.0, 0.05, 200)).tolist()
    got = analyse.analyse(_pairs(a, b), 6, tmp_path, "aa")
    assert got["machine_label"]["label"] == "below-bar"
