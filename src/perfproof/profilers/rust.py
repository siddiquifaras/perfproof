"""Profile a Rust benchmark with sample or perf, into folded stacks.

``perfproof profile --profiler-command`` runs one argv and reads the
folded stacks it writes to ``{output}``. The system profilers write
their own formats, and a Rust symbol reaches them mangled, so this
module is the one process that bridges them::

    python -m perfproof.profilers.rust record --output {output}
        --project-root <repo> -- {command}

On macOS it runs the command and samples it with the built-in
``/usr/bin/sample`` (every thread, one stack per millisecond, waiting
included). On Linux it runs ``perf record`` on the CPU-clock software
event (``-e cpu-clock``, which needs no hardware counters, so it also
works in a container or a VM) and prints it with ``perf script``. The
raw output stays beside ``{output}``, untouched.

The command must be the built binary itself: not ``cargo run`` (cargo
builds the binary and starts it as a child, so the profiler would
sample cargo) and not a shell.

A frame is written ``<module>`<symbol>``, the form
`perfproof.profilers.folded` reads. Rust symbols are demangled (see
`demangle`): the legacy scheme's hash is dropped and ``$LT$`` and the
like are decoded. A symbol in the built binary whose crate the project
defines (a ``Cargo.toml`` under the project root names it) has the
binary's own name as its module, so ``--own-module <binary>`` makes it
project code. Every other symbol in the binary (the standard library, a
dependency, C code linked in) has the module ``<binary>.deps``, which
stays library code. A symbol of another library has that library as its
module. Frames carry no file or line: ``sample`` prints none, and the
source lines ``perf script`` gave in one run were file names without a
directory. A hotspot is found by its path: ``fd::output::print_entry``
is in the crate ``fd``, module ``output``.

A share is a share of the samples kept. On macOS ``sample`` records
every thread whether it runs or waits, so a thread parked in a blocking
call is dropped (see `IDLE_LEAVES`; ``keep_idle`` keeps it) and the
drop is reported on stderr. On Linux the CPU-clock event counts a
thread only while it runs. ``sample`` attaches a few tens of
milliseconds after the command starts, which the first samples miss.

It has been run on macOS 15 (arm64) with ``sample`` and on Linux arm64
in Docker with perf 6.12, on one project. Linux stacks need frame
pointers (``RUSTFLAGS="-C force-frame-pointers=yes"``) or
``--call-graph dwarf``; macOS arm64 has them already.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tomllib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TextIO, cast

from perfproof.profilers._kill import freeze_and_kill

__all__ = [
    "IDLE_LEAVES",
    "FoldStats",
    "ProjectCrates",
    "SampleFormatError",
    "binary_names",
    "crate_of",
    "demangle",
    "fold_perf_script",
    "fold_sample_text",
    "implementing_crates",
    "main",
    "record",
    "run_command",
]

DEFAULT_INTERVAL_MS: Final = 1
"""Sampling period, in milliseconds (``sample``'s own default)."""

DEFAULT_CALL_GRAPH: Final = "fp"
"""``perf record --call-graph`` mode: frame pointers."""

IDLE_LEAVES: Final = frozenset(
    {
        "__psynch_cvwait",
        "__psynch_mutexwait",
        "__psynch_rw_rdlock",
        "__psynch_rw_wrlock",
        "__select",
        "__semwait_signal",
        "__sigwait",
        "__ulock_wait",
        "__ulock_wait2",
        "__workq_kernreturn",
        "kevent",
        "kevent64",
        "kevent_id",
        "mach_msg2_trap",
        "mach_msg_trap",
        "poll",
        "semaphore_wait_trap",
    }
)
"""macOS ``sample`` leaf symbols (of ``libsystem_kernel.dylib``) of a
thread that is parked, not working. A stack ending in one is dropped
unless ``keep_idle`` is set."""

_BLOCKING_READS: Final = frozenset(
    {"__read_nocancel", "accept", "read", "recvfrom"}
)
"""Leaves that wait when a thread does nothing else, as a signal-handler
thread does, but are real input when the thread also does other work. A
thread is dropped whole only if every one of its stacks ends in one of
these or in `IDLE_LEAVES`."""

_KERNEL_LIBRARY: Final = "libsystem_kernel.dylib"
_PENDING_STACKS: Final = 64
"""Stacks of a thread held back until it shows other work."""
_FLUSH_BYTES: Final = 32 * 1024 * 1024
"""Estimated bytes of distinct stacks held before they are written."""
_STACK_OVERHEAD: Final = 120
"""Bytes a held stack costs besides its text (entry, str header)."""
_PRINT_TIMEOUT_S: Final = 1800.0
_SKIPPED_DIRS: Final = frozenset(
    {"target", "build", "out", "node_modules", "__pycache__", "vendor"}
)
_KERNEL: Final = "[kernel]"
_KERNEL_BIT: Final = 1 << 63

_HASH: Final = re.compile(r"::h[0-9a-f]{16}$")
_ESCAPE: Final = re.compile(r"\$([A-Za-z]+|u[0-9a-fA-F]+)\$")
_ESCAPES: Final[dict[str, str]] = {
    "SP": "@",
    "BP": "*",
    "RF": "&",
    "LT": "<",
    "GT": ">",
    "LP": "(",
    "RP": ")",
    "C": ",",
}
_CRATE: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?=::)")
_TYPE_PREFIX: Final = re.compile(r"(?:[<&]|\*const |\*mut |mut |dyn )+")
_THREAD_LINE: Final = re.compile(r"Thread_\S*(?:\s.*)?")
_NODE: Final = re.compile(r"([ +!:|]*)([0-9]+) (.*)")
_SYMBOL: Final = re.compile(
    r"(?P<symbol>.*?)\s+\(in (?P<module>.*?)\)"
    r"(?:\s+\+\s+[0-9,.]+)?\s*(?:\[0x[0-9a-fA-F,.x]*\])?\s*"
)
_PERF_FRAME: Final = re.compile(r"(?P<ip>[0-9a-fA-F]+)\s+(?P<rest>.*)")

