"""Seed a real public repository for a skill-eval task.

The repository, commit, build steps and test command are read from the
mined task's manifest (``eval/tasks/<family>/<id>.json``) and its
``eval/mine/checks.json`` entry, so they have one source. A repository
is fetched once into a bare cache (shallow, only the commits a task
needs), then cloned from there into each run, so repeated runs do not
download it again. The run's clone has no remote at all, so there is
nothing to fetch the fix from or push to by name, and the agent's
environment carries no GitHub token.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import shutil
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

import sandbox
from taskset import RealRepo

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = REPO_ROOT / "eval" / "tasks"
CHECKS_FILE = REPO_ROOT / "eval" / "mine" / "checks.json"
WORKLOADS = TASKS_DIR / "workloads"
_WORKLOADS_RELATIVE = "../../../../tasks/workloads"
"""How ``checks.json`` reaches the workload files from its work dir."""
_SPLITS = frozenset({"development", "development-candidate"})
"""Splits the skill eval may use. ``held-out`` is never in it."""
_STATUSES = frozenset({"runs", "builds"})
"""Statuses that mean the task builds here (``builds``: its workload was
not run to a label, which the skill eval does not need)."""
GITHUB = "https://github.com"
CONTAINER_LABEL = "perfproof-eval=1"
FETCH_TIMEOUT_S = 1800.0
SETUP_TIMEOUT_S = 3600.0
ORACLE_TIMEOUT_S = 1800.0
_GIT_IDENTITY = (("user.name", "dev"), ("user.email", "dev@example.com"))
_PROVIDED = {
    "go": ("go",),
    "ruby": ("ruby", "bundle", "gem", "rake"),
}
_SHIM_TEMPLATES = {
    "go": (
        "docker run --rm --init --stop-timeout 10 --label perfproof-eval=1"
        " -e GOTOOLCHAIN=local"
        ' -v "$top":/src -v perfproof-gomod:/go/pkg/mod -w "/src$rel"'
        ' golang:1.25 go "$@"'
    ),
    "ruby": (
        "docker run --rm --init --stop-timeout 10 --label perfproof-eval=1"
        " -e BUNDLE_WITHOUT=rubocop:mdl:doc:job:cable:storage"
        ' -v "$top":/src -v perfproof-gems:/usr/local/bundle'
        ' -w "/src$rel" perfproof-ruby-4.0-tzdata-legacy'
        ' "$(basename "$0")" "$@"'
    ),
}


NEEDS_DOCKER = "needs docker, not available to ppeval"
"""Why a Docker-backed task is skipped for the eval user."""


class SetupError(RuntimeError):
    """The repository could not be fetched or built."""


class RepoSpec(BaseModel):
    """What a manifest says about a task's repository.

    Attributes
    ----------
    repository : str
        ``owner/name`` on GitHub.
    task_commit : str
        The commit the task starts from.
    reference_commit : str
        The commit of the reference change.
    reference_number : str
        Pull request number of the reference change, or ``""``.
    reference_title : str
        Title of the reference change.
    setup : list of str
        Build commands, run in the repo, as ``checks.json`` lists them.
    env : dict of str to str
        Environment the build and tests need.
    oracle : str or None
        The project's test command.
    """

    model_config = ConfigDict(extra="forbid")

    repository: str
    task_commit: str
    reference_commit: str
    reference_number: str
    reference_title: str
    setup: list[str]
    env: dict[str, str]
    oracle: str | None


def load_spec(manifest_id: str) -> RepoSpec:
    """Read a task's manifest and build recipe.

    Parameters
    ----------
    manifest_id : str
        Manifest id, for example ``sharkdp-fd-pr853``.

    Returns
    -------
    RepoSpec
        Repository, commits, build commands, environment and oracle.

    Raises
    ------
    FileNotFoundError
        If no manifest has this id.
    ValueError
        If the manifest is not a ``development`` or
        ``development-candidate`` task whose status is ``runs`` or
        ``builds``; a held-out task never enters this eval.
    KeyError
        If ``checks.json`` has no entry for it.
    """
    found = sorted(TASKS_DIR.glob(f"*/{manifest_id}.json"))
    if not found:
        msg = f"no manifest for {manifest_id}"
        raise FileNotFoundError(msg)
    manifest: dict[str, Any] = json.loads(found[0].read_text(encoding="utf-8"))
    checks: dict[str, Any] = json.loads(
        CHECKS_FILE.read_text(encoding="utf-8")
    )[manifest_id]
    if (
        manifest.get("split") not in _SPLITS
        or manifest.get("status") not in _STATUSES
    ):
        msg = (
            f"{manifest_id} is {manifest.get('split')!r}/"
            f"{manifest.get('status')!r}: only built development tasks "
            "may be used (never a held-out one)"
        )
        raise ValueError(msg)
    oracle: dict[str, Any] = manifest.get("oracle") or {}
    change = manifest["reference_change"]
    url = str(change["url"])
    return RepoSpec(
        repository=manifest["repository"],
        task_commit=manifest["task_commit"],
        reference_commit=change["commit"],
        reference_number=url.rsplit("/", 1)[-1] if "/pull/" in url else "",
        reference_title=str(change.get("title", "")),
        setup=[
            c.replace(_WORKLOADS_RELATIVE, str(WORKLOADS))
            for c in checks["setup"]
        ],
        env=dict(checks.get("env", {})),
        oracle=oracle.get("command"),
    )


def _git(
    cwd: Path, *args: str, timeout: float = FETCH_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=sandbox.agent_env(
            os.environ, cwd.parents[1] / "sandbox" / cwd.name, []
        ),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _fetch_cached(
    spec: RepoSpec, cache_dir: Path, sha: str, depth: int, host: str
) -> Path:
    """Make sure the bare cache holds ``sha`` to ``depth`` commits."""
    bare = cache_dir.resolve() / (spec.repository.replace("/", "__") + ".git")
    if not bare.exists():
        bare.mkdir(parents=True)
        init = _git(bare, "init", "-q", "--bare")
        if init.returncode != 0:
            msg = f"git init failed: {init.stderr}"
            raise SetupError(msg)
    have = _git(bare, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
    if have and (
        depth == 1 or _git(bare, "cat-file", "-e", f"{sha}^").returncode == 0
    ):
        return bare
    url = f"{host}/{spec.repository}.git"
    done = _git(
        bare,
        "fetch",
        "-q",
        "--no-tags",
        f"--depth={depth}",
        url,
        f"+{sha}:refs/cache/{sha}",
    )
    if done.returncode != 0:
        msg = f"fetching {spec.repository}@{sha[:10]} failed: {done.stderr}"
        raise SetupError(msg)
    return bare


def seed_repo(
    spec: RepoSpec,
    real: RealRepo,
    dest: Path,
    cache_dir: Path,
    host: str = GITHUB,
) -> dict[str, str]:
    """Clone the task's commit into ``dest`` from the cache.

    Parameters
    ----------
    spec : RepoSpec
        The manifest's repository and commits.
    real : RealRepo
        The task's repo settings; ``branch`` picks the prove layout.
    dest : Path
        Directory to create.
    cache_dir : Path
        Directory holding the bare caches.
    host : str, optional
        Where repositories are fetched from; GitHub by default.

    Returns
    -------
    dict of str to str
        Branch name to tip sha as seeded. A find task has ``main`` at
        the task commit and nothing after it; a prove task has ``main``
        at the task commit and ``real.branch`` at the reference change.

    Raises
    ------
    SetupError
        If a fetch fails, or the reference change's parent is not the
        task commit.
    """
    prove = real.branch is not None
    top = spec.reference_commit if prove else spec.task_commit
    bare = _fetch_cached(spec, cache_dir, top, 2 if prove else 1, host)
    dest.mkdir(parents=True)
    steps = [
        ["init", "-q", "-b", "main"],
        [
            "fetch",
            "-q",
            "--no-tags",
            f"--depth={2 if prove else 1}",
            str(bare),
            f"refs/cache/{top}",
        ],
    ]
    for args in steps:
        done = _git(dest, *args)
        if done.returncode != 0:
            msg = f"git {args[0]} failed: {done.stderr}"
            raise SetupError(msg)
    if prove:
        assert real.branch is not None  # noqa: S101
        parent = _git(dest, "rev-parse", "FETCH_HEAD^").stdout.strip()
        if parent != spec.task_commit:
            msg = f"parent {parent[:10]} is not the task commit"
            raise SetupError(msg)
        commands = [
            ["branch", real.branch, "FETCH_HEAD"],
            ["checkout", "-q", "-b", "main", parent],
        ]
    else:
        commands = [["checkout", "-q", "-b", "main", "FETCH_HEAD"]]
    commands += [
        *[["config", key, value] for key, value in _GIT_IDENTITY],
    ]
    for args in commands:
        done = _git(dest, *args)
        if done.returncode != 0:
            msg = f"git {args[0]} failed: {done.stderr}"
            raise SetupError(msg)
    tips: dict[str, str] = {}
    out = _git(
        dest,
        "for-each-ref",
        "--format=%(refname:short) %(objectname)",
        "refs/heads",
    ).stdout
    for line in out.splitlines():
        name, sha = line.split()
        tips[name] = sha
    return tips


def task_env(
    spec: RepoSpec, repo: Path, extra_path: list[Path]
) -> dict[str, str]:
    """Environment for build, test and agent commands in the repo.

    A ``.venv`` in the repo is put first on ``PATH`` and exported as
    ``VIRTUAL_ENV``, as a user working in the project would have it.

    Parameters
    ----------
    spec : RepoSpec
        Supplies the manifest's variables.
    repo : Path
        The task repo.
    extra_path : list of Path
        Directories to put on ``PATH`` after the venv.

    Returns
    -------
    dict of str to str
        The full environment.
    """
    dirs = list(extra_path)
    venv = repo / ".venv"
    if (venv / "bin").is_dir():
        dirs.insert(0, venv / "bin")
    env = sandbox.agent_env(
        os.environ, repo.parents[1] / "sandbox" / repo.name, dirs
    )
    env.update(spec.env)
    if (venv / "bin").is_dir():
        env["VIRTUAL_ENV"] = str(venv)
    return env


def _run(
    command: str,
    cwd: Path,
    env: dict[str, str],
    timeout: float,
    *,
    confine: bool = False,
    read: Sequence[Path] = (),
) -> tuple[int, str]:
    argv = shlex.split(command)
    if confine:
        argv = sandbox.wrap(
            argv,
            cwd.parents[1] / "sandbox" / cwd.name,
            checkout=REPO_ROOT,
            allow_docker=argv[:1] == ["docker"],
            cwd=cwd,
            read=[
                cwd.parents[1] / "sandbox" / cwd.name,
                REPO_ROOT / ".venv",
                REPO_ROOT / "src",
                *read,
            ],
            write=[cwd.parents[1] / "sandbox" / cwd.name / "private"],
        )
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        return 127, str(exc)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # The group: cargo, ninja and docker clients are grandchildren.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=30)
        kill_containers()
        return 124, "timed out"
    except BaseException:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        raise
    tail = "\n".join(out.strip().splitlines()[-8:])
    return proc.returncode, tail


def kill_containers() -> None:
    """Kill containers started by the toolchain launchers."""
    try:
        listed = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label={CONTAINER_LABEL}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        ids = listed.stdout.split()
        if ids:
            subprocess.run(
                ["docker", "kill", *ids],
                capture_output=True,
                check=False,
                timeout=60,
            )
    except (OSError, subprocess.TimeoutExpired):
        return


def run_setup(
    spec: RepoSpec,
    repo: Path,
    extra_path: list[Path],
    timeout: float = SETUP_TIMEOUT_S,
    *,
    confine: bool = False,
) -> None:
    """Run the build commands, then hide what they created from git.

    Parameters
    ----------
    spec : RepoSpec
        The build commands and environment.
    repo : Path
        The task repo.
    extra_path : list of Path
        Directories for ``PATH`` (the toolchain launchers).
    timeout : float, optional
        Seconds allowed for each command.
    confine : bool, optional
        Run each command behind the ``sandbox-exec`` profile, as the
        agent runs: writes only in the repo and the task's private home
        and temp directory, no clipboard, Apple Events or keychain, the
        workload files readable. Used for the eval user, where a build
        script must not be able to rewrite the staged harness.

    Raises
    ------
    SetupError
        If a command fails; the message holds its tail.
    """
    env = task_env(spec, repo, extra_path)
    for command in spec.setup:
        code, tail = _run(
            command,
            repo,
            env,
            timeout,
            confine=confine,
            read=[WORKLOADS],
        )
        if code != 0:
            msg = f"setup `{command[:80]}` exited {code}: {tail}"
            raise SetupError(msg)
        # A later step can use what an earlier one made (the venv).
        env = task_env(spec, repo, extra_path)
    status = _git(repo, "status", "--porcelain", "--untracked-files=normal")
    created = [
        ln[3:].rstrip("/")
        for ln in status.stdout.splitlines()
        if ln.startswith("?? ")
    ]
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    with exclude.open("a", encoding="utf-8") as sink:
        sink.write("".join(f"/{name}\n" for name in created))


def run_oracle(
    spec: RepoSpec,
    repo: Path,
    extra_path: list[Path],
    timeout: float = ORACLE_TIMEOUT_S,
) -> tuple[bool, str]:
    """Run the project's tests in the repo as it is checked out.

    Parameters
    ----------
    spec : RepoSpec
        The test command and environment.
    repo : Path
        The task repo, with the tree to test checked out.
    extra_path : list of Path
        Directories for ``PATH``.
    timeout : float, optional
        Seconds allowed.

    Returns
    -------
    tuple of (bool, str)
        Whether the command exited 0, and the tail of its output.
    """
    if spec.oracle is None:
        return False, "the manifest has no oracle command"
    code, tail = _run(
        spec.oracle,
        repo,
        task_env(spec, repo, extra_path),
        timeout,
        confine=True,
    )
    return code == 0, tail


def missing_requirements(real: RealRepo) -> list[str]:
    """List what a task needs that this machine lacks.

    Parameters
    ----------
    real : RealRepo
        The task's repo settings.

    Returns
    -------
    list of str
        Names from ``requires`` that are neither on ``PATH`` nor
        provided by the task's launcher, plus ``docker`` when a
        launcher needs it and the daemon does not answer. Empty when
        the task can run.
    """
    if real.shim is not None and sandbox.as_eval_user():
        # Not a probe of the daemon: the eval user has no Docker and the
        # owner's sockets are closed to it, by design.
        return [NEEDS_DOCKER]
    provided = _PROVIDED.get(real.shim or "", ())
    missing = [
        r for r in real.requires if r not in provided and not shutil.which(r)
    ]
    if real.shim is not None:
        try:
            up = (
                subprocess.run(
                    ["docker", "info"],
                    capture_output=True,
                    check=False,
                    timeout=60,
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            up = False
        if not up:
            missing.append("docker (daemon not answering)")
    return missing


def write_shims(bindir: Path, shim: str) -> None:
    """Write launchers that run a toolchain in its container.

    Parameters
    ----------
    bindir : Path
        Directory to write the executable scripts to.
    shim : str
        ``"go"`` or ``"ruby"``. Each launcher mounts the enclosing git
        work tree at ``/src`` and starts in the same relative directory,
        so it works in perfproof's own worktrees too.
    """
    bindir.mkdir(parents=True, exist_ok=True)
    body = (
        "#!/bin/sh\n"
        "top=$(git rev-parse --show-toplevel 2>/dev/null || pwd)\n"
        'rel=${PWD#"$top"}\n'
        f"exec {_SHIM_TEMPLATES[shim]}\n"
    )
    for name in _PROVIDED[shim]:
        launcher = bindir / name
        launcher.write_text(body, encoding="utf-8")
        launcher.chmod(0o755)
