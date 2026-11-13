"""Tests of the golden-set tools: bounds, McNemar, the wrapper."""

import functools
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from golden_stats import binom_cdf, cp_upper, mcnemar

WRAP = Path(__file__).resolve().parents[2] / "studies" / "golden" / "wrap.py"


@functools.cache
def perf_unusable() -> bool:
    """Whether the wrapper's ``perf stat`` cannot run here.

    On Linux the wrapper exits non-zero when ``perf stat`` does, and
    ``perf`` being on the PATH does not mean it may count: a CI runner
    has it with ``perf_event_paranoid`` at 4. The probe uses the
    wrapper's own events.
    """
    if sys.platform != "linux":
        return False
    if shutil.which("perf") is None:
        return True
    try:
        done = subprocess.run(
            ["perf", "stat", "-e", "instructions:u", "--", "true"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return done.returncode != 0


def test_cp_upper_zero_errors_matches_closed_form() -> None:
    assert cp_upper(0, 200) == pytest.approx(1 - 0.05 ** (1 / 200), abs=1e-9)
    assert cp_upper(0, 200) < 0.015


def test_cp_upper_known_value() -> None:
    # scipy.stats.beta.ppf(0.95, 6, 95), the Clopper-Pearson bound.
    assert cp_upper(5, 100) == pytest.approx(0.1022533776, abs=1e-8)
    assert cp_upper(10, 10) == 1.0


def test_cp_upper_rejects_bad_counts() -> None:
    with pytest.raises(ValueError, match="errors"):
        cp_upper(11, 10)
    with pytest.raises(ValueError, match="errors"):
        cp_upper(0, 0)


def test_binom_cdf_edges() -> None:
    assert binom_cdf(-1, 10, 0.3) == 0.0
    assert binom_cdf(10, 10, 0.3) == 1.0
    assert binom_cdf(0, 4, 0.5) == pytest.approx(1 / 16)
    assert binom_cdf(2, 4, 0.5) == pytest.approx(11 / 16)


def test_mcnemar_counts_and_p() -> None:
    a = np.array([True] * 8 + [False] * 2 + [True] * 5)
    b = np.array([False] * 8 + [True] * 2 + [True] * 5)
    t = mcnemar(a, b)
    assert (t.only_a, t.only_b) == (8, 2)
    # 2 * P(X <= 2 | n = 10, p = .5) = 2 * 56 / 1024.
    assert t.p == pytest.approx(2 * 56 / 1024)


def test_mcnemar_no_discordant_pairs() -> None:
    a = np.array([True, False])
    assert mcnemar(a, a).p == 1.0
    with pytest.raises(ValueError, match="shapes"):
        mcnemar(a, np.array([True]))


@pytest.mark.skipif(
    perf_unusable(),
    reason="the wrapper counts the child under perf stat on Linux",
)
def test_wrapper_spins_the_requested_fraction(tmp_path: Path) -> None:
    (tmp_path / "golden.arm").write_text("A\n")
    log = tmp_path / "log.jsonl"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"spin_A": 0.25, "log": str(log)}))
    env = {"GOLDEN_CFG": str(cfg), "PATH": "/usr/bin:/bin"}
    child = [sys.executable, "-c", "sum(range(2_000_000))"]
    done = subprocess.run(
        [sys.executable, str(WRAP), "--", *child],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    reported = float(done.stdout.split('"value":')[1].strip(" }\n"))
    row = json.loads(log.read_text())
    assert row["arm"] == "A"
    assert row["spin_s"] == pytest.approx(0.25 * row["work_s"], rel=0.02)
    assert reported == pytest.approx(row["work_s"] + row["spin_s"], abs=1e-6)
    assert math.isclose(reported / row["work_s"], 1.25, rel_tol=0.02)


@pytest.mark.skipif(
    perf_unusable(),
    reason="the wrapper counts the child under perf stat on Linux",
)
def test_wrapper_candidate_arm_is_not_slowed(tmp_path: Path) -> None:
    (tmp_path / "golden.arm").write_text("B\n")
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"spin_A": 0.5}))
    env = {"GOLDEN_CFG": str(cfg), "PATH": "/usr/bin:/bin"}
    done = subprocess.run(
        [sys.executable, str(WRAP), "--", sys.executable, "-c", "pass"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert done.stdout.startswith('PERFPROOF {"value": ')


@pytest.mark.skipif(
    perf_unusable(),
    reason="the wrapper counts the child under perf stat on Linux",
)
def test_wrapper_overhead_after_exit_is_negligible(tmp_path: Path) -> None:
    # The candidate arm is never spun, so its reported value is the
    # child's work plus whatever the wrapper does after the child
    # exits. That must stay far below the 1% effects under test (the
    # first version read /proc/stat there: about 100 us).
    (tmp_path / "golden.arm").write_text("B\n")
    log = tmp_path / "log.jsonl"
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"log": str(log)}))
    env = {"GOLDEN_CFG": str(cfg), "PATH": "/usr/bin:/bin"}
    for _ in range(7):
        subprocess.run(
            [sys.executable, str(WRAP), "--", sys.executable, "-c", "pass"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            check=True,
            timeout=60,
        )
    rows = [json.loads(x) for x in log.read_text().splitlines()]
    extra = sorted(r["reported_s"] - r["work_s"] for r in rows)
    assert extra[len(extra) // 2] < 60e-6
