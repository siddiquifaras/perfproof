"""The operations the ``init`` and ``project`` commands run.

Each takes the repository root and does one transaction against
``.perfproof/``: `init_project` merges a detection into the model, and
`check_project` marks stale entries and revalidates them. Edits, goals,
confirmations and retirements are `perfproof.project.lifecycle`
functions run under `perfproof.project.store.mutate`.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from perfproof.project.detect import Proposal, detect
from perfproof.project.entries import all_entries
from perfproof.project.errors import ProjectError
from perfproof.project.lifecycle import Merge, propose
from perfproof.project.staleness import (
    PROBE_TIMEOUT_SECONDS,
    Baseline,
    CheckResult,
    ProbeResult,
    baseline_for,
    find_stale,
    load_baselines,
    mark_findings,
    own_files_differ,
    revalidate,
    run_probe,
    save_baselines,
)
from perfproof.project.store import commit, load_model, locked, mutate
from perfproof.schema import (
    EntryKind,
    ProjectHistoryRecord,
    ProjectModel,
    WorkloadEntry,
)

__all__ = ["InitResult", "check_project", "init_project"]


@dataclass(frozen=True)
class InitResult:
    """What `init_project` did.

    Attributes
    ----------
    proposal : Proposal
        What detection found.
    merge : Merge or None
        What was added to the model; ``None`` for a dry run.
    """

    proposal: Proposal
    merge: Merge | None


def init_project(
    root: Path,
    *,
    now: datetime,
    version: str,
    dry_run: bool = False,
    slow_tests: bool = False,
    python: Path | None = None,
    test_timeout: float = 600.0,
) -> InitResult:
    """Detect the project and add what is new to the model, proposed.

    Parameters
    ----------
    root : Path
        Repository root.
    now : datetime
        Time of the proposal, with time zone.
    version : str
        Perfproof version recorded in provenance.
    dry_run : bool, optional
        Detect and return without writing anything, not even
        ``.perfproof/``.
    slow_tests : bool, optional
        Allow one pytest run to find the slowest tests; see
        `perfproof.project.detect.detect`.
    python : Path, optional
        Interpreter for that run.
    test_timeout : float, optional
        Time limit of that run, in seconds.

    Returns
    -------
    InitResult
        The proposal and, unless ``dry_run``, the merge.
    """
    proposal = detect(
        root,
        now=now,
        version=version,
        slow_tests=slow_tests,
        python=python,
        test_timeout=test_timeout,
    )
    if dry_run:
        return InitResult(proposal, None)
    merges: list[Merge] = []

    def change(
        model: ProjectModel,
    ) -> tuple[ProjectModel, list[ProjectHistoryRecord]]:
        merged = propose(model, proposal, now)
        merges.append(merged)
        return merged.model, merged.records

    mutate(root, change, now=lambda: now)
    return InitResult(proposal, merges[0])


def check_project(
    root: Path,
    *,
    now: datetime,
    version: str,
    host_id: Callable[[], str | None],
    probe: bool = False,
    probe_timeout: float = PROBE_TIMEOUT_SECONDS,
    revisions: dict[EntryKind, str] | None = None,
) -> CheckResult:
    """Mark stale entries and revalidate them, in one transaction.

    Detection and probes (which run subprocesses) happen before the
    writer lock is taken; the stale check and the write happen under
    it, on the model as it is then.

    Parameters
    ----------
    root : Path
        Repository root.
    now : datetime
        Time of the lines.
    version : str
        Perfproof version, recorded in re-derived provenance.
    host_id : callable
        Returns this machine's host ID; called only when an entry is
        bound to a machine.
    probe : bool, optional
        Run each confirmed workload's command to check
        ``command_failed`` and ``output_shape_changed``.
    probe_timeout : float, optional
        Time limit of each probe, in seconds.
    revisions : dict, optional
        Derivation revisions, for tests.

    Returns
    -------
    CheckResult
        What became stale and how each stale entry was settled.

    Raises
    ------
    ProjectError
        ``no_model`` when ``init`` has not been run.
    """
    first = load_model(root)
    if first is None:
        raise ProjectError(
            "no_model", "there is no project model: run `perfproof init`"
        )
    proposal = detect(root, now=now, version=version)
    probes: dict[str, ProbeResult] = {}
    if probe:
        for e in all_entries(first):
            if not isinstance(e, WorkloadEntry):
                continue
            # A workload whose own files differ from the ones its
            # confirmation covered is re-proposed, never run, whatever
            # its status or trigger.
            wanted = not own_files_differ(root, e) and (
                e.status == "confirmed"
                or (
                    e.status == "stale"
                    and e.invalidation is not None
                    and e.invalidation.trigger
                    in ("command_failed", "output_shape_changed")
                )
            )
            if wanted:
                probes[e.id] = run_probe(root, e, probe_timeout)
    with locked(root):
        model = load_model(root) or first
        baselines = load_baselines(root)
        needs_host = any(e.host_id is not None for e in all_entries(model))
        findings, unknown = find_stale(
            root,
            model,
            host_id=host_id() if needs_host else None,
            probes=probes,
            baselines=baselines,
            revisions=revisions,
        )
        model, marked = mark_findings(model, findings, now)
        done = revalidate(
            model, proposal, now, probes=probes, baselines=baselines
        )
        bases: dict[str, Baseline] = dict(done.baselines)
        for wid, result in probes.items():
            current = next(
                (w for w in done.model.workloads if w.id == wid), None
            )
            if (
                result.ok
                and result.shape is not None
                and current is not None
                and baseline_for(bases, current) is None
                and current.status != "stale"
            ):
                bases[wid] = Baseline(result.shape, current.version)
        records = [*marked, *done.records]
        final = done.model
        if records:
            final = final.model_copy(update={"updated_at": now})
            commit(root, final, records)
        if bases != baselines:
            save_baselines(root, bases)
    shown: Mapping[str, Baseline] = bases
    return CheckResult(
        final,
        records,
        marked_stale=tuple(findings),
        revalidated=done.revalidated,
        reproposed=done.reproposed,
        unresolved=done.unresolved,
        unknown=tuple(text for _, text in unknown),
        baselines=shown,
    )
