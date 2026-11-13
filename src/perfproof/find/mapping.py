"""The map and the leads.

The map is what the user's workloads reach; the leads are what only the
``find-*`` probes reached.

Everything here reads saved profiles through ``profile show --rank``'s
own code. Three rankings at most: the user workloads (the map), the
``find-*`` probes (the lead candidates) and every name together (what
was never reached). With no user workload there is no user ranking and
the map is empty.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, cast

from perfproof.bench.spec import STATE_DIR
from perfproof.commands import CommandError, JsonValue
from perfproof.commands._profile_rank import rank
from perfproof.commands._profile_show import RESULT_FILE
from perfproof.profilers.rank import _is_test  # pyright: ignore[reportPrivateUsage]
from perfproof.schema import Lead, MapRow, Part

__all__ = [
    "MAP_ROWS",
    "MAX_LEADS",
    "Mapped",
    "Ranking",
    "build",
    "layer_of",
    "part_of",
]

MAX_LEADS: Final = 20
MAP_ROWS: Final = 50
_TOP: Final = 1_000_000
_WHOLE: Final = frozenset({"", "."})
_LANGUAGE_LAYERS: Final = frozenset(
    {
        "javascript",
        "typescript",
        "java",
        "kotlin",
        "csharp",
        "ruby",
    }
)

type Row = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class Ranking:
    """One ``--rank`` call's rows and never-sampled files."""

    functions: tuple[Row, ...]
    never_sampled: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Mapped:
    """What `build` returns.

    Attributes
    ----------
    map : tuple of MapRow
        The user workloads' ranking, at most `MAP_ROWS` rows.
    leads : tuple of Lead
        Function leads first, then file leads.
    not_reached : tuple of str
        Source files no saved profile sampled.
    user : Ranking or None
        The user ranking; ``None`` with no user workload profile.
    """

    map: tuple[MapRow, ...]
    leads: tuple[Lead, ...]
    not_reached: tuple[str, ...]
    user: Ranking | None


def _rank(root: Path, names: Sequence[str]) -> Ranking | None:
    """Rank ``names``; ``None`` when there is nothing to read."""
    if not names:
        return None
    try:
        out = rank(root, list(names), _TOP)
    except CommandError as exc:
        if exc.code == "not_found":
            return None
        raise
    functions = cast("list[Row]", out.result["functions"])
    coverage = cast("Row", out.result["coverage"])
    files = cast("list[str]", coverage["files"])
    return Ranking(tuple(functions), tuple(files))


def part_of(parts: Sequence[Part], file: str | None) -> str | None:
    """Return the id of the part with the longest root prefix of a file.

    ``None`` when the file is ``None`` or in no part.
    """
    if file is None:
        return None
    best: tuple[int, str] | None = None
    for part in parts:
        base = part.root.strip("/")
        if base in _WHOLE:
            size = 0
        elif file == base or file.startswith(f"{base}/"):
            size = len(base)
        else:
            continue
        if best is None or size > best[0]:
            best = (size, part.id)
    return None if best is None else best[1]


_EXTENSION_LAYERS: Final = {
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".cs": "csharp",
    ".rb": "ruby",
    ".py": "python",
    ".c": "native",
    ".cc": "native",
    ".cpp": "native",
    ".cxx": "native",
    ".h": "native",
    ".hpp": "native",
}
"""Layer a source file's extension names."""


def layer_of(
    frame_kind: str, part: Part | None, file: str | None = None
) -> str:
    """Return a map row's layer.

    Parameters
    ----------
    frame_kind : str
        ``Hotspot.frame_kind``.
    part : Part or None
        The part the frame's file is in.
    file : str, optional
        The frame's source file.

    Returns
    -------
    str
        ``other`` without a part; ``gpu`` for a GPU kernel; then the
        layer the file's extension names (``.js`` javascript, ``.ts``
        typescript, ``.go`` go, ``.rs`` rust, and so on); then by the
        frame kind and the part's language: a native frame is the
        part's language for Rust and Go and ``native`` otherwise, and a
        script frame is the part's language for a JavaScript, JVM, C#
        or Ruby part and ``python`` otherwise.
    """
    if part is None:
        return "other"
    if frame_kind == "gpu_kernel":
        return "gpu"
    if file is not None:
        named = _EXTENSION_LAYERS.get(PurePosixPath(file).suffix.lower())
        if named is not None:
            return named
    if frame_kind == "native":
        return part.language if part.language in ("rust", "go") else "native"
    if part.language in _LANGUAGE_LAYERS:
        return part.language
    return "python"


