"""The ``perfproof`` command line: registry, dispatch and output.

``perfproof <command> [options]`` imports only the module of the
command it names, from `perfproof.commands`, so starting the CLI costs
one import, and a new command is a new module with no other edit (see
`perfproof.commands` for what a module defines).

Output
------
Text goes to stdout, errors to stderr. With ``--json``, given before or
after the command name, stdout carries exactly one JSON document, for
errors too::

    {"command": "doctor", "ok": true, "result": {...}}
    {"command": "doctor", "ok": false,
     "error": {"code": "...", "message": "...", "details": {...}}}

``command`` is ``null`` when no command was named. Help is a result
too: ``{"command": ..., "ok": true, "result": {"help": "<text>"}}``.
Error codes that come from the CLI itself are ``usage`` (bad
arguments; exit 2), ``unknown_command`` (exit 2), ``not_built`` (exit
1) and ``internal_error`` (exit 3); each command documents its own.

An exception other than `perfproof.commands.CommandError`, raised while
loading, running or rendering a command, is a fault in perfproof, not a
failure the command reports. It becomes ``internal_error``, with the
exception's type and text in the message and its type in
``details.exception``, and its traceback goes to stderr. Nothing of the
command's own output is written first: a result is serialized in full
before any of it reaches stdout.

JSON is written with ``ensure_ascii``, so it is ASCII whatever the
locale, and a path that is not valid UTF-8 (a lone surrogate after
Python decodes it) is a JSON unicode escape that `json.loads` reads
back. Text that the output stream's encoding cannot represent is
written with backslash escapes instead (``backslashreplace``), never
as a raw byte and never as a crash.

If the reader closes stdout early (``perfproof doctor | head -1``),
perfproof stops writing, sends the rest of its output to the null
device and exits 141, as a shell reports a program killed by SIGPIPE.
Only stdout does this. When stderr is closed (``2>&-``), its reader
has gone, or writing to it fails for any other reason, what would go
there is dropped and the exit status alone reports the outcome, with
the command's own code.

Exit status
-----------
0
    The command succeeded.
1
    The command failed for a reason it reports (a `CommandError`), or
    it is listed in the help but not built yet.
2
    The arguments were wrong.
3
    perfproof itself failed (``internal_error``); the traceback is on
    stderr.
141
    stdout was closed before the output was written.
"""

import argparse
import importlib
import json
import os
import pkgutil
import sys
import textwrap
import traceback
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Final, NoReturn, TextIO, cast

from perfproof import __version__, commands
from perfproof.commands import CommandError, JsonValue, Output

__all__ = ["COMMANDS", "HELP", "available_commands", "main"]

HELP: Final = """\
perfproof: find where code can get faster, and keep only the changes
that really are.

usage: perfproof <command> [options]

Understand
  init             Create .perfproof/ and propose a project model from
                   the repository: target classes, candidate workloads,
                   build and run commands, each with its evidence
  project          Show the project model; propose, confirm or retire
                   entries; check which entries are stale

Discover
  profile          Run a profiler on a workload and summarise the top
                   frames with file:line and share of time
  measure          Measure one version: scaling runs (--sizes), a stage
                   with one function replayed from captured calls
                   (--stub), or one setting changed (--set)
  log              Record a hypothesis, its cheap test, its candidate
                   and its outcome; show the log for a workload

Prove
  bench            Define and lock a workload's benchmark spec
  check            Run the correctness gates only: tests, output
                   comparison, captured-call replay, coverage
  compare          Prove a candidate against a baseline: gates,
                   interleaved rounds, guardrails, verdict, record
  confirm          Choose the best of several candidates, then confirm
                   it with a fresh, independent compare
  status           State and progress of a detached compare
  cancel           Stop a detached compare and its whole process tree
  report           Render a run record as readable text

Helpers
  load             Drive an HTTP service at a constant request rate and
                   record latency percentiles and throughput
  replay           Record an app's model and tool calls, or replay them
  doctor           Check this machine, its noise sources and the tools
                   perfproof can use here
  install-skills   Install the /perfproof skill and the playbooks for
                   Claude Code or Codex

Common options
  --json           Machine-readable output, for agents
  --detach         Return at once; follow with `perfproof status`
  --resume RUN     Continue an interrupted compare from its last
                   completed round
  --host HOST      Run on ssh://user@host or modal:<image> instead of here

Every verdict is KEEP, REJECT or INCONCLUSIVE, with a reason. A gate
that isn't available on this target blocks KEEP; it never passes."""
"""The ``perfproof --help`` text, as ``docs/cli.md`` specifies it."""

COMMANDS: Final = (
    "init",
    "project",
    "profile",
    "measure",
    "log",
    "bench",
    "check",
    "compare",
    "confirm",
    "status",
    "cancel",
    "report",
    "load",
    "replay",
    "doctor",
    "install-skills",
)
"""Every command `HELP` lists, in its order. A listed command without a
module in `perfproof.commands` exits with ``not_built``."""

