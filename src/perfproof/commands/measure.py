"""``perfproof measure``: cheap tests that bound or size a hypothesis.

``measure NAME`` takes a benchmark spec stored by ``bench define`` and
runs it, in the repository's working tree as it is, under one of three
variations. Each result is an **estimate** or a **bound**, stored as
evidence in ``.perfproof/evidence/<NAME>/`` and never as a verdict: the
output carries no direction (nothing is called faster or slower),
because only ``compare``, through its gates, decides that. Every
measurement is labelled with the load average at its start and end and
as not calibrated.

``--sizes LIST --size-arg=FLAG=BASE`` (or ``--size-env NAME=BASE``)
    Runs the benchmark at ``BASE`` times each multiplier of ``LIST``
    (``1,2,4,8``; at least three distinct, an ``x`` suffix is allowed)
    and fits how the metric grows with size, ``metric ~ size**e``,
    reporting the exponent ``e`` with a 95% interval. An estimate.
``--stub MODULE:QUALNAME``
    Replays one Python function from calls captured in a run of the
    benchmark and reports a **cap**: the benchmark's metric with that
    function's cost taken out, as a bound on what any change to the
    function alone could give: the upper end of the interval. No cap is
    given (``cap`` is ``null``, ``cap_withheld`` is ``uninformative``)
    when what is left cannot be told from zero or the hook's own cost is
    as large as it. See `perfproof.bench.stub`.
``--set NAME=VALUE``
    Runs the benchmark as it is and with one environment variable set,
    interleaved, and reports the unchanged side's metric over the
    changed side's with a 95% interval. An estimate.

Each variation runs the benchmark in interleaved rounds through the
runner, two executions of each variant per round.

``--json`` result
-----------------
``name``, ``spec_hash``, ``kind`` (``sizes``, ``stub`` or ``set``)
    The benchmark measured, and what was done.
``claim``
    ``estimate`` for ``sizes`` and ``set``; ``bound`` for ``stub``.
``calibrated``
    Always ``false``.
``label``
    One sentence naming the kind of number.
``hypothesis``
    The ID given with ``--hypothesis``, or ``null``.
``evidence``
    ``{"path", "sha256"}`` of the stored record.
``machine``
    ``load_average_start``, ``load_average_end`` (one-minute, ``null``
    when unknown) and ``cpu_count``.
``metric``
    The spec's metric ``kind`` and ``unit``.
``seed``, ``directory``
    The seed of the per-round order, and the run's directory, with its
    logs.
``sizes``, ``stub`` or ``setting``
    The result itself; the key is that of ``kind`` (``set`` is
    ``setting``). Every interval is 95%.

Error codes
-----------
``not_a_repository``, ``invalid_name``, ``not_found``,
``ambiguous_hash``, ``invalid_record``, ``state_read_failed``,
``state_write_failed``, ``invalid_result`` (a result that cannot be
stored as JSON, such as a non-finite number), and the codes of
`perfproof.bench.conditions.MeasureError`: ``invalid_arguments``,
``invalid_target``, ``unsupported_metric``, ``unsupported_spec``,
``side_effects``, ``capture_failed``, ``hook_not_loaded``,
``target_not_found``, ``no_calls``, ``workload_failed`` and
``runner_failed``.
"""

import argparse
import math
import os
import re
import secrets
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from perfproof.bench.conditions import MeasureError
from perfproof.bench.estimates import LEVEL, Estimate
from perfproof.bench.evidence import Claim, hypothesis_id, write_evidence
from perfproof.bench.scaling import (
    SizeKnob,
    SizesResult,
    measure_sizes,
    parse_knob,
)
from perfproof.bench.setting import (
    SettingResult,
    measure_setting,
    parse_setting,
)
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
from perfproof.bench.stub import StubResult, stub_function
from perfproof.commands import CommandError, JsonValue, Output
from perfproof.schema import BenchmarkSpec

__all__ = ["add_arguments", "run"]

