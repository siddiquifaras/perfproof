"""Profile Node.js and TypeScript programs into folded stacks.

``perfproof profile`` reads folded stacks and has no adapter per
profiler; a profiler plugs in as a ``--profiler-command`` that writes
them. This module is that command for Node::

    python -m perfproof.profilers.node run
        --output {output} -- {command}

``run`` starts the benchmark's own command with a preload
(``node_preload.mjs``, added to ``NODE_OPTIONS``) that samples every
Node process and worker thread it starts with V8's CPU profiler, then
turns what they wrote into one folded-stack file. A launcher such as
``npx``, ``tsx`` or ``node_modules/.bin/vitest`` that starts a second
Node is followed, because ``NODE_OPTIONS`` is inherited; each process
is the root frame of its own stacks, ``process <pid>:"<argv>"``, so
the launcher's share can be told from the program's. The per-process
profiles are kept untouched beside the output, in ``<output>.raw/``.

``convert`` does the second half alone, for ``.cpuprofile`` files that
``node --cpu-prof`` wrote. Those carry no source-map information, so a
frame in a TypeScript or JSX file (``.ts``, ``.tsx``, ``.mts``,
``.cts``, ``.jsx``) gets a line only when the file proves Node ran it
as written: the function's name, at least three characters, stands in
the file at the position V8 gives. Otherwise it gets no line rather
than a wrong one.

Why a preload and not ``--cpu-prof``: ``tsx``, ``esbuild-register``,
``ts-node`` and ``@swc-node/register`` compile in memory, and V8
reports positions in the compiled code. ``tsx`` puts all of a file on
its first line, so every frame of ``src/context.ts`` would read
``context.ts:1``. The preload also records each script's source-map
URL (inline for all four loaders, a ``.map`` file beside a bundle), and
the positions are mapped back to the TypeScript. Code with no map that
Node ran itself (``node --experimental-strip-types``, on by default in
Node 24.13, where it was run) is checked against the file as above.

What a line means
-----------------
A frame's line is where the function starts (V8 knows the function, not
the call site). The function that was running when a sample was taken
gets, instead, the line the sample landed on, when V8 recorded one
(``positionTicks``): its self time is split between those lines in
proportion to the ticks. In compiled code that line is mapped through
the source map to the first mapped position of the generated line; when
the sample and the function start share a generated line (everything
minified onto one line, as ``tsx`` does) there is nothing finer than
the function start and that is what is used.

Weights are microseconds: each sample stands for the time until the
next one. ``(idle)`` (the event loop waiting) and ``(root)`` are
dropped unless ``--idle`` is given, so a share is of time the process
was running, as with py-spy; wall time against CPU time in ``perfproof
profile`` shows the waiting. ``(garbage collector)`` and ``(program)``
(native code outside JavaScript) are kept.

Only the standard library and `perfproof.profilers._kill` (itself
standard library only) are imported, so the command starts fast.
"""

import argparse
import base64
import bisect
import json
import os
import posixpath
import re
import subprocess
import sys
import urllib.parse
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TextIO, cast

from perfproof.profilers._kill import freeze_and_kill

__all__ = [
    "PRELOAD",
    "NodeProfileError",
    "SourceMap",
    "convert_profiles",
    "decode_mappings",
    "decode_vlq",
    "main",
]

PRELOAD: Final = Path(__file__).with_name("node_preload.mjs")
"""The ``--import`` preload that samples a Node process."""

