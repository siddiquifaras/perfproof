"""Attribute the tokens in agent sessions to their causes.

Reads Claude Code session logs (JSONL) and ``codex exec`` rollouts in
place and writes aggregates only: category names and numbers, never
transcript text. Per-call token counts come from the API ``usage``
fields and are exact. The split of each call's input among the things
in the context is estimated: tool-result sizes are measured in
characters and converted to tokens with ratios fitted to the exact
growth of ``usage`` between consecutive calls.

Run with ``uv run --with numpy --with scipy --with matplotlib
studies/tokens/analyze.py ~/.claude/projects --out studies/tokens``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import nnls

# Price multipliers relative to one fresh input token. These are the
# public API price ratios; they are an assumption of the cost view, not
# something measured from the logs.
W_FRESH = 1.0
W_WRITE_5M = 1.25
W_WRITE_1H = 2.0
W_READ = 0.1
W_OUT = 5.0

RESULT_CATS = (
    "test_output",
    "profiler_output",
    "perfproof_cli",
    "file_read_search",
    "git",
    "web_gh",
    "subagent_reports",
    "other_shell",
    "edit_acks",
    "other_tools",
    "user_messages",
    "injected_context",
    "hook_feedback",
    "prompt_initial",
)
CALL_CATS = (
    "agent_calls_brief",
    "agent_calls_edit",
    "agent_calls_shell",
    "agent_calls_read",
    "agent_calls_other",
)
OWN_CATS = ("agent_thinking", "agent_messages", *CALL_CATS)
BASE_CAT = "system_prefix"
ALL_CATS = (BASE_CAT, *RESULT_CATS, *OWN_CATS)

LABELS = {
    "test_output": "test, lint and type-check output",
    "profiler_output": "profiler output",
    "perfproof_cli": "perfproof CLI output",
    "file_read_search": "file reads and code search",
    "git": "git diffs and logs",
    "web_gh": "web and gh fetches",
    "subagent_reports": "subagent reports",
    "other_shell": "other shell output",
    "edit_acks": "edit and write acknowledgements",
    "other_tools": "other tool results",
    "user_messages": "user messages",
    "injected_context": "harness reminders and attachments",
    "hook_feedback": "hook feedback",
    "prompt_initial": "initial prompt and project instructions",
    "agent_thinking": "agent reasoning (thinking)",
    "agent_messages": "agent messages",
    "agent_calls_brief": "agent writes: subagent briefs and final reports",
    "agent_calls_edit": "agent writes: code and file contents (Edit, Write)",
    "agent_calls_shell": "agent writes: shell commands",
    "agent_calls_read": "agent writes: read, search and fetch requests",
    "agent_calls_other": "agent writes: other tool calls",
    BASE_CAT: "system prompt and tool schemas (fixed prefix)",
}

_RE_PERFPROOF = re.compile(
    r"(?:uv\s+run|uvx)\s+(?:[^\n;&|\"']*?\s)?perfproof(?=\s|$)"
    r"|(?:^|[;&|(\n])\s*(?:python3?\s+-m\s+)?perfproof(?=\s|$)"
    r"|bin/perfproof\b",
    re.M,
)
_RE_AUTHORING = re.compile(r"\bgit\s+(add|commit)|cat\s*>|sed\s+-i|mkdir")
_RE_PROFILER = re.compile(
    r"py-spy|pyinstrument|cprofile|scalene|snakeviz|pstats|flamegraph"
    r"|\bperf (record|stat|report)\b|\bnsys\b|\bncu\b|--profile\b"
    r"|memray|line_profiler|\bvmprof\b|explain analyze",
    re.I,
)
_RE_TEST = re.compile(
    r"pytest|\bruff\b|pyright|\bmypy\b|unittest|manage\.py test"
    r"|cargo (test|clippy)|\bmake (test|check)\b|npm (run )?test"
    r"|/verify|\bctest\b|go test|\btox\b|\bnox\b"
)
_RE_GIT = re.compile(r"\bgit\s+(-C\s+\S+\s+)?(diff|log|show|blame|status)\b")
_RE_WEB = re.compile(r"\bcurl\b|\bwget\b|\bgh\s+(pr|api|issue|run|repo)\b")
_RE_FILES = re.compile(
    r"(^|[\s;&|(])(cat|head|tail|sed|grep|rg|find|ls|wc|tree|awk"
    r"|sort|cut|nl|file|stat|du)\b"
)

_FILE_TOOLS = {"Read", "Grep", "Glob", "NotebookRead", "LS"}
_WEB_TOOLS = {"WebFetch", "WebSearch"}
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_AGENT_TOOLS = {"Agent", "Task"}


def classify_tool(name: str, tool_input: Any) -> str:
    """Return the cause category of a tool call's result.

    Parameters
    ----------
    name : str
        Tool name as logged by the harness.
    tool_input : Any
        The call's input object. Only the ``command`` of shell tools
        is inspected.

    Returns
    -------
    str
        One of the members of ``RESULT_CATS``.
    """
    if name in _FILE_TOOLS:
        return "file_read_search"
    if name in _WEB_TOOLS:
        return "web_gh"
    if name in _EDIT_TOOLS:
        return "edit_acks"
    if name in _AGENT_TOOLS:
        return "subagent_reports"
    if name in {"Bash", "exec_command", "exec"}:
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = str(tool_input.get("command") or tool_input.get("cmd", ""))
        elif isinstance(tool_input, str):
            cmd = tool_input
        if name == "exec":
            # Codex wraps the command in JavaScript, so the first word
            # of the text is not the command that runs.
            return _classify_anywhere(cmd)
        return classify_command(cmd)
    return "other_tools"


def call_cat(name: str) -> str:
    """Return the category of what the agent wrote in a tool call.

    Parameters
    ----------
    name : str
        Tool name as logged by the harness.

    Returns
    -------
    str
        One of the members of ``CALL_CATS``.
    """
    if name in _AGENT_TOOLS or name == "SubagentHandback":
        return "agent_calls_brief"
    if name in _EDIT_TOOLS:
        return "agent_calls_edit"
    if name in {"Bash", "exec_command", "exec"}:
        return "agent_calls_shell"
    if name in _FILE_TOOLS or name in _WEB_TOOLS:
        return "agent_calls_read"
    return "agent_calls_other"


def sub_label(name: str, tool_input: Any) -> str:
    """Return a drill-down label for a tool result.

    Parameters
    ----------
    name : str
        Tool name as logged by the harness.
    tool_input : Any
        The call's input; only shell commands are inspected.

    Returns
    -------
    str
        The tool name, or for shell tools the first word of the command
        after leading ``cd`` and variable-assignment steps.
    """
    if name not in {"Bash", "exec_command", "exec"}:
        return name or "unknown"
    cmd = ""
    if isinstance(tool_input, dict):
        cmd = str(tool_input.get("command") or tool_input.get("cmd", ""))
    elif isinstance(tool_input, str):
        cmd = tool_input
    for part in re.split(r"&&|;|\n", cmd):
        words = part.split()
        while words and re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0]):
            words.pop(0)
        if words and words[0] not in {"cd", "set", "export", "source", "#"}:
            return f"sh:{words[0].rsplit('/', 1)[-1][:20]}"
    return "sh:other"


_TEST_HEADS = {"pytest", "ruff", "pyright", "mypy", "tox", "nox", "ctest"}
_PROFILER_HEADS = {
    "py-spy",
    "pyinstrument",
    "scalene",
    "snakeviz",
    "memray",
    "nsys",
    "ncu",
    "vmprof",
}
_FILE_HEADS = {
    "cat",
    "head",
    "tail",
    "sed",
    "grep",
    "rg",
    "find",
    "ls",
    "wc",
    "tree",
    "awk",
    "sort",
    "cut",
    "nl",
    "file",
    "stat",
    "du",
}
_SKIP_HEADS = {"cd", "set", "export", "source", "#", "true", ":"}
_WRAPPERS = {"time", "nohup", "sudo", "command", "env"}
_UV_VALUE_OPTS = {"--project", "--with", "--directory", "-p", "--python"}
CLASSIFIER_MODES = ("head", "anywhere")
_mode = "head"


def set_classifier(mode: str) -> None:
    """Choose how shell commands are classified.

    Parameters
    ----------
    mode : {"head", "anywhere"}
        ``head`` looks at the command that runs first in the line,
        after leading ``cd``, variable assignments, ``time`` and the
        ``uv run`` prefix. ``anywhere`` matches the rules against the
        whole text and is kept to give a sensitivity range.

    Raises
    ------
    ValueError
        If ``mode`` is not one of ``CLASSIFIER_MODES``.
    """
    global _mode
    if mode not in CLASSIFIER_MODES:
        raise ValueError(mode)
    _mode = mode


def head_words(cmd: str) -> list[str]:
    """Return the words of the first real command in a command line.

    Leading ``cd``, ``set``, ``export`` steps, variable assignments,
    ``time``-style wrappers and the ``uv run`` prefix with its options
    are skipped; only the first command of a pipeline is kept.

    Parameters
    ----------
    cmd : str
        The full command line as logged.

    Returns
    -------
    list[str]
        The words, or an empty list when nothing runs.
    """
    for part in re.split(r"&&|\|\||;|\n", cmd):
        words = part.split("|")[0].split()
        while words and (
            re.fullmatch(r"[A-Za-z_]\w*=\S*", words[0])
            or words[0] in _WRAPPERS
        ):
            words.pop(0)
        if words[:2] == ["uv", "run"] or words[:1] == ["uvx"]:
            words = words[2:] if words[0] == "uv" else words[1:]
            while words and words[0].startswith("-"):
                skip = 2 if words[0] in _UV_VALUE_OPTS else 1
                words = words[skip:]
        if not words or words[0].rsplit("/", 1)[-1] in _SKIP_HEADS:
            continue
        words[0] = words[0].rsplit("/", 1)[-1]
        return words
    return []


def _classify_head(cmd: str) -> str:
    """Classify by the command that runs first (see ``head_words``)."""
    if _RE_PERFPROOF.search(cmd):
        return "perfproof_cli"
    words = head_words(cmd)
    if not words:
        return "other_shell"
    head = words[0]
    rest = words[1:]
    if head in {"python", "python3"} and rest[:1] == ["-m"] and len(rest) > 1:
        head, rest = rest[1], rest[2:]
    if head in _PROFILER_HEADS or head in {"cProfile", "cprofile"}:
        return "profiler_output"
    if head == "perf" and rest[:1] in (["record"], ["stat"], ["report"]):
        return "profiler_output"
    if "--profile" in words:
        return "profiler_output"
    if head in _TEST_HEADS or head == "/verify":
        return "test_output"
    if head == "cargo" and rest[:1] in (["test"], ["clippy"]):
        return "test_output"
    if head in {"make", "npm"} and (
        rest[:1] in (["test"], ["check"]) or rest[:2] == ["run", "test"]
    ):
        return "test_output"
    if head == "go" and rest[:1] == ["test"]:
        return "test_output"
    if head == "git":
        args = [w for w in rest if not w.startswith("-")]
        sub = args[1] if rest[:1] == ["-C"] and len(args) > 1 else args[:1]
        sub = sub if isinstance(sub, str) else (sub[0] if sub else "")
        if sub in {"diff", "log", "show", "blame", "status"}:
            return "git"
        return "other_shell"
    if head in {"curl", "wget"} or head == "gh":
        return "web_gh"
    if head in _FILE_HEADS:
        return "file_read_search"
    return "other_shell"


def _classify_anywhere(cmd: str) -> str:
    """Classify by the first rule that matches anywhere in the text."""
    if _RE_PERFPROOF.search(cmd):
        return "perfproof_cli"
    if _RE_PROFILER.search(cmd) and not _RE_AUTHORING.search(cmd):
        return "profiler_output"
    if _RE_TEST.search(cmd):
        return "test_output"
    if _RE_GIT.search(cmd):
        return "git"
    if _RE_WEB.search(cmd):
        return "web_gh"
    if _RE_FILES.search(cmd):
        return "file_read_search"
    return "other_shell"


def classify_command(cmd: str) -> str:
    """Return the cause category of a shell command's output.

    A ``perfproof`` invocation counts as CLI output wherever it sits in
    the line, because runs often wrap it in a logging script. Everything
    else is classified by the command that runs first (see
    ``set_classifier``).

    Parameters
    ----------
    cmd : str
        The full command line as logged.

    Returns
    -------
    str
        One of the members of ``RESULT_CATS``.
    """
    if _mode == "anywhere":
        return _classify_anywhere(cmd)
    return _classify_head(cmd)


@dataclass
class Item:
    """One thing appended to the context between two calls.

    Attributes
    ----------
    cat : str
        Cause category.
    chars : int
        Length of the text in characters.
    turn : bool
        True for a user message that starts a new turn. Thinking from
        earlier turns is dropped from the context when one arrives.
    sub : str
        Finer label for drill-down: tool name, first word of a shell
        command, or attachment type. Never text from the transcript.
    repeat : bool
        True for a file read of a path already read in this session.
    """

    cat: str
    chars: int
    turn: bool = False
    sub: str = ""
    repeat: bool = False


@dataclass
class Call:
    """One API call with its exact usage and what preceded it.

    Attributes
    ----------
    fresh, write_5m, write_1h, read, out, think : int
        Exact usage: uncached input, cache writes by TTL, cache reads,
        output tokens and the thinking part of the output.
    text_chars : int
        Characters of the visible reply.
    tool_by : dict[str, int]
        Characters of the tool-call inputs, by ``CALL_CATS`` member.
    has_thinking : bool
        Whether the reply carried a thinking block.
    out_exact : bool
        True when the log holds the final output count. Subagent logs
        keep the count from the start of the stream, a few tokens, so
        their output is derived later and ``out_logged`` keeps the
        logged value.
    gap : list[Item]
        Items appended after the previous call, in context order.
    """

    fresh: int = 0
    write_5m: int = 0
    write_1h: int = 0
    read: int = 0
    out: int = 0
    think: int = 0
    text_chars: int = 0
    tool_by: dict[str, int] = field(default_factory=dict)
    has_thinking: bool = False
    out_exact: bool = False
    out_logged: int = 0
    gap: list[Item] = field(default_factory=list)

    @property
    def tool_chars(self) -> int:
        """Return the characters of all tool-call inputs."""
        return sum(self.tool_by.values())

    def own_split(self) -> list[tuple[str, float]]:
        """Return the output categories with their share of the reply.

        The reply is split by characters between its text and each
        class of tool-call input. A reply with no visible characters
        counts as a message.
        """
        total = self.text_chars + self.tool_chars
        if not total:
            return [("agent_messages", 1.0)]
        parts = [("agent_messages", self.text_chars / total)]
        parts.extend((c, n / total) for c, n in self.tool_by.items())
        return parts

    @property
    def turn_start(self) -> bool:
        """Return whether a new user turn began before this call."""
        return any(item.turn for item in self.gap)

    @property
    def total_in(self) -> int:
        """Return all input tokens: uncached, written and read."""
        return self.fresh + self.write_5m + self.write_1h + self.read

    @property
    def cost_units(self) -> float:
        """Return the call's cost in fresh-input-token equivalents."""
        return (
            self.fresh * W_FRESH
            + self.write_5m * W_WRITE_5M
            + self.write_1h * W_WRITE_1H
            + self.read * W_READ
            + self.out * W_OUT
        )


