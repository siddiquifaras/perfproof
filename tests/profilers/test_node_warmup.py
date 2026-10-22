"""The numbers in docs/method.md "Warm-up (Node)", from the raw output.

``tests/fixtures/node/warmup`` holds the driver and what it printed
(see SOURCE.md there); nothing here is measured again.
"""

import json
import statistics
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parents[1] / "fixtures" / "node" / "warmup"
BATCH = 500


def curve() -> list[list[float]]:
    lines = (DIR / "curve.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def medians() -> list[float]:
    rows = curve()
    return [
        statistics.median(row[b] for row in rows) for b in range(len(rows[0]))
    ]


def steady() -> float:
    return statistics.median(medians()[40:])


def test_the_curve_is_12_processes_of_60_batches() -> None:
    rows = curve()
    assert len(rows) == 12
    assert {len(row) for row in rows} == {60}


@pytest.mark.parametrize(
    ("batch", "micros", "ratio"),
    [
        (0, 131.6, 7.54),
        (1, 39.2, 2.25),
        (11, 36.2, 2.07),
        (12, 18.3, 1.05),
        (18, 18.0, 1.03),
    ],
)
def test_the_first_table(batch: int, micros: float, ratio: float) -> None:
    median = medians()[batch]
    assert round(median, 1) == micros
    assert round(median / steady(), 2) == pytest.approx(ratio, abs=0.006)
    assert round(steady(), 1) == 17.5


def test_batches_after_the_drop_stay_noisy() -> None:
    later = [m / steady() for m in medians()[12:]]
    assert len(later) == 48
    assert sum(1 for r in later if r > 1.1) == 21
    assert round(max(later), 2) == 1.46


@pytest.mark.parametrize(
    ("warm", "mean", "cv"),
    [
        (0, 35.8, 6.2),
        (4, 27.8, 4.9),
        (8, 23.9, 4.2),
        (12, 21.6, 7.4),
        (20, 21.7, 9.0),
        (30, 21.1, 9.1),
    ],
)
def test_the_second_table(warm: int, mean: float, cv: float) -> None:
    # `warm` is in batches of 500; the table names iterations.
    window = [statistics.mean(r[warm : warm + 20]) for r in curve()]
    assert round(statistics.mean(window), 1) == mean
    spread = statistics.pstdev(window) / statistics.mean(window) * 100
    assert round(spread, 1) == cv


@pytest.mark.parametrize(
    ("name", "mean", "cv"),
    [("bench-warm-0.txt", 36.3, 7.1), ("bench-warm-12000.txt", 21.4, 8.8)],
)
def test_the_reported_values_of_15_processes(
    name: str, mean: float, cv: float
) -> None:
    lines = (DIR / name).read_text(encoding="utf-8").splitlines()
    values = [
        json.loads(line.split("PERFPROOF ", 1)[1])["value"] * 1e6
        for line in lines
    ]
    assert len(values) == 15
    assert round(statistics.mean(values), 1) == mean
    assert (
        round(statistics.pstdev(values) / statistics.mean(values) * 100, 1)
        == cv
    )


def test_raw_batches_after_the_drop_are_noisier_than_the_medians() -> None:
    later = [v / steady() for row in curve() for v in row[12:]]
    assert len(later) == 576
    assert sum(1 for r in later if r > 1.1) == 301
    assert round(max(later), 2) == 2.79
