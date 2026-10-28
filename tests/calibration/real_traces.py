"""Real A/A traces as a source of replicates for T1.11 phase 1b.

The traces are paired A/A runs recorded for perfproof (`<label>.jsonl`
beside `<label>.header.json`, one record per execution: ``pair``,
``warmup``, ``pos`` (0 = first of the pair), ``arm`` (``A`` baseline,
``B`` candidate), ``wall_s``, ``reported_s``, ``load1``). Warm-up pairs
are dropped. A run's time is ``reported_s`` when present, else
``wall_s``. Per pair ``d = log(t_A / t_B)`` (lower is better, so above 0
is a faster candidate), ``first`` is True where B ran first, and the
covariate is ``x = load1_B - load1_A``, the recorded load average at the
candidate's run minus the baseline's.

Replicates are moving-block bootstraps of the pairs. Blocks start on
even pair indexes and have even length, so that the two rounds of an
order pair stay together and every replicate keeps the design's balanced
order. A speedup ``S`` is injected by centring ``d`` on the trace's mean
(the A/A ratio of a trace is within 0.4% of 1, but not exactly 1) and
adding ``log(S)``, so that the true speedup of every replicate is
exactly ``S``: ``log c' = log b - d'``. Block length is Politis-White
(`inference.politis_white_length` on the order-centred ``d``, rounded up
to even) or fixed.

Nothing here touches the network.
"""

import gzip
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import numpy as np
from inference import FloatArray, order_centre, politis_white_length
from numpy.typing import NDArray

TRACE_DIR: Final = Path(__file__).parent / "traces" / "data"
"""The committed traces (T2.11 phase 1, PR #84): ``<label>.jsonl.gz``
beside ``<label>.header.json``."""
PATTERNS: Final = ("linux-*.jsonl.gz", "linuxvm-*.jsonl.gz", "mac-*.jsonl.gz")
"""Which committed traces are A/A traces of the real hosts."""
EXCLUDED_SUFFIX: Final = "-partial-before-retry"
"""A run cut short and redone; kept in the repo for the record only."""
FIXED_BLOCKS: Final = (6, 20)
"""Fixed block lengths (even: 5 would split an order pair, so 6, the
primary's block, stands for 5)."""


@dataclass(frozen=True, slots=True)
class Trace:
    """One recorded A/A trace after dropping warm-up.

    Attributes
    ----------
    name : str
        The label, e.g. ``linux-rolling``.
    t_base, d, x : numpy.ndarray, shape (N,)
        Baseline time in seconds, log ratio ``log(t_A / t_B)`` and
        covariate difference, per complete pair, in run order.
    first : numpy.ndarray of bool, shape (N,)
        True where B (the candidate) ran first.
    """

    name: str
    t_base: FloatArray
    d: FloatArray
    x: FloatArray
    first: NDArray[np.bool_]

    @property
    def pairs(self) -> int:
        """Number of complete pairs."""
        return int(self.d.size)


def load_trace(path: Path) -> Trace:
    """Read ``path`` (a ``.jsonl`` trace) into a `Trace`.

    Raises
    ------
    ValueError
        If a time is not positive and finite, or there are no complete
        pairs.
    """
    pairs: dict[int, dict[str, tuple[float, float, int]]] = {}
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as source:
        for line in source:
            r = json.loads(line)
            if r["warmup"]:
                continue
            seconds = r["reported_s"]
            if seconds is None:
                seconds = r["wall_s"]
            if seconds is None or not math.isfinite(seconds) or seconds <= 0:
                msg = f"{path.name}: bad time {seconds!r} in pair {r['pair']}"
                raise ValueError(msg)
            pairs.setdefault(r["pair"], {})[r["arm"]] = (
                float(seconds),
                float(r["load1"]),
                int(r["pos"]),
            )
    keep = sorted(p for p, v in pairs.items() if {"A", "B"} <= set(v))
    if not keep:
        msg = f"{path.name}: no complete pairs"
        raise ValueError(msg)
    t_a = np.array([pairs[p]["A"][0] for p in keep])
    t_b = np.array([pairs[p]["B"][0] for p in keep])
    load_a = np.array([pairs[p]["A"][1] for p in keep])
    load_b = np.array([pairs[p]["B"][1] for p in keep])
    first = np.array([pairs[p]["B"][2] == 0 for p in keep], dtype=np.bool_)
    return Trace(
        name=path.name.removesuffix(".gz").removesuffix(".jsonl"),
        t_base=t_a,
        d=np.log(t_a / t_b),
        x=load_b - load_a,
        first=first,
    )


def load_all(directory: Path = TRACE_DIR) -> list[Trace]:
    """Every committed trace of `PATTERNS` in ``directory``, by name.

    Raises
    ------
    FileNotFoundError
        If ``directory`` is missing or holds no trace: the traces are
        part of the repository, so this means a broken checkout, and
        running on fewer traces would change every result silently.
    """
    found = sorted(
        {
            p
            for pattern in PATTERNS
            for p in directory.glob(pattern)
            if EXCLUDED_SUFFIX not in p.name
        },
        key=lambda p: p.name,
    )
    if not found:
        msg = (
            f"no A/A traces in {directory} (expected {', '.join(PATTERNS)} "
            "from PR #84); check the checkout"
        )
        raise FileNotFoundError(msg)
    return [load_trace(p) for p in found]


def politis_white_block(trace: Trace) -> int:
    """Even block length from Politis-White on the trace's ``d``."""
    centred = order_centre(trace.d[None, :], trace.first[None, :])
    length = float(politis_white_length(centred)[0])
    return max(2, 2 * math.ceil(length / 2))


def replicate_indexes(
    trace: Trace, block: int, rows: int, rounds: int, rng: np.random.Generator
) -> NDArray[np.int64]:
    """Moving-block bootstrap indexes, shape ``(rows, rounds)``.

    Blocks of ``block`` (even) consecutive pairs start at even pair
    indexes, at least ``block`` from the end, so each order pair stays
    whole.
    """
    if block % 2 or block < 2:
        msg = f"block must be even and at least 2, got {block}"
        raise ValueError(msg)
    n = trace.pairs - trace.pairs % 2
    if block > n:
        msg = f"block {block} exceeds the {n} usable pairs of {trace.name}"
        raise ValueError(msg)
    count = math.ceil(rounds / block)
    starts = 2 * rng.integers(0, (n - block) // 2 + 1, size=(rows, count))
    idx = starts[:, :, None] + np.arange(block)[None, None, :]
    return idx.reshape(rows, count * block)[:, :rounds].astype(np.int64)


def replicates(
    trace: Trace,
    block: int,
    speedup: float,
    rows: int,
    rounds: int,
    rng: np.random.Generator,
) -> tuple[FloatArray, NDArray[np.bool_], FloatArray]:
    """Log times, order flags and covariate of ``rows`` replicates.

    Returns ``log_times`` shaped ``(rows, rounds, 2)`` (baseline,
    candidate), ``first`` shaped ``(rows, rounds)`` and ``x`` shaped
    ``(rows, rounds)``, with true speedup exactly ``speedup``.
    """
    idx = replicate_indexes(trace, block, rows, rounds, rng)
    d = trace.d[idx] - trace.d.mean() + math.log(speedup)
    log_b = np.log(trace.t_base[idx])
    log_times = np.stack((log_b, log_b - d), axis=-1)
    return log_times, trace.first[idx], trace.x[idx]