_PROFILE_DIR_ENV: Final = "PERFPROOF_NODE_PROFILE_DIR"
_INTERVAL_ENV: Final = "PERFPROOF_NODE_INTERVAL_US"
_DEFAULT_INTERVAL_US: Final = 1000
_DEFAULT_TIMEOUT_SECONDS: Final = 3600.0
_CHUNK_CHARS: Final = 1 << 20
_MAX_BUFFER_CHARS: Final = 1 << 29
"""Longest single JSON value the reader will hold, in characters."""
_MAX_MAP_BYTES: Final = 1 << 28
_PROFILE_SUFFIX: Final = ".nodeprof.json"
_NEEDS_TRANSFORM: Final = frozenset({".ts", ".tsx", ".mts", ".cts", ".jsx"})
_DROPPED: Final = frozenset({"(root)"})
_IDLE: Final = "(idle)"
_BASE64_DIGITS: Final = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
)
_BASE64_VALUE: Final = {c: i for i, c in enumerate(_BASE64_DIGITS)}
_ARGV_CHARS: Final = 200
_MIN_PROVEN_NAME: Final = 3
_MAX_SOURCE_BYTES: Final = 1 << 24
_LINE_BREAK: Final = re.compile(r"\r\n|\r|\n|\u2028|\u2029")


class NodeProfileError(Exception):
    """Profiles that cannot be converted, or a run that wrote none."""


@dataclass(frozen=True, slots=True)
class _Call:
    """A V8 call frame: one function in one script."""

    name: str
    script_id: str
    url: str
    line: int
    column: int


class _Tree:
    """One profile's call tree, as compact as it can be kept."""

    def __init__(self) -> None:
        self.calls: list[_Call] = []
        self.call_index: dict[_Call, int] = {}
        self.node_call: list[int] = []
        self.node_self: list[int] = []
        self.node_ticks: list[tuple[tuple[int, int], ...]] = []
        self.children: list[list[int]] = []
        self.ids: dict[int, int] = {}
        self.weight_unit = "microseconds"


class SourceMap:
    """A decoded source map: generated position to original position.

    Parameters
    ----------
    document : dict
        The parsed source map, version 3, without index sections.
    base : str
        Absolute path of the directory its ``sources`` are relative to.

    Attributes
    ----------
    sources : list of str
        Each source as a path (or URL), resolved against ``base``.
    lines : list of list of tuple of int
        `decode_mappings` of the map, indexed by generated line.
    """

    def __init__(self, document: dict[str, Any], base: str) -> None:
        root = str(document.get("sourceRoot") or "")
        raw = cast("list[object]", document.get("sources") or [])
        self.sources = [_source_path(base, root, entry) for entry in raw]
        self._columns: dict[int, list[int]] = {}
        self.lines = decode_mappings(str(document.get("mappings") or ""))

    def lookup(self, line: int, column: int) -> tuple[str, int] | None:
        """Map a 0-based generated position to ``(file, 1-based line)``.

        The segment at or before the column on that generated line
        answers; when the line's first segment starts after the
        column, the first segment does. ``None`` when the line has no
        mapping.
        """
        if not 0 <= line < len(self.lines):
            return None
        segments = self.lines[line]
        if not segments:
            return None
        columns = self._columns.get(line)
        if columns is None:
            columns = [segment[0] for segment in segments]
            self._columns[line] = columns
        index = bisect.bisect_right(columns, column) - 1
        _, source, original_line, _ = segments[max(index, 0)]
        if not 0 <= source < len(self.sources) or not self.sources[source]:
            return None
        return self.sources[source], original_line + 1


def _source_path(base: str, root: str, entry: object) -> str:
    """Resolve one ``sources`` entry to a path or URL."""
    text = str(entry)
    if text.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(text).path)
    if "://" in text or text.startswith(("data:", "webpack:")):
        return text
    joined = posixpath.join(root, text) if root else text
    if joined.startswith("/"):
        return posixpath.normpath(joined)
    return posixpath.normpath(posixpath.join(base, joined))