_HASH_FILE: Final = re.compile(r"^[0-9a-f]{64}\.json$")
_HEX: Final = re.compile(r"^[0-9a-f]+$")
_ZERO_HASH: Final = "0" * 64
_DEFAULT_ROUNDS: Final = {"sizes": 5, "stub": 8, "set": 8}
_LABELS: Final = {
    "sizes": "estimate: a fit to a few runs on a machine that may be loaded",
    "set": "estimate: a few runs on a machine that may be loaded",
    "stub": "bound: the cost of the function taken out of the workload; "
    "not a measured effect of any change",
}
_NOT_CALIBRATED: Final = "not a calibrated measurement"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the benchmark name and the measurement options.

    Parameters
    ----------
    parser : argparse.ArgumentParser
        The command's parser.
    """
    parser.description = (
        "Measure one version of a benchmark: how it scales with input "
        "size (--sizes), what it costs with one function replayed from "
        "captured calls (--stub), or with one setting changed (--set). "
        "Results are estimates or bounds, stored as evidence."
    )
    parser.add_argument("name", help="benchmark name")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument(
        "--sizes",
        type=_factors,
        metavar="LIST",
        help="multipliers of the base size, such as 1,2,4,8 (at least 3)",
    )
    what.add_argument(
        "--stub",
        metavar="MODULE:QUALNAME",
        help="Python function to replay from captured calls",
    )
    what.add_argument(
        "--set",
        metavar="NAME=VALUE",
        dest="setting",
        help="environment variable to change",
    )
    knob = parser.add_mutually_exclusive_group()
    knob.add_argument(
        "--size-arg",
        metavar="FLAG=BASE",
        help="flag that sets the input size, appended as 'FLAG size'; "
        "write it as --size-arg=--n=1000",
    )
    knob.add_argument(
        "--size-env",
        metavar="NAME=BASE",
        help="environment variable that sets the input size",
    )
    parser.add_argument(
        "--rounds",
        type=_positive_int,
        metavar="N",
        help="interleaved rounds, two executions of each variant per "
        "round (default: 5 for --sizes, 8 otherwise)",
    )
    parser.add_argument(
        "--hypothesis",
        type=hypothesis_id,
        metavar="ID",
        help="hypothesis this is evidence for",
    )
    parser.add_argument(
        "--spec-hash",
        metavar="HASH",
        help="spec version, by hash or unique prefix (default: the only one)",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        metavar="SECONDS",
        help="time limit for each execution (default: the spec's)",
    )
    parser.add_argument(
        "--seed",
        type=_non_negative_int,
        metavar="N",
        help="seed for the order of variants within rounds (default: random)",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(),
        help="any path in the repository (default: .)",
    )


def run(args: argparse.Namespace) -> Output:
    """Run one measurement on one stored benchmark spec.

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
    sizes = cast("tuple[int, ...] | None", args.sizes)
    stub = cast("str | None", args.stub)
    setting = cast("str | None", args.setting)
    kind = "sizes" if sizes is not None else "stub" if stub else "set"
    try:
        knob = _knob(args) if sizes is not None else None
        pair = parse_setting(setting) if setting is not None else None
    except MeasureError as exc:
        raise CommandError(exc.code, exc.message, _details(exc)) from exc
    root = _repo_root(cast("Path", args.repo))
    spec = _spec(root, name, cast("str | None", args.spec_hash))
    rounds = cast("int | None", args.rounds) or _DEFAULT_ROUNDS[kind]
    seed = cast("int | None", args.seed)
    if seed is None:
        seed = secrets.randbits(32)
    work_dir = _new_directory(root, name, kind)
    timeout = cast("float | None", args.timeout)
    load_start = _load_average()
    block: dict[str, JsonValue]
    claim: Claim
    text: list[str]
    try:
        if sizes is not None and knob is not None:
            found = measure_sizes(
                spec,
                root,
                knob,
                sizes,
                rounds=rounds,
                work_dir=work_dir,
                seed=seed,
                process_env=os.environ,
                timeout_seconds=timeout,
            )
            claim, key, block, text = "estimate", "sizes", *_sizes(found)
        elif stub is not None:
            bound = stub_function(
                spec,
                root,
                stub,
                rounds=rounds,
                work_dir=work_dir,
                seed=seed,
                process_env=os.environ,
                timeout_seconds=timeout,
            )
            claim, key, block, text = "bound", "stub", *_stub(bound)
        elif pair is not None:
            changed = measure_setting(
                spec,
                root,
                *pair,
                rounds=rounds,
                work_dir=work_dir,
                seed=seed,
                process_env=os.environ,
                timeout_seconds=timeout,
            )
            claim, key, block, text = "estimate", "setting", *_setting(changed)
        else:
            msg = "no measurement was chosen"
            raise MeasureError("invalid_arguments", msg)
    except MeasureError as exc:
        raise CommandError(exc.code, exc.message, _details(exc)) from exc
    load_end = _load_average()
    machine: dict[str, JsonValue] = {
        "load_average_start": load_start,
        "load_average_end": load_end,
        "cpu_count": os.cpu_count(),
    }
    hypothesis = cast("str | None", args.hypothesis)
    try:
        ref = write_evidence(
            root,
            name,
            kind,
            claim,
            {key: block, "machine": machine, "seed": seed},
            hypothesis=hypothesis,
            spec_hash=spec.spec_hash,
        )
    except OSError as exc:
        raise _os_error("state_write_failed", exc, work_dir) from exc
    except ValueError as exc:
        msg = f"the result could not be stored as evidence: {exc}"
        raise CommandError("invalid_result", msg) from exc
    result: dict[str, JsonValue] = {
        "name": spec.name,
        "spec_hash": spec.spec_hash,
        "kind": kind,
        "claim": claim,
        "calibrated": False,
        "label": f"{_LABELS[kind]}; {_NOT_CALIBRATED}",
        "hypothesis": hypothesis,
        "evidence": {"path": str(ref.path), "sha256": ref.sha256},
        "machine": machine,
        "metric": {"kind": spec.metric.kind, "unit": spec.metric.unit},
        "seed": seed,
        "directory": str(work_dir),
        key: block,
    }
    text.append(_load_text(load_start, load_end, os.cpu_count()))
    text.append(
        f"Stored as {claim} evidence"
        + ("" if hypothesis is None else f" for {hypothesis}")
        + f": {ref.path}"
    )
    return Output(result=result, text="\n".join(text))


