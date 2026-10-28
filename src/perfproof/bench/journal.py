"""The files a detached compare keeps in its run directory.

A detached compare has no state but its run directory
(``docs/cli.md``, "Long runs"). This module owns the files that carry a
run's progress and make it resumable; `perfproof.bench.detach` owns the
process side (``pid``, signals, ``job.json``).

========================  ==============================================
``progress.json``         Phase, rounds done and looks taken, replaced
                          atomically after each round
``samples.jsonl``         One line per completed round, in order
``machine.json``          The machine fingerprint taken before anything
                          ran, and the start time, kept for a resume
========================  ==============================================

Every write happens between rounds, outside any timed region, and is
small. Nothing is fsynced: a flush to disk would be a burst of I/O in
front of the next round, and a ``kill -9`` of the process loses nothing
the kernel already holds. Only a machine crash can tear a line, and a
torn last line is dropped when the journal is read back.
"""

import contextlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from perfproof.schema import Arm, MachineFingerprint, Sample

__all__ = [
    "JOURNAL_NAME",
    "MACHINE_NAME",
    "METRIC_NAME",
    "PROGRESS_NAME",
    "Journal",
    "JournalError",
    "Resumed",
    "RoundEntry",
    "load_journal",
    "read_json",
    "write_json",
]

JOURNAL_NAME: Final = "samples.jsonl"
"""One line per completed round."""
PROGRESS_NAME: Final = "progress.json"
"""Phase and counts, replaced after each round."""
MACHINE_NAME: Final = "machine.json"
"""The first fingerprint and the start time of the run."""
METRIC_NAME: Final = "metric.json"
"""Run-directory file naming the metric kind the run measured."""

_ARMS: Final[tuple[Arm, Arm]] = ("baseline", "candidate")


class JournalError(Exception):
    """The journal in a run directory cannot be trusted."""


@dataclass(frozen=True, slots=True)
class RoundEntry:
    """One completed round, as measured and as journaled.

    Attributes
    ----------
    samples : tuple of perfproof.schema.Sample
        The round's two samples, in the order they ran.
    steps : Mapping of {"baseline", "candidate"} to float
        The resolution step of each arm's value, in the metric's unit.
    elapsed : float
        Seconds of round time spent when this round ended, summed over
        every process that worked on the run.
    duration : float
        Seconds this round took, wall clock.
    reported : Mapping of {"baseline", "candidate"} to a mapping
        Each arm's reported guardrail values, by metric name, as
        ``(value, resolution step)`` in the guardrail's unit. A
        guardrail the execution's ``PERFPROOF`` line left out is absent
        from its arm's mapping: that value is unknown. Empty for a
        journal written before reported guardrails were read.
    """

    samples: tuple[Sample, Sample]
    steps: Mapping[Arm, float]
    elapsed: float
    duration: float
    reported: Mapping[Arm, Mapping[str, tuple[float, float]]] = field(
        default_factory=dict[Arm, Mapping[str, tuple[float, float]]]
    )


@dataclass(frozen=True, slots=True)
class Resumed:
    """What a resumed compare starts from.

    Attributes
    ----------
    rounds : tuple of RoundEntry
        The completed rounds, in order, from round 0.
    machine : perfproof.schema.MachineFingerprint
        The fingerprint taken before the first round of the run.
    started : datetime.datetime
        When the run first started, with time zone.
    attempt : int
        1 for the first resume, 2 for the second, and so on. Log
        directories of a resumed attempt are kept apart from the
        earlier ones.
    """

    rounds: tuple[RoundEntry, ...]
    machine: MachineFingerprint
    started: datetime
    attempt: int
    metric_kind: str | None = None
    """The metric kind the first attempt measured (``wall`` where a
    ``counter`` fell back), or ``None`` when it was not recorded."""


