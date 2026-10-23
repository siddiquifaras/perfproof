"""Profile a Ruby command with stackprof and write folded stacks.

``perfproof profile --profiler-command`` runs one argv and reads the
folded stacks it writes to ``{output}``. stackprof's own dump is a Ruby
``Marshal`` file, so the conversion runs inside the profiled process,
in `ruby_hook.rb`, and this module is the process around it::

    python -m perfproof.profilers.ruby record --output {output}
        -- {command}

It launches the command with ``RUBYOPT=-rruby_hook`` and the hook's
directory in ``RUBYLIB`` (every Ruby the command starts reads them, so
``ruby script.rb``, ``bundle exec rake`` and a test runner are the
same), waits for it under a time limit, and
concatenates the per-process files the hook wrote into ``{output}``::

    Class#method (lib/file.rb:42);...  3

A frame's line is the line the sample was on in that frame. A file under
the project root is the project's; a gem's frames are shown by their
absolute path, which is outside it. Streams of any size are copied line
by line, so memory does not grow with the profile.

The stackprof gem must be installed for the Ruby that runs (``gem
install stackprof``); without it the hook says so and exits 4. The
sampling clock is CPU time by default (``--mode wall`` for wall time).
A Ruby inside a container needs the hook and the profile directory
mounted at the same paths and the three variables passed through
(``skills/perfproof/ruby.md``). The per-process files stay in
``{output}.stackprof/``, untouched.
"""

from __future__ import annotations

import argparse
import glob
import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from perfproof.profilers._kill import freeze_and_kill

__all__ = ["HOOK", "main", "record", "run_command"]

HOOK: Final = Path(__file__).with_name("ruby_hook.rb")
"""The Ruby file that samples and folds inside the profiled process."""

DEFAULT_INTERVAL_US: Final = 1000
"""Sampling period, in microseconds of the chosen clock."""

_EXIT_NO_TOOL: Final = 4
_EXIT_NO_PROFILE: Final = 5
_EXIT_EMPTY: Final = 6
_EXIT_STALE: Final = 7
_EXIT_TIMEOUT: Final = 124
_MODES: Final = ("cpu", "wall")


def _say(message: str) -> None:
    sys.stderr.write(f"perfproof.profilers.ruby: {message}\n")


def run_command(
    command: Sequence[str], env: dict[str, str], timeout_seconds: float
) -> tuple[int, bool]:
    """Run a command; return ``(status, timed_out)``.

    On the time limit the command and every descendant are stopped and
    then killed (`perfproof.profilers._kill.freeze_and_kill`), and the
    status is 124.

    Parameters
    ----------
    command : sequence of str
        The argv; stdout and stderr are inherited.
    env : dict of str to str
        The environment.
    timeout_seconds : float
        Time limit in seconds.
    """
    process = subprocess.Popen(list(command), env=env)
    try:
        return process.wait(timeout=timeout_seconds), False
    except subprocess.TimeoutExpired:
        freeze_and_kill(process.pid, _say)
        process.wait()
        _say(f"the command ran past {timeout_seconds:g} s and was killed")
        return _EXIT_TIMEOUT, True


def record(
    command: Sequence[str],
    output: Path,
    *,
    interval_us: int = DEFAULT_INTERVAL_US,
    mode: str = "cpu",
    timeout_seconds: float = 3600.0,
) -> int:
    """Run ``command`` under stackprof and write folded stacks.

    Parameters
    ----------
    command : sequence of str
        The Ruby command, as an argv.
    output : Path
        Folded stacks are written here; the per-process files go to
        ``<output>.stackprof/``.
    interval_us : int, optional
        Sampling period in microseconds of the clock (CPU time or wall
        time, per ``mode``). Samples are counted, not weighted.
    mode : str, optional
        ``cpu`` or ``wall``.
    timeout_seconds : float, optional
        Time limit for the command, in seconds.

    Returns
    -------
    int
        The command's exit status when it is non-zero (4 when stackprof
        is missing), 124 on a timeout, 5 when no process wrote a
        profile, 6 when the profiles hold no samples, 7 when files of
        an earlier run of the same ``output`` are there, else 0. The
        reason goes to stderr.

    Raises
    ------
    ValueError
        If ``interval_us`` is less than 1 or ``mode`` is unknown.
    """
    if interval_us < 1 or mode not in _MODES:
        msg = "interval_us must be at least 1 and mode cpu or wall"
        raise ValueError(msg)
    directory = Path(f"{output}.stackprof")
    if directory.exists() and any(directory.iterdir()):
        _say(f"{directory} has files of an earlier run; use a fresh output")
        return _EXIT_STALE
    directory.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    # RUBYOPT is split on spaces with no quoting, so the hook is found
    # through RUBYLIB (split on the path separator) and named by its
    # file name only; a directory with a space in it then works.
    existing = env.get("RUBYOPT")
    hook = f"-r{HOOK.stem}"
    env["RUBYOPT"] = f"{hook} {existing}" if existing else hook
    lib = env.get("RUBYLIB")
    env["RUBYLIB"] = (
        f"{HOOK.parent}{os.pathsep}{lib}" if lib else str(HOOK.parent)
    )
    env["PERFPROOF_RUBY_PROFILE_DIR"] = str(directory.resolve())
    env["PERFPROOF_RUBY_INTERVAL_US"] = str(interval_us)
    env["PERFPROOF_RUBY_MODE"] = mode
    status, timed_out = run_command(command, env, timeout_seconds)
    if timed_out:
        return _EXIT_TIMEOUT
    if status == _EXIT_NO_TOOL:
        _say("the stackprof gem is not installed for this Ruby")
    files = sorted(glob.glob(glob.escape(str(directory)) + "/ruby.*.folded"))
    if not files:
        _say(
            f"no profile was written to {directory}: the command started "
            "no Ruby, or the Ruby was killed before it could exit"
        )
        return status or _EXIT_NO_PROFILE
    wrote = 0
    with output.open("w", encoding="utf-8") as out:
        for name in files:
            with Path(name).open(encoding="utf-8") as part:
                for line in part:
                    out.write(line)
                    wrote += 1
    if wrote == 0:
        _say(
            "the profiles hold no samples: the Ruby ran too briefly; run "
            "the workload longer"
        )
        return _EXIT_EMPTY
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line (``record`` or ``hook``).

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments after the program name; ``sys.argv[1:]`` when
        omitted.

    Returns
    -------
    int
        The exit status `record` returns; 0 for ``hook``, which prints
        the hook's path.
    """
    parser = argparse.ArgumentParser(
        prog="python -m perfproof.profilers.ruby",
        description="Run a Ruby command under stackprof.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    rec = sub.add_parser("record", help="run a command under stackprof")
    rec.add_argument("--output", required=True, type=Path)
    rec.add_argument("--interval-us", type=int, default=DEFAULT_INTERVAL_US)
    rec.add_argument("--mode", choices=_MODES, default="cpu")
    rec.add_argument("--timeout", type=float, default=3600.0)
    rec.add_argument("command", nargs=argparse.REMAINDER)
    sub.add_parser("hook", help="print the path of the hook file")
    args = parser.parse_args(argv)
    if args.action == "hook":
        sys.stdout.write(f"{HOOK}\n")
        return 0
    command: list[str] = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("give the command after --")
    return record(
        command,
        args.output,
        interval_us=args.interval_us,
        mode=args.mode,
        timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
