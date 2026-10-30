"""Run one probe: a profile of one spec, with the tree check around it.

A probe is `profile` run in this process on a stored spec, so it takes
the same code path as the command, with the same errors. Around it:

- the ``git status`` before and after, so a probe that changed the
  user's tree is caught and its profile thrown away;
- the new run directories, so a failed or timed-out probe leaves no
  partial profile and a successful one names exactly its runs;
- the old runs of a stale probe, deleted before it is profiled again,
  because ``rank`` reads every run of a name.
"""

import argparse
import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, cast

from perfproof.bench.spec import STATE_DIR
from perfproof.commands import CommandError, profile
from perfproof.commands._profile_show import RESULT_FILE
from perfproof.find import gittree
from perfproof.find.specs import define_spec
from perfproof.schema import BenchmarkSpec, ProbeRecord, SizeKnobSpec

__all__ = [
    "SIZE_FACTORS",
    "Outcome",
    "PlannedProbe",
    "native_leaf",
    "run_probe",
]

SIZE_FACTORS: Final = "1,2,4"
_LOG_TAIL_CHARS: Final = 2000
_LOG_LINES: Final = 20
_LINE_CHARS: Final = 500
_MIN_SECONDS: Final = 1.0
_TIMED_OUT: Final = "timed out after"


@dataclass(frozen=True, slots=True)
class PlannedProbe:
    """A probe `find` wants current, and how to run it.

    Attributes
    ----------
    key : str
        The record key: the spec name, ``<name>@native`` or
        ``<name>@sizes``.
    name : str
        The spec name ``profile`` runs.
    spec : perfproof.schema.BenchmarkSpec
        The spec; defined first when ``define`` is true.
    define : bool
        Whether it is a ``find-*`` spec to freeze before profiling.
    part_id : str or None
        The part it belongs to; ``None`` for a user workload.
    kind : str
        ``tests``, ``tests_native``, ``bench``, ``workload``,
        ``workload_native`` or ``workload_sizes``.
    requirement : bool
        Whether the depth table requires it.
    own_timeout : float
        The probe's own time limit, per run.
    template : tuple of str
        The ``--profiler-command`` template; empty for the default.
    own_modules : tuple of str
        ``--own-module`` values.
    knob : SizeKnobSpec or None
        The size knob, for a sized run.
    blocked : str or None
        A reason to record the probe failed without running it.
    exempt : Exemptions
        Part roots where more tool output is not a tree change.
    """

    key: str
    name: str
    spec: BenchmarkSpec
    define: bool
    part_id: str | None
    kind: str
    requirement: bool
    own_timeout: float
    template: tuple[str, ...] = ()
    own_modules: tuple[str, ...] = ()
    knob: SizeKnobSpec | None = None
    blocked: str | None = None
    exempt: gittree.Exemptions = gittree.NO_EXEMPTIONS
    stale: ProbeRecord | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class Outcome:
    """What running one probe came to.

    Attributes
    ----------
    record : ProbeRecord or None
        The record to keep, or ``None`` when the budget cut the probe
        short and it is still pending.
    spent_seconds : float
        Wall-clock seconds the probe took.
    """

    record: ProbeRecord | None
    spent_seconds: float


def _profiles_dir(root: Path, name: str) -> Path:
    return root / STATE_DIR / "profiles" / name


def _listing(root: Path, name: str) -> frozenset[str]:
    directory = _profiles_dir(root, name)
    if not directory.is_dir():
        return frozenset()
    return frozenset(p.name for p in directory.iterdir())


def _remove(root: Path, name: str, entries: frozenset[str]) -> None:
    """Delete run directories and growth files by name."""
    directory = _profiles_dir(root, name)
    for entry in entries:
        path = directory / entry
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def delete_runs(root: Path, name: str, run_ids: tuple[str, ...]) -> None:
    """Delete the saved runs of one probe, and its growth files.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    name : str
        The spec name.
    run_ids : tuple of str
        Run directory names the probe's record holds.
    """
    directory = _profiles_dir(root, name)
    entries: set[str] = set(run_ids)
    for run_id in run_ids:
        group = _size_group(directory / run_id / RESULT_FILE)
        if group is not None:
            entries.add(f"growth-{group}.json")
    _remove(root, name, frozenset(entries))


def _size_group(path: Path) -> str | None:
    try:
        saved: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(saved, dict):
        return None
    size = cast("dict[str, object]", saved).get("size")
    if not isinstance(size, dict):
        return None
    group = cast("dict[str, object]", size).get("group")
    return group if isinstance(group, str) else None


def _arguments(
    planned: PlannedProbe, root: Path, timeout: float
) -> argparse.Namespace:
    """Parse the ``profile`` arguments the probe stands for."""
    parser = argparse.ArgumentParser()
    profile.add_arguments(parser)
    argv = [
        planned.name,
        "--repo",
        str(root),
        "--spec-hash",
        planned.spec.spec_hash,
        "--timeout",
        repr(timeout),
    ]
    if planned.template:
        argv += ["--profiler-command", json.dumps(list(planned.template))]
    for module in planned.own_modules:
        argv += ["--own-module", module]
    if planned.knob is not None:
        flag = "--size-arg" if planned.knob.kind == "arg" else "--size-env"
        argv += [
            f"{flag}={planned.knob.name}={planned.knob.base}",
            "--sizes",
            SIZE_FACTORS,
        ]
    return parser.parse_args(argv)