_USAGE_STATUS: Final = 2
_INTERNAL_STATUS: Final = 3
_BROKEN_PIPE_STATUS: Final = 141

type _AddArguments = Callable[[argparse.ArgumentParser], None]
type _Run = Callable[[argparse.Namespace], Output]


class _UsageError(Exception):
    """Arguments argparse refused."""


class _HelpRequestedError(Exception):
    """``--help`` was given; carries the help text of its parser."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


class _Parser(argparse.ArgumentParser):
    """An `argparse.ArgumentParser` that raises instead of exiting.

    Raising lets `main` return the status, and report the error or the
    help as JSON under ``--json``.
    """

    def error(self, message: str) -> NoReturn:
        """Raise the usage error argparse found."""
        raise _UsageError(message)

    def print_help(self, file: object = None) -> NoReturn:
        """Raise the help text instead of printing it."""
        raise _HelpRequestedError(self.format_help().rstrip("\n"))


def _module_name(command: str) -> str:
    """Return the module name of a command name."""
    return command.replace("-", "_")


def available_commands() -> tuple[str, ...]:
    """List the commands that have a module, sorted.

    Returns
    -------
    tuple of str
        Command names (``-`` where the module has ``_``) of every
        public module in `perfproof.commands`. Finding them lists the
        package's directories; no module is imported.
    """
    names = (
        info.name.replace("_", "-")
        for info in pkgutil.iter_modules(commands.__path__)
        if not info.name.startswith("_")
    )
    return tuple(sorted(names))


def _load(command: str) -> tuple[_AddArguments, _Run] | None:
    """Import a command's module and return its two functions.

    Returns ``None`` when no module has the command's name.

    Raises
    ------
    TypeError
        If the module lacks ``add_arguments`` or ``run``.
    """
    if command not in available_commands():
        return None
    name = f"{commands.__name__}.{_module_name(command)}"
    module: ModuleType = importlib.import_module(name)
    add_arguments: object = getattr(module, "add_arguments", None)
    run: object = getattr(module, "run", None)
    if not callable(add_arguments) or not callable(run):
        msg = f"{name} must define add_arguments(parser) and run(args)"
        raise TypeError(msg)
    return cast("_AddArguments", add_arguments), cast("_Run", run)


def _json_default(value: object) -> object:
    """Let `json.dumps` write any mapping as an object."""
    if isinstance(value, Mapping):
        return dict(cast("Mapping[str, object]", value))
    msg = f"{type(value).__name__} is not JSON serializable"
    raise TypeError(msg)


def _dumps(document: Mapping[str, JsonValue]) -> str:
    """Serialize one JSON document."""
    return json.dumps(
        document,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
        allow_nan=False,
        default=_json_default,
    )


class _StdoutClosedError(Exception):
    """The reader of stdout went away before the output was written."""


def _write(text: str, stream: TextIO | None) -> None:
    """Write a line, escaping what the stream's encoding cannot hold.

    Nothing is written when ``stream`` is ``None`` (the descriptor was
    closed before Python started). If writing to stderr fails, stderr
    is set to ``None`` so nothing else, including the interpreter's
    final flush, tries it again. Under some emulators a closed fd 2 is
    reused before Python starts, so stderr exists but every write fails
    with EBADF; this covers that case too.

    Raises
    ------
    _StdoutClosedError
        If ``stream`` is stdout and its reader has closed it.
    OSError
        If writing to stdout fails for another reason.
    """
    if stream is None:
        return
    encoding = stream.encoding or "utf-8"
    safe = text.encode(encoding, "backslashreplace").decode(encoding)
    try:
        stream.write(safe + "\n")
        if stream is not sys.stdout:
            stream.flush()
    except OSError as exc:
        if stream is not sys.stdout:
            if stream is sys.stderr:
                sys.stderr = None
            return
        if isinstance(exc, BrokenPipeError):
            _discard(stream)
            raise _StdoutClosedError from exc
        raise


def _emit_json(document: Mapping[str, JsonValue]) -> None:
    """Write one JSON document to stdout."""
    _write(_dumps(document), sys.stdout)


def _show_help(command: str | None, text: str, *, as_json: bool) -> int:
    """Write a help text, as a JSON result under ``--json``."""
    if as_json:
        _emit_json({"command": command, "ok": True, "result": {"help": text}})
    else:
        _write(text, sys.stdout)
    return 0


def _internal_error(
    command: str | None, exc: Exception, *, as_json: bool
) -> int:
    """Report a fault in perfproof itself, with its traceback."""
    _write("".join(traceback.format_exception(exc)).rstrip("\n"), sys.stderr)
    return _fail(
        command,
        "internal_error",
        f"perfproof failed: {type(exc).__name__}: {exc}",
        {"exception": type(exc).__name__},
        as_json=as_json,
        status=_INTERNAL_STATUS,
    )


def _fail(
    command: str | None,
    code: str,
    message: str,
    details: Mapping[str, JsonValue],
    *,
    as_json: bool,
    status: int,
) -> int:
    """Report a failure and return its exit status."""
    if as_json:
        _emit_json(
            {
                "command": command,
                "ok": False,
                "error": {
                    "code": code,
                    "message": message,
                    "details": details,
                },
            }
        )
    else:
        prog = "perfproof" if command is None else f"perfproof {command}"
        _write(f"{prog}: error: {message}", sys.stderr)
    return status


def _top_parser() -> _Parser:
    """Build the parser for what comes before the command's options."""
    parser = _Parser(prog="perfproof", add_help=False)
    parser.add_argument("-h", "--help", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("command", nargs="?")
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    return parser


def _not_built_footer() -> str:
    """List the commands in the help that have no module yet."""
    built = set(available_commands())
    missing = [name for name in COMMANDS if name not in built]
    if not missing:
        return ""
    line = f"Not built yet in perfproof {__version__}: {', '.join(missing)}."
    return "\n\n" + textwrap.fill(line, width=72, break_on_hyphens=False)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``perfproof`` command line.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments after the program name; ``sys.argv[1:]`` when
        ``None``.

    Returns
    -------
    int
        Exit status: 0 on success, 1 when the command failed or is not
        built yet, 2 for wrong arguments, 3 when perfproof itself
        failed, 141 when stdout was closed early.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        status = _main(args)
    except _StdoutClosedError:
        return _BROKEN_PIPE_STATUS
    if sys.stdout is not None:
        try:
            sys.stdout.flush()
        except BrokenPipeError:
            _discard(sys.stdout)
            return _BROKEN_PIPE_STATUS
    return status


def _discard(stream: TextIO) -> None:
    """Point a stream's descriptor at the null device.

    Used once the stream's reader has gone; without it, the
    interpreter's final flush fails again at exit and reports a second
    error.
    """
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, stream.fileno())
    except (OSError, ValueError):
        pass
    finally:
        os.close(devnull)


