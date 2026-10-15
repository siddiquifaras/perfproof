"""Summarise folded stacks into a short ranked list of hotspots.

Folded stacks are the one profile format perfproof reads. Each line is
a stack, root first, frames joined by ``;``, then a space and an
integer sample count::

    main (/repo/run.py:14);slow_sum (/repo/app/kernels.py:18) 8

py-spy (``record --format raw``), Brendan Gregg's ``stackcollapse-*``
scripts and inferno's ``inferno-collapse-*`` converters all write it.
The input is read once, line by line, so a capture of any size is
summarised in memory proportional to its number of distinct frames,
not its length.

A frame's self count is the samples whose stack ends in it. Its total
count is the samples whose stack contains it, counting a stack once
however many times the frame recurs in it.

Frames are normalised before they are counted:

- Python frames (``function (file.py:line)``, as py-spy writes them)
  keep their file and line; a file under the project root becomes
  relative to it.
- Every other frame is native. A native frame is shown as
  ``module`symbol`` when the profiler names its binary, and never
  merges with a Python frame of the same name.
- A frame is marked as library code unless it is shown to be the
  project's: a file under the project root that is not in a
  ``site-packages`` or ``dist-packages`` directory, or a native frame
  of a binary the caller names as the project's own.
"""

import heapq
import os
import posixpath
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from perfproof.schema import Hotspot

__all__ = [
    "DEFAULT_TOP_N",
    "FoldedStackError",
    "summarise",
    "summarise_file",
]

DEFAULT_TOP_N: Final = 20
"""Number of hotspots returned when the caller does not say."""

_LIBRARY_DIRS: Final = frozenset({"site-packages", "dist-packages"})
_PYTHON_SUFFIXES: Final = (".py", ".pyw")
_LINE_SUFFIX: Final = re.compile(r"(?P<file>.+):(?P<line>[0-9]+)")
_FILE_SUFFIX: Final = re.compile(r"(\.so(\.[0-9]+)?|\.dylib|\.dll|\.pyw?)$")
_UNRESOLVED: Final = "?"
"""py-spy's location for a native frame it could not symbolise."""


class FoldedStackError(ValueError):
    """Folded stacks that cannot be summarised.

    Parameters
    ----------
    reason : str
        What is wrong.
    line_number : int or None, optional
        1-based input line the problem is on, or ``None`` when it
        concerns the input as a whole.
    """

    def __init__(self, reason: str, line_number: int | None = None) -> None:
        self.line_number = line_number
        where = "" if line_number is None else f"line {line_number}: "
        super().__init__(f"{where}{reason}")


@dataclass(frozen=True, slots=True)
class _Frame:
    """A normalised frame; equal frames are counted as one.

    ``location`` is part of the identity but not of the output, so two
    native symbols from different binaries with the same file name
    stay apart.
    """

    function: str
    file: str | None
    line: int | None
    frame_kind: Literal["python", "native"]
    is_library: bool
    location: str | None


