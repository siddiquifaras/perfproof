"""Builders and a git repository for the find tests."""

import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from perfproof.schema import Part, ProbeRecord

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "find"
ZERO = "0" * 64


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return stdout."""
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return done.stdout


def init_repo(repo: Path) -> Path:
    """Make ``repo`` a git repository with everything committed."""
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "T")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def copy_fixture(name: str, dest: Path) -> Path:
    """Copy a fixture project to ``dest`` and commit it."""
    shutil.copytree(FIXTURES / name, dest)
    return init_repo(dest)


def tool(name: str, available: bool = True, reason: str | None = None) -> Any:
    """Return a ``ToolStatus`` value."""
    return {"name": name, "available": available, "reason": reason}


def make_part(
    part_id: str = "root",
    *,
    root: str = ".",
    language: str = "python",
    target_class: str | None = "python_cpu",
    target_id: str | None = None,
    test: tuple[str, ...] | None = (sys.executable, "-m", "pytest", "-q"),
    manifests: tuple[str, ...] = ("pyproject.toml",),
    native: tuple[Mapping[str, Any], ...] = (),
    benchmarks: tuple[Mapping[str, Any], ...] = (),
    entry_points: tuple[Mapping[str, Any], ...] = (),
    coverage: str = "not_needed",
    coverage_install: tuple[str, ...] | None = None,
    recorder: bool = True,
) -> Part:
    """Build a `Part` from plain values."""
    return Part.model_validate(
        {
            "id": part_id,
            "root": root,
            "manifests": list(manifests),
            "target_id": target_id,
            "language": language,
            "target_class": (
                {"value": target_class}
                if target_class
                else {"reason": "no class"}
            ),
            "test_command": (
                {"value": {"argv": list(test), "cwd": ".", "evidence": "t"}}
                if test is not None
                else {"reason": "no test command found"}
            ),
            "native": [dict(n) for n in native],
            "benchmarks": [dict(b) for b in benchmarks],
            "entry_points": [dict(e) for e in entry_points],
            "tooling": {
                "profiler": tool("profiler"),
                "native_recorder": tool(
                    "native",
                    recorder,
                    None if recorder else "perf_event_open refused",
                ),
                "coverage": coverage,
                "coverage_install": (
                    None
                    if coverage_install is None
                    else list(coverage_install)
                ),
            },
        }
    )


def record(
    key: str,
    status: str = "done",
    *,
    spec_hash: str = ZERO,
    tree: str = "t1",
    run_ids: tuple[str, ...] = (),
    reason: str | None = None,
) -> ProbeRecord:
    """Build a `ProbeRecord`."""
    return ProbeRecord.model_validate(
        {
            "key": key,
            "requirement": True,
            "spec_hash": spec_hash,
            "tree_id": tree,
            "status": status,
            "run_ids": list(run_ids),
            "reason": reason,
            "log_tail": None,
        }
    )
