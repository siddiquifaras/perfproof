"""How each project function's cost grows with the input size.

``perfproof profile NAME --sizes 1,2,4`` profiles the benchmark once per
size. This module turns those profiles into a growth exponent per
project function, so the agent can tell a function that merely is
costly now from one that gets costlier as inputs grow.

Method
------
For every project function and every size:

1. its **cost** is its total share of that size's profile times the
   unprofiled run's wall time at that size, in seconds (an estimate: a
   profile share is of the profiler's basis, active samples for py-spy
   and the main thread's wall time for pyinstrument, and the shares and
   the wall time come from two separate runs);
2. the exponent ``e`` of ``cost ~ size ** e`` is fitted by least squares
   on the logarithms (`perfproof.bench.estimates.fit_exponent`), over
   the sizes where the function was sampled, with its 95% interval,
   ``r_squared`` and the number of points ``n``;
3. the whole workload's exponent is fitted the same way to the wall
   times.

Each fitted function gets an ``evidence`` of how it grows against the
workload:

- ``shown``: the lower end of the function's interval is above the
  upper end of the workload's. Its share of the run then rises with
  size, and so does its cap (the most a perfect fix of it could give),
  at these sizes and with these intervals. Only this makes the ranking
  say a ceiling rises.
- ``lead``: not shown, but the function's exponent exceeds the
  workload's by at least `LEAD_MARGIN`. The intervals overlap, so the
  fits cannot tell the two apart: a place to look, with the note
  ``interval_overlaps_workload``, and no claim about the ceiling.
  `LEAD_MARGIN` is an arbitrary display margin that keeps near-ties out
  of the leads; it decides nothing about what is shown.
- ``none``: neither.

``evidence`` is ``null`` when the function or the workload has no fit.

Limits
------
- One profile per size, so ``n`` is the number of sizes, usually 3 or
  4. An interval from three points has one degree of freedom and is
  very wide; the fit is an estimate, never a measured effect.
- ``notes`` mark a fit as unreliable: ``few_points`` (fewer than
  `MIN_POINTS` sizes), ``noisy`` (``r_squared`` below `MIN_R_SQUARED`,
  or an interval wider than `MAX_INTERVAL_WIDTH`), ``few_samples`` (at
  some size the function had fewer than `MIN_SAMPLES` samples, so its
  share is itself noisy) and ``missing_sizes`` (sampled at only some
  sizes; the fit uses those, and none is made below three). A function
  absent from the smaller sizes and present at the larger ones gets
  ``appears_at_larger_sizes``: no exponent, but a reason to look.
- Fixed costs (start-up in the wall time) pull the workload's exponent
  toward 0 and so make more functions look faster-growing; the wall
  metric's note in `perfproof.bench.scaling` applies here too.
- A function that dominates the workload has the workload's own
  exponent, so it is not flagged; its cap at the largest size, in the
  ranking, is what shows it.
- The sizes must change the work. If the knob changes nothing the
  exponents are near 0 and nothing is flagged.
- Functions are matched across sizes by kind, file and name, not by
  line. A function renamed or moved between sizes is two functions.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from perfproof.bench.estimates import ExponentFit, fit_exponent
from perfproof.commands import JsonValue
from perfproof.profilers.folded import rank_file
from perfproof.schema import Hotspot

__all__ = [
    "LEAD_MARGIN",
    "MAX_INTERVAL_WIDTH",
    "MIN_POINTS",
    "MIN_R_SQUARED",
    "MIN_SAMPLES",
    "FunctionKey",
    "GrowthResult",
    "SizePoint",
    "analyse_growth",
    "function_key",
    "project_functions",
    "workload_json",
]

LEAD_MARGIN: Final = 0.25
"""Exponent excess over the workload's that makes a lead; arbitrary."""
MIN_POINTS: Final = 4
"""Fewest sizes a fit needs to carry no ``few_points`` note."""
MIN_R_SQUARED: Final = 0.9
"""Lowest ``r_squared`` a fit may have without a ``noisy`` note."""
MAX_INTERVAL_WIDTH: Final = 1.0
"""Widest exponent interval a fit may have without a ``noisy`` note."""
MIN_SAMPLES: Final = 30.0
"""Fewest samples at any size before a ``few_samples`` note."""
MAX_PROJECT_FUNCTIONS: Final = 100_000
"""Most functions read from one profile."""
_MICROSECONDS_PER_SAMPLE: Final = 1000.0
"""A pyinstrument sample is 1 ms, so its microseconds / 1000."""
_FIT_MIN_SIZES: Final = 3

