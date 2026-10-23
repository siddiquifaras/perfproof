"""Read a saved task directory: stream events, tool calls, final text.

The files are what ``runner.py`` wrote: ``turn-N.jsonl`` holds one
``claude -p --output-format stream-json`` event per line, with the
unscored noise removed. Nothing here runs anything.
"""

from __future__ import annotations

import ast
import json
import posixpath
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NamedTuple, cast

Event = dict[str, Any]
"""One stream-json event."""

_QUOTE = r"""["']"""


def as_dict(value: object) -> dict[str, Any] | None:
    """Narrow parsed JSON to an object, or ``None``.

    Parameters
    ----------
    value : object
        Anything ``json.loads`` returned.

    Returns
    -------
    dict or None
        ``value`` if it is a JSON object, else ``None``.
    """
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def as_list(value: object) -> list[Any]:
    """Narrow parsed JSON to an array, empty if it is not one.

    Parameters
    ----------
    value : object
        Anything ``json.loads`` returned.

    Returns
    -------
    list
        ``value`` if it is a JSON array, else an empty list.
    """
    return cast("list[Any]", value) if isinstance(value, list) else []


@dataclass(frozen=True)
class ToolCall:
    """A tool call and what came back.

    Attributes
    ----------
    name : str
        Tool name, such as ``Bash`` or ``Skill``.
    input : dict
        The call's arguments.
    output : str
        The result text, empty if none was recorded.
    """

    name: str
    input: dict[str, Any]
    output: str


@dataclass(frozen=True)
class Turn:
    """One ``claude`` call of a task.

    Attributes
    ----------
    events : list of dict
        Stream events in order.
    result : dict or None
        The ``result`` event, ``None`` if the call was killed.
    """

    events: list[Event]
    result: Event | None


@dataclass(frozen=True)
class SeenVerdict:
    """A verdict read from the text of a compare's output.

    Attributes
    ----------
    outcome : str
        ``KEEP``, ``REJECT`` or ``INCONCLUSIVE``.
    reason : str or None
        The reason, when printed.
    gate : str or None
        The gate, when printed and not null.
    calibration : str or None
        ``calibrated`` or ``uncalibrated``, when printed.
    """

    outcome: str
    reason: str | None
    gate: str | None
    calibration: str | None


def load_turns(task_dir: Path) -> list[Turn]:
    """Read every ``turn-N.jsonl`` of a task, in turn order.

    Parameters
    ----------
    task_dir : Path
        A task directory of a run.

    Returns
    -------
    list of Turn
        One per call; empty if there are no transcripts.
    """
    files = sorted(
        task_dir.glob("turn-*.jsonl"),
        key=lambda p: int(p.stem.split("-")[1]),
    )
    turns: list[Turn] = []
    for path in files:
        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parsed = as_dict(json.loads(line))
            if parsed is not None:
                events.append(parsed)
        results = [e for e in events if e.get("type") == "result"]
        turns.append(Turn(events, results[-1] if results else None))
    return turns


def _blocks(event: Event) -> list[dict[str, Any]]:
    message = as_dict(event.get("message"))
    if message is None:
        return []
    blocks = (as_dict(b) for b in as_list(message.get("content")))
    return [b for b in blocks if b is not None]


def assistant_texts(turns: list[Turn]) -> list[str]:
    """List every text block the assistant wrote, in order.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.

    Returns
    -------
    list of str
        What the user could read, tool calls excluded.
    """
    texts: list[str] = []
    for turn in turns:
        for event in turn.events:
            if event.get("type") != "assistant":
                continue
            for block in _blocks(event):
                text = block.get("text")
                if block.get("type") == "text" and isinstance(text, str):
                    texts.append(text)
    return texts


def final_text(turns: list[Turn]) -> str:
    """Return the last message the user saw.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.

    Returns
    -------
    str
        The ``result`` text of the last call that has one, else the
        last assistant text, else an empty string.
    """
    for turn in reversed(turns):
        if turn.result is not None:
            text = turn.result.get("result")
            if isinstance(text, str) and text.strip():
                return text
    texts = assistant_texts(turns)
    return texts[-1] if texts else ""


def _result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    parts = (as_dict(part) for part in as_list(content))
    return "\n".join(str(p.get("text", "")) for p in parts if p is not None)


def tool_calls(turns: list[Turn]) -> list[ToolCall]:
    """Pair every tool call with its result.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.

    Returns
    -------
    list of ToolCall
        In call order; a call with no recorded result has empty output.
    """
    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    order: list[str] = []
    outputs: dict[str, str] = {}
    for turn in turns:
        for event in turn.events:
            kind = event.get("type")
            for block in _blocks(event):
                if kind == "assistant" and block.get("type") == "tool_use":
                    ident = str(block.get("id"))
                    args = block.get("input")
                    pending[ident] = (
                        str(block.get("name")),
                        args if isinstance(args, dict) else {},
                    )
                    order.append(ident)
                elif kind == "user" and block.get("type") == "tool_result":
                    outputs[str(block.get("tool_use_id"))] = _result_text(
                        block
                    )
    return [
        ToolCall(pending[i][0], pending[i][1], outputs.get(i, ""))
        for i in order
    ]


def skill_started(turns: list[Turn]) -> bool:
    """Whether the perfproof skill was invoked through the Skill tool.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.

    Returns
    -------
    bool
        True if a ``Skill`` call named ``perfproof`` appears. Prompts
        that start with ``/perfproof`` never show one, because the
        client expands them before the model runs; the scorer treats
        those as started by construction.
    """
    return any(
        call.name == "Skill"
        and str(call.input.get("skill", "")).split(":")[-1] == "perfproof"
        for call in tool_calls(turns)
    )