_EXIT_NO_TOOL: Final = 4
_EXIT_NO_RECORDING: Final = 5
_EXIT_BAD_RECORDING: Final = 6
_EXIT_STALE: Final = 7
_EXIT_TIMEOUT: Final = 124


class SampleFormatError(ValueError):
    """Profiler text that cannot be read as stacks.

    Parameters
    ----------
    reason : str
        What is wrong.
    line_number : int
        1-based input line.
    """

    def __init__(self, reason: str, line_number: int) -> None:
        self.line_number = line_number
        super().__init__(f"line {line_number}: {reason}")


@dataclass(slots=True)
class FoldStats:
    """What a conversion read and what it kept.

    Attributes
    ----------
    samples : int
        Samples in the input.
    kept : int
        Samples written.
    idle : int
        Samples dropped because their thread was parked (``sample``
        only).
    anomalies : int
        Call-graph nodes whose children counted more than they did
        (``sample`` only); their own count is taken as 0.
    """

    samples: int = 0
    kept: int = 0
    idle: int = 0
    anomalies: int = 0


def demangle(symbol: str) -> str:
    r"""Turn a mangled Rust symbol into its readable path.

    Three forms are read. ``sample`` prints the legacy scheme's names
    with the hash and the ``$`` escapes still in them; ``perf script``
    prints most legacy names decoded but leaves some escapes
    (``_$u7b$$u7b$closure$u7d$$u7d$``) and writes a name with an LLVM
    suffix raw (``_ZN3std2io5Write9write_all17h...E.llvm.123``); and the
    allocator shim is written in the v0 scheme
    (``_RNvCs..._7___rustc11___rdl_alloc``).

    The raw legacy form is split into its components and its suffix
    dropped; the hash (``::h`` and 16 hex digits) is dropped; a leading
    underscore on a component that starts with ``$`` is dropped;
    ``$LT$``, ``$GT$``, ``$RF$``, ``$C$``, ``$SP$``, ``$BP$``, ``$LP$``,
    ``$RP$`` and ``$u<hex>$`` are decoded; ``..`` becomes ``::``. A v0
    name is decoded only when it is a crate and nested names with
    plain identifiers; generics, closures and the rest of the grammar
    leave it as printed. A symbol with none of these (a C symbol, an
    already readable name) is returned unchanged.

    Parameters
    ----------
    symbol : str
        A symbol as the profiler printed it.

    Returns
    -------
    str
        The decoded symbol.
    """
    name = symbol
    if name.startswith("_ZN"):
        name = _split_legacy(name) or name
    elif name.startswith("_R"):
        return _decode_v0(name) or name
    name = _HASH.sub("", name)
    if "$" in name:
        name = "::".join(
            part[1:] if part.startswith("_$") else part
            for part in name.split("::")
        )
        name = _ESCAPE.sub(_decode_escape, name)
    if ".." in name:
        name = name.replace("..", "::")
    return name


def _split_legacy(symbol: str) -> str | None:
    """Join the length-prefixed parts of ``_ZN...E`` with ``::``."""
    parts: list[str] = []
    index = 3
    while index < len(symbol) and symbol[index].isdigit():
        end = index
        while end < len(symbol) and symbol[end].isdigit():
            end += 1
        length = int(symbol[index:end])
        if length == 0 or end + length > len(symbol):
            return None
        parts.append(symbol[end : end + length])
        index = end + length
    if not parts or symbol[index : index + 1] != "E":
        return None
    return "::".join(parts)


