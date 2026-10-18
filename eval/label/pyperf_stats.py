"""pyperf's statistics on the labeller's recorded times.

pyperf's own runner times one command at a time, so it cannot
interleave two arms. The times are taken by `measure` with the clock
pyperf uses, then handed to pyperf here: one `pyperf.Benchmark` per
arm, saved in pyperf's JSON format, compared with the two-sample t-test
``pyperf compare_to`` uses, and checked with ``pyperf check``.

pyperf ships no type information, so this module is the one place
that touches it, and turns what it returns into plain Python values.
"""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportPrivateUsage=false
# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pyperf
from pyperf import _utils as pyperf_utils

TOOL_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class PyperfComparison:
    """What pyperf says about arm B against arm A.

    Attributes
    ----------
    version : str
        pyperf's version.
    mean_a, mean_b : float
        ``Benchmark.mean()`` of each arm, seconds.
    stdev_a, stdev_b : float
        ``Benchmark.stdev()`` of each arm, seconds.
    ratio : float
        ``mean_a / mean_b``; above 1 means B is faster.
    significant : bool
        pyperf's two-sample Student t-test at 95%.
    t_score : float
        That test's t statistic.
    compare_to : str
        Output of ``python -m pyperf compare_to a.json b.json``.
    check_a, check_b : str
        Output of ``python -m pyperf check`` on each arm.
    files : dict of str to str
        The arms' pyperf JSON files, by arm.
    """

    version: str
    mean_a: float
    mean_b: float
    stdev_a: float
    stdev_b: float
    ratio: float
    significant: bool
    t_score: float
    compare_to: str
    check_a: str
    check_b: str
    files: dict[str, str]


def _benchmark(values: list[float]) -> pyperf.Benchmark:
    # The same name in both files, so compare_to pairs them up.
    run = pyperf.Run(
        values,
        metadata={"name": "workload", "unit": "second", "loops": 1},
        collect_metadata=False,
    )
    return pyperf.Benchmark([run])


def _tool(args: list[str]) -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pyperf", *args],
        capture_output=True,
        text=True,
        timeout=TOOL_TIMEOUT_S,
        check=False,
    )
    return (proc.stdout + proc.stderr).strip()


def compare(
    a: list[float], b: list[float], out_dir: Path, stem: str
) -> PyperfComparison:
    """Save both arms as pyperf benchmarks and compare them.

    Parameters
    ----------
    a, b : list of float
        Wall times of arm A and arm B, seconds, all positive.
    out_dir : Path
        Directory for the two JSON files.
    stem : str
        File name prefix; the files are ``<stem>.a.pyperf.json`` and
        ``<stem>.b.pyperf.json``.

    Returns
    -------
    PyperfComparison
        pyperf's means, test and tool output.

    Raises
    ------
    ValueError
        If either list is empty or holds a value that is not positive
        (pyperf's own check).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    bench_a, bench_b = _benchmark(a), _benchmark(b)
    path_a = out_dir / f"{stem}.a.pyperf.json"
    path_b = out_dir / f"{stem}.b.pyperf.json"
    bench_a.dump(str(path_a), replace=True)
    bench_b.dump(str(path_b), replace=True)
    significant, t_score = pyperf_utils.is_significant(a, b)
    mean_a, mean_b = float(bench_a.mean()), float(bench_b.mean())
    return PyperfComparison(
        version=str(pyperf.__version__),
        mean_a=mean_a,
        mean_b=mean_b,
        stdev_a=float(bench_a.stdev()),
        stdev_b=float(bench_b.stdev()),
        ratio=mean_a / mean_b,
        significant=bool(significant),
        t_score=float(t_score),
        compare_to=_tool(["compare_to", str(path_a), str(path_b)]),
        check_a=_tool(["check", str(path_a)]),
        check_b=_tool(["check", str(path_b)]),
        files={"a": path_a.name, "b": path_b.name},
    )
