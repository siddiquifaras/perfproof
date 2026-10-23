"""The environment and confinement of an eval agent.

The agent has unrestricted ``Bash``. Three layers keep it from acting as
the owner or reading the answer:

1. **Environment** (``agent_env``): an allowlist of variable names, so
   an unknown variable is dropped; ``gh`` replaced by a stub and given a
   sentinel token (``gh auth token`` finds the owner's keychain login
   even with an empty config directory); git told, through
   ``GIT_CONFIG_COUNT``, to use no credential helper, no fsmonitor, no
   hooks and no ssh. Those settings are command-line scope, so a repo's
   own ``.git/config`` cannot override them.
2. **Confinement** (``wrap``): on macOS, ``sandbox-exec`` with a profile
   that denies reading the owner's credential files and the eval's own
   task files, and executing ``security`` and the real ``gh`` (and
   ``docker`` for tasks that do not need it).
3. **Scoring** (``scoring.py``): any call that touches the task files,
   the reference change or the network is flagged.

The limits that remain are written in ``docs/evaluation.md``.
"""

from __future__ import annotations

import base64
import functools
import json
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from xml.sax.saxutils import escape

TOKEN_FILE_ENV = "PERFPROOF_EVAL_TOKEN_FILE"  # noqa: S105
"""Names the file with the eval user's sign-in (a path, not a secret).

Set only in the eval user's own harness process; ``agent_env`` drops it.
"""
SENTINEL = "perfproof-eval-denied"
"""``GH_TOKEN`` value: never valid; keeps ``gh`` off the keychain."""
# Variables the ``claude`` CLI is known to need for its own sign-in.
ALLOWED_SECRETS = frozenset(
    {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"}
)
# Everything else is dropped. Names only: no value is ever inspected.
_ALLOWED_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TMPDIR",
        "LANG",
        "TERM",
        "TZ",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_TMPDIR",
        "JAVA_HOME",
        "GOPATH",
        "GOROOT",
        "GOTOOLCHAIN",
        "CARGO_HOME",
        "RUSTUP_HOME",
        "RUSTUP_TOOLCHAIN",
        "SDKROOT",
        "DEVELOPER_DIR",
        "MACOSX_DEPLOYMENT_TARGET",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        "__CF_USER_TEXT_ENCODING",
    }
)
_ALLOWED_PREFIXES = ("LC_", "UV_", "NVM_", "PYENV_")
_GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "dev",
    "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "dev",
    "GIT_COMMITTER_EMAIL": "dev@example.com",
}
# Command-line-scope git settings, applied through GIT_CONFIG_COUNT.
_GIT_SETTINGS = (
    ("credential.helper", ""),
    ("core.fsmonitor", "false"),
    ("core.hooksPath", os.devnull),
    ("core.sshCommand", "false"),
)
_STUB = "#!/bin/sh\necho 'gh is not available in eval runs' >&2\nexit 1\n"
# The owner's home is unreadable and unwritable to the agent except for
# the paths below, which hold toolchains and their caches. Anything not
# listed (``~/.ssh``, ``~/.claude``, ``~/.config``, shell history and
# rc files, ``~/Library``, other projects) stays closed.
_HOME_READ = (
    ".local/bin",
    ".local/share/uv",
    ".local/share/claude",
    ".local/jdk",
    ".local/go",
    ".local/dotnet",
    ".local/ruby",
    ".cargo/bin",
    ".rustup",
    ".nvm",
)
# Shared caches are not writable: an unconfined build or the owner's own
# work would read what the agent planted. Each task gets private ones
# (``private_env``).
_HOME_WRITE: tuple[str, ...] = ()
# Inside an allowed directory, still closed.
_HOME_CARVED = (
    ".cargo/credentials",
    ".cargo/credentials.toml",
    ".cargo/config.toml",
    ".cargo/config",
)
# No shared temp directory is readable or writable: ``TMPDIR`` points
# into the task's private directory, and a hard-coded ``/tmp/x`` fails.
_TEMP_DIRS: tuple[str, ...] = ()
_SHARED_TEMP = ("/private/tmp", "/private/var/tmp", "/private/var/folders")
# The one unix socket every task may use: name resolution.
_ALLOWED_SOCKETS = ("/private/var/run/mDNSResponder",)
_CLAUDE_TMP = r"^/private/tmp/claude-[0-9]+"
_DOCKER_SOCKETS = (
    "/var/run/docker.sock",
    ".docker/run/docker.sock",
)
# Mach (XPC) services are an allowlist, like files: a service the agent
# can look up is a service it can use as the owner (the pasteboard,
# which often holds a token; LaunchServices; AppleEvents; the keychain's
# securityd; a password manager's helper). Only the names below, each
# one needed by a command the tasks run, are allowed; see
# ``docs/evaluation.md`` for what each is for.
_MACH_ALLOWED: tuple[str, ...] = (
    # macOS ``sample`` (the skill uses it for native programs that
    # perfproof cannot profile) asks this service to symbolicate.
    "com.apple.coresymbolicationd",
    # getpwuid and friends: the user name and home of a uid, which
    # ``id -un`` and some build tools look up. Read-only, no secrets.
    "com.apple.system.opendirectoryd.libinfo",
)
_MACH_ALLOWED_PREFIXES: tuple[str, ...] = ()
# Never allowed, whatever else is: the keychain.
_KEYCHAIN_SERVICES = (
    "com.apple.SecurityServer",
    "com.apple.securityd",
    "com.apple.security.agent",
)
_NEVER_EXEC = ("/usr/bin/security",)


