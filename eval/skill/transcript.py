"""Read a saved task directory: stream events, tool calls, final text.

The files are what ``runner.py`` wrote: ``turn-N.jsonl`` holds one
``claude -p --output-format stream-json`` event per line, with the
unscored noise removed. Nothing here runs anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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


def _top_hotspot(text: str) -> str | None:
    """Name the function with the largest ``total_fraction`` printed.

    Reads each printed object (JSON or a Python dict repr) that has a
    ``function`` and a ``total_fraction``, and skips interpreter frames
    (files in angle brackets) and code-object pseudo-functions such as
    ``<module>``, whose total share covers the whole script and so
    outranks every real function once printed.
    """
    best: tuple[float, str] | None = None
    for chunk in re.finditer(r"\{[^{}]*\}", text):
        body = chunk.group(0)
        func = re.search(
            rf"{_QUOTE}function{_QUOTE}\s*:\s*({_QUOTE})([^'\"]+)\1", body
        )
        frac = re.search(
            rf"{_QUOTE}total_fraction{_QUOTE}\s*:\s*([0-9.eE+-]+)", body
        )
        where = re.search(
            rf"{_QUOTE}file{_QUOTE}\s*:\s*({_QUOTE})([^'\"]*)\1", body
        )
        if func is None or frac is None:
            continue
        if where is not None and where.group(2).startswith("<"):
            continue
        if func.group(2).startswith("<"):
            continue
        try:
            score = float(frac.group(1))
        except ValueError:
            continue
        if best is None or score > best[0]:
            best = (score, func.group(2))
    if best is None:
        best = _top_hotspot_lines(text)
    return None if best is None else best[1]


_HOTSPOT_LINE = re.compile(
    r"^\s*([A-Za-z_][\w.]*)\s+(\S+)(?:\s+\S+)*?\s+(0?\.\d+|1\.0+|1)\s*$"
)


def _top_hotspot_lines(text: str) -> tuple[float, str] | None:
    """Read hotspots printed one per line: function, file, ..., share.

    Agents often print ``result.hotspots[]`` compactly, for example
    ``rolling_sum rolling.py 51 0.906``. The share is the last field;
    lines whose file is an interpreter frame (angle brackets) are
    skipped, as in the JSON reading.
    """
    best: tuple[float, str] | None = None
    for line in text.splitlines():
        match = _HOTSPOT_LINE.match(line)
        if match is None or match.group(2).startswith("<"):
            continue
        score = float(match.group(3))
        if best is None or score > best[0]:
            best = (score, match.group(1))
    return best


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
    top: str | None = None
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
                    top = _top_hotspot(_result_text(block))
    return top, after


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
