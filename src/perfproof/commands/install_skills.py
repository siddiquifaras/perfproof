"""``perfproof install-skills``: put ``/perfproof`` where agents look.

Copies the skill (``SKILL.md`` and its playbooks) into the personal
skills directory of Claude Code (``~/.claude/skills/perfproof``) and of
Codex (``~/.agents/skills/perfproof``). Nothing else is written: no
project file, no client settings. ``--uninstall`` removes the copies
this command made. A directory with that name that perfproof did not
create is left alone unless ``--force`` is given.

Options: ``--client {claude,codex,all}`` (default ``all``),
``--uninstall``, ``--force``.

``--json`` result
-----------------
``action``
    ``install`` or ``uninstall``.
``clients``
    List of ``{"client", "path", "outcome"}``; ``outcome`` is
    ``installed``, ``updated``, ``unchanged``, ``removed`` or
    ``absent``.

Error codes: ``skill_not_found`` (this perfproof has no skill files),
``skill_dir_not_ours`` (``details.path`` exists and perfproof did not
make it), ``install_failed`` (the copy or removal raised an OS error).
"""

import argparse

from perfproof.commands import CommandError, JsonValue, Output
from perfproof.commands import _skills as skills

__all__ = ["add_arguments", "run"]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Describe ``perfproof install-skills``.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Install the /perfproof skill for Claude Code and Codex, or "
        "remove it with --uninstall."
    )
    parser.add_argument(
        "--client",
        choices=(*skills.CLIENTS, "all"),
        default="all",
        help="which client to install for (default: all)",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the skill instead of installing it",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace or remove a skill directory perfproof did not make",
    )


def run(args: argparse.Namespace) -> Output:
    """Install or remove the skill for the chosen clients.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed options: ``client``, ``uninstall``, ``force``.

    Returns
    -------
    Output
        The result described in this module's docstring.

    Raises
    ------
    CommandError
        See the module docstring for the codes.
    """
    chosen = skills.CLIENTS if args.client == "all" else (args.client,)
    removing: bool = args.uninstall
    force: bool = args.force
    source = None if removing else skills.skill_source()
    if not removing and source is None:
        raise CommandError(
            "skill_not_found",
            "This perfproof has no skill files. Reinstall it: "
            "`uv tool install --reinstall <perfproof source>`.",
        )
    rows: list[JsonValue] = []
    lines: list[str] = []
    for client in chosen:
        path = str(skills.client_dir(client))
        try:
            outcome = (
                skills.install(client, source, force=force)
                if source is not None
                else skills.uninstall(client, force=force)
            )
        except FileExistsError as exc:
            raise CommandError(
                "skill_dir_not_ours",
                f"{path} exists and perfproof did not create it. "
                "Move it away, or pass --force to replace it.",
                {"path": path},
            ) from exc
        except OSError as exc:
            raise CommandError(
                "install_failed", f"{client}: {exc}", {"path": path}
            ) from exc
        rows.append({"client": client, "path": path, "outcome": outcome})
        lines.append(f"{client:<7} {outcome}: {path}")
    return Output(
        result={
            "action": "uninstall" if removing else "install",
            "clients": rows,
        },
        text="\n".join(lines),
    )
