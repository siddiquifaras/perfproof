"""``perfproof profile show``: look functions up in a saved profile.

The printed lists of ``perfproof profile`` hold the top frames. A
function the agent suspects from reading the code may not be among
them, and without its share the agent cannot compute its cap.
``profile show NAME [RUN] --function PATTERN`` reads a saved run,
``.perfproof/profiles/<name>/<id>/``, and answers for that function from
the run's folded stacks; nothing is profiled again. Without a run id
the latest run (the greatest directory name, which starts with its UTC
start time) that has a ``result.json`` is read; a run id is a directory
name or a unique prefix of one.

Matching is `perfproof.profilers.names.names_match`: the function's
last component must be equal, and an owner in the pattern
(``Echo.ServeHTTP``, ``Foo::bar``) must end the printed owner. Argument
lists, generics, import paths and hash suffixes are ignored, so
``createChildLogger`` finds ``createChildLogger`` whatever file it is
in.

``--json`` result
-----------------
``name``, ``run_id``, ``directory``, ``folded``
    The run read and the folded stacks searched.
``profiler``, ``basis``, ``weight_unit``
    As saved by the run: what the shares are shares of.
``pattern``
    The name asked for.
``samples``
    All samples of the profile, the denominator of every share.
``found``
    ``true`` when at least one function matched.
``match``, ``note``
    How the functions matched: ``exact`` (the rule above), ``file`` or
    ``bare_name``; ``null`` when none matched. A pattern with an owner
    (``kernels.fib``) that matches no frame exactly is tried with the
    owner as the frame's file stem (profilers often print a frame with
    no owner), then as the bare name (``fib``), so a function present
    under its bare name is never reported as absent. ``note`` says
    which, and is ``null`` for ``exact``.
``functions``
    The matching functions by total share then self share, descending,
    at most `perfproof.profilers.query.MAX_MATCHES`. Each has
    ``function``, ``file``, ``line``, ``frame_kind``, ``is_library``,
    ``self_fraction``, ``total_fraction``, ``self_samples``,
    ``total_samples``, ``cap`` (the bound ``1 / (1 - total_fraction)``,
    never a measured effect; ``null`` when the function is the whole
    profile), ``seen`` (``true``), and ``cap_above_bar`` when the run
    saved its bar (``true`` for a ``null`` cap). When nothing matched
    it holds one entry for the pattern with zero shares and counts,
    ``seen`` false and the reason: not an error.
``functions_omitted``
    Matches beyond the list's limit.
``reason``
    ``null``, or why nothing matched.
``min_effect``
    The run's bar as a fraction, or ``null`` when the run did not save
    it.
``labels``
    What each kind of number is.

Error codes
-----------
``not_a_repository``, ``invalid_name``, ``invalid_argument`` (a run id
that is not a directory name, or no ``--function``), ``invalid_pattern``
(a name that names no function), ``not_found`` (no saved run, or none
with that id), ``ambiguous_run`` (a prefix of several runs),
``invalid_record`` (``result.json`` is not a profile result),
``state_read_failed`` and ``invalid_profile`` (the folded stacks cannot
be read).
"""

import json
import re
from pathlib import Path
from typing import Final, cast

from perfproof.bench.spec import STATE_DIR, spec_record_path
from perfproof.commands import CommandError, JsonValue, Output
from perfproof.profilers.folded import FoldedStackError
from perfproof.profilers.names import short_name
from perfproof.profilers.query import FunctionMatch, query_file
from perfproof.profilers.summary import amdahl_cap

__all__ = ["RESULT_FILE", "show"]

RESULT_FILE: Final = "result.json"
_RUN_ID: Final = re.compile(r"[0-9A-Za-z-]+")
_ZERO_HASH: Final = "0" * 64
_LABELS: Final = {
    "share": "estimate: a share of the profile, not of a calibrated run",
    "cap": "bound: 1 / (1 - total share); not a measured effect",
}