class _Normaliser:
    """Turn raw frame text into `_Frame` values for one project."""

    def __init__(
        self, project_root: Path, own_modules: Collection[str]
    ) -> None:
        self._root = project_root
        prefixes = {
            posixpath.normpath(project_root.as_posix()),
            posixpath.normpath(project_root.resolve().as_posix()),
        }
        self._prefixes = tuple(sorted(p.rstrip("/") + "/" for p in prefixes))
        self._own_modules = frozenset(own_modules)
        self._exists: dict[str, bool] = {}

    def frame(self, text: str) -> _Frame:
        """Normalise one frame as the profiler wrote it."""
        split = _split_location(text)
        if split is not None:
            return self._located(*split)
        module, sep, symbol = text.partition("`")
        if sep and module and symbol:
            return _Frame(
                function=text,
                file=None,
                line=None,
                frame_kind="native",
                is_library=module not in self._own_modules,
                location=module,
            )
        return _Frame(text, None, None, "native", True, None)

    def _located(self, function: str, location: str) -> _Frame:
        """Normalise ``function (location)``, py-spy's frame form."""
        match = _LINE_SUFFIX.fullmatch(location)
        path = match["file"] if match else location
        line = int(match["line"]) if match else None
        if line == 0:
            line = None
        if path.startswith("<") and path.endswith(">"):
            return _Frame(function, path, line, "python", True, location)
        if path == _UNRESOLVED:
            return _Frame(function, None, None, "native", True, location)
        own, shown = self._classify_path(path)
        if path.endswith(_PYTHON_SUFFIXES):
            return _Frame(function, shown, line, "python", not own, location)
        module = posixpath.basename(path)
        own = own or module in self._own_modules
        if line is not None:
            return _Frame(function, shown, line, "native", not own, location)
        name = f"{module}`{function}" if module else function
        return _Frame(name, None, None, "native", not own, location)

    def _classify_path(self, path: str) -> tuple[bool, str]:
        """Say whether a path is the project's, and how to show it.

        Absolute paths are compared lexically with the project root,
        as given and resolved, so no file is touched. A relative path
        (py-spy shortens paths to their ``sys.path`` entry unless run
        with ``--full-filenames``) is the project's only if that file
        exists under the root.
        """
        clean = posixpath.normpath(path)
        if posixpath.isabs(clean):
            for prefix in self._prefixes:
                if clean.startswith(prefix):
                    relative = clean.removeprefix(prefix)
                    return _is_own(relative), relative
            return False, clean
        if clean.startswith("../") or clean == "..":
            return False, path
        own = self._exists.get(clean)
        if own is None:
            own = _is_own(clean) and (self._root / clean).is_file()
            self._exists[clean] = own
        return own, clean


def _split_location(text: str) -> tuple[str, str] | None:
    """Split py-spy's ``function (location)`` form, if text has it.

    Each `` (`` is tried from the left. The first one whose head has
    balanced round parentheses and whose rest is a location followed
    by ``)`` is the split. Demangled C++ symbols also contain `` (``
    (``f(int, (anonymous namespace)::Bar)``, ``g(void (*)(int))``) and
    paths may contain parentheses (``/home/u/proj (copy)/run.py``), so
    neither the first nor the last `` (`` will do on its own. ``<``
    and ``>`` are not brackets here: ``Foo::operator<`` is a whole
    name.

    Returns
    -------
    tuple of (str, str) or None
        The function and the location, or ``None`` when no split
        qualifies and the whole text is a symbol.
    """
    start = text.find(" (")
    while start != -1:
        head, rest = text[:start], text[start + 2 :]
        if (
            head
            and rest.endswith(")")
            and _balanced(head)
            and _is_location(rest[:-1])
        ):
            return head, rest[:-1]
        start = text.find(" (", start + 1)
    return None


def _is_location(text: str) -> bool:
    """Whether text is what py-spy puts in a frame's parentheses.

    That is ``?``; a ``<...>`` pseudo-file with no parentheses inside;
    ``path:line``, where the path may hold spaces and balanced
    parentheses; or a path or library file name with balanced
    parentheses, recognised by a ``/`` or by its suffix.
    """
    if text == _UNRESOLVED:
        return True
    if text.startswith("<") and text.endswith(">"):
        return not any(c in text for c in "()")
    match = _LINE_SUFFIX.fullmatch(text)
    if match is not None:
        return _balanced(match["file"])
    return _balanced(text) and (
        "/" in text or _FILE_SUFFIX.search(text) is not None
    )


def _balanced(text: str) -> bool:
    """Whether every round parenthesis in text is closed, in order."""
    depth = 0
    for c in text:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _is_own(relative: str) -> bool:
    """Whether a root-relative path is outside installed packages."""
    return _LIBRARY_DIRS.isdisjoint(relative.split("/"))


