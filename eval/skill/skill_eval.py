"""Command line of the skill eval: run, score, archive, break-skill.

Examples
--------
Needs ``uv sync`` (the runs use this checkout's CLI and Python) and a
signed-in ``claude``. One task runs at a time. A whole run costs a few
dollars of Sonnet; ``--only`` takes task id patterns::

    uv run python eval/skill/skill_eval.py run --label mine
    uv run python eval/skill/skill_eval.py run --label mine --resume
    uv run python eval/skill/skill_eval.py run --only 'prove-*' 'pos-*'

Real repositories (``tasks-real.json``): each task clones a public repo
at a mined manifest's commit into a cache under ``--cache`` (one
download per repository), builds it with the manifest's steps and runs
the skill on it. A task whose toolchain is missing is recorded as
skipped, with the reason, not failed::

    uv run python eval/skill/skill_eval.py --tasks-file \
        eval/skill/tasks-real.json run --label real-1

Score a saved run again, with no model call, optionally against an
earlier ``scores.json``::

    cd eval/skill
    uv run python skill_eval.py score ../../scratch/skill-eval/mine \
        --previous runs/first/scores.json

Measure a skill change: ``break-skill`` copies the skill with its "no
direction words" rule inverted, to show the scorer sees the difference::

    uv run python skill_eval.py break-skill ../../scratch/broken
    uv run python skill_eval.py run --skill ../../scratch/broken

``archive`` keeps a run's transcripts and scores for the repo.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import realrepo
import reporting
import runner
import sandbox
import scoring
import taskset

DEFAULT_OUT = runner.REPO_ROOT / "scratch" / "skill-eval"
"""Gitignored directory runs are written to by default."""
# The rule the broken copy replaces, from skills/perfproof/SKILL.md
# ("Estimates"). The replacement says the opposite, so the copy is
# worse on wording by construction, not by chance.
_RULE = re.compile(
    r"Never call an\s+estimate faster.*?until perfproof can\s+give a"
    r" verdict\.",
    re.DOTALL,
)
_BROKEN = (
    "Say plainly whether the estimate shows the change is faster or"
    " slower, by how much, and\n    whether it matches the claim."
)


def _select(
    tasks: list[taskset.Task], patterns: list[str] | None
) -> list[taskset.Task]:
    if not patterns:
        return tasks
    return [
        t for t in tasks if any(fnmatch.fnmatch(t.id, p) for p in patterns)
    ]


def break_skill(dest: Path) -> None:
    """Copy the skill with its "no direction words" rule inverted.

    Parameters
    ----------
    dest : Path
        Directory to create.

    Raises
    ------
    ValueError
        If ``SKILL.md`` no longer contains the rule text this edits;
        update ``_RULE`` when the skill's wording changes.
    """
    shutil.copytree(runner.SKILL_SOURCE, dest)
    skill = dest / "SKILL.md"
    text = skill.read_text(encoding="utf-8")
    if _RULE.search(text) is None:
        msg = "the direction-word rule is not in SKILL.md as expected"
        raise ValueError(msg)
    skill.write_text(_RULE.sub(_BROKEN, text), encoding="utf-8")


def wait_while_marked(
    marker: Path | None,
    poll_seconds: float = 120.0,
    sleep: Callable[[float], None] = time.sleep,
) -> float:
    """Wait until a pause marker file is gone.

    Parameters
    ----------
    marker : Path or None
        File whose presence means "the machine must stay quiet"; no
        waiting when ``None`` or absent.
    poll_seconds : float, optional
        Seconds between looks.
    sleep : callable, optional
        The wait, replaceable in tests.

    Returns
    -------
    float
        Seconds waited.
    """
    waited = 0.0
    while marker is not None and marker.exists():
        sys.stdout.write(f"waiting: {marker} exists\n")
        sys.stdout.flush()
        sleep(poll_seconds)
        waited += poll_seconds
    return waited


def _scrub(text: str, token: str | None) -> str:
    return text.replace(token, "<redacted>") if token else text


def auth_probe(out: Path, work_root: Path, allow_docker: bool) -> Path:
    """Run one tiny ``claude -p`` call under the sandbox and record it.

    Parameters
    ----------
    out : Path
        Directory to write ``probe-<n>.json`` into.
    work_root : Path
        Where the throwaway working directory lives.
    allow_docker : bool
        Whether the profile lets the call run ``docker``.

    Returns
    -------
    Path
        The record. It holds the exit code, the first characters of the
        reply and of stderr, and the launcher; never an environment or
        a credential value.
    """
    work = work_root / "auth-probe"
    work.mkdir(parents=True, exist_ok=True)
    env = sandbox.agent_env(os.environ, work / "sandbox" / "p", [])
    env.update(runner.ISOLATION_ENV)
    env["CLAUDE_CONFIG_DIR"] = str(work / "config")
    env["CLAUDE_CODE_TMPDIR"] = str(work / "config" / "tmp")
    (work / "config" / "tmp").mkdir(parents=True, exist_ok=True)
    (work / "config").mkdir(exist_ok=True)
    token = sandbox.claude_oauth_token()
    if token is not None:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    launch = sandbox.wrap(
        [],
        work / "sandbox" / "p",
        checkout=runner.REPO_ROOT,
        allow_docker=allow_docker,
        cwd=work,
        read=[work / "sandbox" / "p", runner.REPO_ROOT / ".venv"],
        write=[work / "config", work / "sandbox" / "p" / "private"],
    )
    done = subprocess.run(
        [
            *launch,
            "claude",
            "-p",
            "reply with the single word ok",
            "--model",
            "claude-sonnet-5-5",
            "--max-budget-usd",
            "0.30",
            "--setting-sources",
            "project,local",
            "--strict-mcp-config",
        ],
        cwd=work,
        env=env,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    out.mkdir(parents=True, exist_ok=True)
    n = len(list(out.glob("probe-*.json"))) + 1
    path = out / f"probe-{n}.json"
    record = {
        "launcher": launch[:1],
        "sandbox_exec": bool(launch),
        "returncode": done.returncode,
        "stdout_head": _scrub(done.stdout[:120], token),
        "stderr_head": _scrub(done.stderr[:120], token),
        "token_source": "keychain, read by the harness"
        if token
        else "environment",
        "authenticated": done.returncode == 0
        and "Not logged in" not in done.stdout + done.stderr,
        "env_names_passed": sorted(env),
    }
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def _cmd_auth_probe(args: argparse.Namespace) -> int:
    sys.stdout.write(f"{auth_probe(args.out, args.work_root, False)}\n")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    tasks = taskset.load(args.tasks_file)
    chosen = _select(tasks.tasks, args.only)
    run_id = args.label or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(args.out) / run_id
    run_dir.mkdir(parents=True, exist_ok=args.resume)
    path_dir = runner.shim_dir(run_dir)
    skill_dir = Path(args.skill)
    sys.stdout.write(f"run {run_dir}\n")
    stopped: str | None = None
    for task in chosen:
        if args.resume and (run_dir / task.id / "meta.json").exists():
            continue
        for stale in (
            run_dir / task.id,
            run_dir / "repos" / task.id,
            args.work_root / run_id / "repos" / task.id,
        ):
            shutil.rmtree(stale, ignore_errors=True)
        wait_while_marked(args.pause_marker)
        try:
            meta = runner.run_task(
                task,
                tasks,
                run_dir,
                skill_dir,
                path_dir,
                args.cache,
                args.work_root,
            )
        except sandbox.SandboxUnavailableError as exc:
            # The sign-in ran out between tasks: keep what finished.
            stopped = str(exc)
            sys.stdout.write(f"{task.id}: STOPPED, {stopped}\n")
            break
        except realrepo.SetupError as exc:
            (run_dir / task.id).mkdir(parents=True, exist_ok=True)
            (run_dir / task.id / "skipped.json").write_text(
                json.dumps({"task_id": task.id, "reason": str(exc)}, indent=2),
                encoding="utf-8",
            )
            sys.stdout.write(f"{task.id}: SKIPPED, {str(exc)[:200]}\n")
            continue
        sys.stdout.write(
            f"{task.id}: {meta.wall_seconds:.0f}s, "
            f"load {meta.load_start[0]:.1f}\n"
        )
        sys.stdout.flush()
    if sandbox.TAMPERED:
        (run_dir / "tampered.json").write_text(
            json.dumps(sandbox.TAMPERED, indent=2), encoding="utf-8"
        )
    status = _score_and_report(run_dir, args.tasks_file, args.previous)
    if args.archive_to is not None:
        archive(run_dir, args.archive_to)
    if stopped is not None:
        sys.stderr.write(f"run stopped early: {stopped}\n")
        return 2
    return status


def _score_and_report(
    run_dir: Path, tasks_file: Path, previous: Path | None
) -> int:
    tasks = taskset.load(tasks_file)
    scores = scoring.score_run(run_dir, tasks)
    reporting.write(run_dir, scores, previous)
    sys.stdout.write((run_dir / "report.md").read_text(encoding="utf-8"))
    return 0


def _cmd_score(args: argparse.Namespace) -> int:
    return _score_and_report(
        Path(args.run_dir), args.tasks_file, args.previous
    )


def _cmd_break(args: argparse.Namespace) -> int:
    break_skill(Path(args.dest))
    sys.stdout.write(f"{args.dest}\n")
    return 0


class ArchiveRefusedError(RuntimeError):
    """A run holds a credential and was not archived."""


_ARCHIVE_IGNORE = ("repos", "work", "bin", "shims", "*.err")


def _archive_secrets() -> list[str]:
    """Collect every sign-in value the archive scan must look for.

    The values this process handed to agents are kept in memory
    (``runner.SECRETS_USED``) and never written out. The current
    environment's and keychain's sign-in are added. If neither source
    gives anything, the scan has nothing to look for and must not pass.
    """
    known = list(runner.SECRETS_USED)
    current = sandbox.env_secrets(os.environ)
    if not current:
        try:
            token = sandbox.claude_oauth_token()
        except sandbox.SandboxUnavailableError as exc:
            if not known:
                msg = (
                    "cannot read the sign-in this run used (" + str(exc) + ");"
                    " not archiving. Archive in the process that ran it"
                    " (`run --archive-to`), or sign in again first"
                )
                raise ArchiveRefusedError(msg) from exc
            token = None
        current = [token] if token else []
    return [*known, *[c for c in current if c not in known]]


def scan_for_secrets(run_dir: Path, secrets: list[str]) -> list[str]:
    """Find the files of a run that hold a secret in any known form.

    Parameters
    ----------
    run_dir : Path
        The run directory.
    secrets : list of str
        Secret values; their reversed, hex and base64 forms are looked
        for too.

    Returns
    -------
    list of str
        Paths relative to ``run_dir``, never the values.
    """
    forms = sandbox.secret_forms(secrets)
    hits: list[str] = []
    if not forms:
        msg = "no secret to scan for: refusing to call a run clean"
        raise ArchiveRefusedError(msg)
    for root, dirs, files in os.walk(run_dir):
        dirs[:] = [
            d
            for d in dirs
            if not any(fnmatch.fnmatch(d, p) for p in _ARCHIVE_IGNORE)
        ]
        for name in files:
            if any(fnmatch.fnmatch(name, p) for p in _ARCHIVE_IGNORE):
                continue
            path = Path(root) / name
            text = path.read_text(encoding="utf-8", errors="replace")
            if sandbox.holds_secret(text, forms):
                hits.append(str(path.relative_to(run_dir)))
    return sorted(hits)


def archive(
    run_dir: Path, dest: Path, secrets: list[str] | None = None
) -> None:
    """Copy what scoring reads out of a run, to keep it in the repo.

    Parameters
    ----------
    run_dir : Path
        A run directory.
    dest : Path
        Directory to create. It gets every task's transcripts,
        ``meta.json`` and ``state.json`` plus ``scores.json`` and
        ``report.md``; the built repos, the launchers, the scratch
        trees and the ``claude`` stderr files are left out.
    secrets : list of str, optional
        Values to look for; the sign-in of the current environment and
        keychain by default.

    Raises
    ------
    ArchiveRefusedError
        If any file to be copied holds a secret (or its reversed, hex
        or base64 form). Nothing is copied then.
    """
    hits = scan_for_secrets(
        run_dir, _archive_secrets() if secrets is None else secrets
    )
    if hits:
        msg = f"refusing to archive: a credential appears in {hits}"
        raise ArchiveRefusedError(msg)
    shutil.copytree(
        run_dir, dest, ignore=shutil.ignore_patterns(*_ARCHIVE_IGNORE)
    )


def _cmd_archive(args: argparse.Namespace) -> int:
    archive(args.run_dir, args.dest)
    sys.stdout.write(f"{args.dest}\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    Parameters
    ----------
    argv : list of str, optional
        Arguments; ``sys.argv[1:]`` when omitted.

    Returns
    -------
    int
        Exit status.
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--tasks-file", type=Path, default=taskset.TASKS_FILE)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run tasks, then score the run")
    run.add_argument("--only", nargs="+", help="task id patterns")
    run.add_argument("--skill", type=Path, default=runner.SKILL_SOURCE)
    run.add_argument("--label", help="run id; a timestamp by default")
    run.add_argument(
        "--resume",
        action="store_true",
        help="keep finished tasks of an existing run, redo the rest",
    )
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    run.add_argument(
        "--archive-to",
        type=Path,
        help="archive the run here when it ends, scanning with the "
        "sign-in values this process used (kept in memory)",
    )
    run.add_argument(
        "--work-root",
        type=Path,
        default=Path.home() / ".cache" / "perfproof-eval",
        help="where task repos live; outside the perfproof checkout",
    )
    run.add_argument(
        "--pause-marker",
        type=Path,
        help="do not start a task while this file exists",
    )
    run.add_argument(
        "--cache",
        type=Path,
        default=DEFAULT_OUT / "cache",
        help="where real repositories are cached between runs",
    )
    run.add_argument("--previous", type=Path, help="scores.json to compare")
    run.set_defaults(func=_cmd_run)
    probe = sub.add_parser(
        "auth-probe", help="one tiny sandboxed claude call, to show sign-in"
    )
    probe.add_argument("out", type=Path)
    probe.add_argument(
        "--work-root",
        type=Path,
        default=Path.home() / ".cache" / "perfproof-eval",
    )
    probe.set_defaults(func=_cmd_auth_probe)
    score = sub.add_parser("score", help="score a saved run, no re-run")
    score.add_argument("run_dir", type=Path)
    score.add_argument("--previous", type=Path)
    score.set_defaults(func=_cmd_score)
    keep = sub.add_parser("archive", help="copy a run's scoring inputs")
    keep.add_argument("run_dir", type=Path)
    keep.add_argument("dest", type=Path)
    keep.set_defaults(func=_cmd_archive)
    brk = sub.add_parser("break-skill", help="copy the skill, rule inverted")
    brk.add_argument("dest", type=Path)
    brk.set_defaults(func=_cmd_break)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
