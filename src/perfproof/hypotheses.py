"""The append-only hypothesis log (``docs/discovery.md``, "Hypotheses").

``.perfproof/hypotheses/<workload>.jsonl`` holds one
`perfproof.schema` hypothesis event per line. A hypothesis is the
sequence of events that share a ``hypothesis_id``. Nothing is edited or
deleted: a correction is a new event naming the one it corrects.

Concurrent writers
------------------
Several sessions may append to one log at once. Two things protect it.
Each event is written with a single ``write`` on a descriptor opened
``O_APPEND``, and a line may not exceed `MAX_LINE_BYTES`, so a line is
never split by another writer's line; this relies on the local
filesystem's append semantics (macOS APFS, Linux ext4, xfs, tmpfs). And
`record_event` holds an exclusive ``flock`` on the log file around
read, check and append, so the checks (an unknown hypothesis, a skipped
step, the refusal to implement a refuted hypothesis) see the log as it
will be. A writer that bypasses `record_event` can still race; reading
replays the whole log through the same checks and reports a violation
on its hypothesis (`HypothesisView.violations`), and skips a repeated
event ID as a corrupt line.

A line that cannot be read (not UTF-8, not JSON, not a valid event, a
repeated event ID) is skipped when reading and reported with its line
number. The file is never rewritten, so the line stays. A writer that
died mid-line leaves no final newline; the next append starts on a new
line, so the half line is one corrupt line and no valid event is lost.

Staleness
---------
Staleness is derived when the log is read, not appended. A hypothesis is
stale when its code anchor's definition changed or vanished, the
workload's spec hash differs, a project entry it relied on has another
version, or a ``stale`` event was recorded after its last
``revalidated`` event. A code anchor is matched by file and qualified
name, and its hash is that of the definition's source (decorators
through the last line, de-indented), not of its position, so a function
that moves within its file keeps its anchor. Only Python files can be
checked; for another language the anchor is reported as unchecked, never
as fresh.
"""

import ast
import fcntl
import hashlib
import os
import textwrap
import time
import warnings
from collections.abc import Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import ValidationError

from perfproof.schema import (
    HYPOTHESIS_EVENT,
    Anchors,
    CodeAnchor,
    FalsificationEvent,
    HypothesisEvent,
    HypothesisStaleness,
    ImplementationEvent,
    ObservationEvent,
    OutcomeEvent,
    PredictionEvent,
    ReorderedEvent,
    RevalidatedEvent,
    SkippedEvent,
    StaleEvent,
)

__all__ = [
    "LOCK_TIMEOUT_SECONDS",
    "MAX_LINE_BYTES",
    "STEPS",
    "CodeState",
    "CorruptLine",
    "HypothesisLogError",
    "HypothesisView",
    "LogContents",
    "StaleReason",
    "append_event",
    "assess_staleness",
    "check_event",
    "code_state",
    "current_workload_spec_hash",
    "definition_hash",
    "find_past",
    "log_lock",
    "log_path",
    "read_log",
    "record_event",
    "views",
]

STATE_DIR: Final = ".perfproof"
"""Directory of perfproof's state in the repository."""

MAX_LINE_BYTES: Final = 64 * 1024
"""Largest line, newline included, that `append_event` writes."""

LOCK_TIMEOUT_SECONDS: Final = 10.0
"""Longest `log_lock` waits for another session, in seconds."""

STEPS: Final = (
    "observation",
    "mechanism",
    "prediction",
    "falsification",
    "implementation",
    "outcome",
)
"""The chain, in order."""

type Step = Literal[
    "observation",
    "mechanism",
    "prediction",
    "falsification",
    "implementation",
    "outcome",
]
type StepState = Literal["done", "skipped", "missing"]
type CodeState = Literal["current", "changed", "missing", "unchecked"]

_DEFINITIONS: Final = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

_STEP_INDEX: Final = {name: i for i, name in enumerate(STEPS)}


