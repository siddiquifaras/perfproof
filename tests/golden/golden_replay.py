"""Shadow replay of recorded golden-set comparisons through the methods.

A golden comparison is one real ``perfproof compare`` run to its
maximum rounds (``studies/golden/campaign.py``), recorded as one CSV row
per execution. This module loads those rows into per-cell arrays and
decides every comparison with the three candidate methods on the same
data, using `perfproof.stats.sequential.run_looks`, the code a verdict
is made by (``calibrated=True``, no time budget, as the calibration
harness does).

Methods
-------
``gated``
    The T1.8 primary: batch means of 6 with the diagnostic gate.
``hac_lf``
    The T1.11 interval on every schedule.
``split``
    `perfproof.stats.sequential.procedure_for_schedule`, what
    ``perfproof compare`` decides with: ``hac_lf`` except on the
    ``gpu_kernels`` schedule.
"""

import csv
import gzip
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import numpy as np
from numpy.typing import NDArray

from perfproof.schema import LookSchedule
from perfproof.stats.sequential import procedure_for_schedule, run_looks

FloatArray = NDArray[np.float64]
Method = Literal["gated", "hac_lf", "split"]
METHODS: Final[tuple[Method, ...]] = ("gated", "hac_lf", "split")
MIN_EFFECT: Final = 0.03

SCHEDULES: Final[dict[str, tuple[LookSchedule, int]]] = {
    "python_native": (LookSchedule(n0=10, k=10, max_looks=6), 60),
    "web_agentic": (LookSchedule(n0=10, k=10, max_looks=3), 30),
    "llm_serving": (LookSchedule(n0=10, k=6, max_looks=3), 22),
    "gpu_kernels": (LookSchedule(n0=20, k=20, max_looks=6), 120),
}
"""Schedule name to (looks, max rounds); the defaults of "Stopping" in
``docs/method.md``."""


@dataclass(frozen=True, slots=True)
class Cell:
    """The recorded comparisons of one workload at one nominal speedup.

    Attributes
    ----------
    workload, label : str
        The workload and the cell label (``"1.03"``).
    reps : numpy.ndarray of int, shape (R,)
        Replicate numbers, ascending.
    base, cand : numpy.ndarray of float, shape (R, n)
        Reported seconds of the baseline (slowed) and candidate arm per
        round, in the order run.
    first : numpy.ndarray of bool, shape (R, n)
        True where the candidate ran first in the round.
    spin_ratio : numpy.ndarray of float, shape (R, n)
        ``spin_s / work_s`` of the baseline execution of each round: the
        injected slowdown actually achieved.
    """

    workload: str
    label: str
    reps: NDArray[np.int64]
    base: FloatArray
    cand: FloatArray
    first: NDArray[np.bool_]
    spin_ratio: FloatArray

    @property
    def nominal(self) -> float:
        """The nominal true speedup, ``1 + f``."""
        return float(self.label)

    @property
    def rounds(self) -> int:
        """Rounds recorded per comparison."""
        return int(self.base.shape[1])


def open_text(path: Path) -> Iterator[dict[str, str]]:
    """Yield the rows of a CSV, gzipped or not."""
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="") as source:
        yield from csv.DictReader(source)


def load_cells(path: Path) -> dict[str, Cell]:
    """Read one workload's CSV into cells keyed by label.

    Parameters
    ----------
    path : pathlib.Path
        ``<workload>.csv`` or ``.csv.gz`` of the campaign.

    Returns
    -------
    dict of str to Cell
        One `Cell` per label; replicates with fewer rounds than the
        cell's longest are rejected.

    Raises
    ------
    ValueError
        If a replicate's executions do not form complete rounds.
    """
    runs: dict[tuple[str, int], dict[int, dict[str, tuple[int, float, float]]]]
    runs = {}
    workload = ""
    for row in open_text(path):
        workload = row["workload"]
        key = (row["cell"], int(row["rep"]))
        spin = float(row["spin_s"]) / float(row["work_s"])
        runs.setdefault(key, {}).setdefault(int(row["round"]), {})[
            row["arm"]
        ] = (int(row["pos"]), float(row["reported_s"]), spin)
    cells: dict[str, Cell] = {}
    for label in sorted({k[0] for k in runs}):
        reps = sorted(r for lab, r in runs if lab == label)
        n = max(len(runs[(label, r)]) for r in reps)
        base = np.empty((len(reps), n))
        cand = np.empty((len(reps), n))
        first = np.empty((len(reps), n), dtype=np.bool_)
        spin = np.empty((len(reps), n))
        for i, r in enumerate(reps):
            rounds = runs[(label, r)]
            if sorted(rounds) != list(range(n)) or any(
                set(v) != {"A", "B"} for v in rounds.values()
            ):
                msg = f"{path.name} {label}/{r}: incomplete rounds"
                raise ValueError(msg)
            for j in range(n):
                a, b = rounds[j]["A"], rounds[j]["B"]
                base[i, j], cand[i, j], spin[i, j] = a[1], b[1], a[2]
                first[i, j] = b[0] == 0
        cells[label] = Cell(
            workload,
            label,
            np.array(reps, dtype=np.int64),
            base,
            cand,
            first,
            spin,
        )
    return cells


