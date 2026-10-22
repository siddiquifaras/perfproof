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


def _link(home: Path, rel: str, target: Path) -> None:
    """Make ``home/rel`` a link to ``target``, whatever is in the way.

    The agent can write in its private home, so a directory or file may
    sit where the link belongs, or in place of one of its parents. That
    must neither crash the harness nor be left standing: it is removed,
    inside the private home only, and recorded in ``TAMPERED``.
    """
    link = home / rel
    here = home
    for part in Path(rel).parts[:-1]:
        here = here / part
        if here.is_symlink() or (here.exists() and not here.is_dir()):
            TAMPERED.append(f"replaced {here} (was not a directory)")
            _remove(here)
    link.parent.mkdir(parents=True, exist_ok=True)
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
        links the real ``~/.rustup`` and ``~/.local/share/uv/python``
        (read-only to an agent) so rustup and uv still find their
        toolchains. Downloads
        happen per task; the owner's own caches are never written by an
        agent.
    """
    tmp = base / "tmp"
    home = base / "home"
    _ensure_dir(tmp)
    _ensure_dir(home)
    real = Path.home()
    for rel in (".rustup", ".local/share/uv/python"):
        if (real / rel).exists():
            _link(home, rel, real / rel)
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
    linked, into a directory of their own.

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


def claude_oauth_token(
    environ: Mapping[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    now: float | None = None,
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

    Returns
    -------
    str or None
        ``None`` when the environment already carries a sign-in
        (``ALLOWED_SECRETS``); otherwise the keychain's access token.

    Raises
    ------
    SandboxUnavailableError
        If the keychain has no usable sign-in, or its access token
        expires within an hour (run ``claude`` once to refresh it).
    """
    env = os.environ if environ is None else environ
    if any(env.get(name) for name in ALLOWED_SECRETS):
        return None
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
    oauth: dict[str, Any] = {}
    if isinstance(data, dict):
        found: Any = cast("dict[str, Any]", data).get("claudeAiOauth")
        if isinstance(found, dict):
            oauth = cast("dict[str, Any]", found)
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        msg = "no claude sign-in found in the keychain; run `claude` once"
        raise SandboxUnavailableError(msg)
    expires = oauth.get("expiresAt")
    clock = time.time() if now is None else now
    if isinstance(expires, int | float) and expires / 1000 < clock + 3600:
        msg = "the claude access token expires within an hour; run `claude`"
        raise SandboxUnavailableError(msg)
    return token


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
