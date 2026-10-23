"""Run the skill eval as a separate macOS user.

The ``sandbox-exec`` profile (``sandbox.py``) works by listing what an
agent may touch, and review kept finding same-user leaks in that list.
A second macOS account closes by construction what the list closes by
enumeration: the agent runs as ``ppeval``, who cannot read the owner's
files, keychain, process environments or sockets. The profile stays on
top of that, because ``sudo -u`` keeps the process in the owner's GUI
session, where the clipboard, Finder (AppleScript) and ``open`` still
answer; only the profile's ``(deny mach-lookup)`` closes those.

Layout. The harness is split in two, along the user boundary:

* the *host* (this module, run by the owner) builds a wheel of this
  checkout, stages a bundle of the harness, the task manifests and the
  skill into the eval user's home, installs the wheel there with
  ``uv tool``, starts one guest process per task, and finally copies the
  scanned results back;
* the *guest* is the ordinary ``skill_eval.py run``, started as the eval
  user from that bundle. It seeds the task repositories in the eval
  user's home, runs the setup builds and the oracle, starts each agent
  behind the profile, redacts and scans the results with the eval
  user's token (which only it can read), and archives them.

Nothing the owner owns is readable to the guest, so the eval session
never needs the owner's checkout. Whatever the guest produces is data to
the host: it is extracted with a filter that admits only regular files
and directories, and nothing in it is ever executed.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Generator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import IO, Any, cast

import sandbox

EVAL_USER = "ppeval"
"""Name of the account the eval runs as."""
MARKER = "eval-user.json"
"""File in a pulled run directory that says where it came from."""
MAX_PULL_BYTES = 1 << 30
"""Most the host extracts from the eval user's archive."""
_CHECK_TIMEOUT = 30.0
_STAGE_TIMEOUT = 600.0
_INSTALL_TIMEOUT = 1800.0
_PULL_TIMEOUT = 900.0
_TERMINATE_WAIT = 120.0
MAX_PULL_MEMBERS = 200_000
"""Most entries the host extracts from the eval user's archive."""
_PYTHON = "3.12"
_BUNDLE_SKIP = ("__pycache__", ".pytest_cache", ".ruff_cache", ".DS_Store")
# Sets PATH and the toolchain variables from what the eval user has
# installed (its provisioning is in docs/evaluation.md), then runs the
# command. The globs are expanded by the eval user's own shell, which
# can read its home; the owner's cannot.
_LAUNCHER = r"""
set -eu
cd "$HOME"
jdk=
for d in "$HOME"/.local/jdk/*/Contents/Home; do
  if [ -d "$d" ]; then jdk=$d; fi
done
node=
for d in "$HOME"/.nvm/versions/node/*/bin; do
  if [ -d "$d" ]; then node=$d; fi
done
p="$HOME/.local/bin:$HOME/.cargo/bin"
if [ -n "$jdk" ]; then p="$p:$jdk/bin"; JAVA_HOME=$jdk; export JAVA_HOME; fi
if [ -n "$node" ]; then p="$p:$node"; fi
PATH="$p:/usr/bin:/bin:/usr/sbin:/sbin"
export PATH
exec "$@"
"""
_REMOVE = r"""
set -eu
for d in "$@"; do rm -rf -- "$d"; done
"""
# Runs as the eval user from the host's command line, so it is not code
# the guest could have changed. Hashes every entry under the bundle
# (files, directories, links, anything else) the way ``_digest`` does on
# the host: sorted by name; each entry is its kind, its length-prefixed
# name, its permission bits (0 for a link), and a length-prefixed
# payload (a file's bytes, a link's target, nothing for a directory).
_DIGEST = r"""
import hashlib, os, stat, sys
root = sys.argv[1]
entries = []
def walk(directory, prefix):
    with os.scandir(directory) as it:
        for e in it:
            rel = prefix + e.name
            m = e.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(m):
                entries.append((rel, "l", 0, os.readlink(e.path).encode()))
            elif stat.S_ISDIR(m):
                entries.append((rel, "d", stat.S_IMODE(m), b""))
                walk(e.path, rel + "/")
            elif stat.S_ISREG(m):
                with open(e.path, "rb") as f:
                    entries.append((rel, "f", stat.S_IMODE(m), f.read()))
            else:
                entries.append((rel, "x", stat.S_IMODE(m), b""))
walk(root, "")
h = hashlib.sha256()
for name, kind, mode, payload in sorted(entries, key=lambda t: t[0].encode()):
    n = name.encode()
    h.update(kind.encode())
    h.update(len(n).to_bytes(8, "big"))
    h.update(n)
    h.update(mode.to_bytes(4, "big"))
    h.update(len(payload).to_bytes(8, "big"))
    h.update(payload)
sys.stdout.write(h.hexdigest())
"""
_EXTRACT = r"""
set -eu
rm -rf -- "$1"
mkdir -p -- "$1"
exec tar -x -C "$1" -f -
"""