@dataclass
class Session:
    """A parsed session: its group, label and ordered calls."""

    label: str
    group: str
    calls: list[Call]
    models: set[str] = field(default_factory=set)
    writes: dict[str, list[int]] = field(default_factory=dict)


def _text_len(content: Any) -> int:
    """Return the number of text characters in a content value."""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(b["text"])
            for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return 0


def _billed(usage: dict[str, Any]) -> bool:
    """Return whether a usage record belongs to a real API call."""
    return any(
        int(usage.get(k, 0))
        for k in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )


def _merge_usage(call: Call, usage: dict[str, Any], first: bool) -> None:
    """Fold one record's usage into its call, keeping the maxima."""
    cc = usage.get("cache_creation") or {}
    w5 = int(cc.get("ephemeral_5m_input_tokens", 0))
    w1 = int(cc.get("ephemeral_1h_input_tokens", 0))
    total_w = int(usage.get("cache_creation_input_tokens", 0))
    if w5 + w1 != total_w:
        w5, w1 = total_w, 0
    details = usage.get("output_tokens_details") or {}
    new = (
        int(usage.get("input_tokens", 0)),
        w5,
        w1,
        int(usage.get("cache_read_input_tokens", 0)),
        int(usage.get("output_tokens", 0)),
        int(details.get("thinking_tokens", 0)),
    )
    if first:
        call.fresh, call.write_5m, call.write_1h = new[:3]
        call.read = new[3]
    call.out = max(call.out, new[4])
    call.think = max(call.think, new[5])
    call.out_logged = call.out
    if "output_tokens_details" in usage:
        call.out_exact = True


