"""The CI-sized subset of the recorded calibration.

Reruns every cell nearest the bar, 1.03 for false KEEP and 1.04 for
false REJECT, of every schedule, scenario and host mode, for the t
primary and the batch-means fallback in blocks of `mc.BATCH_BLOCK`
rounds. At the seed of record every count must equal the one in
``tests/calibration/results/`` (the counts ``docs/method.md`` tabulates)
and every cell must get the verdict recorded there; at any other seed
the fallback must pass the acceptance rule in every cell, as the audit
will ask of it.
"""

import csv
from functools import cache
from pathlib import Path
from typing import Any, Final, cast

import montecarlo as mc
import pytest

pytestmark = pytest.mark.calibration

RESULTS: Final = Path(__file__).parent / "results"
SUBSET: Final = tuple(
    cell.name for cell in mc.all_cells() if cell.speedup in (1.03, 1.04)
)


@cache
def _recorded(seed: int) -> dict[tuple[str, str], dict[str, str]]:
    path = RESULTS / f"seed-{seed}.csv"
    with path.open(newline="") as source:
        return {(r["cell"], r["procedure"]): r for r in csv.DictReader(source)}


def test_the_subset_is_the_cells_nearest_the_bar() -> None:
    assert len(SUBSET) == len(mc.SCHEDULES) * (11 + 9) * 2
    recorded = _recorded(mc.DEFAULT_SEED)
    for name in SUBSET:
        for procedure in mc.PROCEDURES:
            assert (name, procedure) in recorded


@pytest.mark.parametrize("name", SUBSET)
def test_subset_cell_reproduces_and_is_judged_as_recorded(name: str) -> None:
    seed = mc.calibration_seed()
    result = mc.run_cell(mc.cell_by_name(name), seed=seed)
    found = cast("dict[str, dict[str, Any]]", result["procedures"])
    for procedure in mc.PROCEDURES:
        summary = found[procedure]
        verdict = mc.passes(summary["errors"], mc.REPLICATIONS)
        assert verdict == (summary["cp_upper"] <= mc.LIMIT)
        if seed == mc.DEFAULT_SEED:
            row = _recorded(seed)[name, procedure]
            assert summary["keep"] == int(row["keep"]), procedure
            assert summary["reject"] == int(row["reject"]), procedure
            assert summary["inconclusive"] == int(row["inconclusive"])
            assert verdict == bool(int(row["passes"])), procedure
            assert summary["rounds_p95"] == int(row["rounds_p95"])
        elif procedure == mc.FALLBACK:
            assert verdict, (name, summary["errors"])