class HypothesisLogError(Exception):
    """An event the log refuses, or a log that cannot be used.

    Parameters
    ----------
    code : str
        Stable snake_case identifier, such as ``refuted_hypothesis``.
    message : str
        What went wrong and what to do about it.
    details : Mapping of str to str, optional
        Structured context.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Mapping[str, str] = {} if details is None else details


@dataclass(frozen=True)
class CorruptLine:
    """A line of the log that was skipped.

    Attributes
    ----------
    number : int
        1-based line number.
    reason : str
        Why it could not be used.
    """

    number: int
    reason: str


@dataclass(frozen=True)
class LogContents:
    """What one log file holds.

    Attributes
    ----------
    events : tuple of HypothesisEvent
        Valid events in file order.
    corrupt : tuple of CorruptLine
        Lines skipped, which stay in the file.
    """

    events: tuple[HypothesisEvent, ...]
    corrupt: tuple[CorruptLine, ...]


@dataclass(frozen=True)
class StaleReason:
    """One reason a hypothesis is stale.

    Attributes
    ----------
    anchor : str
        ``code``, ``workload`` or ``project_entry``.
    detail : str
        What changed.
    """

    anchor: HypothesisStaleness
    detail: str


@dataclass(frozen=True)
class HypothesisView:
    """One hypothesis, derived from its events.

    Attributes
    ----------
    hypothesis_id : str
        Its ID.
    workload : str
        Workload whose log holds it.
    events : tuple of HypothesisEvent
        Its events in file order.
    anchors : Anchors
        Those of the observation, or of the last ``revalidated`` event.
    retry_of : str or None
        The refuted hypothesis it retries.
    steps : tuple of (str, str)
        Each step of the chain with ``done``, ``skipped`` or
        ``missing``.
    recorded_stale : StaleReason or None
        A ``stale`` event recorded after the last ``revalidated`` one.
    violations : tuple of str
        Events of this hypothesis that break the chain's rules when the
        log is replayed, such as an implementation of a refuted
        hypothesis that a racing session recorded.
    """

    hypothesis_id: str
    workload: str
    events: tuple[HypothesisEvent, ...]
    anchors: Anchors
    retry_of: str | None
    steps: tuple[tuple[Step, StepState], ...]
    recorded_stale: StaleReason | None
    violations: tuple[str, ...] = ()

    @property
    def next_step(self) -> Step | None:
        """The first step neither done nor skipped, or ``None``."""
        for name, state in self.steps:
            if state == "missing":
                return name
        return None

    @property
    def falsification(self) -> FalsificationEvent | None:
        """The latest falsification that no event corrects."""
        return _last(_effective(self.events), FalsificationEvent)

    @property
    def prediction(self) -> PredictionEvent | None:
        """The latest prediction that no event corrects."""
        return _last(_effective(self.events), PredictionEvent)

    @property
    def outcome(self) -> OutcomeEvent | None:
        """The latest outcome that no event corrects."""
        return _last(_effective(self.events), OutcomeEvent)

    @property
    def first_recorded(self) -> datetime:
        """When its first event was recorded."""
        return self.events[0].recorded_at

    @property
    def last_recorded(self) -> datetime:
        """When its last event was recorded."""
        return max(e.recorded_at for e in self.events)


def log_path(root: Path, workload: str) -> Path:
    """Return the log file of a workload.

    Parameters
    ----------
    root : Path
        Root of the user's repository.
    workload : str
        Workload ID.

    Returns
    -------
    Path
        ``root/.perfproof/hypotheses/<workload>.jsonl``.

    Raises
    ------
    HypothesisLogError
        ``invalid_name`` if ``workload`` is not a plain name, so it
        cannot leave the state directory.
    """
    if not _is_name(workload):
        msg = f"{workload!r} is not a workload name"
        raise HypothesisLogError("invalid_name", msg, {"name": workload})
    return root / STATE_DIR / "hypotheses" / f"{workload}.jsonl"


def _is_name(value: str) -> bool:
    """Whether ``value`` matches `perfproof.schema.Name`."""
    return (
        bool(value)
        and value[0].isascii()
        and value[0].isalnum()
        and all(c.isascii() and (c.isalnum() or c in "._-") for c in value)
    )


def read_log(path: Path) -> LogContents:
    """Read a log, skipping the lines that cannot be used.

    Parameters
    ----------
    path : Path
        The log file; a missing file is an empty log.

    Returns
    -------
    LogContents
        The valid events in file order and the skipped lines. The file
        is not changed. Time and memory are linear in its size.

    Raises
    ------
    HypothesisLogError
        ``log_unreadable`` if the file exists but cannot be read.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return LogContents((), ())
    except OSError as exc:
        msg = f"cannot read {path}: {exc}"
        raise HypothesisLogError(
            "log_unreadable", msg, {"path": str(path)}
        ) from exc
    events: list[HypothesisEvent] = []
    corrupt: list[CorruptLine] = []
    seen: set[str] = set()
    for number, raw in enumerate(data.split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            corrupt.append(CorruptLine(number, "not valid UTF-8"))
            continue
        try:
            event = HYPOTHESIS_EVENT.validate_json(text)
        except ValidationError as exc:
            corrupt.append(CorruptLine(number, _why(exc)))
            continue
        if event.event_id in seen:
            reason = f"event_id {event.event_id} appears earlier"
            corrupt.append(CorruptLine(number, reason))
            continue
        seen.add(event.event_id)
        events.append(event)
    return LogContents(tuple(events), tuple(corrupt))


def _why(exc: ValidationError) -> str:
    """Return a one-line reason for a line that did not validate."""
    errors = exc.errors()
    if any(e["type"] == "json_invalid" for e in errors):
        return "not valid JSON"
    first = errors[0]
    where = ".".join(str(p) for p in first["loc"])
    return f"not a valid event: {where}: {first['msg']}"[:200]


def append_event(path: Path, event: HypothesisEvent) -> None:
    """Append one event as one line, without a check of the chain.

    A single ``write`` on an ``O_APPEND`` descriptor, so concurrent
    appends from other processes never interleave (module docstring).

    Parameters
    ----------
    path : Path
        The log file; it and its directory are created if needed.
    event : HypothesisEvent
        The event to record.

    Raises
    ------
    HypothesisLogError
        ``line_too_long`` if the line would exceed `MAX_LINE_BYTES`;
        ``log_write_failed`` if the file cannot be written, or the
        write was short (the half line then stays, and is skipped by
        `read_log`).
    """
    line = event.model_dump_json().encode("utf-8") + b"\n"
    if len(line) > MAX_LINE_BYTES:
        msg = (
            f"the event is {len(line)} bytes; a line is at most "
            f"{MAX_LINE_BYTES}. Put the detail in an evidence file and "
            "record its path and hash."
        )
        raise HypothesisLogError("line_too_long", msg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.fstat(fd).st_size
            lead = b""
            if size and os.pread(fd, 1, size - 1) != b"\n":
                lead = b"\n"
            data = lead + line
            written = os.write(fd, data)
        finally:
            os.close(fd)
    except OSError as exc:
        msg = f"cannot write {path}: {exc}"
        raise HypothesisLogError(
            "log_write_failed", msg, {"path": str(path)}
        ) from exc
    if written != len(data):
        msg = f"short write to {path}: {written} of {len(data)} bytes"
        raise HypothesisLogError("log_write_failed", msg, {"path": str(path)})


def _effective(events: Iterable[HypothesisEvent]) -> list[HypothesisEvent]:
    """Drop the events that another event corrects."""
    seq = list(events)
    corrected = {e.corrects for e in seq if e.corrects is not None}
    return [e for e in seq if e.event_id not in corrected]


def _last[E: HypothesisEvent](
    events: Iterable[HypothesisEvent], kind: type[E]
) -> E | None:
    """Return the last event of one class, or ``None``."""
    found: E | None = None
    for event in events:
        if isinstance(event, kind):
            found = event
    return found


def _step_of(event: HypothesisEvent) -> Step | None:
    """Return the step an event records, or ``None`` for a non-step."""
    if isinstance(event, SkippedEvent):
        return event.step
    if isinstance(event, StaleEvent | ReorderedEvent | RevalidatedEvent):
        return None
    return event.event


def _step_states(
    events: Sequence[HypothesisEvent],
) -> tuple[tuple[Step, StepState], ...]:
    done = {e.event for e in events if _step_of(e) == e.event}
    skipped = {e.step for e in events if isinstance(e, SkippedEvent)}
    out: list[tuple[Step, StepState]] = []
    for name in STEPS:
        state: StepState = (
            "done"
            if name in done
            else "skipped"
            if name in skipped
            else "missing"
        )
        out.append((name, state))
    return tuple(out)


def _build(
    workload: str, hypothesis_id: str, events: Sequence[HypothesisEvent]
) -> HypothesisView | None:
    """Derive a view; ``None`` when no observation was recorded."""
    first = next((e for e in events if isinstance(e, ObservationEvent)), None)
    if first is None:
        return None
    anchors = first.anchors
    recorded: StaleReason | None = None
    for e in events:
        if isinstance(e, RevalidatedEvent):
            anchors = e.anchors
            recorded = None
        elif isinstance(e, StaleEvent):
            recorded = StaleReason(e.anchor, e.detail)
    return HypothesisView(
        hypothesis_id=hypothesis_id,
        workload=workload,
        events=tuple(events),
        anchors=anchors,
        retry_of=first.retry_of,
        steps=_step_states(events),
        recorded_stale=recorded,
    )


def views(
    workload: str, events: Iterable[HypothesisEvent]
) -> list[HypothesisView]:
    """Group events into hypotheses.

    Parameters
    ----------
    workload : str
        Workload whose log the events came from.
    events : iterable of HypothesisEvent
        Events in file order.

    Returns
    -------
    list of HypothesisView
        One per hypothesis that has an observation, in the order of its
        first event. Events of a hypothesis with no observation are
        ignored.
    """
    seq = list(events)
    broken = _violations(seq)
    out: list[HypothesisView] = []
    for hid, group in _group(seq).items():
        view = _build(workload, hid, group)
        if view is not None:
            out.append(replace(view, violations=tuple(broken.get(hid, ()))))
    return out


def _group(
    events: Iterable[HypothesisEvent],
) -> dict[str, list[HypothesisEvent]]:
    grouped: dict[str, list[HypothesisEvent]] = {}
    for e in events:
        grouped.setdefault(e.hypothesis_id, []).append(e)
    return grouped


def _violations(
    events: Sequence[HypothesisEvent],
) -> dict[str, list[str]]:
    """Replay the log through `check_event`; quadratic in its length."""
    out: dict[str, list[str]] = {}
    for i, event in enumerate(events):
        try:
            check_event(events[:i], event)
        except HypothesisLogError as exc:
            out.setdefault(event.hypothesis_id, []).append(
                f"{event.event_id}: {exc.message}"
            )
    return out


def check_event(
    history: Sequence[HypothesisEvent], event: HypothesisEvent
) -> None:
    """Refuse an event that breaks the chain or the log's rules.

    Parameters
    ----------
    history : sequence of HypothesisEvent
        The log's valid events so far, in file order.
    event : HypothesisEvent
        The event to be appended.

    Raises
    ------
    HypothesisLogError
        ``duplicate_event`` (the event ID is used), ``unknown_event``
        (``corrects`` names no event of this hypothesis),
        ``invalid_correction`` (``corrects`` names an event of another
        type, or of another step for a skip), ``empty_override_reason``,
        ``retry_unmarked`` (an observation of code a refuted hypothesis
        was about, unchanged, without ``retry_of``),
        ``duplicate_hypothesis`` (an observation for an ID already
        observed), ``unknown_hypothesis`` (no observation yet),
        ``unknown_retry`` (``retry_of`` is not in this log),
        ``unknown_before`` (a reorder names a hypothesis not in this
        log), ``step_missing`` (an earlier step is neither recorded nor
        skipped with a reason), or ``refuted_hypothesis`` (an
        implementation of a hypothesis whose cheap test refuted it,
        with no ``override_reason``).
    """
    if any(h.event_id == event.event_id for h in history):
        msg = f"event_id {event.event_id} is already in this log"
        raise HypothesisLogError(
            "duplicate_event", msg, {"event_id": event.event_id}
        )
    mine = [h for h in history if h.hypothesis_id == event.hypothesis_id]
    observed = any(isinstance(h, ObservationEvent) for h in mine)
    if isinstance(event, ObservationEvent):
        _check_observation(history, event, observed=observed)
        return
    if not observed:
        msg = (
            f"hypothesis {event.hypothesis_id} has no observation; record "
            "one first"
        )
        raise HypothesisLogError(
            "unknown_hypothesis", msg, {"hypothesis_id": event.hypothesis_id}
        )
    if event.corrects is not None:
        _check_correction(mine, event)
    if isinstance(event, ReorderedEvent) and event.before not in {
        h.hypothesis_id for h in history if isinstance(h, ObservationEvent)
    }:
        msg = f"hypothesis {event.before} is not in this log"
        raise HypothesisLogError(
            "unknown_before", msg, {"hypothesis_id": event.before}
        )
    step = _step_of(event)
    if step is not None:
        _check_prerequisites(mine, step)
    if isinstance(event, ImplementationEvent):
        _check_not_refuted(mine, event)
    if isinstance(event, FalsificationEvent) and event.corrects is None:
        _check_rerun_marked(mine, event)


def _check_correction(
    mine: Sequence[HypothesisEvent], event: HypothesisEvent
) -> None:
    """Require a correction to match the step and type it corrects."""
    target = next((h for h in mine if h.event_id == event.corrects), None)
    if target is None:
        msg = (
            f"{event.corrects} is not an event of hypothesis "
            f"{event.hypothesis_id}"
        )
        raise HypothesisLogError(
            "unknown_event", msg, {"event_id": event.corrects or ""}
        )
    same_skip = (
        not isinstance(event, SkippedEvent)
        or not isinstance(target, SkippedEvent)
        or target.step == event.step
    )
    if type(target) is not type(event) or not same_skip:
        msg = (
            f"{event.event_id} is a {event.event} event and cannot correct "
            f"{target.event_id}, a {target.event} event; a correction is "
            "the same step and type as what it corrects"
        )
        raise HypothesisLogError(
            "invalid_correction", msg, {"event_id": target.event_id}
        )


def _check_observation(
    history: Sequence[HypothesisEvent],
    event: ObservationEvent,
    *,
    observed: bool,
) -> None:
    if observed:
        msg = f"hypothesis {event.hypothesis_id} already has an observation"
        raise HypothesisLogError(
            "duplicate_hypothesis",
            msg,
            {"hypothesis_id": event.hypothesis_id},
        )
    if event.retry_of is not None and event.retry_of not in {
        h.hypothesis_id for h in history if isinstance(h, ObservationEvent)
    }:
        msg = f"retry_of {event.retry_of} is not a hypothesis in this log"
        raise HypothesisLogError(
            "unknown_retry", msg, {"hypothesis_id": event.retry_of}
        )
    if event.retry_of is None:
        _check_retry_marked(history, event)


def _check_retry_marked(
    history: Sequence[HypothesisEvent], event: ObservationEvent
) -> None:
    """Unchanged code of a refuted hypothesis is retried on purpose."""
    new = {
        (a.file, a.qualified_name, a.source_hash) for a in event.anchors.code
    }
    for hid, group in _group(history).items():
        view = _build("", hid, group)
        if view is None:
            continue
        test = view.falsification
        if test is None or test.result != "refuted":
            continue
        old = {
            (a.file, a.qualified_name, a.source_hash)
            for a in view.anchors.code
        }
        if old & new:
            msg = (
                f"hypothesis {hid} was refuted on this code, which has not "
                "changed; record this as a retry with retry_of and "
                "what_differs"
            )
            raise HypothesisLogError(
                "retry_unmarked", msg, {"hypothesis_id": hid}
            )


def _check_prerequisites(mine: Sequence[HypothesisEvent], step: Step) -> None:
    """Every step before ``step`` is recorded or skipped."""
    states = dict(_step_states(mine))
    for earlier in STEPS[: _STEP_INDEX[step]]:
        if states[earlier] == "missing":
            msg = (
                f"step {earlier} comes before {step} and is neither "
                f"recorded nor skipped; record it, or a skipped event for "
                f"step {earlier} that says why"
            )
            raise HypothesisLogError(
                "step_missing", msg, {"step": earlier, "before": step}
            )


def _check_rerun_marked(
    mine: Sequence[HypothesisEvent], event: FalsificationEvent
) -> None:
    """Refuse a new cheap test after a refutation unless it corrects it.

    Otherwise a later ``supported`` result would quietly lift the
    refusal to implement, with nothing in the log saying the first
    result was set aside.
    """
    test = _last(_effective(mine), FalsificationEvent)
    if test is not None and test.result == "refuted":
        msg = (
            f"hypothesis {event.hypothesis_id} was refuted by "
            f"{test.event_id}; a new cheap test must set corrects to "
            f"{test.event_id} and say what differs"
        )
        raise HypothesisLogError(
            "rerun_unmarked",
            msg,
            {
                "hypothesis_id": event.hypothesis_id,
                "refuted_by": test.event_id,
            },
        )


def _check_not_refuted(
    mine: Sequence[HypothesisEvent], event: ImplementationEvent
) -> None:
    if event.override_reason is not None and not event.override_reason.strip():
        msg = "override_reason is empty; say why it is implemented anyway"
        raise HypothesisLogError("empty_override_reason", msg)
    test = _last(_effective(mine), FalsificationEvent)
    if (
        test is not None
        and test.result == "refuted"
        and event.override_reason is None
    ):
        msg = (
            f"hypothesis {event.hypothesis_id} was refuted by its cheap "
            f"test ({test.event_id}); it is not implemented. Retry it on "
            "different code as a new hypothesis with retry_of and "
            "what_differs, or give override_reason."
        )
        raise HypothesisLogError(
            "refuted_hypothesis",
            msg,
            {
                "hypothesis_id": event.hypothesis_id,
                "falsification": test.event_id,
            },
        )


def definition_hash(source: str, qualified_name: str) -> str | None:
    """Hash one function or class definition of a Python source.

    The text hashed runs from the first decorator (or the ``def`` or
    ``class`` line) to the definition's last line, with common leading
    whitespace removed and newlines normalised, so moving the definition
    within its file, or nesting it differently, does not change it.

    Parameters
    ----------
    source : str
        Python source text.
    qualified_name : str
        Dotted path of nested classes and functions, such as
        ``TileWriter.write``; ``<locals>`` parts are ignored.

    Returns
    -------
    str or None
        Lowercase SHA-256 hex of the definition, or ``None`` if the
        source does not parse or has no such definition. Every
        definition of that name in its scope is hashed together, in
        source order, so a property's setter or an ``overload`` cannot
        hide the others.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    parts = [p for p in qualified_name.split(".") if p != "<locals>"]
    if not parts:
        return None
    frontier: list[ast.AST] = [tree]
    for part in parts:
        frontier = [
            child
            for node in frontier
            for child in getattr(node, "body", ())
            if isinstance(child, _DEFINITIONS) and child.name == part
        ]
        if not frontier:
            return None
    # ``ast`` counts lines at \n, \r\n and \r only, never at U+2028 or a
    # form feed, which str.splitlines would also split at.
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segments: list[str] = []
    for node in sorted(frontier, key=lambda n: getattr(n, "lineno", 0)):
        if not isinstance(node, _DEFINITIONS):
            return None
        first = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        text = "\n".join(lines[first - 1 : node.end_lineno])
        segments.append(textwrap.dedent(text))
    return hashlib.sha256("\n".join(segments).encode("utf-8")).hexdigest()


def code_state(root: Path, anchor: CodeAnchor) -> tuple[CodeState, str]:
    """Compare a code anchor with the code now.

    Parameters
    ----------
    root : Path
        Root of the user's repository.
    anchor : CodeAnchor
        The anchor.

    Returns
    -------
    tuple of (str, str)
        ``current`` (same definition hash), ``changed`` (another hash),
        ``missing`` (no file, or no such definition) or ``unchecked``
        (not a Python file, or not readable as UTF-8), with a detail.
    """
    if not anchor.file.endswith(".py"):
        return "unchecked", f"{anchor.file} is not a Python file"
    try:
        source = (root / anchor.file).read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing", f"{anchor.file} does not exist"
    except (OSError, UnicodeDecodeError) as exc:
        return "unchecked", f"{anchor.file} is not readable: {exc}"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ast.parse(source)
    except (SyntaxError, ValueError):
        return "unchecked", f"{anchor.file} does not parse"
    now = definition_hash(source, anchor.qualified_name)
    if now is None:
        detail = f"{anchor.qualified_name} is not in {anchor.file}"
        return "missing", detail
    if now != anchor.source_hash:
        detail = f"{anchor.qualified_name} in {anchor.file} changed"
        return "changed", detail
    return "current", f"{anchor.qualified_name} is unchanged"


def assess_staleness(
    root: Path,
    view: HypothesisView,
    *,
    workload_spec_hash: str | None,
    entry_versions: Mapping[tuple[str, str], int] | None = None,
) -> tuple[tuple[StaleReason, ...], tuple[StaleReason, ...]]:
    """List why a hypothesis is stale, and what could not be checked.

    Parameters
    ----------
    root : Path
        Root of the user's repository.
    view : HypothesisView
        The hypothesis.
    workload_spec_hash : str or None
        The workload's current spec hash; ``None`` when unknown, and
        then the workload anchor is reported as unchecked.
    entry_versions : mapping, optional
        Current version of each project entry, keyed by ``(kind, id)``;
        ``None`` when the project model could not be read, and then the
        entry anchors are reported as unchecked. An entry missing from a
        given mapping makes the hypothesis stale.

    Returns
    -------
    tuple of (tuple of StaleReason, tuple of StaleReason)
        The reasons, which are empty for a fresh hypothesis, and the
        anchors that could not be checked, each with why. A hypothesis
        with anything unchecked is not known to be fresh.
    """
    reasons: list[StaleReason] = []
    unchecked: list[StaleReason] = []
    if view.recorded_stale is not None:
        reasons.append(view.recorded_stale)
    for anchor in view.anchors.code:
        state, detail = code_state(root, anchor)
        if state in ("changed", "missing"):
            reasons.append(StaleReason("code", detail))
        elif state == "unchecked":
            unchecked.append(StaleReason("code", detail))
    if workload_spec_hash is None:
        unchecked.append(
            StaleReason(
                "workload", "the workload's current spec hash is unknown"
            )
        )
    elif workload_spec_hash != view.anchors.workload_spec_hash:
        reasons.append(
            StaleReason(
                "workload", f"workload {view.anchors.workload} was redefined"
            )
        )
    if view.anchors.entries:
        if entry_versions is None:
            unchecked.append(
                StaleReason("project_entry", "the project model was not read")
            )
        else:
            for ref in view.anchors.entries:
                now = entry_versions.get((ref.kind, ref.id))
                if now != ref.version:
                    detail = (
                        f"{ref.kind} {ref.id} is gone"
                        if now is None
                        else f"{ref.kind} {ref.id} is version {now}, "
                        f"not {ref.version}"
                    )
                    reasons.append(StaleReason("project_entry", detail))
    return tuple(reasons), tuple(unchecked)


@contextmanager
def log_lock(
    path: Path, timeout: float = LOCK_TIMEOUT_SECONDS
) -> Generator[None]:
    """Hold an exclusive advisory lock on a log file.

    Writers take it around read, check and append so that a check is
    made on the log as it will be, not as it was. Readers do not lock.
    ``flock`` is released when its holder exits, so a crash cannot leave
    the log locked. The file is created, empty, if it does not exist.

    Parameters
    ----------
    path : Path
        The log file.
    timeout : float, optional
        Seconds to wait for another session.

    Raises
    ------
    HypothesisLogError
        ``log_locked`` if the lock was not free in time;
        ``log_write_failed`` if the file cannot be opened.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        msg = f"cannot open {path}: {exc}"
        raise HypothesisLogError(
            "log_write_failed", msg, {"path": str(path)}
        ) from exc
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    msg = f"{path} is locked by another session"
                    raise HypothesisLogError(
                        "log_locked", msg, {"path": str(path)}
                    ) from None
                time.sleep(0.005)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def record_event(path: Path, event: HypothesisEvent) -> LogContents:
    """Check an event against the log and append it, under the lock.

    Parameters
    ----------
    path : Path
        The log file.
    event : HypothesisEvent
        The event to record.

    Returns
    -------
    LogContents
        The log as it was just before the append.

    Raises
    ------
    HypothesisLogError
        Any code of `check_event`, `append_event` and `log_lock`.
    """
    with log_lock(path):
        contents = read_log(path)
        check_event(contents.events, event)
        append_event(path, event)
    return contents


def current_workload_spec_hash(root: Path, workload: str) -> str | None:
    """Return the workload's current spec hash, or ``None`` if unknown.

    This is the one place that decides what "current" is. Until the
    project model can be read (T3.2), it is the only spec stored under
    ``.perfproof/benchmarks/<workload>/``; with none or several, which
    one is current is not known and the answer is ``None``. T3.2
    replaces the body with the workload entry's ``spec_hash``.

    Parameters
    ----------
    root : Path
        Root of the user's repository.
    workload : str
        Workload ID.

    Returns
    -------
    str or None
        SHA-256 hex of the current spec, or ``None`` when unknown.
    """
    directory = root / STATE_DIR / "benchmarks" / workload
    try:
        found = [
            p.stem
            for p in directory.iterdir()
            if p.suffix == ".json" and _is_sha256(p.stem)
        ]
    except OSError:
        return None
    return found[0] if len(found) == 1 else None


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def find_past(
    state_dir: Path,
    anchors: Anchors,
    *,
    exclude: tuple[str, str] | None = None,
) -> list[HypothesisView]:
    """Find earlier hypotheses on the same code, in every log.

    Two hypotheses are on the same code when a code anchor of each has
    the same file and qualified name, whatever its source hash.

    Parameters
    ----------
    state_dir : Path
        ``<repo>/.perfproof/hypotheses``.
    anchors : Anchors
        Anchors of the new hypothesis.
    exclude : tuple of (str, str), optional
        ``(workload, hypothesis_id)`` to leave out, the new one.

    Returns
    -------
    list of HypothesisView
        Newest first by last event. Reads every log in the directory,
        so the cost is linear in their total size.
    """
    wanted = {(a.file, a.qualified_name) for a in anchors.code}
    if not wanted or not state_dir.is_dir():
        return []
    found: list[HypothesisView] = []
    for path in sorted(state_dir.glob("*.jsonl")):
        workload = path.stem
        try:
            contents = read_log(path)
        except HypothesisLogError:
            continue
        for view in views(workload, contents.events):
            if (workload, view.hypothesis_id) == exclude:
                continue
            if any(
                (a.file, a.qualified_name) in wanted for a in _all_code(view)
            ):
                found.append(view)
    found.sort(key=lambda v: v.last_recorded, reverse=True)
    return found


def _all_code(view: HypothesisView) -> list[CodeAnchor]:
    """Code anchors of the observation and the revalidations."""
    out: list[CodeAnchor] = []
    for e in view.events:
        if isinstance(e, ObservationEvent | RevalidatedEvent):
            out.extend(e.anchors.code)
    return out
