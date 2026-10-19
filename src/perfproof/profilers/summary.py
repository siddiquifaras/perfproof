"""A small, labelled summary of a profile, for the agent to read.

`perfproof.profilers.folded` ranks frames; this module turns that
ranking and the unprofiled run's wall and CPU time into the few
numbers that decide what to look at next, in a fixed small size:

- the top frames, each with its share of time and its **cap**, the
  Amdahl bound ``1 / (1 - p)`` where ``p`` is the frame's *total*
  fraction (the time in it and everything it calls, not its self
  fraction). A cap is an upper bound from a model of the workload: no
  change to that frame alone can give more. It is never a measured
  effect of anything, and the summary labels it so;
- wall time against CPU time, which says how much of the run no thread
  was on a CPU for, that is, waiting (for I/O, a lock, a sleep) rather
  than computing;
- what the shares are shares of (``basis``), since a share of py-spy's
  active samples and a share of pyinstrument's wall time are different
  things.

Nothing here times anything: every number comes from a profile that was
already run. The result says nothing about direction; it carries no
word for one, and ``labels`` names each kind of number.
"""

import json
import math
from collections.abc import Sequence
from typing import Final

from perfproof.commands import JsonValue
from perfproof.schema import Hotspot

__all__ = [
    "BRIEF_BYTES",
    "DEFAULT_BRIEF_TOP",
    "amdahl_cap",
    "brief",
    "summarise_profile",
]

BRIEF_BYTES: Final = 1800
"""Most bytes of the compact JSON `brief` returns."""
DEFAULT_BRIEF_TOP: Final = 5
"""Frames in a summary when the caller does not say."""
_TEXT_CHARS: Final = 100
"""Longest function or file name kept in a summary, in characters."""
_LABELS: Final = {
    "share": "estimate: a share of the profile, not of a calibrated run",
    "cap": "bound: 1 / (1 - total share); not a measured effect",
    "time": "measured once, on the machine as loaded at the time",
}


def amdahl_cap(total_fraction: float | None) -> float | None:
    """Return the cap a share of time puts on any change to it.

    Parameters
    ----------
    total_fraction : float or None
        Share ``p`` of the run spent in a frame and its callees, from 0
        to 1; ``None`` when the profiler gives no total.

    Returns
    -------
    float or None
        ``1 / (1 - p)``; ``None`` when ``p`` is unknown or is 1 (the
        frame is the whole run, so the cap is not finite).
    """
    if total_fraction is None or total_fraction >= 1:
        return None
    return 1.0 / (1.0 - total_fraction)


def _short(text: str | None) -> str | None:
    """Cut a name to `_TEXT_CHARS`, marking the cut."""
    if text is None or len(text) <= _TEXT_CHARS:
        return text
    return text[: _TEXT_CHARS - 1] + "~"


def _frame(hotspot: Hotspot, min_effect: float | None) -> JsonValue:
    """Describe one frame with its cap."""
    cap = amdahl_cap(hotspot.total_fraction)
    entry: dict[str, JsonValue] = {
        "function": _short(hotspot.function),
        "file": _short(hotspot.file),
        "line": hotspot.line,
        "frame_kind": hotspot.frame_kind,
        "is_library": hotspot.is_library,
        "self_fraction": hotspot.self_fraction,
        "total_fraction": hotspot.total_fraction,
        "cap": cap,
    }
    if cap is not None and min_effect is not None:
        entry["cap_above_bar"] = cap > 1.0 + min_effect
    return entry


def _time(
    wall_seconds: float,
    cpu_seconds: float,
    load_average: float | None,
    cpu_count: int | None,
) -> dict[str, JsonValue]:
    """Compare wall and CPU time of the unprofiled run."""
    ratio = cpu_seconds / wall_seconds
    entry: dict[str, JsonValue] = {
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "cpu_over_wall": ratio,
        "load_average": load_average,
        "cpu_count": cpu_count,
    }
    if ratio <= 1:
        entry["waiting_share_at_least"] = 1 - ratio
        entry["reading"] = (
            "for at least this share of wall time no thread was on a CPU: "
            "the run was waiting, not computing"
        )
    else:
        entry["waiting_share_at_least"] = None
        entry["reading"] = (
            "CPU time exceeds wall time: other threads used CPU alongside "
            "the main one, so this gap cannot show waiting"
        )
    if load_average is not None and cpu_count is not None:
        entry["machine_loaded"] = load_average > cpu_count
    return entry