def _decode_v0(symbol: str) -> str | None:
    """Decode a v0 name that is a crate and nested plain identifiers.

    ``_R`` then ``N<namespace>`` once for each nested name, then ``C``
    and the crate's identifier, then one identifier per nesting, each
    an optional ``s<base 62>_`` disambiguator, a decimal length, an
    optional ``_`` and that many bytes. Anything else (generic
    arguments, closures, a backreference, bytes left over) is not
    decoded.
    """
    pos = 2
    nesting = 0
    while symbol[pos : pos + 1] == "N":
        if not symbol[pos + 1 : pos + 2].islower():
            return None
        pos += 2
        nesting += 1
    if symbol[pos : pos + 1] != "C":
        return None
    pos += 1
    names: list[str] = []
    for _ in range(nesting + 1):
        found = _v0_identifier(symbol, pos)
        if found is None:
            return None
        name, pos = found
        names.append(name)
    return "::".join(names) if pos == len(symbol) else None


def _v0_identifier(symbol: str, pos: int) -> tuple[str, int] | None:
    """Read the v0 identifier at ``pos`` and say where it ends."""
    if symbol[pos : pos + 1] == "s":
        end = symbol.find("_", pos)
        if end < 0:
            return None
        pos = end + 1
    start = pos
    while pos < len(symbol) and symbol[pos].isdigit():
        pos += 1
    if start == pos:
        return None
    length = int(symbol[start:pos])
    if symbol[pos : pos + 1] == "_":
        pos += 1
    name = symbol[pos : pos + length]
    if len(name) != length or not name:
        return None
    return name, pos + length


def _decode_escape(match: re.Match[str]) -> str:
    body = match.group(1)
    if body.startswith("u") and len(body) > 1:
        try:
            return chr(int(body[1:], 16))
        except (ValueError, OverflowError):
            return match.group(0)
    return _ESCAPES.get(body, match.group(0))


def crate_of(symbol: str) -> str | None:
    """Return the crate a demangled Rust path starts in.

    The first path segment, after ``<``, ``&``, ``*const``, ``*mut``,
    ``mut`` and ``dyn``.

    Parameters
    ----------
    symbol : str
        A demangled symbol, or a type or trait path.

    Returns
    -------
    str or None
        The first path segment, or ``None`` when there is no path
        (``main``, a C function, ``[unknown]``, ``[u8; 4]``).
    """
    prefix = _TYPE_PREFIX.match(symbol)
    start = prefix.end() if prefix else 0
    found = _CRATE.match(symbol, start)
    return found.group(0) if found else None


def implementing_crates(symbol: str) -> tuple[str, ...]:
    """Return the crates a demangled symbol may be written in.

    For a plain path that is its first segment. For ``<T as Trait>::m``
    it is the crate of ``T`` and the crate of ``Trait``: Rust's orphan
    rule puts the impl in one of them, so a project that owns either may
    have written it, and the symbol counts as project code. An impl of
    a standard trait for a standard type with a project type only as a
    generic argument (``<Vec<fd::X> as Clone>::clone``) is the
    standard library's and is not credited to the project.

    Parameters
    ----------
    symbol : str
        A demangled symbol.

    Returns
    -------
    tuple of str
        Zero, one or two crate names.
    """
    if symbol.startswith("<"):
        parts = _split_impl(symbol)
        if parts is not None:
            return tuple(c for c in map(crate_of, parts) if c is not None)
    found = crate_of(symbol)
    return () if found is None else (found,)


def _split_impl(symbol: str) -> tuple[str, str] | None:
    """Split ``<T as Trait>::m`` into ``T`` and ``Trait``, if it is."""
    depth = 0
    split = -1
    for index, char in enumerate(symbol):
        if char == "<":
            depth += 1
        elif char == ">" and symbol[index - 1] != "-":
            depth -= 1
            if depth == 0:
                if split < 0:
                    return None
                return symbol[1:split], symbol[split + 4 : index]
        elif depth == 1 and split < 0 and symbol.startswith(" as ", index):
            split = index
    return None


def binary_names(path: Path) -> frozenset[str]:
    """Return the file names a built binary may be reported under.

    cargo writes a binary as ``deps/<name>-<hash>`` and hard-links it to
    ``<name>``, and ``perf`` names the file it mapped, which can be
    either. The names are the binary's own and those of the files beside
    it, in its directory and in ``deps`` there, that are hard links to
    the same file.

    Parameters
    ----------
    path : pathlib.Path
        The binary.

    Returns
    -------
    frozenset of str
        File names; just the binary's own when it cannot be read.
    """
    names = {path.name}
    try:
        target = path.stat()
        for directory in (path.parent, path.parent / "deps"):
            with os.scandir(directory) as entries:
                for entry in entries:
                    seen = entry.stat(follow_symlinks=False)
                    if (seen.st_dev, seen.st_ino) == (
                        target.st_dev,
                        target.st_ino,
                    ):
                        names.add(entry.name)
    except OSError:
        return frozenset(names)
    return frozenset(names)


