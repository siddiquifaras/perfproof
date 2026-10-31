"""The ``find-*`` specs, and the user's specs `find` reads.

A ``find-*`` spec exists so ``profile`` has something to run: it is
profile-only (``compare`` refuses it), so its schedule values never
decide anything. They come from the part's class so the spec is a valid
one. User workloads are every spec whose name does not start with
``find-``.
"""

import argparse
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from perfproof.bench.spec import STATE_DIR, SpecRecordError, load_spec
from perfproof.commands import CommandError, bench
from perfproof.schema import (
    CLASS_SCHEDULES,
    BenchmarkSpec,
    Budget,
    CorrectnessSpec,
    LookSchedule,
    MetricSpec,
    TargetClass,
)

__all__ = [
    "BENCH_TIMEOUT_SECONDS",
    "FIND_PREFIX",
    "NO_BYTECODE",
    "UserSpec",
    "bench_spec_name",
    "build_spec",
    "define_spec",
    "is_find_name",
    "safe_name",
    "tests_spec_name",
    "user_specs",
]

NO_BYTECODE: Final = {"PYTHONDONTWRITEBYTECODE": "1"}
"""Declared for the Python probes, so they write no ``__pycache__``."""
FIND_PREFIX: Final = "find-"
BENCH_TIMEOUT_SECONDS: Final = 300.0
"""Time limit of an existing benchmark's probe, per run."""
_UNSAFE: Final = re.compile(r"[^A-Za-z0-9._-]")
_FALLBACK_CLASS: Final[TargetClass] = "python_cpu"


@dataclass(frozen=True, slots=True)
class UserSpec:
    """One user workload: the newest stored version of a spec name.

    Attributes
    ----------
    name : str
        The benchmark name.
    spec : perfproof.schema.BenchmarkSpec
        Its newest stored version.
    """

    name: str
    spec: BenchmarkSpec


def is_find_name(name: str) -> bool:
    """Whether a spec name belongs to `find` (``find-*``)."""
    return name.startswith(FIND_PREFIX)


def safe_name(text: str) -> str:
    """Spell ``text`` with characters a spec name allows."""
    return _UNSAFE.sub("-", text)


def tests_spec_name(part_id: str) -> str:
    """Return ``find-tests-<part id>``."""
    return f"{FIND_PREFIX}tests-{part_id}"


def bench_spec_name(part_id: str, benchmark: str) -> str:
    """Return ``find-bench-<part id>-<benchmark>``."""
    return f"{FIND_PREFIX}bench-{part_id}-{safe_name(benchmark)}"


def build_spec(
    *,
    name: str,
    command: tuple[str, ...],
    cwd: str,
    harness: str,
    target_class: TargetClass | None,
    timeout_seconds: float,
    env: Mapping[str, str] | None = None,
) -> BenchmarkSpec:
    """Build a ``find-*`` spec from the contract's constants.

    Parameters
    ----------
    name : str
        The spec name.
    command : tuple of str
        The argv to profile.
    cwd : str
        Working directory relative to the repository.
    harness : str
        The part's first manifest, repository-relative; it is the one
        locked file.
    target_class : str or None
        The part's class; the schedule of ``python_cpu`` is used when it
        is ``None``.
    timeout_seconds : float
        Per-run limit, also the budget's ``max_seconds``.
    env : Mapping of str to str, optional
        Environment the spec declares.

    Returns
    -------
    perfproof.schema.BenchmarkSpec
        Wall metric, no warmup, no correctness command.

    Raises
    ------
    CommandError
        ``invalid_spec`` if the pieces do not make a valid spec.
    """
    n0, k, looks, rounds = CLASS_SCHEDULES[target_class or _FALLBACK_CLASS]
    try:
        return BenchmarkSpec(
            name=name,
            command=command,
            cwd=cwd,
            env=dict(env or {}),
            metric=MetricSpec(
                kind="wall", direction="lower_is_better", unit="s"
            ),
            correctness=CorrectnessSpec(),
            warmup=0,
            budget=Budget(max_rounds=rounds, max_seconds=timeout_seconds),
            looks=LookSchedule(n0=n0, k=k, max_looks=looks),
            timeout_seconds=timeout_seconds,
            harness=(harness,),
            target_class=target_class,
        )
    except ValidationError as exc:
        msg = f"the {name} spec is not valid: {exc}"
        raise CommandError("invalid_spec", msg, {"name": name}) from exc


def define_spec(root: Path, spec: BenchmarkSpec) -> None:
    """Freeze and lock a spec with ``bench define``.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.
    spec : perfproof.schema.BenchmarkSpec
        The spec; it is written under ``.perfproof/find/specs/`` and
        handed to the same code ``perfproof bench define`` runs.

    Raises
    ------
    CommandError
        Whatever ``bench define`` raises, for example ``lock_failed``
        when the harness file is missing.
    """
    directory = root / STATE_DIR / "find" / "specs"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{spec.name}.json"
        document = spec.model_dump(mode="json", exclude_computed_fields=True)
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    except OSError as exc:
        msg = f"cannot write the {spec.name} spec file: {exc}"
        raise CommandError("state_write_failed", msg) from exc
    parser = argparse.ArgumentParser()
    bench.add_arguments(parser)
    args = parser.parse_args(
        ["define", spec.name, "--spec", str(path), "--repo", str(root)]
    )
    out = bench.run(args)
    stored = out.result.get("spec_hash")
    if stored != spec.spec_hash:
        msg = f"bench define stored {stored}, not {spec.spec_hash}"
        raise CommandError("internal_fault", msg)


def user_specs(root: Path) -> Mapping[str, UserSpec]:
    """List every user spec, newest stored version of each name.

    Parameters
    ----------
    root : pathlib.Path
        Repository root.

    Returns
    -------
    Mapping of str to UserSpec
        Every stored spec whose name is not ``find-*``, by name. A
        record that does not load is skipped: it is not a workload.
    """
    base = root / STATE_DIR / "benchmarks"
    found: dict[str, UserSpec] = {}
    if not base.is_dir():
        return found
    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or is_find_name(directory.name):
            continue
        records = [p for p in directory.glob("*.json") if p.is_file()]
        if not records:
            continue
        newest = max(records, key=lambda p: (p.stat().st_mtime_ns, p.name))
        try:
            spec, _ = load_spec(root, directory.name, newest.stem)
        except (SpecRecordError, OSError):
            continue
        found[directory.name] = UserSpec(directory.name, spec)
    return found
