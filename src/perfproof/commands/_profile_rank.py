"""``perfproof profile show --rank``: one ranking across saved profiles.

``profile show --rank [NAME...]`` reads every saved profile run,
``.perfproof/profiles/<name>/<id>/``, of the named benchmarks (every
benchmark with a saved profile when none is named) and ranks the
project's functions across all of them, by the rule in
`perfproof.profilers.rank`: cap at the largest measured size, then the
number of profiles that saw the function, then file, line and name.
Nothing is profiled or run: each run's folded stacks are streamed once.
It also lists the project's source files no saved profile sampled.

``--json`` result
-----------------
``benchmarks``
    The benchmarks read, sorted.
``runs``
    Each profile read: ``benchmark``, ``run_id``, ``size`` and
    ``factor`` (``null`` for a plain profile), ``samples`` (as
    `perfproof.profilers.growth.project_functions` counts them) and
    ``wall_seconds`` of its unprofiled run.
``skipped_runs``
    Runs that could not be read, each with ``benchmark``, ``run_id`` and
    the ``reason``. Never dropped silently.
``order``
    The sorting rule, in words.
``functions``
    Every ranked function, as `perfproof.profilers.rank.rank_functions`
    describes the rows. The text shows only the top ``--top``.
``coverage``
    ``label`` ("never sampled" is not coverage), ``target_classes``,
    ``suffixes`` and ``suffix_source``, ``source_files``, ``sampled``,
    ``never_sampled`` (the count), ``never_sampled_by_suffix``,
    ``files`` (the never-sampled files, at most
    `perfproof.profilers.rank.MAX_FILES_LISTED`), ``walk_truncated``
    and ``excluded`` (what is not counted as source).
``labels``
    What each kind of number is.

Error codes
-----------
``not_a_repository``, ``invalid_name``, ``not_found`` (no readable
saved profile for the benchmarks) and ``state_read_failed`` (the
profiles directory cannot be listed). A run whose ``result.json`` or
folded stacks cannot be read is in ``skipped_runs``, not an error.
"""

import json
from pathlib import Path
from typing import Final, cast

from perfproof.bench.spec import STATE_DIR, spec_record_path
from perfproof.commands import CommandError, JsonValue, Output
from perfproof.commands._profile_show import RESULT_FILE
from perfproof.profilers.folded import FoldedStackError
from perfproof.profilers.growth import project_functions
from perfproof.profilers.rank import (
    SavedRun,
    never_sampled,
    rank_functions,
    suffixes_for,
)
from perfproof.project.detect import walk_files
from perfproof.project.errors import ProjectError
from perfproof.project.store import load_model

__all__ = ["GROWTH_PREFIX", "rank"]

GROWTH_PREFIX: Final = "growth-"
_ZERO_HASH: Final = "0" * 64
_ORDER: Final = (
    "cap at the largest size measured, descending (the cap in each "
    "benchmark's profile at its largest size, the largest across "
    "benchmarks; a function that is a whole profile has no finite cap "
    "and comes first; a function not seen at the largest size has an "
    "unknown cap and comes last), then the number of profiles that saw "
    "it (more first), then file, line and name"
)
_EXCLUDED: Final = (
    "test files (a test or tests directory, test_*, *_test.*, "
    "conftest.py) and directories the project walk skips"
)
_LABELS: Final = {
    "cap": "bound at the largest size measured: 1 / (1 - total share); "
    "not a measured effect",
    "best_any_size": "informational: the highest cap in any saved profile; "
    "not used to rank",
    "share": "estimate: a share of a profile, not of a calibrated run",
    "growth": "estimate: a fit to a few profiles, one per size",
    "coverage": "never sampled by any saved profile; not line coverage",
}


