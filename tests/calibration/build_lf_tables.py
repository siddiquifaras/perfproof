"""Build the critical-value table of the ``hac_lf`` interval.

``docs/method.md`` ("The critical-value table") specifies it. This
writes ``src/perfproof/stats/lf_tables.py`` (or any path given), the
constants `perfproof.stats.intervals.hac_lf_interval` reads::

    uv run python tests/calibration/build_lf_tables.py build \\
        --seed 7974144670174977917 --draws 4000000 --b 0.1 \\
        --workers 4 \\
        --out src/perfproof/stats/lf_tables.py
    uv run python tests/calibration/build_lf_tables.py compare A.py B.py

What a table value is. For one look length ``n``, per-look confidence
and limit ``rho_max``: critical values ``c_b``, one per bin ``b`` of the
sample lag-1 ``r``, such that for **every** AR(1) coefficient ``rho`` in
the grid up to ``rho_max`` the unconditional probability that the
interval ``d_bar -/+ c_{bin(r)} se`` misses the true mean is at most
``1 - confidence``. Among such vectors the builder takes the one a
greedy search reaches by starting at the independent-noise quantile in
every bin and raising, while some ``rho`` is over its budget, the value
of the bin that contributes most to that ``rho``'s miss rate. A bin the
data of a ``rho`` rarely visit therefore costs nothing for that ``rho``,
and bins where dependence shows up get the width. Values are then made
nondecreasing in ``r``, in ``rho_max`` and nonincreasing in ``n``
(running maxima), and rounded up. The unconditional form is what the
argument under "Stopping" in ``docs/method.md`` uses: each look is valid
at its own ``n``.

``build`` is resumable with ``--cache DIR`` (per-node results) and
``--limit``; ``compare`` prints how far two independent builds differ.
The committed table is seed 7974144670174977917, 4,000,000 draws.
"""

import argparse
import importlib.util
import multiprocessing
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Final

import numpy as np
from fixedb import ar1_normal, order_pattern
from inference import FloatArray, Spec, lag1, pivot
from numpy.typing import NDArray

from perfproof.stats.sequential import look_confidence

HAC_B: Final = 0.10
"""Fixed-b ratio of the bandwidth, the default of ``--b``."""
RHOS: Final = (
    0.0,
    0.05,
    0.1,
    0.15,
    0.2,
    0.25,
    0.3,
    0.4,
    0.5,
    0.6,
    0.7,
    0.8,
    0.9,
)
RHO_MAXES: Final = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
"""The upper limits of rho a value covers."""
EDGES: Final = tuple(
    float(x) + 0.0 for x in np.round(np.arange(-0.5, 0.95, 0.1), 10)
)
"""Bin edges of the sample lag-1: 15 edges, 16 bins, open ended."""
LEVEL_KS: Final = (1, 2, 3, 4, 6, 8, 12)
NODES: Final = (*range(12, 31, 2), 36, 42, 48, 54, 60, 72, 84, 96, 108, 120)
C_GRID: Final = np.round(np.arange(2.0, 40.0001, 0.02), 4)
"""Candidate critical values, in steps of 0.02."""
CHUNK_ELEMENTS: Final = 2_000_000
MODES: Final = ("one_host", "cross_host")