def write_json(path: Path, document: Mapping[str, object]) -> None:
    """Replace ``path`` with ``document``, completely or not at all.

    Parameters
    ----------
    path : pathlib.Path
        File to write; its directory must exist.
    document : Mapping of str to object
        JSON-serializable; NaN and infinity are refused.

    Raises
    ------
    OSError
        If the file cannot be written.
    ValueError
        If ``document`` is not JSON-serializable.
    """
    data = json.dumps(document, allow_nan=False, sort_keys=True).encode()
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data + b"\n")
        tmp.replace(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def read_json(path: Path) -> dict[str, object] | None:
    """Read a JSON object file; ``None`` if missing or unreadable.

    Parameters
    ----------
    path : pathlib.Path
        File to read.

    Returns
    -------
    dict of str to object or None
        The object; ``None`` when the file is missing, is not valid
        JSON or does not hold an object.
    """
    try:
        document: object = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    return cast("dict[str, object]", document)


class Journal:
    """Append one line per completed round and keep ``progress.json``.

    Parameters
    ----------
    run_dir : pathlib.Path
        The run directory; it must exist.
    max_rounds : int
        The spec's round budget, shown in the progress.
    resume_after : int, default 0
        Rounds the journal already holds; the next one written must be
        this index. `load_journal` has already dropped any torn tail.

    Raises
    ------
    OSError
        If the journal cannot be opened.
    """

    def __init__(
        self, run_dir: Path, max_rounds: int, resume_after: int = 0
    ) -> None:
        self._dir = run_dir
        self._max_rounds = max_rounds
        self._count = resume_after
        self._looks = 0
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        self._fd = os.open(run_dir / JOURNAL_NAME, flags, 0o644)

    def begin(self, machine: MachineFingerprint, started: datetime) -> None:
        """Keep the first fingerprint and start time for a resume.

        Parameters
        ----------
        machine : perfproof.schema.MachineFingerprint
            The fingerprint taken before anything ran.
        started : datetime.datetime
            The start time, with time zone.
        """
        write_json(
            self._dir / MACHINE_NAME,
            {
                "started_at": started.isoformat(),
                "machine": machine.model_dump(mode="json"),
            },
        )

    def metric(self, kind: str) -> None:
        """Keep the metric kind measured, so a resume can't mix units.

        Parameters
        ----------
        kind : str
            ``wall``, ``cpu``, ``reported`` or ``counter``: what the
            samples of this run are.
        """
        write_json(self._dir / METRIC_NAME, {"kind": kind})

    def phase(self, phase: str) -> None:
        """Record the phase the run is in: ``gates`` or ``rounds``."""
        self._progress(phase)

    def round_done(self, index: int, entry: RoundEntry) -> None:
        """Append one completed round.

        The line is written with one ``write`` call, so an interrupted
        process leaves it whole or absent.

        Parameters
        ----------
        index : int
            The round index; it must be the next one.
        entry : RoundEntry
            The round.

        Raises
        ------
        ValueError
            If ``index`` is not the next round: a round is never
            written twice or out of order.
        OSError
            If the line cannot be written.
        """
        if index != self._count:
            msg = f"round {index} written, but round {self._count} is next"
            raise ValueError(msg)
        line = json.dumps(
            {
                "round": index,
                "samples": [s.model_dump(mode="json") for s in entry.samples],
                "steps": dict(entry.steps),
                "reported": {
                    arm: {k: list(v) for k, v in found.items()}
                    for arm, found in entry.reported.items()
                },
                "elapsed": entry.elapsed,
                "duration": entry.duration,
            },
            allow_nan=False,
            sort_keys=True,
        )
        data = line.encode() + b"\n"
        if os.write(self._fd, data) != len(data):
            msg = "the journal line was written short"
            raise OSError(msg)
        self._count += 1
        self._progress("rounds")

    def looks(self, taken: int) -> None:
        """Record the looks taken so far and refresh the progress."""
        self._looks = taken
        self._progress("rounds")

    def close(self) -> None:
        """Close the journal file."""
        with contextlib.suppress(OSError):
            os.close(self._fd)

    def _progress(self, phase: str) -> None:
        write_json(
            self._dir / PROGRESS_NAME,
            {
                "phase": phase,
                "rounds_done": self._count,
                "max_rounds": self._max_rounds,
                "looks": self._looks,
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )


def load_journal(run_dir: Path, *, repair: bool = False) -> list[RoundEntry]:
    """Read the completed rounds back, in order from round 0.

    Parameters
    ----------
    run_dir : pathlib.Path
        The run directory.
    repair : bool, default False
        Cut a torn last line (one without its newline, as only a
        machine crash leaves) from the file, so the next line appends
        cleanly.

    Returns
    -------
    list of RoundEntry
        One per whole line; empty when there is no journal. O(size of
        the journal).

    Raises
    ------
    JournalError
        If a line that ends in a newline is not a valid round, or the
        rounds are not 0, 1, 2 and so on. A journal like that was
        edited or damaged; no round is guessed.
    OSError
        If the journal cannot be read or repaired.
    """
    path = run_dir / JOURNAL_NAME
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return []
    *whole, tail = data.split(b"\n")
    entries: list[RoundEntry] = []
    for number, raw in enumerate(whole):
        try:
            entries.append(_parse(raw, number))
        except (ValueError, KeyError, TypeError) as exc:
            msg = f"{path}: line {number + 1} is not round {number}: {exc}"
            raise JournalError(msg) from exc
    if tail and repair:
        keep = len(data) - len(tail)
        with path.open("r+b") as f:
            f.truncate(keep)
    return entries


def _parse(raw: bytes, number: int) -> RoundEntry:
    """Parse one journal line, which must be round ``number``."""
    doc: object = json.loads(raw)
    if not isinstance(doc, dict):
        msg = "not an object"
        raise TypeError(msg)
    line = cast("dict[str, object]", doc)
    if line["round"] != number:
        msg = f"it says round {line['round']!r}"
        raise ValueError(msg)
    raw_samples = cast("list[object]", line["samples"])
    a, b = (Sample.model_validate(s) for s in raw_samples)
    if {a.version, b.version} != set(_ARMS) or {a.round, b.round} != {number}:
        msg = "its samples are not one round of both versions"
        raise ValueError(msg)
    steps = cast("dict[str, float]", line["steps"])
    reported = _parse_reported(line.get("reported", {}))
    return RoundEntry(
        samples=(a, b),
        steps={arm: float(steps[arm]) for arm in _ARMS},
        elapsed=float(cast("float", line["elapsed"])),
        duration=float(cast("float", line["duration"])),
        reported=reported,
    )


def _parse_reported(
    raw: object,
) -> dict[Arm, dict[str, tuple[float, float]]]:
    """Validate a journal line's reported guardrail values.

    Each arm maps metric names to ``[value, step]``: a finite positive
    value and a finite step of at least 0. Anything else is a damaged
    line, never a value to feed the statistics.
    """
    if not isinstance(raw, dict):
        msg = "reported is not an object"
        raise TypeError(msg)
    out: dict[Arm, dict[str, tuple[float, float]]] = {}
    for arm, found in cast("dict[str, object]", raw).items():
        if arm not in _ARMS or not isinstance(found, dict):
            msg = f"reported has {arm!r}, which is not an arm's values"
            raise ValueError(msg)
        values: dict[str, tuple[float, float]] = {}
        for name, pair in cast("dict[str, object]", found).items():
            ok = (
                isinstance(pair, list)
                and len(cast("list[object]", pair)) == 2
                and all(
                    isinstance(x, int | float) and not isinstance(x, bool)
                    for x in cast("list[object]", pair)
                )
            )
            if not ok:
                msg = f"reported {name!r} is not [value, step]"
                raise ValueError(msg)
            value, step = (float(x) for x in cast("list[float]", pair))
            if not (
                math.isfinite(value)
                and value > 0
                and math.isfinite(step)
                and step >= 0
            ):
                msg = f"reported {name!r} has an impossible value or step"
                raise ValueError(msg)
            values[name] = (value, step)
        out[arm] = values
    return out
