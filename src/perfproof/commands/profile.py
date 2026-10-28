"""``perfproof profile``: run a profiler on a benchmark and rank frames.

``profile NAME`` takes a benchmark spec stored by ``bench define``,
runs its command once unprofiled and once under a profiler, in the
repository's working tree as it is, and lists the frames with the
largest share of the profile, by ``file:line``, next to the
unprofiled run's wall and CPU time. How each run is launched, and what
a share is a share of for each profiler, is in
`perfproof.profilers.run`.

``--profiler`` is ``py-spy`` or ``pyinstrument``; ``auto``, the
default, is py-spy on Linux and pyinstrument on macOS. Any other
profiler plugs in with ``--profiler-command``: a JSON array of strings,
an argv template in which the argument ``{command}`` stands for the
benchmark's argv and ``{output}`` for the file the folded stacks go
to; both are required. The profiler's stdout is never read as folded
stacks, because it also carries whatever the benchmark prints.

Everything the run writes goes to a new directory,
``.perfproof/profiles/<name>/<id>/`` in the repository, with ``<id>``
the UTC start time and a random suffix; the profiler's output there is
untouched. The ``--json`` result, with or without ``--json``, is also
saved there as ``result.json`` (written to a temporary file and
renamed, so a reader never sees half of it), to read a profile again
without profiling again. A failed write is ``state_write_failed``.

``profile show NAME [RUN_ID] --function PATTERN`` looks functions up in
such a saved run; see `perfproof.commands._profile_show`.

``--json`` result
-----------------
``name``, ``spec_hash``
    The benchmark profiled.
``profiler``, ``tool_version``
    ``py-spy``, ``pyinstrument`` or ``command``, and the version that
    ran; ``tool_version`` is ``null`` when unknown.
``basis``, ``weight_unit``
    What the shares are of (``active``: py-spy's samples of active
    threads; ``wall``: pyinstrument's wall-clock time of the main
    thread; ``unknown``) and the unit of the folded counts
    (``samples``, ``microseconds``, ``unknown``).
``hotspots``
    List of `perfproof.schema.Hotspot` JSON model dumps, by self share.
    A frame with no self time of its own is never in it.
``frames_by_total``
    List of `perfproof.schema.Hotspot` JSON model dumps, by total
    (inclusive) share, at most ``--top`` of them: the frames the time
    is spent under, with or without self time of their own. A project
    function whose cost is in the library calls it makes is here and
    not in ``hotspots``. ``is_library`` is false for the project's own
    frames. Synthetic frames (``<module>``, ``<listcomp>``) and entry
    frames are left out: the frames before the first project frame of
    a stack (``thread_start``, ``runtime.goexit``, ``testing``
    runners) unless they have self time or sit below a project frame
    in another stack, and the frames on every stack above the point
    where stacks first differ (``main``, ``runpy`` wrappers).
    Added after ``hotspots``; ``hotspots`` is unchanged.
``project_frames_above_bar``
    List of `perfproof.schema.Hotspot` JSON model dumps: the project's
    own frames (``is_library`` false) whose total share is at least
    ``min_effect``, by total share. Not cut to ``--top``, only to
    `perfproof.profilers.query.PROJECT_FRAMES_LIMIT`, so every project
    function worth a cap is here. Frames ``frames_by_total`` leaves out
    are left out.
``min_effect``, ``own_modules``
    The spec's bar, and the ``--own-module`` values; ``profile show``
    reads them from the saved result.
``unprofiled``
    ``{"wall_seconds", "cpu_seconds", "cpu_fraction"}`` of the
    unprofiled run. CPU time is user plus system time summed over
    every thread and every process waited for, so ``cpu_fraction``
    (CPU over wall time) exceeds 1 when other threads used CPU, as an
    idle OpenBLAS pool's spinning workers do; it then shows no waiting.
``profiled_wall_seconds``
    Wall time of the profiled run.
``load_average``, ``cpu_count``
    One-minute load average before the runs, and logical CPUs; ``null``
    when unknown.
``side_effects``
    The `perfproof.schema.GateResult` of the ``side_effects`` gate.
``summary``
    `perfproof.profilers.summary` in its brief form, at most
    `perfproof.profilers.summary.BRIEF_BYTES` bytes: the top frames with
    their share and Amdahl cap (a bound, never a measured effect), and
    wall against CPU time. An agent reads this, not ``hotspots``.
``evidence``
    ``{"path", "sha256"}`` of the summary stored as estimate evidence in
    ``.perfproof/evidence/<name>/``, never as a verdict.
``hypothesis``
    The ID given with ``--hypothesis``, or ``null``.
``argv``
    The profiled run's argv.
``run_id``, ``finished_at``
    The run directory's name (the ``<id>`` above), and the UTC time the
    result was written, as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``. A profile
    that finished before an edit has a ``finished_at`` earlier than the
    edit's time on the same machine's clock.
``directory``, ``raw``, ``folded``, ``logs``
    Absolute paths of the run's directory, the profiler's output, the
    folded stacks summarised and the runs' stdout and stderr.

Error codes
-----------
``not_a_repository``, ``invalid_name``, ``not_found`` (no spec, or no
spec with that hash), ``ambiguous_hash`` (a hash prefix, or no hash
with several specs stored), ``invalid_record``, ``state_read_failed``,
``state_write_failed``, ``invalid_result`` (the summary or its
evidence record cannot be built), ``invalid_command`` (a
``--profiler-command`` that is not a JSON array of strings with
``{command}``), and the codes
of `perfproof.profilers.run.ProfileError`: ``profiler_unavailable``,
``side_effects``, ``reset_failed``, ``workload_failed``,
``profiler_failed`` and ``invalid_profile``.
"""