class SandboxUnavailableError(RuntimeError):
    """Confinement was required and this machine cannot provide it."""


def _allowed(name: str) -> bool:
    return (
        name in ALLOWED_SECRETS
        or name in _ALLOWED_NAMES
        or name.startswith(_ALLOWED_PREFIXES)
    )


TAMPERED: list[str] = []
"""What the harness had to remove from a task's private directory."""


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path, ignore_errors=True)


def _ensure_dir(path: Path) -> None:
    """Make ``path`` a real directory, removing what is in the way."""
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        TAMPERED.append(f"replaced {path} (was not a directory)")
        _remove(path)
    path.mkdir(parents=True, exist_ok=True)


def _parents(home: Path, rel: str) -> None:
    """Make every parent of ``home/rel`` a real directory."""
    here = home
    for part in Path(rel).parts[:-1]:
        here = here / part
        if here.is_symlink() or (here.exists() and not here.is_dir()):
            TAMPERED.append(f"replaced {here} (was not a directory)")
            _remove(here)
    (home / rel).parent.mkdir(parents=True, exist_ok=True)


def _link(home: Path, rel: str, target: Path) -> None:
    """Make ``home/rel`` a link to ``target``, whatever is in the way.

    The agent can write in its private home, so a directory or file may
    sit where the link belongs, or in place of one of its parents. That
    must neither crash the harness nor be left standing: it is removed,
    inside the private home only, and recorded in ``TAMPERED``.
    """
    link = home / rel
    _parents(home, rel)
    if link.is_symlink():
        if link.readlink() == target:
            return
        TAMPERED.append(f"replaced {link} (pointed elsewhere)")
        link.unlink()
    elif link.exists():
        TAMPERED.append(f"replaced {link} (was not our link)")
        _remove(link)
    try:
        link.symlink_to(target)
    except OSError as exc:
        TAMPERED.append(f"could not link {link}: {exc}")


# A JVM takes ``user.home`` from the account database, not from
# ``$HOME``, so Maven would use the account's real ``~/.m2`` (closed to
# the agent, or the eval user's shared one) however private ``HOME``
# is. ``mvn`` sources ``$HOME/.mavenrc`` before it starts the JVM, and
# ``perfproof`` passes ``HOME`` to every command it runs, so this file
# reaches the benchmarks too, which an environment variable would not.
_MAVENRC = 'MAVEN_OPTS="${MAVEN_OPTS:-} -Duser.home=${HOME}"\n'


def _write_private(home: Path, rel: str, text: str) -> None:
    """Write ``home/rel`` as a plain file, whatever is in the way.

    The agent can leave a link or a directory where the file belongs;
    writing through a link would aim the harness's write at a file the
    agent cannot reach itself. The last component is opened with
    ``O_NOFOLLOW | O_EXCL``; the profile (``guarded``) keeps the agent
    from swapping a parent. Anything unexpected is recorded in
    ``TAMPERED`` and skipped: the harness never crashes on it.
    """
    path = home / rel
    try:
        _parents(home, rel)
        if path.is_symlink() or path.exists():
            if not path.is_symlink() and path.is_file():
                if path.read_text(encoding="utf-8") == text:
                    return
            else:
                TAMPERED.append(f"replaced {path} (was not a plain file)")
            _remove(path)
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as sink:
            sink.write(text)
    except (OSError, UnicodeDecodeError) as exc:
        TAMPERED.append(f"could not write {path}: {exc}")


_NUGET_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="perfproof-eval" value="{folder}" />
  </packageSources>
