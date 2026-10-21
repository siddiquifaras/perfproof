"""The CI-sized subset of the recorded calibration.

Reruns every cell nearest the bar, 1.03 for false KEEP and 1.04 for
false REJECT, of every schedule, scenario (the development scenarios
and the exposed ones) and host mode, for the primary, batch means in
blocks of `mc.BATCH_BLOCK` rounds with the diagnostic gate, and for the
same interval without the gate. At the seed of record every count must
equal the one in ``tests/calibration/results/`` (the counts
``docs/method.md`` tabulates) and every cell must get the verdict
recorded there; at any other seed the primary must pass the acceptance
rule in every cell, as the audit will ask of it.
"""

import csv
from functools import cache
from pathlib import Path
from typing import Any, Final, cast

import montecarlo as mc
import pytest
from exposed import EXPOSED

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
    scenario_modes = len(mc.scenarios("one_host")) + len(
        mc.scenarios("cross_host")
    )
    assert len(SUBSET) == len(mc.SCHEDULES) * scenario_modes * 2
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
        elif procedure == mc.PRIMARY:
            assert verdict, (name, summary["errors"])


FRESH_SEED: Final = 3078527063959977686
"""The second complete run; both seeds were drawn before either ran."""


@pytest.mark.parametrize("seed", [mc.DEFAULT_SEED, FRESH_SEED])
def test_recorded_primary_passes_every_cell(seed: int) -> None:
    rows = _recorded(seed)
    names = [cell.name for cell in mc.all_cells()]
    assert len(names) == 1344
    for name in names:
        row = rows[name, mc.PRIMARY]
        column = "keep" if mc.cell_by_name(name).error == "KEEP" else "reject"
        errors = int(row[column])
        assert int(row["errors"]) == errors
        assert int(row["replications"]) == mc.REPLICATIONS
        assert mc.passes(errors, mc.REPLICATIONS), name
        assert row["passes"] == "1", name


@pytest.mark.parametrize("seed", [mc.DEFAULT_SEED, FRESH_SEED])
def test_recorded_ungated_fails_only_in_the_exposed_scenarios(
    seed: int,
) -> None:
    rows = _recorded(seed)
    failing = [
        name
        for name, procedure in rows
        if procedure == mc.UNGATED and rows[name, procedure]["passes"] != "1"
    ]
    assert failing
    assert {name.split("/")[1] for name in failing} <= set(EXPOSED)
