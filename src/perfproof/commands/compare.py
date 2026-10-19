"""``perfproof compare``: prove a candidate against a baseline.

``compare NAME --candidate PATH_OR_REV`` runs
`perfproof.bench.compare.compare` in the foreground on the benchmark
``NAME`` as defined by ``perfproof bench define``: gates, interleaved
rounds, verdict, then ``record.json``, ``report.md`` and
``samples.npz`` in ``.perfproof/runs/<run-id>/``.

- The spec is the one stored version of ``NAME``, or the one
  ``--spec-hash`` names by its full hash or a unique prefix.
- The baseline is ``--baseline`` (default ``HEAD``), a revision of the
  repository, checked out as a detached worktree under
  ``.perfproof/worktrees/baseline-<sha12>/``.
- The candidate is a directory holding the candidate tree when
  ``--candidate`` names one, else a revision, checked out like the
  baseline under ``.perfproof/worktrees/candidate-<sha12>/``.

Until the interval procedure is calibrated, a comparison that passes
its gates is INCONCLUSIVE with ``procedure_uncalibrated``; only a gate
gives REJECT. ``--detach`` and ``--resume`` are not built yet and fail
with ``not_built``.

``--json`` result
-----------------
``{"run_id", "run_dir", "record_path", "report_path", "verdict",
"gates"}``: ``verdict`` is the JSON dump of the record's
`perfproof.schema.Verdict`; ``gates`` lists ``{"gate", "status"}`` in
the record's order; paths are absolute.

Error codes
-----------
``not_a_repository``, ``not_found`` (no such benchmark, spec or
candidate), ``ambiguous_hash``, ``invalid_record``, ``invalid_revision``
(a revision that names no commit, or a worktree that can't be made),
``not_built`` (``--detach``, ``--resume``), and the codes of
`perfproof.bench.compare.CompareStoppedError`, whose
``details.run_dir`` keeps the logs: ``broken_baseline``,
``harness_fault``, ``unsupported_spec``, ``undeclared_guardrails`` and
``internal_fault``.
"""

import argparse
import os
import re
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from perfproof.bench.compare import CompareStoppedError, compare
from perfproof.bench.snapshot import (
    GIT_TIMEOUT_SECONDS,
    SnapshotError,
    add_baseline_worktree,
    exclude_state_dir,
    resolve_commit,
)
from perfproof.bench.spec import (
    STATE_DIR,
    SpecRecordError,
    load_spec,
    spec_record_path,
)
from perfproof.commands import CommandError, JsonValue, Output
from perfproof.schema import BenchmarkSpec, LockManifest, RunRecord

__all__ = ["add_arguments", "run"]

