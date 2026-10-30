"""One ranking of project functions across saved profiles.

``perfproof profile show --rank`` reads every saved profile of some
benchmarks and answers two questions the single profile cannot:

- **Where is the biggest possible win?** Every project function seen in
  any profile is ranked by its **cap** at scale, ``1 / (1 - p)``
  (`perfproof.profilers.summary.amdahl_cap`), a bound on what a perfect
  fix of that function alone could give, never a measured effect. ``p``
  is the function's total share in the profile at its benchmark's
  largest measured size (a plain profile is its own largest; a function
  not sampled at that size has an unknown cap there, not a small one);
  across benchmarks the largest of these counts. Real inputs are at
  least as large as the test size, so a function that is costly only
  at small sizes does not outrank one that dominates at the largest.
  Nothing is projected beyond the measured sizes. The rule, fixed: that
  cap descending (a function that is a whole profile has no finite cap
  and comes first; unknown caps come last), then the number of profiles
  that saw the function (more first), then file, line and name. The
  highest cap in any profile stays in each row as ``best_any_size``,
  labelled informational. Each row also shows the function's self
  share, because a wrapper whose time is all in its callees (``main``)
  has a large cap and nothing of its own to fix.
- **Which source files did no profile ever sample?** `never_sampled`
  lists the project's source files that appear in no function of any
  saved profile. That says the saved workloads never ran them (or
  that their time was too small to be sampled); it is not line or
  branch coverage, and a file listed may still be tested.

Which files are "the project's source": the walk of
`perfproof.project.detect.walk_files` (VCS, virtualenv, build and cache
directories skipped, depth and file count bounded), files with a suffix
of a language the project has, minus test files (a ``test`` or
``tests`` directory, ``test_*``, ``*_test.*`` and ``conftest.py``). The
project model records target classes, not languages, so the suffixes
come from `suffixes_for`: a fixed table from the model's target classes
plus the suffixes of every project file a profile did sample.
"""

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Final

from perfproof.commands import JsonValue
from perfproof.profilers.growth import FunctionKey, SizePoint
from perfproof.profilers.summary import amdahl_cap
from perfproof.schema import Hotspot

__all__ = [
    "MAX_FILES_LISTED",
    "NeverSampled",
    "SavedRun",
    "never_sampled",
    "rank_functions",
    "suffixes_for",
]

_ANY_SIZE_LABEL: Final = (
    "highest cap in any saved profile, whatever its size; informational, "
    "not used to rank"
)
MAX_FILES_LISTED: Final = 5000
"""Most never-sampled files the JSON result lists."""
_PYTHON: Final = frozenset({".py", ".pyw"})
_NATIVE: Final = frozenset(
    {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".rs", ".go"}
)
_NODE: Final = frozenset({".js", ".mjs", ".cjs", ".ts", ".tsx"})
_JVM: Final = frozenset({".java", ".kt", ".scala"})
_BY_CLASS: Final[Mapping[str, frozenset[str]]] = {
    "python_cpu": _PYTHON,
    "web_service": _PYTHON,
    "llm_serving": _PYTHON,
    "agentic": _PYTHON,
    "native": _NATIVE,
    "node": _NODE,
    "jvm": _JVM,
    "gpu_kernel": _PYTHON | {".cu", ".cuh"},
}
_TEST_DIRS: Final = frozenset({"test", "tests"})


@dataclass(frozen=True, slots=True)
class SavedRun:
    """One saved profile, as ranking reads it.

    Attributes
    ----------
    benchmark, run_id : str
        The benchmark and the run's directory name.
    size : int or None
        Input size it was profiled at, ``None`` for a plain profile.
    factor : int or None
        The size's multiplier of the base, ``None`` likewise.
    min_effect : float or None
        The bar the run saved, ``None`` when it saved none.
    functions : Mapping
        `perfproof.profilers.growth.project_functions`' first result.
    """

    benchmark: str
    run_id: str
    size: int | None
    factor: int | None
    min_effect: float | None
    functions: Mapping[FunctionKey, tuple[Hotspot, SizePoint]]


@dataclass(frozen=True, slots=True)
class NeverSampled:
    """Source files no saved profile sampled.

    Attributes
    ----------
    source_files : int
        Project source files found, tests excluded.
    sampled : int
        Of them, files with a sampled function.
    files : tuple of str
        The others, sorted; at most `MAX_FILES_LISTED`.
    count : int
        How many others there are.
    by_suffix : dict of str to int
        Their count per suffix.
    truncated : bool
        Whether the file walk hit its bounds.
    """

    source_files: int
    sampled: int
    files: tuple[str, ...]
    count: int
    by_suffix: dict[str, int]
    truncated: bool


