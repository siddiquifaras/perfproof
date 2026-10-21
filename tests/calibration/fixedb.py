"""Critical values of the studentised challengers, by simulation.

A heteroskedasticity-and-autocorrelation-consistent (HAC) standard
error from ``n`` observations with bandwidth ``M = b n`` is not
asymptotically normal or ``t``: its randomness does not vanish when
``b`` is fixed (Kiefer and Vogelsang, 2005), so the pivot
``(theta_hat - theta) / se`` has its own distribution, which depends
on the kernel, on ``b``, on the estimator and, at the small ``n`` the
looks use, on ``n`` itself. The Kiefer-Vogelsang critical values are
limits as ``n`` grows. The looks here have 12 to 120 observations, so
the table is the finite-sample version: for every procedure of
`SPECS`, every look length ``n`` and each of the two per-look
confidence levels of the default schedules, the quantile of ``|T|``
when the observations are i.i.d. standard normal (the pivot is then
location and scale free), drawn with a fixed seed and the same order
structure as the design (balanced order pairs with a coin per pair on
one host, one group cross host).

Under dependence or heavy tails the pivot's distribution is another
one; the acceptance rule of ``docs/method.md`` is what says whether
this correction is enough. A second table, the least favourable one
(`LF_SPECS`), does not assume independence: the pivot is drawn under
AR(1) noise with ``rho`` in `RHOS` (the audit range of
``docs/method.md``), the draws are binned by the sample lag-1
autocorrelation ``r`` of the order-centred observations, and the
critical value of a bin is the largest conditional quantile over
``rho``, made nondecreasing in ``r`` and interpolated linearly between
bin edges from each bin's own (conservative) value. The interval
therefore widens continuously with ``r`` and covers, at every ``r``,
the worst AR(1) in the range given that ``r``: no threshold, and no
withholding.

Each row of the i.i.d. table records the Monte Carlo standard error of
its critical value, from the binomial interval of the tail count (the
delta method), in the units of the critical value.

Usage::

    uv run python tests/calibration/fixedb.py generate --table iid \\
        --draws 300000 --workers 4 \\
        --out tests/calibration/results/fixed_b_critical.csv
    uv run python tests/calibration/fixedb.py generate --table lf \\
        --draws 300000 --workers 4 \\
        --out tests/calibration/results/lf_critical.csv
    uv run python tests/calibration/fixedb.py generate \\
        --table lf-extra --draws 200000 --workers 4 \\
        --out tests/calibration/results/lf_critical_extra.csv
"""

import argparse
import csv
import math
import multiprocessing
import os
import sys
import zlib
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Final

import numpy as np
from inference import BoolArray, FloatArray, Spec, lag1, pivot

from perfproof.stats.sequential import look_confidence

TABLE_SEED: Final = 6522260469845266907
"""Drawn before any run, for the committed tables."""
DRAWS: Final = 300_000
N_MIN: Final = 12
N_MAX: Final = 120
LEVELS: Final = tuple(
    sorted(
        {
            round(1.0 - m * (1.0 - look_confidence(0.05, k)), 12)
            for k in (3, 6)
            for m in (1, 2)
        }
    )
)
"""Per-look confidences of the default schedules, ``K`` = 3 and 6, and
the same with ``alpha / (2 K)`` doubled, which a least favourable
critical value may be built at (`challengers.Challenger.widen`)."""
CHUNK_ELEMENTS: Final = 4_000_000
RESULTS: Final = Path(
    os.environ.get("T111_TABLES", Path(__file__).parent / "results")
)
DEFAULT_TABLE: Final = RESULTS / "fixed_b_critical.csv"
DEFAULT_LF_TABLE: Final = RESULTS / "lf_critical.csv"
EXTRA_LF_TABLE: Final = RESULTS / "lf_critical_extra.csv"