def build_node(
    args: tuple[str, int, int, int, float],
) -> tuple[str, int, FloatArray]:
    """Exceedance rates for one mode and look length.

    Returns ``(mode, n, x)`` with ``x`` shaped ``(rhos, bins, grid)``:
    ``x[i, b, g]`` is the share of the draws at ``RHOS[i]`` whose lag-1
    falls in bin ``b`` and whose pivot exceeds ``C_GRID[g]``.
    """
    mode, n, draws, seed, b = args
    spec = Spec("hac_lf", "mean", "bartlett", b)
    edges = np.array(EDGES)
    bins = len(EDGES) + 1
    rng = np.random.default_rng(
        np.random.SeedSequence(
            seed, spawn_key=(zlib.crc32(f"{mode}/{n}".encode()),)
        )
    )
    out = np.zeros((len(RHOS), bins, len(C_GRID)))
    size = max(1, CHUNK_ELEMENTS // n)
    for ri, rho in enumerate(RHOS):
        r_parts: list[FloatArray] = []
        t_parts: list[FloatArray] = []
        done = 0
        while done < draws:
            rows = min(size, draws - done)
            x = ar1_normal(rng, rows, n, rho)
            first = order_pattern(rng, rows, n, mode)
            r_parts.append(lag1(x, first))
            t_parts.append(np.abs(pivot(x, first, spec)))
            done += rows
        r = np.concatenate(r_parts)
        t = np.concatenate(t_parts)
        which = np.digitize(r, edges)
        for k in range(bins):
            mine = np.sort(t[which == k])
            above = mine.size - np.searchsorted(mine, C_GRID, side="right")
            out[ri, k] = above / draws
    return mode, n, out


def optimise(x: FloatArray, rho_max: float, budget: float) -> FloatArray:
    """Critical values per bin for one limit and miss budget.

    Greedy search described in the module docstring over the ``rho``
    in `RHOS` up to ``rho_max``; ``x`` is `build_node`'s output.
    """
    used = [i for i, rho in enumerate(RHOS) if rho <= rho_max]
    sub = x[used]
    bins = sub.shape[1]
    total0 = sub[0].sum(axis=0)
    start = int(np.argmax(total0 <= budget)) if np.any(total0 <= budget) else 0
    idx = np.full(bins, start, dtype=np.int64)
    last = len(C_GRID) - 1
    rows: NDArray[np.int64] = np.arange(bins, dtype=np.int64)
    for _ in range(200_000):
        miss = sub[:, rows, idx].sum(axis=1)
        worst = int(np.argmax(miss))
        if miss[worst] <= budget:
            break
        contrib = np.where(idx < last, sub[worst, rows, idx], -1.0)
        k = int(np.argmax(contrib))
        if contrib[k] <= 0:
            break
        idx[k] += 1
    return C_GRID[idx]


def table_for_node(x: FloatArray) -> FloatArray:
    """Values shaped ``(rho limits, bins, levels)`` for one node."""
    bins = x.shape[1]
    out = np.empty((len(RHO_MAXES), bins, len(LEVEL_KS)))
    for ki, k in enumerate(LEVEL_KS):
        budget = 1.0 - look_confidence(0.05, k)
        for mi, limit in enumerate(RHO_MAXES):
            out[mi, :, ki] = np.maximum.accumulate(optimise(x, limit, budget))
    return np.maximum.accumulate(out, axis=0)


def assemble(
    nodes: Sequence[tuple[str, int, FloatArray]],
) -> dict[str, FloatArray]:
    """Stack the nodes per mode, enforce nonincreasing in ``n``.

    Returns arrays shaped ``(nodes, rho limits, bins, levels)``, each
    value the running maximum from the largest ``n`` down and rounded up
    to three decimals.
    """
    table: dict[str, FloatArray] = {}
    for mode in MODES:
        by_n = {n: table_for_node(x) for m, n, x in nodes if m == mode}
        stack = np.stack([by_n[n] for n in NODES])
        envelope = np.maximum.accumulate(stack[::-1], axis=0)[::-1]
        table[mode] = np.ceil(envelope * 1000.0 - 1e-9) / 1000.0
    return table


def write_module(
    table: dict[str, FloatArray],
    path: Path,
    *,
    seed: int,
    draws: int,
    b: float,
) -> None:
    """Write ``table`` as the Python module the stats layer imports."""
    lines = [
        '"""Critical values of the ``hac_lf`` interval.',
        "",
        "Generated by ``tests/calibration/build_lf_tables.py``; do not edit.",
        '``docs/method.md`` ("The critical-value table") specifies it.',
        f"Built from seed {seed}, {draws} draws per rho.",
        '"""',
        "",
        "from typing import Final",
        "",
        f"SEED: Final = {seed}",
        f"DRAWS: Final = {draws}",
        f"HAC_B: Final = {b!r}",
        f"RHOS: Final = {RHOS!r}",
        f"RHO_MAXES: Final = {RHO_MAXES!r}",
        f"EDGES: Final = {EDGES!r}",
        f"LEVEL_KS: Final = {LEVEL_KS!r}",
        f"NODES: Final = {NODES!r}",
        "",
        "_Table = tuple[tuple[tuple[tuple[float, ...], ...], ...], ...]",
        "",
        "VALUES: Final[dict[str, _Table]] = {",
    ]
    for mode, arr in table.items():
        lines.append(f"    {mode!r}: (")
        for node in arr:
            lines.append("        (")
            for limit in node:
                lines.append("            (")
                lines.extend(
                    f"                {tuple(float(v) for v in row)!r},"
                    for row in limit
                )
                lines.append("            ),")
            lines.append("        ),")
        lines.append("    ),")
    lines.append("}")
    lines.append(
        '"""Mode to values shaped nodes x rho limits x bins x levels."""'
    )
    path.write_text("\n".join(lines) + "\n")


def _cache_path(cache: Path, job: tuple[str, int, int, int, float]) -> Path:
    mode, n, draws, seed, b = job
    return cache / f"{mode}-{n}-{draws}-{seed}-{b}.npy"


def _cached(
    cache: Path | None, job: tuple[str, int, int, int, float]
) -> FloatArray | None:
    if cache is None:
        return None
    path = _cache_path(cache, job)
    return np.load(path) if path.exists() else None


def build(argv: Sequence[str]) -> int:
    """Build the table and write the module."""
    parser = argparse.ArgumentParser(prog="build_lf_tables.py build")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--draws", type=int, default=4_000_000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--b", type=float, default=HAC_B)
    parser.add_argument(
        "--limit",
        type=int,
        help="compute at most this many nodes, then exit 3 if some remain",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="directory of per-node results; nodes found there are reused",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be in [1, 4]")
    jobs = [
        (mode, n, args.draws, args.seed, args.b)
        for mode in MODES
        for n in NODES
    ]
    jobs.sort(key=lambda j: -j[1])
    parts: list[tuple[str, int, FloatArray]] = []
    todo: list[tuple[str, int, int, int, float]] = []
    for job in jobs:
        saved = _cached(args.cache, job)
        if saved is None:
            todo.append(job)
        else:
            parts.append((job[0], job[1], saved))
    remaining = 0
    if args.limit is not None and len(todo) > args.limit:
        remaining = len(todo) - args.limit
        todo = todo[: args.limit]
    context = multiprocessing.get_context("spawn")
    with context.Pool(args.workers) as pool:
        for count, got in enumerate(pool.imap_unordered(build_node, todo), 1):
            parts.append(got)
            if args.cache is not None:
                args.cache.mkdir(parents=True, exist_ok=True)
                np.save(
                    _cache_path(
                        args.cache,
                        (got[0], got[1], args.draws, args.seed, args.b),
                    ),
                    got[2],
                )
            sys.stdout.write(f"[{count}/{len(todo)}] {got[0]} n={got[1]}\n")
            sys.stdout.flush()
    if remaining:
        return 3
    write_module(
        assemble(parts),
        args.out,
        seed=args.seed,
        draws=args.draws,
        b=args.b,
    )
    return 0


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        msg = f"cannot load {path}"
        raise ValueError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compare(argv: Sequence[str]) -> int:
    """Print the relative difference between two builds, per mode."""
    a, b = (_load(Path(p)) for p in argv)
    write = sys.stdout.write
    for key in a.VALUES:
        x = np.array(a.VALUES[key])
        y = np.array(b.VALUES[key])
        rel = np.abs(x - y) / np.maximum(x, y)
        write(
            f"{key}: median {100 * np.median(rel):.2f}% "
            f"p95 {100 * np.quantile(rel, 0.95):.2f}% "
            f"max {100 * rel.max():.2f}%\n"
        )
    return 0


def main() -> int:
    """Dispatch to ``build`` or ``compare``."""
    if len(sys.argv) < 2 or sys.argv[1] not in ("build", "compare"):
        sys.stderr.write(__doc__ or "")
        return 2
    return (build if sys.argv[1] == "build" else compare)(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