class ProjectCrates:
    """The crate names a project defines, read from its ``Cargo.toml``s.

    Parameters
    ----------
    root : pathlib.Path
        Project root; every ``Cargo.toml`` under it is read, except in
        hidden directories and ``target``, ``build``, ``out``,
        ``vendor`` and ``node_modules``.

    Notes
    -----
    A package contributes its name with ``-`` written as ``_``, its
    ``[lib] name`` and each ``[[bin]] name``. A file that does not parse
    is skipped. Time is one walk of the tree, ``O(files)``.
    """

    def __init__(self, root: Path) -> None:
        self.names: frozenset[str] = frozenset(_crate_names(root))


def _crate_names(root: Path) -> Iterator[str]:
    for directory, dirs, files in os.walk(root):
        dirs[:] = [
            d for d in dirs if not d.startswith(".") and d not in _SKIPPED_DIRS
        ]
        if "Cargo.toml" not in files:
            continue
        try:
            with (Path(directory) / "Cargo.toml").open("rb") as handle:
                manifest = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        yield from _manifest_names(manifest)


def _manifest_names(manifest: dict[str, object]) -> Iterator[str]:
    for key in ("package", "lib"):
        table = manifest.get(key)
        if isinstance(table, dict):
            yield from _name_of(cast("dict[str, object]", table))
    bins = manifest.get("bin")
    if isinstance(bins, list):
        for entry in cast("list[object]", bins):
            if isinstance(entry, dict):
                yield from _name_of(cast("dict[str, object]", entry))


def _name_of(table: dict[str, object]) -> Iterator[str]:
    name = table.get("name")
    if isinstance(name, str):
        yield name.replace("-", "_")


@dataclass(frozen=True, slots=True)
class _Namer:
    """Writes folded frames for one binary of one project."""

    binary: str
    crates: frozenset[str]
    aliases: frozenset[str] = frozenset()

    def frame(self, module: str, symbol: str) -> str:
        """Return the folded frame of a symbol in a module."""
        name = demangle(symbol)
        if module in self.aliases:
            module = self.binary
        if module == self.binary:
            if self.crates and self.crates.isdisjoint(
                implementing_crates(name)
            ):
                module = f"{module}.deps"
        elif module.startswith("[kernel"):
            module = _KERNEL
        text = f"{module}`{name}".replace(";", ",")
        return text.replace("\n", " ").replace("\r", " ")


class _Writer:
    """Merges equal stacks within a byte budget and writes them."""

    def __init__(self, out: TextIO, flush_bytes: int) -> None:
        self._out = out
        self._flush_bytes = max(flush_bytes, 1)
        self._counts: dict[str, int] = {}
        self._held = 0

    def add(self, stack: str, count: int) -> None:
        """Add ``count`` samples of a stack."""
        if stack in self._counts:
            self._counts[stack] += count
            return
        self._counts[stack] = count
        self._held += len(stack) + _STACK_OVERHEAD
        if self._held >= self._flush_bytes:
            self.flush()

    def flush(self) -> None:
        """Write what is held."""
        for stack, count in self._counts.items():
            self._out.write(f"{stack} {count}\n")
        self._counts.clear()
        self._held = 0


@dataclass(slots=True)
class _Node:
    depth: int
    frame: str | None
    leaf: tuple[str, str]
    count: int
    children: int = 0


@dataclass(slots=True)
class _Pending:
    stack: str
    count: int
    reading: bool