def _cap_key(cap: float | None) -> float:
    """Order a cap, a missing (infinite) one as the largest."""
    return float("inf") if cap is None else cap


def _largest(
    runs: Sequence[SavedRun],
) -> dict[tuple[str, bool], tuple[int | None, list[SavedRun]]]:
    """Pick, per benchmark, the profiles at its largest measured size.

    A benchmark's sized profiles (from ``--sizes``) and its plain ones
    are separate groups. A sized group keeps the profiles at its
    largest size (several when ``--sizes`` ran more than once); a plain
    group keeps every profile, each its own largest.
    """
    groups: dict[tuple[str, bool], list[SavedRun]] = {}
    for run in runs:
        groups.setdefault((run.benchmark, run.size is not None), []).append(
            run
        )
    out: dict[tuple[str, bool], tuple[int | None, list[SavedRun]]] = {}
    for key, members in groups.items():
        sizes = [r.size for r in members if r.size is not None]
        top = max(sizes) if sizes else None
        out[key] = (top, [r for r in members if r.size == top])
    return out


def _rank_cap(entry: tuple[SavedRun, Hotspot]) -> tuple[float, float]:
    """Order profiles of one function: larger cap, then self share."""
    return _cap_key(amdahl_cap(entry[1].total_fraction)), entry[
        1
    ].self_fraction