SPECS: Final = (
    Spec("hac_b10", "mean", "bartlett", 0.10),
    Spec("hac_b20", "mean", "bartlett", 0.20),
    Spec("hac_b30", "mean", "bartlett", 0.30),
    Spec("sb_pw", "mean", "sb"),
    Spec("huber_hac_b20", "huber", "bartlett", 0.20),
    Spec("huber_hac_b30", "huber", "bartlett", 0.30),
)
LF_SPECS: Final = (
    Spec("lf_iid", "mean", "iid", lf=True),
    Spec("lf_hac_b10", "mean", "bartlett", 0.10, lf=True),
    Spec("lf_hac_b20", "mean", "bartlett", 0.20, lf=True),
    Spec("lf_huber_iid", "huber", "iid", lf=True),
    Spec("lf_huber_hac_b10", "huber", "bartlett", 0.10, lf=True),
    Spec("lf_adj_hac_b10", "adjusted", "bartlett", 0.10, lf=True),
)
LF_EXTRA_SPECS: Final = (
    Spec("lf_huber_adj_hac_b10", "huber_adjusted", "bartlett", 0.10, lf=True),
)
"""Added after the first wave of runs: their table is
``lf_critical_extra.csv``."""
SPEC_BY_NAME: Final = {s.name: s for s in (*SPECS, *LF_SPECS, *LF_EXTRA_SPECS)}
RHOS: Final = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7)
"""AR(1) coefficients of the least favourable table: the audit range."""
LF_DRAWS: Final = 300_000
"""Draws per ``rho`` of the least favourable table."""
LF_N_NODES: Final = (
    *range(12, 31, 2),
    36,
    42,
    48,
    54,
    60,
    72,
    84,
    96,
    108,
    120,
)
"""Look lengths tabulated for the least favourable table; a look of
``n`` observations takes the largest of these at most ``n``. That is
not guaranteed conservative: critical values usually fall as ``n``
grows, but the committed table has bins where the next node is higher
(up to 12.6% in ``lf_hac_b20`` cross host, nodes 48 to 54), a
consequence of Monte Carlo noise in the bins."""
LF_MIN_COUNT: Final = 2000
"""Fewest draws a (``rho``, bin) must hold to set a critical value."""
LF_EDGES: Final = np.round(np.arange(-0.5, 0.9501, 0.05), 10)
"""Bin edges of the sample lag-1 autocorrelation (30 edges, 31 bins)."""
LF_NODES: Final = np.concatenate(([LF_EDGES[0] - 0.05], LF_EDGES))
"""Where each bin's value sits for interpolation: its lower edge."""
COLUMNS: Final = (
    "procedure",
    "mode",
    "n",
    "confidence",
    "draws",
    "crit",
    "mc_se",
    "seed",
)
LF_COLUMNS: Final = (
    "procedure",
    "mode",
    "n",
    "confidence",
    "draws",
    "values",
    "seed",
)