def parse_claude(
    path: Path, label: str, group: str, until: str = "9"
) -> Session | None:
    """Parse one Claude Code JSONL log into calls and context items.

    Streaming logs one record per content block, all repeating the
    message's usage, so records are merged by message id.

    Parameters
    ----------
    path : Path
        The ``.jsonl`` file. It is read in place.
    label, group : str
        Name and group the session is reported under.
    until : str, default "9"
        ISO-8601 UTC timestamp; records stamped later are ignored, so a
        log still being written gives the same numbers on every run.

    Returns
    -------
    Session or None
        None when the log holds no billed call.
    """
    calls: list[Call] = []
    pending: list[Item] = []
    uses: dict[str, tuple[str, Any]] = {}
    models: set[str] = set()
    reads: set[str] = set()
    writes: dict[str, list[int]] = {}
    current_id: str | None = None
    current: Call | None = None
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("timestamp", "") > until:
                continue
            kind = rec.get("type")
            if kind == "assistant":
                msg = rec.get("message", {})
                usage = msg.get("usage") or {}
                if not usage or msg.get("model") == "<synthetic>":
                    continue
                if not _billed(usage):
                    continue
                mid = msg.get("id")
                if current is None or mid != current_id:
                    current = Call(gap=pending)
                    pending = []
                    calls.append(current)
                    current_id = mid
                    _merge_usage(current, usage, True)
                    models.add(str(msg.get("model")))
                else:
                    _merge_usage(current, usage, False)
                for block in msg.get("content", []):
                    btype = block.get("type")
                    if btype == "thinking":
                        current.has_thinking = True
                    elif btype == "text":
                        current.text_chars += len(block.get("text", ""))
                    elif btype == "tool_use":
                        inp = block.get("input", {})
                        ccat = call_cat(block.get("name", ""))
                        current.tool_by[ccat] = current.tool_by.get(
                            ccat, 0
                        ) + len(json.dumps(inp))
                        uses[block["id"]] = (block.get("name", ""), inp)
                        _count_write(writes, block.get("name", ""), inp)
            elif kind == "user":
                _add_user(rec, calls, pending, uses, reads)
            elif kind == "attachment":
                _add_attachment(rec, calls, pending)
    if not calls:
        return None
    return Session(label, group, calls, models, writes)


def _count_write(
    writes: dict[str, list[int]], name: str, tool_input: Any
) -> None:
    """Count the characters the agent wrote in a file or shell call.

    Edit-type calls are counted by tool name; shell commands by kind:
    script (heredoc or ``python -c``), long (over 600 characters) or
    short.
    """
    chars = len(json.dumps(tool_input))
    if name in {"Write", "Edit", "MultiEdit"}:
        key = name
    elif name == "Bash":
        cmd = str(tool_input.get("command", ""))
        if "<<" in cmd or "python3 -c" in cmd or "python -c" in cmd:
            key = "shell: heredoc or python -c script"
        elif len(cmd) > 600:
            key = "shell: other long command (over 600 chars)"
        else:
            key = "shell: short command"
    else:
        return
    entry = writes.setdefault(key, [0, 0])
    entry[0] += 1
    entry[1] += chars


def _add_user(
    rec: dict[str, Any],
    calls: list[Call],
    pending: list[Item],
    uses: dict[str, tuple[str, Any]],
    reads: set[str],
) -> None:
    """Append the items of one user record to the pending gap."""
    content = rec.get("message", {}).get("content")
    text_cat = "user_messages" if calls else "prompt_initial"
    if isinstance(content, str):
        pending.append(Item(text_cat, len(content), bool(calls)))
        return
    for block in content or []:
        if block.get("type") == "tool_result":
            name, inp = uses.get(block.get("tool_use_id", ""), ("", {}))
            path = ""
            if name == "Read" and isinstance(inp, dict):
                path = str(inp.get("file_path", ""))
            repeat = bool(path) and path in reads
            if path:
                reads.add(path)
            pending.append(
                Item(
                    classify_tool(name, inp),
                    _text_len(block.get("content")),
                    sub=sub_label(name, inp),
                    repeat=repeat,
                )
            )
        elif block.get("type") == "text":
            pending.append(
                Item(text_cat, len(block.get("text", "")), bool(calls))
            )


def _add_attachment(
    rec: dict[str, Any], calls: list[Call], pending: list[Item]
) -> None:
    """Append what a harness attachment injected into the context."""
    att = rec.get("attachment", {})
    chars = 0
    for part in rec.get("rendered") or []:
        chars += _text_len(part.get("content"))
    if not chars:
        return
    atype = str(att.get("type", ""))
    if not calls:
        cat = "prompt_initial"
    elif atype == "queued_command":
        note = str(att.get("prompt", ""))[:20]
        cat = (
            "subagent_reports"
            if note.startswith(("<task-notification", "<agent-message"))
            else "user_messages"
        )
    elif atype.startswith("hook"):
        cat = "hook_feedback"
    else:
        cat = "injected_context"
    pending.append(Item(cat, chars, sub=atype))


def parse_codex(path: Path, label: str, group: str) -> Session | None:
    """Parse one ``codex exec`` rollout into calls and context items.

    Codex reports cached input inside ``input_tokens``; here cached
    tokens are stored as reads and the rest as fresh input. Codex has no
    cache-write charge, and its cost view is not computed.

    Parameters
    ----------
    path : Path
        The rollout ``.jsonl`` file, read in place.
    label, group : str
        Name and group the session is reported under.

    Returns
    -------
    Session or None
        None when the log holds no token record.
    """
    calls: list[Call] = []
    pending: list[Item] = []
    uses: dict[str, tuple[str, Any]] = {}
    models: set[str] = set()
    out_text = out_tool = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = rec.get("payload") or {}
            ptype = payload.get("type")
            if rec.get("type") == "token_usage_record":
                usage = payload.get("usage") or {}
                total = int(usage.get("input_tokens", 0))
                cached = int(usage.get("cached_input_tokens", 0))
                call = Call(
                    fresh=total - cached,
                    read=cached,
                    out=int(usage.get("output_tokens", 0)),
                    out_logged=int(usage.get("output_tokens", 0)),
                    out_exact=True,
                    think=int(usage.get("reasoning_output_tokens", 0)),
                    text_chars=out_text,
                    tool_by={"agent_calls_shell": out_tool}
                    if out_tool
                    else {},
                    gap=pending,
                )
                pending = []
                out_text = out_tool = 0
                calls.append(call)
            elif rec.get("type") == "response_item":
                if ptype in {"custom_tool_call", "function_call"}:
                    raw = payload.get("input") or payload.get("arguments")
                    uses[payload.get("call_id", "")] = (
                        "exec",
                        str(raw or ""),
                    )
                    out_tool += len(str(raw or ""))
                elif ptype in {
                    "custom_tool_call_output",
                    "function_call_output",
                }:
                    name, inp = uses.get(payload.get("call_id", ""), ("", ""))
                    pending.append(
                        Item(
                            classify_tool(name, inp),
                            _text_len(payload.get("output")),
                            sub=sub_label(name, inp),
                        )
                    )
                elif ptype == "message" and payload.get("role") == (
                    "assistant"
                ):
                    out_text += _text_len(payload.get("content"))
                elif ptype == "message" and not calls:
                    pending.append(
                        Item(
                            "prompt_initial", _text_len(payload.get("content"))
                        )
                    )
            elif ptype == "thread_settings_applied":
                models.add(
                    str(payload.get("thread_settings", {}).get("model"))
                )
    if not calls:
        return None
    return Session(label, group, calls, models)


