"""Cover a Rust binary and write its coverage as an lcov report.

This is the Rust end of the coverage gate's interface, "a command that
produces a standard coverage report": run the instrumented binary, then
turn the counters it wrote into lcov::

    python -m perfproof.bench.rust_coverage --output {output}.lcov
        --project-root <repo> -- target/perfproof-cov/release/<name> ...

The binary is built in ``setup`` with ``RUSTFLAGS="-C
instrument-coverage"`` (and a ``--target-dir`` of its own, so that the
timed binary stays uninstrumented). It writes ``.profraw`` files to a
directory made for the run; ``llvm-profdata merge`` combines them and
``llvm-cov export --format=lcov`` reads them against the binary, which
are the toolchain's own tools (``rustup component add
llvm-tools-preview``; the ones in the sysroot of the ``rustc`` found
from the project root, so a ``rust-toolchain.toml`` applies) or, when
the toolchain has none, ``llvm-profdata`` and ``llvm-cov`` on the PATH.
They must come from LLVM at least as new as the compiler's.

Only files under the project root are kept, with their paths made
relative to it: the standard library and the dependencies in the
registry are not the project's code. A line is covered when its ``DA``
count is above 0.

The coverage gate does not read this yet (T2.24 wires the gate; it
covers Python only until then), so a Rust change still reports "changed
lines were not checked".
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Final, TextIO

from perfproof.profilers.rust import run_command

__all__ = ["filter_lcov", "llvm_tools", "main", "record"]

_TOOL_TIMEOUT_S: Final = 1800.0
_EXIT_NO_TOOL: Final = 4
_EXIT_NO_DATA: Final = 5
_EXIT_TOOL_FAILED: Final = 6
_EXIT_STALE: Final = 7
_EXIT_TIMEOUT: Final = 124


def filter_lcov(lines: Iterable[str], project_root: Path) -> Iterator[str]:
    """Keep the lcov records of the project's files only.

    Parameters
    ----------
    lines : iterable of str
        An lcov report, read once: records of ``SF:`` through
        ``end_of_record``.
    project_root : pathlib.Path
        Root of the project. A record whose ``SF:`` path is under it (as
        written, or with symlinks resolved) is kept with that path
        relative to the root, in posix form; every other record is
        dropped.

    Yields
    ------
    str
        The kept lines, without line endings.

    Notes
    -----
    One record is held at a time, so memory is that of the largest
    file's record.
    """
    roots = {project_root.absolute(), project_root.resolve()}
    record: list[str] = []
    keep = False
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("SF:"):
            record = []
            shown = _relative(Path(line[3:]), roots)
            keep = shown is not None
            if shown is not None:
                line = f"SF:{shown}"
        if keep:
            record.append(line)
        if line == "end_of_record":
            yield from record
            record = []
            keep = False


def _relative(path: Path, roots: set[Path]) -> str | None:
    for root in roots:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            continue
    return None


def _say(message: str) -> None:
    sys.stderr.write(f"perfproof.bench.rust_coverage: {message}\n")


def llvm_tools(root: Path) -> tuple[str, str] | None:
    """Find ``llvm-profdata`` and ``llvm-cov``, toolchain first.

    Parameters
    ----------
    root : pathlib.Path
        Directory to ask ``rustc`` from, so that a
        ``rust-toolchain.toml`` there selects the toolchain.

    Returns
    -------
    tuple of (str, str) or None
        The two executables, from the sysroot of the ``rustc`` on the
        PATH when it has them, else from the PATH; ``None`` when neither
        has both.
    """
    rustc = shutil.which("rustc")
    if rustc is not None:
        try:
            done = subprocess.run(
                [rustc, "--print", "sysroot"],
                capture_output=True,
                text=True,
                cwd=root,
                timeout=60,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            done = None
        if done is not None and done.returncode == 0:
            for bin_dir in sorted(
                (Path(done.stdout.strip()) / "lib" / "rustlib").glob("*/bin")
            ):
                profdata = bin_dir / "llvm-profdata"
                cov = bin_dir / "llvm-cov"
                if profdata.is_file() and cov.is_file():
                    return str(profdata), str(cov)
    profdata_path = shutil.which("llvm-profdata")
    cov_path = shutil.which("llvm-cov")
    if profdata_path and cov_path:
        return profdata_path, cov_path
    return None


def _run(argv: Sequence[str], stdout: TextIO | int | None = None) -> int:
    try:
        done = subprocess.run(
            list(argv),
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            timeout=_TOOL_TIMEOUT_S,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _say(f"{argv[0]} could not finish: {exc}")
        return _EXIT_TOOL_FAILED
    if done.returncode != 0:
        _say(f"{argv[0]} exited {done.returncode}: {done.stderr[-300:]}")
        return _EXIT_TOOL_FAILED
    return 0


def record(
    command: Sequence[str],
    output: Path,
    *,
    project_root: Path | None = None,
    objects: Sequence[Path] = (),
    timeout_seconds: float = 3600.0,
) -> int:
    """Run an instrumented binary and write its coverage as lcov.

    Parameters
    ----------
    command : sequence of str
        The instrumented binary's argv; its first element is the binary
        the counters are read against. Its stdout and stderr are
        inherited.
    output : pathlib.Path
        The lcov report is written here. It must not exist.
    project_root : pathlib.Path, optional
        Root whose files are kept; the working directory when omitted.
    objects : sequence of pathlib.Path, optional
        Other instrumented binaries the command runs, such as the
        test executables of a library.
    timeout_seconds : float, optional
        Time limit for the command; it and every process it started
        are killed when it is exceeded.

    Returns
    -------
    int
        The command's exit status when it is non-zero (the report is
        written all the same), 124 on a timeout, 4 when the LLVM tools
        are missing, 5 when the command wrote no counters, 6 when an
        LLVM tool failed, 7 when ``output`` exists, else 0. The reason
        goes to stderr.
    """
    if output.exists():
        _say(f"{output} exists; use a fresh output path")
        return _EXIT_STALE
    root = (project_root or Path.cwd()).absolute()
    tools = llvm_tools(root)
    if tools is None:
        _say(
            "llvm-profdata and llvm-cov were not found in the rustc "
            "sysroot or on the PATH; run `rustup component add "
            "llvm-tools-preview`"
        )
        return _EXIT_NO_TOOL
    profdata, cov = tools
    with tempfile.TemporaryDirectory(prefix="perfproof-cov-") as work:
        raw = Path(work)
        env = dict(os.environ)
        env["LLVM_PROFILE_FILE"] = str(raw / "run-%p-%m.profraw")
        status, timed_out = run_command(command, env, timeout_seconds)
        if timed_out:
            return _EXIT_TIMEOUT
        counters = sorted(raw.glob("*.profraw"))
        if not counters:
            _say(
                "the command wrote no .profraw files: the binary was not "
                "built with -C instrument-coverage, or it was killed "
                "before it exited"
            )
            return status or _EXIT_NO_DATA
        merged = raw / "merged.profdata"
        failed = _run(
            [profdata, "merge", "-sparse", "-o", str(merged)]
            + [str(c) for c in counters]
        )
        if failed:
            return failed
        binary = shutil.which(command[0], path=env.get("PATH")) or command[0]
        exported = raw / "export.lcov"
        with exported.open("w", encoding="utf-8") as handle:
            failed = _run(
                [
                    cov,
                    "export",
                    "--format=lcov",
                    f"--instr-profile={merged}",
                    binary,
                    *[f"--object={o}" for o in objects],
                ],
                handle,
            )
        if failed:
            return failed
        with (
            exported.open(encoding="utf-8") as source,
            output.open("x", encoding="utf-8") as out,
        ):
            for line in filter_lcov(source, root):
                out.write(line + "\n")
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments after the program name; ``sys.argv[1:]`` when
        omitted.

    Returns
    -------
    int
        The exit status `record` returns.
    """
    parser = argparse.ArgumentParser(
        prog="python -m perfproof.bench.rust_coverage",
        description="Run an instrumented Rust binary and write lcov.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument("--object", type=Path, action="append", default=[])
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command: list[str] = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("give the command after --")
    return record(
        command,
        args.output,
        project_root=args.project_root,
        objects=args.object,
        timeout_seconds=args.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