def order_pattern(
    rng: np.random.Generator, rows: int, n: int, mode: str
) -> BoolArray:
    """Candidate-first flags: order pairs one host, none cross."""
    first = np.zeros((rows, n), dtype=np.bool_)
    if mode == "one_host":
        coins = rng.random((rows, n // 2)) < 0.5
        first[:, 0::2] = coins
        first[:, 1::2] = ~coins
    return first


def _stream(label: str, mode: str, n: int, seed: int) -> np.random.Generator:
    return np.random.default_rng(
        np.random.SeedSequence(
            seed, spawn_key=(zlib.crc32(f"{label}/{mode}/{n}".encode()),)
        )
    )


def _check_n(mode: str, n: int) -> None:
    if mode == "one_host" and n % 2:
        msg = f"one host looks are on even n, got {n}"
        raise ValueError(msg)


def critical_values(
    mode: str, n: int, specs: Sequence[Spec], draws: int, seed: int
) -> list[dict[str, object]]:
    """Rows of the i.i.d. table for one mode and look length.

    The same ``draws`` i.i.d. normal samples serve every spec.
    """
    _check_n(mode, n)
    rng = _stream("iid", mode, n, seed)
    size = max(1, CHUNK_ELEMENTS // n)
    kept: dict[str, list[FloatArray]] = {s.name: [] for s in specs}
    done = 0
    while done < draws:
        rows = min(size, draws - done)
        z = rng.standard_normal((rows, n))
        first = order_pattern(rng, rows, n, mode)
        cov = rng.standard_normal((rows, n))
        for s in specs:
            kept[s.name].append(np.abs(pivot(z, first, s, cov)))
        done += rows
    out: list[dict[str, object]] = []
    for s in specs:
        t = np.concatenate(kept[s.name])
        if not np.all(np.isfinite(t)):
            msg = f"{s.name} n={n}: non-finite pivot under i.i.d. normal"
            raise ValueError(msg)
        for conf in LEVELS:
            half = 1.96 * math.sqrt(conf * (1 - conf) / draws)
            lo, mid, hi = np.quantile(
                t, [max(conf - half, 0.0), conf, min(conf + half, 1.0)]
            )
            out.append(
                {
                    "procedure": s.name,
                    "mode": mode,
                    "n": n,
                    "confidence": repr(conf),
                    "draws": draws,
                    "crit": repr(float(mid)),
                    "mc_se": repr(float((hi - lo) / (2 * 1.96))),
                    "seed": seed,
                }
            )
    return out


def ar1_normal(
    rng: np.random.Generator, rows: int, n: int, rho: float
) -> FloatArray:
    """Stationary unit-variance Gaussian AR(1), ``(rows, n)``."""
    z = rng.standard_normal((rows, n))
    x = np.empty_like(z)
    x[:, 0] = z[:, 0]
    scale = math.sqrt(1.0 - rho * rho)
    for i in range(1, n):
        x[:, i] = rho * x[:, i - 1] + scale * z[:, i]
    return x


def lf_values(
    mode: str, n: int, specs: Sequence[Spec], draws: int, seed: int
) -> list[dict[str, object]]:
    """Rows of the least favourable table for one mode and look length.

    For each ``rho`` in `RHOS`, ``draws`` AR(1) samples; for each spec
    and per-look confidence, the conditional quantile of ``|T|`` in
    each lag-1 bin holding at least `LF_MIN_COUNT` draws; the maximum
    over ``rho``; then a running maximum along the bins and a floor at
    the i.i.d. (``rho = 0``) quantile, so that the value never falls as
    ``r`` rises.
    """
    _check_n(mode, n)
    rng = _stream("lf", mode, n, seed)
    size = max(1, CHUNK_ELEMENTS // n)
    bins = len(LF_EDGES) + 1
    best = {(s.name, c): np.zeros(bins) for s in specs for c in LEVELS}
    floor: dict[tuple[str, float], float] = {}
    for rho in RHOS:
        t_parts: dict[str, list[FloatArray]] = {s.name: [] for s in specs}
        r_parts: list[FloatArray] = []
        done = 0
        while done < draws:
            rows = min(size, draws - done)
            x = ar1_normal(rng, rows, n, rho)
            first = order_pattern(rng, rows, n, mode)
            cov = rng.standard_normal((rows, n))
            r_parts.append(lag1(x, first))
            for s in specs:
                t_parts[s.name].append(np.abs(pivot(x, first, s, cov)))
            done += rows
        which = np.digitize(np.concatenate(r_parts), LF_EDGES)
        for s in specs:
            t = np.concatenate(t_parts[s.name])
            for conf in LEVELS:
                if rho == 0.0:
                    floor[s.name, conf] = float(np.quantile(t, conf))
                for k in range(bins):
                    mine = t[which == k]
                    if mine.size >= LF_MIN_COUNT:
                        q = float(np.quantile(mine, conf))
                        best[s.name, conf][k] = max(best[s.name, conf][k], q)
    out: list[dict[str, object]] = []
    for s in specs:
        for conf in LEVELS:
            values = np.maximum.accumulate(
                np.maximum(best[s.name, conf], floor[s.name, conf])
            )
            out.append(
                {
                    "procedure": s.name,
                    "mode": mode,
                    "n": n,
                    "confidence": repr(conf),
                    "draws": draws,
                    "values": ";".join(f"{v:.5f}" for v in values),
                    "seed": seed,
                }
            )
    return out


def _work_iid(args: tuple[str, int, int, int]) -> list[dict[str, object]]:
    mode, n, draws, seed = args
    return critical_values(mode, n, SPECS, draws, seed)


def _work_lf(args: tuple[str, int, int, int]) -> list[dict[str, object]]:
    mode, n, draws, seed = args
    return lf_values(mode, n, LF_SPECS, draws, seed)


def _work_lf_extra(
    args: tuple[str, int, int, int],
) -> list[dict[str, object]]:
    mode, n, draws, seed = args
    return lf_values(mode, n, LF_EXTRA_SPECS, draws, seed)


@cache
def load(
    path: Path = DEFAULT_TABLE,
) -> dict[tuple[str, str, int, float], float]:
    """Read the i.i.d. table: ``(procedure, mode, n, confidence)``."""
    with path.open(newline="") as source:
        return {
            (
                r["procedure"],
                r["mode"],
                int(r["n"]),
                round(float(r["confidence"]), 10),
            ): float(r["crit"])
            for r in csv.DictReader(source)
        }


@cache
def load_lf(
    path: Path = DEFAULT_LF_TABLE,
) -> dict[tuple[str, str, int, float], FloatArray]:
    """Read the least favourable table: per bin values, 31 per row.

    Rows of ``lf_critical_extra.csv`` beside the file are included.
    """
    found: dict[tuple[str, str, int, float], FloatArray] = {}
    for name in (path, path.with_name(EXTRA_LF_TABLE.name)):
        if not name.exists():
            continue
        with name.open(newline="") as source:
            for r in csv.DictReader(source):
                key = (
                    r["procedure"],
                    r["mode"],
                    int(r["n"]),
                    round(float(r["confidence"]), 10),
                )
                found[key] = np.array(
                    [float(v) for v in r["values"].split(";")]
                )
    return found


def critical(procedure: str, mode: str, n: int, confidence: float) -> float:
    """I.i.d. critical value of ``procedure`` at ``n`` observations.

    Raises
    ------
    KeyError
        If the table has no such row (``n`` outside 12 to 120, an odd
        one-host ``n``, or a confidence other than `LEVELS`).
    """
    key = (procedure, mode, n, round(confidence, 10))
    try:
        return load()[key]
    except KeyError:
        msg = f"no critical value for {key}; see tests/calibration/fixedb.py"
        raise KeyError(msg) from None


def lf_critical(
    procedure: str,
    mode: str,
    n: int,
    confidence: float,
    rho_hat: FloatArray,
) -> FloatArray:
    """Least favourable critical value at each sample lag-1 ``rho_hat``.

    Piecewise linear in ``rho_hat`` between the bins' lower edges,
    nondecreasing, constant below the first and above the last edge.

    Raises
    ------
    KeyError
        As `critical`.
    """
    node = max(m for m in LF_N_NODES if m <= n)
    key = (procedure, mode, node, round(confidence, 10))
    try:
        values = load_lf()[key]
    except KeyError:
        msg = f"no lf critical value for {key}; see tests/calibration/"
        raise KeyError(msg + "fixedb.py") from None
    return np.interp(rho_hat, LF_NODES, values)


def generate(argv: Sequence[str]) -> int:
    """Write a table for every mode, look length, spec and level."""
    parser = argparse.ArgumentParser(prog="fixedb.py generate")
    parser.add_argument(
        "--table", choices=("iid", "lf", "lf-extra"), required=True
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--draws", type=int)
    parser.add_argument("--seed", type=int, default=TABLE_SEED)
    parser.add_argument("--n-max", type=int, default=N_MAX)
    parser.add_argument("--n-step", type=int, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        parser.error("--workers must be in [1, 4]")
    lf = args.table in ("lf", "lf-extra")
    draws = args.draws or (LF_DRAWS if lf else DRAWS)
    lengths = (
        [n for n in LF_N_NODES if n <= args.n_max]
        if lf
        else range(N_MIN, args.n_max + 1, args.n_step)
    )
    jobs = [
        (mode, n, draws, args.seed)
        for mode in ("one_host", "cross_host")
        for n in lengths
        if mode == "cross_host" or n % 2 == 0
    ]
    jobs.sort(key=lambda j: -j[1])
    context = multiprocessing.get_context("spawn")
    rows: list[dict[str, object]] = []
    with context.Pool(args.workers) as pool:
        work = (
            _work_lf_extra
            if args.table == "lf-extra"
            else _work_lf
            if lf
            else _work_iid
        )
        for count, got in enumerate(pool.imap_unordered(work, jobs), 1):
            rows.extend(got)
            sys.stdout.write(f"[{count}/{len(jobs)}]\n")
            sys.stdout.flush()
    specs = (
        LF_EXTRA_SPECS
        if args.table == "lf-extra"
        else LF_SPECS
        if lf
        else SPECS
    )
    order = {s.name: i for i, s in enumerate(specs)}
    rows.sort(
        key=lambda r: (
            order[str(r["procedure"])],
            str(r["mode"]),
            int(str(r["n"])),
            float(str(r["confidence"])),
        )
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as sink:
        columns = LF_COLUMNS if lf else COLUMNS
        writer = csv.writer(sink, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row[c] for c in columns])
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] != "generate":
        sys.stderr.write(__doc__ or "")
        sys.exit(2)
    sys.exit(generate(sys.argv[2:]))
