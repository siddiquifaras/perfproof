"""Invalidation and revalidation of project model entries.

`find_stale` evaluates the triggers of ``docs/discovery.md`` that can
be seen from outside: ``file_changed``, ``lockfile_changed``,
``machine_differs``, ``perfproof_upgraded``, ``command_failed`` and
``output_shape_changed``. ``dependency_edited`` is raised at the moment
of the edit (`perfproof.project.lifecycle.propagate`). `check` marks
what is stale and then revalidates it.

**Probes.** ``command_failed`` and ``output_shape_changed`` need the
workload's command to be run. That is opt-in (``check(probe=True)``),
and only a ``confirmed`` workload is ever run: the developer accepted
it, and BS-5 of ``docs/skill.md`` forbids running a proposed one. A
probe records the exit status and an output *shape* (the output's
non-empty lines with every digit run replaced by ``#``, consecutive
repeats collapsed, then hashed), not the output, so timings do not
change it. The first successful probe of a workload is its baseline,
kept in ``.perfproof/project.probes.json`` because the model has no
field for an observation. A probe is not a measurement: no number from
it is ever used.

A probe result, when there is one for a stale workload, settles its
revalidation whatever the trigger was, so a failing command cannot hide
behind a file change.

**Revalidation** (``docs/discovery.md``): a stale target, workload or
how-to-run is derived again by `perfproof.project.detect.detect`. If its
claim is identical it returns to the status it came from, with the
refreshed provenance, and a ``revalidated`` line says so. If it differs
the new revision is ``proposed`` at the next version (``reproposed``),
and the developer must confirm it again. A frozen spec (``spec_hash``)
is carried over, except when the trigger was ``dependency_edited``: the
spec no longer matches its goal or constraints, so the re-derived
workload has none and so differs. A stale goal or constraint is stated
by the developer, cannot be derived, and stays stale until confirmed or
edited. An entry that detection no longer produces stays stale and is
reported; it is never retired silently.
"""

import hashlib
import json
import re
import subprocess
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final, cast

from perfproof.bench.spec import STATE_DIR
from perfproof.project.detect import Proposal
from perfproof.project.entries import (
    all_entries,
    dependencies,
    find,
    mark_stale,
    put,
    record,
    with_fields,
)
from perfproof.project.lifecycle import claim_changed, propagate
from perfproof.project.provenance import (
    hash_file,
    is_lockfile,
    needs_rederivation,
)
from perfproof.project.store import atomic_write_text
from perfproof.schema import (
    EntryKind,
    ProjectEntry,
    ProjectHistoryRecord,
    ProjectModel,
    StaleTrigger,
    WorkloadEntry,
)

__all__ = [
    "PROBES_FILE",
    "Baseline",
    "CheckResult",
    "Finding",
    "ProbeResult",
    "baseline_for",
    "find_stale",
    "load_baselines",
    "mark_findings",
    "own_files_differ",
    "revalidate",
    "run_probe",
    "save_baselines",
    "shape_of",
]

PROBES_FILE: Final = "project.probes.json"
PROBE_TIMEOUT_SECONDS: Final = 300.0
_PRECEDENCE: Final[tuple[StaleTrigger, ...]] = (
    "file_changed",
    "lockfile_changed",
    "command_failed",
    "output_shape_changed",
    "machine_differs",
    "perfproof_upgraded",
)
_DERIVED: Final = frozenset({"target", "workload", "how_to_run"})
_DIGITS: Final = re.compile(r"\d+")
_PROBE_TRIGGERS: Final = frozenset({"command_failed", "output_shape_changed"})


@dataclass(frozen=True)
class Finding:
    """One entry an invalidation trigger fired for.

    Attributes
    ----------
    entry_id : str
        The entry.
    trigger : StaleTrigger
        The trigger recorded: the first that fired in the fixed order
        file, lockfile, command, output shape, machine, upgrade.
    detail : str
        What changed, naming every trigger that fired.
    """

    entry_id: str
    trigger: StaleTrigger
    detail: str


@dataclass(frozen=True)
class ProbeResult:
    """What running a workload's command showed.

    Attributes
    ----------
    exit_code : int or None
        Exit status; ``None`` when it did not run or timed out.
    shape : str or None
        Output shape hash; ``None`` when the command did not finish.
    reason : str
        Why it did not finish, empty otherwise.
    """

    exit_code: int | None
    shape: str | None
    reason: str = ""

    @property
    def ok(self) -> bool:
        """Whether the command ran and exited 0."""
        return self.exit_code == 0 and self.shape is not None