def distinct_tops(user: Ranking, names: Sequence[str]) -> int:
    """Count the distinct functions at the top of the rankings.

    Parameters
    ----------
    user : Ranking
        The user workloads' ranking.
    names : sequence of str
        The user workloads with a current profile.

    Returns
    -------
    int
        For each workload the first non-wrapper function in rank order
        that it saw; the number of distinct ones across workloads.
    """
    tops: set[tuple[object, object, object]] = set()
    for name in names:
        for row in user.functions:
            if row.get("wrapper") is True:
                continue
            if name in _benchmarks(row):
                tops.add(_key(row))
                break
    return len(tops)


def _key(row: Row) -> tuple[object, object, object]:
    return (row.get("function"), row.get("file"), row.get("frame_kind"))


def _benchmarks(row: Row) -> tuple[str, ...]:
    seen = cast("list[Row]", row.get("seen_in") or [])
    names: list[str] = []
    for entry in seen:
        name = entry.get("benchmark")
        if isinstance(name, str) and name not in names:
            names.append(name)
    return tuple(names)


def _number(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _map_rows(user: Ranking, parts: Sequence[Part]) -> tuple[MapRow, ...]:
    by_id = {p.id: p for p in parts}
    rows: list[MapRow] = []
    for row in user.functions:
        if row.get("wrapper") is True:
            continue
        file = cast("str | None", row.get("file"))
        part_id = part_of(parts, file)
        layer = layer_of(
            cast("str", row.get("frame_kind")),
            None if part_id is None else by_id[part_id],
            file,
        )
        rows.append(
            MapRow.model_validate(
                {
                    "function": row["function"],
                    "file": file,
                    "line": row.get("line"),
                    "part": part_id,
                    "layer": layer,
                    "cap": _number(row.get("cap")),
                    "total_fraction": _number(row.get("total_fraction")),
                    "seen_in": list(_benchmarks(row)),
                }
            )
        )
        if len(rows) == MAP_ROWS:
            break
    return tuple(rows)


def _frame(text: str, prefixes: Sequence[str]) -> tuple[str, str | None]:
    """Split a folded frame into its function and root-relative file."""
    head, sep, tail = text.rpartition(" (")
    if not (sep and tail.endswith(")")):
        return text, None
    file, _, line = tail[:-1].rpartition(":")
    path = file if line.isdigit() else tail[:-1]
    for prefix in prefixes:
        if path.startswith(prefix):
            return head, path[len(prefix) :]
    return head, path


def via_tests(
    root: Path,
    runs: Sequence[tuple[str, str]],
    wanted: set[tuple[str, str | None]],
) -> dict[tuple[str, str | None], str]:
    """Find the test frame above each wanted function.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    runs : sequence of (str, str)
        ``(spec name, run id)`` of the ``find-tests`` runs to read.
    wanted : set of (str, str or None)
        ``(function, file)`` of the leads.

    Returns
    -------
    dict
        For each lead that has one, ``"<file>::<function>"`` of the
        nearest caller frame in a test file, taking the one with the
        most weight across the stacks. One pass over each folded file.
    """
    weights: dict[tuple[tuple[str, str | None], str], float] = {}
    prefixes = tuple({f"{root}/", f"{root.resolve()}/"})
    for name, run_id in runs:
        directory = root / STATE_DIR / "profiles" / name / run_id
        try:
            saved: object = json.loads(
                (directory / RESULT_FILE).read_text(encoding="utf-8")
            )
            folded = cast("dict[str, object]", saved).get("folded")
            if not isinstance(folded, str):
                continue
            _scan(directory / Path(folded).name, wanted, weights, prefixes)
        except (OSError, ValueError):
            continue
    best: dict[tuple[str, str | None], tuple[float, str]] = {}
    for (lead, test), weight in sorted(weights.items()):
        if lead not in best or weight > best[lead][0]:
            best[lead] = (weight, test)
    return {lead: test for lead, (_, test) in best.items()}


def _scan(
    path: Path,
    wanted: set[tuple[str, str | None]],
    weights: dict[tuple[tuple[str, str | None], str], float],
    prefixes: Sequence[str],
) -> None:
    # Deep stacks repeat frames; parse each distinct frame text once.
    parsed: dict[str, tuple[str, str | None]] = {}
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            stack, _, count = line.rstrip("\n").rpartition(" ")
            try:
                weight = float(count)
            except ValueError:
                continue
            test_frame: str | None = None
            for text in stack.split(";"):
                frame = parsed.get(text)
                if frame is None:
                    frame = parsed[text] = _frame(text, prefixes)
                if test_frame is not None and frame in wanted:
                    key = (frame, test_frame)
                    weights[key] = weights.get(key, 0.0) + weight
                if frame[1] is not None and _is_test(frame[1]):
                    test_frame = f"{frame[1]}::{frame[0]}"


def build(
    root: Path,
    parts: Sequence[Part],
    *,
    user_names: Sequence[str],
    find_names: Sequence[str],
    test_runs: Sequence[tuple[str, str]],
) -> Mapped:
    """Compute the map, the leads and what was not reached.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    parts : sequence of Part
        The project's parts.
    user_names : sequence of str
        User workloads with a current profile; the map ranks exactly
        these, so no ``find-*`` run is in it.
    find_names : sequence of str
        ``find-*`` specs with a current profile.
    test_runs : sequence of (str, str)
        ``(name, run id)`` of ``find-tests`` runs, for ``via_test``.

    Returns
    -------
    Mapped
        The result. Reads each saved folded file once per ranking.
    """
    user = _rank(root, user_names)
    found = _rank(root, find_names)
    both = _rank(root, [*user_names, *find_names]) if user and found else None
    overall = both or user or found
    not_reached = () if overall is None else overall.never_sampled
    leads: list[Lead] = []
    if found is not None:
        seen: set[tuple[object, object, object]] = (
            set()
            if user is None
            else {_key(r) for r in user.functions if r.get("seen_in")}
        )
        picked = [
            r
            for r in found.functions
            if r.get("wrapper") is not True
            and _key(r) not in seen
            and not (
                isinstance(r.get("file"), str) and _is_test(str(r["file"]))
            )
        ][:MAX_LEADS]
        wanted = {
            (cast("str", r["function"]), cast("str | None", r.get("file")))
            for r in picked
        }
        tests = via_tests(root, test_runs, wanted) if wanted else {}
        for r in picked:
            file = cast("str | None", r.get("file"))
            leads.append(
                Lead.model_validate(
                    {
                        "kind": "function",
                        "name": r["function"],
                        "file": file,
                        "part": part_of(parts, file),
                        "cap": _number(r.get("cap")),
                        "seen_in": list(_benchmarks(r)),
                        "via_test": tests.get(
                            (cast("str", r["function"]), file)
                        ),
                    }
                )
            )
        if user is not None:
            sampled = {
                str(r["file"])
                for r in found.functions
                if isinstance(r.get("file"), str)
            }
            leads.extend(
                Lead.model_validate(
                    {
                        "kind": "file",
                        "name": PurePosixPath(file).name,
                        "file": file,
                        "part": part_of(parts, file),
                        "cap": None,
                        "seen_in": _file_seen(found, file),
                        "via_test": None,
                    }
                )
                for file in user.never_sampled
                if file in sampled and not _is_test(file)
            )
    return Mapped(
        map=() if user is None else _map_rows(user, parts),
        leads=tuple(leads),
        not_reached=not_reached,
        user=user,
    )


def _file_seen(found: Ranking, file: str) -> list[str]:
    names: list[str] = []
    for r in found.functions:
        if r.get("file") == file:
            for n in _benchmarks(r):
                if n not in names:
                    names.append(n)
    return names