class _SampleFolder:
    """Folds the call graph of ``sample`` one node at a time.

    Each node's own samples are its count less its children's, written
    as the stack of its ancestors and itself when the node is closed.
    A thread's blocks are held back (at most `_PENDING_STACKS`) while
    every stack so far ends in a blocking call, so a thread that only
    waits is dropped whole and a thread that works is kept.
    """

    def __init__(
        self, namer: _Namer, writer: _Writer, stats: FoldStats, keep_idle: bool
    ) -> None:
        self._namer = namer
        self._writer = writer
        self._stats = stats
        self._keep_idle = keep_idle
        self._stack: list[_Node] = []
        self._pending: list[_Pending] = []
        self._working = keep_idle

    def thread(self, count: int) -> None:
        """Start the block of a thread."""
        self.finish()
        self._working = self._keep_idle
        self._stack.append(_Node(0, None, ("", ""), count))

    def node(self, depth: int, module: str, symbol: str, count: int) -> None:
        """Read a frame node, ``depth`` 1 or more."""
        while self._stack and self._stack[-1].depth >= depth:
            self._close(self._stack.pop())
        if self._stack:
            self._stack[-1].children += count
        frame = self._namer.frame(module, symbol)
        self._stack.append(_Node(depth, frame, (module, symbol), count))

    def finish(self) -> None:
        """End the block; a thread that only waited is dropped."""
        while self._stack:
            self._close(self._stack.pop())
        self._stats.idle += sum(p.count for p in self._pending)
        self._pending.clear()

    def _close(self, node: _Node) -> None:
        own = node.count - node.children
        if own < 0:
            self._stats.anomalies += 1
            own = 0
        if own == 0:
            return
        self._stats.samples += own
        frames = [n.frame for n in self._stack if n.frame is not None]
        if node.frame is not None:
            frames.append(node.frame)
        stack = ";".join(frames)
        module, symbol = node.leaf
        if self._working:
            self._write_or_drop(stack, own, module, symbol)
        elif node.frame is not None and _waits(module, symbol, False):
            self._pending.append(_Pending(stack, own, _reads(module, symbol)))
            if len(self._pending) > _PENDING_STACKS:
                self._settle()
        else:
            self._settle()
            self._write(stack, own)

    def _settle(self) -> None:
        """Release the held stacks: the thread works."""
        self._working = True
        held, self._pending = self._pending, []
        for pending in held:
            if pending.reading:
                self._write(pending.stack, pending.count)
            else:
                self._stats.idle += pending.count

    def _write_or_drop(
        self, stack: str, count: int, module: str, symbol: str
    ) -> None:
        if not self._keep_idle and _waits(module, symbol, True):
            self._stats.idle += count
        else:
            self._write(stack, count)

    def _write(self, stack: str, count: int) -> None:
        self._stats.kept += count
        self._writer.add(stack, count)


def _reads(module: str, symbol: str) -> bool:
    return module == _KERNEL_LIBRARY and symbol in _BLOCKING_READS


def _waits(module: str, symbol: str, working: bool) -> bool:
    """Whether a leaf is a parked thread's, given what its thread did.

    A thread that has shown other work (``working``) is dropped only for
    the primitives that always park it; a read is kept as input.
    """
    if module != _KERNEL_LIBRARY:
        return False
    if symbol in IDLE_LEAVES:
        return True
    return not working and symbol in _BLOCKING_READS


def _graph_nodes(
    lines: Iterable[str],
) -> Iterator[tuple[int, int, int, str]]:
    """Yield ``(line number, depth, count, text)`` of the call graph.

    A thread's header has depth 0 and a frame ``depth`` 1 or more; the
    section ends at ``Total number in stack`` or ``Sort by top of
    stack``.
    """
    in_graph = False
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if not in_graph:
            in_graph = line.strip() == "Call graph:"
            continue
        if line.startswith(("Total number in stack", "Sort by top of stack")):
            return
        if not line.strip():
            continue
        match = _NODE.fullmatch(line)
        if match is None:
            msg = f"not a call-graph line: {line[:120]!r}"
            raise SampleFormatError(msg, number)
        prefix, count, text = match.groups()
        if len(prefix) < 4 or (len(prefix) - 4) % 2:
            msg = f"unexpected indentation {len(prefix)}: {line[:120]!r}"
            raise SampleFormatError(msg, number)
        yield number, (len(prefix) - 4) // 2, int(count), text


def fold_sample_text(
    lines: Iterable[str],
    binary: str,
    crates: Iterable[str],
    out: TextIO,
    *,
    keep_idle: bool = False,
    flush_bytes: int = _FLUSH_BYTES,
    aliases: Iterable[str] = (),
) -> FoldStats:
    """Convert the call graph of macOS ``sample`` to folded stacks.

    Parameters
    ----------
    lines : iterable of str
        The report, read once and in order; only its ``Call graph:``
        section is used.
    binary : str
        File name of the built binary, as ``(in <name>)`` shows it.
    crates : iterable of str
        Crate names the project defines (`ProjectCrates`). When empty,
        every frame of the binary has the binary as its module.
    out : TextIO
        Receives folded stacks, root first, frames joined by ``;``,
        then a space and a count. The thread is not a frame. Equal
        stacks are merged within each batch of about ``flush_bytes`` of
        distinct stacks, so a stack may appear on more than one line.
    keep_idle : bool, optional
        Keep samples of parked threads instead of dropping them.
    flush_bytes : int, optional
        Estimated bytes of distinct stacks held before a batch is
        written; at least 1.
    aliases : iterable of str, optional
        Other file names the binary shows under (`binary_names`).

    Returns
    -------
    FoldStats
        Samples read, kept and dropped as idle.

    Raises
    ------
    SampleFormatError
        If a line of the call graph has no count, or its indentation is
        not a depth.

    Notes
    -----
    Time is linear in the input. Memory is bounded by ``flush_bytes``
    plus the depth of the deepest stack, plus up to 64 stacks of one
    thread while it looks idle.
    """
    stats = FoldStats()
    namer = _Namer(binary, frozenset(crates), frozenset(aliases))
    writer = _Writer(out, flush_bytes)
    folder = _SampleFolder(namer, writer, stats, keep_idle)
    in_thread = False
    for number, depth, count, text in _graph_nodes(lines):
        if depth == 0:
            if _THREAD_LINE.fullmatch(text) is None:
                msg = f"expected a thread, got {text[:120]!r}"
                raise SampleFormatError(msg, number)
            folder.thread(count)
            in_thread = True
            continue
        if not in_thread:
            msg = f"a frame before any thread: {text[:120]!r}"
            raise SampleFormatError(msg, number)
        found = _SYMBOL.fullmatch(text)
        if found is None:
            msg = f"not a frame: {text[:120]!r}"
            raise SampleFormatError(msg, number)
        folder.node(depth, found["module"], found["symbol"], count)
    folder.finish()
    writer.flush()
    return stats