def show(
    root: Path, name: str, run_id: str | None, function: str | None
) -> Output:
    """Answer ``profile show`` for one saved run.

    Parameters
    ----------
    root : pathlib.Path
        Root of the repository holding ``.perfproof/``.
    name : str
        Benchmark name.
    run_id : str or None
        A run directory name or unique prefix; ``None`` for the latest.
    function : str or None
        The function to look up; required.

    Returns
    -------
    Output
        The result described in this module's docstring.

    Raises
    ------
    CommandError
        With one of the codes in this module's docstring.
    """
    if function is None:
        msg = "profile show needs --function PATTERN"
        raise CommandError("invalid_argument", msg)
    try:
        spec_record_path(root, name, _ZERO_HASH)
    except ValueError as exc:
        raise CommandError("invalid_name", str(exc), {"name": name}) from exc
    if short_name(function) is None:
        msg = f"{function!r} does not name a function"
        raise CommandError("invalid_pattern", msg, {"pattern": function})
    directory = _run_directory(root / STATE_DIR / "profiles" / name, run_id)
    saved = _read_result(directory / RESULT_FILE)
    folded = directory / Path(_text(saved, "folded")).name
    own = saved.get("own_modules", [])
    own_modules = (
        [m for m in cast("list[object]", own) if isinstance(m, str)]
        if isinstance(own, list)
        else []
    )
    try:
        query = query_file(
            folded,
            project_root=root,
            pattern=function,
            own_modules=own_modules,
        )
    except FoldedStackError as exc:
        msg = f"{folded} is not folded stacks: {exc}"
        raise CommandError("invalid_profile", msg) from exc
    except OSError as exc:
        where = str(exc.filename or folded)
        raise CommandError(
            "state_read_failed",
            f"{where}: {exc.strerror or exc}",
            {"path": where},
        ) from exc
    bar = saved.get("min_effect")
    min_effect = (
        float(bar)
        if isinstance(bar, int | float) and not isinstance(bar, bool)
        else None
    )
    reason = None
    if query.matches:
        entries = [_entry(m, min_effect) for m in query.matches]
    else:
        reason = (
            f"not seen in this profile: none of its {query.samples} "
            f"samples has a frame whose function matches {function!r}; "
            "the workload may not run it, or the profiler may not show it "
            "(inlined or unsymbolised)"
        )
        entries = [_absent(function, reason, min_effect)]
    result: dict[str, JsonValue] = {
        "name": name,
        "run_id": directory.name,
        "directory": str(directory),
        "folded": str(folded),
        "profiler": saved.get("profiler"),
        "basis": saved.get("basis"),
        "weight_unit": saved.get("weight_unit"),
        "pattern": function,
        "samples": query.samples,
        "found": bool(query.matches),
        "match": query.match,
        "note": query.note,
        "functions": entries,
        "functions_omitted": query.omitted,
        "reason": reason,
        "min_effect": min_effect,
        "labels": dict(_LABELS),
    }
    return Output(result=result, text=_render(result, entries))


def _run_directory(profiles: Path, run_id: str | None) -> Path:
    """Pick the latest run directory, or the one ``run_id`` names."""
    if run_id is not None and not _RUN_ID.fullmatch(run_id):
        msg = f"{run_id!r} is not a profile run id"
        raise CommandError("invalid_argument", msg, {"run_id": run_id})
    try:
        runs = (
            sorted(
                p.name
                for p in profiles.iterdir()
                if (p / RESULT_FILE).is_file()
            )
            if profiles.is_dir()
            else []
        )
    except OSError as exc:
        where = str(exc.filename or profiles)
        raise CommandError(
            "state_read_failed",
            f"{where}: {exc.strerror or exc}",
            {"path": where},
        ) from exc
    if run_id is not None:
        runs = [r for r in runs if r == run_id] or [
            r for r in runs if r.startswith(run_id)
        ]
    if not runs:
        which = "" if run_id is None else f" matching {run_id!r}"
        msg = f"no saved profile run{which} under {profiles}"
        raise CommandError("not_found", msg, {"path": str(profiles)})
    if run_id is not None and len(runs) > 1:
        shown = cast("list[JsonValue]", runs)
        msg = f"{len(runs)} profile runs match {run_id!r}: choose one"
        raise CommandError("ambiguous_run", msg, {"matches": shown})
    return profiles / runs[-1]