def decode_mappings(text: str) -> list[list[tuple[int, int, int, int]]]:
    """Decode a source map's ``mappings`` into one list per line.

    Parameters
    ----------
    text : str
        The ``mappings`` string: lines separated by ``;``, segments by
        ``,``, each segment base64 VLQ fields relative to the one
        before.

    Returns
    -------
    list of list of tuple of int
        One list per generated line; each segment is ``(generated
        column, source, line, column)``, all 0-based. Segments with no
        source are left out. Linear in the length of ``text``.
    """
    lines: list[list[tuple[int, int, int, int]]] = []
    source = original_line = original_column = 0
    for generated in text.split(";"):
        segments: list[tuple[int, int, int, int]] = []
        column = 0
        for part in generated.split(",") if generated else ():
            values = decode_vlq(part)
            if not values:
                continue
            column += values[0]
            if len(values) < 4:
                continue
            source += values[1]
            original_line += values[2]
            original_column += values[3]
            segments.append((column, source, original_line, original_column))
        lines.append(segments)
    return lines


def decode_vlq(part: str) -> list[int]:
    """Decode one base64 VLQ segment into its signed integers.

    Parameters
    ----------
    part : str
        One segment of a ``mappings`` string.

    Returns
    -------
    list of int
        Its fields; empty when ``part`` has a character that is not
        base64.
    """
    values: list[int] = []
    shift = value = 0
    for char in part:
        digit = _BASE64_VALUE.get(char)
        if digit is None:
            return []
        value += (digit & 31) << shift
        if digit & 32:
            shift += 5
            continue
        values.append(-(value >> 1) if value & 1 else value >> 1)
        shift = value = 0
    return values


class _Scripts:
    """A process's scripts' source maps, read when first needed."""

    def __init__(self, scripts: dict[str, dict[str, str]]) -> None:
        self._scripts = scripts
        self._maps: dict[str, SourceMap | None] = {}

    def source_map(self, script_id: str) -> SourceMap | None:
        """Return a script's source map, or ``None`` if it has none."""
        if script_id not in self._maps:
            self._maps[script_id] = self._load(script_id)
        return self._maps[script_id]

    def _load(self, script_id: str) -> SourceMap | None:
        info = self._scripts.get(script_id)
        if info is None or not info.get("sourceMapURL"):
            return None
        reference = info["sourceMapURL"]
        script = _file_path(info.get("url", ""))
        base = posixpath.dirname(script) if script else ""
        try:
            if reference.startswith("data:"):
                document = _data_url_json(reference)
            elif script is None:
                return None
            else:
                target = Path(
                    posixpath.join(
                        base, urllib.parse.unquote(reference.split("?")[0])
                    )
                )
                if target.stat().st_size > _MAX_MAP_BYTES:
                    return None
                document = json.loads(target.read_text(encoding="utf-8"))
                base = str(target.parent)
            if not isinstance(document, dict) or "sections" in document:
                return None
            return SourceMap(cast("dict[str, Any]", document), base)
        except (OSError, ValueError):
            return None


def _data_url_json(url: str) -> object:
    """Parse the JSON in a ``data:application/json`` URL."""
    head, _, body = url.partition(",")
    raw = (
        base64.b64decode(body)
        if head.endswith(";base64")
        else urllib.parse.unquote_to_bytes(body)
    )
    return json.loads(raw.decode("utf-8"))


def _file_path(url: str) -> str | None:
    """Return the path of a ``file:`` URL or an absolute path."""
    if url.startswith("file://"):
        return urllib.parse.unquote(urllib.parse.urlparse(url).path)
    if url.startswith("/"):
        return url
    return None


