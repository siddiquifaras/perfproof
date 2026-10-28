"""Match a manifest's hotspot against the names profilers really print.

A task's manifest names its hotspot the short way a person would
(``to_chars``, ``Echo.ServeHTTP``, ``print_entry``), while profilers
print the qualified symbol: C++ with its argument list, Go with its
import path and pointer receiver, Rust with a hash suffix, macOS native
profiles with the image name in front. `short_name` reduces each to its
last component and the receiver before it, and `names_match` compares
those. Two different functions that merely share a suffix
(``from_chars`` and ``to_chars``) never match, because the last
component is compared whole.

The forms handled are the ones captured in the hidden-fix probe
(the tests quote them), plus the dotted and ``#`` forms the other
languages' manifests use. Nothing here is a guess about a form not seen:
an unfamiliar one keeps its text and so matches only itself.
"""

from __future__ import annotations

import re
from typing import NamedTuple

__all__ = ["ShortName", "files_match", "names_match", "short_name"]


class ShortName(NamedTuple):
    """A function reduced to what identifies it.

    Attributes
    ----------
    path : tuple of str
        The components before the name, outermost first: the module,
        package, namespace, type or class that owns it. Empty when the
        text had no qualifier. A Rust trait impl, ``<Foo as Display>``,
        is one component, so it matches only that impl.
    name : str
        The last component, without arguments, generics or hash.
    instance : bool
        Whether a ``#`` joined the name to its owner (Ruby's instance
        method ``String#blank?``, as against ``String.blank?``).
    """

    path: tuple[str, ...]
    name: str
    instance: bool = False

    @property
    def receiver(self) -> str | None:
        """The component right before the name, or ``None``."""
        return self.path[-1] if self.path else None


_CLONE = re.compile(r"(?:\s*\[clone [^\]]*\])+$")
_QUALIFIERS = re.compile(r"(?:\s+(?:const|volatile|noexcept)|\s*&&?)+$")
_RUST_HASH = re.compile(r"::h[0-9a-f]{16}$")
_GO_METHOD_VALUE = re.compile(r"-fm$")
_OPERATOR = re.compile(r"operator\s*(?:\(\)|<=>|<<=?|>>=?|<=?|>=?|->\*?|\[\])")
_SEPARATORS = re.compile(r"(::|[.#])")
_PLACEHOLDER = "<locals>"
"""Python's qualname marker for a nested function; not a receiver."""


def _freeze_operators(text: str) -> str:
    """Rewrite ``operator()``-style names so they stay whole."""

    def code(match: re.Match[str]) -> str:
        symbol = re.sub(r"\s+", "", match.group(0)[len("operator") :])
        return "operator_" + "_".join(str(ord(c)) for c in symbol)

    return _OPERATOR.sub(code, text)


def _strip_trailing_arguments(text: str) -> str:
    """Drop a final balanced ``(...)`` group and its qualifiers."""
    text = _QUALIFIERS.sub("", text.rstrip())
    if not text.endswith(")"):
        return text
    depth = 0
    for at in range(len(text) - 1, -1, -1):
        char = text[at]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                head = text[:at].rstrip()
                return head or text
    return text


def _matching(text: str, start: int, opening: str, closing: str) -> int:
    """Index of the bracket closing the one at ``start``, or -1."""
    depth = 0
    for at in range(start, len(text)):
        if text[at] == opening:
            depth += 1
        elif text[at] == closing:
            depth -= 1
            if depth == 0:
                return at
    return -1


def _strip_generics(text: str) -> str:
    """Remove ``<...>`` and ``[...]`` groups that follow a name.

    A leading Rust ``<Type as Trait>`` becomes ``Type``. A group right
    after a separator, such as Python's ``<locals>`` or ``<lambda>``, is
    a name of its own and stays; so do empty brackets (Ruby's ``[]``).
    """
    if text.startswith("<"):
        end = _matching(text, 0, "<", ">")
        if end > 0:
            inner = text[1:end].split(" as ")[0]
            text = inner + text[end + 1 :]
    out: list[str] = []
    at = 0
    while at < len(text):
        char = text[at]
        follows_name = bool(out) and (out[-1].isalnum() or out[-1] in "_>]")
        if char == "<" and follows_name:
            end = _matching(text, at, "<", ">")
            if end > 0:
                at = end + 1
                continue
        if char == "[" and follows_name and text[at + 1 : at + 2] != "]":
            end = _matching(text, at, "[", "]")
            if end > 0:
                at = end + 1
                continue
        out.append(char)
        at += 1
    return "".join(out)


def _component(text: str) -> str:
    return text.strip().strip("*&() ").strip()


def _last(path: str) -> str:
    """Last ``::`` component of a path, without generics."""
    return _strip_generics(path.strip()).rsplit("::", 1)[-1].strip()