def rank_functions(
    runs: Sequence[SavedRun],
    growth: Mapping[str, Sequence[Mapping[str, JsonValue]]],
) -> list[dict[str, JsonValue]]:
    """Rank the project functions of saved profiles by cap at scale.

    Parameters
    ----------
    runs : sequence of SavedRun
        Every profile to rank across.
    growth : Mapping
        Per benchmark, the saved growth documents of its ``--sizes``
        runs (``profile`` writes ``growth-<group>.json``).

    Returns
    -------
    list of dict
        One row per function, in the order of the module's rule. A row
        has ``function``, ``file``, ``line``, ``frame_kind`` and:

        - ``cap``: the ranking cap, the largest over benchmarks of the
          function's cap in that benchmark's profile at its largest
          measured size (``null`` when that is a whole profile);
          ``cap_known`` is ``false`` when no benchmark that saw the
          function saw it at its largest size, and ``cap`` is then
          ``null`` too;
        - ``total_fraction`` and ``self_fraction`` (in the profile of
          that cap; ``null`` when unknown), ``cap_above_bar`` (against
          that run's bar; ``null`` when unknown or none saved) and
          ``ranked_in`` (benchmark, run and size of that profile);
        - ``at_largest``: for each benchmark that saw the function, the
          largest measured size, and ``seen``, ``run_id``,
          ``total_fraction`` and ``cap`` there (``seen`` false and the
          rest ``null`` when it was not sampled at that size: its cap
          there is unknown, not 1);
        - ``best_any_size``: the highest cap in any profile, for
          information only (``cap``, ``total_fraction``, benchmark,
          run, size, ``label``); never used to rank, since it can come
          from a size where the function was cheap or costly in a way
          real inputs are not;
        - ``seen_in`` (every profile that saw it: benchmark, run, size,
          factor, total share, cap);
        - ``growth`` (the fit of the benchmark's most-pointed
          ``--sizes`` run that saw it, with the workload's exponent;
          ``null`` without one);
        - ``ceiling_rises_with_size``: ``true`` only when the growth
          is **shown** (the interval of its exponent is above the
          workload's, `perfproof.profilers.growth`), ``false`` when the
          fit says it does not grow faster, ``null`` for a lead that is
          not shown, a function seen only at larger sizes, and a
          function no scaling run saw (``growth`` tells these apart);
        - ``wrapper``: ``true`` when the cap is known, not finite, and
          the function has no time of its own (an entry or driver frame
          such as ``main``).

    Notes
    -----
    Linear in the functions across all profiles and growth entries,
    plus a sort. Nothing is projected beyond the measured sizes.
    """
    rows: dict[FunctionKey, list[tuple[SavedRun, Hotspot]]] = {}
    for run in runs:
        for key, (hotspot, _) in run.functions.items():
            rows.setdefault(key, []).append((run, hotspot))
    largest = _largest(runs)
    index = _growth_index(growth)
    out: list[dict[str, JsonValue]] = []
    for key, seen in rows.items():
        at_largest: list[dict[str, JsonValue]] = []
        known: list[tuple[SavedRun, Hotspot]] = []
        for group in sorted(
            {(r.benchmark, r.size is not None) for r, _ in seen}
        ):
            top, top_runs = largest[group]
            here = [
                (r, r.functions[key][0])
                for r in top_runs
                if key in r.functions
            ]
            if here:
                pick = max(here, key=_rank_cap)
                known.append(pick)
                at_largest.append(
                    {
                        "benchmark": group[0],
                        "size": top,
                        "seen": True,
                        "run_id": pick[0].run_id,
                        "total_fraction": pick[1].total_fraction,
                        "cap": amdahl_cap(pick[1].total_fraction),
                    }
                )
            else:
                at_largest.append(
                    {
                        "benchmark": group[0],
                        "size": top,
                        "seen": False,
                        "run_id": None,
                        "total_fraction": None,
                        "cap": None,
                    }
                )
        any_run, any_hotspot = max(seen, key=_rank_cap)
        ranked_run, ranked = (
            max(known, key=_rank_cap) if known else (None, None)
        )
        shown = ranked or any_hotspot
        cap = None if ranked is None else amdahl_cap(ranked.total_fraction)
        bar = None if ranked_run is None else ranked_run.min_effect
        rising, fit = _growth(key, {r.benchmark for r, _ in seen}, index)
        out.append(
            {
                "function": shown.function,
                "file": shown.file,
                "line": shown.line,
                "frame_kind": shown.frame_kind,
                "cap": cap,
                "cap_known": ranked is not None,
                "total_fraction": (
                    None if ranked is None else ranked.total_fraction
                ),
                "self_fraction": (
                    None if ranked is None else ranked.self_fraction
                ),
                "wrapper": (
                    ranked is not None
                    and cap is None
                    and ranked.self_fraction == 0.0
                ),
                "cap_above_bar": (
                    None if bar is None else cap is None or cap > 1.0 + bar
                ),
                "ranked_in": None
                if ranked_run is None
                else _where(ranked_run),
                "at_largest": list[JsonValue](at_largest),
                "best_any_size": {
                    **_where(any_run),
                    "cap": amdahl_cap(any_hotspot.total_fraction),
                    "total_fraction": any_hotspot.total_fraction,
                    "label": _ANY_SIZE_LABEL,
                },
                "seen_in": [
                    {
                        **_where(r),
                        "total_fraction": h.total_fraction,
                        "cap": amdahl_cap(h.total_fraction),
                    }
                    for r, h in seen
                ],
                "growth": fit,
                "ceiling_rises_with_size": rising,
            }
        )
    out.sort(
        key=lambda e: (
            -(_cap_key(_number(e["cap"])) if e["cap_known"] else -1.0),
            -len(_list(e["seen_in"])),
            str(e["file"] or ""),
            _number(e["line"]) or 0,
            str(e["function"]),
        )
    )
    return out


def _number(value: JsonValue) -> float | None:
    """Read a JSON number, ``None`` for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _list(value: JsonValue) -> list[JsonValue]:
    """Read a JSON list, empty for anything else."""
    return value if isinstance(value, list) else []


def _where(run: SavedRun) -> dict[str, JsonValue]:
    """Name a profile: benchmark, run and the size it ran at."""
    return {
        "benchmark": run.benchmark,
        "run_id": run.run_id,
        "size": run.size,
        "factor": run.factor,
    }


GrowthIndex = dict[
    tuple[str, FunctionKey], list[tuple[int, str, dict[str, JsonValue]]]
]
"""Saved growth entries by benchmark and function."""
_GROWTH_KEYS: Final = frozenset(
    {
        "frame_kind",
        "file",
        "function",
        "fit",
        "evidence",
        "appears_at_larger_sizes",
        "notes",
    }
)


def _growth_index(
    growth: Mapping[str, Sequence[Mapping[str, JsonValue]]],
) -> GrowthIndex:
    """Index every saved growth entry once.

    An entry that lacks the keys is skipped, like an unreadable
    document.
    """
    index: GrowthIndex = {}
    for benchmark, documents in growth.items():
        for document in documents:
            workload = document.get("workload")
            for raw in _list(document.get("functions")):
                if not isinstance(raw, dict) or not raw.keys() >= _GROWTH_KEYS:
                    continue
                fit = raw["fit"]
                points = (
                    _number(fit.get("n")) if isinstance(fit, dict) else None
                )
                key = (
                    str(raw["frame_kind"]),
                    None if raw["file"] is None else str(raw["file"]),
                    str(raw["function"]),
                )
                index.setdefault((benchmark, key), []).append(
                    (
                        int(points or 0),
                        str(document.get("group")),
                        _compose(document, workload, raw),
                    )
                )
    return index


def _growth(
    key: FunctionKey,
    benchmarks: Collection[str],
    index: GrowthIndex,
) -> tuple[bool | None, JsonValue]:
    """Find a function's growth in the saved scaling runs."""
    found = [
        entry for b in sorted(benchmarks) for entry in index.get((b, key), ())
    ]
    if not found:
        return None, None
    shown = max(found, key=lambda e: (e[0], e[1]))[2]
    evidence = shown["evidence"]
    if evidence == "shown":
        return True, shown
    if evidence == "none":
        return False, shown
    return None, shown