_HASH_FILE: Final = re.compile(r"^[0-9a-f]{64}\.json$")
_HEX: Final = re.compile(r"^[0-9a-f]+$")
_ZERO_HASH: Final = "0" * 64
_SHORT_SHA: Final = 12


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``compare``'s options.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Prove a candidate against a baseline: gates, interleaved rounds, "
        "verdict, record."
    )
    parser.add_argument("name", help="benchmark name")
    parser.add_argument(
        "--candidate",
        required=True,
        metavar="PATH_OR_REV",
        help="candidate tree directory, or a git revision",
    )
    parser.add_argument(
        "--baseline",
        default="HEAD",
        metavar="REV",
        help="baseline git revision (default: HEAD)",
    )
    parser.add_argument(
        "--spec-hash",
        metavar="HASH",
        help="spec version, by hash or unique prefix (default: the only one)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )
    parser.add_argument("--detach", action="store_true", help="not built yet")
    parser.add_argument("--resume", metavar="RUN", help="not built yet")


def run(args: argparse.Namespace) -> Output:
    """Run one comparison in the foreground.

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
    if args.detach or args.resume is not None:
        option = "--detach" if args.detach else "--resume"
        msg = (
            f"compare {option} is not built yet; run compare in the foreground"
        )
        raise CommandError("not_built", msg, {"option": option})
    root = _repo_root(cast("Path", args.repo))
    name = cast("str", args.name)
    spec, lock = _spec(root, name, cast("str | None", args.spec_hash))
    try:
        exclude_state_dir(root)
        baseline_commit = resolve_commit(root, cast("str", args.baseline))
        baseline_root = add_baseline_worktree(
            root, baseline_commit, f"baseline-{baseline_commit[:_SHORT_SHA]}"
        )
        candidate_root = _candidate(root, cast("str", args.candidate))
    except SnapshotError as exc:
        raise CommandError("invalid_revision", str(exc)) from exc
    run_id = _run_id()
    run_dir = root / STATE_DIR / "runs" / run_id
    try:
        result = compare(
            spec,
            lock,
            baseline_root=baseline_root,
            baseline_commit=baseline_commit,
            candidate_root=candidate_root,
            run_dir=run_dir,
            process_env=os.environ,
            order_seed=secrets.randbits(63),
        )
    except CompareStoppedError as exc:
        raise CommandError(
            exc.code, exc.message, {"run_dir": str(exc.run_dir)}
        ) from exc
    record = result.record
    gates: list[JsonValue] = [
        {"gate": g.gate, "status": g.status} for g in record.gates
    ]
    payload: dict[str, JsonValue] = {
        "run_id": record.run_id,
        "run_dir": str(result.run_dir),
        "record_path": str(result.record_path),
        "report_path": str(result.report_path),
        "verdict": record.verdict.model_dump(mode="json"),
        "gates": gates,
    }
    return Output(result=payload, text=_text(record, result.report_path))


def _repo_root(repo: Path) -> Path:
    """Return the root of the git working tree containing ``repo``."""
    argv = ["git", "-C", str(repo), "rev-parse", "--show-toplevel"]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"git rev-parse failed: {exc}"
        raise CommandError("not_a_repository", msg) from exc
    if done.returncode != 0:
        msg = f"{repo} is not in a git working tree: {done.stderr.strip()}"
        raise CommandError("not_a_repository", msg, {"repo": str(repo)})
    return Path(done.stdout.strip())


def _spec(
    root: Path, name: str, prefix: str | None
) -> tuple[BenchmarkSpec, LockManifest]:
    """Load the one spec version of ``name`` that ``prefix`` selects."""
    try:
        directory = spec_record_path(root, name, _ZERO_HASH).parent
    except ValueError as exc:
        raise CommandError("not_found", str(exc), {"name": name}) from exc
    if prefix is not None and not _HEX.fullmatch(prefix):
        msg = f"{prefix!r} is not a spec hash or a prefix of one"
        raise CommandError("not_found", msg, {"name": name})
    try:
        found = (
            sorted(
                p.stem for p in directory.iterdir() if _HASH_FILE.match(p.name)
            )
            if directory.is_dir()
            else []
        )
    except OSError as exc:
        raise CommandError("invalid_record", str(exc)) from exc
    matches = [h for h in found if prefix is None or h.startswith(prefix)]
    if not matches:
        msg = f"no spec of benchmark {name!r} matches in {root}" + (
            "" if prefix is None else f" hash {prefix}"
        )
        raise CommandError("not_found", msg, {"name": name})
    if len(matches) > 1:
        listed: list[JsonValue] = list(matches)
        msg = (
            f"benchmark {name!r} has {len(matches)} matching specs; pick "
            "one with --spec-hash"
        )
        raise CommandError(
            "ambiguous_hash", msg, {"name": name, "matches": listed}
        )
    try:
        return load_spec(root, name, matches[0])
    except (SpecRecordError, OSError) as exc:
        raise CommandError("invalid_record", str(exc)) from exc


def _candidate(root: Path, value: str) -> Path:
    """Return the candidate tree, a directory or a revision."""
    path = Path(value)
    if path.is_dir():
        return path.resolve()
    if path.exists():
        msg = f"candidate {value} is not a directory"
        raise CommandError("not_found", msg, {"candidate": value})
    commit = resolve_commit(root, value)
    return add_baseline_worktree(
        root, commit, f"candidate-{commit[:_SHORT_SHA]}"
    )


def _run_id() -> str:
    """Return a new run id, UTC time then random hex."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def _text(record: RunRecord, report: Path) -> str:
    """Summarize the verdict for a person."""
    v = record.verdict
    head = [v.outcome, v.reason]
    if v.gate is not None:
        head.append(f"gate {v.gate}")
    if v.estimate is not None:
        # Before calibration the number backs no claim, so it is shown
        # as an estimate and never called a speedup.
        label = (
            "uncalibrated estimate"
            if (v.diagnostics.procedure_uncalibrated)
            else "estimate"
        )
        head.append(f"{label} {v.estimate:.4g}x")
    if v.interval is not None:
        head.append(
            f"interval {v.interval.lower:.4g}x to {v.interval.upper:.4g}x"
        )
    head.append(f"{v.rounds} rounds")
    lines = ["  ".join(head)]
    if v.diagnostics.procedure_uncalibrated:
        lines.append(
            "The procedure is not calibrated yet: no KEEP or performance "
            "REJECT is given."
        )
    gates = ", ".join(f"{g.gate} {g.status}" for g in record.gates)
    lines.append(f"Gates: {gates}")
    lines.append(f"Report: {report}")
    return "\n".join(lines)
