"""Find skill text that gives away an evaluation task's reference fix.

The skill is scored on tasks mined from real pull requests
(``eval/tasks/``). A playbook that names a task's fix would let the
skill pass that task from memory rather than by method, and would
inflate every score built on it. This module flags such lines;
``test_no_eval_leaks.py`` runs it over ``skills/``.

It is a title-and-path guard. A line leaks a task when it names one of
the reference change's files by path (with a directory, so a bare
``AGENTS.md`` is not a match), or shares at least two distinctive words
with the reference change's title and at least one of them appears in
the line as code (in backticks, or CamelCase or snake_case), the way a
fix's identifiers would. Distinctive means not generic performance
vocabulary and at least five letters.

The manifests hold no patch, so the guard never sees the diff: a line
that gives a fix away through an identifier from the diff, sharing at
most one word with a terse title, is not caught. Matching is per line.
Review new playbook text for those cases by hand.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "eval" / "tasks"
SKILLS = ROOT / "skills"
# Reviewed coincidences: a skill line and a task that share words but
# not a fix. Each entry names the skill file (relative to the skills
# directory), the task, the exact line text and why it is not a leak.
ALLOW = Path(__file__).resolve().parent / "leak_allow.json"
# JSON files under eval/tasks that are not task manifests.
NOT_TASKS = frozenset(["selection.json"])

# Words every performance write-up uses; matching on them says nothing
# about a particular fix.
GENERIC = frozenset(
    [
        "about",
        "after",
        "allocation",
        "allocations",
        "avoid",
        "avoids",
        "before",
        "better",
        "cache",
        "cached",
        "caching",
        "calls",
        "change",
        "changes",
        "check",
        "checks",
        "cheaper",
        "copies",
        "copy",
        "count",
        "creating",
        "every",
        "faster",
        "fewer",
        "first",
        "function",
        "functions",
        "improve",
        "improves",
        "improvement",
        "instead",
        "large",
        "larger",
        "later",
        "loops",
        "lookup",
        "memory",
        "needed",
        "never",
        "objects",
        "other",
        "perf",
        "performance",
        "reduce",
        "reduces",
        "remove",
        "removes",
        "repeated",
        "result",
        "results",
        "reuse",
        "skips",
        "speed",
        "speedup",
        "speeds",
        "string",
        "strings",
        "their",
        "there",
        "these",
        "thing",
        "those",
        "times",
        "using",
        "values",
        "when",
        "where",
        "which",
        "while",
        "without",
        "would",
    ]
)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]{4,}")
_CODE = re.compile(r"`([^`]+)`")
_IDENT = re.compile(
    r"\b(?:[a-z]+[A-Z]\w*|[A-Z][a-z0-9]+[A-Z]\w*|\w+_\w+|[A-Z][a-z]{4,})\b"
)


@dataclass(frozen=True)
class Leak:
    """One skill line that names part of a task's reference fix."""

    path: Path
    line: int
    task: str
    why: str


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text)} - GENERIC


def _code_words(text: str) -> set[str]:
    """Words a line shows as code: backticked or identifier-shaped."""
    inside = " ".join(_CODE.findall(text))
    return _words(inside) | {w.lower() for w in _IDENT.findall(text)}


def _manifests(tasks: Path) -> Iterable[tuple[str, str, list[str]]]:
    """Yield ``(id, title, files)`` for every task manifest.

    A manifest that cannot be read or lacks a reference change raises
    rather than being skipped, so a broken file cannot hide a leak.
    """
    for path in sorted(tasks.rglob("*.json")):
        if path.name in NOT_TASKS:
            continue
        try:
            data: object = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            raise ValueError(f"{path}: unreadable manifest: {error}") from None
        doc = cast("dict[str, object]", data) if isinstance(data, dict) else {}
        ref = doc.get("reference_change")
        if not isinstance(ref, dict) or not doc.get("id"):
            raise ValueError(f"{path}: no id or reference_change")
        change = cast("dict[str, object]", ref)
        title = str(change.get("title") or "")
        raw = change.get("files")
        items = cast("list[object]", raw) if isinstance(raw, list) else []
        files = [str(f) for f in items if f]
        yield str(doc["id"]), title, files


_ALLOW_KEYS = ("file", "task", "line", "reason")


@dataclass(frozen=True)
class Allowance:
    """One reviewed coincidence: a skill line that leaks no task."""

    file: str
    task: str
    line: str


def allowances(allow: Path = ALLOW) -> list[Allowance]:
    """Read the reviewed coincidences.

    Parameters
    ----------
    allow : Path
        The allowlist; a missing file means no allowances.

    Returns
    -------
    list of Allowance
        In file order.

    Raises
    ------
    ValueError
        If the file is not a list of entries that each give ``file``,
        ``task``, ``line`` and a non-empty ``reason``.
    """
    if not allow.exists():
        return []
    raw: object = json.loads(allow.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"{allow}: not a list")
    out: list[Allowance] = []
    for item in cast("list[object]", raw):
        entry = (
            cast("dict[str, object]", item) if isinstance(item, dict) else {}
        )
        fields = [str(entry.get(k) or "").strip() for k in _ALLOW_KEYS]
        if not all(fields):
            raise ValueError(f"{allow}: incomplete entry {item!r}")
        out.append(Allowance(*fields[:3]))
    return out


def _scan(
    skills: Path, tasks: Path, allow: Path
) -> tuple[list[Leak], set[Allowance]]:
    """Return the leaks found and the allowances no line matched."""
    titled = [
        (tid, _words(title), files) for tid, title, files in _manifests(tasks)
    ]
    allowed = set(allowances(allow))
    used: set[Allowance] = set()
    found: list[Leak] = []
    for path in sorted(skills.rglob("*.md")):
        rel = path.relative_to(skills).as_posix()
        for n, text in enumerate(path.read_text().splitlines(), start=1):
            words = _words(text)
            code = _code_words(text)
            for tid, title_words, files in titled:
                named = [f for f in files if "/" in f and f in text]
                shared = sorted(words & title_words)
                if named:
                    why = "names " + ", ".join(named)
                elif len(shared) >= 2 and set(shared) & code:
                    why = "shares " + ", ".join(shared) + " with the title"
                else:
                    continue
                key = Allowance(rel, tid, text.strip())
                if key in allowed and not named:
                    used.add(key)
                    continue
                found.append(Leak(path, n, tid, why))
    return found, allowed - used


def find_leaks(
    skills: Path = SKILLS, tasks: Path = TASKS, allow: Path = ALLOW
) -> list[Leak]:
    """Return every skill line that leaks a task's reference fix.

    Parameters
    ----------
    skills : Path
        Directory of skill Markdown files.
    tasks : Path
        Directory of task manifests.
    allow : Path
        Reviewed coincidences to skip; an entry never covers a line
        that names a reference file.

    Returns
    -------
    list of Leak
        In file and line order.

    Raises
    ------
    ValueError
        If a manifest or the allowlist is malformed.
    """
    return _scan(skills, tasks, allow)[0]


def unused_allowances(
    skills: Path = SKILLS, tasks: Path = TASKS, allow: Path = ALLOW
) -> list[Allowance]:
    """Return allowlist entries that match no flagged line.

    An entry goes stale when its line is edited or removed; a stale
    entry would otherwise sit in the list unreviewed.

    Parameters
    ----------
    skills, tasks, allow : Path
        As for `find_leaks`.

    Returns
    -------
    list of Allowance
        Sorted by file, task and line.
    """
    return sorted(
        _scan(skills, tasks, allow)[1],
        key=lambda a: (a.file, a.task, a.line),
    )