def _field(text: str, key: str) -> str | None:
    """Read one string-or-null field of the printed verdict.

    The ``gates`` list also has ``gate`` keys, each followed by a
    ``status``; those are not the verdict's gate and are skipped.
    """
    value = rf"({_QUOTE}([A-Za-z_]+){_QUOTE}|null|None)"
    pattern = rf"{_QUOTE}{key}{_QUOTE}\s*:\s*{value}"
    for match in re.finditer(pattern, text):
        after = text[match.end() : match.end() + 24]
        if key == "gate" and re.match(r"\s*,\s*[\"']status", after):
            continue
        return match.group(2)
    return None


def seen_verdicts(turns: list[Turn]) -> list[SeenVerdict]:
    """Read verdicts from the output of each ``perfproof compare``.

    The agent often filters the JSON through ``python``, ``grep`` or
    ``jq``, so this reads the ``outcome``, ``reason``, ``gate`` and
    ``calibration`` fields by pattern, with either quote style, rather
    than parsing a whole document.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.

    Returns
    -------
    list of SeenVerdict
        One per compare output that printed an ``outcome``, in order.
    """
    found: list[SeenVerdict] = []
    for call in tool_calls(turns):
        command = str(call.input.get("command", ""))
        if call.name != "Bash" or "compare" not in command:
            continue
        at = call.output.find("verdict")
        text = call.output if at < 0 else call.output[at:]
        outcome = _field(text, "outcome")
        if outcome not in {"KEEP", "REJECT", "INCONCLUSIVE"}:
            continue
        found.append(
            SeenVerdict(
                outcome=outcome,
                reason=_field(text, "reason"),
                gate=_field(text, "gate"),
                calibration=_field(text, "calibration"),
            )
        )
    return found


class RankedFrame(NamedTuple):
    """A function a profile printed, with what was printed beside it.

    Attributes
    ----------
    name : str
        The function exactly as printed (qualified, with arguments or
        hash, as the profiler gave it).
    file : str or None
        The source file printed beside it, ``None`` when none was.
    total : float
        Its total (inclusive) share, as printed.
    source : {"self", "total"} or None
        The list it was printed under: ``"self"`` for ``hotspots``,
        ``"total"`` for ``frames_by_total``, ``None`` when no section
        named either (it then ranks by total share, as one list).
    rank : int
        Its place in that list, from 1, in the order printed (for
        ``None``: by total share).
    """

    name: str
    file: str | None
    total: float
    source: Literal["self", "total"] | None = None
    rank: int = 0


_FrameKey = tuple[str, str | None, Literal["self", "total"] | None]
_SECTION_SOURCE: dict[str, Literal["self", "total"]] = {
    "hotspots": "self",
    "frames_by_total": "total",
    "self": "self",
    "total": "total",
}
# The list's name as a JSON or dict key, ...
_KEY = re.compile(r"(?P<q>[\"'])(?P<word>hotspots|frames_by_total)(?P=q)\s*:")
# ... or as the whole of a line, bar punctuation, a table border and a
# trailing colon. A word inside a path or a function name is neither.
_HEADING = re.compile(
    r"^[ \t|#*=>_-]*(?:result\.)?[`\"']?(?P<word>hotspots|frames_by_total)"
    r"[`\"']?(?:\[\])?[ \t:|]*$",
    re.MULTILINE,
)
# The titles of the CLI's own text output (``perfproof profile``
# without ``--json``), one per list.
_TITLE = re.compile(r"Top frames by (?P<word>self|total) time")
_BARE_NAME = re.compile(r"^\s*[A-Za-z_]\w*\s*$")


def _ranked_hotspots(text: str) -> list[str]:
    """List the functions printed, largest ``total_fraction`` first."""
    names: list[str] = []
    for frame in ranked_frames(text):
        if frame.name not in names:
            names.append(frame.name)
    return names


def _share(body: str, key: str) -> float | None:
    match = re.search(rf"{_QUOTE}{key}{_QUOTE}\s*:\s*([0-9.eE+-]+)", body)
    if match is None:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _objects(text: str) -> list[tuple[int, str, str | None, float]]:
    """Read printed JSON or dict objects: offset, name, file, total."""
    found: list[tuple[int, str, str | None, float]] = []
    for chunk in re.finditer(r"\{[^{}]*\}", text):
        body = chunk.group(0)
        func = re.search(
            rf"{_QUOTE}function{_QUOTE}\s*:\s*({_QUOTE})([^'\"]+)\1", body
        )
        where = re.search(
            rf"{_QUOTE}file{_QUOTE}\s*:\s*({_QUOTE})([^'\"]*)\1", body
        )
        total = _share(body, "total_fraction")
        if func is None or total is None:
            continue
        if where is not None and where.group(2).startswith("<"):
            continue
        if func.group(2).startswith("<"):
            continue
        file = None if where is None else where.group(2) or None
        found.append((chunk.start(), func.group(2), file, total))
    return found


def _sections(text: str) -> list[tuple[int, Literal["self", "total"]]]:
    """Find where each printed list starts.

    A start is one of the CLI's titles (``Top frames by self time``,
    ``Top frames by total time``), the list's name as a JSON or dict key
    (``"hotspots":``), or a line that is only the name (with
    punctuation, a table border or a trailing colon). The word inside a
    path, a function name or a list of names is not a start, and a line
    that holds a frame is never one. A bare name on a line of its own
    followed by another bare name is a list of key names, not a
    heading. A heading may have other lines (underline, column header,
    note) before its first row. A bare name that ends a one-per-line
    list of key names cannot be told from a heading and counts as one.
    """
    found: list[tuple[int, Literal["self", "total"]]] = [
        (m.start(), _SECTION_SOURCE[m["word"]]) for m in _TITLE.finditer(text)
    ]
    found.extend(
        (m.start(), _SECTION_SOURCE[m["word"]]) for m in _KEY.finditer(text)
    )
    for m in _HEADING.finditer(text):
        end = m.end()
        if end < len(text) and _BARE_NAME.match(_next_line(text, end + 1)):
            continue
        found.append((m.start("word"), _SECTION_SOURCE[m["word"]]))
    return sorted(found)


