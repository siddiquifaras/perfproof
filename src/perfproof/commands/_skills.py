"""Where the ``/perfproof`` skill comes from and where clients find it.

Shared by ``perfproof install-skills`` and ``perfproof doctor``. The
skill is one directory (``SKILL.md`` and its playbooks). A copy is
installed per client, with a marker file so a later install or
uninstall only ever touches a directory perfproof made.

Client locations, from each client's skill documentation (checked
2026-10-22):

* Claude Code: ``~/.claude/skills/perfproof/``
* Codex: ``~/.agents/skills/perfproof/``

The skill ships inside the wheel as data files (``pyproject.toml``,
``[tool.uv.build-backend.data]``), which the installer puts under the
``sysconfig`` data path. In a source checkout it is
``skills/perfproof``, which wins: an editable install also has a data
copy, frozen at the last build, that goes stale as the skill changes.
"""

import hashlib
import json
import shutil
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from perfproof import __version__

__all__ = [
    "CLIENTS",
    "MARKER",
    "SKILL_NAME",
    "SkillState",
    "check",
    "client_dir",
    "install",
    "skill_source",
    "uninstall",
]

SKILL_NAME: Final = "perfproof"
"""Directory name of the skill, and its name in both clients."""

MARKER: Final = ".perfproof-install.json"
"""File written into an installed copy; its presence means perfproof
owns the directory."""

CLIENTS: Final = ("claude", "codex")
"""Client names accepted by ``--client``."""

_ROOTS: Final[dict[str, tuple[str, ...]]] = {
    "claude": (".claude", "skills"),
    "codex": (".agents", "skills"),
}


@dataclass(frozen=True)
class SkillState:
    """What is installed for one client.

    Attributes
    ----------
    client : str
        ``claude`` or ``codex``.
    path : str
        The skill directory for this client.
    state : str
        ``missing``, ``current``, ``stale`` (perfproof's own copy that
        differs from this version's), or ``foreign`` (a directory
        perfproof did not create).
    """

    client: str
    path: str
    state: str


def skill_source() -> Path | None:
    """Find the skill directory shipped with this perfproof.

    Returns
    -------
    pathlib.Path or None
        The checkout's ``skills/perfproof`` when perfproof runs from a
        source checkout, else the wheel's data copy, else ``None`` when
        neither holds a ``SKILL.md``.
    """
    data = Path(sysconfig.get_path("data")) / SKILL_NAME
    checkout = Path(__file__).resolve().parents[3] / "skills" / SKILL_NAME
    for candidate in (checkout, data):
        if (candidate / "SKILL.md").is_file():
            return candidate
    return None


def client_dir(client: str) -> Path:
    """Return the directory a client reads the skill from.

    Parameters
    ----------
    client : str
        One of `CLIENTS`.

    Returns
    -------
    pathlib.Path
        ``<home>/<client skills root>/perfproof``, with the home of the
        current user (``HOME``).
    """
    return Path.home().joinpath(*_ROOTS[client], SKILL_NAME)


def _digest(root: Path) -> str:
    """Hash every file's relative path and bytes under ``root``."""
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name == MARKER:
            continue
        h.update(path.relative_to(root).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def check(client: str, source: Path | None) -> SkillState:
    """Report what is installed for ``client``, without changing it.

    Parameters
    ----------
    client : str
        One of `CLIENTS`.
    source : pathlib.Path or None
        The shipped skill directory to compare with, if found.

    Returns
    -------
    SkillState
        ``stale`` also when ``source`` is unknown.
    """
    target = client_dir(client)
    if not target.exists():
        return SkillState(client, str(target), "missing")
    if not (target / MARKER).is_file():
        return SkillState(client, str(target), "foreign")
    if source is not None and _digest(target) == _digest(source):
        return SkillState(client, str(target), "current")
    return SkillState(client, str(target), "stale")


def install(client: str, source: Path, *, force: bool) -> str:
    """Copy the skill into a client's directory.

    Parameters
    ----------
    client : str
        One of `CLIENTS`.
    source : pathlib.Path
        The skill directory to copy.
    force : bool
        Replace a directory perfproof did not create.

    Returns
    -------
    str
        ``installed``, ``updated`` or ``unchanged``.

    Raises
    ------
    FileExistsError
        The directory exists, was not made by perfproof and ``force``
        is false.
    OSError
        The copy failed.
    """
    target = client_dir(client)
    before = check(client, source).state
    if before == "current":
        return "unchanged"
    if before == "foreign" and not force:
        raise FileExistsError(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{SKILL_NAME}.installing"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        shutil.copytree(source, staging)
        (staging / MARKER).write_text(
            json.dumps({"perfproof_version": __version__}) + "\n",
            encoding="utf-8",
        )
        if target.exists():
            shutil.rmtree(target)
        staging.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return "installed" if before == "missing" else "updated"


def uninstall(client: str, *, force: bool) -> str:
    """Remove the skill from a client's directory.

    Parameters
    ----------
    client : str
        One of `CLIENTS`.
    force : bool
        Remove a directory perfproof did not create.

    Returns
    -------
    str
        ``removed`` or ``absent``.

    Raises
    ------
    FileExistsError
        The directory was not made by perfproof and ``force`` is false.
    """
    target = client_dir(client)
    if not target.exists():
        return "absent"
    if not (target / MARKER).is_file() and not force:
        raise FileExistsError(str(target))
    shutil.rmtree(target)
    return "removed"