def _split_dso(rest: str) -> tuple[str, str]:
    """Split ``symbol (dso)`` at the parenthesis that closes the line.

    The dso may hold parentheses of its own (``/tmp/x (deleted)``), so
    the opening one is found by matching from the end.
    """
    if not rest.endswith(")"):
        return rest, "[unknown]"
    depth = 0
    for index in range(len(rest) - 1, -1, -1):
        if rest[index] == ")":
            depth += 1
        elif rest[index] == "(":
            depth -= 1
            if depth == 0:
                if index == 0 or rest[index - 1] != " ":
                    break
                return rest[: index - 1], rest[index + 1 : -1]
    return rest, "[unknown]"


def _perf_frame(text: str, namer: _Namer) -> tuple[str, bool] | None:
    """Return a ``perf script`` frame as ``(folded frame, kernel)``."""
    match = _PERF_FRAME.fullmatch(text.strip())
    if match is None:
        return None
    symbol, dso = _split_dso(match["rest"])
    dso = dso.removesuffix(" (deleted)")
    module = dso if dso.startswith("[") else dso.rsplit("/", 1)[-1]
    module = module or "[unknown]"
    kernel = int(match["ip"], 16) >= _KERNEL_BIT or module.startswith(
        "[kernel"
    )
    if kernel:
        module = "[kernel"
    return namer.frame(module, symbol.strip()), kernel


def fold_perf_script(
    lines: Iterable[str],
    binary: str,
    crates: Iterable[str],
    out: TextIO,
    *,
    flush_bytes: int = _FLUSH_BYTES,
    aliases: Iterable[str] = (),
) -> FoldStats:
    """Convert ``perf script -F comm,tid,ip,sym,dso`` to folded stacks.

    Parameters
    ----------
    lines : iterable of str
        The printed samples, read once and in order: a header line
        (command and thread), then one indented ``address symbol
        (dso)`` line per frame, leaf first, then a blank line.
    binary : str
        File name of the built binary, as the dso's last path component.
    crates : iterable of str
        Crate names the project defines (`ProjectCrates`). When empty,
        every frame of the binary has the binary as its module.
    out : TextIO
        Receives folded stacks as `fold_sample_text` does. A frame at a
        kernel address is ``[kernel]`` and the kernel symbol when perf
        could read it; a run of frames the kernel hides is one frame.
    flush_bytes : int, optional
        Estimated bytes of distinct stacks held before a batch is
        written; at least 1.
    aliases : iterable of str, optional
        Other file names the binary shows under (`binary_names`): perf
        reports the file it mapped, which for a cargo build can be
        ``deps/<name>-<hash>`` and not ``<name>``.

    Returns
    -------
    FoldStats
        Samples read, all of them kept.

    Raises
    ------
    SampleFormatError
        If a frame line does not start with an address.

    Notes
    -----
    Time is linear in the input and memory is bounded as in
    `fold_sample_text`.
    """
    stats = FoldStats()
    namer = _Namer(binary, frozenset(crates), frozenset(aliases))
    writer = _Writer(out, flush_bytes)
    frames: list[str] | None = None
    last_hidden = False
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if not line.strip():
            if frames is not None:
                _end_sample(frames, writer, stats)
            frames = None
            continue
        if not line[0].isspace():
            if frames is not None:
                _end_sample(frames, writer, stats)
            frames = []
            last_hidden = False
            continue
        if frames is None:
            frames = []
        parsed = _perf_frame(line, namer)
        if parsed is None:
            msg = f"not a frame: {line.strip()[:120]!r}"
            raise SampleFormatError(msg, number)
        frame, kernel = parsed
        hidden = kernel and frame.endswith("`[unknown]")
        if hidden and last_hidden:
            continue
        last_hidden = hidden
        frames.append(frame)
    if frames is not None:
        _end_sample(frames, writer, stats)
    writer.flush()
    return stats


def _end_sample(frames: list[str], writer: _Writer, stats: FoldStats) -> None:
    stats.samples += 1
    stats.kept += 1
    writer.add(";".join(reversed(frames)), 1)