def _main(args: list[str]) -> int:
    """Run the command line; `main` adds the closed-stdout handling."""
    try:
        top = _top_parser().parse_args(args)
    except _UsageError as exc:
        return _fail(
            None,
            "usage",
            str(exc),
            {},
            as_json="--json" in args,
            status=_USAGE_STATUS,
        )
    as_json = bool(top.json)
    command: str | None = top.command
    rest: list[str] = top.rest
    if command is None:
        if top.help:
            return _show_help(
                None, HELP + _not_built_footer(), as_json=as_json
            )
        return _fail(
            None,
            "usage",
            "name a command; see perfproof --help",
            {},
            as_json=as_json,
            status=_USAGE_STATUS,
        )
    if top.help:
        rest = ["--help"]
    as_json = as_json or "--json" in rest
    try:
        return _dispatch(command, rest, as_json=as_json)
    except _StdoutClosedError:
        raise
    except Exception as exc:
        return _internal_error(command, exc, as_json=as_json)


def _dispatch(command: str, rest: list[str], *, as_json: bool) -> int:
    """Parse one command's options, run it and render its output.

    Any exception but the usage, help and command errors it reports
    propagates to `main`, which reports it as ``internal_error``.
    """
    loaded = _load(command)
    if loaded is None:
        if command in COMMANDS:
            return _fail(
                command,
                "not_built",
                f"perfproof {command} is not built yet in perfproof "
                f"{__version__}.",
                {},
                as_json=as_json,
                status=1,
            )
        return _fail(
            None,
            "unknown_command",
            f"unknown command {command!r}; see perfproof --help",
            {"command": command},
            as_json=as_json,
            status=_USAGE_STATUS,
        )
    add_arguments, run = loaded
    parser = _Parser(prog=f"perfproof {command}")
    parser.add_argument(
        "--json",
        action="store_true",
        help="machine-readable output, for agents",
    )
    add_arguments(parser)
    try:
        parsed, extra = parser.parse_known_args(rest)
    except _UsageError as exc:
        return _fail(
            command,
            "usage",
            str(exc),
            {},
            as_json=as_json,
            status=_USAGE_STATUS,
        )
    except _HelpRequestedError as exc:
        return _show_help(command, exc.text, as_json=as_json)
    as_json = as_json or bool(parsed.json)
    unknown = [arg for arg in extra if arg != "--json"]
    if unknown:
        return _fail(
            command,
            "usage",
            f"unrecognized arguments: {' '.join(unknown)}",
            {},
            as_json=as_json,
            status=_USAGE_STATUS,
        )
    try:
        output = run(parsed)
    except CommandError as exc:
        return _fail(
            command,
            exc.code,
            exc.message,
            exc.details,
            as_json=as_json,
            status=1,
        )
    if as_json:
        text = _dumps(
            {"command": command, "ok": True, "result": output.result}
        )
    else:
        text = output.text
    _write(text, sys.stdout)
    return 0