_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def check_label(label: str) -> str:
    """Refuse a run label that is not one plain directory name.

    Raises
    ------
    ValueError
        If ``label`` could name a path outside its directory.
    """
    if not _LABEL.fullmatch(label) or ".." in label:
        msg = f"run label {label!r}: use letters, digits, '.', '_' and '-'"
        raise ValueError(msg)
    return label


class EvalUserUnavailableError(RuntimeError):
    """The eval user is missing or cannot be used from this account."""


class EvalUserBusyError(EvalUserUnavailableError):
    """Another eval already holds the eval user."""


class BundleTamperedError(RuntimeError):
    """The staged harness is not what the host put there."""


class PullRefusedError(RuntimeError):
    """What the eval user sent back is not a plain tree of files."""


@dataclass(frozen=True)
class EvalUser:
    """The account the agents run as and where its files are.

    Attributes
    ----------
    name : str
        The account name.
    uid : int
        Its user id.
    home : Path
        Its home directory.
    """

    name: str
    uid: int
    home: Path

    @property
    def work(self) -> Path:
        """Root of everything the eval creates under the home."""
        return self.home / "eval-work"

    @property
    def bundle(self) -> Path:
        """The staged harness, manifests and skill."""
        return self.work / "bundle"

    @property
    def runs(self) -> Path:
        """Run directories, one per label."""
        return self.work / "runs"

    @property
    def archives(self) -> Path:
        """Scanned copies of runs: all the host reads back."""
        return self.work / "archive"

    @property
    def tool_venv(self) -> Path:
        """Where ``uv tool install`` puts ``perfproof``."""
        return self.home / ".local" / "share" / "uv" / "tools" / "perfproof"

    @property
    def token_file(self) -> Path:
        """The eval user's Claude token (mode 0600); never read here."""
        return self.home / ".config" / "perfproof-eval" / "token"


_held = False


@contextlib.contextmanager
def exclusive(path: Path | None = None) -> Generator[None]:
    """Hold the eval user for one eval at a time.

    ``sweep`` kills every process of the eval user, so a second eval
    starting while one runs would kill the first one's guest. The lock
    is a file in the owner's cache directory (``flock``, released when
    this process ends however it ends).

    Parameters
    ----------
    path : Path, optional
        The lock file; ``~/.cache/perfproof-eval/eval-user.lock``.

    Raises
    ------
    EvalUserBusyError
        If another process holds it (or this one already does).
    """
    global _held
    lock = path or Path.home() / ".cache" / "perfproof-eval" / "eval-user.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            msg = (
                f"another eval holds the eval user ({lock}); wait for it "
                "to finish"
            )
            raise EvalUserBusyError(msg) from exc
        _held = True
        try:
            yield
        finally:
            _held = False
            fcntl.flock(handle, fcntl.LOCK_UN)


def lookup(name: str = EVAL_USER) -> EvalUser:
    """Find the eval user and refuse to run as it.

    Parameters
    ----------
    name : str, optional
        The account name.

    Returns
    -------
    EvalUser
        Its uid and home.

    Raises
    ------
    EvalUserUnavailableError
        If the account does not exist, or is the account running this
        process (no separation then).
    """
    try:
        entry = pwd.getpwnam(name)
    except KeyError as exc:
        msg = (
            f"the macOS user {name!r} does not exist on this machine; "
            "create it, or pass --no-eval-user to run the agents as "
            "yourself behind the sandbox profile only"
        )
        raise EvalUserUnavailableError(msg) from exc
    if entry.pw_uid == os.getuid():
        msg = f"this process already runs as {name!r}: no separation"
        raise EvalUserUnavailableError(msg)
    return EvalUser(name, entry.pw_uid, Path(entry.pw_dir))


