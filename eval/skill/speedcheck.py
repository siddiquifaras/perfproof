"""Time a run's final tree against its base tree, for both arms.

The harness does this itself, after the agent is done, so no number
here comes from the agent. The method is a fixed A/B loop, not
``perfproof compare``: the baseline arm must be judged without the tool
it does not have, ``compare`` needs a benchmark definition per project,
and a plain interleaved loop over one command is reliable for any
language. It is cruder than ``compare`` (no calibration, no sequential
looks) and is reported as what it is: ``rounds`` samples of each tree,
the ratio of medians and a seeded percentile bootstrap interval.

Procedure (`measure`): check out the base commit and the final commit
alternately, run the optional ``build`` untimed after each switch, run
the command once untimed in each tree as warm-up, then take ``rounds``
timed samples per tree, interleaved (base, final, base, final, ...,
starting with whichever tree the round number makes first, so a drift
in machine load does not always favour one side). A command that exits
non-zero or times out in either tree makes the result ``unknown``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from taskset import WALL_METRICS, SpeedCheck

SPEED_FILE = "speed.json"
"""Written next to a task's transcripts."""
ROUNDS = 7
"""Timed samples per tree."""
BOOTSTRAP = 2000
CONFIDENCE = 0.95
SEED = 20261029

Run = Callable[[str, Path], tuple[int, float]]
"""Run a command line in a directory: exit code and wall seconds."""
Git = Callable[..., str]
"""Run ``git <args>`` in the repo, return stdout; raises on error."""


class SpeedResult(BaseModel):
    """What the harness measured, or why it could not.

    Attributes
    ----------
    status : {"measured", "unchanged", "unknown"}
        ``unchanged``: the final tree equals the base tree, so nothing
        was timed. ``unknown``: it could not be measured (``reason``).
    verdict : {"faster", "slower", "no_difference"} or None
        ``faster`` when the whole interval is above 1, ``slower`` when
        it is below 1, otherwise ``no_difference``.
    ratio : float or None
        Median base seconds over median final seconds: above 1 means
        the final tree is faster.
    low, high : float or None
        Bootstrap interval of the ratio, ``CONFIDENCE`` level.
    base_median_s, final_median_s : float or None
        The medians, seconds.
    rounds : int
        Timed samples per tree.
    final_ref : str
        What the final tree is (``working`` or a branch name).
    reason : str
        Why ``unknown``; empty otherwise.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["measured", "unchanged", "unknown"]
    verdict: Literal["faster", "slower", "no_difference"] | None = None
    ratio: float | None = None
    low: float | None = None
    high: float | None = None
    base_median_s: float | None = None
    final_median_s: float | None = None
    rounds: int = 0
    final_ref: str = ""
    reason: str = ""


def ratio_interval(
    base: list[float], final: list[float]
) -> tuple[float, float, float]:
    """Ratio of medians and its bootstrap interval.

    Parameters
    ----------
    base, final : list of float
        Wall seconds of each tree's samples (each at least one).

    Returns
    -------
    tuple of (float, float, float)
        ``median(base) / median(final)``, then the lower and upper
        bound of the percentile bootstrap (resample each side with
        replacement, ``BOOTSTRAP`` times, fixed ``SEED``).

    Notes
    -----
    One ``(BOOTSTRAP, n)`` index draw per side and one ``axis=1``
    median; ``O(BOOTSTRAP * n)``.
    """
    a = np.asarray(base, dtype=np.float64)
    b = np.asarray(final, dtype=np.float64)
    rng = np.random.default_rng(SEED)
    ia = rng.integers(0, a.size, size=(BOOTSTRAP, a.size))
    ib = rng.integers(0, b.size, size=(BOOTSTRAP, b.size))
    boot = np.median(a[ia], axis=1) / np.median(b[ib], axis=1)
    tail = (1.0 - CONFIDENCE) / 2.0 * 100.0
    low, high = np.percentile(boot, [tail, 100.0 - tail])
    return (
        float(np.median(a) / np.median(b)),
        float(low),
        float(high),
    )


def _unknown(reason: str, ref: str) -> SpeedResult:
    return SpeedResult(status="unknown", reason=reason, final_ref=ref)


def measure(
    base_sha: str,
    final_sha: str,
    final_ref: str,
    check: SpeedCheck,
    repo: Path,
    run: Run,
    git: Git,
    rounds: int = ROUNDS,
) -> SpeedResult:
    """Time the final tree against the base tree.

    Parameters
    ----------
    base_sha, final_sha : str
        Commits holding the base and the final tree.
    final_ref : str
        Name recorded for the final tree.
    check : SpeedCheck
        The command, metric and optional build step.
    repo : Path
        The task repository; its checkout is switched between the two.
    run : callable
        Runs a command line in ``repo`` (confined by the caller).
    git : callable
        Runs git in ``repo``.
    rounds : int, optional
        Timed samples per tree.

    Returns
    -------
    SpeedResult
        Never raises for a failing command or an unknown metric.
    """
    if check.metric not in WALL_METRICS:
        return _unknown(f"unsupported metric {check.metric!r}", final_ref)
    if (
        base_sha == final_sha
        or not git("diff", "--name-only", base_sha, final_sha).strip()
    ):
        return SpeedResult(status="unchanged", final_ref=final_ref)
    trees = {"base": base_sha, "final": final_sha}

    def sample(which: str, timed: bool) -> tuple[float | None, str]:
        git("checkout", "-q", "--detach", "-f", trees[which])
        if check.build is not None:
            code, _ = run(check.build, repo)
            if code != 0:
                return None, f"build failed in {which} tree (exit {code})"
        code, seconds = run(check.command, repo)
        if code != 0:
            return None, f"command exited {code} in {which} tree"
        return seconds if timed else 0.0, ""

    seen: dict[str, list[float]] = {"base": [], "final": []}
    for which in ("base", "final"):
        value, why = sample(which, timed=False)
        if value is None:
            return _unknown(why, final_ref)
    for n in range(rounds):
        order = ("base", "final") if n % 2 == 0 else ("final", "base")
        for which in order:
            value, why = sample(which, timed=True)
            if value is None:
                return _unknown(why, final_ref)
            seen[which].append(value)
    ratio, low, high = ratio_interval(seen["base"], seen["final"])
    verdict: Literal["faster", "slower", "no_difference"] = (
        "faster" if low > 1.0 else "slower" if high < 1.0 else "no_difference"
    )
    return SpeedResult(
        status="measured",
        verdict=verdict,
        ratio=round(ratio, 4),
        low=round(low, 4),
        high=round(high, 4),
        base_median_s=round(float(np.median(seen["base"])), 6),
        final_median_s=round(float(np.median(seen["final"])), 6),
        rounds=rounds,
        final_ref=final_ref,
    )