class _Reader:
    """Read the top-level values of a JSON object a piece at a time.

    The profile files are one object whose largest value is an array of
    call-tree nodes. This yields the array's elements one by one and
    never holds more than a chunk and the element being decoded, so a
    profile of any size is read in memory proportional to its largest
    node. The ``scripts`` object, however, is decoded whole, and a value
    larger than one chunk is re-parsed after each chunk, so time grows
    about quadratically with its size (not measured here).
    """

    def __init__(self, handle: TextIO) -> None:
        self._handle = handle
        self._buffer = ""
        self._position = 0
        self._eof = False
        self._decoder = json.JSONDecoder()

    def _fill(self) -> bool:
        if self._eof:
            return False
        chunk = self._handle.read(_CHUNK_CHARS)
        if not chunk:
            self._eof = True
            return False
        self._buffer = self._buffer[self._position :] + chunk
        self._position = 0
        if len(self._buffer) > _MAX_BUFFER_CHARS:
            msg = "a JSON value is larger than the reader's limit"
            raise NodeProfileError(msg)
        return True

    def _peek(self) -> str:
        """Return the next non-space character, or ``""`` at the end."""
        while True:
            while self._position < len(self._buffer):
                char = self._buffer[self._position]
                if char not in " \t\r\n":
                    return char
                self._position += 1
            if not self._fill():
                return ""

    def _expect(self, char: str) -> None:
        if self._peek() != char:
            msg = f"expected {char!r} in a Node profile"
            raise NodeProfileError(msg)
        self._position += 1

    def _value(self) -> object:
        """Decode the next value, reading more input until it ends."""
        self._peek()
        while True:
            try:
                value, end = self._decoder.raw_decode(
                    self._buffer, self._position
                )
            except json.JSONDecodeError:
                if not self._fill():
                    msg = "a Node profile is not valid JSON"
                    raise NodeProfileError(msg) from None
                continue
            # A number or literal that touches the end of the buffer
            # may continue in the next chunk.
            if end >= len(self._buffer) and self._fill():
                continue
            self._position = end
            return value

    def items(self) -> Iterator[tuple[str, object]]:
        """Yield ``(key, value)`` of the top-level object.

        The ``nodes`` array is yielded element by element as
        ``("nodes", node)``. Reading stops at ``samples`` and
        ``timeDeltas``, which a raw ``--cpu-prof`` file puts after its
        nodes and which are not needed.
        """
        self._expect("{")
        while self._peek() not in ("}", ""):
            key = self._value()
            if not isinstance(key, str):
                msg = "a Node profile has a key that is not a string"
                raise NodeProfileError(msg)
            self._expect(":")
            if key in ("samples", "timeDeltas"):
                return
            if key != "nodes":
                yield key, self._value()
            else:
                self._expect("[")
                while self._peek() != "]":
                    yield key, self._value()
                    if self._peek() == ",":
                        self._position += 1
                self._position += 1
            if self._peek() == ",":
                self._position += 1