def _next_line(text: str, start: int) -> str:
    """Return the first non-blank line at or after ``start``."""
    while start < len(text):
        end = text.find("\n", start)
        line = text[start : len(text) if end < 0 else end]
        if line.strip():
            return line
        if end < 0:
            break
        start = end + 1
    return ""


def ranked_frames(text: str) -> list[RankedFrame]:
    """List the frames printed, largest ``total_fraction`` first.

    Reads each printed object (JSON or a Python dict repr) that has a
    ``function`` and a ``total_fraction``, and skips interpreter frames
    (files in angle brackets) and code-object pseudo-functions such as
    ``<module>``, whose total share covers the whole script and so
    outranks every real function once printed. Output with no objects
    is read as one frame per line (see `_hotspot_lines`).

    The CLI's titles, or the word ``hotspots`` or ``frames_by_total``
    used as a JSON key or heading (see `_sections`), start a section:
    the frames after it, up to the next, came from that list, and each
    is ranked by its place in the order printed. Frames before any such
    start form a single list ranked by total share. A function printed
    more than once in a list (several lines of one function) is one
    frame, at its first place and largest total.
    """
    entries = _objects(text)
    if not entries:
        entries = _hotspot_lines(text)
    sections = _sections(text)
    kept: dict[_FrameKey, tuple[float, int]] = {}
    order: dict[Literal["self", "total"] | None, int] = {}
    for at, name, file, total in entries:
        source: Literal["self", "total"] | None = None
        for start, kind in sections:
            if start < at:
                source = kind
        key = (name, file, source)
        if key in kept:
            first, place = kept[key]
            kept[key] = (max(first, total), place)
            continue
        order[source] = order.get(source, 0) + 1
        kept[key] = (total, order[source])
    frames = [
        RankedFrame(name, file, total, source, place)
        for (name, file, source), (total, place) in kept.items()
    ]
    frames.sort(key=lambda frame: -frame.total)
    rank = 0
    for at, frame in enumerate(frames):
        if frame.source is None:
            rank += 1
            frames[at] = frame._replace(rank=rank)
    return frames


def window_frames(frames: list[RankedFrame], top_n: int) -> list[RankedFrame]:
    """Take the frames within the first ``top_n`` of a printed list.

    A profile has two lists, ``hotspots`` (by self share) and
    ``frames_by_total`` (by total share), and an agent prints either or
    both. A frame is in the window if it is among the first ``top_n``
    of the list it was printed under, in the order printed. A frame
    from one list is never ranked by the other's share. Frames printed
    under neither name rank by total share. For ``top_n`` of 1 (the
    small examples) the window is the one frame with the largest total
    share, as before.

    Parameters
    ----------
    frames : list of RankedFrame
        As returned by `ranked_frames`.
    top_n : int
        The window size.

    Returns
    -------
    list of RankedFrame
        The frames in the window, largest total share first.
    """
    if top_n == 1:
        return frames[:1]
    return [frame for frame in frames if frame.rank <= top_n]


_SHARE = r"(?:0?\.\d+|1\.0+|1)"
_HOTSPOT_LINE = re.compile(
    rf"^\s*([A-Za-z_][\w.]*)\s+(\S+)(?:\s+\S+)*?\s+({_SHARE})\s*$"
)
# ``function file line self total`` with a function that has spaces or
# punctuation of its own: a C++ argument list, a Go
# ``(*Echo).ServeHTTP``, a macOS ``image`symbol``. Read from the right
# so the function is whatever is left of the file.
_FIELD_LINE = re.compile(
    rf"^\s*(?P<func>\S.*?)\s+(?P<file>\S+)\s+(?:\d+|None|null|-)"
    rf"(?:\s+{_SHARE})?\s+(?P<share>{_SHARE})\s*$"
)
_NO_FILE = frozenset({"None", "null", "-"})
# A row of the CLI text tables: ``  53.4%  53.4%  file:line  function``
# (``-`` for no location, a total left blank when unknown, and a
# trailing ``(library)`` mark).
_CLI_ROW = re.compile(
    r"^\s+(?P<own>\d+(?:\.\d+)?)%\s+(?:(?P<total>\d+(?:\.\d+)?)%\s+)?"
    r"(?P<where>\S+)  (?P<func>\S.*?)(?:  \(library\))?\s*$"
)
# Python's interpreter "files" contain a space (``<frozen
# importlib._bootstrap>``), so they must be recognised before the
# function is read as everything left of the file.
_INTERPRETER_FILE = re.compile(r"\s<(?:frozen|built-in|string|stdin)\b")