def summarise(
    lines: Iterable[str],
    *,
    project_root: Path,
    top_n: int = DEFAULT_TOP_N,
    own_modules: Collection[str] = (),
) -> tuple[Hotspot, ...]:
    """Rank the frames of folded stacks by self time.

    Parameters
    ----------
    lines : iterable of str
        Folded-stack lines, read once and in order. Trailing
        whitespace and line endings are ignored; blank lines are
        skipped. A line with an empty stack (py-spy writes `` 8``
        when it read no frames) counts towards the total but towards
        no frame.
    project_root : pathlib.Path
        Root of the project being profiled. Files under it are shown
        relative to it and count as project code.
    top_n : int, optional
        Most hotspots to return; at least 1.
    own_modules : collection of str, optional
        Names of native binaries (as the profiler names them, such as
        ``mylib.so`` or ``mybinary``) that are project code.

    Returns
    -------
    tuple of perfproof.schema.Hotspot
        Frames with a nonzero self count, by self count then total
        count, both descending; ties keep the order in which frames
        first appear. At most ``top_n``. Fractions are of all samples
        in the input.

    Raises
    ------
    FoldedStackError
        If a line has no space before its count, a count that is not
        a non-negative decimal integer, or an empty frame in a
        non-empty stack, or if the input holds no samples.
    ValueError
        If ``top_n`` is less than 1.

    Notes
    -----
    Time is linear in the input's size. Memory is linear in the number
    of distinct frames plus the deepest stack, and does not grow with
    the number of lines. Ranking is ``O(F log top_n)`` for ``F``
    distinct frames.
    """
    if top_n < 1:
        msg = f"top_n must be at least 1, got {top_n}"
        raise ValueError(msg)
    normaliser = _Normaliser(project_root, own_modules)
    ids: dict[str, int] = {}
    frame_ids: dict[_Frame, int] = {}
    frames: list[_Frame] = []
    self_counts: list[int] = []
    total_counts: list[int] = []
    samples = 0
    for number, raw in enumerate(lines, start=1):
        text = raw.rstrip()
        if not text:
            continue
        stack, space, count_text = text.rpartition(" ")
        if not space:
            reason = "expected a stack, a space and a sample count"
            raise FoldedStackError(reason, number)
        if not (count_text.isascii() and count_text.isdigit()):
            reason = f"sample count {count_text!r} is not an integer"
            raise FoldedStackError(reason, number)
        count = int(count_text)
        samples += count
        if not stack:
            continue
        stack_ids: list[int] = []
        for text_frame in stack.split(";"):
            frame_id = ids.get(text_frame)
            if frame_id is None:
                if not text_frame:
                    raise FoldedStackError("empty frame", number)
                frame = normaliser.frame(text_frame)
                frame_id = frame_ids.get(frame)
                if frame_id is None:
                    frame_id = len(frames)
                    frame_ids[frame] = frame_id
                    frames.append(frame)
                    self_counts.append(0)
                    total_counts.append(0)
                ids[text_frame] = frame_id
            stack_ids.append(frame_id)
        self_counts[stack_ids[-1]] += count
        for frame_id in set(stack_ids):
            total_counts[frame_id] += count
    if samples == 0:
        raise FoldedStackError("no samples in the input")
    ranked = heapq.nlargest(
        top_n,
        (i for i, n in enumerate(self_counts) if n > 0),
        key=lambda i: (self_counts[i], total_counts[i]),
    )
    return tuple(
        Hotspot(
            function=frames[i].function,
            file=frames[i].file,
            line=frames[i].line,
            frame_kind=frames[i].frame_kind,
            is_library=frames[i].is_library,
            self_fraction=self_counts[i] / samples,
            total_fraction=total_counts[i] / samples,
        )
        for i in ranked
    )


def summarise_file(
    path: str | os.PathLike[str],
    *,
    project_root: Path,
    top_n: int = DEFAULT_TOP_N,
    own_modules: Collection[str] = (),
) -> tuple[Hotspot, ...]:
    """Rank the frames of a folded-stack file by self time.

    The file is streamed, never read whole. Bytes that are not UTF-8
    are replaced with U+FFFD rather than failing a large capture on
    one badly encoded symbol.

    Parameters
    ----------
    path : str or os.PathLike
        Folded-stack file.
    project_root : pathlib.Path
        As for `summarise`.
    top_n : int, optional
        As for `summarise`.
    own_modules : collection of str, optional
        As for `summarise`.

    Returns
    -------
    tuple of perfproof.schema.Hotspot
        As for `summarise`.

    Raises
    ------
    FoldedStackError
        As for `summarise`.
    ValueError
        If ``top_n`` is less than 1.
    OSError
        If the file cannot be read.
    """
    with open(path, encoding="utf-8", errors="replace") as stream:
        return summarise(
            stream,
            project_root=project_root,
            top_n=top_n,
            own_modules=own_modules,
        )