def run_probe(
    root: Path,
    planned: PlannedProbe,
    *,
    tree: str,
    remaining_seconds: float,
    clock: Callable[[], float] = time.monotonic,
) -> Outcome:
    """Run one probe and return how it ended.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    planned : PlannedProbe
        The probe.
    tree : str
        The tree id the record is stamped with.
    remaining_seconds : float
        What is left of this call's budget; the probe's time limit is
        the smaller of this and its own.
    clock : callable, optional
        Monotonic seconds, for tests.

    Returns
    -------
    Outcome
        A record for a probe that finished, failed, timed out on its
        own limit or changed the tree; no record when the budget ended
        it before its own limit did.

    Raises
    ------
    CommandError
        ``tree_check_failed`` when git cannot be read; or the error of
        defining the spec.
    """
    spec_hash = planned.spec.spec_hash

    def record(
        status: str,
        run_ids: tuple[str, ...] = (),
        reason: str | None = None,
        tail: str | None = None,
    ) -> ProbeRecord:
        return ProbeRecord.model_validate(
            {
                "key": planned.key,
                "requirement": planned.requirement,
                "spec_hash": spec_hash,
                "tree_id": tree,
                "status": status,
                "run_ids": list(run_ids),
                "reason": reason,
                "log_tail": tail,
            }
        )

    if planned.blocked is not None:
        return Outcome(record("failed", reason=planned.blocked), 0.0)
    timeout = min(remaining_seconds, planned.own_timeout)
    if timeout < _MIN_SECONDS:
        return Outcome(None, 0.0)
    if planned.stale is not None:
        delete_runs(root, planned.name, planned.stale.run_ids)
    if planned.define:
        define_spec(root, planned.spec)
    before_listing = _listing(root, planned.name)
    try:
        before = gittree.status_entries(root, planned.exempt)
    except gittree.GitError as exc:
        raise CommandError("tree_check_failed", str(exc)) from exc
    args = _arguments(planned, root, timeout)
    started = clock()
    failure: CommandError | None = None
    try:
        profile.run(args)
    except CommandError as exc:
        failure = exc
    spent = clock() - started
    created = _listing(root, planned.name) - before_listing
    try:
        added = gittree.added_entries(
            before, gittree.status_entries(root, planned.exempt)
        )
    except gittree.GitError as exc:
        _remove(root, planned.name, created)
        raise CommandError("tree_check_failed", str(exc)) from exc
    if added:
        _remove(root, planned.name, created)
        shown = ", ".join(added)
        return Outcome(
            record("tree_modified", reason=f"tests_modified_tree: {shown}"),
            spent,
        )
    if failure is not None:
        tail = _log_tail(_profiles_dir(root, planned.name), created)
        _remove(root, planned.name, created)
        return _failed(planned, failure, timeout, spent, record, tail)
    runs = tuple(
        sorted(
            e
            for e in created
            if (_profiles_dir(root, planned.name) / e / RESULT_FILE).is_file()
        )
    )
    _remove(root, planned.name, created - set(runs) - _growth(created))
    return Outcome(record("done", run_ids=runs), spent)


def _growth(entries: frozenset[str]) -> frozenset[str]:
    return frozenset(e for e in entries if e.startswith("growth-"))


def _log_tail(directory: Path, created: frozenset[str]) -> str:
    """Return the last lines of the failed run's stdout and stderr.

    Read before the run directory is deleted. Only the newest created
    run that has logs is read; each file contributes its last
    `_LOG_LINES` lines, each cut to `_LINE_CHARS` characters.
    """
    for run in sorted(created, reverse=True):
        base = directory / run
        sections: list[str] = []
        for stem in ("unprofiled", "profiled"):
            for stream in ("stdout", "stderr"):
                path = base / f"{stem}.{stream}"
                try:
                    lines = path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()
                except OSError:
                    continue
                if lines:
                    body = "\n".join(
                        line[:_LINE_CHARS] for line in lines[-_LOG_LINES:]
                    )
                    sections.append(f"[{stem} {stream}]\n{body}")
        if sections:
            return "\n".join(sections)
    return ""


def _failed(
    planned: PlannedProbe,
    failure: CommandError,
    timeout: float,
    spent: float,
    record: Callable[..., ProbeRecord],
    log: str,
) -> Outcome:
    """Classify a probe that raised."""
    message = failure.message
    tail = log or message[-_LOG_TAIL_CHARS:]
    if _TIMED_OUT in message:
        if timeout < planned.own_timeout:
            return Outcome(None, spent)
        return Outcome(
            record("timeout", reason=f"timeout: {failure.code}", tail=tail),
            spent,
        )
    first = (message.splitlines() or [failure.code])[0].split(";")[0]
    return Outcome(
        record("failed", reason=f"failed: {failure.code}: {first}", tail=tail),
        spent,
    )


def native_leaf(root: Path, name: str, run_id: str, bar: float) -> bool:
    """Whether a profile's leaf frames are native code at the bar.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    name : str
        The spec name.
    run_id : str
        The saved run to read.
    bar : float
        ``min_effect``: the smallest self share that counts.

    Returns
    -------
    bool
        True when a hotspot whose ``frame_kind`` is ``native`` (a
        builtin or extension frame) has a self share of at least
        ``bar``. False when the run cannot be read: an unknown leaf is
        not evidence for one.
    """
    path = _profiles_dir(root, name) / run_id / RESULT_FILE
    try:
        saved: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(saved, dict):
        return False
    hotspots = cast("dict[str, object]", saved).get("hotspots")
    if not isinstance(hotspots, list):
        return False
    for item in cast("list[object]", hotspots):
        if not isinstance(item, dict):
            continue
        row = cast("dict[str, object]", item)
        share = row.get("self_fraction")
        function = row.get("function")
        is_native = row.get("frame_kind") == "native" or (
            isinstance(function, str) and function.startswith("<built-in")
        )
        if (
            is_native
            and isinstance(share, int | float)
            and not isinstance(share, bool)
            and share >= bar
        ):
            return True
    return False