def rank(root: Path, names: list[str], top: int) -> Output:
    """Rank project functions across saved profiles.

    Parameters
    ----------
    root : pathlib.Path
        Root of the repository holding ``.perfproof/``.
    names : list of str
        Benchmarks to read; every benchmark with a saved profile when
        empty.
    top : int
        Functions and files the text shows; the JSON has all.

    Returns
    -------
    Output
        The result described in this module's docstring.

    Raises
    ------
    CommandError
        With one of the codes in this module's docstring.
    """
    profiles = root / STATE_DIR / "profiles"
    benchmarks = sorted(set(names)) or _all_benchmarks(profiles)
    runs: list[SavedRun] = []
    meta: list[JsonValue] = []
    skipped: list[JsonValue] = []
    growth: dict[str, list[dict[str, JsonValue]]] = {}
    for name in benchmarks:
        try:
            spec_record_path(root, name, _ZERO_HASH)
        except ValueError as exc:
            raise CommandError(
                "invalid_name", str(exc), {"name": name}
            ) from exc
        directory = profiles / name
        growth[name] = _growth_documents(directory)
        for run_dir in _run_directories(directory):
            try:
                run, info = _read_run(root, name, run_dir)
            except (FoldedStackError, OSError, CommandError) as exc:
                skipped.append(
                    {
                        "benchmark": name,
                        "run_id": run_dir.name,
                        "reason": _reason(exc),
                    }
                )
                continue
            runs.append(run)
            meta.append(info)
    if not runs:
        which = ", ".join(benchmarks) or "any benchmark"
        msg = f"no readable saved profile for {which} under {profiles}"
        raise CommandError(
            "not_found",
            msg,
            {"path": str(profiles), "skipped_runs": skipped},
        )
    functions = rank_functions(runs, growth)
    sampled = {h.file for r in runs for h, _ in r.functions.values() if h.file}
    result: dict[str, JsonValue] = {
        "benchmarks": list[JsonValue](benchmarks),
        "runs": meta,
        "skipped_runs": skipped,
        "order": _ORDER,
        "functions": list[JsonValue](functions),
        "coverage": _coverage(root, sampled),
        "labels": dict(_LABELS),
    }
    return Output(result=result, text=_render(result, functions, top))


def _reason(exc: Exception) -> str:
    """Say why a run could not be read."""
    if isinstance(exc, CommandError):
        return exc.message
    if isinstance(exc, OSError):
        return f"{exc.filename or ''}: {exc.strerror or exc}".strip(": ")
    return f"the folded stacks are not readable: {exc}"


def _all_benchmarks(profiles: Path) -> list[str]:
    """Name every benchmark with a saved profile directory."""
    try:
        return sorted(p.name for p in profiles.iterdir() if p.is_dir())
    except FileNotFoundError:
        return []
    except OSError as exc:
        where = str(exc.filename or profiles)
        raise CommandError(
            "state_read_failed",
            f"{where}: {exc.strerror or exc}",
            {"path": where},
        ) from exc


def _run_directories(directory: Path) -> list[Path]:
    """List a benchmark's saved runs, oldest first."""
    try:
        return sorted(
            p
            for p in directory.iterdir()
            if p.is_dir() and (p / RESULT_FILE).is_file()
        )
    except FileNotFoundError:
        return []
    except OSError as exc:
        where = str(exc.filename or directory)
        raise CommandError(
            "state_read_failed",
            f"{where}: {exc.strerror or exc}",
            {"path": where},
        ) from exc


def _growth_documents(directory: Path) -> list[dict[str, JsonValue]]:
    """Read the growth documents ``profile --sizes`` saved."""
    out: list[dict[str, JsonValue]] = []
    try:
        paths = sorted(directory.glob(f"{GROWTH_PREFIX}*.json"))
    except OSError:
        return out
    for path in paths:
        try:
            document: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict):
            out.append(cast("dict[str, JsonValue]", document))
    return out


