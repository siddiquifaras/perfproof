"""Look up functions in a saved profile by name.

``perfproof profile`` prints a short list, so a function the agent
suspects from reading the code can be missing from it. Here,
the gap closes without profiling again:

- `query_file` finds the functions of a saved folded-stack file that
  match a name and returns their self and total share and counts. A
  name that matches no sample is a result with no matches, never an
  error: the function was not seen in this profile.

A name matches by `perfproof.profilers.names.names_match`, the rule the
skill evaluation scores hotspots with: the last component must be
equal, and an owner (``Echo.ServeHTTP``, ``Foo::bar``) given in the
name must end the printed owner. Arguments, generics, import paths and
hash suffixes in the printed name are ignored.
"""

import os
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal

from perfproof.profilers.folded import rank_file
from perfproof.profilers.names import files_match, names_match, short_name
from perfproof.schema import Hotspot

__all__ = [
    "MAX_MATCHES",
    "PROJECT_FRAMES_LIMIT",
    "FunctionMatch",
    "FunctionQuery",
    "query_file",
]

MAX_MATCHES: Final = 50
"""Most matching functions a query returns."""
_CANDIDATES: Final = 2**31
"""Ranking size for same-named functions, which are few."""
PROJECT_FRAMES_LIMIT: Final = 50
"""Most frames `project_frames_above_bar` returns."""


@dataclass(frozen=True, slots=True)
class FunctionMatch:
    """One function that matched a query.

    Attributes
    ----------
    hotspot : perfproof.schema.Hotspot
        The function, with its self and total fraction of all samples
        (``is_library`` false for the project's own).
    self_samples, total_samples : int
        The counts the fractions are of.
    """

    hotspot: Hotspot
    self_samples: int
    total_samples: int


@dataclass(frozen=True, slots=True)
class FunctionQuery:
    """The answer to one query.

    Attributes
    ----------
    pattern : str
        The name asked for.
    samples : int
        All samples of the profile, the denominator of every share.
    matches : tuple of FunctionMatch
        Matching functions by total share then self share, descending,
        at most the query's limit. Empty when the function has no
        samples.
    omitted : int
        Matching functions beyond the limit.
    match : {"exact", "file", "bare_name"} or None
        How the matches were found; ``None`` when there are none.
        ``exact`` is `perfproof.profilers.names.names_match`. When a
        pattern with an owner (``kernels.fib``) matches nothing that
        way, because profilers often print a frame with no owner, the
        owner is tried as the frame's file (``file``: ``app/kernels.py``
        has the stem ``kernels``), and failing that the bare name alone
        is matched (``bare_name``).
    note : str or None
        What a ``file`` or ``bare_name`` match means; ``None`` for
        ``exact`` and for no match.
    """

    pattern: str
    samples: int
    matches: tuple[FunctionMatch, ...]
    omitted: int
    match: Literal["exact", "file", "bare_name"] | None = None
    note: str | None = None


def _owner_in_file(file: str | None, owner: tuple[str, ...]) -> bool:
    """Whether a pattern's owner names the file a frame is in."""
    if file is None or not owner:
        return False
    stem = PurePosixPath(file.replace("\\", "/")).stem
    return stem == owner[-1] or files_match(file, "/".join(owner))


def query_file(
    path: str | os.PathLike[str],
    *,
    project_root: Path,
    pattern: str,
    own_modules: Collection[str] = (),
    limit: int = MAX_MATCHES,
) -> FunctionQuery:
    """Find the functions of a folded-stack file that match a name.

    Parameters
    ----------
    path : str or os.PathLike
        Folded-stack file, streamed as `perfproof.profilers.folded`
        does.
    project_root : pathlib.Path
        Root frames were shown relative to when the profile was made.
    pattern : str
        A function name, short (``createChildLogger``) or with an owner
        (``Echo.ServeHTTP``), as `perfproof.profilers.names` reads it.
    own_modules : collection of str, optional
        Native binaries that are project code, as for the profile.
    limit : int, optional
        Most matches to return; at least 1.

    Returns
    -------
    FunctionQuery
        No frame is left out for being synthetic, an entry frame or on
        every stack: a match is found wherever it is.

    Raises
    ------
    ValueError
        If ``pattern`` names no function (it is empty after stripping)
        or ``limit`` is below 1.
    FoldedStackError
        If the file is not folded stacks.
    OSError
        If the file cannot be read.

    Notes
    -----
    One pass over the file, in memory proportional to its distinct
    frames, as for `perfproof.profilers.folded.rank`.
    """
    if limit < 1:
        msg = f"limit must be at least 1, got {limit}"
        raise ValueError(msg)
    wanted = short_name(pattern)
    if wanted is None:
        msg = f"{pattern!r} does not name a function"
        raise ValueError(msg)
    ranking = rank_file(
        path,
        project_root=project_root,
        top_n=_CANDIDATES,
        own_modules=own_modules,
        keep_all=True,
        name_filter=lambda printed: _bare(printed) == wanted.name,
    )
    samples = ranking.samples
    candidates = list(ranking.by_total)
    exact = [h for h in candidates if names_match(h.function, pattern)]
    how: Literal["exact", "file", "bare_name"] | None = "exact"
    note = None
    chosen = exact
    if not chosen and candidates:
        chosen = [h for h in candidates if _owner_in_file(h.file, wanted.path)]
        how = "file"
        note = (
            f"{pattern!r} has an owner no printed frame carries; matched "
            f"{wanted.name!r} in a file named for {wanted.path[-1]!r}"
        )
        if not chosen:
            chosen = candidates
            how = "bare_name"
            note = (
                f"no frame is {pattern!r} with that owner or file; "
                f"matched the bare name {wanted.name!r}"
            )
    if not chosen:
        how = None
        note = None
    shown = chosen[:limit]
    matches = tuple(
        FunctionMatch(
            hotspot=h,
            self_samples=round(h.self_fraction * samples),
            total_samples=round((h.total_fraction or 0.0) * samples),
        )
        for h in shown
    )
    return FunctionQuery(
        pattern, samples, matches, len(chosen) - len(shown), how, note
    )


def _bare(printed: str) -> str | None:
    """Return the last component of a printed function name."""
    short = short_name(printed)
    return None if short is None else short.name