def _read_result(path: Path) -> dict[str, JsonValue]:
    """Read a run's ``result.json``."""
    try:
        document: object = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        where = str(exc.filename or path)
        raise CommandError(
            "state_read_failed",
            f"{where}: {exc.strerror or exc}",
            {"path": where},
        ) from exc
    except ValueError as exc:
        msg = f"{path} is not JSON: {exc}"
        raise CommandError("invalid_record", msg, {"path": str(path)}) from exc
    if not isinstance(document, dict):
        msg = f"{path} does not hold a profile result"
        raise CommandError("invalid_record", msg, {"path": str(path)})
    return cast("dict[str, JsonValue]", document)


def _text(saved: dict[str, JsonValue], key: str) -> str:
    """Return a string member of a saved result, or fail the record."""
    value = saved.get(key)
    if not isinstance(value, str) or not value:
        msg = f"the saved result has no {key!r}"
        raise CommandError("invalid_record", msg)
    return value


def _entry(match: FunctionMatch, min_effect: float | None) -> JsonValue:
    """Describe one matching function with its cap."""
    h = match.hotspot
    cap = amdahl_cap(h.total_fraction)
    entry: dict[str, JsonValue] = {
        "function": h.function,
        "file": h.file,
        "line": h.line,
        "frame_kind": h.frame_kind,
        "is_library": h.is_library,
        "self_fraction": h.self_fraction,
        "total_fraction": h.total_fraction,
        "self_samples": match.self_samples,
        "total_samples": match.total_samples,
        "cap": cap,
        "seen": True,
    }
    if min_effect is not None:
        entry["cap_above_bar"] = cap is None or cap > 1.0 + min_effect
    return entry


def _absent(function: str, reason: str, min_effect: float | None) -> JsonValue:
    """Describe a function with no samples: share 0, and why."""
    entry: dict[str, JsonValue] = {
        "function": function,
        "file": None,
        "line": None,
        "frame_kind": None,
        "is_library": None,
        "self_fraction": 0.0,
        "total_fraction": 0.0,
        "self_samples": 0,
        "total_samples": 0,
        "cap": 1.0,
        "seen": False,
        "reason": reason,
    }
    if min_effect is not None:
        entry["cap_above_bar"] = False
    return entry


def _render(result: dict[str, JsonValue], entries: list[JsonValue]) -> str:
    """Render the lookup for a person."""
    lines = [
        f"Profile run {result['run_id']} of {result['name']}: "
        f"{result['pattern']!r} ({result['samples']} samples).",
    ]
    if not result["found"]:
        lines.append(str(result["reason"]))
        return "\n".join(lines)
    lines.append("   self  total  samples  cap     location  function")
    for raw in entries:
        e = cast("dict[str, JsonValue]", raw)
        place = "-" if e["file"] is None else str(e["file"])
        if e["file"] is not None and e["line"] is not None:
            place = f"{place}:{e['line']}"
        cap = e["cap"]
        shown = "inf" if cap is None else f"{float(cast('float', cap)):.2f}x"
        library = "  (library)" if e["is_library"] else ""
        lines.append(
            f"  {float(cast('float', e['self_fraction'])):5.1%} "
            f"{float(cast('float', e['total_fraction'])):6.1%} "
            f"{e['total_samples']!s:>8}  {shown:>6}  {place}  "
            f"{e['function']}{library}"
        )
    omitted = result["functions_omitted"]
    if omitted:
        lines.append(f"{omitted} more matches not shown.")
    return "\n".join(lines)
