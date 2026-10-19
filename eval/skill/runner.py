"""Build each task's repo, run the installed skill, save what happened.

One task at a time: timing noise matters to the thing being measured, so
tasks never overlap. Each task gets a fresh repo built from an example
under the run directory, the skill copied in from a directory of the
caller's choice, and ``claude -p`` driven through ``--resume`` for the
scripted follow-ups. Everything the scorer needs is written to the
task's directory: the transcripts, a ``meta.json`` and a ``state.json``
taken from the repo afterwards.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel, ConfigDict

from taskset import Task, TaskSet
from transcript import as_dict, as_list, load_turns, seen_verdicts

REPO_ROOT = Path(__file__).resolve().parents[2]
"""The perfproof checkout this harness belongs to."""
EXAMPLES = REPO_ROOT / "examples"
SKILL_SOURCE = REPO_ROOT / "skills" / "perfproof"
"""The skill as it is in the working tree."""
ALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep,Skill"
"""Tools allowed without a prompt; nobody is there to answer one."""
TURN_TIMEOUT_SECONDS = 1500.0
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}
# The owner's own CLAUDE.md and memory change how the skill answers (a
# private report format leaked into one transcript): runs ignore them.
_ISOLATION_ENV = {
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
}
_NOISE_SUBTYPES = frozenset(
    {
        "thinking_tokens",
        "task_summary",
        "post_turn_summary",
        "task_started",
        "task_notification",
    }
)
_COMMAND_TIMEOUT = 120.0


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TreeState(_Model):
    """One tree of the repo after the run, with its example tests run.

    Attributes
    ----------
    ref : str
        ``"working"`` for the checked-out tree with uncommitted
        changes, otherwise a branch name.
    changed : list of str
        Paths that differ from the baseline commit.
    tests_ok : bool or None
        Whether the example's own tests passed there (the deliberately
        flaky one deselected); ``None`` if not run.
    tests_tail : str
        Last lines of the test output.
    """

    ref: str
    changed: list[str]
    tests_ok: bool | None
    tests_tail: str


class RunVerdict(_Model):
    """The verdict of one perfproof run left in ``.perfproof/runs``.

    Attributes
    ----------
    run_id : str
        The run's id.
    outcome : str
        ``verdict.outcome``.
    reason : str
        ``verdict.reason``.
    gate : str or None
        ``verdict.gate``.
    calibration : str
        ``verdict.calibration``.
    candidate_commit : str or None
        ``candidate_commit`` of the record. The CLI leaves it null when
        the candidate is a branch, so nothing here relies on it.
    """

    run_id: str
    outcome: str
    reason: str
    gate: str | None
    calibration: str
    candidate_commit: str | None = None


class State(_Model):
    """The repo after the run.

    Attributes
    ----------
    status : str
        ``git status --porcelain`` output, ``.perfproof/`` included.
    current_branch : str
        Checked-out branch, or ``"HEAD"`` when detached.
    branches : dict of str to str
        Branch name to tip sha.
    seeded : dict of str to str
        Branch name to tip sha when the run started.
    trees : list of TreeState
        The working tree and every branch that was not seeded.
    run_verdicts : list of RunVerdict
        Verdicts from the run records, oldest first.
    """

    status: str
    current_branch: str
    branches: dict[str, str]
    seeded: dict[str, str]
    trees: list[TreeState]
    run_verdicts: list[RunVerdict]


class TurnMeta(_Model):
    """One ``claude`` call.

    Attributes
    ----------
    prompt : str
        The message sent.
    argv : list of str
        The command, prompt included.
    seconds : float
        Wall time of the call.
    exit_code : int or None
        Process exit code; ``None`` if it was killed on timeout.
    session_id : str or None
        Session id read from the stream.
    """

    prompt: str
    argv: list[str]
    seconds: float
    exit_code: int | None
    session_id: str | None


class Meta(_Model):
    """How a task was run.

    Attributes
    ----------
    task_id : str
        The task's id.
    started_at : str
        UTC timestamp, ISO 8601.
    wall_seconds : float
        Whole task, build and capture included.
    load_start, load_end : list of float
        One, five and fifteen minute load averages.
    claude_version : str
        ``claude --version`` output.
    skill_sha : str
        Hash of the installed skill's files.
    turns : list of TurnMeta
        The calls, in order.
    """

    task_id: str
    started_at: str
    wall_seconds: float
    load_start: list[float]
    load_end: list[float]
    claude_version: str
    skill_sha: str
    turns: list[TurnMeta]


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **_GIT_ENV}
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=_COMMAND_TIMEOUT,
    )
    if check and done.returncode != 0:
        msg = f"git {' '.join(args)} failed: {done.stdout}{done.stderr}"
        raise RuntimeError(msg)
    return done


def _materialize_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "materialize", EXAMPLES / "materialize.py"
    )
    if spec is None or spec.loader is None:
        msg = "cannot load examples/materialize.py"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def skill_hash(skill_dir: Path) -> str:
    """Hash the files of a skill directory.

    Parameters
    ----------
    skill_dir : Path
        A directory holding ``SKILL.md`` and its sibling files.

    Returns
    -------
    str
        First 12 hex digits of a SHA-256 over every file's relative
        path and bytes, in sorted path order.
    """
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(p for p in skill_dir.rglob("*") if p.is_file()):
        digest.update(path.relative_to(skill_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def shim_dir(root: Path) -> Path:
    """Create ``python`` and ``perfproof`` launchers for the run's PATH.

    A user's agent finds both on its PATH; here they point at this
    checkout's virtualenv (``uv sync`` has to have run), so the run
    uses the working-tree CLI and never touches another checkout.

    Parameters
    ----------
    root : Path
        Directory to create the ``bin`` directory in.

    Returns
    -------
    Path
        The ``bin`` directory.

    Raises
    ------
    FileNotFoundError
        If the checkout has no ``.venv``.
    """
    venv = REPO_ROOT / ".venv" / "bin"
    if not (venv / "python").exists():
        msg = f"{venv} is missing; run `uv sync` first"
        raise FileNotFoundError(msg)
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name, target in (
        ("python", venv / "python"),
        ("perfproof", venv / "perfproof"),
    ):
        launcher = bindir / name
        launcher.write_text(f'#!/bin/sh\nexec "{target}" "$@"\n')
        launcher.chmod(0o755)
    return bindir


def build_repo(task: Task, dest: Path, skill_dir: Path) -> dict[str, str]:
    """Build the task's repo: baseline on ``main``, branches, the skill.

    Parameters
    ----------
    task : Task
        The task; its example and branches decide the content.
    dest : Path
        Directory to create; it must not exist.
    skill_dir : Path
        The skill to copy to ``.claude/skills/perfproof/``.

    Returns
    -------
    dict of str to str
        Branch name to tip sha as seeded, ``main`` included.

    Raises
    ------
    FileExistsError
        If ``dest`` exists.
    """
    mat = _materialize_module()
    mat.materialize(task.example, None, dest)
    for spec in dest.glob("spec*.json"):
        spec.unlink()
    _git(dest, "init", "-q", "-b", "main")
    (dest / ".git" / "info").mkdir(exist_ok=True)
    (dest / ".git" / "info" / "exclude").write_text(".claude/\n")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", f"{task.example} baseline")
    for branch in task.branches:
        _git(dest, "checkout", "-q", "-b", branch.name)
        root = EXAMPLES / task.example / "candidates" / branch.candidate
        for rel in mat.changed_files(task.example, branch.candidate):
            shutil.copyfile(root / rel, dest / rel)
        # A same-size copy with the same mtime looks unchanged to git's
        # stat cache (a same-length edit did, once): stage by content.
        _git(dest, "rm", "-r", "--cached", "-q", ".")
        _git(dest, "add", "-A")
        _git(dest, "commit", "-q", "-m", branch.message)
        _git(dest, "checkout", "-q", "main")
    target = dest / ".claude" / "skills" / "perfproof"
    shutil.copytree(skill_dir, target)
    return _branch_tips(dest)


def _branch_tips(repo: Path) -> dict[str, str]:
    out = _git(
        repo,
        "for-each-ref",
        "--format=%(refname:short) %(objectname)",
        "refs/heads",
    ).stdout
    tips: dict[str, str] = {}
    for line in out.splitlines():
        name, sha = line.split()
        tips[name] = sha
    return tips


def _trim(event: dict[str, object]) -> dict[str, object] | None:
    """Reduce a stream event to what scoring reads, or drop it."""
    kind = event.get("type")
    if kind == "system":
        subtype = event.get("subtype")
        if subtype in _NOISE_SUBTYPES:
            return None
        if subtype == "init":
            keep = ("type", "subtype", "model", "claude_code_version")
            reduced = {k: event[k] for k in keep if k in event}
            reduced["skills"] = event.get("skills", [])
            return reduced
    if kind == "rate_limit_event":
        return None
    if kind == "user":
        return {k: v for k, v in event.items() if k != "tool_use_result"}
    if kind == "assistant":
        return _without_thinking(event)
    return event


def _without_thinking(event: dict[str, object]) -> dict[str, object]:
    """Drop thinking blocks, which are large and never scored."""
    message = as_dict(event.get("message"))
    if message is None:
        return event
    blocks = (as_dict(b) for b in as_list(message.get("content")))
    kept = [b for b in blocks if b is not None and b.get("type") != "thinking"]
    return {**event, "message": {**message, "content": kept}}


def _claude_argv(
    task: Task, taskset: TaskSet, prompt: str, session_id: str | None
) -> list[str]:
    argv = [
        "claude",
        "-p",
        prompt,
        "--model",
        taskset.model,
        "--allowedTools",
        ALLOWED_TOOLS,
        "--permission-mode",
        "acceptEdits",
        "--output-format",
        "stream-json",
        "--verbose",
        "--setting-sources",
        "project,local",
        "--strict-mcp-config",
        "--max-budget-usd",
        str(task.budget_usd),
    ]
    if task.max_turns is not None:
        argv += ["--max-turns", str(task.max_turns)]
    if session_id is not None:
        argv += ["--resume", session_id]
    return argv


def _run_turn(
    argv: list[str], repo: Path, path_dir: Path, out: Path, err: Path
) -> tuple[TurnMeta, dict[str, object] | None]:
    env = {
        **os.environ,
        "PATH": f"{path_dir}{os.pathsep}{os.environ['PATH']}",
        **_ISOLATION_ENV,
    }
    started = time.monotonic()
    result: dict[str, object] | None = None
    session_id: str | None = None
    with (
        out.open("w", encoding="utf-8") as sink,
        err.open("w", encoding="utf-8") as errsink,
    ):
        proc = subprocess.Popen(
            argv,
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errsink,
            text=True,
        )
        timed_out = threading.Event()

        def _kill() -> None:
            timed_out.set()
            proc.kill()

        timer = threading.Timer(TURN_TIMEOUT_SECONDS, _kill)
        timer.start()
        try:
            assert proc.stdout is not None  # noqa: S101
            for line in proc.stdout:
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = as_dict(raw)
                if event is None:
                    continue
                sid = event.get("session_id")
                if isinstance(sid, str):
                    session_id = sid
                if event.get("type") == "result":
                    result = event
                trimmed = _trim(event)
                if trimmed is not None:
                    sink.write(json.dumps(trimmed) + "\n")
            proc.wait()
        finally:
            timer.cancel()
    meta = TurnMeta(
        prompt=argv[2],
        argv=argv,
        seconds=round(time.monotonic() - started, 2),
        exit_code=None if timed_out.is_set() else proc.returncode,
        session_id=session_id,
    )
    return meta, result


_WAITING_ON_MACHINE = re.compile(
    r"\b(?:load|busy|on battery|battery power|low power mode|thermal"
    r"|governor)\b",
    re.IGNORECASE,
)


def session_over(result: dict[str, object]) -> bool:
    """Tell whether a call ended the session with a ``Result:`` line.

    Parameters
    ----------
    result : dict
        The ``result`` event of the call.

    Returns
    -------
    bool
        True if the last non-empty line is a ``Result:`` line, so the
        skill has finished, and a scripted reply must not push a
        correct refusal into new work. The exception is a ``NO
        VERDICT`` whose reason names the machine (load, battery, power,
        thermal): the skill stopped to ask whether to go on under that
        warning (docs/skill.md, BS-10), which is a question the reply
        answers.
    """
    text = result.get("result")
    if not isinstance(text, str):
        return False
    lines = [ln.strip(" `*_") for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[-1].startswith("Result:"):
        return False
    body = lines[-1].removeprefix("Result:").strip()
    waiting = body.startswith("NO VERDICT") and _WAITING_ON_MACHINE.search(
        body
    )
    return not waiting


def asks_question(result: dict[str, object]) -> bool:
    """Tell whether a call ended on a question the user must answer.

    Parameters
    ----------
    result : dict
        The ``result`` event of the call.

    Returns
    -------
    bool
        True if a question mark appears in the last six non-empty lines
        of the final message, not counting the ``Result:`` line.
    """
    text = result.get("result")
    if not isinstance(text, str):
        return False
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    body = [ln for ln in lines if not ln.strip(" `*").startswith("Result:")]
    return "?" in "\n".join(body[-6:])


def _tests(tree: Path, python: Path) -> tuple[bool, str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    proc = subprocess.run(
        [
            str(python),
            "-m",
            "pytest",
            "-q",
            "-m",
            "not deliberately_flaky",
        ],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=_COMMAND_TIMEOUT,
    )
    tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-5:])
    return proc.returncode == 0, tail


def _changed_vs_main(repo: Path, ref: str | None) -> list[str]:
    args = ["diff", "--name-only", "main"]
    if ref is not None:
        args.append(ref)
    out = _git(repo, *args, "--").stdout
    return sorted(out.split())


def _tree_state(
    repo: Path, ref: str, scratch: Path, python: Path
) -> TreeState:
    if ref == "working":
        changed = _changed_vs_main(repo, None)
        shutil.copytree(
            repo,
            scratch,
            ignore=shutil.ignore_patterns(
                ".git", ".perfproof", ".claude", "__pycache__"
            ),
        )
    else:
        changed = _changed_vs_main(repo, ref)
        scratch.mkdir()
        archive = subprocess.run(
            ["git", "archive", ref],
            cwd=repo,
            capture_output=True,
            check=True,
            timeout=_COMMAND_TIMEOUT,
        )
        subprocess.run(
            ["tar", "-x", "-C", str(scratch)],
            input=archive.stdout,
            check=True,
            timeout=_COMMAND_TIMEOUT,
        )
    try:
        ok, tail = _tests(scratch, python)
    except subprocess.TimeoutExpired:
        ok, tail = False, "tests timed out"
    shutil.rmtree(scratch, ignore_errors=True)
    return TreeState(ref=ref, changed=changed, tests_ok=ok, tests_tail=tail)


def _run_verdicts(repo: Path) -> list[RunVerdict]:
    found: list[RunVerdict] = []
    for record in sorted((repo / ".perfproof" / "runs").glob("*/record.json")):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
            verdict = data["verdict"]
            found.append(
                RunVerdict(
                    run_id=str(data["run_id"]),
                    outcome=str(verdict["outcome"]),
                    reason=str(verdict["reason"]),
                    gate=None
                    if verdict.get("gate") is None
                    else str(verdict["gate"]),
                    calibration=str(verdict.get("calibration", "unknown")),
                    candidate_commit=(
                        None
                        if data.get("candidate_commit") is None
                        else str(data["candidate_commit"])
                    ),
                )
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def capture_state(repo: Path, seeded: dict[str, str], work: Path) -> State:
    """Read the repo's state after a run, running the example's tests.

    Parameters
    ----------
    repo : Path
        The task repo.
    seeded : dict of str to str
        Branch tips from ``build_repo``.
    work : Path
        Empty scratch directory the trees are exported into; removed
        tree by tree.

    Returns
    -------
    State
        Status, branches, the tests on every changed tree and the
        verdicts of the run records.
    """
    python = REPO_ROOT / ".venv" / "bin" / "python"
    branches = _branch_tips(repo)
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    work.mkdir(parents=True, exist_ok=True)
    refs = ["working", *sorted(b for b in branches if b not in seeded)]
    trees = [
        _tree_state(repo, ref, work / f"tree{i}", python)
        for i, ref in enumerate(refs)
    ]
    return State(
        status=_git(repo, "status", "--porcelain").stdout,
        current_branch=head,
        branches=branches,
        seeded=seeded,
        trees=trees,
        run_verdicts=_run_verdicts(repo),
    )


def run_task(
    task: Task,
    taskset: TaskSet,
    run_dir: Path,
    skill_dir: Path,
    path_dir: Path,
) -> Meta:
    """Run one task end to end and write its record.

    Parameters
    ----------
    task : Task
        The task to run.
    taskset : TaskSet
        The set it belongs to, for the model name.
    run_dir : Path
        The run's directory; ``run_dir/<task id>/`` is created.
    skill_dir : Path
        The skill to install.
    path_dir : Path
        Directory with the ``python`` and ``perfproof`` launchers.

    Returns
    -------
    Meta
        What was run, also written to ``meta.json``.
    """
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    load_start = list(os.getloadavg())
    out = run_dir / task.id
    out.mkdir(parents=True)
    repo = run_dir / "repos" / task.id
    seeded = build_repo(task, repo, skill_dir)
    turns: list[TurnMeta] = []
    session_id: str | None = None
    prompt = task.prompt
    replies = list(task.followups)
    while True:
        n = len(turns) + 1
        argv = _claude_argv(task, taskset, prompt, session_id)
        meta, result = _run_turn(
            argv,
            repo,
            path_dir,
            out / f"turn-{n}.jsonl",
            out / f"turn-{n}.err",
        )
        turns.append(meta)
        session_id = meta.session_id
        if (
            result is None
            or session_id is None
            or not replies
            or not asks_question(result)
            or session_over(result)
        ):
            break
        # Proving ends at the verdict. A generic "yes" to the follow-up
        # offer (try a fix, prove another branch) would start new work.
        if task.kind == "prove" and seen_verdicts(load_turns(out)):
            break
        prompt = replies.pop(0)
    state = capture_state(repo, seeded, run_dir / "work" / task.id)
    (out / "state.json").write_text(state.model_dump_json(indent=2))
    version = subprocess.run(
        ["claude", "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    ).stdout.strip()
    meta_all = Meta(
        task_id=task.id,
        started_at=started_at,
        wall_seconds=round(time.monotonic() - started, 2),
        load_start=load_start,
        load_end=list(os.getloadavg()),
        claude_version=version,
        skill_sha=skill_hash(skill_dir),
        turns=turns,
    )
    (out / "meta.json").write_text(meta_all.model_dump_json(indent=2))
    return meta_all