def _hotspot_lines(text: str) -> list[tuple[int, str, str | None, float]]:
    """Read hotspots printed one per line: function, file, ..., share.

    Agents often print ``result.hotspots[]`` compactly, for example
    ``rolling_sum rolling.py 51 0.906``, or as the skill documents,
    ``function file line self_fraction total_fraction``. The share is
    the last field (the total, when two are printed). Lines whose file
    or function is an interpreter frame (angle brackets) are skipped,
    as in the JSON reading. A function that is not a plain identifier
    is read too, so long as the line has that shape.

    Returns
    -------
    list of tuple
        Each line's offset in ``text``, function, file and share.
    """
    found: list[tuple[int, str, str | None, float]] = []
    for line in re.finditer(r"^.*$", text, re.MULTILINE):
        cli = _CLI_ROW.match(line.group(0))
        if cli is not None:
            where = cli["where"]
            file = None if where == "-" else where
            if file is not None and file.rpartition(":")[2].isdigit():
                file = file.rpartition(":")[0]
            if (file or "").startswith("<") or cli["func"].startswith("<"):
                continue
            own = float(cli["own"])
            share = (float(cli["total"]) if cli["total"] else own) / 100
            if max(own, share * 100) > 100:
                continue
            found.append((line.start(), cli["func"], file, share))
            continue
        if _INTERPRETER_FILE.search(line.group(0)):
            continue
        wide = _FIELD_LINE.match(line.group(0))
        if wide is not None:
            name, file, share = wide["func"], wide["file"], wide["share"]
        else:
            match = _HOTSPOT_LINE.match(line.group(0))
            if match is None:
                continue
            name, file, share = match.group(1), match.group(2), match.group(3)
        if file.startswith("<") or name.startswith("<"):
            continue
        found.append(
            (
                line.start(),
                name,
                None if file in _NO_FILE else file,
                float(share),
            )
        )
    return found


def profile_evidence(turns: list[Turn]) -> tuple[str | None, list[str]]:
    """Find the first ``perfproof profile`` call and what followed it.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.

    Returns
    -------
    tuple of (str or None, list of str)
        The function that call's output ranks first, or ``None`` if no
        profile call, or no hotspots in its output, were recorded; and
        every assistant text written after the call.
    """
    ranked, after = profile_ranking(turns)
    return (ranked[0] if ranked else None), after


def profile_ranking(turns: list[Turn]) -> tuple[list[str], list[str]]:
    """Find the first ``perfproof profile`` call and rank its output.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.

    Returns
    -------
    tuple of (list of str, list of str)
        The functions that call's output prints, largest share first
        (empty if no profile call, or no hotspots in its output, were
        recorded); and every assistant text written after the call.
    """
    top: list[str] | None = None
    after: list[str] = []
    ident: str | None = None
    for turn in turns:
        for event in turn.events:
            kind = event.get("type")
            for block in _blocks(event):
                btype = block.get("type")
                if kind == "assistant" and btype == "tool_use":
                    args = as_dict(block.get("input")) or {}
                    command = str(args.get("command", ""))
                    if ident is None and "perfproof profile" in command:
                        ident = str(block.get("id"))
                elif kind == "assistant" and btype == "text":
                    if ident is not None:
                        after.append(str(block.get("text", "")))
                elif (
                    kind == "user"
                    and btype == "tool_result"
                    and ident is not None
                    and top is None
                    and str(block.get("tool_use_id")) == ident
                ):
                    top = _ranked_hotspots(_result_text(block))
    return (top or []), after


_EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
_SCRATCH_DIRS = (".perfproof", ".claude")
_SHARED_PREFIXES = ("/tmp/", "/private/", "/var/", "/dev/")  # noqa: S108
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")
_WRITE_OPEN = re.compile(
    r"open\(\s*(['\"])(?P<path>[^'\"]+)\1\s*,\s*['\"][rbt]*[wax+][bt+]*['\"]"
)
_WRITE_PATH = re.compile(
    r"Path\(\s*(['\"])(?P<path>[^'\"]+)\1\s*\)\s*\.write_(?:text|bytes)\("
)
_PY_WRITES = re.compile(
    r"open\([^)]*,\s*['\"][rbt]*[wax+][bt+]*['\"]|\.write_(?:text|bytes)\("
)
_PY_STRING = re.compile(r"['\"]([\w./-]+\.\w+)['\"]")
_GIT_EDIT = re.compile(
    r"\bgit\s+(?:commit|apply|cherry-pick|merge|rebase|am|pull|restore)"
    r"(?![-\w])"
    r"|\bgit\s+stash\s+(?:pop|apply)\b"
    r"|\bgit\s+reset\s+[^|;&]*--hard\b"
    r"|\bgit\s+checkout\s+[^|;&]*?\s--\s+\S"
)
# Runs that only report: a dry run, a patch check, a staged restore.
_GIT_NO_EDIT = re.compile(
    r"\bgit\s+commit\b[^|;&]*\s--dry-run\b"
    r"|\bgit\s+apply\b(?![^|;&]*\s--apply\b)[^|;&]*"
    r"\s--(?:check|stat|numstat|summary)\b"
    r"|\bgit\s+restore\b(?![^|;&]*\s--worktree\b)[^|;&]*\s--staged\b"
)
_GIT_CHECKOUT = re.compile(r"\bgit\s+checkout\s+([^|;&]*)")
_VALUE_OPTS: dict[tuple[str, ...], frozenset[str]] = {
    ("profile",): frozenset(
        {
            "--spec-hash",
            "--profiler",
            "--profiler-command",
            "--top",
            "--timeout",
            "--hypothesis",
            "--own-module",
            "--repo",
        }
    ),
    ("bench", "define"): frozenset({"--spec", "--repo"}),
}
_SED_IN_PLACE = re.compile(r"-[A-Za-z]*i\S*|--in-place(?:=.*)?")
_PERL_IN_PLACE = re.compile(r"-[pnalw0-9]*i\S*")