</configuration>
"""


def _nuget_feed(home: Path, real: Path) -> None:
    """Point a private home's NuGet at the eval user's package feed.

    A confined ``dotnet restore`` could not reach nuget.org (NuGet:
    ``bad certificate format``; the cause is inferred to be the
    Security framework's Mach services, which the profile closes). The
    NuGet packages of the
    C# tasks are therefore put in ``~/.local/dotnet/nuget-feed``
    when the eval user is provisioned
    (``.claude/local/eval-user-setup.md``), which is also a valid local
    feed (NuGet's global-packages layout), and the private home's only
    package source is that folder. A local feed, unlike a fallback
    folder, also answers floating versions (``8.0.*``). The folder is
    read-only to the agent. Nothing is written when it does not exist,
    so ``--no-eval-user`` keeps NuGet's defaults.

    Parameters
    ----------
    home : Path
        The task's private home.
    real : Path
        The real home, whose ``.local/dotnet/nuget-feed`` is used.
    """
    folder = real / ".local" / "dotnet" / "nuget-feed"
    if not folder.is_dir():
        return
    _write_private(
        home,
        ".nuget/NuGet/NuGet.Config",
        _NUGET_CONFIG.format(folder=escape(str(folder), {'"': "&quot;"})),
    )


def private_env(base: Path) -> dict[str, str]:
    """Give a task its own home directory and temp directory.

    ``perfproof`` hands its benchmark and profiler commands only a short
    list of variables (``HOME`` and ``TMPDIR`` among them), so cache
    variables such as ``UV_CACHE_DIR`` would not reach a ``uv`` or
    ``cargo`` it starts. A private ``HOME`` does: every tool's default
    cache (``~/.cache/uv``, ``~/.cargo``, ``~/go``) then lands in it.

    Parameters
    ----------
    base : Path
        Directory to hold them; created.

    Returns
    -------
    dict of str to str
        ``HOME``, ``TMPDIR`` and zsh's ``TMPPREFIX`` inside ``base``,
        and ``RUSTUP_HOME`` at the real toolchains. The private home
        links the real ``~/.rustup`` (read-only to an agent) so rustup
        still finds its toolchains. ``uv`` finds the installed
        interpreters through ``PATH`` (``~/.local/bin`` links) and
        downloads a missing one into the private home's own
        ``~/.local/share/uv/python``, which the harness never touches.
        Downloads happen per task; the owner's own caches are never
        written by an agent. ``home``, ``.rustup`` and ``.mavenrc`` are
        the paths the unconfined harness manages: the profile
        (``guarded``) stops the agent renaming or removing them or
        their parent.
    """
    tmp = base / "tmp"
    home = base / "home"
    _ensure_dir(tmp)
    _ensure_dir(home)
    real = Path.home()
    if (real / ".rustup").exists():
        _link(home, ".rustup", real / ".rustup")
    _write_private(home, ".mavenrc", _MAVENRC)
    _nuget_feed(home, real)
    # zsh writes heredocs under TMPPREFIX (default /tmp/zsh), not in
    # TMPDIR.
    env = {
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "TMPPREFIX": str(tmp / "zsh"),
    }
    if (real / ".rustup").is_dir():
        env["RUSTUP_HOME"] = str(real / ".rustup")
    return env


@functools.lru_cache(maxsize=1)
def toolchain_env() -> dict[str, str]:
    """Name the Apple developer directory and SDK explicitly.

    With neither set, the ``/usr/bin`` shims (``clang``, ``git``) call
    ``xcrun``, which writes a cache file into the per-user temp
    directory (found by ``confstr``, ignoring ``TMPDIR``). That
    directory is closed to the agent, and the file would be shared with
    the owner's own builds, so the agent gets the answers instead.

    Returns
    -------
    dict of str to str
        ``DEVELOPER_DIR`` and ``SDKROOT``, or nothing where
        ``xcode-select`` and ``xcrun`` are absent.
    """
    found: dict[str, str] = {}
    for key, argv in (
        ("DEVELOPER_DIR", ["xcode-select", "-p"]),
        ("SDKROOT", ["xcrun", "--sdk", "macosx", "--show-sdk-path"]),
    ):
        try:
            done = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0 and done.stdout.strip():
            found[key] = done.stdout.strip()
    return found


_BYPASS_TOOLS = (
    "ar",
    "as",
    "c++",
    "cc",
    "clang",
    "clang++",
    "cpp",
    "dsymutil",
    "g++",
    "gcc",
    "git",
    "gnumake",
    "install_name_tool",
    "ld",
    "libtool",
    "lipo",
    "make",
    "nm",
    "otool",
    "ranlib",
    "strip",
)


def _tool_bin(directory: Path, developer: str | None) -> Path | None:
    """Link the compiler and linker tools that skip ``xcrun``.

    Putting the whole ``$DEVELOPER_DIR/usr/bin`` on ``PATH`` would also
    shadow ``python3`` and ``pip3`` (the Command Line Tools' own Python)
    over the project's. So only the tools named in ``_BYPASS_TOOLS`` are
    linked, into a directory of their own. A linked ``git`` looks for
    its helpers (``git-remote-https``, ``git-upload-pack``) under the
    Xcode path compiled into it, absent on a Command Line Tools install,
    so `agent_env` points ``GIT_EXEC_PATH`` at the developer directory's
    own; the ``/usr/bin/git`` shim would instead try to write the xcrun
    cache in the closed temp directory on every call.

    Parameters
    ----------
    directory : Path
        Where to put the links; created.
    developer : str or None
        ``DEVELOPER_DIR``.

    Returns
    -------
    Path or None
        The directory, or ``None`` when there is no developer directory.
    """
    if not developer or not (Path(developer) / "usr" / "bin").is_dir():
        return None
    directory.mkdir(parents=True, exist_ok=True)
    for name in _BYPASS_TOOLS:
        real = Path(developer) / "usr" / "bin" / name
        link = directory / name
        if real.exists() and not link.is_symlink():
            link.symlink_to(real)
    return directory


def agent_env(
    base: Mapping[str, str], scratch: Path, extra_path: list[Path]
) -> dict[str, str]:
    """Build the environment for an agent call or a build step.

    Parameters
    ----------
    base : mapping of str to str
        The environment to start from, normally ``os.environ``.
    scratch : Path
        Directory that holds the empty config directories and the
        ``gh`` stub; created if absent.
    extra_path : list of Path
        Directories to put on ``PATH``, before the stub and the rest.

    Returns
    -------
    dict of str to str
        Only the allowlisted names of ``base``, with ``gh`` and git
        neutered, git identity set by environment, and ``PATH`` led by
        ``extra_path`` and a ``gh`` stub that fails.
    """
    env = {k: v for k, v in base.items() if _allowed(k)}
    gh_dir = scratch / "gh"
    xdg = scratch / "xdg"
    stub = scratch / "denied-bin"
    templates = scratch / "git-templates"
    for d in (gh_dir, xdg, stub, templates):
        d.mkdir(parents=True, exist_ok=True)
    launcher = stub / "gh"
    launcher.write_text(_STUB, encoding="utf-8")
    launcher.chmod(0o755)
    env.update(
        {
            "GH_CONFIG_DIR": str(gh_dir),
            "XDG_CONFIG_HOME": str(xdg),
            "GH_TOKEN": SENTINEL,
            "GH_PROMPT_DISABLED": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_COUNT": str(len(_GIT_SETTINGS)),
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "never",
            "GIT_SSH_COMMAND": "false",
            "GIT_TEMPLATE_DIR": str(templates),
            **_GIT_IDENTITY,
        }
    )
    for n, (key, value) in enumerate(_GIT_SETTINGS):
        env[f"GIT_CONFIG_KEY_{n}"] = key
        env[f"GIT_CONFIG_VALUE_{n}"] = value
    env.update(private_env(scratch / "private"))
    env.update(toolchain_env())
    dirs = [*(str(p) for p in extra_path), str(stub)]
    toolbin = _tool_bin(scratch / "toolbin", env.get("DEVELOPER_DIR"))
    if toolbin is not None:
        dirs.append(str(toolbin))
        git_core = Path(env["DEVELOPER_DIR"], "usr", "libexec", "git-core")
        if git_core.is_dir():
            env["GIT_EXEC_PATH"] = str(git_core)
    env["PATH"] = os.pathsep.join([*dirs, base.get("PATH", os.defpath)])
    return env


_CREDENTIAL_LIKE = re.compile(
    r"TOKEN|SECRET|PASS|CREDENTIAL|KEY|AUTH(?!OR)|NETRC|DATABASE_URL|_URL$",
    re.IGNORECASE,
)


def suspicious(env: Mapping[str, str]) -> list[str]:
    """Name variables of ``env`` that look like credentials.

    Parameters
    ----------
    env : mapping of str to str
        An environment.

    Returns
    -------
    list of str
        Sorted names that match a broad credential pattern and are
        neither allowlisted for the ``claude`` sign-in, nor the sentinel
        ``GH_TOKEN``, nor the git settings this module sets itself.
    """
    return sorted(
        k
        for k, v in env.items()
        if _CREDENTIAL_LIKE.search(k)
        and k not in ALLOWED_SECRETS
        and k != "ANTHROPIC_BASE_URL"
        and not k.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
        and not (k == "GH_TOKEN" and v == SENTINEL)
    )


def as_eval_user(environ: Mapping[str, str] | None = None) -> bool:
    """Tell whether this process is the eval user's guest harness.

    Returns
    -------
    bool
        True when ``PERFPROOF_EVAL_TOKEN_FILE`` is set, which only the
        host's launcher does, and only for the guest.
    """
    return bool(
        (os.environ if environ is None else environ).get(TOKEN_FILE_ENV)
    )


def _token_from_file(path: Path) -> str:
    """Read the eval user's sign-in from its token file.

    The harness runs as the eval user, so the file (mode 0600, owned by
    that user) is read inside the process that needs it. The value is
    never put on a command line or written anywhere.

    Raises
    ------
    SandboxUnavailableError
        If the file is missing, unreadable or empty.
    """
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        msg = f"cannot read the eval user's token file {path}: {exc.strerror}"
        raise SandboxUnavailableError(msg) from exc
    if not token:
        msg = f"the eval user's token file {path} is empty"
        raise SandboxUnavailableError(msg)
    return token


TOKEN_MIN_REMAINING_S = 1800.0
"""Least time an access token must have left when a task starts.

The longest real-repository task wall time in round 7's three runs was
1009 s (``real-xarray-hidden-fix``, r7-2: setup plus an 805 s agent
turn; from the runs' ``meta.json`` on branch ``task/t3.8-skill-7``), so
thirty minutes covers what was seen. It does not cover a task that runs
to its own timeout (1800 to 3000 s in ``tasks-real.json``): the agent
holds a fixed token and cannot renew it, so such a task can end with an
authentication error. It replaces a fixed hour, which stopped round 7's
third run while the token was still good. The CLI seemed to renew only
when about to expire (one observation, not recorded: a one-turn
``claude -p`` with 44 minutes left changed nothing), so an hour's margin
is a window of an hour in every eight in which no run can start.
"""


def _read_oauth(
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    """Read the ``claudeAiOauth`` keychain record, or ``{}``."""
    data: Any = None
    try:
        got = run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            stdin=subprocess.DEVNULL,
            check=False,
        )
        if got.returncode == 0:
            data = json.loads(got.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        data = None
    if isinstance(data, dict):
        found: Any = cast("dict[str, Any]", data).get("claudeAiOauth")
        if isinstance(found, dict):
            return cast("dict[str, Any]", found)
    return {}


def refresh_sign_in(
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Let the ``claude`` CLI renew its token, outside the sandbox.

    One cheap turn (``haiku``, at most five cents) with no project
    settings, no MCP servers, no tools, from a neutral directory. The
    CLI renews and stores its
    token itself when the token is about to expire; the harness never
    sees the refresh token's value. Failures are ignored: the caller
    reads the expiry again and decides.

    Parameters
    ----------
    run : callable, optional
        ``subprocess.run``, replaceable in tests.
    """
    try:
        run(
            [
                "claude",
                "-p",
                "reply with the single word ok",
                "--model",
                "haiku",
                "--max-budget-usd",
                "0.05",
                # No MCP servers, tools or settings: the owner's own
                # would start (and run) with this turn. Variadic flags
                # are each followed by another flag.
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--tools",
                "",
                "--setting-sources",
                "",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            stdin=subprocess.DEVNULL,
            check=False,
            cwd=Path.home(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return


def claude_oauth_token(
    environ: Mapping[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: float | None = None,
    *,
    min_remaining: float = TOKEN_MIN_REMAINING_S,
    refresh: Callable[[], None] | None = None,
) -> str | None:
    """Fetch the ``claude`` access token the confined agent will use.

    The agent cannot reach the keychain (``security`` and the keychain
    files are denied), and ``claude`` signs in through it. So the
    harness, outside the sandbox, reads the access token once and hands
    it to the agent as ``CLAUDE_CODE_OAUTH_TOKEN``. That is a narrower
    grant than the keychain: one short-lived token that only does what
    the agent's own sign-in does. The refresh token never leaves the
    harness. The value is only ever held in memory; callers must
    redact it from anything they write.

    Parameters
    ----------
    environ : mapping of str to str, optional
        Where to look for a token already provided; ``os.environ``.
    run : callable, optional
        ``subprocess.run``, replaceable in tests.
    now : float, optional
        Seconds since the epoch, replaceable in tests.
    min_remaining : float, optional
        Seconds the token must have left; ``TOKEN_MIN_REMAINING_S``.
    refresh : callable, optional
        Called once, with no arguments, when the token has less than
        ``min_remaining`` left, before the keychain is read again
        (``refresh_sign_in``). Only an expiry time is ever looked at.

    Returns
    -------
    str or None
        ``None`` when the environment already carries a sign-in
        (``ALLOWED_SECRETS``); the contents of the file named by
        ``PERFPROOF_EVAL_TOKEN_FILE`` when that is set; otherwise the
        keychain's access token.

    Raises
    ------
    SandboxUnavailableError
        If the token file is missing or empty, or the keychain has no
        usable sign-in, or its access token has less than
        ``min_remaining`` left even after ``refresh`` (keychain only).
    """
    env = os.environ if environ is None else environ
    if any(env.get(name) for name in ALLOWED_SECRETS):
        return None
    if env.get(TOKEN_FILE_ENV):
        # The eval user token: long-lived, no keychain, no expiry check,
        # no renewing turn.
        return _token_from_file(Path(env[TOKEN_FILE_ENV]))
    oauth = _read_oauth(run)
    left = _seconds_left(oauth, now)
    if left is not None and left < min_remaining and refresh is not None:
        refresh()
        oauth = _read_oauth(run)
        left = _seconds_left(oauth, now)
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        msg = "no claude sign-in found in the keychain; run `claude` once"
        raise SandboxUnavailableError(msg)
    if left is not None and left < min_remaining:
        msg = (
            f"the claude access token expires in {left / 60:.0f} minutes "
            f"and a task needs {min_remaining / 60:.0f}: the CLI renews it "
            "only when it is about to expire, so wait and retry, or sign "
            "in again with `claude auth login`"
        )
        raise SandboxUnavailableError(msg)
    return token


def _seconds_left(oauth: Mapping[str, Any], now: float | None) -> float | None:
    """Seconds until the access token expires; ``None`` if unknown."""
    expires = oauth.get("expiresAt")
    if not isinstance(expires, int | float):
        return None
    clock = time.time() if now is None else now
    return expires / 1000 - clock


def env_secrets(env: Mapping[str, str]) -> list[str]:
    """List the sign-in values present in an environment.

    Parameters
    ----------
    env : mapping of str to str
        The environment handed to an agent.

    Returns
    -------
    list of str
        The non-empty values of ``ALLOWED_SECRETS``, whatever their
        source (the keychain handoff or the owner's own variables).
    """
    return [v for k in sorted(ALLOWED_SECRETS) if (v := env.get(k))]


def secret_forms(values: Iterable[str]) -> list[str]:
    """Spell out the ways a secret is likely to appear in a transcript.

    Parameters
    ----------
    values : iterable of str
        Secret values.

    Returns
    -------
    list of str
        Each value, reversed, as hex, and as standard and URL-safe
        base64 (with and without padding, and at the three byte
        alignments a value can take inside a longer encoded string).
        Anything cleverer than these gets through; the network is open
        anyway.
    """
    forms: list[str] = []
    for value in values:
        if not value:
            continue
        raw = value.encode()
        found = [
            value,
            value[::-1],
            raw.hex(),
            raw.hex().upper(),
            urllib.parse.quote(value, safe=""),
            urllib.parse.quote_plus(value),
        ]
        for shift in (0, 1, 2):
            padded = b"\0" * shift + raw
            for encode in (base64.b64encode, base64.urlsafe_b64encode):
                text = encode(padded).decode()
                text = text[(shift * 4 + 2) // 3 :] if shift else text
                found += [text, text.rstrip("="), text.rstrip("=")[:-2]]
        forms += [f for f in found if len(f) >= 8 and f not in forms]
    return forms


_BETWEEN = r"(?:\s|\\[nrt])*"


@functools.lru_cache(maxsize=8)
def _patterns(forms: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    # Whitespace, or its JSON escape, may sit between any two
    # characters: wrapped base64 and ``xxd -p`` output.
    return tuple(
        re.compile(_BETWEEN.join(re.escape(c) for c in form))
        for form in forms
        if form
    )


def redact(text: str, forms: Sequence[str]) -> str:
    """Replace every form of a secret in a text.

    Parameters
    ----------
    text : str
        A line, a file's text or a JSON event.
    forms : sequence of str
        From ``secret_forms``.

    Returns
    -------
    str
        ``text`` with each form, including when whitespace splits it
        (wrapped base64, ``xxd -p``), replaced by ``<redacted>``.
    """
    for pattern in _patterns(tuple(forms)):
        text = pattern.sub("<redacted>", text)
    return text


def holds_secret(text: str, forms: Sequence[str]) -> bool:
    """Tell whether any form of a secret appears in a text.

    Parameters
    ----------
    text : str
        The text to look in.
    forms : sequence of str
        From ``secret_forms``.

    Returns
    -------
    bool
        True if a form appears, whitespace splitting it included.
    """
    return any(p.search(text) for p in _patterns(tuple(forms)))


def perfproof_root(checkout: Path) -> Path:
    """Find the main perfproof checkout from a checkout or a worktree.

    Parameters
    ----------
    checkout : Path
        This checkout's root, possibly under ``.claude/worktrees``.

    Returns
    -------
    Path
        The root that holds ``.claude/worktrees``, or ``checkout``.
    """
    parts = checkout.parts
    for i in range(len(parts) - 1):
        if parts[i : i + 2] == (".claude", "worktrees"):
            return Path(*parts[:i])
    return checkout


def _both(paths: Sequence[Path]) -> list[Path]:
    found: list[Path] = []
    for p in paths:
        for candidate in (p, p.resolve()):
            if candidate not in found:
                found.append(candidate)
    return found


def guarded(write: Sequence[Path]) -> list[Path]:
    """List the paths the unconfined harness manages in a private home.

    ``private_env`` links, removes and writes ``home/.rustup`` and
    ``home/.mavenrc`` with the owner's (or the eval user's) full
    rights, in a directory the agent can write. A process of the agent
    that outlives its group could swap one of those, or ``home`` or
    ``private`` above them, for a link between the harness's check and
    its action. Denying the agent every write to them, and to the two
    directories above, closes that; the rest of the private home stays
    writable.

    Parameters
    ----------
    write : sequence of Path
        The writable paths; each one named ``private`` holds a ``home``.

    Returns
    -------
    list of Path
        The private directory, its ``home``, ``.rustup``,
        ``.mavenrc`` and the NuGet config (``.nuget``, ``.nuget/NuGet``
        and its ``NuGet.Config``, written when the feed exists).
    """
    found: list[Path] = []
    for path in write:
        if path.name == "private":
            home = path / "home"
            nuget = home / ".nuget" / "NuGet"
            found += [
                path,
                home,
                home / ".rustup",
                home / ".mavenrc",
                home / ".nuget",
                nuget,
                nuget / "NuGet.Config",
            ]
    return found


def _subpaths(verb: str, paths: Sequence[Path | str]) -> list[str]:
    return [f'({verb} (subpath "{p}"))' for p in paths]


def profile(
    home: Path,
    root: Path,
    allow_docker: bool,
    *,
    read: Sequence[Path] = (),
    write: Sequence[Path] = (),
    temp_dirs: Sequence[Path | str] | None = None,
) -> str:
    """Write the ``sandbox-exec`` profile text.

    The rules are an allowlist for the places the agent could read a
    secret or an answer, or move one: reads of the home and of the
    perfproof checkout are denied and a short list is allowed back, and
    writes are denied everywhere and a short list is allowed back. With
    writes confined, a denied directory cannot be renamed to a place
    the agent can read, which a read-only deny list allows.

    Parameters
    ----------
    home : Path
        The owner's home directory.
    root : Path
        The main perfproof checkout.
    allow_docker : bool
        Whether the task needs the Docker daemon.
    read : sequence of Path, optional
        Further paths that stay readable (the task tree, launchers, the
        perfproof environment).
    write : sequence of Path, optional
        Paths that stay writable besides the temp directories (the task
        tree, the agent's ``claude`` config directory). They are also
        readable.
    temp_dirs : sequence of Path or str, optional
        Writable temp directories; the system's by default.

    Returns
    -------
    str
        The profile. It also denies the keychain's Mach services, the
        unix sockets of the Docker daemon unless ``allow_docker``, and
        running ``security`` and the real ``gh`` (a copy of either is
        stopped by the read and socket rules, not by this one).
    """
    temps = list(_TEMP_DIRS if temp_dirs is None else temp_dirs)
    # The kernel matches the real path; a task dir reached through a
    # symlink (``/tmp`` is one) must be allowed under both spellings.
    read = _both(read)
    write = _both(write)
    lines = [
        "(version 1)",
        "(allow default)",
        f'(deny file-read* (subpath "{home}"))',
        f'(deny file-read* (subpath "{root}"))',
        f'(deny file-read* (regex #"{_CLAUDE_TMP}"))',
        # Shared temp and per-user cache directories: other programs'
        # files live there. Allowed back below only where a task's own
        # tree is.
        *[f'(deny file-read* (subpath "{t}"))' for t in _SHARED_TEMP],
        # Metadata only: programs stat every parent of their working
        # directory, and a failed stat stops them.
        *[f'(allow file-read-metadata (subpath "{t}"))' for t in _SHARED_TEMP],
        f'(allow file-read-metadata (subpath "{home}"))',
        f'(allow file-read-metadata (subpath "{root}"))',
    ]
    lines += _subpaths(
        "allow file-read*",
        [home / p for p in _HOME_READ] + list(read) + list(write),
    )
    lines += _subpaths("deny file-read*", [home / p for p in _HOME_CARVED])
    lines.append("(deny file-write*)")
    lines.append('(allow file-write* (subpath "/dev"))')
    lines += _subpaths(
        "allow file-write*",
        temps + [home / p for p in _HOME_WRITE] + list(write),
    )
    lines.append(f'(deny file-write* (regex #"{_CLAUDE_TMP}"))')
    # Last, so it wins over the allows above: what the harness manages.
    lines += [
        f'(deny file-write* (literal "{p}"))' for p in _both(guarded(write))
    ]
    # Other processes of the same user: their arguments and environment
    # (``sysctl`` KERN_PROCARGS2: the harness's own environment, a live
    # claude's tokens), their memory maps and samples. The rules stop a
    # reader in another session; the harness starts every confined
    # command in a new session of its own. The agent's own children
    # (its benchmark, ``sample`` of them) are not "others" to it.
    lines += [
        "(deny process-info* (target others))",
        '(deny sysctl-read (sysctl-name-prefix "kern.proc"))',
        "(deny mach-priv-task-port (target others))",
    ]
    lines.append("(deny mach-lookup)")
    allow = [f'(global-name "{n}")' for n in _MACH_ALLOWED] + [
        f'(global-name-prefix "{n}")' for n in _MACH_ALLOWED_PREFIXES
    ]
    if allow:
        lines.append(f"(allow mach-lookup {' '.join(allow)})")
    names = " ".join(f'(global-name "{n}")' for n in _KEYCHAIN_SERVICES)
    lines.append(f"(deny mach-lookup {names})")
    # Unix sockets: connect() ignores file-read denies, so a socket the
    # owner has open (ssh-agent, a live claude, a root service) is
    # reachable unless connections are denied here.
    lines.append("(deny network-outbound (remote unix-socket))")
    lines += [
        f'(allow network-outbound (remote unix-socket (path-literal "{p}")))'
        for p in _ALLOWED_SOCKETS
    ]
    lines += [
        f'(allow network-outbound (remote unix-socket (subpath "{p}")))'
        for p in write
    ]
    if allow_docker:
        sockets = [
            str(p) if p.startswith("/") else str(home / p)
            for p in _DOCKER_SOCKETS
        ]
        lines += [
            "(allow network-outbound (remote unix-socket "
            f'(path-literal "{p}")))'
            for p in sockets
        ]
    barred: list[str] = list(_NEVER_EXEC)
    found = shutil.which("gh", path="/usr/local/bin:/opt/homebrew/bin")
    if found:
        barred += [str(Path(found).resolve()), found]
    lines += [
        f'(deny process-exec (literal "{path}"))'
        for path in dict.fromkeys(barred)
    ]
    return "\n".join(lines) + "\n"


def confinement_available() -> bool:
    """Tell whether ``sandbox-exec`` can be used here."""
    return (
        platform.system() == "Darwin"
        and shutil.which("sandbox-exec") is not None
    )


def wrap(
    argv: list[str],
    scratch: Path,
    *,
    checkout: Path,
    allow_docker: bool,
    strict: bool = True,
    cwd: Path | None = None,
    read: Sequence[Path] = (),
    write: Sequence[Path] = (),
) -> list[str]:
    """Prefix a command with ``sandbox-exec``.

    Parameters
    ----------
    argv : list of str
        The command.
    scratch : Path
        Directory to write the profile into.
    checkout : Path
        This checkout, to find the perfproof root.
    allow_docker : bool
        Whether the command may run the Docker client.
    strict : bool, optional
        Raise when confinement is unavailable; otherwise return
        ``argv`` unchanged (for harness steps whose environment is
        already clean).
    cwd : Path, optional
        The directory the command runs in; it stays readable and
        writable.
    read, write : sequence of Path, optional
        More paths to leave readable, and readable and writable.

    Returns
    -------
    list of str
        ``sandbox-exec -f <profile> <argv...>``.

    Raises
    ------
    SandboxUnavailableError
        If ``strict`` and this machine has no ``sandbox-exec``.
    """
    if not confinement_available():
        if strict:
            msg = "sandbox-exec is needed to confine the agent"
            raise SandboxUnavailableError(msg)
        return argv
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / ("docker.sb" if allow_docker else "plain.sb")
    path.write_text(
        profile(
            Path.home(),
            perfproof_root(checkout),
            allow_docker,
            read=[*read, *([] if cwd is None else [cwd])],
            write=[*write, *([] if cwd is None else [cwd])],
        ),
        encoding="utf-8",
    )
    return ["sandbox-exec", "-f", str(path), *argv]