import argparse
import json
import math
import os
import re
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from perfproof.bench.evidence import hypothesis_id, write_evidence
from perfproof.bench.snapshot import (
    GIT_TIMEOUT_SECONDS,
    SnapshotError,
    exclude_state_dir,
)
from perfproof.bench.spec import (
    STATE_DIR,
    SpecRecordError,
    load_spec,
    spec_record_path,
)
from perfproof.commands import CommandError, JsonValue, Output
from perfproof.commands._profile_show import RESULT_FILE, show
from perfproof.profilers.folded import DEFAULT_TOP_N
from perfproof.profilers.run import (
    Profile,
    ProfileError,
    ProfilerName,
    default_profiler,
    profile_benchmark,
)
from perfproof.profilers.summary import brief, summarise_profile
from perfproof.schema import BenchmarkSpec, Hotspot

__all__ = ["RESULT_FILE", "add_arguments", "run"]

_HASH_FILE: Final = re.compile(r"^[0-9a-f]{64}\.json$")
_HEX: Final = re.compile(r"^[0-9a-f]+$")
_ZERO_HASH: Final = "0" * 64
_BASIS_TEXT: Final = {
    "active": "share of py-spy's samples of active threads",
    "wall": "share of the main thread's wall-clock time",
    "unknown": "share of the profiler's samples",
}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the benchmark name and the profiler options.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Run a profiler on a benchmark and summarise the top frames with "
        "file:line and share of time."
    )
    parser.add_argument(
        "name",
        help='benchmark name; or "show" to look up a function in a saved '
        "profile: profile show NAME [RUN_ID] --function PATTERN",
    )
    parser.add_argument(
        "show_args",
        nargs="*",
        metavar="NAME [RUN_ID]",
        help="with show: the benchmark and, optionally, the run",
    )
    parser.add_argument(
        "--function",
        metavar="PATTERN",
        help="with show: function to find, such as createChildLogger or "
        "Echo.ServeHTTP; its self and total share come from the run's "
        "folded stacks",
    )
    parser.add_argument(
        "--spec-hash",
        metavar="HASH",
        help="spec version, by hash or unique prefix (default: the only one)",
    )
    chosen = parser.add_mutually_exclusive_group()
    chosen.add_argument(
        "--profiler",
        choices=("auto", "py-spy", "pyinstrument"),
        default="auto",
        help="built-in profiler (default: py-spy on Linux, pyinstrument "
        "on macOS)",
    )
    chosen.add_argument(
        "--profiler-command",
        metavar="JSON",
        help='argv template as a JSON array, with "{command}" and "{output}"',
    )
    parser.add_argument(
        "--top",
        type=_positive_int,
        default=DEFAULT_TOP_N,
        metavar="N",
        help=f"frames in each list (default: {DEFAULT_TOP_N})",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        metavar="SECONDS",
        help="time limit for each run (default: the spec's)",
    )
    parser.add_argument(
        "--hypothesis",
        type=hypothesis_id,
        metavar="ID",
        help="hypothesis this profile is evidence for",
    )
    parser.add_argument(
        "--own-module",
        action="append",
        default=[],
        metavar="NAME",
        help="native binary that is project code; repeatable",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )


def run(args: argparse.Namespace) -> Output:
    """Profile one stored benchmark spec.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed options.

    Returns
    -------
    Output
        The result described in this module's docstring.

    Raises
    ------
    CommandError
        With one of the codes in this module's docstring.
    """
    name = cast("str", args.name)
    extra = cast("list[str]", args.show_args)
    function = cast("str | None", args.function)
    if name == "show" and (extra or function is not None):
        if not 1 <= len(extra) <= 2:
            msg = "usage: profile show NAME [RUN_ID] --function PATTERN"
            raise CommandError("invalid_argument", msg)
        return show(
            _repo_root(cast("Path", args.repo)),
            extra[0],
            extra[1] if len(extra) > 1 else None,
            function,
        )
    if extra or function is not None:
        msg = (
            "profile takes one benchmark name; NAME [RUN_ID] and "
            "--function belong to `profile show`"
        )
        raise CommandError("invalid_argument", msg)
    template = _template(cast("str | None", args.profiler_command))
    root = _repo_root(cast("Path", args.repo))
    spec = _spec(root, name, cast("str | None", args.spec_hash))
    try:
        profiler: ProfilerName = (
            "command"
            if template
            else default_profiler()
            if args.profiler == "auto"
            else cast("ProfilerName", args.profiler)
        )
    except ProfileError as exc:
        raise CommandError(exc.code, exc.message) from exc
    out_dir = _new_directory(root, name)
    try:
        profile = profile_benchmark(
            spec,
            root,
            out_dir,
            profiler=profiler,
            process_env=os.environ,
            template=template,
            timeout_seconds=cast("float | None", args.timeout),
            top_n=cast("int", args.top),
            own_modules=cast("list[str]", args.own_module),
        )
    except ProfileError as exc:
        details: dict[str, JsonValue] = {"directory": str(out_dir)}
        if exc.gate is not None:
            details["gate"] = exc.gate.model_dump(mode="json")
        raise CommandError(exc.code, exc.message, details) from exc
    try:
        summary = brief(
            summarise_profile(
                profile.hotspots,
                basis=profile.basis,
                weight_unit=profile.weight_unit,
                wall_seconds=profile.unprofiled.wall_seconds,
                cpu_seconds=profile.unprofiled.cpu_seconds,
                load_average=profile.load_average,
                cpu_count=profile.cpu_count,
                min_effect=spec.min_effect,
            )
        )
    except ValueError as exc:
        msg = f"the profile could not be summarised: {exc}"
        raise CommandError("invalid_result", msg) from exc
    hypothesis = cast("str | None", args.hypothesis)
    try:
        ref = write_evidence(
            root,
            name,
            "profile",
            "estimate",
            {"summary": summary, "profiler": profile.profiler},
            hypothesis=hypothesis,
            spec_hash=spec.spec_hash,
        )
    except OSError as exc:
        raise _os_error("state_write_failed", exc, out_dir) from exc
    except ValueError as exc:
        msg = f"the profile could not be stored as evidence: {exc}"
        raise CommandError("invalid_result", msg) from exc
    result = dict(_result(spec, profile, out_dir))
    result["summary"] = summary
    result["evidence"] = {"path": str(ref.path), "sha256": ref.sha256}
    result["hypothesis"] = hypothesis
    result["min_effect"] = spec.min_effect
    result["own_modules"] = list(cast("list[str]", args.own_module))
    result["run_id"] = out_dir.name
    result["finished_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _save(out_dir, result)
    return Output(result=result, text=_text(spec, profile, out_dir))


def _save(out_dir: Path, result: dict[str, JsonValue]) -> None:
    """Write the result to ``result.json`` in the run directory.

    The file is written whole to a temporary name and renamed, so it
    exists complete or not at all.

    Raises
    ------
    CommandError
        ``state_write_failed``, if the file cannot be written.
    """
    path = out_dir / RESULT_FILE
    temporary = out_dir / f".{RESULT_FILE}.tmp"
    data = json.dumps(result, indent=2, allow_nan=False) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise _os_error("state_write_failed", exc, path) from exc


def _positive_int(text: str) -> int:
    """Parse an integer of at least 1, for argparse."""
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        msg = f"expected an integer of at least 1, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return value


def _positive_seconds(text: str) -> float:
    """Parse a positive, finite number of seconds, for argparse."""
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not (math.isfinite(value) and value > 0):
        msg = f"expected a positive number of seconds, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    return value


def _template(given: str | None) -> tuple[str, ...]:
    """Parse ``--profiler-command``; empty when it is not given."""
    if given is None:
        return ()
    try:
        value: object = json.loads(given)
    except json.JSONDecodeError as exc:
        msg = f"--profiler-command is not JSON: {exc}"
        raise CommandError("invalid_command", msg) from exc
    if not (
        isinstance(value, list)
        and value
        and all(isinstance(a, str) for a in cast("list[object]", value))
    ):
        msg = "--profiler-command must be a non-empty JSON array of strings"
        raise CommandError("invalid_command", msg)
    argv = tuple(cast("list[str]", value))
    if "{command}" not in argv:
        msg = (
            '--profiler-command must contain the argument "{command}", '
            "where the benchmark's argv goes"
        )
        raise CommandError("invalid_command", msg)
    if not any("{output}" in arg for arg in argv):
        msg = (
            '--profiler-command must contain "{output}", the file the '
            "profiler writes its folded stacks to; its stdout also carries "
            "what the benchmark prints, so it is never read as a profile"
        )
        raise CommandError("invalid_command", msg)
    return argv


def _repo_root(repo: Path) -> Path:
    """Return the root of the git working tree containing ``repo``."""
    argv = ["git", "-C", str(repo), "rev-parse", "--show-toplevel"]
    try:
        done = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            errors="surrogateescape",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        msg = f"git rev-parse did not finish in {GIT_TIMEOUT_SECONDS} s"
        raise CommandError("not_a_repository", msg) from exc
    except OSError as exc:
        msg = f"git could not be started: {exc}"
        raise CommandError("not_a_repository", msg) from exc
    if done.returncode != 0:
        msg = (
            f"{repo} is not in a git working tree: {done.stderr.strip()}; "
            "perfproof keeps benchmarks in the repository's .perfproof/"
        )
        raise CommandError("not_a_repository", msg, {"repo": str(repo)})
    return Path(done.stdout.strip())


def _spec(root: Path, name: str, prefix: str | None) -> BenchmarkSpec:
    """Load the spec version to profile."""
    try:
        directory = spec_record_path(root, name, _ZERO_HASH).parent
    except ValueError as exc:
        raise CommandError("invalid_name", str(exc), {"name": name}) from exc
    if prefix is not None and not _HEX.fullmatch(prefix):
        msg = f"{prefix!r} is not a spec hash or a prefix of one"
        raise CommandError("not_found", msg, {"name": name})
    try:
        hashes = (
            sorted(
                p.stem
                for p in directory.iterdir()
                if _HASH_FILE.fullmatch(p.name)
            )
            if directory.is_dir()
            else []
        )
    except OSError as exc:
        raise _os_error("state_read_failed", exc, directory) from exc
    matches = [h for h in hashes if prefix is None or h.startswith(prefix)]
    if not matches:
        shown = "" if prefix is None else f" with hash {prefix}"
        msg = f"no spec{shown} is defined for benchmark {name!r} in {root}"
        raise CommandError("not_found", msg, {"name": name})
    if len(matches) > 1:
        candidates: list[JsonValue] = list(matches)
        msg = (
            f"benchmark {name!r} has {len(matches)} specs"
            + ("" if prefix is None else f" matching {prefix}")
            + "; choose one with --spec-hash"
        )
        raise CommandError(
            "ambiguous_hash", msg, {"name": name, "matches": candidates}
        )
    path = directory / f"{matches[0]}.json"
    try:
        spec, _ = load_spec(root, name, matches[0])
    except SpecRecordError as exc:
        raise CommandError(
            "invalid_record", str(exc), {"path": str(path)}
        ) from exc
    except OSError as exc:
        raise _os_error("state_read_failed", exc, path) from exc
    return spec


def _new_directory(root: Path, name: str) -> Path:
    """Create this run's directory under ``.perfproof/profiles``."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = (
        root
        / STATE_DIR
        / "profiles"
        / name
        / (f"{stamp}-{secrets.token_hex(3)}")
    )
    try:
        exclude_state_dir(root)
    except SnapshotError as exc:
        raise CommandError("not_a_repository", str(exc)) from exc
    except OSError as exc:
        raise _os_error("state_write_failed", exc, root / ".git") from exc
    try:
        path.mkdir(parents=True)
    except OSError as exc:
        raise _os_error("state_write_failed", exc, path) from exc
    return path


def _os_error(code: str, exc: OSError, path: Path) -> CommandError:
    """Turn an operating system refusal into a `CommandError`."""
    where = str(path) if exc.filename is None else str(exc.filename)
    reason = exc.strerror or str(exc)
    return CommandError(code, f"{where}: {reason}", {"path": where})


def _result(
    spec: BenchmarkSpec, profile: Profile, out_dir: Path
) -> dict[str, JsonValue]:
    """Build the ``--json`` result."""
    plain = profile.unprofiled
    hotspots: list[JsonValue] = [
        h.model_dump(mode="json") for h in profile.hotspots
    ]
    by_total: list[JsonValue] = [
        h.model_dump(mode="json") for h in profile.frames_by_total
    ]
    above: list[JsonValue] = [
        h.model_dump(mode="json") for h in profile.project_frames_above_bar
    ]
    return {
        "name": spec.name,
        "spec_hash": spec.spec_hash,
        "profiler": profile.profiler,
        "tool_version": profile.tool_version,
        "basis": profile.basis,
        "weight_unit": profile.weight_unit,
        "hotspots": hotspots,
        "frames_by_total": by_total,
        "project_frames_above_bar": above,
        "unprofiled": {
            "wall_seconds": plain.wall_seconds,
            "cpu_seconds": plain.cpu_seconds,
            "cpu_fraction": plain.cpu_seconds / plain.wall_seconds,
        },
        "profiled_wall_seconds": profile.profiled_wall_seconds,
        "load_average": profile.load_average,
        "cpu_count": profile.cpu_count,
        "side_effects": profile.side_effects.model_dump(mode="json"),
        "argv": list(profile.argv),
        "directory": str(out_dir),
        "raw": str(profile.raw_path),
        "folded": str(profile.folded_path),
        "logs": [str(p) for p in profile.log_paths],
    }


def _location(hotspot: Hotspot) -> str:
    """Show where a frame is: ``file:line``, ``file`` or nothing."""
    if hotspot.file is None:
        return "-"
    if hotspot.line is None:
        return hotspot.file
    return f"{hotspot.file}:{hotspot.line}"


def _row(h: Hotspot) -> str:
    """Render one frame of a table: shares, location, name."""
    total = "" if h.total_fraction is None else f"{h.total_fraction:.1%}"
    library = "  (library)" if h.is_library else ""
    return (
        f"  {h.self_fraction:5.1%} {total:>6}  {_location(h)}  "
        f"{h.function}{library}"
    )


def _text(spec: BenchmarkSpec, profile: Profile, out_dir: Path) -> str:
    """Render the profile for a person."""
    version = profile.tool_version or "version unknown"
    lines = [
        f"Profile of {spec.name} (spec {spec.spec_hash[:12]}) with "
        f"{profile.profiler} {version}.",
        f"Top frames by self time ({_BASIS_TEXT[profile.basis]}):",
        "   self  total  location  function",
    ]
    lines.extend(_row(h) for h in profile.hotspots)
    lines.extend(
        [
            "Top frames by total time (with callees; a frame can be here "
            "with no self time):",
            "   self  total  location  function",
        ]
    )
    lines.extend(_row(h) for h in profile.frames_by_total)
    plain = profile.unprofiled
    share = plain.cpu_seconds / plain.wall_seconds
    lines.append(
        f"Unprofiled run: wall {plain.wall_seconds:.3g} s, CPU "
        f"{plain.cpu_seconds:.3g} s (summed over all threads and "
        "processes)."
    )
    if share <= 1:
        lines.append(
            f"For at least {1 - share:.0%} of wall time no thread of the "
            "benchmark was on a CPU: it was waiting, not computing."
        )
    else:
        lines.append(
            f"CPU time is {share:.2f}x wall time: other threads used CPU "
            "alongside the main one (a thread pool such as BLAS's can, "
            "even while idle), so this gap cannot show waiting."
        )
    load, cpus = profile.load_average, profile.cpu_count
    if load is not None and cpus is not None:
        lines.append(f"Load average {load:.1f} on {cpus} CPUs.")
        if load > cpus:
            lines.append(
                "The load exceeds the CPU count, so part of any gap "
                "between wall and CPU time is waiting for a CPU."
            )
    lines.append(
        f"Profiled run: wall {profile.profiled_wall_seconds:.3g} s. "
        f"Files: {out_dir}"
    )
    return "\n".join(lines)
