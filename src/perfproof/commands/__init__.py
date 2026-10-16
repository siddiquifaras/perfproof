"""The ``perfproof`` subcommands, one module per command.

`perfproof.cli` finds a command by its name: ``perfproof <name>``
imports ``perfproof.commands.<module>``, where ``<module>`` is the name
with ``-`` spelled ``_`` (``install-skills`` is ``install_skills``).
Adding a command is adding its module here; nothing else is edited.
Modules whose name starts with ``_`` are not commands.

Each command module defines two functions:

``add_arguments(parser: argparse.ArgumentParser) -> None``
    Add the command's own options and set its description. ``--json``
    is added by `perfproof.cli` for every command.
``run(args: argparse.Namespace) -> Output``
    Do the work and return its result. Raise `CommandError` for any
    expected failure. A command never prints: `perfproof.cli` renders
    the `Output` as text or, with ``--json``, as one JSON document.
"""

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["CommandError", "JsonValue", "Output"]

type JsonValue = (
    bool
    | int
    | float
    | str
    | list[JsonValue]
    | tuple[JsonValue, ...]
    | Mapping[str, JsonValue]
    | None
)
"""A value `json.dumps` writes as it is; NaN and infinity excluded."""


@dataclass(frozen=True)
class Output:
    """What a command returns on success.

    Attributes
    ----------
    result : Mapping of str to JsonValue
        The ``result`` member of the ``--json`` document. Its keys are
        the command's stable interface for agents: a key is never
        renamed or given another meaning, only added.
    text : str
        The same result for a person to read, without a trailing
        newline.
    """

    result: Mapping[str, JsonValue]
    text: str


class CommandError(Exception):
    """An expected failure of a command; it exits with status 1.

    Parameters
    ----------
    code : str
        Stable snake_case identifier of the failure, such as
        ``invalid_spec``; agents branch on it.
    message : str
        What went wrong and, where it helps, what to do about it.
    details : Mapping of str to JsonValue, optional
        Structured context for the ``--json`` document, such as the
        path that was refused.
    """

    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: Mapping[str, JsonValue] = (
            {} if details is None else details
        )