def command(
    user: EvalUser,
    argv: Sequence[str | Path],
    env: Mapping[str, str] | None = None,
) -> list[str]:
    """Build the command that runs ``argv`` as the eval user.

    The environment is rebuilt from nothing (``env -i``), because
    ``sudo`` passes the owner's ``PATH`` through, and the launcher sets
    ``PATH`` from the eval user's own toolchains. Only names and paths
    travel on the command line (visible to every user through ``ps``);
    the eval user's token never does.

    Parameters
    ----------
    user : EvalUser
        The account.
    argv : sequence of str or Path
        The command to run.
    env : mapping of str to str, optional
        Variables to set besides ``HOME``, ``USER``, ``LOGNAME``,
        ``LANG`` and ``TERM``.

    Returns
    -------
    list of str
        ``sudo -n -u <user> -H /usr/bin/env -i ... /bin/sh -c <launcher>
        sh <argv...>``. ``-n`` makes ``sudo`` fail instead of asking for
        a password.
    """
    base = {
        "HOME": str(user.home),
        "USER": user.name,
        "LOGNAME": user.name,
        "LANG": "en_US.UTF-8",
        "TERM": "dumb",
        **(env or {}),
    }
    return [
        "sudo",
        "-n",
        "-u",
        user.name,
        "-H",
        "/usr/bin/env",
        "-i",
        *[f"{k}={v}" for k, v in base.items()],
        "/bin/sh",
        "-c",
        _LAUNCHER,
        "sh",
        *[str(a) for a in argv],
    ]


