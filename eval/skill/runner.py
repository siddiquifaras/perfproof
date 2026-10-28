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

import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel, ConfigDict

import realrepo
import sandbox
from taskset import Task, TaskSet
from transcript import as_dict, as_list, load_turns, seen_verdicts

REPO_ROOT = Path(__file__).resolve().parents[2]
"""The perfproof checkout this harness belongs to."""
EXAMPLES = REPO_ROOT / "examples"
SKILL_SOURCE = REPO_ROOT / "skills" / "perfproof"
"""The skill as it is in the working tree."""
VENV_ENV = "PERFPROOF_EVAL_VENV"
"""Names the Python environment that holds ``perfproof``, when it is not
this checkout's ``.venv``: the eval user's installed tool environment,
which has no checkout to point at. A path, not a secret."""


def venv_dir() -> Path:
    """Find the Python environment the agent's ``perfproof`` runs from.

    Returns
    -------
    Path
        ``$PERFPROOF_EVAL_VENV`` if set (the eval user's ``uv tool``
        environment), otherwise this checkout's ``.venv``.
    """
    named = os.environ.get(VENV_ENV)
    return Path(named) if named else REPO_ROOT / ".venv"


ALLOWED_TOOLS = "Bash,Read,Write,Edit,Glob,Grep,Skill"
"""Tools allowed without a prompt; nobody is there to answer one."""
TURN_TIMEOUT_SECONDS = 1500.0
SECRETS_USED: list[str] = []
"""Sign-in values handed to agents by this process; memory only."""
# The owner's own CLAUDE.md and memory change how the skill answers (a
# private report format leaked into one transcript): runs ignore them.
ISOLATION_ENV = {
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
_MIN_CALL_USD = 0.05
"""Below this much task budget, no further call is started."""


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
    setup_seconds : float
        Time spent seeding and building the repo before the first call.
    """

    task_id: str
    started_at: str
    wall_seconds: float
    load_start: list[float]
    load_end: list[float]
    claude_version: str
    skill_sha: str
    turns: list[TurnMeta]
    setup_seconds: float = 0.0


def confined(
    argv: list[str],
    tree: Path,
    *,
    allow_docker: bool = False,
    strict: bool = True,
) -> tuple[list[str], dict[str, str]]:
    """Wrap a harness command that touches an agent-controlled tree.

    Parameters
    ----------
    argv : list of str
        The command.
    tree : Path
        The task tree (or any path two levels inside the run's work
        directory); the sandbox scratch is two levels up, in
        ``sandbox/<name>``.
    allow_docker : bool, optional
        Whether the command may run the Docker client.
    strict : bool, optional
        Fail when ``sandbox-exec`` is missing, instead of running
        unconfined with only the clean environment.

    Returns
    -------
    tuple of (list of str, dict of str to str)
        The command, behind ``sandbox-exec``, and the sanitized
        environment: nothing of the owner's survives that
        ``sandbox.agent_env`` does not allow, and git runs no
        repo-local helper or hook. Harness steps on an agent's tree
        (``git status``, the tests, an archive) would otherwise run the
        agent's repo configuration with the owner's credentials.
    """
    scratch = tree.parents[1] / "sandbox" / tree.name
    env = sandbox.agent_env(os.environ, scratch, [])
    wrapped = sandbox.wrap(
        argv,
        scratch,
        checkout=REPO_ROOT,
        allow_docker=allow_docker,
        strict=strict,
        cwd=tree,
        read=[REPO_ROOT / ".venv", REPO_ROOT / "src", scratch],
        write=[scratch / "private"],
    )
    return wrapped, env


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    argv, env = confined(["git", *args], repo, strict=False)
    done = subprocess.run(
        argv,
        cwd=repo,
        env=env,
        start_new_session=True,
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
    venv = venv_dir() / "bin"
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
        launcher.write_text(
            f'#!/bin/sh\nexec "{target}" "$@"\n', encoding="utf-8"
        )
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
    if task.example is None:
        msg = f"task {task.id} has no example to build"
        raise ValueError(msg)
    mat = _materialize_module()
    mat.materialize(task.example, None, dest)
    for spec in dest.glob("spec*.json"):
        spec.unlink()
    _git(dest, "init", "-q", "-b", "main")
    (dest / ".git" / "info").mkdir(exist_ok=True)
    (dest / ".git" / "info" / "exclude").write_text(
        ".claude/\n", encoding="utf-8"
    )
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


def build_real_repo(
    task: Task,
    spec: realrepo.RepoSpec,
    dest: Path,
    skill_dir: Path,
    cache_dir: Path,
    extra_path: list[Path],
    before_setup: Callable[[], None] | None = None,
) -> dict[str, str]:
    """Build a real repository: the commit, the skill, the build steps.

    Parameters
    ----------
    task : Task
        A task with ``repo`` set.
    spec : RepoSpec
        The manifest's repository, commits, build commands and oracle.
    dest : Path
        Directory to create; it must not exist.
    skill_dir : Path
        The skill to copy to ``.claude/skills/perfproof/``.
    cache_dir : Path
        Where fetched repositories are cached between runs.
    extra_path : list of Path
        Directories for ``PATH`` while building.
    before_setup : callable, optional
        Run once the repository is seeded and before the build steps,
        outside the profile. A Go task downloads its modules here: Go
        on macOS verifies a certificate through ``trustd``, a Mach
        service the profile closes, so a confined ``go test`` cannot
        fetch anything and must find the module cache full.

    Returns
    -------
    dict of str to str
        Branch name to tip sha as seeded, ``main`` included.

    Raises
    ------
    realrepo.SetupError
        If the fetch or a build step fails.
    """
    if task.repo is None:
        msg = f"task {task.id} has no real repository"
        raise ValueError(msg)
    seeded = realrepo.seed_repo(spec, task.repo, dest, cache_dir)
    (dest / ".git" / "info").mkdir(exist_ok=True)
    (dest / ".git" / "info" / "exclude").write_text(
        ".claude/\n", encoding="utf-8"
    )
    shutil.copytree(skill_dir, dest / ".claude" / "skills" / "perfproof")
    if before_setup is not None:
        before_setup()
    realrepo.run_setup(
        spec,
        dest,
        extra_path,
        confine=sandbox.as_eval_user(),
        hide_planted=task.is_hidden_fix,
    )
    return seeded


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
    task: Task,
    taskset: TaskSet,
    prompt: str,
    session_id: str | None,
    budget: float,
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
        f"{budget:.2f}",
    ]
    if task.max_turns is not None:
        argv += ["--max-turns", str(task.max_turns)]
    if session_id is not None:
        argv += ["--resume", session_id]
    return argv


def run_turn(
    argv: list[str],
    repo: Path,
    env: dict[str, str],
    out: Path,
    err: Path,
    timeout: float,
    prefix: list[str] | None = None,
    redact: list[str] | None = None,
) -> tuple[TurnMeta, dict[str, object] | None]:
    """Run one ``claude`` call, stream its events to ``out``.

    Parameters
    ----------
    argv : list of str
        The command; ``argv[2]`` is recorded as the prompt.
    repo : Path
        Working directory.
    env : dict of str to str
        Environment.
    out, err : Path
        Files for the trimmed event stream and for stderr.
    timeout : float
        Seconds before the call is killed.
    prefix : list of str, optional
        Words to run before ``argv`` (the ``sandbox-exec`` launcher);
        ``argv`` is what gets recorded.
    redact : list of str, optional
        Strings (a credential handed to the agent) replaced by
        ``<redacted>`` in every recorded event and in the stderr file,
        so a transcript that kept the agent's ``env`` output holds no
        secret.

    Returns
    -------
    tuple of (TurnMeta, dict or None)
        What was run, and the call's ``result`` event if it sent one.
        The call ends when ``claude`` exits, not when its output pipe
        closes: a background process the agent started holds the pipe
        open, and waiting for it would hang the harness. The whole
        process group is killed once the call is over, normally or on
        timeout, and also if the harness is interrupted.
    """
    started = time.monotonic()
    state: dict[str, object] = {"result": None, "session": None}
    with (
        out.open("w", encoding="utf-8") as sink,
        err.open("w", encoding="utf-8") as errsink,
    ):
        proc = subprocess.Popen(
            [*(prefix or []), *argv],
            cwd=repo,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=errsink,
            text=True,
            start_new_session=True,
        )

        def _pump() -> None:
            assert proc.stdout is not None  # noqa: S101
            for line in proc.stdout:
                if redact:
                    line = sandbox.redact(line, redact)
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = as_dict(raw)
                if event is None:
                    continue
                sid = event.get("session_id")
                if isinstance(sid, str):
                    state["session"] = sid
                if event.get("type") == "result":
                    state["result"] = event
                trimmed = _trim(event)
                if trimmed is not None:
                    sink.write(json.dumps(trimmed) + "\n")

        reader = threading.Thread(target=_pump, daemon=True)
        reader.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            reader.join(timeout=30)
            if proc.stdout is not None:
                proc.stdout.close()
    if redact:
        err.write_text(
            sandbox.redact(
                err.read_text(encoding="utf-8", errors="replace"), redact
            ),
            encoding="utf-8",
        )
    session = state["session"]
    result = state["result"]
    meta = TurnMeta(
        prompt=argv[2],
        argv=argv,
        seconds=round(time.monotonic() - started, 2),
        exit_code=None if timed_out else proc.returncode,
        session_id=session if isinstance(session, str) else None,
    )
    return meta, as_dict(result)


_WAITING_ON_MACHINE = re.compile(
    r"\b(?:load|busy|on battery|battery power|low power mode|thermal"
    r"|governor)\b",
    re.IGNORECASE,
)


def auth_failed(result: dict[str, object] | None) -> bool:
    """Tell whether a call ended because the sign-in was rejected.

    Parameters
    ----------
    result : dict or None
        The ``result`` event of the call.

    Returns
    -------
    bool
        True for an error result with HTTP status 401 or 403. Observed
        for a rejected OAuth token (2026-10-22, claude 2.1.291): the
        result has ``is_error`` true, ``api_error_status`` 401,
        ``terminal_reason`` ``api_error`` and a "Failed to
        authenticate" text. Such a call says nothing about the skill.
    """
    if result is None or result.get("is_error") is not True:
        return False
    return result.get("api_error_status") in (401, 403)


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


_FLAKY_MARKER = "deliberately_flaky"


def oracle_argv(tree: Path, python: Path) -> list[str]:
    """Build the oracle's pytest command for one example tree.

    pytest 9.2 rejects a ``-m`` expression that names a marker the
    tree does not register when ``--strict-markers`` is on, so the
    flaky test is deselected only in a tree that declares the marker.

    Parameters
    ----------
    tree : Path
        The example tree the tests run in.
    python : Path
        The interpreter that runs pytest.

    Returns
    -------
    list of str
        The argv, with ``-m "not deliberately_flaky"`` only when the
        tree's ``pytest.ini`` or ``pyproject.toml`` names the marker.
    """
    argv = [str(python), "-m", "pytest", "-q"]
    for name in ("pytest.ini", "pyproject.toml"):
        config = tree / name
        if config.is_file() and _FLAKY_MARKER in config.read_text(
            encoding="utf-8"
        ):
            return [*argv, "-m", f"not {_FLAKY_MARKER}"]
    return argv


def _tests(tree: Path, python: Path) -> tuple[bool, str]:
    argv, env = confined(oracle_argv(tree, python), tree)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        argv,
        cwd=tree,
        env=env,
        start_new_session=True,
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


def copy_tree(repo: Path, scratch: Path) -> None:
    """Copy an agent's working tree for the test run.

    Parameters
    ----------
    repo : Path
        The task repo.
    scratch : Path
        Directory to create.

    Notes
    -----
    Symlinks are copied as symlinks. This copy runs unconfined, and the
    agent can plant ``ln -s ~/.ssh/id_rsa lnk``: dereferencing it would
    put the file's content in the copy, where the tests could print it
    into ``state.json``. A link left in place points at the same target
    but is only ever read by the confined test run, which cannot read
    it.
    """
    shutil.copytree(
        repo,
        scratch,
        symlinks=True,
        ignore=shutil.ignore_patterns(
            ".git", ".perfproof", ".claude", "__pycache__"
        ),
    )


def _tree_state(
    repo: Path, ref: str, scratch: Path, python: Path
) -> TreeState:
    if ref == "working":
        changed = _changed_vs_main(repo, None)
        copy_tree(repo, scratch)
    else:
        changed = _changed_vs_main(repo, ref)
        scratch.mkdir()
        argv, env = confined(["git", "archive", ref], repo, strict=False)
        archive = subprocess.run(
            argv,
            cwd=repo,
            env=env,
            start_new_session=True,
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


def run_verdicts(repo: Path) -> list[RunVerdict]:
    """Read the verdicts of the run records an agent left in its repo.

    Parameters
    ----------
    repo : Path
        The task repo.

    Returns
    -------
    list of RunVerdict
        Oldest first. A record that is a symlink, or sits in a symlinked
        directory, is skipped: this read is unconfined.
    """
    found: list[RunVerdict] = []
    for record in sorted((repo / ".perfproof" / "runs").glob("*/record.json")):
        # Unconfined read of a file the agent controls: a link out of
        # the tree is never followed.
        if record.is_symlink() or record.parent.is_symlink():
            continue
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


PROFILE_RESULT_LIMIT = 1_000_000
"""Bytes: a result is a few kilobytes; a larger file is skipped."""


PROFILE_SKIPPED = "profile-skipped.txt"
"""Written beside the copies when a result file was not kept."""


def profile_file_name(name: str, run_id: str) -> str:
    """Name the copy of one profile's result.

    Each part is cut to 40 characters and a hash of the full
    ``<name>/<run id>`` is added, so a name the agent made long cannot
    exceed the file system's limit, and two profiles cannot collide.

    Parameters
    ----------
    name : str
        The benchmark's name (a directory of the repo).
    run_id : str
        The profile run's directory name.

    Returns
    -------
    str
        ``profile-<name>-<id>-<hash>.json``.
    """
    # A non-UTF-8 directory name arrives as surrogate escapes on Linux.
    key = f"{name}/{run_id}".encode("utf-8", "surrogateescape")
    digest = hashlib.sha256(key).hexdigest()[:12]
    return f"profile-{name[:40]}-{run_id[:40]}-{digest}.json"


def _printable(text: str) -> str:
    """Make a path text safe to write, whatever bytes the agent used."""
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _copy_profile_result(
    source: Path, out: Path, saved: list[Path]
) -> str | None:
    """Copy one saved result; return why it was skipped, or ``None``.

    Raises
    ------
    OSError, ValueError
        For anything else the agent's tree does; the caller records it
        as a skip.
    """
    if any(p.is_symlink() for p in (source, *source.parents[:4])):
        return "a symlink, or in a symlinked directory"
    if not stat.S_ISREG(source.lstat().st_mode):
        return "not a regular file"
    with source.open("rb") as f:
        data = f.read(PROFILE_RESULT_LIMIT + 1)
    if len(data) > PROFILE_RESULT_LIMIT:
        return f"over {PROFILE_RESULT_LIMIT} bytes"
    target = out / profile_file_name(
        source.parents[1].name, source.parent.name
    )
    target.write_bytes(data)
    saved.append(target)
    return None


def save_profile_results(repo: Path, out: Path) -> list[Path]:
    """Copy the agent's saved profile results next to the transcripts.

    Every ``.perfproof/profiles/<name>/<id>/result.json`` of the repo
    (written by ``perfproof profile``) goes to the file
    `profile_file_name` gives, so the profile id stays in the name.
    Nothing else of the profile directories is copied: they hold the
    profiler's raw output.

    Parameters
    ----------
    repo : Path
        The task repo.
    out : Path
        The task directory.

    Returns
    -------
    list of Path
        The files written, in name order. A file that is a symlink or
        not regular, in a symlinked directory, larger than
        `PROFILE_RESULT_LIMIT`, not readable or not writable is
        skipped, and each skip is recorded with its reason in
        `PROFILE_SKIPPED` (the agent controls the tree, and this read
        is unconfined). Nothing here raises for what the agent made.
    """
    saved: list[Path] = []
    skipped: list[str] = []
    profiles = repo / ".perfproof" / "profiles"
    try:
        sources = sorted(profiles.glob("*/*/result.json"))
    except OSError as exc:
        sources = []
        skipped.append(f"profiles: not listable ({exc.strerror})")
    for source in sources:
        label = _printable("/".join(source.parts[-3:-1]))[:120]
        try:
            reason = _copy_profile_result(source, out, saved)
        except (OSError, ValueError) as exc:
            reason = f"not copied ({getattr(exc, 'strerror', None) or exc})"
        if reason is not None:
            skipped.append(f"{label}: {reason}")
    if skipped:
        with contextlib.suppress(OSError):
            (out / PROFILE_SKIPPED).write_text(
                "\n".join(skipped) + "\n",
                encoding="utf-8",
                errors="backslashreplace",
            )
    return saved


def _real_trees(
    repo: Path,
    seeded: dict[str, str],
    task: Task,
    spec: realrepo.RepoSpec,
    extra_path: list[Path],
) -> list[TreeState]:
    """Name what changed in each tree; run the oracle on kept branches.

    The oracle runs in the repo itself, with the branch checked out and
    the original checkout restored afterwards, because the build (a
    venv with an editable install, a build directory) lives there.
    """
    branches = _branch_tips(repo)
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    trees = [
        TreeState(
            ref="working",
            changed=_changed_vs_main(repo, None),
            tests_ok=None,
            tests_tail="",
        )
    ]
    for ref in sorted(b for b in branches if b not in seeded):
        changed = _changed_vs_main(repo, ref)
        ok: bool | None = None
        tail = "not run"
        if task.expect.fix != "none" and ref.startswith("perfproof/"):
            moved = _git(repo, "checkout", "-q", ref, check=False)
            if moved.returncode != 0:
                ok, tail = False, "could not check out: " + moved.stderr
            else:
                ok, tail = realrepo.run_oracle(spec, repo, extra_path)
                _git(repo, "checkout", "-q", head)
        trees.append(
            TreeState(ref=ref, changed=changed, tests_ok=ok, tests_tail=tail)
        )
    return trees


def capture_state(
    repo: Path,
    seeded: dict[str, str],
    work: Path,
    real: tuple[Task, realrepo.RepoSpec, list[Path]] | None = None,
) -> State:
    """Read the repo's state after a run, running the tests.

    Parameters
    ----------
    repo : Path
        The task repo.
    seeded : dict of str to str
        Branch tips from ``build_repo``.
    work : Path
        Empty scratch directory the trees are exported into; removed
        tree by tree.
    real : tuple of (Task, RepoSpec, list of Path), optional
        For a real repository: the task, its manifest spec and the
        ``PATH`` directories. The manifest's test command then runs on
        each kept branch; the example's tests do not apply.

    Returns
    -------
    State
        Status, branches, the tests on every changed tree and the
        verdicts of the run records.
    """
    status = _git(repo, "status", "--porcelain").stdout
    branches = _branch_tips(repo)
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if real is not None:
        trees = _real_trees(repo, seeded, *real)
    else:
        python = venv_dir() / "bin" / "python"
        work.mkdir(parents=True, exist_ok=True)
        refs = ["working", *sorted(b for b in branches if b not in seeded)]
        trees = [
            _tree_state(repo, ref, work / f"tree{i}", python)
            for i, ref in enumerate(refs)
        ]
    return State(
        status=status,
        current_branch=head,
        branches=branches,
        seeded=seeded,
        trees=trees,
        run_verdicts=run_verdicts(repo),
    )


def call_budget(task: Task, spent: float) -> float:
    """Dollar cap for the next ``claude`` call.

    Parameters
    ----------
    task : Task
        Supplies ``budget_usd`` and ``task_budget_usd``.
    spent : float
        Cost of the calls so far.

    Returns
    -------
    float
        ``budget_usd``, lowered to what is left of ``task_budget_usd``.
    """
    if task.task_budget_usd is None:
        return task.budget_usd
    return min(task.budget_usd, task.task_budget_usd - spent)


def budget_spent(task: Task, spent: float) -> bool:
    """Tell whether too little of the task budget is left for a call."""
    return (
        task.task_budget_usd is not None
        and task.task_budget_usd - spent < _MIN_CALL_USD
    )


def call_timeout(task: Task) -> float:
    """Wall-time cap in seconds for one ``claude`` call of a task."""
    return task.timeout_s or TURN_TIMEOUT_SECONDS


def _call_cost(result: dict[str, object] | None) -> float:
    cost = None if result is None else result.get("total_cost_usd")
    return float(cost) if isinstance(cost, int | float) else 0.0


def run_task(
    task: Task,
    taskset: TaskSet,
    run_dir: Path,
    skill_dir: Path,
    path_dir: Path,
    cache_dir: Path | None = None,
    work_root: Path | None = None,
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
    cache_dir : Path, optional
        Where real repositories are cached; ``run_dir/../cache`` by
        default.
    work_root : Path, optional
        Where the task repo, launchers and config scratch live, as
        ``<work_root>/<run id>/``. Put it outside the perfproof
        checkout so the agent's working directory has no ``eval/`` near
        it; ``run_dir`` itself when omitted.

    Returns
    -------
    Meta
        What was run, also written to ``meta.json``.

    Raises
    ------
    realrepo.SetupError
        If a real repository cannot be fetched or built, or the machine
        lacks a toolchain it needs. Nothing is written to the task
        directory then, so the caller can record the task as skipped.
    """
    started = time.monotonic()
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    load_start = list(os.getloadavg())
    out = run_dir / task.id
    work = run_dir if work_root is None else work_root / run_dir.name
    repo = work / "repos" / task.id
    # The task's temp directory is outside the run tree: empty it once,
    # before any build step uses it and the agent starts.
    sandbox.reset_tmp(work / "sandbox" / task.id / "private")
    extra_path = [path_dir]
    spec: realrepo.RepoSpec | None = None
    toolchains: list[Path] = []
    if task.repo is not None:
        # Native toolchains first, so a launcher that runs the same one
        # in a container (Linux binaries) never shadows them.
        toolchains = realrepo.toolchain_dirs(task.repo)
        extra_path = [*toolchains, path_dir]
        missing = realrepo.missing_requirements(task.repo)
        if missing:
            msg = (
                f"skipped: {realrepo.NEEDS_DOCKER}"
                if missing == [realrepo.NEEDS_DOCKER]
                else "missing on this machine: " + ", ".join(missing)
            )
            raise realrepo.SetupError(msg)
        if task.repo.shim is not None:
            shims = work / "shims" / task.repo.shim
            realrepo.write_shims(shims, task.repo.shim)
            extra_path.append(shims)
        spec = realrepo.load_spec(task.repo.manifest)
        branch = task.repo.branch
        fetch_go_modules = "go" in task.repo.requires and bool(toolchains)

        def go_modules() -> None:
            realrepo.prefill_go_modcache(
                spec, repo, extra_path, [] if branch is None else [branch]
            )

        seeded = build_real_repo(
            task,
            spec,
            repo,
            skill_dir,
            cache_dir or run_dir.parent / "cache",
            extra_path,
            go_modules if fetch_go_modules else None,
        )
        env = realrepo.task_env(spec, repo, extra_path)
    else:
        seeded = build_repo(task, repo, skill_dir)
        env = sandbox.agent_env(
            os.environ, work / "sandbox" / task.id, [path_dir]
        )
    config_dir = work / "claude-config" / task.id
    config_dir.mkdir(parents=True, exist_ok=True)
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    # claude otherwise uses /tmp/claude-<uid>, which also holds other
    # sessions' scratch and is closed to the agent.
    env["CLAUDE_CODE_TMPDIR"] = str(config_dir / "tmp")
    (config_dir / "tmp").mkdir(exist_ok=True)
    env.update(ISOLATION_ENV)
    token = sandbox.claude_oauth_token(refresh=sandbox.refresh_sign_in)
    if token is not None:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    secrets = sandbox.env_secrets(env)
    SECRETS_USED.extend(s for s in secrets if s not in SECRETS_USED)
    forms = sandbox.secret_forms(secrets)
    setup_seconds = round(time.monotonic() - started, 2)
    out.mkdir(parents=True)
    turns: list[TurnMeta] = []
    session_id: str | None = None
    prompt = task.prompt
    replies = list(task.followups)
    spent = 0.0
    while True:
        n = len(turns) + 1
        argv = _claude_argv(
            task, taskset, prompt, session_id, call_budget(task, spent)
        )
        launch = sandbox.wrap(
            [],
            work / "sandbox" / task.id,
            checkout=REPO_ROOT,
            allow_docker=bool(task.repo and task.repo.shim),
            cwd=repo,
            read=[
                path_dir,
                *toolchains,
                work / "shims",
                work / "sandbox" / task.id,
                REPO_ROOT / ".venv",
                REPO_ROOT / "src",
            ],
            write=[config_dir, work / "sandbox" / task.id / "private"],
        )
        meta, result = run_turn(
            argv,
            repo,
            env,
            out / f"turn-{n}.jsonl",
            out / f"turn-{n}.err",
            call_timeout(task),
            launch,
            forms,
        )
        if auth_failed(result):
            # Not a task failure: the sign-in is dead, so this task and
            # every later one would be scored on nothing. Drop the
            # partial record, as for a task never started.
            shutil.rmtree(out, ignore_errors=True)
            msg = (
                f"the claude sign-in was rejected during {task.id} "
                "(HTTP 401/403); renew the token"
            )
            raise sandbox.SandboxUnavailableError(msg)
        turns.append(meta)
        if spec is not None:
            realrepo.kill_containers()
        spent += _call_cost(result)
        session_id = meta.session_id
        if (
            result is None
            or session_id is None
            or not replies
            or not asks_question(result)
            or session_over(result)
            or budget_spent(task, spent)
        ):
            break
        # Proving ends at the verdict. A generic "yes" to the follow-up
        # offer (try a fix, prove another branch) would start new work.
        if task.kind == "prove" and seen_verdicts(load_turns(out)):
            break
        prompt = replies.pop(0)
    if spec is not None:
        realrepo.kill_containers()
    state = capture_state(
        repo,
        seeded,
        run_dir / "work" / task.id,
        None if spec is None else (task, spec, extra_path),
    )
    (out / "state.json").write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )
    save_profile_results(repo, out)
    version = subprocess.run(
        ["claude", "--version"],
        env=sandbox.agent_env(os.environ, work / "sandbox" / "version", []),
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
        setup_seconds=setup_seconds,
    )
    (out / "meta.json").write_text(
        meta_all.model_dump_json(indent=2), encoding="utf-8"
    )
    return meta_all