def _details(exc: MeasureError) -> dict[str, JsonValue]:
    """Turn an error's context into JSON values."""
    return {k: str(v) for k, v in exc.details.items()}


def _knob(args: argparse.Namespace) -> SizeKnob:
    """Read the size knob for ``--sizes``."""
    arg = cast("str | None", args.size_arg)
    env = cast("str | None", args.size_env)
    if arg is not None:
        return parse_knob("arg", arg)
    if env is not None:
        return parse_knob("env", env)
    msg = "--sizes needs --size-arg FLAG=BASE or --size-env NAME=BASE"
    raise MeasureError("invalid_arguments", msg)


def _est(e: Estimate | None) -> JsonValue:
    """Show an estimate with its interval."""
    if e is None:
        return None
    return {
        "value": e.value,
        "low": e.low,
        "high": e.high,
        "level": LEVEL,
        "n": e.n,
    }


def _g(x: float | None) -> str:
    """Format a number to three significant digits."""
    return "unknown" if x is None else f"{x:.3g}"


def _range(e: Estimate) -> str:
    """Format an estimate with its interval."""
    if e.low is None or e.high is None:
        return f"{_g(e.value)} (no interval from {e.n} value)"
    return f"{_g(e.value)} ({LEVEL:.0%} interval {_g(e.low)} to {_g(e.high)})"


def _sizes(r: SizesResult) -> tuple[dict[str, JsonValue], list[str]]:
    """Build the result block and text of a scaling run."""
    fit = r.fit
    block: dict[str, JsonValue] = {
        "knob": {
            "kind": r.knob.kind,
            "name": r.knob.name,
            "base": r.knob.base,
        },
        "factors": list(r.factors),
        "sizes": list(r.sizes),
        "rounds": r.rounds,
        "measurements": fit.n,
        "exponent": fit.exponent,
        "interval": {"low": fit.low, "high": fit.high, "level": LEVEL},
        "r_squared": fit.r_squared,
        "residual_sd_log": fit.residual_sd,
        "medians": list(fit.medians),
        "local_exponents": list(fit.local_exponents),
        "unit": r.unit,
        "notes": list(r.notes),
    }
    text = [
        f"Growth of the {r.metric_kind} metric with {r.knob.name}: the "
        f"fitted exponent is {fit.exponent:.3g} ({LEVEL:.0%} interval "
        f"{fit.low:.3g} to {fit.high:.3g}) over sizes "
        f"{', '.join(str(s) for s in r.sizes)}, from {fit.n} runs. An "
        "estimate.",
        "Exponent between neighbouring sizes: "
        + ", ".join(f"{x:.3g}" for x in fit.local_exponents)
        + ".",
        *r.notes,
    ]
    return block, text