def _compose(
    document: Mapping[str, JsonValue],
    workload: JsonValue,
    raw: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    """Show one function's growth with its run's workload exponent."""
    fit = raw["fit"]
    workload_exponent = (
        workload.get("exponent") if isinstance(workload, dict) else None
    )
    return {
        "group": document.get("group"),
        "sizes": document.get("sizes"),
        "exponent": fit.get("exponent") if isinstance(fit, dict) else None,
        "low": fit.get("low") if isinstance(fit, dict) else None,
        "high": fit.get("high") if isinstance(fit, dict) else None,
        "n": fit.get("n") if isinstance(fit, dict) else None,
        "workload_exponent": workload_exponent,
        "evidence": raw["evidence"],
        "appears_at_larger_sizes": raw["appears_at_larger_sizes"],
        "notes": raw["notes"],
    }


def suffixes_for(
    target_classes: Iterable[str], sampled_files: Iterable[str]
) -> frozenset[str]:
    """Return the source suffixes of the project's languages.

    Parameters
    ----------
    target_classes : iterable of str
        The project model's target classes.
    sampled_files : iterable of str
        Project files some profile sampled.

    Returns
    -------
    frozenset of str
        The class table's suffixes for each target class (Python for
        every class but ``native``, whose C, C++, Rust and Go suffixes
        stand in for it), plus the suffix of every sampled file.
    """
    suffixes: set[str] = set()
    for target in target_classes:
        suffixes |= _BY_CLASS.get(target, frozenset())
    suffixes |= {
        s for f in sampled_files if (s := PurePosixPath(f).suffix.lower())
    }
    return frozenset(suffixes)


def _is_test(path: str) -> bool:
    """Whether a root-relative path is a test file."""
    p = PurePosixPath(path)
    name = p.name
    return (
        not _TEST_DIRS.isdisjoint(p.parts[:-1])
        or name.startswith("test_")
        or p.stem.endswith("_test")
        or name == "conftest.py"
    )


def never_sampled(
    files: Sequence[str],
    truncated: bool,
    suffixes: Collection[str],
    sampled: Collection[str],
) -> NeverSampled:
    """List the source files no profile sampled.

    Parameters
    ----------
    files : sequence of str
        Root-relative paths, as `perfproof.project.detect.walk_files`
        returns them.
    truncated : bool
        Whether that walk was cut short.
    suffixes : collection of str
        Suffixes that count as source (`suffixes_for`).
    sampled : collection of str
        Root-relative files with at least one sampled project function.

    Returns
    -------
    NeverSampled
        Counts, and the files up to `MAX_FILES_LISTED`.
    """
    source = [
        f
        for f in files
        if PurePosixPath(f).suffix.lower() in suffixes and not _is_test(f)
    ]
    sampled_set = set(sampled)
    missing = [f for f in source if f not in sampled_set]
    by_suffix: dict[str, int] = {}
    for f in missing:
        s = PurePosixPath(f).suffix.lower()
        by_suffix[s] = by_suffix.get(s, 0) + 1
    return NeverSampled(
        source_files=len(source),
        sampled=len(source) - len(missing),
        files=tuple(missing[:MAX_FILES_LISTED]),
        count=len(missing),
        by_suffix=dict(sorted(by_suffix.items())),
        truncated=truncated,
    )
