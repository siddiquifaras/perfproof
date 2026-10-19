"""The stub's cap against the real fix, on every Python example.

T3.4's bar: where a function's real fix is known, the cap measured by
replaying that function is at least the ratio the fix gets, so it holds
as an upper bound there. Both are timed on whatever the machine is
doing, so this is an integration test with a margin of its own: the
bound (the upper end of the cap's interval) must not fall below the
fix's ratio. Where the timed region is only the function, no bound is
given, and the test says so.
"""

import os
import shutil
import sys
from pathlib import Path

import pytest

from perfproof.bench.estimates import geometric_mean
from perfproof.bench.runner import Runner
from perfproof.bench.stub import stub_function
from perfproof.schema import BenchmarkSpec

pytestmark = pytest.mark.integration

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
TARGETS = {
    "rolling": "rolling:rolling_sum",
    "membership": "orders:held_orders",
    "growth": "pairs:count_pairs",
    "settings": "writer:write_block",
}
WITHHELD = ("rolling", "membership")
"""Examples whose timed region is the target function alone."""
STUB_ROUNDS = 5
FIX_ROUNDS = 10


def tree(tmp_path: Path, example: str, *, fix: bool) -> Path:
    root = tmp_path / f"{example}-{'fix' if fix else 'base'}"
    shutil.copytree(
        EXAMPLES / example,
        root,
        ignore=shutil.ignore_patterns("candidates", "__pycache__", "output"),
    )
    if fix:
        for source in (EXAMPLES / example / "candidates" / "fix").iterdir():
            shutil.copy2(source, root / source.name)
    return root


@pytest.mark.parametrize("example", sorted(TARGETS))
def test_the_cap_is_at_least_the_real_fixs_ratio(
    tmp_path: Path, example: str
) -> None:
    spec = BenchmarkSpec.model_validate_json(
        (EXAMPLES / example / "spec.json").read_text()
    )
    spec = spec.model_copy(
        update={"command": (sys.executable, *spec.command[1:])}
    )
    baseline = tree(tmp_path, example, fix=False)
    fixed = tree(tmp_path, example, fix=True)
    result = stub_function(
        spec,
        baseline,
        TARGETS[example],
        rounds=STUB_ROUNDS,
        work_dir=tmp_path / "stub",
        seed=1,
        process_env=os.environ,
    )
    runner = Runner(
        spec,
        {"baseline": baseline, "candidate": fixed},
        tmp_path / "fix-logs",
        order_seed=7,
    )
    runner.warmup()
    ratios: list[float] = []
    for r in range(FIX_ROUNDS):
        done = runner.run_round(r)
        values = {e.arm: e.value for e in done.executions}
        ratios.append(values["baseline"] / values["candidate"])
    real = geometric_mean(ratios)
    assert result.replayed_per_run > 0
    assert result.real_per_run == 0
    assert real.value > 1.5, "the example's fix should be a real one"
    if example in WITHHELD:
        # The timed region is only the function, so what is left cannot
        # be told from the hook's own cost: no bound is claimed.
        assert result.cap is None
        assert result.cap_bound is None
        assert result.cap_withheld == "uninformative"
    else:
        assert result.cap_bound is not None, result.notes
        assert result.cap_bound >= real.value