FunctionKey = tuple[str, str | None, str]
"""Frame kind, file and name: what a function is across sizes."""


@dataclass(frozen=True, slots=True)
class SizePoint:
    """One function in one size's profile.

    Attributes
    ----------
    total_fraction : float
        Share of the profile's samples in the function and its callees.
    samples : float
        Samples behind that share, as the profiler's unit converts
        (microseconds count as 1 ms samples); at least 0.
    """

    total_fraction: float
    samples: float


@dataclass(frozen=True, slots=True)
class GrowthResult:
    """The fits of one scaling profile run.

    Attributes
    ----------
    workload : perfproof.bench.estimates.ExponentFit or None
        Fit of the unprofiled wall time against size; ``None`` when the
        wall times cannot be fitted (fewer than three distinct sizes).
    functions : tuple of dict
        One JSON-ready entry per function seen at any size, by exponent
        descending then name; see `analyse_growth`.
    """

    workload: ExponentFit | None
    functions: tuple[dict[str, JsonValue], ...]


def function_key(h: Hotspot) -> FunctionKey:
    """Return what identifies a function across profiles.

    Parameters
    ----------
    h : perfproof.schema.Hotspot
        A function of a profile.

    Returns
    -------
    tuple
        ``(frame_kind, file, function)``.
    """
    return (h.frame_kind, h.file, h.function)


def project_functions(
    folded: Path,
    root: Path,
    own_modules: Sequence[str],
    *,
    weight_unit: str,
) -> tuple[dict[FunctionKey, tuple[Hotspot, SizePoint]], float]:
    """Read every project function of one saved profile.

    Parameters
    ----------
    folded : pathlib.Path
        The run's folded stacks, streamed once.
    root : pathlib.Path
        The project root frames are classified against.
    own_modules : sequence of str
        The run's ``--own-module`` values.
    weight_unit : str
        ``samples``, ``microseconds`` or ``unknown`` (counted as
        samples), to turn counts into samples.

    Returns
    -------
    functions : dict
        ``FunctionKey`` to the function and its `SizePoint`; the
        project's own functions with at least one sample, the entry and
        synthetic frames `perfproof.profilers.folded.rank` leaves out
        excluded.
    samples : float
        The profile's samples in the same unit.

    Raises
    ------
    perfproof.profilers.folded.FoldedStackError
        If the file is not folded stacks.
    OSError
        If it cannot be read.

    Notes
    -----
    One streaming pass, memory proportional to the distinct frames.
    """
    ranking = rank_file(
        folded,
        project_root=root,
        top_n=1,
        own_modules=own_modules,
        bar=0.0,
        bar_limit=MAX_PROJECT_FUNCTIONS,
    )
    scale = _MICROSECONDS_PER_SAMPLE if weight_unit == "microseconds" else 1.0
    total = ranking.samples / scale
    found: dict[FunctionKey, tuple[Hotspot, SizePoint]] = {}
    for h in ranking.above_bar:
        if h.total_fraction is None:
            continue
        found[function_key(h)] = (
            h,
            SizePoint(h.total_fraction, h.total_fraction * total),
        )
    return found, total


def _fit(
    sizes: Sequence[float], values: Sequence[float]
) -> ExponentFit | None:
    """Fit an exponent, or ``None`` below three distinct sizes."""
    if len(set(sizes)) < _FIT_MIN_SIZES:
        return None
    return fit_exponent(sizes, values)


def _fit_json(fit: ExponentFit) -> dict[str, JsonValue]:
    """Show a fit as JSON."""
    return {
        "exponent": fit.exponent,
        "low": fit.low,
        "high": fit.high,
        "r_squared": fit.r_squared,
        "n": fit.n,
    }