@dataclass(frozen=True, slots=True)
class Replay:
    """Every comparison of a cell decided by one method on one schedule.

    Attributes
    ----------
    outcome : numpy.ndarray of str, shape (R,)
        ``KEEP``, ``REJECT`` or ``INCONCLUSIVE``.
    rounds_used : numpy.ndarray of int, shape (R,)
        Rounds at the last look taken.
    looks : numpy.ndarray of int, shape (R,)
        Looks taken.
    withheld : numpy.ndarray of str, shape (R,)
        The diagnostic that withheld the last look, or ``""``.
    seconds : float
        Statistics-code seconds per comparison, each decided alone as a
        live comparison would be (``R = 1``), wall clock, this host.
    """

    outcome: NDArray[np.str_]
    rounds_used: NDArray[np.int64]
    looks: NDArray[np.int64]
    withheld: NDArray[np.str_]
    seconds: float


def is_outcome(result: Replay, name: str) -> NDArray[np.bool_]:
    """Return which comparisons of ``result`` ended as ``name``."""
    return np.array([o == name for o in result.outcome.tolist()], dtype=bool)


def procedure_of(method: Method, schedule: str) -> str:
    """Return the interval procedure ``method`` uses on ``schedule``."""
    if method == "gated":
        return "t_log_ratio_batch_means"
    if method == "hac_lf":
        return "hac_lf"
    return procedure_for_schedule(*SCHEDULES[schedule])


def replay(cell: Cell, schedule: str, method: Method) -> Replay:
    """Decide every comparison of ``cell`` with ``method``.

    Parameters
    ----------
    cell : Cell
        Recorded comparisons; at least the schedule's rounds each.
    schedule : str
        A key of `SCHEDULES`.
    method : {"gated", "hac_lf", "split"}
        The method.

    Returns
    -------
    Replay
        Outcomes and costs. Each comparison is decided by its own
        ``run_looks`` call (``R = 1``), so ``seconds`` is the cost of a
        live verdict, and the decision is what a real comparison on
        these rounds would be.

    Raises
    ------
    ValueError
        If the cell has fewer rounds than the schedule's maximum.
    """
    looks, max_rounds = SCHEDULES[schedule]
    if cell.rounds < max_rounds:
        msg = f"{cell.workload}/{cell.label}: {cell.rounds} < {max_rounds}"
        raise ValueError(msg)
    procedure = procedure_of(method, schedule)
    count = cell.base.shape[0]
    outcome = np.empty(count, dtype="<U12")
    used = np.empty(count, dtype=np.int64)
    n_looks = np.empty(count, dtype=np.int64)
    withheld = np.empty(count, dtype="<U24")
    spent = 0.0
    for i in range(count):
        b = cell.base[i : i + 1, :max_rounds]
        c = cell.cand[i : i + 1, :max_rounds]
        f = cell.first[i : i + 1, :max_rounds]
        started = time.perf_counter()
        result = run_looks(
            b,
            c,
            direction="lower_is_better",
            schedule=looks,
            mode="one_host",
            min_effect=MIN_EFFECT,
            calibrated=True,
            resolution=None,
            exhausted=np.ones(1, dtype=np.bool_),
            procedure=procedure,  # pyright: ignore[reportArgumentType]
            candidate_first=f,
        )
        spent += time.perf_counter() - started
        outcome[i] = str(result.outcome[0])
        used[i] = int(result.rounds_used[0])
        n_looks[i] = int(result.looks[0])
        last = int(result.looks[0]) - 1
        withheld[i] = str(result.withheld[0, last]) if last >= 0 else ""
    return Replay(outcome, used, n_looks, withheld, spent / count)