def _read_tree(path: Path) -> tuple[dict[str, Any], _Scripts, _Tree]:
    """Read one profile file into a compact call tree."""
    meta: dict[str, Any] = {}
    scripts: dict[str, dict[str, str]] = {}
    tree = _Tree()
    pending: list[tuple[int, list[int]]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for key, value in _Reader(handle).items():
                if key == "meta":
                    meta = cast("dict[str, Any]", value)
                elif key == "scripts":
                    scripts = cast("dict[str, dict[str, str]]", value)
                elif key == "nodes":
                    pending.append(_add_node(tree, value))
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"{path} cannot be read: {exc}"
        raise NodeProfileError(msg) from exc
    if not tree.node_call:
        msg = f"{path} holds no profile nodes"
        raise NodeProfileError(msg)
    for index, ids in pending:
        try:
            tree.children[index] = [tree.ids[i] for i in ids]
        except KeyError as exc:
            msg = f"{path}: a node names a child {exc} that is not there"
            raise NodeProfileError(msg) from exc
    return meta, _Scripts(scripts), tree


def _add_node(tree: _Tree, value: object) -> tuple[int, list[int]]:
    """Add one profile node; return its index and its children's ids."""
    if not isinstance(value, dict):
        msg = "a Node profile has a node that is not an object"
        raise NodeProfileError(msg)
    node = cast("dict[str, Any]", value)
    try:
        frame = node["callFrame"]
        call = _Call(
            str(frame.get("functionName", "")),
            str(frame.get("scriptId", "")),
            str(frame.get("url", "")),
            int(frame.get("lineNumber", -1)),
            int(frame.get("columnNumber", -1)),
        )
        identity = int(node["id"])
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        msg = f"a Node profile node is malformed: {exc!r}"
        raise NodeProfileError(msg) from exc
    call_id = tree.call_index.get(call)
    if call_id is None:
        call_id = len(tree.calls)
        tree.calls.append(call)
        tree.call_index[call] = call_id
    if "selfMicros" in node:
        weight = int(node["selfMicros"])
    else:
        weight = int(node.get("hitCount", 0))
        tree.weight_unit = "samples"
    ticks = tuple(
        (int(t["line"]), int(t["ticks"]))
        for t in node.get("positionTicks") or ()
        if int(t["ticks"]) > 0
    )
    index = len(tree.node_call)
    tree.ids[identity] = index
    tree.node_call.append(call_id)
    tree.node_self.append(max(weight, 0))
    tree.node_ticks.append(ticks)
    tree.children.append([])
    return index, [int(c) for c in node.get("children") or ()]


class _Frames:
    """Turn call frames into folded-stack frame text, with caches."""

    def __init__(self, tree: _Tree, scripts: _Scripts) -> None:
        self._tree = tree
        self._scripts = scripts
        self._function: dict[int, str] = {}
        self._as_written: dict[int, bool] = {}
        self._sources: dict[str, list[str] | None] = {}

    def function(self, call_id: int) -> str:
        """Frame text of a function, located at where it starts."""
        text = self._function.get(call_id)
        if text is None:
            call = self._tree.calls[call_id]
            text = self._text(call, call.line, call.column)
            self._function[call_id] = text
        return text

    def at_line(self, call_id: int, line: int) -> str:
        """Frame text of a function at a sampled 1-based line."""
        call = self._tree.calls[call_id]
        if line - 1 == call.line:
            return self.function(call_id)
        return self._text(call, line - 1, 0)

    def _text(self, call: _Call, line: int, column: int) -> str:
        name = _clean(call.name or "(anonymous)")
        if not call.url or call.line < 0:
            return name
        path = _file_path(call.url)
        shown = path if path is not None else call.url.split("#")[0]
        number: int | None = line + 1
        source_map = self._scripts.source_map(call.script_id)
        if source_map is not None:
            found = source_map.lookup(line, column)
            if found is None:
                number = None
            else:
                shown, number = found
        elif posixpath.splitext(shown)[1] in _NEEDS_TRANSFORM and (
            not self._runs_as_written(call, shown)
        ):
            number = None
        return f"{name} ({shown}:{number})" if number else f"{name} ({shown})"

    def _runs_as_written(self, call: _Call, path: str) -> bool:
        """Whether Node ran this TypeScript file as it is on disk.

        With no source map, V8's positions are the file's own only when
        nothing rewrote it (``node --experimental-strip-types`` blanks
        the types and keeps every position). The file proves it: the
        function's name must stand at the position V8 gives, which is
        just after the name. A short or missing name proves nothing.
        """
        key = self._tree.call_index[call]
        if key not in self._as_written:
            self._as_written[key] = self._name_at(call, path)
        return self._as_written[key]

    def _name_at(self, call: _Call, path: str) -> bool:
        name = call.name
        if len(name) < _MIN_PROVEN_NAME:
            return False
        if path not in self._sources:
            self._sources[path] = _read_lines(path)
        lines = self._sources[path]
        if lines is None or not 0 <= call.line < len(lines):
            return False
        text = lines[call.line]
        start = max(call.column - len(name) - 1, 0)
        return name in text[start : call.column + len(name) + 1]


def _read_lines(path: str) -> list[str] | None:
    """Read a file as V8 counts lines; ``None`` if it cannot be read."""
    try:
        if os.stat(path).st_size > _MAX_SOURCE_BYTES:
            return None
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _LINE_BREAK.split(text)


def _clean(text: str) -> str:
    """Make text safe inside a folded-stack frame."""
    return text.replace(";", ",").replace("\n", " ").replace("\r", " ")


def _stacks(
    tree: _Tree,
    frames: _Frames,
    root_text: str,
    *,
    idle: bool,
) -> Iterator[tuple[str, int]]:
    """Yield ``(stack, weight)`` for every node with time of its own.

    Depth-first from the roots, holding only the current path.
    """
    parents = [-1] * len(tree.node_call)
    for index, kids in enumerate(tree.children):
        for kid in kids:
            parents[kid] = index
    roots = [i for i, p in enumerate(parents) if p == -1]
    path: list[str] = [root_text]
    work: list[tuple[int, int]] = [(r, 1) for r in reversed(roots)]
    while work:
        index, keep = work.pop()
        del path[keep:]
        call_id = tree.node_call[index]
        name = tree.calls[call_id].name
        if name not in _DROPPED and (idle or name != _IDLE):
            path.append(frames.function(call_id))
            weight = tree.node_self[index]
            if weight > 0:
                yield from _leaves(tree, frames, index, path, weight)
        # A dropped node adds no frame, so its children share its depth.
        depth = len(path)
        work.extend((kid, depth) for kid in reversed(tree.children[index]))


def _leaves(
    tree: _Tree,
    frames: _Frames,
    index: int,
    path: list[str],
    weight: int,
) -> Iterator[tuple[str, int]]:
    """Split a node's own time between the lines its samples hit."""
    ticks = tree.node_ticks[index]
    total = sum(count for _, count in ticks)
    if total == 0:
        yield ";".join(path), weight
        return
    prefix = ";".join(path[:-1])
    call_id = tree.node_call[index]
    given = 0
    running = 0.0
    for line, count in ticks:
        running += weight * count / total
        share = round(running) - given
        given += share
        if share > 0:
            leaf = frames.at_line(call_id, line)
            yield (f"{prefix};{leaf}" if prefix else leaf), share


def convert_profiles(
    paths: Sequence[Path],
    output: Path,
    *,
    idle: bool = False,
) -> dict[str, int]:
    """Write one folded-stack file from Node profile files.

    Parameters
    ----------
    paths : sequence of pathlib.Path
        ``*.nodeprof.json`` files written by the preload, or
        ``.cpuprofile`` files from ``node --cpu-prof``. Each is one
        process or thread; its stacks start with a
        ``process <pid>:"<argv>"`` frame (``process <file>`` for a
        ``.cpuprofile``, which does not say).
    output : pathlib.Path
        The folded-stack file to write; an existing file is replaced.
    idle : bool, optional
        Keep ``(idle)``, the event loop waiting. Dropped by default.

    Returns
    -------
    dict of str to int
        ``samples``-like totals: ``weight`` (all stack weights
        written), ``stacks`` (distinct stacks) and ``processes``.

    Raises
    ------
    NodeProfileError
        If a file cannot be read or is not a profile, or every stack
        is empty.

    Notes
    -----
    Each file is read in a stream; memory is proportional to its
    number of call-tree nodes, not to its size. Time is linear in the
    input plus the distinct stacks' length.
    """
    merged: dict[str, int] = {}
    for path in paths:
        meta, scripts, tree = _read_tree(path)
        frames = _Frames(tree, scripts)
        argv = " ".join(str(a) for a in meta.get("argv", ()))
        label = (
            f'process {meta["pid"]}:"{_clean(argv)[:_ARGV_CHARS]}"'
            if "pid" in meta
            else f"process {_clean(path.name)}"
        )
        for stack, weight in _stacks(tree, frames, label, idle=idle):
            merged[stack] = merged.get(stack, 0) + weight
    if not merged:
        msg = "the profiles hold no samples with time of their own"
        raise NodeProfileError(msg)
    with output.open("w", encoding="utf-8") as sink:
        for stack, weight in sorted(merged.items(), key=lambda s: -s[1]):
            sink.write(f"{stack} {weight}\n")
    return {
        "weight": sum(merged.values()),
        "stacks": len(merged),
        "processes": len(paths),
    }


def _say(message: str) -> None:
    """Say something on stderr, with this command's prefix."""
    sys.stderr.write(f"perfproof node: {message}\n")


def _run(arguments: argparse.Namespace) -> int:
    """Run a command under the preload and write its folded stacks."""
    command: list[str] = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        sys.stderr.write("perfproof node: no command to run\n")
        return 2
    output = Path(arguments.output)
    raw = output.with_name(output.name + ".raw")
    try:
        raw.mkdir()
    except OSError as exc:
        sys.stderr.write(f"perfproof node: cannot create {raw}: {exc}\n")
        return 2
    env = dict(os.environ)
    preload = f"--import={PRELOAD.as_uri()}"
    env["NODE_OPTIONS"] = " ".join(
        part for part in (env.get("NODE_OPTIONS", ""), preload) if part
    )
    env[_PROFILE_DIR_ENV] = str(raw)
    env[_INTERVAL_ENV] = str(arguments.interval_us)
    try:
        process = subprocess.Popen(command, env=env)
    except OSError as exc:
        sys.stderr.write(f"perfproof node: {command[0]} failed: {exc}\n")
        return 2
    try:
        status = process.wait(timeout=arguments.timeout)
    except subprocess.TimeoutExpired:
        # The whole tree: a launcher's children outlive it otherwise.
        freeze_and_kill(process.pid, _say)
        process.wait()
        _say(f"the command ran past {arguments.timeout:g} s and was killed")
        return 2
    if status != 0:
        return status
    files = sorted(raw.glob(f"*{_PROFILE_SUFFIX}"))
    if not files:
        sys.stderr.write(
            "perfproof node: the command wrote no Node profile; it "
            "started no Node process (a native or Python program), or "
            "every Node it started was killed by a signal\n"
        )
        return 3
    try:
        convert_profiles(files, output, idle=arguments.idle)
    except NodeProfileError as exc:
        sys.stderr.write(f"perfproof node: {exc}\n")
        return 3
    return 0


def _convert(arguments: argparse.Namespace) -> int:
    """Convert profile files that already exist."""
    try:
        convert_profiles(
            [Path(p) for p in arguments.profiles],
            Path(arguments.output),
            idle=arguments.idle,
        )
    except NodeProfileError as exc:
        sys.stderr.write(f"perfproof node: {exc}\n")
        return 3
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m perfproof.profilers.node",
        description=__doc__.split("\n\n")[0] if __doc__ else None,
    )
    sub = parser.add_subparsers(dest="mode", required=True)
    run = sub.add_parser("run", help="profile a command's Node processes")
    run.add_argument("--output", required=True, help="folded-stack file")
    run.add_argument(
        "--interval-us",
        type=int,
        default=_DEFAULT_INTERVAL_US,
        help="sampling interval in microseconds (default 1000)",
    )
    run.add_argument("--idle", action="store_true", help="keep (idle)")
    run.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_SECONDS,
        help="seconds the command may run",
    )
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(handler=_run)
    convert = sub.add_parser("convert", help="convert .cpuprofile files")
    convert.add_argument("--output", required=True)
    convert.add_argument("--idle", action="store_true")
    convert.add_argument("profiles", nargs="+")
    convert.set_defaults(handler=_convert)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command line; return the process exit status.

    Parameters
    ----------
    argv : sequence of str, optional
        Arguments after the program name; ``sys.argv[1:]`` by default.

    Returns
    -------
    int
        0 on success; the command's own status when it fails; 2 when
        it cannot start, no command was given, or the command ran
        past ``--timeout`` and was killed; 3 when it ran but no usable
        profile came out of it.
    """
    arguments = _parser().parse_args(argv)
    handler = arguments.handler
    return int(handler(arguments))


if __name__ == "__main__":
    sys.exit(main())