def _say(message: str) -> None:
    sys.stderr.write(f"perfproof.profilers.rust: {message}\n")


def _fold_report(stats: FoldStats) -> None:
    if stats.idle:
        _say(
            f"kept {stats.kept} of {stats.samples} samples; "
            f"{stats.idle} were threads parked in a blocking call"
        )
    if stats.anomalies:
        _say(
            f"{stats.anomalies} call-graph nodes counted less than "
            "their children"
        )


def run_command(
    command: Sequence[str], env: dict[str, str], timeout_seconds: float
) -> tuple[int, bool]:
    """Run a command; return ``(status, timed_out)``.

    On the timeout the command and every descendant it started are
    stopped and then killed with ``SIGKILL``
    (`perfproof.profilers._kill.freeze_and_kill`), so a process that
    keeps forking cannot get away.

    Parameters
    ----------
    command : sequence of str
        The argv; stdout and stderr are inherited.
    env : dict of str to str
        The environment.
    timeout_seconds : float
        Time limit in seconds.

    Returns
    -------
    tuple of (int, bool)
        The exit status, and whether the limit was exceeded (then the
        status is 124).
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
    interval_ms: int = DEFAULT_INTERVAL_MS,
    project_root: Path | None = None,
    timeout_seconds: float = 3600.0,
    keep_idle: bool = False,
    call_graph: str = DEFAULT_CALL_GRAPH,
) -> int:
    """Run a built Rust binary under ``sample`` or ``perf``.

    Parameters
    ----------
    command : sequence of str
        The binary's argv. Its stdout and stderr are inherited.
    output : pathlib.Path
        Folded stacks are written here. The raw report is written
        beside it, as ``<output>.sample.txt`` on macOS and
        ``<output>.perf.data`` on Linux.
    interval_ms : int, optional
        Sampling period in milliseconds, at least 1 (on Linux it sets
        the frequency, ``1000 / interval_ms`` Hz).
    project_root : pathlib.Path, optional
        Root whose ``Cargo.toml`` files name the project's crates; the
        working directory when omitted.
    timeout_seconds : float, optional
        Time limit for the command, in seconds; the command and every
        process it started are killed when it is exceeded.
    keep_idle : bool, optional
        macOS only: keep the samples of parked threads.
    call_graph : str, optional
        Linux only: ``perf record --call-graph`` mode (``fp``,
        ``dwarf``, ``lbr``).

    Returns
    -------
    int
        The command's exit status when it is non-zero, 124 on a
        timeout, 4 when the profiler is missing or the platform has
        none, 5 when no report was written, 6 when it cannot be read or
        holds no samples, 7 when the raw report of an earlier run of the
        same ``output`` is there, else 0. The reason goes to stderr.

    Raises
    ------
    ValueError
        If ``interval_ms`` is less than 1.
    """
    if interval_ms < 1:
        msg = "interval_ms must be at least 1"
        raise ValueError(msg)
    if sys.platform == "darwin":
        raw = Path(f"{output}.sample.txt")
    elif sys.platform.startswith("linux"):
        raw = Path(f"{output}.perf.data")
    else:
        _say(f"no profiler is wired for {sys.platform}")
        return _EXIT_NO_TOOL
    if raw.exists():
        _say(f"{raw} from an earlier run is there; use a fresh output path")
        return _EXIT_STALE
    tool = "sample" if sys.platform == "darwin" else "perf"
    found = shutil.which(tool)
    if found is None:
        _say(f"{tool} was not found on the PATH")
        return _EXIT_NO_TOOL
    env = dict(os.environ)
    if sys.platform == "darwin":
        argv = [
            sys.executable,
            "-m",
            "perfproof.profilers.rust",
            "_sample-run",
            "--raw",
            str(raw),
            "--sample",
            found,
            "--interval-ms",
            str(interval_ms),
            "--duration",
            str(int(timeout_seconds) + 60),
            "--",
            *command,
        ]
    else:
        argv = [
            found,
            "record",
            "-q",
            "-e",
            "cpu-clock",
            "-F",
            str(max(1, round(1000 / interval_ms))),
            "--call-graph",
            call_graph,
            "-o",
            str(raw),
            "--",
            *command,
        ]
    status, timed_out = run_command(argv, env, timeout_seconds)
    if timed_out:
        return _EXIT_TIMEOUT
    if not raw.is_file():
        _say(
            f"{tool} wrote no report to {raw}: the command ended before "
            "it could attach, or it could not start"
        )
        return status or _EXIT_NO_RECORDING
    root = project_root or Path.cwd()
    binary = Path(command[0]).name
    aliases = binary_names(Path(shutil.which(command[0]) or command[0]))
    crates = ProjectCrates(root).names
    if not crates:
        _say(
            f"no Cargo.toml under {root} names a crate, so every frame of "
            f"{binary} is shown as the binary's"
        )
    code = _convert(found, raw, binary, aliases, crates, output, keep_idle)
    return code or status


def _convert(
    tool: str,
    raw: Path,
    binary: str,
    aliases: frozenset[str],
    crates: frozenset[str],
    output: Path,
    keep_idle: bool,
) -> int:
    """Fold the raw report into ``output``; return an exit code."""
    try:
        with output.open("w", encoding="utf-8") as out:
            if sys.platform == "darwin":
                with raw.open(encoding="utf-8", errors="replace") as source:
                    stats = fold_sample_text(
                        source,
                        binary,
                        crates,
                        out,
                        keep_idle=keep_idle,
                        aliases=aliases,
                    )
            else:
                result = _fold_perf(tool, raw, binary, aliases, crates, out)
                if isinstance(result, int):
                    return result
                stats = result
    except SampleFormatError as exc:
        _say(f"{raw}: {exc}")
        return _EXIT_BAD_RECORDING
    _fold_report(stats)
    if stats.kept == 0:
        _say(
            "the report holds no samples: the command ran too briefly, "
            "or only waited; run the workload longer"
        )
        return _EXIT_BAD_RECORDING
    return 0


def _fold_perf(
    perf: str,
    raw: Path,
    binary: str,
    aliases: frozenset[str],
    crates: frozenset[str],
    out: TextIO,
) -> FoldStats | int:
    """Print a perf recording and fold it; an int is an exit code."""
    argv = [perf, "script", "-i", str(raw), "-F", "comm,tid,ip,sym,dso"]
    with (
        tempfile.TemporaryFile(
            mode="w+", dir=raw.parent, encoding="utf-8"
        ) as errors,
        subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            errors="replace",
        ) as printer,
    ):
        stream = printer.stdout
        if stream is None:
            return _EXIT_BAD_RECORDING
        timer = threading.Timer(_PRINT_TIMEOUT_S, printer.kill)
        timer.start()
        try:
            stats = fold_perf_script(
                stream, binary, crates, out, aliases=aliases
            )
            printer.wait()
        except SampleFormatError as exc:
            printer.kill()
            _say(f"{raw}: {exc}")
            return _EXIT_BAD_RECORDING
        finally:
            timer.cancel()
        if printer.returncode != 0:
            errors.seek(0)
            detail = errors.read().strip()[:300]
            _say(
                f"perf script of {raw} failed or ran past "
                f"{_PRINT_TIMEOUT_S:g} s: {detail}"
            )
            return _EXIT_BAD_RECORDING
    return stats


def _sample_run(
    command: Sequence[str],
    raw: Path,
    sample: str,
    interval_ms: int,
    duration: int,
) -> int:
    """Start the command and ``sample`` it until it exits.

    Runs as its own process so that `record`'s timeout kills the
    command, the sampler and this process together. ``sample`` stops
    when the process it watches exits.
    """
    child = subprocess.Popen(list(command))
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as chatter:
        sampler = subprocess.Popen(
            [
                sample,
                str(child.pid),
                str(duration),
                str(interval_ms),
                "-file",
                str(raw),
            ],
            stdout=chatter,
            stderr=chatter,
        )
        status = child.wait()
        if sampler.wait() != 0:
            chatter.seek(0)
            _say(
                f"sample exited {sampler.returncode}: {chatter.read()[-300:]}"
            )
    return status


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``record`` command line.

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
        prog="python -m perfproof.profilers.rust",
        description="Run a Rust binary under sample (macOS) or perf (Linux).",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    rec = sub.add_parser("record", help="run a command under a profiler")
    rec.add_argument("--output", required=True, type=Path)
    rec.add_argument("--interval-ms", type=int, default=DEFAULT_INTERVAL_MS)
    rec.add_argument("--project-root", type=Path, default=None)
    rec.add_argument("--timeout", type=float, default=3600.0)
    rec.add_argument("--keep-idle", action="store_true")
    rec.add_argument("--call-graph", default=DEFAULT_CALL_GRAPH)
    rec.add_argument("command", nargs=argparse.REMAINDER)
    inner = sub.add_parser("_sample-run")
    inner.add_argument("--raw", required=True, type=Path)
    inner.add_argument("--sample", required=True)
    inner.add_argument("--interval-ms", type=int, required=True)
    inner.add_argument("--duration", type=int, required=True)
    inner.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command: list[str] = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("give the command after --")
    if args.action == "_sample-run":
        return _sample_run(
            command, args.raw, args.sample, args.interval_ms, args.duration
        )
    return record(
        command,
        args.output,
        interval_ms=args.interval_ms,
        project_root=args.project_root,
        timeout_seconds=args.timeout,
        keep_idle=args.keep_idle,
        call_graph=args.call_graph,
    )


if __name__ == "__main__":
    sys.exit(main())