@dataclass(frozen=True)
class Baseline:
    """The output shape a workload had when last accepted."""

    shape: str
    workload_version: int


@dataclass(frozen=True)
class CheckResult:
    """What `check` found and did.

    Attributes
    ----------
    model : ProjectModel
        The model after marking and revalidation.
    records : list of ProjectHistoryRecord
        The history lines of the changes, oldest first.
    marked_stale : tuple of Finding
        Entries that became stale in this run.
    revalidated, reproposed : tuple of str
        IDs derived again with no change, and with a change.
    unresolved : tuple of tuple of str
        ``(id, why)`` for entries still stale.
    unknown : tuple of str
        Checks that could not be made (a machine that could not be
        identified). Never treated as a pass or as a change.
    baselines : mapping of str to Baseline
        Probe baselines after this run, to be saved with the model.
    """

    model: ProjectModel
    records: list[ProjectHistoryRecord]
    marked_stale: tuple[Finding, ...] = ()
    revalidated: tuple[str, ...] = ()
    reproposed: tuple[str, ...] = ()
    unresolved: tuple[tuple[str, str], ...] = ()
    unknown: tuple[str, ...] = ()
    baselines: Mapping[str, Baseline] = field(
        default_factory=dict[str, Baseline]
    )


def shape_of(output: str) -> str:
    """Return the shape hash of a command's output.

    Parameters
    ----------
    output : str
        Text the command wrote.

    Returns
    -------
    str
        SHA-256 of the non-empty lines with digit runs replaced by
        ``#`` and consecutive duplicates collapsed.
    """
    lines: list[str] = []
    for raw in output.splitlines():
        line = _DIGITS.sub("#", raw.strip())
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def run_probe(
    root: Path,
    workload: WorkloadEntry,
    timeout: float = PROBE_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Run a workload's command once and describe what it did.

    Parameters
    ----------
    root : Path
        Repository root.
    workload : WorkloadEntry
        The workload; the caller has checked it is ``confirmed``.
    timeout : float, optional
        Time limit in seconds.

    Returns
    -------
    ProbeResult
        Never raises for a command that fails to start or times out.
    """
    try:
        done = subprocess.run(
            list(workload.command),
            cwd=root / workload.cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(None, None, f"timed out after {timeout:g} s")
    except OSError as exc:
        return ProbeResult(None, None, f"could not start: {exc}")
    return ProbeResult(done.returncode, shape_of(done.stdout))


def _probes_path(root: Path) -> Path:
    return root / STATE_DIR / PROBES_FILE


def load_baselines(root: Path) -> dict[str, Baseline]:
    """Read the probe baselines; absent or unreadable means none."""
    try:
        data = json.loads(_probes_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, Baseline] = {}
    if isinstance(data, dict):
        for key, item in cast("dict[str, object]", data).items():
            if isinstance(item, dict):
                body = cast("dict[str, object]", item)
                shape, version = body.get("shape"), body.get("version")
                if isinstance(shape, str) and isinstance(version, int):
                    out[key] = Baseline(shape, version)
    return out


def save_baselines(root: Path, baselines: Mapping[str, Baseline]) -> None:
    """Write the probe baselines atomically."""
    payload = {
        k: {"shape": b.shape, "version": b.workload_version}
        for k, b in sorted(baselines.items())
    }
    atomic_write_text(_probes_path(root), json.dumps(payload, indent=2) + "\n")


def baseline_for(
    baselines: Mapping[str, Baseline] | None, entry: ProjectEntry
) -> Baseline | None:
    """Return the entry's baseline if it was taken at this version."""
    base = (baselines or {}).get(entry.id)
    if base is None or base.workload_version != entry.version:
        return None
    return base


def _file_findings(root: Path, entry: ProjectEntry) -> list[tuple[str, str]]:
    changed: list[tuple[str, str]] = []
    for f in entry.provenance.files:
        try:
            now_hash: str | None = hash_file(root / f.path)
        except OSError:
            now_hash = None
        if now_hash != f.sha256:
            what = "missing" if now_hash is None else "changed"
            changed.append((f.path, what))
    return changed


def own_files_differ(root: Path, entry: ProjectEntry) -> bool:
    """Return whether an entry's recorded files differ from the disk.

    The recorded hashes are the ones from the entry's last derivation
    or confirmation, which revalidation never replaces without a
    confirmation, so this holds for any status.
    """
    return bool(_file_findings(root, entry))


def find_stale(
    root: Path,
    model: ProjectModel,
    *,
    host_id: str | None,
    probes: Mapping[str, ProbeResult] | None = None,
    baselines: Mapping[str, Baseline] | None = None,
    revisions: dict[EntryKind, str] | None = None,
    only: Collection[str] | None = None,
) -> tuple[list[Finding], list[tuple[str, str]]]:
    """Find the entries an invalidation trigger has fired for.

    Parameters
    ----------
    root : Path
        Repository root.
    model : ProjectModel
        Model to check.
    host_id : str or None
        `MachineFingerprint.host_id` of this machine; ``None`` when it
        could not be established.
    probes : mapping, optional
        Probe results by workload ID, from `run_probe`.
    baselines : mapping, optional
        Probe baselines by workload ID.
    revisions : dict, optional
        Derivation revisions, for tests; see
        `perfproof.project.provenance.DERIVATION_REVISION`.
    only : collection of str, optional
        Check only these entries.

    Returns
    -------
    findings : list of Finding
        One per ``proposed`` or ``confirmed`` entry a trigger fired
        for.
    unknown : list of tuple of str
        ``(entry ID, text)`` of each check that could not be made.
    """
    findings: list[Finding] = []
    unknown: list[tuple[str, str]] = []
    for entry in all_entries(model):
        if entry.status not in ("proposed", "confirmed"):
            continue
        if only is not None and entry.id not in only:
            continue
        fired: dict[StaleTrigger, str] = {}
        files = _file_findings(root, entry)
        plain = [f"{p} {w}" for p, w in files if not is_lockfile(p)]
        locks = [f"{p} {w}" for p, w in files if is_lockfile(p)]
        if plain:
            fired["file_changed"] = ", ".join(plain)
        if locks:
            fired["lockfile_changed"] = ", ".join(locks)
        result = (probes or {}).get(entry.id)
        if result is not None:
            if not result.ok:
                fired["command_failed"] = (
                    result.reason or f"exited {result.exit_code}"
                )
            else:
                base = baseline_for(baselines, entry)
                if base is not None and base.shape != result.shape:
                    fired["output_shape_changed"] = (
                        "the output no longer has the shape it had when "
                        "last accepted"
                    )
        if entry.host_id is not None:
            if host_id is None:
                unknown.append(
                    (
                        entry.id,
                        f"{entry.id} is bound to a machine and this machine "
                        "could not be identified",
                    )
                )
            elif host_id != entry.host_id:
                fired["machine_differs"] = (
                    f"set on host {entry.host_id[:12]}, now on {host_id[:12]}"
                )
        if entry.kind in _DERIVED and needs_rederivation(
            entry.kind, entry.provenance.perfproof_version, revisions
        ):
            fired["perfproof_upgraded"] = (
                f"derived by perfproof {entry.provenance.perfproof_version}"
            )
        if not fired:
            continue
        order: list[StaleTrigger] = [t for t in _PRECEDENCE if t in fired]
        first = order[0]
        detail = fired[first]
        if len(order) > 1:
            detail += f" (also: {', '.join(order[1:])})"
        findings.append(Finding(entry.id, first, detail))
    return findings, unknown


def _derived_entry(
    proposal: Proposal, entry: ProjectEntry
) -> ProjectEntry | None:
    pool: tuple[ProjectEntry, ...] = (
        *proposal.targets,
        *proposal.workloads,
        *proposal.how_to_run,
    )
    return next((e for e in pool if e.id == entry.id), None)


def _settle(
    model: ProjectModel,
    entry: ProjectEntry,
    proposal: Proposal,
    now: datetime,
    probes: Mapping[str, ProbeResult],
    bases: dict[str, Baseline],
    records: list[ProjectHistoryRecord],
    revalidated: list[str],
    reproposed: list[str],
    unresolved: list[tuple[str, str]],
) -> ProjectModel:
    """Settle one stale entry; see `revalidate`."""
    if entry.invalidation is None:
        return model
    trigger = entry.invalidation.trigger
    if entry.kind not in _DERIVED:
        unresolved.append(
            (entry.id, "stated by the developer: confirm or edit it")
        )
        return model
    derived = _derived_entry(proposal, entry)
    if derived is None:
        unresolved.append(
            (entry.id, "detection no longer produces it: retire it?")
        )
        return model
    recorded = {f.path: f.sha256 for f in derived.provenance.files}
    own_files = isinstance(entry, WorkloadEntry) and any(
        recorded.get(f.path) != f.sha256 for f in entry.provenance.files
    )
    if trigger == "dependency_edited" and not own_files:
        waiting = [
            d.id for d in dependencies(model, entry) if d.status != "confirmed"
        ]
        if waiting:
            unresolved.append(
                (
                    entry.id,
                    f"waiting for {', '.join(waiting)} to be confirmed",
                )
            )
            return model
    if isinstance(entry, WorkloadEntry) and isinstance(derived, WorkloadEntry):
        spec = None if trigger == "dependency_edited" else entry.spec_hash
        derived = with_fields(derived, spec_hash=spec)
    same = not claim_changed(entry, derived)
    result = probes.get(entry.id)
    if trigger in _PROBE_TRIGGERS and result is None and not own_files:
        unresolved.append(
            (entry.id, "run `project check --probe` to settle it")
        )
        return model
    shape_new: str | None = None
    if result is not None and not own_files:
        if not result.ok:
            unresolved.append(
                (
                    entry.id,
                    "its command still fails: "
                    + (result.reason or f"exit {result.exit_code}"),
                )
            )
            return model
        base = baseline_for(bases, entry)
        if base is not None and base.shape != result.shape:
            same = False
        shape_new = result.shape
    if same and not own_files:
        back = with_fields(
            entry,
            status=entry.previous_status,
            previous_status=None,
            invalidation=None,
            provenance=derived.provenance,
        )
        model = put(model, back)
        records.append(
            record("revalidated", back, now, "derived again, no change")
        )
        revalidated.append(entry.id)
        if shape_new is not None:
            bases[entry.id] = Baseline(shape_new, back.version)
        return model
    new = with_fields(derived, version=entry.version + 1)
    model = put(model, new)
    why = (
        "its own files changed, so the confirmation covered different code"
        if same
        else "the claim differs"
    )
    records.append(
        record("reproposed", new, now, f"derived again after {trigger}; {why}")
    )
    reproposed.append(entry.id)
    if shape_new is not None:
        bases[entry.id] = Baseline(shape_new, new.version)
    model, extra = propagate(model, new, now, "was re-derived")
    records.extend(extra)
    return model


def revalidate(
    model: ProjectModel,
    proposal: Proposal,
    now: datetime,
    *,
    probes: Mapping[str, ProbeResult] | None = None,
    baselines: Mapping[str, Baseline] | None = None,
) -> CheckResult:
    """Derive every stale entry again and settle it.

    Parameters
    ----------
    model : ProjectModel
        Model whose stale entries are revalidated.
    proposal : Proposal
        Fresh detection of the repository as it is now.
    now : datetime
        Time of the lines.
    probes : mapping, optional
        Probe results; needed to settle ``command_failed`` and
        ``output_shape_changed``.
    baselines : mapping, optional
        Probe baselines; updated for a workload whose shape changed.

    Returns
    -------
    CheckResult
        See the module docstring for the three outcomes.
    """
    records: list[ProjectHistoryRecord] = []
    revalidated: list[str] = []
    reproposed: list[str] = []
    unresolved: list[tuple[str, str]] = []
    bases = dict(baselines or {})
    seen: set[str] = set()
    while True:
        batch = [
            e
            for e in all_entries(model)
            if e.status == "stale" and e.id not in seen
        ]
        if not batch:
            break
        for entry in batch:
            seen.add(entry.id)
            model = _settle(
                model,
                entry,
                proposal,
                now,
                probes or {},
                bases,
                records,
                revalidated,
                reproposed,
                unresolved,
            )
    return CheckResult(
        model,
        records,
        revalidated=tuple(revalidated),
        reproposed=tuple(reproposed),
        unresolved=tuple(unresolved),
        baselines=bases,
    )


def mark_findings(
    model: ProjectModel, findings: list[Finding], now: datetime
) -> tuple[ProjectModel, list[ProjectHistoryRecord]]:
    """Mark the entries of ``findings`` stale.

    Parameters
    ----------
    model : ProjectModel
        Current model.
    findings : list of Finding
        From `find_stale`.
    now : datetime
        When it was noticed.

    Returns
    -------
    tuple of ProjectModel and list of ProjectHistoryRecord
        An entry that is no longer ``proposed`` or ``confirmed`` is
        skipped.
    """
    records: list[ProjectHistoryRecord] = []
    for f in findings:
        entry = find(model, f.entry_id)
        if entry is None or entry.status not in ("proposed", "confirmed"):
            continue
        stale = mark_stale(entry, f.trigger, f.detail, now)
        model = put(model, stale)
        records.append(record("marked_stale", stale, now, f.detail))
    return model, records