def _read_run(
    root: Path, name: str, run_dir: Path
) -> tuple[SavedRun, JsonValue]:
    """Read one saved run: its result and its folded stacks."""
    path = run_dir / RESULT_FILE
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        msg = f"{path} is not JSON: {exc}"
        raise CommandError("invalid_record", msg) from exc
    if not isinstance(document, dict):
        msg = f"{path} does not hold a profile result"
        raise CommandError("invalid_record", msg)
    saved = cast("dict[str, JsonValue]", document)
    folded = saved.get("folded")
    if not isinstance(folded, str) or not folded:
        raise CommandError("invalid_record", f"{path} has no 'folded'")
    own = saved.get("own_modules")
    own_modules = (
        [m for m in own if isinstance(m, str)] if isinstance(own, list) else []
    )
    unit = saved.get("weight_unit")
    functions, samples = project_functions(
        run_dir / Path(folded).name,
        root,
        own_modules,
        weight_unit=unit if isinstance(unit, str) else "unknown",
    )
    size = saved.get("size")
    value = size.get("value") if isinstance(size, dict) else None
    factor = size.get("factor") if isinstance(size, dict) else None
    bar = saved.get("min_effect")
    unprofiled = saved.get("unprofiled")
    wall = (
        unprofiled.get("wall_seconds")
        if isinstance(unprofiled, dict)
        else None
    )
    run = SavedRun(
        benchmark=name,
        run_id=run_dir.name,
        size=value if isinstance(value, int) else None,
        factor=factor if isinstance(factor, int) else None,
        min_effect=(
            float(bar)
            if isinstance(bar, int | float) and not isinstance(bar, bool)
            else None
        ),
        functions=functions,
    )
    info: JsonValue = {
        "benchmark": name,
        "run_id": run_dir.name,
        "size": run.size,
        "factor": run.factor,
        "samples": samples,
        "wall_seconds": wall,
    }
    return run, info


def _coverage(root: Path, sampled: set[str]) -> dict[str, JsonValue]:
    """List the source files no saved profile sampled."""
    targets: list[str] = []
    try:
        model = load_model(root)
    except ProjectError:
        model = None
    if model is not None:
        targets = sorted({t.target_class for t in model.targets})
    suffixes = suffixes_for(targets, sampled)
    files, truncated = walk_files(root)
    found = never_sampled(files, truncated, suffixes, sampled)
    return {
        "label": _LABELS["coverage"],
        "target_classes": list[JsonValue](targets),
        "suffixes": list[JsonValue](sorted(suffixes)),
        "suffix_source": (
            "target classes in the project model, plus the suffixes of "
            "sampled project files"
            if targets
            else "the suffixes of sampled project files (no project model)"
        ),
        "source_files": found.source_files,
        "sampled": found.sampled,
        "never_sampled": found.count,
        "never_sampled_by_suffix": dict[str, JsonValue](found.by_suffix),
        "files": list[JsonValue](found.files),
        "walk_truncated": found.truncated,
        "excluded": _EXCLUDED,
    }


def _render(
    result: dict[str, JsonValue],
    functions: list[dict[str, JsonValue]],
    top: int,
) -> str:
    """Render the ranking for a person."""
    runs = cast("list[JsonValue]", result["runs"])
    names = ", ".join(cast("list[str]", result["benchmarks"]))
    lines = [
        f"Ranked {len(functions)} project functions across {len(runs)} "
        f"saved profiles of {names}; by {_ORDER}."
    ]
    # Wrappers (no time of their own, no finite cap) go after the real
    # candidates here; the JSON keeps the rule's order and the flag.
    shown = [f for f in functions if not f["wrapper"]] + [
        f for f in functions if f["wrapper"]
    ]
    lines.extend(_row(n, f) for n, f in enumerate(shown[:top], start=1))
    if len(shown) > top:
        lines.append(f"{len(shown) - top} more in the JSON result.")
    skipped = cast("list[JsonValue]", result["skipped_runs"])
    if skipped:
        lines.append(f"{len(skipped)} saved runs could not be read.")
    coverage = cast("dict[str, JsonValue]", result["coverage"])
    never = cast("list[str]", coverage["files"])
    count = int(cast("int", coverage["never_sampled"]))
    lines.append(
        f"Never sampled: {count} of {coverage['source_files']} source "
        f"files ({', '.join(cast('list[str]', coverage['suffixes']))}) "
        "appear in no saved profile. That is not coverage: the saved "
        "workloads never ran them, or only for too little time to be "
        "sampled."
    )
    lines.extend(f"  {f}" for f in never[:top])
    if count > top:
        lines.append(f"  and {count - top} more")
    return "\n".join(lines)