def _stub(r: StubResult) -> tuple[dict[str, JsonValue], list[str]]:
    """Build the result block and text of a stub run."""
    block: dict[str, JsonValue] = {
        "function": r.function,
        "rounds": r.rounds,
        "unit": r.unit,
        "baseline": _est(r.baseline),
        "stub_run": _est(r.stub_run),
        "remaining": _est(r.remaining),
        "cap": _est(r.cap),
        "cap_bound": r.cap_bound,
        "cap_withheld": r.cap_withheld,
        "cap_lower_limit": r.cap_lower_limit,
        "cap_uncorrected": _est(r.cap_uncorrected),
        "removed_share": r.removed_share,
        "recorded_calls": r.recorded_calls,
        "calls_per_run": r.calls_per_run,
        "processes_per_run": r.processes_per_run,
        "replayed_per_run": r.replayed_per_run,
        "real_per_run": r.real_per_run,
        "real_seconds_per_run": r.real_seconds_per_run,
        "overhead_seconds_per_run": r.overhead_seconds_per_run,
        "install_seconds_per_run": r.install_seconds_per_run,
        "captured_mutating_calls": r.capture_mutating_calls,
        "captured_unpicklable_calls": r.capture_unpicklable,
        "side_effects_skipped": list(r.effects),
        "notes": list(r.notes),
    }
    text = [
        f"Bound for {r.function}, replayed from captured calls "
        f"({r.calls_per_run:.4g} calls per run: {r.replayed_per_run:.4g} "
        f"replayed, {r.real_per_run:.4g} run for real).",
        f"Benchmark {r.metric_kind} metric: {_range(r.baseline)} "
        f"{r.unit}. With the function's cost taken out: "
        + ("not resolved" if r.remaining is None else _range(r.remaining))
        + (f" {r.unit}." if r.remaining is not None else "."),
    ]
    if r.cap is not None and r.cap_bound is not None:
        text.append(
            f"Cap on any change to this function alone: {_g(r.cap_bound)}, "
            f"in old time over new time. It is the upper end of the "
            f"{LEVEL:.0%} interval ({_range(r.cap)}), a bound and not a "
            "measured effect of a change."
        )
    else:
        text.append(
            "No cap can be given: this measurement cannot tell what is "
            "left from zero."
            + (
                ""
                if r.cap_lower_limit is None
                else f" A lower limit on the cap is {_g(r.cap_lower_limit)}."
            )
        )
    text.extend(r.notes)
    return block, text


def _setting(r: SettingResult) -> tuple[dict[str, JsonValue], list[str]]:
    """Build the result block and text of a setting run."""
    block: dict[str, JsonValue] = {
        "name": r.name,
        "value": r.value,
        "declared_in_spec": r.declared,
        "rounds": r.rounds,
        "unit": r.unit,
        "unchanged": _est(r.unchanged),
        "changed": _est(r.changed),
        "ratio_unchanged_over_changed": _est(r.ratio),
    }
    was = (
        "its value in the spec is unknown to perfproof"
        if r.declared is None
        else f"the spec sets it to {r.declared!r}"
    )
    text = [
        f"Setting {r.name}={r.value} ({was}).",
        f"The {r.metric_kind} metric unchanged: {_range(r.unchanged)} "
        f"{r.unit}; changed: {_range(r.changed)} {r.unit}.",
        f"Unchanged over changed: {_range(r.ratio)}. An estimate from "
        f"{r.rounds} rounds.",
    ]
    return block, text


def _load_average() -> float | None:
    """Return the one-minute load average, or ``None`` if unknown."""
    try:
        return os.getloadavg()[0]
    except OSError:
        return None


def _load_text(
    start: float | None, end: float | None, cpus: int | None
) -> str:
    """Say what the machine was doing, and that this is uncalibrated."""
    shown = "unknown" if start is None else f"{start:.1f}"
    after = "unknown" if end is None else f"{end:.1f}"
    on = "" if cpus is None else f" on {cpus} CPUs"
    return (
        f"Load average {shown} at the start and {after} at the end{on}; "
        f"{_NOT_CALIBRATED}."
    )


def _factors(text: str) -> tuple[int, ...]:
    """Parse ``--sizes``, for argparse."""
    try:
        values = tuple(
            int(part.strip().removesuffix("x"))
            for part in text.split(",")
            if part.strip()
        )
    except ValueError:
        values = ()
    if len(set(values)) < 3 or min(values, default=0) < 1:
        msg = (
            "expected at least three distinct integer multipliers of 1 or "
            f"more, such as 1,2,4,8, got {text!r}"
        )
        raise argparse.ArgumentTypeError(msg)
    return values


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


def _non_negative_int(text: str) -> int:
    """Parse an integer of at least 0, for argparse."""
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value < 0:
        msg = f"expected an integer of at least 0, got {text!r}"
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
    """Load the spec version to measure."""
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


def _new_directory(root: Path, name: str, kind: str) -> Path:
    """Create this run's directory under ``.perfproof/measure``."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = (
        root
        / STATE_DIR
        / "measure"
        / name
        / f"{kind}-{stamp}-{secrets.token_hex(3)}"
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
