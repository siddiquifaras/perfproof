"""``perfproof init``: propose a project model from the repository.

``init`` looks at the repository (manifests, existing benchmark suites,
JUnit files that name the slowest tests, entry points, routes, serving
configs) and records what it found in ``.perfproof/project.json`` as
``proposed`` entries, each with its evidence and the hashes of the
files it came from. It also lists what it could not establish and asks:
every goal starts with an unknown metric, direction, unit, bar and
guardrails, and a class with no runnable workload evidence gets a
question, not an invented command. Nothing it finds is confirmed and
nothing is measured.

Running it again adds what is new and leaves every existing entry
alone, whatever its status; use ``perfproof project check`` to
re-derive a stale entry.

The only thing written is ``.perfproof/`` (and its line in the
repository's ``.git/info/exclude``). ``--dry-run`` writes nothing at
all. ``--slow-tests`` runs the project's own pytest once to find the
slowest tests when no JUnit file names them; that executes the test
suite, so it is opt-in.

``--json`` result
-----------------
``{"dry_run", "root", "model_path", "added", "skipped", "targets",
"workloads", "goals", "how_to_run", "questions", "notes"}``.
``model_path`` is ``null`` for a dry run; ``added`` lists entry IDs
written; ``skipped`` lists ``{"id", "reason"}``. Every entry listing
carries ``id`` and ``evidence``, the sentences that explain it.
``questions`` is a list of ``{"id", "entry", "text"}`` the agent puts
to the developer.

Error codes
-----------
``not_a_repository``, ``invalid_model`` (an existing model file that
cannot be read is never overwritten), ``state_write_failed``,
``lock_timeout``.
"""

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from perfproof import __version__
from perfproof.bench.snapshot import SnapshotError, exclude_state_dir
from perfproof.commands import CommandError, JsonValue, Output
from perfproof.commands._project import as_command_error, repo_root
from perfproof.project.detect import Proposal
from perfproof.project.errors import ProjectError
from perfproof.project.service import InitResult, init_project
from perfproof.project.store import model_path

__all__ = ["add_arguments", "run"]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``init``'s options.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Create .perfproof/ and propose a project model from the repository."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the proposal and write nothing",
    )
    parser.add_argument(
        "--slow-tests",
        action="store_true",
        help="run the project's pytest once to find the slowest tests",
    )
    parser.add_argument(
        "--python",
        type=Path,
        metavar="PATH",
        help="interpreter for --slow-tests (default: .venv/bin/python)",
    )
    parser.add_argument(
        "--test-timeout",
        type=float,
        default=600.0,
        metavar="SECONDS",
        help="time limit of the --slow-tests run (default: 600)",
    )


def run(args: argparse.Namespace) -> Output:
    """Detect the project and record the proposal.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed options.

    Returns
    -------
    Output
        The result described in this module's docstring.

    Raises
    ------
    CommandError
        With one of the codes in this module's docstring.
    """
    root = repo_root(cast("Path", args.repo))
    dry = cast("bool", args.dry_run)
    try:
        if not dry:
            exclude_state_dir(root)
        result = init_project(
            root,
            now=datetime.now(UTC),
            version=__version__,
            dry_run=dry,
            slow_tests=cast("bool", args.slow_tests),
            python=cast("Path | None", args.python),
            test_timeout=cast("float", args.test_timeout),
        )
    except SnapshotError as exc:
        raise CommandError("state_write_failed", str(exc)) from exc
    except ProjectError as exc:
        raise as_command_error(exc) from exc
    payload = _payload(root, dry, result)
    return Output(result=payload, text=_text(dry, result))


def _payload(
    root: Path, dry: bool, result: InitResult
) -> dict[str, JsonValue]:
    p = result.proposal
    ev = p.evidence
    merge = result.merge
    added = list(merge.added) if merge else []
    skipped: list[JsonValue] = (
        [{"id": i, "reason": r} for i, r in merge.skipped] if merge else []
    )

    def lines(entry_id: str) -> list[JsonValue]:
        return list(ev.get(entry_id, ()))

    return cast(
        "dict[str, JsonValue]",
        {
            "dry_run": dry,
            "root": str(root),
            "model_path": None if dry else str(model_path(root)),
            "added": list(added),
            "skipped": skipped,
            "targets": [
                {
                    "id": t.id,
                    "target_class": t.target_class,
                    "evidence": lines(t.id),
                }
                for t in p.targets
            ],
            "workloads": [
                {
                    "id": w.id,
                    "source": w.source,
                    "command": list(w.command),
                    "cwd": w.cwd,
                    "files": [f.path for f in w.provenance.files],
                    "evidence": lines(w.id),
                }
                for w in p.workloads
            ],
            "goals": [
                {
                    "id": g.id,
                    "workload_id": g.workload_id,
                    "unknown": sorted(g.missing),
                }
                for g in p.goals
            ],
            "how_to_run": [
                {
                    "id": h.id,
                    "setup": _argvs(h.setup),
                    "build": _argvs(h.build),
                    "unknown": dict(h.missing),
                }
                for h in p.how_to_run
            ],
            "questions": [
                {"id": q.id, "entry": q.entry, "text": q.text}
                for q in p.questions
            ],
            "notes": list(p.notes),
        },
    )


def _argvs(value: tuple[tuple[str, ...], ...] | None) -> JsonValue:
    if value is None:
        return None
    return [list(a) for a in value]


def _text(dry: bool, result: InitResult) -> str:
    p: Proposal = result.proposal
    head = (
        "Proposed project model (dry run, nothing written; nothing is "
        "confirmed):"
        if dry
        else "Proposed project model (nothing is confirmed yet):"
    )
    out = [head]
    for t in p.targets:
        out.append(f"  target   {t.target_class}")
        out.extend(f"           evidence: {e}" for e in t.evidence)
    for w in p.workloads:
        out.append(f"  workload {w.id}   {' '.join(w.command)}   ({w.source})")
        out.extend(f"           evidence: {e}" for e in p.evidence[w.id])
    out.extend(
        f"  goal     {g.workload_id}: unknown, to be asked" for g in p.goals
    )
    out.extend(
        f"  run      setup {_cmds(h.setup)}; build {_cmds(h.build)}"
        for h in p.how_to_run
    )
    if result.merge and result.merge.skipped:
        out.append("Already in the model, left as they are:")
        out.extend(f"  {i}: {r}" for i, r in result.merge.skipped)
    if p.questions:
        out.append("Questions for the developer:")
        out.extend(f"  - {q.text}" for q in p.questions)
    out.extend(f"Note: {n}" for n in p.notes)
    return "\n".join(out)


def _cmds(value: tuple[tuple[str, ...], ...] | None) -> str:
    if value is None:
        return "unknown"
    return ", ".join(" ".join(a) for a in value) or "none"