def run(
    user: EvalUser,
    argv: Sequence[str | Path],
    *,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run a command as the eval user and capture its output.

    Parameters
    ----------
    user : EvalUser
        The account.
    argv : sequence of str or Path
        The command.
    env : mapping of str to str, optional
        Extra variables, see ``command``.
    stdin : bytes, optional
        Bytes for the command's standard input.
    timeout : float, optional
        Seconds before it is killed.

    Returns
    -------
    subprocess.CompletedProcess
        The result; the return code is not checked. The working
        directory is ``/``, because the owner's current directory is
        unreadable to the eval user.
    """
    return subprocess.run(
        command(user, argv, env),
        cwd="/",
        input=stdin,
        stdin=None if stdin is not None else subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def check(user: EvalUser) -> None:
    """Show that this account can act as the eval user.

    Raises
    ------
    EvalUserUnavailableError
        If ``sudo -n -u`` does not run a command as the eval user (no
        sudo rule, or a password is wanted), or runs it as someone else.
    """
    try:
        done = run(user, ["/usr/bin/id", "-u"], timeout=_CHECK_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = f"cannot run commands as {user.name!r}: {exc}"
        raise EvalUserUnavailableError(msg) from exc
    if done.returncode != 0 or done.stdout.strip() != str(user.uid).encode():
        detail = done.stderr.decode(errors="replace").strip()[-200:]
        msg = (
            f"`sudo -n -u {user.name}` failed (is a passwordless sudo rule "
            f"for it in place?): {detail}; pass --no-eval-user to run "
            "without it"
        )
        raise EvalUserUnavailableError(msg)


def require(name: str = EVAL_USER) -> EvalUser:
    """Find the eval user and check that it works.

    Returns
    -------
    EvalUser
        The usable account.

    Raises
    ------
    EvalUserUnavailableError
        If it is missing or unusable; the message says what to do. There
        is no automatic fallback: running without the user is a flag.
    """
    user = lookup(name)
    check(user)
    return user


def build_wheel(checkout: Path, out_dir: Path) -> Path:
    """Build a wheel of this checkout.

    Parameters
    ----------
    checkout : Path
        The perfproof checkout (the directory with ``pyproject.toml``).
    out_dir : Path
        Directory to write the wheel to; created.

    Returns
    -------
    Path
        The wheel file.

    Raises
    ------
    RuntimeError
        If ``uv build`` fails or does not produce exactly one wheel.
    """
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    done = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out_dir), str(checkout)],
        capture_output=True,
        text=True,
        check=False,
        timeout=_STAGE_TIMEOUT,
    )
    wheels = sorted(out_dir.glob("*.whl"))
    if done.returncode != 0 or len(wheels) != 1:
        msg = f"uv build failed: {(done.stdout + done.stderr)[-400:]}"
        raise RuntimeError(msg)
    return wheels[0]


def _tracked(directory: Path, patterns: Iterable[str]) -> list[Path]:
    return [
        path
        for pattern in patterns
        for path in sorted(directory.glob(pattern))
        if path.is_file()
        and not any(part in _BUNDLE_SKIP for part in path.parts)
    ]


def bundle_files(checkout: Path, skill_dir: Path) -> dict[str, Path]:
    """List what the guest needs, under the names it finds it by.

    Parameters
    ----------
    checkout : Path
        The perfproof checkout.
    skill_dir : Path
        The skill to evaluate (``--skill``).

    Returns
    -------
    dict of str to Path
        Path inside the bundle to the file in the checkout. The bundle
        mirrors the checkout's layout, so the harness finds its
        manifests and examples by the same relative paths: the harness
        modules without their tests, the task files, the mined
        manifests and ``checks.json``, ``examples/`` and the skill.
    """
    files: dict[str, Path] = {}
    skill = checkout / "eval" / "skill"
    for path in _tracked(skill, ["*.py", "*.json"]):
        if not path.name.startswith("test_"):
            files[path.relative_to(checkout).as_posix()] = path
    for path in _tracked(checkout / "eval" / "tasks", ["**/*"]):
        files[path.relative_to(checkout).as_posix()] = path
    checks = checkout / "eval" / "mine" / "checks.json"
    files[checks.relative_to(checkout).as_posix()] = checks
    for path in _tracked(checkout / "examples", ["**/*"]):
        files[path.relative_to(checkout).as_posix()] = path
    for path in _tracked(skill_dir, ["**/*"]):
        files[f"skills/perfproof/{path.relative_to(skill_dir).as_posix()}"] = (
            path
        )
    return files


def _file_mode(path: Path) -> int:
    return 0o755 if path.stat().st_mode & 0o100 else 0o644


def _directories(files: Mapping[str, Path]) -> list[str]:
    found = {
        parent.as_posix()
        for name in files
        for parent in PurePosixPath(name).parents
        if parent.as_posix() != "."
    }
    return sorted(found)


def _tar_bytes(files: Mapping[str, Path]) -> bytes:
    """Pack files into an archive with neutral ownership and modes.

    Directories are explicit members with mode 0755, so the tree the
    eval user extracts has the modes ``_digest`` expects.
    """
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name in _directories(files):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
        for name, path in sorted(files.items()):
            info = tar.gettarinfo(str(path), arcname=name)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = _file_mode(path)
            with path.open("rb") as handle:
                tar.addfile(info, handle)
    return buffer.getvalue()


@dataclass(frozen=True)
class Staged:
    """What was put in the eval user's home.

    Attributes
    ----------
    wheel : str
        File name of the installed wheel.
    wheel_sha256 : str
        Its SHA-256.
    bundle_sha256 : str
        SHA-256 over the bundle's names and bytes.
    perfproof : str
        Where the guest's ``perfproof`` package was imported from.
    """

    wheel: str
    wheel_sha256: str
    bundle_sha256: str
    perfproof: str


def _digest(files: Mapping[str, Path]) -> str:
    """Hash the tree ``_tar_bytes`` extracts to, as ``_DIGEST`` does."""
    entries: list[tuple[str, str, int, bytes]] = [
        (d, "d", 0o755, b"") for d in _directories(files)
    ]
    entries += [
        (name, "f", _file_mode(path), path.read_bytes())
        for name, path in files.items()
    ]
    digest = hashlib.sha256()
    for name, kind, mode, payload in sorted(
        entries, key=lambda e: e[0].encode()
    ):
        raw = name.encode()
        digest.update(kind.encode())
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        digest.update(mode.to_bytes(4, "big"))
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def stage(
    user: EvalUser,
    checkout: Path,
    skill_dir: Path,
    tasks_file: Path,
    scratch: Path,
) -> Staged:
    """Install perfproof for the eval user and stage the harness.

    The wheel is built from ``checkout``, copied into the bundle (owned
    by the eval user) through ``tar`` on standard input, and installed
    there with ``uv tool install`` as the eval user, so the agent runs
    the same package a user would get from the wheel and needs nothing
    from the owner's checkout. Any earlier bundle is removed first.

    Parameters
    ----------
    user : EvalUser
        The account.
    checkout : Path
        The perfproof checkout to build and bundle.
    skill_dir : Path
        The skill to install into each task repository.
    tasks_file : Path
        The task set; staged as ``tasks-file.json``.
    scratch : Path
        Directory for the built wheel.

    Returns
    -------
    Staged
        Hashes of what was staged and where ``perfproof`` imports from.

    Raises
    ------
    RuntimeError
        If staging or the install fails.
    """
    wheel = build_wheel(checkout, scratch / "wheel")
    files = bundle_files(checkout, skill_dir)
    files[f"dist/{wheel.name}"] = wheel
    files["tasks-file.json"] = tasks_file
    done = run(
        user,
        ["/bin/sh", "-c", _EXTRACT, "sh", user.bundle],
        stdin=_tar_bytes(files),
        timeout=_STAGE_TIMEOUT,
    )
    if done.returncode != 0:
        msg = f"staging the bundle failed: {done.stderr.decode()[-300:]}"
        raise RuntimeError(msg)
    installed = run(
        user,
        [
            "uv",
            "tool",
            "install",
            "--force",
            "--reinstall",
            "--python",
            _PYTHON,
            "--with",
            "pytest",
            user.bundle / "dist" / wheel.name,
        ],
        timeout=_INSTALL_TIMEOUT,
    )
    if installed.returncode != 0:
        tail = (installed.stdout + installed.stderr).decode()[-400:]
        msg = f"uv tool install failed: {tail}"
        raise RuntimeError(msg)
    where = run(
        user,
        [
            user.tool_venv / "bin" / "python",
            "-c",
            "import perfproof; print(perfproof.__file__)",
        ],
        timeout=_CHECK_TIMEOUT,
    )
    if where.returncode != 0:
        msg = "the installed perfproof does not import"
        raise RuntimeError(msg)
    return Staged(
        wheel=wheel.name,
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
        bundle_sha256=_digest(files),
        perfproof=where.stdout.decode().strip(),
    )


def verify_bundle(user: EvalUser, staged: Staged) -> None:
    """Check the staged harness against the hash taken at staging.

    The harness that redacts and scans runs from the eval user's home.
    The agent and the setup builds are confined and cannot write there,
    but the scan is what the owner trusts, so the host checks, by a
    script on its own command line, before each guest and after it,
    that every file (and no extra file, link or bytecode) is as staged.

    Raises
    ------
    BundleTamperedError
        If the digest differs or cannot be computed.
    """
    done = run(
        user,
        [
            user.home / ".local" / "bin" / f"python{_PYTHON}",
            "-B",
            "-I",
            "-c",
            _DIGEST,
            user.bundle,
        ],
        timeout=_CHECK_TIMEOUT,
    )
    got = done.stdout.decode().strip()
    if done.returncode != 0 or got != staged.bundle_sha256:
        msg = (
            "the staged bundle in the eval user's home changed since it "
            f"was staged ({got[:12] or 'unreadable'} != "
            f"{staged.bundle_sha256[:12]}); not trusting its scan"
        )
        raise BundleTamperedError(msg)


def guest_env(user: EvalUser) -> dict[str, str]:
    """Variables the guest harness needs, none of them secret.

    Returns
    -------
    dict of str to str
        The path of the token file (the guest reads the token itself)
        and of the Python environment that holds ``perfproof``.
    """
    return {
        sandbox.TOKEN_FILE_ENV: str(user.token_file),
        "PERFPROOF_EVAL_VENV": str(user.tool_venv),
        # No bytecode in the bundle: the digest covers every file in it.
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def guest_argv(
    user: EvalUser,
    *,
    label: str,
    only: Sequence[str],
) -> list[str | Path]:
    """Build the command line of one guest ``run``.

    Parameters
    ----------
    user : EvalUser
        The account.
    label : str
        The run id.
    only : sequence of str
        Task ids the guest runs (one per call, so the host can honour
        the pause marker between tasks).

    Returns
    -------
    list of str or Path
        The guest's own ``skill_eval.py run``, from the bundle, with
        every path under the eval user's home. ``--no-eval-user`` here
        means "do not start another user": this process already is one.
        ``--resume`` keeps tasks finished by earlier calls of the same
        label. ``--sign-in-checked``: the host probed the sign-in once
        for the whole run, so the guest does not probe again.
    """
    return [
        user.tool_venv / "bin" / "python",
        user.bundle / "eval" / "skill" / "skill_eval.py",
        "--tasks-file",
        user.bundle / "tasks-file.json",
        "run",
        "--no-eval-user",
        "--label",
        label,
        "--resume",
        "--sign-in-checked",
        "--out",
        user.runs,
        "--work-root",
        user.work / "work",
        "--cache",
        user.work / "cache",
        "--skill",
        user.bundle / "skills" / "perfproof",
        "--archive-to",
        user.archives / label,
        "--only",
        *only,
    ]


def remove(user: EvalUser, *paths: Path) -> None:
    """Remove paths under the eval user's work directory, as that user.

    Raises
    ------
    RuntimeError
        If a path is outside ``EvalUser.work`` (a bug, not an input) or
        the removal fails.
    """
    for path in paths:
        if user.work not in path.parents or ".." in path.parts:
            msg = f"refusing to remove {path}: not under {user.work}"
            raise RuntimeError(msg)
    done = run(
        user,
        ["/bin/sh", "-c", _REMOVE, "sh", *paths],
        timeout=_PULL_TIMEOUT,
    )
    if done.returncode != 0:
        msg = f"could not remove {list(paths)}: {done.stderr.decode()[-300:]}"
        raise RuntimeError(msg)


def reset(user: EvalUser, label: str) -> None:
    """Remove the eval user's directories for a run label."""
    remove(
        user,
        user.runs / label,
        user.work / "work" / label,
        user.archives / label,
    )


def run_guest(
    user: EvalUser, argv: Sequence[str | Path]
) -> subprocess.CompletedProcess[bytes]:
    """Run a guest command with its output going to this terminal.

    Parameters
    ----------
    user : EvalUser
        The account.
    argv : sequence of str or Path
        From ``guest_argv`` or similar.

    Returns
    -------
    subprocess.CompletedProcess
        The result; the return code is the guest's.

    Notes
    -----
    If this process is interrupted, ``sudo`` is sent SIGTERM, which it
    relays to the guest; the guest turns that into the unwinding that
    kills a running agent's process group. A plain kill of ``sudo``
    (what ``subprocess.run`` does) would leave the guest running as the
    eval user, which this owner cannot signal.
    """
    proc = subprocess.Popen(
        command(user, argv, guest_env(user)),
        cwd="/",
        stdin=subprocess.DEVNULL,
    )
    try:
        code = proc.wait()
    except BaseException:
        proc.terminate()
        try:
            proc.wait(timeout=_TERMINATE_WAIT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        sweep(user)
        raise
    sweep(user)
    return subprocess.CompletedProcess(proc.args, code)


def sweep(user: EvalUser) -> None:
    """Kill every process of the eval user.

    ``ppeval`` exists only for the evals and the host runs one eval at a
    time, so nothing of its own is worth keeping. The caller must
    hold ``exclusive``: a second eval would otherwise be swept. This is
    what stops a
    guest, or an agent's background process, that survived the death of
    ``sudo`` (the host cannot signal the eval user's processes itself)
    or that ignores SIGTERM. SIGKILL, because the point is processes
    that do not stop when asked. ``pkill`` exits 1 when there was
    nothing to kill, which is fine.
    """
    if not _held:
        msg = "refusing to sweep the eval user without holding the lock"
        raise RuntimeError(msg)
    run(
        user,
        ["/usr/bin/pkill", "-KILL", "-U", str(user.uid)],
        timeout=_CHECK_TIMEOUT,
    )


def _safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts


def extract_safely(
    stream: IO[bytes],
    dest: Path,
    limit: int = MAX_PULL_BYTES,
    max_members: int = MAX_PULL_MEMBERS,
) -> int:
    """Extract a tar stream that came from the eval user.

    Parameters
    ----------
    stream : file object
        The archive, read sequentially.
    dest : Path
        Directory to extract into; created.
    limit : int, optional
        Most bytes of file content to accept.
    max_members : int, optional
        Most archive entries to accept.

    Returns
    -------
    int
        Number of files written.

    Raises
    ------
    PullRefusedError
        If a member is anything but a regular file or a directory (a
        link or device would let the eval user aim a later write at the
        owner's files), has an absolute or ``..`` name, or the total
        exceeds ``limit``. Members already written stay.
    """
    dest.mkdir(parents=True, exist_ok=True)
    total = 0
    count = 0
    seen = 0
    with tarfile.open(fileobj=stream, mode="r|") as tar:
        for member in tar:
            seen += 1
            if seen > max_members:
                msg = f"refusing more than {max_members} entries"
                raise PullRefusedError(msg)
            if not (member.isreg() or member.isdir()):
                msg = f"refusing {member.name!r}: not a plain file"
                raise PullRefusedError(msg)
            if not _safe_name(member.name):
                msg = f"refusing {member.name!r}: path escapes the target"
                raise PullRefusedError(msg)
            total += member.size
            if total > limit:
                msg = f"refusing more than {limit} bytes from the eval user"
                raise PullRefusedError(msg)
            tar.extract(member, dest, filter="data")
            count += member.isreg()
    return count


def pull(user: EvalUser, remote: Path, dest: Path) -> int:
    """Copy a directory of the eval user's out, as data.

    Parameters
    ----------
    user : EvalUser
        The account.
    remote : Path
        A directory under the eval user's home (an archive that the
        guest scanned for secrets).
    dest : Path
        Directory on this side.

    Returns
    -------
    int
        Number of files copied.

    Raises
    ------
    PullRefusedError
        If the archive holds anything but plain files, or the copy
        fails.
    """
    # stderr goes to a file, not a pipe: a full pipe would block the
    # tar while this side is still reading its stdout.
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(
            command(
                user,
                ["/usr/bin/tar", "-c", "-C", str(remote), "."],
                {"COPYFILE_DISABLE": "1"},
            ),
            cwd="/",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=err,
        )
        assert proc.stdout is not None  # noqa: S101
        timer = threading.Timer(_PULL_TIMEOUT, proc.kill)
        timer.start()
        try:
            count = extract_safely(proc.stdout, dest)
        except tarfile.TarError as exc:
            proc.kill()
            msg = f"copying {remote}: not a readable archive ({exc})"
            raise PullRefusedError(msg) from exc
        except BaseException:
            proc.kill()
            raise
        finally:
            timer.cancel()
            proc.stdout.close()
            proc.wait()
        if proc.returncode != 0:
            err.seek(0)
            tail = err.read()[-300:].decode(errors="replace")
            msg = f"copying {remote} failed: {tail}"
            raise PullRefusedError(msg)
    return count


def _read_marker(run_dir: Path) -> dict[str, Any] | None:
    try:
        data: Any = json.loads((run_dir / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cast("dict[str, Any]", data) if isinstance(data, dict) else None


def write_marker(
    dest: Path, user: EvalUser, staged: Staged, labels: Mapping[str, object]
) -> None:
    """Record in a pulled run where it came from.

    Parameters
    ----------
    dest : Path
        The pulled run directory.
    user : EvalUser
        The account.
    staged : Staged
        What was installed.
    labels : mapping of str to object
        More facts to record (the guest's exit codes).
    """
    earlier: list[dict[str, Any]] = []
    previous = _read_marker(dest)
    if previous is not None:
        earlier = [*previous.get("stagings", []), previous]
        earlier = [
            {k: v for k, v in e.items() if k != "stagings"} for e in earlier
        ]
    record = {
        "user": user.name,
        "uid": user.uid,
        "pulled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "wheel": staged.wheel,
        "wheel_sha256": staged.wheel_sha256,
        "bundle_sha256": staged.bundle_sha256,
        "perfproof": staged.perfproof,
        "redaction_and_scan": "run by the eval user before the copy",
        # A resumed run is staged again; tasks finished earlier ran with
        # the earlier wheel and skill, so every staging is kept.
        "stagings": earlier,
        **labels,
    }
    (dest / MARKER).write_text(
        json.dumps(record, indent=2) + "\n", encoding="utf-8"
    )