def _trait_impl(text: str) -> tuple[str | None, str]:
    """Split a leading Rust ``<Type as Trait>`` into one component.

    Returns the component (``<Type as Trait>`` by last components) and
    the rest of the text, or ``None`` and the text if it has no such
    prefix.
    """
    if not text.startswith("<"):
        return None, text
    end = _matching(text, 0, "<", ">")
    inner = text[1:end] if end > 0 else ""
    if end <= 0 or " as " not in inner:
        return None, text
    kind, trait = inner.split(" as ", 1)
    return f"<{_last(kind)} as {_last(trait)}>", text[end + 1 :]


def short_name(text: str) -> ShortName | None:
    """Reduce a printed or manifest function name to its short form.

    Parameters
    ----------
    text : str
        A function as a profiler prints it, or as a manifest names it.
        Examples: ``simdjson::internal::to_chars(char*, char const*,
        double)``, ``github.com/labstack/echo/v4.(*Echo).ServeHTTP``,
        ``fd`fd::output::print_entry::ha19644765a9c40d9``,
        ``Echo.ServeHTTP``.

    Returns
    -------
    ShortName or None
        ``None`` when nothing is left after stripping.

    Notes
    -----
    Steps, in order: the image name before a backtick (macOS native
    profiles), a ``[clone ...]`` suffix, a final argument list and its
    qualifiers, a Rust ``::h<16 hex>`` hash, Go's ``-fm`` suffix, a
    leading ``<Type as Trait>``, generics, then the module or package
    path before the last ``/``. The rest is split on ``::``, ``.`` and
    ``#``, remembering whether a ``#`` joined the name.
    """
    cleaned = text.strip()
    if "`" in cleaned:
        cleaned = cleaned.rsplit("`", 1)[1]
    cleaned = _freeze_operators(cleaned)
    cleaned = _CLONE.sub("", cleaned)
    cleaned = _strip_trailing_arguments(cleaned)
    cleaned = _RUST_HASH.sub("", cleaned)
    cleaned = _GO_METHOD_VALUE.sub("", cleaned)
    impl, cleaned = _trait_impl(cleaned)
    cleaned = _strip_generics(cleaned)
    cleaned = cleaned.rsplit("/", 1)[-1]
    pieces = _SEPARATORS.split(cleaned)
    parts: list[tuple[str, str]] = []
    separator = ""
    for piece in pieces:
        if _SEPARATORS.fullmatch(piece):
            separator = piece
            continue
        comp = _component(piece)
        if comp and piece.strip() != _PLACEHOLDER:
            parts.append((separator, comp))
        separator = ""
    if impl is not None:
        parts.insert(0, ("", impl))
    if not parts:
        return None
    return ShortName(
        tuple(comp for _, comp in parts[:-1]),
        parts[-1][1],
        parts[-1][0] == "#" and len(parts) > 1,
    )


def names_match(printed: str, wanted: str) -> bool:
    """Whether a profiler's function is the one a manifest names.

    The last components must be equal. If the manifest names an owner
    (``Echo.ServeHTTP``, ``fd::output::print_entry``) the printed
    owner path must end with the manifest's whole path, and a ``#``
    must be a ``#`` on both sides; a printed name with no owner cannot
    be confirmed and does not match. Without an owner in the manifest
    any owner matches.

    Parameters
    ----------
    printed : str
        The name as printed in a profile.
    wanted : str
        The name as written in the task's manifest.

    Returns
    -------
    bool
        ``True`` on a match.
    """
    have = short_name(printed)
    want = short_name(wanted)
    if have is None or want is None or have.name != want.name:
        return False
    if not want.path:
        return True
    return (
        have.instance == want.instance
        and len(have.path) >= len(want.path)
        and have.path[len(have.path) - len(want.path) :] == want.path
    )


def _path_parts(path: str) -> list[str]:
    return [
        p for p in path.replace("\\", "/").split("/") if p not in {"", "."}
    ]


def files_match(printed: str | None, wanted: str) -> bool:
    """Whether a printed source file is the manifest's file.

    One path's components must end the other's, so ``/src/echo.go``,
    ``echo.go`` and ``a/b/echo.go`` agree. A frame with no file (native
    frames without debug information) is not contradicted: its name
    decides.

    Parameters
    ----------
    printed : str or None
        The file a profile printed for the frame.
    wanted : str
        The manifest's file, relative to the repository root.

    Returns
    -------
    bool
        ``True`` when the file agrees or is unknown.
    """
    if printed is None:
        return True
    have = _path_parts(printed)
    want = _path_parts(wanted)
    if not have or not want:
        return False
    short = min(len(have), len(want))
    return have[-short:] == want[-short:]
