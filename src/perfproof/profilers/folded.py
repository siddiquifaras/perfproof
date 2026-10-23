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

Two rankings come out of one pass. The self ranking lists the frames
that burn the time. The total ranking lists the frames the time is
spent under, including frames with no self time of their own: a project
function whose cost sits in the library calls it makes is invisible by
self time at any ``top_n``, and is often where the fix belongs (call
less, cache, batch).

Frames are normalised before they are counted:

- Python frames (``function (file.py:line)``, as py-spy writes them)
  keep their file and line; a file under the project root becomes
  relative to it.
- Every other frame is native. A native frame is shown as
  ``module`symbol`` when the profiler names its binary, and never
  merges with a Python frame of the same name.
- A frame is marked as library code unless it is shown to be the
  project's: a file under the project root that is not in a
  ``site-packages``, ``dist-packages`` or ``node_modules`` directory,
  or a native frame of a binary the caller names as the project's own.
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
    "Ranking",
    "rank",
    "rank_file",
    "summarise",
    "summarise_file",
]

DEFAULT_TOP_N: Final = 20
"""Number of hotspots returned when the caller does not say."""

_LIBRARY_DIRS: Final = frozenset(
    {"site-packages", "dist-packages", "node_modules"}
)
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


@dataclass(frozen=True, slots=True)
class Ranking:
    """The two rankings of one set of folded stacks.

    Attributes
    ----------
    by_self : tuple of perfproof.schema.Hotspot
        Frames with a nonzero self count, as `summarise` returns them.
    by_total : tuple of perfproof.schema.Hotspot
        Functions by total (inclusive) share, as `rank` describes them.
        ``is_library`` is false for the project's own frames.
    """

    by_self: tuple[Hotspot, ...]
    by_total: tuple[Hotspot, ...]


_PSEUDO_NAME: Final = re.compile(r"<[^<>\s]+>")
_TRAILING_LINE: Final = re.compile(r":[0-9]+$")


def _synthetic(frame: _Frame) -> bool:
    """Whether a frame is the interpreter's, not a function of a file.

    ``<module>``, ``<listcomp>``, ``<frozen runpy>`` and
    pyinstrument's ``MainThread (<thread>)`` are one angle-bracketed
    word or a ``<...>`` pseudo-file. A Rust symbol such as
    ``<Type as Trait>::method`` is real code and is kept.
    """
    if _PSEUDO_NAME.fullmatch(frame.function):
        return True
    file = frame.file
    return file is not None and file.startswith("<") and file.endswith(">")


def _function_key(frame: _Frame) -> tuple[object, ...]:
    """Identify a frame's function, whatever line it is executing.

    py-spy and Go give the line a frame is executing, so one function
    that calls from several lines is several `_Frame` values; its
    inclusive time is the sum over them.
    """
    location = frame.location
    if location is not None and frame.line is not None:
        location = _TRAILING_LINE.sub("", location)
    return (
        frame.function,
        frame.file,
        frame.frame_kind,
        frame.is_library,
        location,
    )


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
    return rank(
        lines,
        project_root=project_root,
        top_n=top_n,
        own_modules=own_modules,
    ).by_self


def rank(
    lines: Iterable[str],
    *,
    project_root: Path,
    top_n: int = DEFAULT_TOP_N,
    own_modules: Collection[str] = (),
) -> Ranking:
    """Rank the frames of folded stacks by self time and by total time.

    Parameters
    ----------
    lines : iterable of str
        As for `summarise`.
    project_root : pathlib.Path
        As for `summarise`.
    top_n : int, optional
        Most frames in each ranking; at least 1.
    own_modules : collection of str, optional
        As for `summarise`.

    Returns
    -------
    Ranking
        ``by_self`` is `summarise`'s result. ``by_total`` holds the
        functions with the largest total count, a function being one
        frame whatever line it is executing (a function that calls from
        several lines counts the samples under all of them once, and
        shows the line with the most; ``self_fraction`` is likewise the
        sum over its lines), by total then self count,
        both descending, ties in order of first appearance; at most
        ``top_n``. It includes frames with a self count of zero and
        leaves out two kinds of frame that say nothing about where the
        time goes: synthetic ones (a function or file written in angle
        brackets, such as ``<module>`` or ``MainThread (<thread>)``),
        and entry frames: on every sample and above the point where the
        stacks first differ (``main``, ``runpy`` wrappers,
        ``runtime.goexit``), so their total is the whole profile and
        says nothing. The last frame of that common prefix stays,
        since that is where the time divides. Each frame's
        ``is_library`` says whether it is project code. Fractions are
        of all samples in the input.

    Raises
    ------
    FoldedStackError
        As for `summarise`.
    ValueError
        If ``top_n`` is less than 1.

    Notes
    -----
    Time is as for `summarise`. Both rankings are one heap
    pass, ``O(F log top_n)``; the entry-frame test keeps one prefix of
    the deepest stack.
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
    group_ids: dict[tuple[object, ...], int] = {}
    group_of: list[int] = []
    members: list[list[int]] = []
    group_self: list[int] = []
    group_total: list[int] = []
    prefix: list[int] | None = None
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
                    key = _function_key(frame)
                    group = group_ids.get(key)
                    if group is None:
                        group = len(members)
                        group_ids[key] = group
                        members.append([])
                        group_self.append(0)
                        group_total.append(0)
                    members[group].append(frame_id)
                    group_of.append(group)
                ids[text_frame] = frame_id
            stack_ids.append(frame_id)
        self_counts[stack_ids[-1]] += count
        for frame_id in set(stack_ids):
            total_counts[frame_id] += count
        groups = [group_of[i] for i in stack_ids]
        group_self[groups[-1]] += count
        for group in set(groups):
            group_total[group] += count
        if count == 0:
            continue
        if prefix is None:
            prefix = groups
        else:
            common = 0
            for a, b in zip(prefix, groups, strict=False):
                if a != b:
                    break
                common += 1
            del prefix[common:]
    if samples == 0:
        raise FoldedStackError("no samples in the input")
    by_self = heapq.nlargest(
        top_n,
        (i for i, n in enumerate(self_counts) if n > 0),
        key=lambda i: (self_counts[i], total_counts[i]),
    )
    wrappers = (
        {
            g
            for g in prefix[:-1]
            if group_total[g] == samples and group_self[g] == 0
        }
        - {prefix[-1]}
        if prefix
        else set[int]()
    )
    by_group = heapq.nlargest(
        top_n,
        (
            g
            for g in range(len(members))
            if group_total[g] > 0
            and g not in wrappers
            and not _synthetic(frames[members[g][0]])
        ),
        key=lambda g: (group_total[g], group_self[g]),
    )

    def hotspots(ids: list[int]) -> tuple[Hotspot, ...]:
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
            for i in ids
        )

    def function_hotspots(groups: list[int]) -> tuple[Hotspot, ...]:
        out: list[Hotspot] = []
        for g in groups:
            shown = frames[max(members[g], key=total_counts.__getitem__)]
            out.append(
                Hotspot(
                    function=shown.function,
                    file=shown.file,
                    line=shown.line,
                    frame_kind=shown.frame_kind,
                    is_library=shown.is_library,
                    self_fraction=group_self[g] / samples,
                    total_fraction=group_total[g] / samples,
                )
            )
        return tuple(out)

    return Ranking(hotspots(by_self), function_hotspots(by_group))


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
    return rank_file(
        path,
        project_root=project_root,
        top_n=top_n,
        own_modules=own_modules,
    ).by_self


def rank_file(
    path: str | os.PathLike[str],
    *,
    project_root: Path,
    top_n: int = DEFAULT_TOP_N,
    own_modules: Collection[str] = (),
) -> Ranking:
    """Rank the frames of a folded-stack file, as `rank` does.

    The file is streamed and decoded as for `summarise_file`.

    Parameters
    ----------
    path : str or os.PathLike
        Folded-stack file.
    project_root : pathlib.Path
        As for `rank`.
    top_n : int, optional
        As for `rank`.
    own_modules : collection of str, optional
        As for `rank`.

    Returns
    -------
    Ranking
        As for `rank`.

    Raises
    ------
    FoldedStackError
        As for `rank`.
    ValueError
        If ``top_n`` is less than 1.
    OSError
        If the file cannot be read.
    """
    with open(path, encoding="utf-8", errors="replace") as stream:
        return rank(
            stream,
            project_root=project_root,
            top_n=top_n,
            own_modules=own_modules,
        )