@dataclass
class Calibration:
    """Fitted tokens per character and what the fit leaves unexplained.

    Attributes
    ----------
    ratio : dict[str, float]
        Tokens per character for each result category. The initial
        prompt only appears before the first call, which gives no
        growth to fit, so it borrows the harness-reminder ratio.
    per_item : float
        Framing tokens each appended item costs on top of its text.
    phi : float
        Share of thinking tokens that stay in later calls' context
        within a turn.
    out_text, out_tool : float
        Output tokens per character of visible text and of tool-call
        inputs, fitted on calls whose output count is final.
    gaps : int
        Number of gaps the fit used.
    in_sample_error : float
        Sum of absolute residuals over sum of observed growth.
    cv_error : float
        The same, measured on sessions held out of the fit (5 folds).
    total_bias : float
        Predicted over observed total growth on held-out sessions.
    """

    ratio: dict[str, float]
    per_item: float
    phi: float
    out_text: float
    out_tool: float
    gaps: int
    in_sample_error: float
    cv_error: float
    total_bias: float


def _gap_rows(
    sessions: list[Session],
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Build the regression rows from consecutive calls.

    Each row regresses the exact growth of input between two calls,
    minus the exact output of the earlier call (which joins the
    context), on the characters appended per category, the item count
    and minus the earlier call's thinking tokens, whose coefficient is
    the share of thinking that does not stay in context. Dropped rows:
    input shrank (context cleared or compacted), a new user turn began
    (earlier thinking leaves the context), the earlier call's output
    count is partial, or the cache did not cover the previous context
    (rebuilt after expiry, so the composition may differ).

    Returns
    -------
    tuple
        Design matrix ``(n, len(RESULT_CATS) + 2)``, targets ``(n,)``
        and the session index of every row.
    """
    ncat = len(RESULT_CATS)
    index = {c: i for i, c in enumerate(RESULT_CATS)}
    rows: list[np.ndarray] = []
    ys: list[float] = []
    owner: list[int] = []
    for si, sess in enumerate(sessions):
        for prev, cur in zip(sess.calls, sess.calls[1:], strict=False):
            growth = cur.total_in - prev.total_in
            target = growth - prev.out
            if growth <= 0 or cur.turn_start or not prev.out_exact:
                continue
            if cur.read < 0.5 * prev.total_in:
                continue
            x = np.zeros(ncat + 2)
            for item in cur.gap:
                x[index[item.cat]] += item.chars
            x[ncat] = len(cur.gap)
            x[ncat + 1] = -prev.think
            rows.append(x)
            ys.append(target)
            owner.append(si)
    return np.array(rows), np.array(ys), owner


def calibrate(sessions: list[Session]) -> Calibration:
    """Fit tokens per character per category from ``usage`` growth.

    Non-negative least squares on all consecutive-call gaps, with
    5-fold cross-validation over sessions for the error.

    Parameters
    ----------
    sessions : list[Session]
        Sessions of one tokenizer family.

    Returns
    -------
    Calibration
        Fitted ratios and their error.
    """
    x, y, owner = _gap_rows(sessions)
    # Rows are scaled by the target so every gap weighs the same share
    # of its own size instead of the largest tool results dominating.
    w = np.ones_like(y)
    coef, _ = nnls(x * w[:, None], y * w)
    pred = x @ coef
    in_err = float(np.abs(pred - y).sum() / np.abs(y).sum())
    owner_arr = np.array(owner)
    fold = owner_arr % 5
    abs_err = 0.0
    pred_total = 0.0
    for k in range(5):
        train, test = fold != k, fold == k
        if not test.any() or not train.any():
            continue
        ck, _ = nnls(x[train] * w[train, None], y[train] * w[train])
        pk = x[test] @ ck
        abs_err += float(np.abs(pk - y[test]).sum())
        pred_total += float(pk.sum())
    ncat = len(RESULT_CATS)
    exact = [c for s in sessions for c in s.calls if c.out_exact]
    xa = np.array([[c.text_chars, c.tool_chars] for c in exact], dtype=float)
    ya = np.array([c.out - c.think for c in exact], dtype=float)
    wa = 1.0 / np.maximum(ya, 20.0)
    oc, _ = nnls(xa * wa[:, None], ya * wa)
    ratio = dict(
        zip(RESULT_CATS, (float(c) for c in coef[:ncat]), strict=True)
    )
    seen = sorted(v for v in ratio.values() if v > 0)
    ratio["prompt_initial"] = ratio["injected_context"] or seen[len(seen) // 2]
    return Calibration(
        ratio=ratio,
        per_item=float(coef[ncat]),
        phi=float(max(1.0 - coef[ncat + 1], 0.0)),
        out_text=float(oc[0]),
        out_tool=float(oc[1]),
        gaps=len(y),
        in_sample_error=in_err,
        cv_error=abs_err / float(np.abs(y).sum()),
        total_bias=pred_total / float(np.abs(y).sum()),
    )


def _derived_out(sess: Session, j: int, cal: Calibration) -> float:
    """Return the estimated output tokens of call ``j``.

    The next call's input contains this call's whole output, so the
    output is the exact growth of input minus the estimated size of
    what else was appended. Where that is not possible (last call, a
    new user turn that drops thinking, a cache rebuilt after expiry) the
    estimate falls back to characters, which leaves out any thinking.
    """
    call = sess.calls[j]
    vis = cal.out_text * call.text_chars + cal.out_tool * call.tool_chars
    out = vis
    if j + 1 < len(sess.calls):
        nxt = sess.calls[j + 1]
        growth = nxt.total_in - call.total_in
        if (
            growth > 0
            and not nxt.turn_start
            and nxt.read >= 0.5 * call.total_in
        ):
            gap = sum(
                cal.ratio[i.cat] * i.chars + cal.per_item for i in nxt.gap
            )
            out = max(out, growth - gap)
    return out


def derive_outputs(sess: Session, cal: Calibration) -> int:
    """Fill in output tokens for calls whose logged count is partial.

    Parameters
    ----------
    sess : Session
        Session to update in place.
    cal : Calibration
        Fitted ratios.

    Returns
    -------
    int
        Number of calls whose output was derived. Each is never set
        below its logged value; the part beyond the visible reply is
        taken to be thinking.
    """
    derived = 0
    for j, call in enumerate(sess.calls):
        if call.out_exact:
            continue
        derived += 1
        vis = cal.out_text * call.text_chars + cal.out_tool * call.tool_chars
        out = max(float(call.out_logged), _derived_out(sess, j, cal))
        call.out = round(out)
        call.think = round(max(out - vis, 0.0))
    return derived


def validate_outputs(
    sessions: list[Session], cal: Calibration
) -> tuple[int, float, float]:
    """Check the output derivation against calls with a final count.

    Applies the derivation to calls whose logged output is final and
    compares with the logged value.

    Returns
    -------
    tuple
        Calls tested, sum of absolute errors over sum of true output,
        and derived total over true total.
    """
    err = true = est = 0.0
    n = 0
    for sess in sessions:
        for j, call in enumerate(sess.calls[:-1]):
            if not call.out_exact:
                continue
            nxt = sess.calls[j + 1]
            if nxt.turn_start or nxt.read < 0.5 * call.total_in:
                continue
            guess = _derived_out(sess, j, cal)
            err += abs(guess - call.out)
            true += call.out
            est += guess
            n += 1
    return n, err / true, est / true


SIZE_EDGES = (2_000, 10_000, 40_000)
SIZE_LABELS = ("under 2k chars", "2k to 10k", "10k to 40k", "over 40k")
_CAP = 4096
Key = tuple[str, str]


@dataclass
class Totals:
    """Exact and attributed numbers summed over a set of sessions.

    Dictionaries are keyed by ``(category, sub-label)``; the sub-label
    is empty for categories without a drill-down.

    Attributes
    ----------
    cost_fresh, cost_write, cost_read, cost_out : float
        Exact cost split by usage type, in fresh-input-token units.
    ingest : dict
        Estimated tokens appended to the context, counted once.
    reread : dict
        Estimated tokens served from the prompt cache, summed over
        calls.
    cost_by : dict
        Estimated cost in fresh-input-token units, summed over calls.
    sizes : dict
        ``(category, size bucket)`` to ``[items, ingested tokens]``.
    repeat_tokens, repeat_items : float
        Ingest of file reads of a path already read in the session.
    """

    sessions: int = 0
    calls: int = 0
    fresh: int = 0
    write: int = 0
    read: int = 0
    out: int = 0
    out_logged: int = 0
    derived_calls: int = 0
    think: int = 0
    cost_fresh: float = 0.0
    cost_write: float = 0.0
    cost_read: float = 0.0
    cost_out: float = 0.0
    scale_sum: float = 0.0
    scale_dev: float = 0.0
    drops: int = 0
    repeat_tokens: float = 0.0
    repeat_items: int = 0
    ingest: dict[Key, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    reread: dict[Key, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    cost_by: dict[Key, float] = field(
        default_factory=lambda: defaultdict(float)
    )
    sizes: dict[tuple[str, int], list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0.0, 0.0])
    )

    _SCALARS = (
        "sessions",
        "calls",
        "fresh",
        "write",
        "read",
        "out",
        "out_logged",
        "derived_calls",
        "think",
        "cost_fresh",
        "cost_write",
        "cost_read",
        "cost_out",
        "scale_sum",
        "scale_dev",
        "drops",
        "repeat_tokens",
        "repeat_items",
    )

    @property
    def cost(self) -> float:
        """Return the exact total cost in fresh-input-token units."""
        return (
            self.cost_fresh + self.cost_write + self.cost_read + self.cost_out
        )

    @property
    def total_in(self) -> int:
        """Return all input tokens: uncached, written and read."""
        return self.fresh + self.write + self.read

    def add(self, other: Totals) -> None:
        """Add another set of totals into this one."""
        for name in self._SCALARS:
            setattr(self, name, getattr(self, name) + getattr(other, name))
        for mine, theirs in (
            (self.ingest, other.ingest),
            (self.reread, other.reread),
            (self.cost_by, other.cost_by),
        ):
            for key, value in theirs.items():
                mine[key] += value
        for key, (count, tokens) in other.sizes.items():
            self.sizes[key][0] += count
            self.sizes[key][1] += tokens


def by_cat(table: dict[Key, float]) -> dict[str, float]:
    """Collapse a ``(category, sub-label)`` table to categories."""
    out: dict[str, float] = defaultdict(float)
    for (cat, _sub), value in table.items():
        out[cat] += value
    return out


def attribute(sess: Session, cal: Calibration, priced: bool = True) -> Totals:
    """Attribute a session's exact usage to context categories.

    The context before each call is the ordered list of everything
    appended so far. The prompt cache covers a prefix, so the first
    ``read`` tokens of that list are the cached part and the remainder
    is what the call paid to write or read fresh. Item sizes are
    estimates; they are rescaled per call so they sum to the exact
    input, and the rescale factor is recorded.

    Parameters
    ----------
    sess : Session
        Parsed session, outputs already derived where partial.
    cal : Calibration
        Fitted token ratios.
    priced : bool, default True
        Whether the Claude cost multipliers apply. When False (Codex,
        whose multipliers are not known here) every token weighs 1.

    Returns
    -------
    Totals
        Exact sums plus per-category ingest tokens, cache re-read
        tokens and cost, all summed over the session.

    Notes
    -----
    Time is linear in the number of items times the number of calls.
    """
    tot = Totals(sessions=1)
    keys: dict[Key, int] = {}
    ids: list[int] = []
    toks: list[float] = []
    acc_read = np.zeros(_CAP)
    acc_cost = np.zeros(_CAP)
    w_out = W_OUT if priced else 1.0
    w_read = W_READ if priced else 1.0

    def key_id(cat: str, sub: str = "") -> int:
        if (cat, sub) not in keys:
            if len(keys) >= _CAP:
                raise ValueError("too many drill-down labels")
            keys[(cat, sub)] = len(keys)
        return keys[(cat, sub)]

    first = sess.calls[0]
    prev: Call | None = None
    for call in sess.calls:
        added: list[tuple[str, str, float, Item | None]] = []
        if prev is not None:
            vis = max(prev.out - prev.think, 0)
            added.append(("agent_thinking", "", cal.phi * prev.think, None))
            added.extend((c, "", vis * w, None) for c, w in prev.own_split())
        for item in call.gap:
            est = cal.ratio[item.cat] * item.chars + cal.per_item
            added.append((item.cat, item.sub, est, item))
        if prev is None:
            base = max(first.total_in - sum(a[2] for a in added), 0.0)
            ids.append(key_id(BASE_CAT))
            toks.append(base)
        if call.turn_start:
            think_id = key_id("agent_thinking")
            for k, kid in enumerate(ids):
                if kid == think_id:
                    toks[k] = 0.0
            added = [
                (c, s, 0.0 if c == "agent_thinking" else t, i)
                for c, s, t, i in added
            ]
        for cat, sub, tok, _item in added:
            ids.append(key_id(cat, sub))
            toks.append(tok)
        total_in = call.total_in
        est_total = sum(toks)
        scale = total_in / est_total if est_total else 1.0
        if prev is not None and total_in < prev.total_in * 0.8:
            tot.drops += 1
        tot.scale_sum += scale
        tot.scale_dev += abs(math.log(scale)) if scale > 0 else 0.0
        arr = np.array(toks) * scale
        ends = np.cumsum(arr)
        read_part = np.clip(
            np.minimum(ends, call.read) - (ends - arr), 0, None
        )
        rest = arr - read_part
        wtok = call.fresh + call.write_5m + call.write_1h
        rate = W_FRESH
        if wtok and priced:
            rate = (
                call.fresh * W_FRESH
                + call.write_5m * W_WRITE_5M
                + call.write_1h * W_WRITE_1H
            ) / wtok
        idx = np.array(ids)
        acc_read += np.bincount(idx, read_part, minlength=_CAP)
        acc_cost += np.bincount(
            idx, read_part * w_read + rest * rate, minlength=_CAP
        )
        for cat, sub, tok, item in added:
            tot.ingest[(cat, sub)] += tok * scale
            if item is not None:
                b = sum(item.chars >= e for e in SIZE_EDGES)
                tot.sizes[(cat, b)][0] += 1
                tot.sizes[(cat, b)][1] += tok * scale
                if item.repeat:
                    tot.repeat_tokens += tok * scale
                    tot.repeat_items += 1
        if prev is None:
            tot.ingest[(BASE_CAT, "")] += toks[0] * scale
        vis = max(call.out - call.think, 0)
        tot.cost_by[("agent_thinking", "")] += call.think * w_out
        for cat, share in call.own_split():
            tot.cost_by[(cat, "")] += vis * share * w_out
        tot.calls += 1
        tot.fresh += call.fresh
        tot.write += call.write_5m + call.write_1h
        tot.read += call.read
        tot.out += call.out
        tot.out_logged += call.out_logged
        tot.derived_calls += 0 if call.out_exact else 1
        tot.think += call.think
        if priced:
            tot.cost_fresh += call.fresh * W_FRESH
            tot.cost_write += (
                call.write_5m * W_WRITE_5M + call.write_1h * W_WRITE_1H
            )
            tot.cost_read += call.read * W_READ
            tot.cost_out += call.out * W_OUT
        else:
            tot.cost_fresh += call.fresh
            tot.cost_read += call.read
            tot.cost_out += call.out
        prev = call
    for key, kid in keys.items():
        tot.reread[key] += acc_read[kid]
        tot.cost_by[key] += acc_cost[kid]
    return tot


def _claude_files(root: Path) -> Iterator[tuple[Path, str, str]]:
    """Yield ``(path, label, group)`` for perfproof Claude sessions.

    Groups come from the project directory name and, for subagent
    transcripts, from the agent type recorded next to them.
    """
    for proj in sorted(root.iterdir()):
        name = proj.name
        if "perfproof" not in name:
            continue
        short = name.split("perfproof", 1)[1].lstrip("-") or "main"
        short = short.replace("claude-worktrees-", "").replace(
            "-scratch-pr-study-", "/"
        )
        for path in sorted(proj.rglob("*.jsonl")):
            if path.name.endswith(".prefix.json"):
                continue
            if "subagents" in path.parts:
                meta = path.with_suffix(".meta.json")
                agent = "unknown"
                if meta.exists():
                    agent = json.loads(meta.read_text()).get(
                        "agentType", "unknown"
                    )
                group = f"subagent:{agent}"
                label = f"{short}/{path.stem}"
            else:
                group = _session_group(short)
                label = f"{short}/{path.stem[:8]}"
            yield path, label, group


def _session_group(short: str) -> str:
    """Return the study group for a top-level session directory."""
    if "t3-10" in short:
        return "T3.10 hand-runs"
    if "t3-11" in short:
        return "T3.11 PR study"
    if short == "main":
        return "lead sessions"
    return "task worker sessions"


def _codex_files(root: Path, after: str) -> Iterator[tuple[Path, str, str]]:
    """Yield Codex rollouts whose cwd is inside the perfproof tree."""
    for path in sorted(root.rglob("rollout-*.jsonl")):
        if path.name < f"rollout-{after}":
            continue
        with path.open(encoding="utf-8") as fh:
            head = json.loads(fh.readline())
        cwd = str((head.get("payload") or {}).get("cwd", ""))
        if "perfproof" not in cwd:
            continue
        run = cwd.rsplit("/", 1)[-1]
        yield path, f"codex/{run}/{path.stem[-8:]}", "codex exec"


def load(
    root: Path, codex: Path | None, after: str, until: str
) -> list[Session]:
    """Parse every selected session under the given roots.

    Parameters
    ----------
    root : Path
        The Claude Code projects directory.
    codex : Path or None
        The Codex sessions directory, if any.
    after : str
        Codex rollouts whose name sorts before ``rollout-<after>`` are
        skipped.
    until : str
        ISO-8601 UTC snapshot time. Records stamped later are ignored in
        top-level sessions, and subagent transcripts and rollouts last
        written after it are skipped, so the numbers do not move as
        agents keep working.

    Returns
    -------
    list[Session]
        Parsed sessions.
    """
    cut = datetime.fromisoformat(until.replace("Z", "+00:00")).timestamp()
    sessions: list[Session] = []
    for path, label, group in _claude_files(root):
        if "subagents" in path.parts and path.stat().st_mtime > cut:
            continue
        sess = parse_claude(path, label, group, until)
        if sess is not None:
            sessions.append(sess)
    if codex is not None:
        for path, label, group in _codex_files(codex, after):
            if path.stat().st_mtime > cut:
                continue
            sess = parse_codex(path, label, group)
            if sess is not None:
                sessions.append(sess)
    return sessions


GROUP_NAMES = {
    "subagent:general-purpose": "task workers (general-purpose subagents)",
    "subagent:reviewer": "reviewer subagents",
    "subagent:stats-auditor": "stats-auditor subagents",
}


def _tok(n: float) -> str:
    """Format a token count compactly."""
    if abs(n) >= 1e9:
        return f"{n / 1e9:.2f}B"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.2f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.0f}k"
    return f"{n:.0f}"


def _pct(part: float, whole: float) -> str:
    """Format a share as a percentage."""
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def cat_table(tot: Totals, top: int | None = None) -> list[str]:
    """Render a category table, largest cost first.

    Parameters
    ----------
    tot : Totals
        Totals to render.
    top : int, optional
        Keep only this many rows and fold the rest into one row.

    Returns
    -------
    list[str]
        Markdown lines.
    """
    cost = by_cat(tot.cost_by)
    ingest = by_cat(tot.ingest)
    reread = by_cat(tot.reread)
    whole = sum(cost.values())
    rows = sorted(cost, key=lambda c: -cost[c])
    shown, rest = (rows, []) if top is None else (rows[:top], rows[top:])
    lines = [
        "| cause | share of cost | ingested | re-read from cache |\n",
        "|---|---:|---:|---:|\n",
    ]
    lines.extend(
        f"| {LABELS[cat]} | {_pct(cost[cat], whole)} "
        f"| {_tok(ingest.get(cat, 0.0))} "
        f"| {_tok(reread.get(cat, 0.0))} |\n"
        for cat in shown
    )
    if rest:
        lines.append(
            f"| {len(rest)} smaller causes "
            f"| {_pct(sum(cost[c] for c in rest), whole)} "
            f"| {_tok(sum(ingest.get(c, 0.0) for c in rest))} "
            f"| {_tok(sum(reread.get(c, 0.0) for c in rest))} |\n"
        )
    return lines


def usage_table(groups: dict[str, Totals]) -> list[str]:
    """Render the exact usage of each group of sessions.

    Parameters
    ----------
    groups : dict[str, Totals]
        Group name to totals; a pooled row is the caller's to add.

    Returns
    -------
    list[str]
        Markdown lines. Input columns and the cost split are exact;
        output is the logged count and the count after derivation.
    """
    lines = [
        "| group | sessions | calls | input | cache read share of input "
        "| output logged | output used | cost units "
        "| cost: cache read / write / uncached / output |\n",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|\n",
    ]
    for name, t in groups.items():
        c = t.cost
        lines.append(
            f"| {GROUP_NAMES.get(name, name)} | {t.sessions} | {t.calls} "
            f"| {_tok(t.total_in)} | {_pct(t.read, t.total_in)} "
            f"| {_tok(t.out_logged)} | {_tok(t.out)} | {_tok(c)} "
            f"| {_pct(t.cost_read, c)} / {_pct(t.cost_write, c)} "
            f"/ {_pct(t.cost_fresh, c)} / {_pct(t.cost_out, c)} |\n"
        )
    return lines


def drill_table(tot: Totals, cat: str, top: int = 8) -> list[str]:
    """Render the sub-labels of one category, largest cost first.

    Parameters
    ----------
    tot : Totals
        Totals to render.
    cat : str
        Category to expand.
    top : int, default 8
        Number of sub-labels shown.

    Returns
    -------
    list[str]
        Markdown lines.
    """
    rows = {k[1]: v for k, v in tot.cost_by.items() if k[0] == cat}
    whole = sum(rows.values())
    lines = [
        "| source | share of the category's cost | ingested |\n",
        "|---|---:|---:|\n",
    ]
    ordered = sorted(rows, key=lambda s: -rows[s])
    lines.extend(
        f"| {sub or 'all'} | {_pct(rows[sub], whole)} "
        f"| {_tok(tot.ingest.get((cat, sub), 0.0))} |\n"
        for sub in ordered[:top]
    )
    return lines


def size_table(tot: Totals, cats: tuple[str, ...]) -> list[str]:
    """Render how a category's ingest splits by result size.

    Parameters
    ----------
    tot : Totals
        Totals to render.
    cats : tuple[str, ...]
        Categories to show, one row per size bucket.

    Returns
    -------
    list[str]
        Markdown lines.
    """
    lines = [
        "| cause | result size | results | share of the cause's ingest |\n",
        "|---|---|---:|---:|\n",
    ]
    for cat in cats:
        whole = sum(tot.sizes[(cat, b)][1] for b in range(4))
        for b, label in enumerate(SIZE_LABELS):
            n, tokens = tot.sizes[(cat, b)]
            lines.append(
                f"| {LABELS[cat]} | {label} | {n:.0f} "
                f"| {_pct(tokens, whole)} |\n"
            )
    return lines


def session_summary(rows: list[tuple[Session, Totals]]) -> list[str]:
    """Render one overview line per session with its top causes.

    Parameters
    ----------
    rows : list[tuple[Session, Totals]]
        Sessions with their totals.

    Returns
    -------
    list[str]
        Markdown lines, largest cost first.
    """
    lines = [
        "| session | calls | input | output used | cost units "
        "| top three causes (share of cost) |\n",
        "|---|---:|---:|---:|---:|---|\n",
    ]
    for sess, t in sorted(rows, key=lambda r: -r[1].cost):
        cost = by_cat(t.cost_by)
        whole = sum(cost.values())
        top = sorted(cost, key=lambda c: -cost[c])[:3]
        text = "; ".join(f"{LABELS[c]} {_pct(cost[c], whole)}" for c in top)
        lines.append(
            f"| {sess.label} | {t.calls} | {_tok(t.total_in)} "
            f"| {_tok(t.out)} | {_tok(t.cost)} | {text} |\n"
        )
    return lines


def write_csv(rows: list[tuple[Session, Totals]], path: Path) -> None:
    """Write one aggregate line per session, with no transcript text.

    Parameters
    ----------
    rows : list[tuple[Session, Totals]]
        Sessions with their totals.
    path : Path
        Output CSV path.
    """
    head = [
        "session",
        "group",
        "model",
        "calls",
        "input_tokens",
        "uncached_input",
        "cache_write",
        "cache_read",
        "output_logged",
        "output_used",
        "cost_units",
        *[f"cost_share_{c}" for c in ALL_CATS],
    ]
    lines = [",".join(head) + "\n"]
    for sess, t in rows:
        cost = by_cat(t.cost_by)
        whole = sum(cost.values()) or 1.0
        model = "+".join(sorted(sess.models))
        vals = [
            sess.label,
            sess.group,
            model,
            t.calls,
            t.total_in,
            t.fresh,
            t.write,
            t.read,
            t.out_logged,
            t.out,
            round(t.cost),
            *[f"{cost.get(c, 0.0) / whole:.4f}" for c in ALL_CATS],
        ]
        lines.append(",".join(str(v) for v in vals) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def chart(pooled: Totals, path: Path, worker_share: float) -> None:
    """Draw the pooled cost split as a horizontal bar chart.

    Parameters
    ----------
    pooled : Totals
        Pooled totals of the Claude sessions.
    path : Path
        PNG output path.
    worker_share : float
        Share of the pooled cost that belongs to task workers, shown in
        the title because it says what kind of work the mix comes from.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cost = by_cat(pooled.cost_by)
    whole = sum(cost.values())
    order = sorted(cost, key=lambda c: cost[c])[-12:]
    fig, ax = plt.subplots(figsize=(9, 5.6))
    ys = np.arange(len(order))
    ax.barh(
        ys,
        [100 * cost[c] / whole for c in order],
        height=0.6,
        color="#2a6f97",
    )
    for y, c in zip(ys, order, strict=True):
        ax.text(
            100 * cost[c] / whole + 0.4,
            y,
            f"{100 * cost[c] / whole:.1f}%",
            va="center",
            fontsize=9,
        )
    ax.set_yticks(ys, [LABELS[c] for c in order], fontsize=9)
    ax.set_xlabel("share of estimated cost, input-token equivalents (%)")
    ax.set_title(
        "Pooled Claude Code sessions, mostly perfproof development\n"
        f"(task workers are {100 * worker_share:.0f}% of cost)",
        fontsize=11,
    )
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _inv(r: float) -> str:
    """Format characters per token, or a dash when the ratio is 0."""
    return f"{1 / r:.2f}" if r else "n/a"


def _calibration_lines(name: str, cal: Calibration) -> list[str]:
    """Render a calibration as a Markdown table."""
    lines = [
        f"{name}: {cal.gaps} gaps between consecutive calls. A ratio of "
        "0 means the category never appeared in a fitted gap; its items "
        "are then sized at the framing overhead only and the per-call "
        "rescale absorbs the difference.\n\n",
        "| category | tokens per character | characters per token |\n",
        "|---|---:|---:|\n",
    ]
    for cat in RESULT_CATS:
        r = cal.ratio[cat]
        lines.append(f"| {LABELS[cat]} | {r:.3f} | {_inv(r)} |\n")
    lines.append(
        f"| visible reply text (output) | {cal.out_text:.3f} "
        f"| {_inv(cal.out_text)} |\n"
    )
    lines.append(
        f"| tool-call inputs (output) | {cal.out_tool:.3f} "
        f"| {_inv(cal.out_tool)} |\n"
    )
    lines.append(
        f"\nFraming overhead per appended item: {cal.per_item:.1f} tokens. "
        f"Thinking kept in context within a turn: {cal.phi:.2f}. "
        f"Error on the growth of input: {100 * cal.in_sample_error:.1f}% "
        f"in sample, {100 * cal.cv_error:.1f}% on held-out sessions "
        f"(5 folds); predicted over observed total "
        f"{cal.total_bias:.3f}.\n"
    )
    return lines


def group_share_table(groups: dict[str, Totals]) -> list[str]:
    """Render each group's share of the pooled cost.

    Parameters
    ----------
    groups : dict[str, Totals]
        Group name to totals.

    Returns
    -------
    list[str]
        Markdown lines, largest first.
    """
    whole = sum(t.cost for t in groups.values())
    lines = ["| group | share of pooled cost |\n", "|---|---:|\n"]
    lines.extend(
        f"| {GROUP_NAMES.get(n, n)} | {_pct(t.cost, whole)} |\n"
        for n, t in sorted(groups.items(), key=lambda kv: -kv[1].cost)
    )
    return lines


_MIX_COLS = (
    ("system_prefix", "fixed prefix"),
    ("prompt_initial", "initial prompt"),
    ("file_read_search", "file reads and search"),
    ("agent_calls_shell", "shell commands written"),
    ("agent_thinking", "thinking"),
    ("perfproof_cli", "perfproof CLI"),
    ("test_output", "test output"),
    ("profiler_output", "profiler"),
    ("git", "git"),
)


def mix_table(rows: list[tuple[str, Totals]]) -> list[str]:
    """Render cost mixes side by side, one row per set of sessions.

    Parameters
    ----------
    rows : list[tuple[str, Totals]]
        Row name and totals.

    Returns
    -------
    list[str]
        Markdown lines: the exact split of cost by usage type, then the
        estimated share of selected causes.
    """
    head = "| sessions | calls | cache read | cache write | output |"
    head += "".join(f" {n} |" for _c, n in _MIX_COLS)
    lines = [
        head + "\n",
        "|---|---:|---:|---:|---:|" + "---:|" * len(_MIX_COLS) + "\n",
    ]
    for name, t in rows:
        cost = by_cat(t.cost_by)
        whole = sum(cost.values())
        cells = "".join(
            f" {_pct(cost.get(c, 0.0), whole)} |" for c, _n in _MIX_COLS
        )
        lines.append(
            f"| {name} | {t.calls} | {_pct(t.cost_read, t.cost)} "
            f"| {_pct(t.cost_write, t.cost)} | {_pct(t.cost_out, t.cost)} |"
            + cells
            + "\n"
        )
    return lines


def write_table(sessions: list[Session]) -> list[str]:
    """Render what the agent wrote in file and shell calls.

    Parameters
    ----------
    sessions : list[Session]
        Claude Code sessions, each with its counted writes.

    Returns
    -------
    list[str]
        Markdown lines: calls, characters and share of the characters
        counted, within file-writing calls and within shell calls.
    """
    total: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for sess in sessions:
        for key, (n, chars) in sess.writes.items():
            total[key][0] += n
            total[key][1] += chars
    lines = [
        "| call kind | calls | characters | share of its family |\n",
        "|---|---:|---:|---:|\n",
    ]
    for family in ("Write", "shell"):
        keys = [
            k for k in total if k.startswith("shell") == (family == "shell")
        ]
        whole = sum(total[k][1] for k in keys)
        lines.extend(
            f"| {k} | {total[k][0]} | {total[k][1]} "
            f"| {_pct(total[k][1], whole)} |\n"
            for k in sorted(keys, key=lambda k: -total[k][1])
        )
    return lines


def sensitivity_table(
    primary: Totals, alternate: Totals, cats: tuple[str, ...]
) -> list[str]:
    """Render cause shares under the two shell-command classifiers.

    Parameters
    ----------
    primary, alternate : Totals
        Pooled totals under ``head`` and ``anywhere``.
    cats : tuple[str, ...]
        Categories to show.

    Returns
    -------
    list[str]
        Markdown lines with both shares and the range between them.
    """
    a = by_cat(primary.cost_by)
    b = by_cat(alternate.cost_by)
    wa, wb = sum(a.values()), sum(b.values())
    lines = [
        "| cause | first command | match anywhere | range |\n",
        "|---|---:|---:|---:|\n",
    ]
    for cat in cats:
        x, y = 100 * a.get(cat, 0.0) / wa, 100 * b.get(cat, 0.0) / wb
        lines.append(
            f"| {LABELS[cat]} | {x:.1f}% | {y:.1f}% "
            f"| {min(x, y):.1f}% to {max(x, y):.1f}% |\n"
        )
    return lines


def main() -> None:
    """Run the analysis and write the tables, CSV and chart."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("root", type=Path, help="~/.claude/projects directory")
    ap.add_argument("--codex", type=Path, default=None)
    ap.add_argument("--codex-after", default="2026-10-14")
    ap.add_argument(
        "--until",
        required=True,
        help="snapshot time, ISO-8601 UTC, for example 2026-10-18T23:00:00Z",
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    root = args.root.expanduser()
    sessions = load(
        root,
        args.codex.expanduser() if args.codex else None,
        args.codex_after,
        args.until,
    )
    claude = [s for s in sessions if s.group != "codex exec"]
    codex = [s for s in sessions if s.group == "codex exec"]
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    md: list[str] = []

    cal = calibrate(claude)
    derived = sum(derive_outputs(s, cal) for s in claude)
    n_val, val_err, val_bias = validate_outputs(claude, cal)
    rows = [(s, attribute(s, cal)) for s in claude]
    groups: dict[str, Totals] = defaultdict(Totals)
    pooled = Totals()
    for s, t in rows:
        groups[s.group].add(t)
        pooled.add(t)
    ordered_groups = dict(sorted(groups.items(), key=lambda kv: -kv[1].cost))
    n_logged_exact = sum(c.out_exact for s in claude for c in s.calls)

    md.append("## Exact usage by group\n\n")
    md += usage_table({**ordered_groups, "pooled": pooled})
    md.append(
        f"\nOutput used: {derived} of {pooled.calls} calls have a partial "
        f"logged output count and use a derived one; {n_logged_exact} "
        "calls have a final count.\n"
    )
    md.append("\n## Share of pooled cost by group\n\n")
    md += group_share_table(ordered_groups)
    md.append(
        "\nThe pooled mix is mostly perfproof's own development and "
        "review, not perfproof-driven optimisation runs.\n"
    )
    hand = [
        (f"{r[0].label} (T3.10 hand-run)", r[1])
        for r in rows
        if r[0].group == "T3.10 hand-runs"
    ]
    md.append("\n## Pooled mix beside the hand-runs\n\n")
    md += mix_table(
        [
            ("pooled, all Claude Code sessions", pooled),
            ("T3.10 hand-runs together", ordered_groups["T3.10 hand-runs"]),
            *hand,
        ]
    )
    md.append("\n## Pooled causes\n\n")
    md += cat_table(pooled)
    ing = by_cat(pooled.ingest)
    outside = {BASE_CAT, *OWN_CATS}
    md.append(
        f"\nIngested once: {_tok(sum(ing.values()))} tokens in total, "
        f"{_tok(sum(v for c, v in ing.items() if c not in outside))} of "
        "them tool results, messages, reminders and prompts. Re-read from "
        f"the cache: {_tok(sum(by_cat(pooled.reread).values()))} tokens.\n"
    )
    md.append("\n## Causes by group\n")
    for name, t in ordered_groups.items():
        md.append(f"\n### {GROUP_NAMES.get(name, name)}\n\n")
        md += cat_table(t)
    md.append("\n## Drill-down, pooled\n")
    for cat in ("file_read_search", "agent_calls_shell", "git", "test_output"):
        md.append(f"\n### {LABELS[cat]}\n\n")
        md += drill_table(pooled, cat)
    md.append("\n### Size of results\n\n")
    md += size_table(
        pooled,
        (
            "file_read_search",
            "git",
            "test_output",
            "web_gh",
            "subagent_reports",
            "injected_context",
        ),
    )
    md.append(
        f"\nFile reads of a path already read in the same session: "
        f"{pooled.repeat_items:.0f} reads, {_tok(pooled.repeat_tokens)} "
        "tokens ingested.\n"
    )
    md.append("\n## What the agent wrote in file and shell calls\n\n")
    md += write_table(claude)
    md.append("\n## Sensitivity to shell-command classification\n\n")
    set_classifier("anywhere")
    alt_sessions = load(root, None, args.codex_after, args.until)
    set_classifier("head")
    alt_claude = [s for s in alt_sessions if s.group != "codex exec"]
    alt_cal = calibrate(alt_claude)
    for s in alt_claude:
        derive_outputs(s, alt_cal)
    alt = Totals()
    for s in alt_claude:
        alt.add(attribute(s, alt_cal))
    md += sensitivity_table(
        pooled,
        alt,
        (
            "file_read_search",
            "git",
            "test_output",
            "web_gh",
            "perfproof_cli",
            "profiler_output",
            "other_shell",
        ),
    )
    md.append(
        "\nThe first column classifies by the command that runs first in "
        "the line; the second by the first rule that matches anywhere in "
        "its text. Only shell commands differ between the two.\n"
    )
    md.append("\n## Calibration and checks\n\n")
    md += _calibration_lines("Claude Code", cal)
    md.append(
        f"\nPer-call rescale so estimated context matches exact input: "
        f"mean {pooled.scale_sum / pooled.calls:.3f}, mean absolute log "
        f"{pooled.scale_dev / pooled.calls:.3f}; {pooled.drops} calls "
        "where input shrank.\n"
    )
    md.append(
        f"\nDerivation check on {n_val} calls with a final output count: "
        f"mean absolute error {100 * val_err:.0f}% of true output, derived "
        f"total {val_bias:.2f} of true total.\n"
    )
    models: dict[str, int] = defaultdict(int)
    for s in claude:
        for m in s.models:
            models[m] += 1
    md.append(
        "\nModels seen, as sessions that used each model (a session can "
        "use several, so the counts overlap): "
        + ", ".join(f"{m} {n}" for m, n in sorted(models.items()))
        + ".\n"
    )

    if codex:
        ccal = calibrate(codex)
        crows = [(s, attribute(s, ccal, priced=False)) for s in codex]
        ctot = Totals()
        for _s, t in crows:
            ctot.add(t)
        md.append("\n## codex exec sessions (tokens, unweighted)\n\n")
        md.append(
            f"{ctot.sessions} sessions, {ctot.calls} calls, input "
            f"{_tok(ctot.total_in)} of which cached "
            f"{_pct(ctot.read, ctot.total_in)}, output {_tok(ctot.out)} "
            f"(reasoning {_tok(ctot.think)}). Input and output are exact "
            "usage; the split below is estimated. Share is of all tokens, "
            "cached input included, because the cached-input discount is "
            "not known here.\n\n"
        )
        md += cat_table(ctot)
        md.append("\n")
        md += _calibration_lines("codex exec", ccal)
        rows += crows
    (out / "tables.md").write_text("".join(md), encoding="utf-8")

    per = [
        "# Per-session tables\n\n",
        "One table per session that is not a subagent transcript; "
        "subagent transcripts are in `tables.md` by agent type and in "
        "`sessions.csv` one by one.\n",
    ]
    for s, t in sorted(rows, key=lambda r: -r[1].cost):
        if s.group.startswith("subagent:"):
            continue
        per.append(f"\n## {s.label} ({s.group})\n\n")
        per.append(
            f"{t.calls} calls; input {_tok(t.total_in)} (cache read "
            f"{_pct(t.read, t.total_in)}); output {_tok(t.out)}.\n\n"
        )
        per += cat_table(t, top=10)
    (out / "per-session.md").write_text("".join(per), encoding="utf-8")

    overview = session_summary(
        [
            r
            for r in rows
            if not r[0].group.startswith("subagent:")
            and r[0].group != "codex exec"
        ]
    )
    (out / "overview.md").write_text("".join(overview), encoding="utf-8")
    write_csv(rows, out / "sessions.csv")
    chart(
        pooled,
        out / "cost_split.png",
        ordered_groups["subagent:general-purpose"].cost / pooled.cost,
    )
    sys.stdout.write(
        f"{len(claude)} Claude and {len(codex)} Codex sessions analysed; "
        f"wrote {out}\n"
    )


if __name__ == "__main__":
    main()
