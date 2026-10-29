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
  A native frame whose symbol is in a language runtime namespace is
  library code even in such a binary: the Rust standard library
  (``std::``, ``core::``, ``alloc::``, also as ``<std::...>`` and the
  legacy ``_$LT$std..``) and the C++ standard library (``std::``,
  ``__gnu_cxx::``), which a binary carries its own copy of. Only the
  function's own name counts, not a type it mentions; see
  `_is_runtime`.
"""

import heapq
import os
import posixpath
import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from perfproof.bench.spec import STATE_DIR
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
_CXX_RUNTIME: Final = ("std::", "__gnu_cxx::", "__cxxabiv1::")
_QUALIFIED: Final = re.compile(r"[A-Za-z_~][\w~]*(?:::[A-Za-z_~][\w~]*)+")
_RUST_RUNTIME: Final = ("std::", "core::", "alloc::")
_RUST_LEGACY_RUNTIME: Final = ("std..", "core..", "alloc..")
_RUST_HASH: Final = re.compile(r"::h[0-9a-f]{16}$")
_RUST_ESCAPE: Final = re.compile(r"\$[A-Za-z0-9_]+\$")
_CXX_RUNTIME_NAME: Final = re.compile(
    r"(?<![\w:])(?:std|__gnu_cxx|__cxxabiv1)(?:::[A-Za-z_~][\w~]*)+"
)
_CLONE: Final = re.compile(r"\s*\[clone [^\]]*\]\s*$")
_CXX_WORDS: Final = re.compile(
    r"\b(?:const|volatile|unsigned|signed|long|short|int|char|bool|"
    r"float|double|void|auto|wchar_t|char8_t|char16_t|"
    r"char32_t)\b"
)
_IDENTIFIER: Final = re.compile(r"[A-Za-z_]")
_OPERATOR: Final = re.compile(
    r"operator(?:\(\)|<=>|<<=|>>=|->\*|->|<<|>>|[-+*/%^&|!=<>]=?|~|,|\[\])"
)
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
    is_harness: bool = False


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
        self._file_exists: dict[str, bool] = {}

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
                is_library=module not in self._own_modules
                or _is_runtime(symbol),
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
        harness = self._is_harness(shown)
        if harness:
            own = False
        if path.endswith(_PYTHON_SUFFIXES):
            return _Frame(
                function, shown, line, "python", not own, location, harness
            )
        module = posixpath.basename(path)
        own = not harness and (
            own or (module in self._own_modules and not _is_runtime(function))
        )
        if line is not None:
            return _Frame(
                function, shown, line, "native", not own, location, harness
            )
        name = f"{module}`{function}" if module else function
        return _Frame(name, None, None, "native", not own, location, harness)

    def _is_harness(self, shown: str) -> bool:
        """Whether a shown path is under the project's state directory.

        This is the one place ownership of such a file is decided: the
        workload driver an agent writes under ``.perfproof/harness/``
        is perfproof's own state, never the user's code. ``shown`` is
        root-relative only for a file inside the project, so a path
        outside it never matches. A relative path counts only when
        the file exists, as for `_classify_path`.
        """
        if shown.split("/", 1)[0] != STATE_DIR:
            return False
        if posixpath.isabs(shown):
            return False
        cached = self._file_exists.get(shown)
        if cached is None:
            cached = (self._root / shown).is_file()
            self._file_exists[shown] = cached
        return cached

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


def _is_runtime(symbol: str) -> bool:
    """Whether a native symbol is a standard library's own function.

    A binary built from Rust or C++ carries its own copy of its
    language's standard library, so the image a symbol is in does not
    say it is the project's. A symbol counts only when all of it is
    runtime, never because it mentions a runtime type:

    - C++: take the symbol without a ``[clone ...]`` suffix and
      without the text inside template angle brackets, remove every
      identifier qualified by ``std::`` (libc++'s ``std::__1::``
      included), ``__gnu_cxx::`` or ``__cxxabiv1::``, and remove the
      keywords and builtin type words (``const``, ``unsigned``,
      ``void``...); ``operator`` stays unless a runtime name
      qualifies it, so an unqualified ``operator()(std::string)`` is
      the project's. It is runtime when something was
      removed and nothing identifier-like is left, so ``void std::f()``
      and ``std::ostream& std::operator<<(std::ostream&, char const*)``
      are runtime, while ``process(std::vector<int>&)``,
      ``(anonymous namespace)::f(std::string)``,
      ``app::Foo::operator std::string() const`` and
      ``std::string_view (*app::get())(int)`` are the project's. The
      rule can only err toward the project: ``std::sort<app::X*>(
      app::X*, app::X*)`` stays the image's.
    - Rust ``<X as Trait>::f`` (and the legacy
      ``_$LT$X$u20$as$u20$Trait$GT$::f``): runtime only when both
      ``X`` and ``Trait`` start ``std::``, ``core::`` or ``alloc::``,
      since an impl of a project trait, or on a project type, is
      project code. ``<X>::f`` is runtime when ``X`` is.
    - Rust names go through the same rule after the hash suffix and
      the ``$LT$``-style escapes are dropped, so
      ``std::thread::lifecycle::JoinInner$LT$T$GT$::join::h...`` is
      runtime.
    - Rust ``std::...`` is runtime; ``core::...`` and ``alloc::...``
      only with the 16-digit hash suffix (``::h0123456789abcdef``)
      Rust appends, because ``core::`` is a common C++ project
      namespace.

    Anything else, including a name this cannot parse, stays the
    image's.
    """
    if symbol.startswith("_$LT$"):
        body = symbol.removeprefix("_$LT$")
        head, sep, rest = body.partition("$u20$as$u20$")
        if not head.startswith(_RUST_LEGACY_RUNTIME):
            return False
        return not sep or rest.startswith(_RUST_LEGACY_RUNTIME)
    if symbol.startswith("<"):
        head, sep, rest = _split_rust_impl(symbol)
        if not head.startswith(_RUST_RUNTIME):
            return False
        return not sep or rest.startswith(_RUST_RUNTIME)
    if _RUST_HASH.search(symbol) is not None:
        plain = _RUST_ESCAPE.sub("", _RUST_HASH.sub("", symbol))
        names = _QUALIFIED.findall(_outside_angles(plain))
        allowed = (*_CXX_RUNTIME, "core::", "alloc::")
        return bool(names) and all(n.startswith(allowed) for n in names)
    text = _outside_angles(_CLONE.sub("", symbol))
    left = _CXX_RUNTIME_NAME.sub("", text)
    if left == text:
        return False
    return _IDENTIFIER.search(_CXX_WORDS.sub("", left)) is None


def _split_rust_impl(symbol: str) -> tuple[str, str, str]:
    """Split ``<X as Trait>::f`` into ``X``, `` as `` and ``Trait``.

    The separator is the first `` as `` outside nested ``<>``. For
    ``<X>::f`` the separator and trait are empty.
    """
    depth = 0
    for index, char in enumerate(symbol):
        if char == "<":
            depth += 1
        elif char == ">":
            depth -= 1
            if depth == 0:
                return symbol[1:index], "", ""
        elif depth == 1 and symbol.startswith(" as ", index):
            return symbol[1:index], " as ", symbol[index + 4 :]
    return symbol[1:], "", ""


def _outside_angles(symbol: str) -> str:
    """Return a symbol with everything inside ``<>`` removed.

    Operator names are read as one word first, so the ``<`` and ``>``
    of ``operator<<`` are not brackets.
    """
    out: list[str] = []
    depth = 0
    for char in _OPERATOR.sub("operator", symbol):
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(depth - 1, 0)
        elif depth == 0:
            out.append(char)
    return "".join(out)


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
    samples : int
        Total sample count of the input, the denominator of every
        fraction. A frame's own count is ``round(fraction * samples)``.
    above_bar : tuple of perfproof.schema.Hotspot
        The project's own functions whose total fraction is at least
        the ``bar`` given to `rank`, by total then self count, at most
        ``bar_limit``; empty when no bar was given. Chosen from the
        same functions as ``by_total``, whatever ``top_n`` is.
    matched : int
        Functions eligible for ``by_total`` (those matching
        ``name_filter`` when one was given), before ``top_n`` cut them.
    has_project : bool
        Whether any stack with samples holds a frame classified as the
        project's own. False means the project is unknown to this
        profile (for native frames, no ``own_modules`` matched).
    by_module : bool
        Whether the frames name their code by module, not by a source
        file: some frame is written ``module`symbol`` with no file, and
        none has a source file. Only then can ``own_modules`` decide
        which frames are the project's; otherwise the project root does.
    """

    by_self: tuple[Hotspot, ...]
    by_total: tuple[Hotspot, ...]
    samples: int
    above_bar: tuple[Hotspot, ...] = ()
    matched: int = 0
    has_project: bool = False
    by_module: bool = False


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
    keep_all: bool = False,
    name_filter: Callable[[str], bool] | None = None,
    bar: float | None = None,
    bar_limit: int = DEFAULT_TOP_N,
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
    keep_all : bool, optional
        Leave out no function from ``by_total``: not the synthetic,
        entry, trampoline or common-prefix frames described below. For
        looking up one function by name, where a frame the ranking
        would hide must still be found. Default false.
    name_filter : callable, optional
        Takes a function's printed name; only functions it accepts are
        eligible for ``by_total``.
    bar : float, optional
        A fraction of the samples. When given, ``above_bar`` lists the
        project's own functions with at least that total fraction.
    bar_limit : int, optional
        Most functions in ``above_bar``; at least 1.

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
        and entry frames. If any stack with samples holds a project
        frame, then in every such stack the frames before the first
        project frame are entry or runtime frames (``thread_start``,
        ``_pthread_start``, ``start_thread``, ``runtime.goexit``,
        ``threading.py`` ``_bootstrap``, ``testing.(*B).launch``). A
        frame is left out when it is never at or after a project frame
        and never has self time; a stack with no project frame, such as
        an idle runtime thread, therefore marks nothing. Library frames
        below a project frame are real work and stay. A library frame is
        also left out when it is a trampoline: it has no self time and,
        in every stack it appears in, a project frame lies deeper. The
        rule is structural. The captured fd profile shows Rust
        ``__rust_begin_short_backtrace`` (between ``main`` and
        ``fd::main``) and the ``write_fmt`` and ``core::fmt::write``
        formatting frames above a project ``Display`` impl going; no
        captured Python, Go or C++ profile is changed by it. It only
        calls back into project
        code, so it says nothing about where the time goes. A library
        frame with self time, or in any stack with no project frame
        deeper, stays, and so does every project frame. If no stack
        holds a project frame, the project is unknown and nothing is
        judged by this rule. Either way, a frame on every sample and
        above the point where the stacks first differ (``main``,
        ``runpy`` wrappers) has a total that is the whole profile and
        is left out, except the last frame of that common prefix, which
        stays since that is where the time divides. Each frame's
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
    pass, ``O(F log top_n)``; the entry-frame and trampoline tests keep
    one prefix of the deepest stack and two flags per function.
    """
    if top_n < 1:
        msg = f"top_n must be at least 1, got {top_n}"
        raise ValueError(msg)
    if bar_limit < 1:
        msg = f"bar_limit must be at least 1, got {bar_limit}"
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
    in_work: list[bool] = []
    no_project_below: list[bool] = []
    has_project = False
    module_named = False
    path_named = False
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
                if frame.file is None:
                    module_named = module_named or "`" in frame.function
                elif not frame.file.startswith("<"):
                    path_named = True
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
                        in_work.append(False)
                        no_project_below.append(False)
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
        own_at = [
            i for i, f in enumerate(stack_ids) if not frames[f].is_library
        ]
        if own_at:
            has_project = True
            for group in groups[own_at[0] :]:
                in_work[group] = True
        for group in groups[own_at[-1] if own_at else 0 :]:
            no_project_below[group] = True
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
        if prefix and not keep_all
        else set[int]()
    )
    eligible = [
        g
        for g in range(len(members))
        if group_total[g] > 0
        and (
            keep_all
            or (
                g not in wrappers
                and (
                    not has_project
                    or in_work[g]
                    or group_self[g] > 0
                    or frames[members[g][0]].is_harness
                )
                and (
                    not has_project
                    or group_self[g] > 0
                    or no_project_below[g]
                    or not frames[members[g][0]].is_library
                    or frames[members[g][0]].is_harness
                )
                and not _synthetic(frames[members[g][0]])
            )
        )
        and (
            name_filter is None or name_filter(frames[members[g][0]].function)
        )
    ]

    def by_total_then_self(g: int) -> tuple[int, int]:
        return group_total[g], group_self[g]

    by_group = heapq.nlargest(top_n, eligible, key=by_total_then_self)
    above = (
        heapq.nlargest(
            bar_limit,
            (
                g
                for g in eligible
                if not frames[members[g][0]].is_library
                and group_total[g] / samples >= bar
            ),
            key=by_total_then_self,
        )
        if bar is not None
        else []
    )

    def hotspots(ids: list[int]) -> tuple[Hotspot, ...]:
        return tuple(
            Hotspot(
                function=frames[i].function,
                file=frames[i].file,
                line=frames[i].line,
                frame_kind=frames[i].frame_kind,
                is_library=frames[i].is_library,
                is_harness=frames[i].is_harness,
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
                    is_harness=shown.is_harness,
                    self_fraction=group_self[g] / samples,
                    total_fraction=group_total[g] / samples,
                )
            )
        return tuple(out)

    return Ranking(
        hotspots(by_self),
        function_hotspots(by_group),
        samples,
        function_hotspots(above),
        len(eligible),
        has_project,
        module_named and not path_named,
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
    keep_all: bool = False,
    name_filter: Callable[[str], bool] | None = None,
    bar: float | None = None,
    bar_limit: int = DEFAULT_TOP_N,
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
    keep_all, name_filter, bar, bar_limit
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
            keep_all=keep_all,
            name_filter=name_filter,
            bar=bar,
            bar_limit=bar_limit,
        )
