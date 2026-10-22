"""Profile a Go benchmark with pprof and write folded stacks.

``perfproof profile --profiler-command`` runs one argv and reads the
folded stacks it writes to ``{output}``. Go's profilers write pprof
files (``profile.proto``, gzip-compressed) and their frames name a
function and a file but not the project, so this module is the one
process that bridges them::

    python -m perfproof.profilers.go record --output {output}
        -- {command}

It starts the command with a Go profile switched on, waits for it,
reads the pprof file the command wrote and converts it to folded
stacks in the form `perfproof.profilers.folded` reads::

    Join (/repo/work.go:16);runtime.memmove (...) 9

How the profile is switched on depends on the command:

- ``go test ...`` gets ``-cpuprofile`` (or ``-memprofile``,
  ``-mutexprofile``, ``-blockprofile``) and ``-o <dir>/``, so the test
  binary is not left in the working directory;
- a compiled test binary (a name ending in ``.test``, or an argument
  starting ``-test.``) gets the same flags as ``-test.cpuprofile=...``;
- any other command, ``go run`` and a built program included, gets
  ``PERFPROOF_PPROF`` (the file to write) and ``PERFPROOF_PPROF_KIND``
  in its environment. A program honours them with the helper in
  ``skills/perfproof/go.md``; a program that does not writes no
  profile, and that is reported, not guessed at.

pprof is read here, in Python, not through ``go tool pprof``: the
profile is converted wherever perfproof runs, whether or not that
machine has Go (a container's Go wrote it). The file is read twice, in
chunks, and its samples are never held: memory grows with the number of
locations and strings in the file, not with the number of samples.

A function in the project's own files is shown without its module path
(``Join``, ``Server.Serve``, ``sub.T.Run``); one outside them keeps its
import path (``net/url.(*URL).EscapedPath``).

A share is a share of the weight in one sample type: ``cpu`` (on-CPU
time in nanoseconds, sampled by the runtime at 100 Hz) by default;
``alloc_space``, ``alloc_objects``, ``inuse_space`` and
``inuse_objects`` for a memory profile; ``delay`` (nanoseconds) and
``contentions`` for a mutex or block profile. Time spent waiting is
not in a CPU profile.

Run on Go 1.25 only. The raw pprof file stays beside the output.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, Literal, TextIO, cast

from perfproof.profilers._kill import freeze_and_kill

__all__ = [
    "PprofFormatError",
    "fold_pprof",
    "main",
    "record",
]

type ProfileKind = Literal["cpu", "mem", "mutex", "block"]

PROFILE_KINDS: Final[tuple[ProfileKind, ...]] = (
    "cpu",
    "mem",
    "mutex",
    "block",
)
DEFAULT_SAMPLE_TYPE: Final[Mapping[ProfileKind, str]] = {
    "cpu": "cpu",
    "mem": "alloc_space",
    "mutex": "delay",
    "block": "delay",
}
"""The sample type each kind of profile is read by, unless told."""
DEFAULT_MEM_RATE: Final = 4096
"""``-memprofilerate``, in bytes: one sample per this many allocated
(Go's own default, 512 KiB, gives few samples in a short benchmark)."""
ENV_PATH: Final = "PERFPROOF_PPROF"
ENV_KIND: Final = "PERFPROOF_PPROF_KIND"

_CHUNK_BYTES: Final = 1 << 20
_MAX_FIELD_BYTES: Final = 64 * 1024 * 1024
_MAX_STRING_BYTES: Final = 256 * 1024 * 1024
"""Most bytes of string table held; a profile with more is refused."""
_FLUSH_BYTES: Final = 32 * 1024 * 1024
"""Estimated bytes of distinct stacks held before they are written."""
_STACK_OVERHEAD: Final = 120
_GZIP_MAGIC: Final = b"\x1f\x8b"
_CPU_WARN_NS: Final = 1_000_000_000

_EXIT_NO_PROFILE: Final = 5
_EXIT_BAD_PROFILE: Final = 6
_EXIT_STALE: Final = 7
_EXIT_TIMEOUT: Final = 124

# Field numbers of profile.proto, from github.com/google/pprof.
_PROFILE_SAMPLE_TYPE: Final = 1
_PROFILE_SAMPLE: Final = 2
_PROFILE_LOCATION: Final = 4
_PROFILE_FUNCTION: Final = 5
_PROFILE_STRING_TABLE: Final = 6
_VALUE_TYPE_TYPE: Final = 1
_SAMPLE_LOCATION_ID: Final = 1
_SAMPLE_VALUE: Final = 2
_LOCATION_ID: Final = 1
_LOCATION_ADDRESS: Final = 3
_LOCATION_LINE: Final = 4
_LINE_FUNCTION_ID: Final = 1
_LINE_LINE: Final = 2
_FUNCTION_ID: Final = 1
_FUNCTION_NAME: Final = 2
_FUNCTION_FILENAME: Final = 4

_RECEIVER: Final = re.compile(r"\(\*([^()]+)\)")

_VARINT: Final = 0
_FIXED64: Final = 1
_LEN: Final = 2
_FIXED32: Final = 5


class PprofFormatError(ValueError):
    """A pprof file that cannot be read as the profile asked for.

    Parameters
    ----------
    reason : str
        What is wrong.
    """


def _say(message: str) -> None:
    sys.stderr.write(f"perfproof.profilers.go: {message}\n")


def _varint(data: bytes | memoryview, at: int) -> tuple[int, int]:
    """Decode an unsigned varint at ``at``; return ``(value, next)``."""
    value = 0
    shift = 0
    while True:
        if at >= len(data) or shift > 63:
            msg = "a varint runs past the end of its message"
            raise PprofFormatError(msg)
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, at
        shift += 7


def _signed(value: int) -> int:
    """Reinterpret an unsigned varint as a signed 64-bit integer."""
    return value - (1 << 64) if value >= 1 << 63 else value


def _fields(
    data: bytes | memoryview,
) -> Iterator[tuple[int, int, int | bytes]]:
    """Yield ``(field number, wire type, value)`` of one message.

    A varint or fixed-width value is an ``int``; a length-delimited
    value is ``bytes``.
    """
    at = 0
    while at < len(data):
        tag, at = _varint(data, at)
        number, wire = tag >> 3, tag & 7
        if wire == _VARINT:
            value, at = _varint(data, at)
            yield number, wire, value
        elif wire == _LEN:
            size, at = _varint(data, at)
            if at + size > len(data):
                msg = "a field runs past the end of its message"
                raise PprofFormatError(msg)
            yield number, wire, bytes(data[at : at + size])
            at += size
        elif wire in (_FIXED64, _FIXED32):
            size = 8 if wire == _FIXED64 else 4
            if at + size > len(data):
                msg = "a fixed-width field runs past the end of its message"
                raise PprofFormatError(msg)
            yield (
                number,
                wire,
                int.from_bytes(data[at : at + size], "little"),
            )
            at += size
        else:
            msg = f"wire type {wire} is not one profile.proto uses"
            raise PprofFormatError(msg)


def _packed(value: int | bytes) -> Iterator[int]:
    """Yield the integers of a repeated field, packed or not."""
    if isinstance(value, int):
        yield value
        return
    at = 0
    while at < len(value):
        item, at = _varint(value, at)
        yield item


class _Messages:
    """The top-level fields of a profile, read in chunks.

    Parameters
    ----------
    stream : BinaryIO
        The profile's bytes, already decompressed.
    """

    def __init__(self, stream: BinaryIO) -> None:
        self._stream = stream
        self._buffer = b""
        self._at = 0
        self._eof = False

    def _need(self, count: int) -> bool:
        """Make ``count`` bytes available; say whether they are."""
        while len(self._buffer) - self._at < count and not self._eof:
            chunk = self._stream.read(_CHUNK_BYTES)
            if not chunk:
                self._eof = True
                break
            self._buffer = self._buffer[self._at :] + chunk
            self._at = 0
        return len(self._buffer) - self._at >= count

    def _byte_varint(self) -> int | None:
        """Read a varint from the stream; ``None`` at a clean end."""
        value = 0
        shift = 0
        while True:
            if not self._need(1):
                if shift == 0:
                    return None
                msg = "the profile ends inside a varint"
                raise PprofFormatError(msg)
            byte = self._buffer[self._at]
            self._at += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value
            shift += 7
            if shift > 63:
                msg = "a varint is longer than 64 bits"
                raise PprofFormatError(msg)

    def __iter__(self) -> Iterator[tuple[int, int, int | bytes]]:
        while (tag := self._byte_varint()) is not None:
            number, wire = tag >> 3, tag & 7
            if wire == _VARINT:
                value = self._byte_varint()
                if value is None:
                    msg = "the profile ends inside a field"
                    raise PprofFormatError(msg)
                yield number, wire, value
            elif wire == _LEN:
                size = self._byte_varint()
                if size is None or size > _MAX_FIELD_BYTES:
                    msg = (
                        "a field is larger than the reader's "
                        f"{_MAX_FIELD_BYTES} bytes"
                    )
                    raise PprofFormatError(msg)
                if not self._need(size):
                    msg = "the profile ends inside a field"
                    raise PprofFormatError(msg)
                yield (
                    number,
                    wire,
                    self._buffer[self._at : self._at + size],
                )
                self._at += size
            elif wire in (_FIXED64, _FIXED32):
                size = 8 if wire == _FIXED64 else 4
                if not self._need(size):
                    msg = "the profile ends inside a field"
                    raise PprofFormatError(msg)
                piece = self._buffer[self._at : self._at + size]
                self._at += size
                yield number, wire, int.from_bytes(piece, "little")
            else:
                msg = f"wire type {wire} is not one profile.proto uses"
                raise PprofFormatError(msg)


def _open(path: Path) -> BinaryIO:
    """Open a profile for reading, gunzipping it when it is gzip."""
    with path.open("rb") as probe:
        magic = probe.read(2)
    if magic == _GZIP_MAGIC:
        return cast("BinaryIO", gzip.open(path, "rb"))
    return path.open("rb")


@dataclass(slots=True)
class _Tables:
    """What the first pass keeps: the profile minus its samples."""

    sample_types: list[tuple[int, int]]
    locations: dict[int, tuple[int, tuple[tuple[int, int], ...]]]
    functions: dict[int, tuple[int, int]]
    strings: list[str]


def _read_tables(path: Path) -> _Tables:
    """First pass: sample types, locations, functions and strings."""
    tables = _Tables([], {}, {}, [])
    held = 0
    try:
        with _open(path) as stream:
            for number, _, value in _Messages(stream):
                if not isinstance(value, bytes):
                    continue
                if number == _PROFILE_SAMPLE_TYPE:
                    tables.sample_types.append(_value_type(value))
                elif number == _PROFILE_LOCATION:
                    ident, entry = _location(value)
                    tables.locations[ident] = entry
                elif number == _PROFILE_FUNCTION:
                    ident, name_file = _function(value)
                    tables.functions[ident] = name_file
                elif number == _PROFILE_STRING_TABLE:
                    held += len(value)
                    if held > _MAX_STRING_BYTES:
                        msg = (
                            "the string table is larger than the reader's "
                            f"{_MAX_STRING_BYTES} bytes"
                        )
                        raise PprofFormatError(msg)
                    tables.strings.append(value.decode("utf-8", "replace"))
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        msg = f"{path} cannot be read as a pprof profile: {exc}"
        raise PprofFormatError(msg) from exc
    return tables


def _value_type(data: bytes) -> tuple[int, int]:
    kind = unit = 0
    for number, _, value in _fields(data):
        if isinstance(value, int):
            if number == _VALUE_TYPE_TYPE:
                kind = value
            elif number == _VALUE_TYPE_TYPE + 1:
                unit = value
    return kind, unit


def _location(
    data: bytes,
) -> tuple[int, tuple[int, tuple[tuple[int, int], ...]]]:
    ident = address = 0
    lines: list[tuple[int, int]] = []
    for number, _, value in _fields(data):
        if number == _LOCATION_ID and isinstance(value, int):
            ident = value
        elif number == _LOCATION_ADDRESS and isinstance(value, int):
            address = value
        elif number == _LOCATION_LINE and isinstance(value, bytes):
            function = line = 0
            for sub, _, item in _fields(value):
                if isinstance(item, int):
                    if sub == _LINE_FUNCTION_ID:
                        function = item
                    elif sub == _LINE_LINE:
                        line = _signed(item)
            lines.append((function, line))
    return ident, (address, tuple(lines))


def _function(data: bytes) -> tuple[int, tuple[int, int]]:
    ident = name = filename = 0
    for number, _, value in _fields(data):
        if isinstance(value, int):
            if number == _FUNCTION_ID:
                ident = value
            elif number == _FUNCTION_NAME:
                name = value
            elif number == _FUNCTION_FILENAME:
                filename = value
    return ident, (name, filename)


class _PathMapper:
    """Show the files of a profile as paths on this machine.

    Parameters
    ----------
    root : pathlib.Path or None
        The project root, as it is on this machine.
    path_map : sequence of (str, str)
        ``(profile prefix, local prefix)`` pairs, such as
        ``("/src", "/Users/me/repo")`` for a profile written in a
        container that mounted the repository at ``/src``. The first
        pair whose prefix starts a file wins.
    module : str or None
        The Go module's path. A file named ``<module>/x/y.go``, the
        form ``go build -trimpath`` records, is ``<root>/x/y.go``.
    """

    def __init__(
        self,
        root: Path | None,
        path_map: Sequence[tuple[str, str]],
        module: str | None,
    ) -> None:
        self._root = root
        self._pairs = tuple(
            (src.rstrip("/"), dst.rstrip("/")) for src, dst in path_map
        )
        self._module = module
        self._prefixes: tuple[str, ...] = ()
        if root is not None:
            texts = {root.as_posix(), root.resolve().as_posix()}
            self._prefixes = tuple(sorted(t.rstrip("/") + "/" for t in texts))

    def __call__(self, file: str) -> str:
        for src, dst in self._pairs:
            if file == src or file.startswith(src + "/"):
                return dst + file[len(src) :]
        module = self._module
        if (
            module is not None
            and self._root is not None
            and file.startswith(module + "/")
        ):
            return f"{self._root.as_posix()}/{file[len(module) + 1 :]}"
        return file

    def short(self, name: str) -> str:
        """Drop the module path from the name of the project's function.

        ``example.com/m/v2.(*Server).Serve`` is shown as
        ``Server.Serve``, and ``example.com/m/v2/sub.(*T).Run`` as
        ``sub.T.Run``: the file says which package it is in. Without a
        module path the name is left as it is.
        """
        module = self._module
        if module is None or not name.startswith((module + ".", module + "/")):
            return name
        return _RECEIVER.sub(r"\1", name[len(module) + 1 :], count=1)

    def is_own(self, mapped: str) -> bool:
        """Say whether a mapped path is the project's own source.

        That is a path under the project root that is not under a
        ``vendor`` directory.
        """
        if self._root is None:
            return False
        for prefix in self._prefixes:
            if mapped.startswith(prefix):
                return "vendor" not in mapped[len(prefix) :].split("/")
        return False


def module_path(root: Path) -> str | None:
    """Return the module path in ``root/go.mod``, or ``None``.

    Parameters
    ----------
    root : pathlib.Path
        A directory that may hold a ``go.mod``.

    Returns
    -------
    str or None
        The path on the ``module`` line; ``None`` when there is no
        ``go.mod`` or no such line.
    """
    try:
        text = (root / "go.mod").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        words = line.split("//", 1)[0].split()
        if len(words) == 2 and words[0] == "module":
            return words[1].strip('"`')
    return None


class _Frames:
    """Turn a location into the text of its frames, memoised."""

    def __init__(self, tables: _Tables, mapper: _PathMapper) -> None:
        self._tables = tables
        self._mapper = mapper
        self._memo: dict[int, tuple[tuple[str, bool], ...]] = {}

    def _string(self, index: int) -> str:
        strings = self._tables.strings
        if not 0 <= index < len(strings):
            msg = f"string index {index} is outside the string table"
            raise PprofFormatError(msg)
        return strings[index]

    def of(self, location: int) -> tuple[tuple[str, bool], ...]:
        """Return a location's frames, inlined callee first.

        Each frame is its text and whether it is the project's own.
        """
        found = self._memo.get(location)
        if found is not None:
            return found
        entry = self._tables.locations.get(location)
        if entry is None:
            msg = f"a sample names location {location}, which is not defined"
            raise PprofFormatError(msg)
        address, lines = entry
        if not lines:
            frames = ((f"0x{address:x}", False),)
        else:
            frames = tuple(self._frame(*line) for line in lines)
        self._memo[location] = frames
        return frames

    def _frame(self, function: int, line: int) -> tuple[str, bool]:
        pair = self._tables.functions.get(function)
        if pair is None:
            msg = f"a line names function {function}, which is not defined"
            raise PprofFormatError(msg)
        name = self._string(pair[0]).replace(";", ":").replace("\n", " ")
        file = self._string(pair[1])
        if not file:
            return name, False
        mapped = self._mapper(file)
        own = self._mapper.is_own(mapped)
        if own:
            name = self._mapper.short(name)
        return f"{name} ({mapped}:{max(line, 0)})", own


def _sample_index(tables: _Tables, wanted: str | None) -> int:
    """Pick the sample type to weight stacks by.

    Raises
    ------
    PprofFormatError
        If ``wanted`` is not one of the profile's types, or is ``None``
        and the profile has several with no ``cpu`` among them.
    """
    names = [tables.strings[kind] for kind, _ in tables.sample_types]
    if not names:
        msg = "the profile has no sample types"
        raise PprofFormatError(msg)
    if wanted is None:
        if len(names) == 1:
            return 0
        if "cpu" in names:
            return names.index("cpu")
        msg = f"the profile has sample types {names}; choose one"
        raise PprofFormatError(msg)
    if wanted not in names:
        msg = f"the profile has sample types {names}, not {wanted!r}"
        raise PprofFormatError(msg)
    return names.index(wanted)


def fold_pprof(
    profile: Path,
    out: TextIO,
    *,
    sample_type: str | None = None,
    project_root: Path | None = None,
    path_map: Sequence[tuple[str, str]] = (),
    own_frames: bool = False,
) -> int:
    """Convert a pprof profile to folded stacks.

    Parameters
    ----------
    profile : pathlib.Path
        A ``profile.proto`` file, gzip-compressed or not, as Go's
        ``runtime/pprof`` and ``go test -cpuprofile`` write it.
    out : TextIO
        Receives one line per distinct stack, root first, frames joined
        by ``;``, then the stack's weight. A stack can appear on more
        than one line when the profile is large; the reader of folded
        stacks adds them.
    sample_type : str, optional
        The sample type to weight by (``cpu``, ``alloc_space``,
        ``delay``, ``contentions`` ...); with none, the profile's only
        type, else ``cpu``.
    project_root : pathlib.Path, optional
        The project, as it is on this machine; with its ``go.mod`` it
        maps a ``-trimpath`` file name to a path under it.
    path_map : sequence of (str, str), optional
        ``(profile prefix, local prefix)`` pairs for a profile written
        where the repository was mounted elsewhere.
    own_frames : bool, optional
        Cut each stack below its deepest frame in the project's own
        files, so the time of a library call (``runtime.mallocgc``,
        ``memmove``, a map access) is charged to the project line that
        made it. A stack with no frame of the project's is kept whole.
        Needs ``project_root``. The result is not a profile of where
        the CPU is, but of which project code it is spent for.

    Returns
    -------
    int
        The total weight written, in the sample type's unit.

    Raises
    ------
    ValueError
        If ``own_frames`` is set and ``project_root`` is not.
    PprofFormatError
        If the file is not a readable profile, names a location,
        function or string it does not define, or has no such sample
        type.

    Notes
    -----
    Time is linear in the file's size. Memory is the profile's
    location, function and string tables plus at most about 32 MiB of
    distinct stacks; samples are read one at a time.
    """
    if own_frames and project_root is None:
        msg = "own_frames needs project_root"
        raise ValueError(msg)
    tables = _read_tables(profile)
    index = _sample_index(tables, sample_type)
    module = module_path(project_root) if project_root is not None else None
    frames = _Frames(tables, _PathMapper(project_root, path_map, module))
    counts: dict[str, int] = {}
    held = 0
    total = 0
    try:
        with _open(profile) as stream:
            for number, _, value in _Messages(stream):
                if number != _PROFILE_SAMPLE or not isinstance(value, bytes):
                    continue
                weight, stack = _sample(value, index, frames, own_frames)
                if weight <= 0 or not stack:
                    continue
                total += weight
                if stack not in counts:
                    held += len(stack) + _STACK_OVERHEAD
                counts[stack] = counts.get(stack, 0) + weight
                if held >= _FLUSH_BYTES:
                    _write(counts, out)
                    held = 0
    except (OSError, EOFError, gzip.BadGzipFile) as exc:
        msg = f"{profile} cannot be read as a pprof profile: {exc}"
        raise PprofFormatError(msg) from exc
    _write(counts, out)
    return total


def _sample(
    data: bytes, index: int, frames: _Frames, own_frames: bool
) -> tuple[int, str]:
    """Return one sample's weight and its folded stack, root first."""
    locations: list[int] = []
    values: list[int] = []
    for number, _, value in _fields(data):
        if number == _SAMPLE_LOCATION_ID:
            locations.extend(_packed(value))
        elif number == _SAMPLE_VALUE:
            values.extend(_signed(v) for v in _packed(value))
    if index >= len(values):
        msg = "a sample has fewer values than the profile has sample types"
        raise PprofFormatError(msg)
    leaf_first: list[tuple[str, bool]] = []
    for location in locations:
        leaf_first.extend(frames.of(location))
    if own_frames:
        for at, (_, own) in enumerate(leaf_first):
            if own:
                del leaf_first[:at]
                break
    return values[index], ";".join(text for text, _ in reversed(leaf_first))


def _write(counts: dict[str, int], out: TextIO) -> None:
    for stack, weight in counts.items():
        out.write(f"{stack} {weight}\n")
    counts.clear()


def _flags(
    kind: ProfileKind, target: str, mem_rate: int, *, test_binary: bool
) -> list[str]:
    """Return the ``go test`` or test-binary flags for one profile."""
    prefix = "-test." if test_binary else "-"
    option = {
        "cpu": [("cpuprofile", target)],
        "mem": [("memprofile", target), ("memprofilerate", str(mem_rate))],
        "mutex": [("mutexprofile", target), ("mutexprofilefraction", "1")],
        "block": [("blockprofile", target), ("blockprofilerate", "1")],
    }[kind]
    if test_binary:
        return [f"{prefix}{name}={value}" for name, value in option]
    return [
        word for name, value in option for word in (f"{prefix}{name}", value)
    ]


def _launch(
    command: Sequence[str],
    kind: ProfileKind,
    target: str,
    mem_rate: int,
    directory: str,
) -> tuple[list[str], str]:
    """Return the argv to run, and how the profile gets switched on."""
    name = Path(command[0]).name
    args = list(command)
    if name == "go" and args[1:2] == ["test"]:
        extra = _flags(kind, target, mem_rate, test_binary=False)
        if not any(a == "-o" or a.startswith("-o=") for a in args[2:]):
            extra += ["-o", directory.rstrip("/") + "/"]
        return [*args[:2], *extra, *args[2:]], "go test"
    if name.endswith(".test") or any(a.startswith("-test.") for a in args[1:]):
        return [*args, *_flags(kind, target, mem_rate, test_binary=True)], (
            "a test binary"
        )
    return args, "the program"


def _to_visible(host: Path, path_map: Sequence[tuple[str, str]]) -> str:
    """Spell a local path as the command sees it."""
    text = host.as_posix()
    for src, dst in path_map:
        local = dst.rstrip("/")
        if text == local or text.startswith(local + "/"):
            return src.rstrip("/") + text[len(local) :]
    return text


def record(
    command: Sequence[str],
    output: Path,
    *,
    profile: ProfileKind = "cpu",
    sample_type: str | None = None,
    project_root: Path | None = None,
    path_map: Sequence[tuple[str, str]] = (),
    profile_dir: Path | None = None,
    mem_rate: int = DEFAULT_MEM_RATE,
    timeout_seconds: float = 3600.0,
    own_frames: bool = False,
) -> int:
    """Run a command under a Go profile and write folded stacks.

    Parameters
    ----------
    command : sequence of str
        The benchmark's argv. Its stdout and stderr are inherited.
    output : pathlib.Path
        Folded stacks are written here. The raw profile is written to
        ``profile_dir/<profile>.pprof`` and kept.
    profile : {"cpu", "mem", "mutex", "block"}, optional
        Which profile to switch on.
    sample_type : str, optional
        Sample type to weight by; the kind's default
        (`DEFAULT_SAMPLE_TYPE`) when omitted.
    project_root : pathlib.Path, optional
        Root that frames are shown relative to; the working directory
        when omitted.
    path_map : sequence of (str, str), optional
        ``(path in the command, path here)`` pairs. A command that runs
        in a container with the repository mounted at ``/src`` writes
        files named ``/src/...``: ``("/src", "<repo root>")`` shows them
        under the repository, and a profile directory under the
        repository is spelled ``/src/...`` for the command.
    profile_dir : pathlib.Path, optional
        Where the raw profile (and ``go test``'s binary) go; a new
        directory ``<output>.pprof.d`` when omitted. It must not exist
        yet, so a profile of an earlier run is never read. It must be
        visible to the command (inside the mount, for a container).
    mem_rate : int, optional
        ``-memprofilerate`` in bytes, for ``profile="mem"``.
    timeout_seconds : float, optional
        Time limit for the command; its whole process tree is stopped
        and killed with ``SIGKILL`` when it is exceeded.
    own_frames : bool, optional
        Charge library time to the project line that called it; see
        `fold_pprof`.

    Returns
    -------
    int
        The command's exit status when it is non-zero, 124 on a
        timeout, 5 when the command wrote no profile, 6 when the
        profile cannot be read or holds no samples, 7 when
        ``profile_dir`` exists, else 0. The reason goes to stderr.

    Raises
    ------
    ValueError
        If ``command`` is empty, or ``mem_rate`` is less than 1.
    """
    if not command:
        msg = "command is empty"
        raise ValueError(msg)
    if mem_rate < 1:
        msg = "mem_rate must be at least 1"
        raise ValueError(msg)
    directory = profile_dir or output.with_name(output.name + ".pprof.d")
    try:
        directory.mkdir(parents=True)
    except FileExistsError:
        _say(
            f"{directory} exists, from an earlier run; use a fresh output path"
        )
        return _EXIT_STALE
    raw = directory / f"{profile}.pprof"
    visible = _to_visible(raw, path_map)
    argv, how = _launch(
        command, profile, visible, mem_rate, _to_visible(directory, path_map)
    )
    env = dict(os.environ)
    env[ENV_PATH] = visible
    env[ENV_KIND] = profile
    status, timed_out = run_command(argv, env, timeout_seconds)
    if timed_out:
        return _EXIT_TIMEOUT
    if status != 0:
        return status
    if not raw.is_file():
        _say(
            f"{how} wrote no profile to {raw}: "
            + (
                "it started no test binary that ran (a build failure, or "
                "no benchmark matched)"
                if how != "the program"
                else "the program did not write the file named by "
                f"{ENV_PATH}; add the helper in skills/perfproof/go.md"
            )
        )
        return _EXIT_NO_PROFILE
    root = project_root or Path.cwd()
    try:
        with output.open("w", encoding="utf-8") as out:
            total = fold_pprof(
                raw,
                out,
                sample_type=sample_type or DEFAULT_SAMPLE_TYPE[profile],
                project_root=root,
                path_map=path_map,
                own_frames=own_frames,
            )
    except PprofFormatError as exc:
        _say(str(exc))
        return _EXIT_BAD_PROFILE
    if total == 0:
        _say(
            "the profile holds no samples: the program ran too briefly, "
            "or only waited; run the workload longer"
        )
        return _EXIT_BAD_PROFILE
    if profile == "cpu" and total < _CPU_WARN_NS:
        _say(
            f"only {total / 1e9:.2f} s of CPU time was sampled, at 100 "
            "samples a second: a small share is a few samples; run the "
            "workload longer"
        )
    return 0


def run_command(
    command: Sequence[str], env: dict[str, str], timeout_seconds: float
) -> tuple[int, bool]:
    """Run a command; return ``(status, timed_out)``.

    On the timeout the command and every descendant it started (``go
    run`` starts the program as a child; a shell starts what it runs)
    are stopped, then killed with ``SIGKILL``, so a process that keeps
    forking cannot get away.

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
    try:
        process = subprocess.Popen(list(command), env=env)
    except OSError as exc:
        _say(f"{command[0]} could not start: {exc}")
        return 127, False
    try:
        return process.wait(timeout=timeout_seconds), False
    except subprocess.TimeoutExpired:
        freeze_and_kill(process.pid, _say)
        process.wait()
        _say(f"the command ran past {timeout_seconds:g} s and was killed")
        return _EXIT_TIMEOUT, True


def _pair(text: str) -> tuple[str, str]:
    src, sep, dst = text.partition("=")
    if not sep or not src or not dst:
        msg = f"{text!r} is not of the form FROM=TO"
        raise argparse.ArgumentTypeError(msg)
    return src, dst


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``record`` or ``convert`` command line.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments after the program name; ``sys.argv[1:]`` when
        omitted.

    Returns
    -------
    int
        The exit status; see `record`.
    """
    parser = argparse.ArgumentParser(
        prog="python -m perfproof.profilers.go",
        description="Run a Go command under pprof, or convert a profile.",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    rec = sub.add_parser("record", help="run a command under a profile")
    conv = sub.add_parser("convert", help="convert a pprof file")
    for each in (rec, conv):
        each.add_argument("--output", required=True, type=Path)
        each.add_argument("--sample-type", default=None)
        each.add_argument("--project-root", type=Path, default=None)
        each.add_argument(
            "--path-map", type=_pair, action="append", default=[]
        )
        each.add_argument("--own-frames", action="store_true")
    rec.add_argument("--profile", choices=PROFILE_KINDS, default="cpu")
    rec.add_argument("--profile-dir", type=Path, default=None)
    rec.add_argument("--mem-rate", type=int, default=DEFAULT_MEM_RATE)
    rec.add_argument("--timeout", type=float, default=3600.0)
    rec.add_argument("command", nargs=argparse.REMAINDER)
    conv.add_argument("--input", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.action == "convert":
        try:
            with args.output.open("w", encoding="utf-8") as out:
                fold_pprof(
                    args.input,
                    out,
                    sample_type=args.sample_type,
                    project_root=args.project_root,
                    path_map=args.path_map,
                    own_frames=args.own_frames,
                )
        except (PprofFormatError, ValueError) as exc:
            _say(str(exc))
            return _EXIT_BAD_PROFILE
        return 0
    command: list[str] = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("give the command after --")
    resolved = shutil.which(command[0])
    if resolved is None and "/" not in command[0]:
        _say(f"{command[0]} is not on the PATH")
        return 127
    return record(
        command,
        args.output,
        profile=args.profile,
        sample_type=args.sample_type,
        project_root=args.project_root,
        path_map=args.path_map,
        profile_dir=args.profile_dir,
        mem_rate=args.mem_rate,
        timeout_seconds=args.timeout,
        own_frames=args.own_frames,
    )


if __name__ == "__main__":
    sys.exit(main())
