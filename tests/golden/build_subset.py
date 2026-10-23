"""Build the committed regression subset from the full golden data.

Usage::

    uv run python tests/golden/build_subset.py \\
        --data eval/golden/data --out tests/golden/data

Takes the first ``COUNTS[cell]`` comparisons of every workload's cells
(`golden_replay.load_cells`), stores their recorded rounds in
``subset.npz`` and the decision of every method on every schedule in
``expected.json``. ``test_golden_replay.py`` replays the rounds and
requires the same decisions, so a change to the statistics that moves
any of them is seen by ``/verify``. The expected decisions are what the
code at the commit that built the subset decided, not a claim of truth;
rebuild with this script when a change is meant to move them, and say
why in the PR.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import golden_replay as gr
import numpy as np

COUNTS: Final = {"1.00": 20, "1.03": 20, "1.05": 10, "1.10": 10}
"""Comparisons kept per cell."""


def run(argv: Sequence[str]) -> int:
    """Write the subset; return the exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, Any] = {}
    expected: dict[str, dict[str, list[str]]] = {}
    for path in sorted(args.data.glob("*.csv*")):
        for label, cell in gr.load_cells(path).items():
            if label not in COUNTS:
                continue
            keep = COUNTS[label]
            sub = gr.Cell(
                cell.workload,
                label,
                cell.reps[:keep],
                cell.base[:keep],
                cell.cand[:keep],
                cell.first[:keep],
                cell.spin_ratio[:keep],
            )
            name = f"{cell.workload}/{label}"
            arrays[f"{name}/base"] = sub.base
            arrays[f"{name}/cand"] = sub.cand
            arrays[f"{name}/first"] = sub.first
            expected[name] = {}
            for schedule, (_, max_rounds) in gr.SCHEDULES.items():
                if sub.rounds < max_rounds:
                    continue
                for method in gr.METHODS:
                    got = gr.replay(sub, schedule, method)
                    expected[name][f"{schedule}/{method}"] = (
                        got.outcome.tolist()
                    )
    np.savez_compressed(args.out / "subset.npz", **arrays)
    (args.out / "expected.json").write_text(
        json.dumps(expected, indent=1, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