def _segments(command: str) -> list[str]:
    """Split a shell command into simple commands.

    Splits on ``;``, ``&``, ``&&``, ``|``, ``||`` and newlines outside
    quotes, and keeps a here-document's body with the command that
    opened it, so text inside quotes or a body never reads as a command.
    """
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    heredocs: list[str] = []
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            elif ch == "\\" and quote == '"' and i + 1 < n:
                i += 1
                buf.append(command[i])
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "\\" and i + 1 < n:
            buf.append(ch)
            i += 1
            buf.append(command[i])
        elif ch == "&" and i > 0 and command[i - 1] in "<>":
            buf.append(ch)  # a descriptor copy such as ``2>&1``
        elif ch == "\n" or ch in ";&|":
            if ch == "\n" and heredocs:
                tag = heredocs.pop(0)
                end = re.compile(rf"^\s*{re.escape(tag)}\s*$", re.M)
                found = end.search(command, i + 1)
                stop = found.end() if found else n
                buf.append(command[i:stop])
                i = stop
                continue
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
            if command.startswith("<<", i) and not command.startswith(
                "<<<", i
            ):
                found = _HEREDOC.match(command, i)
                if found:
                    heredocs.append(found.group(2))
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _words(segment: str) -> list[str]:
    try:
        words = shlex.split(segment, posix=True)
    except ValueError:
        words = segment.split()
    while words and (
        re.fullmatch(r"\w+=\S*", words[0]) or words[0] in {"(", "{", "time"}
    ):
        words = words[1:]
    return [w.lstrip("({") for w in words] if words else words


def _is_project_path(path: str, root: str | None) -> bool:
    """Tell whether a path belongs to the project."""
    path = path.strip().strip("'\"")
    if not path or path == "-" or path.startswith(("&", "~", "$")):
        return False
    path = posixpath.normpath(path)
    if path == "." or path == ".." or path.startswith("../"):
        return False
    if path.startswith("/"):
        if root is not None:
            if path != root and not path.startswith(root + "/"):
                return False
            path = path[len(root) :].lstrip("/")
        elif path.startswith(_SHARED_PREFIXES) or any(
            f"/{d}/" in path for d in _SCRATCH_DIRS
        ):
            return False
    return bool(path) and path.split("/")[0] not in _SCRATCH_DIRS


def _sed_files(words: list[str]) -> list[str]:
    """Return the file arguments of a ``sed -i`` command."""
    rest = words[1:]
    files: list[str] = []
    script_seen = False
    skip = False
    for k, word in enumerate(rest):
        if skip:
            skip = False
        elif word in {"-e", "-f"}:
            script_seen = True
            skip = True
        elif re.fullmatch(r"-[A-Za-z]*i", word) and k + 1 < len(rest):
            # BSD sed takes the backup suffix as the next word.
            if rest[k + 1] == "":
                skip = True
        elif word.startswith("-"):
            continue
        elif not script_seen:
            script_seen = True
        else:
            files.append(word)
    return files


def _redirect_targets(line: str) -> list[str]:
    """Return the targets of the ``>`` and ``>>`` redirections.

    Quotes are respected, so ``echo 'a > b'`` has none, and ``x>f`` with
    no spaces is read; ``>&2`` duplicates a descriptor and has no file.
    """
    targets: list[str] = []
    quote = ""
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "\\":
            i += 1
        elif ch == ">":
            j = i + 1
            if j < n and line[j] in ">|":
                j += 1
            if j < n and line[j] == "&":
                i = j + 1
                continue
            while j < n and line[j] in " \t":
                j += 1
            buf: list[str] = []
            inner = ""
            while j < n:
                c = line[j]
                if inner:
                    if c == inner:
                        inner = ""
                    else:
                        buf.append(c)
                elif c in "'\"":
                    inner = c
                elif c == "\\" and j + 1 < n:
                    j += 1
                    buf.append(line[j])
                elif c in " \t;|&<>()":
                    break
                else:
                    buf.append(c)
                j += 1
            targets.append("".join(buf))
            i = j
            continue
        i += 1
    return targets


def _git_edit(line: str) -> bool:
    """Tell whether a command line changes files through ``git``."""
    if _GIT_NO_EDIT.search(line):
        return False
    if _GIT_EDIT.search(line):
        return True
    found = _GIT_CHECKOUT.search(line)
    if found is None:
        return False
    rest = found.group(1).split()
    if any(
        w in {"-b", "-B", "-c", "-C", "--orphan", "--detach", "-d"}
        for w in rest
    ):
        return False
    # ``git checkout <path>`` restores a file; a branch has no suffix.
    return any(
        w == "." or re.search(r"\.[A-Za-z]\w*$", w)
        for w in rest
        if w[:1] != "-"
    )


def _perfproof_args(words: list[str]) -> list[str] | None:
    """Return a simple command's words after ``perfproof``, or ``None``.

    Strips a ``uv run`` or ``uvx --from <pkg>`` prefix. Redirections are
    dropped, so ``> out.json`` is not read as an argument.
    """
    if words[:2] == ["uv", "run"]:
        words = words[2:]
    elif words[:2] == ["uvx", "--from"]:
        words = words[3:]
    if not words or words[0] != "perfproof":
        return None
    rest: list[str] = []
    skip = False
    for word in words[1:]:
        if skip:
            skip = False
        elif re.fullmatch(r"\d*>>?", word):
            skip = True
        elif not re.match(r"\d*>", word):
            rest.append(word)
    return rest


def _positionals(words: list[str], value_opts: frozenset[str]) -> list[str]:
    """Return the arguments that are not options or their values."""
    out: list[str] = []
    skip = False
    ended = False
    for word in words:
        if skip:
            skip = False
        elif ended:
            out.append(word)
        elif word == "--":
            ended = True
        elif word.startswith("--"):
            skip = word in value_opts
        elif word.startswith("-") and len(word) > 1:
            continue
        else:
            out.append(word)
    return out


def _subcommand_positionals(
    segment: str, command: tuple[str, ...]
) -> list[str] | None:
    """Return the positionals of ``perfproof <command>`` or ``None``."""
    args = _perfproof_args(_words(segment))
    if args is None or tuple(args[: len(command)]) != command:
        return None
    return _positionals(args[len(command) :], _VALUE_OPTS[command])