def _row(number: int, f: dict[str, JsonValue]) -> str:
    """Render one ranked function in plain words."""
    place = str(f["file"] or "-")
    if f["file"] is not None and f["line"] is not None:
        place = f"{place}:{f['line']}"
    seen = len(cast("list[JsonValue]", f["seen_in"]))
    wrapper = (
        " (a wrapper: no time of its own, so look at what it calls)"
        if f["wrapper"]
        else ""
    )
    head = f"{number}. {f['function']}{wrapper} ({place}): "
    tail = f"seen in {seen} profile{'s' if seen != 1 else ''}."
    if f["cap_known"]:
        where = cast("dict[str, JsonValue]", f["ranked_in"])
        size = "" if where["size"] is None else f"size {where['size']} of "
        cap = f["cap"]
        cap_text = (
            "no finite cap"
            if cap is None
            else f"{float(cast('float', cap)):.2f}x"
        )
        total = float(cast("float", f["total_fraction"]))
        own = float(cast("float", f["self_fraction"]))
        text = (
            f"{head}cap at the largest size measured: {cap_text} "
            f"({size}{where['benchmark']}), {total:.1%} of the time in it "
            f"and its callees, {own:.1%} in itself; {tail}"
        )
    else:
        at = [
            x
            for x in cast("list[dict[str, JsonValue]]", f["at_largest"])
            if not x["seen"]
        ]
        misses = ", ".join(f"size {x['size']} of {x['benchmark']}" for x in at)
        any_cap = cast("dict[str, JsonValue]", f["best_any_size"])["cap"]
        shown = (
            "no finite cap"
            if any_cap is None
            else f"{float(cast('float', any_cap)):.2f}x"
        )
        text = (
            f"{head}cap at the largest size measured is unknown (not "
            f"sampled at {misses}; highest cap in any profile {shown}, "
            f"not used to rank); {tail}"
        )
    growth = f["growth"]
    if isinstance(growth, dict):
        text += _growth_words(growth, f["ceiling_rises_with_size"])
    return text


def _growth_words(growth: dict[str, JsonValue], rises: JsonValue) -> str:
    """Say what the growth fit shows."""
    notes = cast("list[str]", growth["notes"])
    caveat = f" (unreliable: {', '.join(notes)})" if notes else ""
    exponent, workload = growth["exponent"], growth["workload_exponent"]
    if exponent is None:
        if growth["appears_at_larger_sizes"]:
            return (
                " Seen only at the larger sizes: a lead that its share "
                f"grows with input size, not shown{caveat}."
            )
        return f" Growth not fitted: not seen at enough of the sizes{caveat}."
    fit = f"cost grows as size^{float(cast('float', exponent)):.2f}"
    if isinstance(workload, int | float):
        fit += f" against the workload's {float(workload):.2f}"
    if rises:
        return (
            f" Growth: {fit}, faster than the workload and shown at these "
            f"sizes, so its ceiling rises at bigger inputs{caveat}."
        )
    if growth["evidence"] == "lead":
        return (
            f" Growth: {fit}; a lead, not shown at these sizes, since the "
            f"intervals overlap{caveat}."
        )
    return f" Growth: {fit}, not faster than the workload{caveat}."