def summarise_profile(
    hotspots: Sequence[Hotspot],
    *,
    basis: str,
    weight_unit: str,
    wall_seconds: float,
    cpu_seconds: float,
    load_average: float | None = None,
    cpu_count: int | None = None,
    min_effect: float | None = None,
    top_n: int = DEFAULT_BRIEF_TOP,
) -> dict[str, JsonValue]:
    """Summarise a profile and its run's wall and CPU time.

    Parameters
    ----------
    hotspots : sequence of perfproof.schema.Hotspot
        Frames ranked by self share, as the profiler summary returns
        them.
    basis : str
        What the shares are shares of: ``active``, ``wall`` or
        ``unknown``.
    weight_unit : str
        Unit of the profile's counts.
    wall_seconds, cpu_seconds : float
        Wall time (positive) and CPU time (non-negative) of the
        unprofiled run, in seconds.
    load_average : float, optional
        One-minute load average before the run.
    cpu_count : int, optional
        Logical CPUs.
    min_effect : float, optional
        The benchmark's minimum effect; a cap at or below ``1 +
        min_effect`` is marked as not above the bar.
    top_n : int, optional
        Frames to keep; at least 1.

    Returns
    -------
    dict of str to JsonValue
        ``frames``, ``time``, ``basis``, ``weight_unit``, ``labels``
        and ``frames_omitted``.

    Raises
    ------
    ValueError
        If ``top_n`` is below 1 or a time is not finite and in range.
    """
    if top_n < 1:
        msg = f"top_n must be at least 1, got {top_n}"
        raise ValueError(msg)
    if not (
        math.isfinite(wall_seconds)
        and wall_seconds > 0
        and math.isfinite(cpu_seconds)
        and cpu_seconds >= 0
    ):
        msg = "wall time must be positive and CPU time non-negative"
        raise ValueError(msg)
    kept = list(hotspots)[:top_n]
    return {
        "basis": basis,
        "weight_unit": weight_unit,
        "frames": [_frame(h, min_effect) for h in kept],
        "frames_omitted": len(hotspots) - len(kept),
        "time": _time(wall_seconds, cpu_seconds, load_average, cpu_count),
        "labels": dict(_LABELS),
    }


def brief(
    summary: dict[str, JsonValue], max_bytes: int = BRIEF_BYTES
) -> dict[str, JsonValue]:
    """Cut a summary to a byte cap by dropping its last frames.

    Parameters
    ----------
    summary : dict of str to JsonValue
        A `summarise_profile` result.
    max_bytes : int, optional
        Largest size of the compact JSON of the result, in bytes of
        UTF-8.

    Returns
    -------
    dict of str to JsonValue
        The summary with as many leading frames as fit, at least one
        when any exist; ``frames_omitted`` counts every frame left out,
        including those the input already omitted.

    Raises
    ------
    ValueError
        If even one frame with the rest of the summary exceeds
        ``max_bytes``.
    """
    raw = summary["frames"]
    frames = list(raw) if isinstance(raw, list) else []
    omitted = summary.get("frames_omitted", 0)
    already = omitted if isinstance(omitted, int) else 0
    for keep in range(len(frames), -1, -1):
        cut: dict[str, JsonValue] = {
            **summary,
            "frames": frames[:keep],
            "frames_omitted": already + len(frames) - keep,
        }
        size = len(
            json.dumps(cut, separators=(",", ":"), allow_nan=False).encode()
        )
        if size <= max_bytes and (keep or not frames):
            return cut
    msg = f"one frame and the timings exceed {max_bytes} bytes"
    raise ValueError(msg)