# A script is read only when it imports nothing but these modules and
# calls nothing but these names: anything else (``os``, ``shutil``,
# ``subprocess``, ``importlib``, ``exec``, ``globals``, an alias of a
# builtin) can write, rename or rebind by a route the syntax tree does
# not show, so the caller falls back to the pattern match.
_SAFE_MODULES = frozenset({"json", "pathlib", "re", "textwrap"})
_SAFE_FUNCTIONS = frozenset(
    {
        "open",
        "Path",
        "PurePath",
        "PosixPath",
        "print",
        "len",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "set",
        "tuple",
        "sorted",
        "range",
        "enumerate",
        "zip",
        "min",
        "max",
        "sum",
        "any",
        "all",
        "isinstance",
        "repr",
    }
)
_SAFE_METHODS = frozenset(
    {
        "read",
        "write",
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "open",
        "exists",
        "mkdir",
        "replace",
        "format",
        "join",
        "split",
        "splitlines",
        "strip",
        "rstrip",
        "lstrip",
        "startswith",
        "endswith",
        "lower",
        "upper",
        "count",
        "find",
        "index",
        "append",
        "extend",
        "insert",
        "pop",
        "items",
        "keys",
        "values",
        "get",
        "setdefault",
        "sub",
        "search",
        "match",
        "fullmatch",
        "findall",
        "dump",
        "dumps",
        "load",
        "loads",
        "dedent",
        "indent",
    }
)
_PATH_CLASSES = frozenset({"Path", "PurePath", "PosixPath"})


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _literal_paths(
    node: ast.expr, names: dict[str, list[str] | None]
) -> list[str] | None:
    """Resolve a path expression to the strings it can only be."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if (
        isinstance(node, ast.Call)
        and _call_name(node) in _PATH_CLASSES
        and len(node.args) == 1
        and not node.keywords
    ):
        return _literal_paths(node.args[0], names)
    return None


def _write_mode(call: ast.Call, position: int) -> bool | None:
    """Tell whether ``open`` writes; ``None`` if that is unknown."""
    mode: ast.expr | None = None
    if len(call.args) > position:
        mode = call.args[position]
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return any(c in mode.value for c in "wax+")
    return None


def _reads_stdin_program(first_line: str) -> bool:
    """Tell whether Python reads its program from the one here-document.

    True for ``python3 - <<EOF`` and ``python3 <<EOF`` (options that
    take no program, such as ``-I``, allowed). False for ``-c``, ``-m``,
    a script file, a second here-document or a here-string.
    """
    if len(_HEREDOC.findall(first_line)) != 1 or "<<<" in first_line:
        return False
    words = _words(first_line.split("<<", 1)[0])
    if not words or not words[0].rsplit("/", 1)[-1].startswith(
        ("python", "pypy")
    ):
        return False
    for word in words[1:]:
        if word == "-":
            continue
        if word.startswith("-") and not word.startswith("--"):
            if re.search(r"[cm]", word):
                return False
        elif word.startswith("--") or not word.startswith("-"):
            return False
    return True


_REFLECTIVE_NAMES = frozenset(
    {"modules", "f_globals", "f_locals", "f_back", "_getframe"}
)


def _is_plain(node: ast.AST) -> bool:
    """Whether a node imports or calls only what the reader trusts."""
    if isinstance(node, ast.Import):
        return all(a.name.split(".")[0] in _SAFE_MODULES for a in node.names)
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] in _SAFE_MODULES
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name):
            return func.id in _SAFE_FUNCTIONS
        if isinstance(func, ast.Attribute):
            return func.attr in _SAFE_METHODS
        return False
    return True


_WRITERS = frozenset(
    {"open", "write", "write_text", "write_bytes", "replace", "rename"}
)


def _shadows_or_passes_a_writer(nodes: list[ast.AST]) -> bool:
    """Whether a listed name may not mean what the reader assumes.

    The reader trusts a call by its name and finds writes only in calls
    named for them, so a script that rebinds a listed name (an
    assignment, ``def``, ``class``, parameter or import alias) or takes
    a writing method without calling it (``sorted(key=p.write_text)``)
    is not trusted.
    """
    listed = _SAFE_FUNCTIONS | _SAFE_METHODS
    called = {id(n.func) for n in nodes if isinstance(n, ast.Call)}

    def bound(node: ast.AST) -> str | None:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            return node.id
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            return node.name
        if isinstance(node, ast.arg):
            return node.arg
        if isinstance(node, ast.alias):
            return node.asname
        return None

    return any(
        bound(node) in listed
        or (
            isinstance(node, ast.Attribute)
            and node.attr in _WRITERS
            and id(node) not in called
        )
        for node in nodes
    )


def _rebinds_by_reflection(node: ast.AST) -> bool:
    """Whether a node can reach the script's names by another route.

    A dunder or frame name (``sys.modules[...].p``, ``__main__.p``,
    ``g.__dict__["p"]``, ``sys._getframe().f_globals``) can rebind a
    name without a plain assignment, so a script holding one is not
    trusted and falls back to the pattern match.
    """
    if isinstance(node, ast.Name):
        ident = node.id
    elif isinstance(node, ast.Attribute):
        ident = node.attr
    elif isinstance(node, ast.alias):
        ident = node.name.split(".")[0]
    else:
        return False
    return ident.startswith("__") or ident in _REFLECTIVE_NAMES


def _python_write_targets(segment: str) -> list[str] | None:
    """List the files a here-document's Python script writes.

    Parameters
    ----------
    segment : str
        One simple command with the here-document that feeds a Python
        interpreter, as ``_segments`` keeps them.

    Returns
    -------
    list of str or None
        The literal paths that ``open(..., "w")``,
        ``Path(...).write_text`` and ``Path(...).write_bytes`` write,
        following a variable that only ever holds string literals.
        ``None`` when that cannot be shown: the command is not exactly
        one here-document read by Python from standard input, the
        script does not parse (or is too deep to), a path is computed,
        a name is bound in a way that is not a literal assignment, or a
        call can run text, reach names by string or start another
        program. The caller then falls back to the pattern match. That
        match over-counts some scripts and, as before, misses writes
        by other routes (``os.rename``, ``shutil.copy``, an aliased
        ``open``, ``exec(open().read())``); this function does not
        claim to close those.
    """
    first, _, rest = segment.partition("\n")
    found = _HEREDOC.search(first)
    if found is None or not _reads_stdin_program(first):
        return None
    end = re.compile(rf"^\s*{re.escape(found.group(2))}\s*$", re.M)
    closing = end.search(rest)
    body = rest[: closing.start()] if closing else rest
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    names: dict[str, list[str] | None] = {}
    nodes = list(ast.walk(tree))
    if _shadows_or_passes_a_writer(nodes):
        return None
    for node in nodes:
        if not _is_plain(node):
            return None
        if _rebinds_by_reflection(node):
            return None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    # No alias chasing: walk order is not source order.
                    value = _literal_paths(node.value, {})
                    old = names.get(target.id, [])
                    names[target.id] = (
                        None if value is None or old is None else old + value
                    )
    # A name is trusted only if every way it is bound is a plain
    # assignment of a literal: a loop target, an annotated or augmented
    # assignment, unpacking, a parameter, ``with ... as``, an import or
    # a walrus rebinds it to something unseen.
    plain: dict[str, int] = {}
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    plain[target.id] = plain.get(target.id, 0) + 1
    stored: dict[str, int] = {}
    for node in nodes:
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            stored[node.id] = stored.get(node.id, 0) + 1
        elif isinstance(node, ast.arg):
            names[node.arg] = None
        elif isinstance(node, ast.alias):
            names[(node.asname or node.name).split(".")[0]] = None
        elif isinstance(
            node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar
        ) and getattr(node, "name", None):
            names[str(node.name)] = None
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names[node.rest] = None
        elif isinstance(node, ast.Global | ast.Nonlocal):
            for ident in node.names:
                names[ident] = None
    for ident, count in stored.items():
        if plain.get(ident, 0) != count:
            names[ident] = None
    paths: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        func = node.func
        target: ast.expr | None = None
        if name == "open" and isinstance(func, ast.Name):
            writing = _write_mode(node, 1)
            if writing is None or not node.args:
                return None
            target = node.args[0] if writing else None
        elif name == "open" and isinstance(func, ast.Attribute):
            # ``Path(...).open(mode)``; any other receiver (``io``,
            # ``codecs``, ``builtins``) takes the path first: unknown.
            if not (
                isinstance(func.value, ast.Call)
                and _call_name(func.value) in _PATH_CLASSES
            ):
                return None
            writing = _write_mode(node, 0)
            if writing is None:
                return None
            target = func.value if writing else None
        elif name in {"write_text", "write_bytes"} and isinstance(
            func, ast.Attribute
        ):
            target = func.value
        if target is None:
            continue
        got = _literal_paths(target, names)
        if got is None:
            return None
        paths += got
    return paths


def _segment_edits(segment: str, root: str | None) -> bool:
    # Redirections and options are read from the command line itself; a
    # here-document body (the rest) is only the python rule's text.
    line = segment.split("\n", 1)[0]
    words = _words(line)
    if not words:
        return False
    head = words[0].rsplit("/", 1)[-1]
    targets: list[str] = []
    if _perfproof_args(words) is None:
        targets += _redirect_targets(line)
    args = [w for w in words[1:] if not w.startswith("-")]
    if head == "tee":
        targets += args
    elif head in {"cp", "mv", "install"} and len(args) >= 2:
        targets.append(args[-1])
    elif head == "sed" and any(_SED_IN_PLACE.fullmatch(w) for w in words[1:]):
        targets += _sed_files(words)
    elif head == "perl" and any(
        _PERL_IN_PLACE.fullmatch(w) for w in words[1:]
    ):
        targets += args[1:]
    elif head == "patch":
        targets += [a for a in args if "." in a] or ["patch"]
    if any(_is_project_path(t, root) for t in targets):
        return True
    if _git_edit(line):
        return True
    if head.startswith(("python", "pypy")) or "python" in segment[:40]:
        proven = _python_write_targets(segment)
        if proven is not None:
            return any(_is_project_path(p, root) for p in proven)
        paths = [m.group("path") for m in _WRITE_OPEN.finditer(segment)]
        paths += [m.group("path") for m in _WRITE_PATH.finditer(segment)]
        if not paths and _PY_WRITES.search(segment):
            paths = _PY_STRING.findall(segment) or ["unknown"]
        return any(_is_project_path(p, root) for p in paths)
    return False


def is_code_edit(
    name: str, args: dict[str, Any], root: str | None = None
) -> bool:
    """Tell whether a tool call changes the project's code.

    Parameters
    ----------
    name : str
        The tool's name.
    args : dict
        Its arguments.
    root : str, optional
        The project's root directory, when known (see
        ``infer_project_root``). With it, an absolute path outside the
        root is not the project's, and ``.perfproof`` and ``.claude``
        are scratch only as the root's own top-level directories.

    Returns
    -------
    bool
        True for ``Write``, ``Edit``, ``MultiEdit`` and ``NotebookEdit``
        on a project path; and for a ``Bash`` command with a simple
        command that redirects or ``tee``s into a project path, ``cp``s
        or ``mv``s onto one, edits in place (``sed -i`` judged by its
        file arguments, ``perl -pi``, ``patch``), opens a project file
        for writing from ``python``, or runs ``git commit``, ``apply``,
        ``cherry-pick``, ``merge``, ``stash pop`` or ``apply``, or
        ``checkout <ref> -- <file>``. Opening a branch alone and writing
        a new file under ``.perfproof/`` are not edits. The Bash
        patterns are best effort: an edit made some way not listed is
        not seen.
    """
    if name in _EDIT_TOOLS:
        path = str(args.get("file_path") or args.get("notebook_path") or "")
        return _is_project_path(path, root)
    if name == "Bash":
        return any(
            _segment_edits(s, root)
            for s in _segments(str(args.get("command", "")))
        )
    return False


def infer_project_root(turns: list[Turn], task_id: str) -> str | None:
    """Find the task repo's path in what the agent saw and did.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.
    task_id : str
        The task's id; runs keep each repo at ``.../repos/<task id>``.

    Returns
    -------
    str or None
        The first such path, or ``None`` if none appears.
    """
    pattern = re.compile(
        r"(/[^\s\"'\\]*/repos/" + re.escape(task_id) + r")(?=[/\s\"'\\]|$)"
    )
    for turn in turns:
        for event in turn.events:
            for block in _blocks(event):
                text = json.dumps(block.get("input", "")) + _result_text(block)
                found = pattern.search(text)
                if found:
                    return found.group(1)
    return None


def profile_targets(command: str) -> list[str]:
    """List the benchmarks a command asks ``perfproof`` to profile.

    Parameters
    ----------
    command : str
        A Bash command.

    Returns
    -------
    list of str
        The benchmark name of each simple command that starts with
        ``perfproof profile`` (after environment assignments or a
        ``uvx --from`` / ``uv run`` prefix), skipping options and the
        values of those that take one. Text inside quotes or a
        here-document is not a command.
    """
    found: list[str] = []
    for segment in _segments(command):
        rest = _subcommand_positionals(segment, ("profile",))
        if rest is not None:
            found.append(rest[0].rstrip(")};") if rest else "")
    return found


def _defined_benchmarks(command: str) -> list[str]:
    names: list[str] = []
    for segment in _segments(command):
        rest = _subcommand_positionals(segment, ("bench", "define"))
        if rest:
            names.append(rest[0])
    return names


def profile_before_edit(
    turns: list[Turn], root: str | None = None
) -> tuple[list[RankedFrame], list[str], str]:
    """Find the first successful ``perfproof profile`` before any edit.

    A call counts as a profile call only if one of its simple commands
    starts with ``perfproof profile`` (so ``echo 'perfproof profile'``
    does not), and, once the run has defined benchmarks with ``perfproof
    bench define``, only if it profiles one of them. It succeeded if its
    output has hotspots that parse and did not report a non-zero exit. A
    failed attempt followed by a successful one still counts. A success
    in the same command as, or after, the first detected edit of the
    project's code does not count (detection is pattern based, see
    ``is_code_edit``).

    Parameters
    ----------
    turns : list of Turn
        The task's calls.
    root : str, optional
        The project's root, for ``is_code_edit``.

    Returns
    -------
    tuple of (list of RankedFrame, list of str, str)
        The frames that profile printed, largest share first, and
        every assistant text written after it; and a reason when there
        is none: ``"none"`` (no successful profile at all) or
        ``"after_edit"`` (one, but only after the first edit).
    """
    edited = False
    defined: set[str] = set()
    calls: dict[str, tuple[bool, list[str], frozenset[str]]] = {}
    ranked: list[RankedFrame] | None = None
    late = False
    after: list[str] = []
    for turn in turns:
        for event in turn.events:
            kind = event.get("type")
            for block in _blocks(event):
                btype = block.get("type")
                if kind == "assistant" and btype == "tool_use":
                    args = as_dict(block.get("input")) or {}
                    name = str(block.get("name", ""))
                    if is_code_edit(name, args, root):
                        edited = True
                    command = (
                        str(args.get("command", "")) if name == "Bash" else ""
                    )
                    defined.update(_defined_benchmarks(command))
                    calls[str(block.get("id"))] = (
                        edited,
                        profile_targets(command),
                        frozenset(defined),
                    )
                elif kind == "assistant" and btype == "text":
                    if ranked is not None:
                        after.append(str(block.get("text", "")))
                elif kind == "user" and btype == "tool_result":
                    was_edited, targets, known = calls.get(
                        str(block.get("tool_use_id")), (False, [], frozenset())
                    )
                    if not targets or ranked is not None:
                        continue
                    if known and not any(t in known for t in targets):
                        continue
                    text = _result_text(block)
                    found = ranked_frames(text)
                    if not found or text.lstrip().startswith("Exit code"):
                        continue
                    if was_edited:
                        late = True
                    else:
                        ranked = found
    if ranked is None:
        return [], [], "after_edit" if late else "none"
    return ranked, after, ""


def result_metrics(turns: list[Turn]) -> tuple[float, int, float]:
    """Sum cost, turns and API-reported duration over a task's calls.

    Parameters
    ----------
    turns : list of Turn
        The task's calls.

    Returns
    -------
    tuple of (float, int, float)
        Cost in US dollars, agent turns, and ``duration_ms`` in
        seconds, taken from each call's ``result`` event.
    """
    cost = 0.0
    count = 0
    duration = 0.0
    for turn in turns:
        if turn.result is None:
            continue
        cost += float(turn.result.get("total_cost_usd") or 0.0)
        count += int(turn.result.get("num_turns") or 0)
        duration += float(turn.result.get("duration_ms") or 0.0) / 1000.0
    return cost, count, duration