def analyse_growth(
    sizes: Sequence[int],
    walls: Sequence[float],
    tables: Sequence[Mapping[FunctionKey, tuple[Hotspot, SizePoint]]],
) -> GrowthResult:
    """Fit every function's cost against size and the workload's.

    Parameters
    ----------
    sizes : sequence of int
        Input size of each profile, positive; at least 3 distinct for
        any fit.
    walls : sequence of float
        Unprofiled wall time of each, in seconds, positive.
    tables : sequence of Mapping
        For each size, `project_functions`' first result.

    Returns
    -------
    GrowthResult
        ``functions`` entries have ``function``, ``file``, ``line``
        (at the largest size it was seen), ``frame_kind``, ``costs``
        (seconds per size, ``null`` where unseen), ``total_fractions``
        (``null`` likewise), ``samples`` (``null`` likewise),
        ``fit`` (``exponent``, ``low``, ``high``, ``r_squared``, ``n``;
        ``null`` when fewer than three sizes saw it),
        ``evidence`` (``shown``, ``lead`` or ``none`` as the module
        docstring defines; ``null`` without a fit or a workload fit),
        ``appears_at_larger_sizes`` and ``notes``.

    Raises
    ------
    ValueError
        If the three sequences differ in length or are empty, or a size
        or a wall time is not positive and finite.

    Notes
    -----
    Linear in the functions times the sizes.
    """
    if not (len(sizes) == len(walls) == len(tables)) or not sizes:
        msg = "sizes, walls and tables must be non-empty and equal in length"
        raise ValueError(msg)
    order = sorted(range(len(sizes)), key=sizes.__getitem__)
    ordered_sizes = [float(sizes[i]) for i in order]
    ordered_walls = [walls[i] for i in order]
    ordered = [tables[i] for i in order]
    workload = _fit(ordered_sizes, ordered_walls)
    keys = sorted(
        {k for t in ordered for k in t},
        key=lambda k: (k[0], k[1] or "", k[2]),
    )
    entries = [
        _entry(k, ordered_sizes, ordered_walls, ordered, workload)
        for k in keys
    ]
    entries.sort(key=_by_exponent)
    return GrowthResult(workload, tuple(entries))


def _by_exponent(entry: dict[str, JsonValue]) -> tuple[int, float, str]:
    """Order: fitted by exponent descending, then the unfitted."""
    fit = entry["fit"]
    if isinstance(fit, dict):
        exponent = fit["exponent"]
        value = float(exponent) if isinstance(exponent, int | float) else 0.0
        return (0, -value, str(entry["function"]))
    return (1, 0.0, str(entry["function"]))


def _entry(
    key: FunctionKey,
    sizes: Sequence[float],
    walls: Sequence[float],
    tables: Sequence[Mapping[FunctionKey, tuple[Hotspot, SizePoint]]],
    workload: ExponentFit | None,
) -> dict[str, JsonValue]:
    """Build one function's growth entry."""
    seen = [t.get(key) for t in tables]
    costs = [
        None if s is None else s[1].total_fraction * w
        for s, w in zip(seen, walls, strict=True)
    ]
    present = [i for i, s in enumerate(seen) if s is not None]
    last = seen[present[-1]]
    assert last is not None  # noqa: S101  # key came from some table
    hotspot = last[0]
    fitted = [(sizes[i], costs[i]) for i in present]
    fit = (
        _fit([x for x, _ in fitted], [c for _, c in fitted if c is not None])
        if all(c is not None and c > 0 for _, c in fitted)
        else None
    )
    notes: list[str] = []
    evidence: str | None = None
    appears = False
    if len(present) < len(sizes):
        notes.append("missing_sizes")
        first = present[0]
        appears = first > 0 and present == list(range(first, len(sizes)))
    if fit is None:
        notes.append("no_fit")
    else:
        if fit.n < MIN_POINTS:
            notes.append("few_points")
        if (
            fit.r_squared < MIN_R_SQUARED
            or fit.high - fit.low > MAX_INTERVAL_WIDTH
        ):
            notes.append("noisy")
        if workload is not None:
            if fit.low > workload.high:
                evidence = "shown"
            elif fit.exponent - workload.exponent >= LEAD_MARGIN:
                evidence = "lead"
                notes.append("interval_overlaps_workload")
            else:
                evidence = "none"
    if any(s is not None and s[1].samples < MIN_SAMPLES for s in seen):
        notes.append("few_samples")
    return {
        "function": hotspot.function,
        "file": hotspot.file,
        "line": hotspot.line,
        "frame_kind": hotspot.frame_kind,
        "costs": list[JsonValue](costs),
        "total_fractions": [
            None if s is None else s[1].total_fraction for s in seen
        ],
        "samples": [None if s is None else s[1].samples for s in seen],
        "fit": None if fit is None else _fit_json(fit),
        "evidence": evidence,
        "appears_at_larger_sizes": appears,
        "notes": list(notes),
    }


def workload_json(fit: ExponentFit | None) -> JsonValue:
    """Show the workload's fit as JSON, ``null`` when there is none.

    Parameters
    ----------
    fit : perfproof.bench.estimates.ExponentFit or None
        `GrowthResult.workload`.

    Returns
    -------
    JsonValue
        The fit's exponent, interval, ``r_squared`` and ``n``.
    """
    return None if fit is None else _fit_json(fit)
