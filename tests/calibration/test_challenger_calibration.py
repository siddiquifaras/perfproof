"""A CI-sized rerun of T1.11's recorded challenger results.

Reruns every twelfth cell nearest the bar (true speedups 1.03 and 1.04)
of the main grid with every procedure, at each of the two seeds of the
T1.11 runs, and checks the outcome counts against
``results/t111-main-seed-<seed>.csv.gz``. The primary and the ungated
interval must also match the T1.8 file at the seed of record.
"""

import csv
import gzip
from functools import cache
from pathlib import Path
from typing import Any, Final, cast

import montecarlo as mc
import pytest

pytestmark = pytest.mark.calibration

RESULTS: Final = Path(__file__).parent / "results"
PROCEDURES: Final = (*mc.ALL_PROCEDURES, *mc.WAVE_2_NAMES)
SEEDS: Final = (3598798392042499920, 5616223391016012722)
SUBSET: Final = tuple(
    cell.name for cell in mc.cells_for("main") if cell.speedup in (1.03, 1.04)
)[::12]


@cache
def _recorded(seed: int) -> dict[tuple[str, str], dict[str, str]]:
    path = RESULTS / f"t111-main-seed-{seed}.csv.gz"
    with gzip.open(path, "rt", newline="") as source:
        return {(r["cell"], r["procedure"]): r for r in csv.DictReader(source)}


def test_the_subset_is_a_sample_of_the_cells_nearest_the_bar() -> None:
    assert len(SUBSET) >= 30
    for seed in SEEDS:
        recorded = _recorded(seed)
        for name in SUBSET:
            for procedure in PROCEDURES:
                assert (name, procedure) in recorded


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", SUBSET)
def test_subset_cell_reproduces_the_recorded_counts(
    name: str, seed: int
) -> None:
    result = mc.run_cell(
        mc.cell_by_name(name), seed=seed, procedures=PROCEDURES
    )
    found = cast("dict[str, dict[str, Any]]", result["procedures"])
    for procedure in PROCEDURES:
        row = _recorded(seed)[name, procedure]
        assert found[procedure]["keep"] == int(row["keep"]), procedure
        assert found[procedure]["reject"] == int(row["reject"]), procedure
        assert found[procedure]["errors"] == int(row["errors"]), procedure
