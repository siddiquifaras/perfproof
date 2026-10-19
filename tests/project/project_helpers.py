"""Constants and helpers shared by the project model tests."""

import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from perfproof.project.detect import detect
from perfproof.project.lifecycle import GoalUpdate, confirm, propose, set_goal
from perfproof.project.store import mutate
from perfproof.schema import ProjectHistoryRecord, ProjectModel

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
VERSION = "0.1.0"

type MakeRepo = Callable[[Mapping[str, str]], Path]


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return done.stdout.strip()


def commit_all(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "snapshot")


BENCH = (
    "import json, sys\n"
    "if __name__ == '__main__':\n"
    "    print('PERFPROOF ' + json.dumps({'value': 0.5}))\n"
)

PYTHON_FILES: Mapping[str, str] = {
    "pyproject.toml": (
        '[project]\nname = "demo"\nversion = "1"\ndependencies = ["numpy"]\n'
    ),
    "bench.py": BENCH,
    "tests/test_demo.py": "def test_demo():\n    assert True\n",
}


type Records = list[ProjectHistoryRecord]


def proposed(repo: Path, **kwargs: Any) -> ProjectModel:
    """Run ``init`` on ``repo`` and return the model on disk."""
    proposal = detect(repo, now=NOW, version=VERSION, **kwargs)

    def change(model: ProjectModel) -> tuple[ProjectModel, Records]:
        merged = propose(model, proposal, NOW)
        return merged.model, merged.records

    return mutate(repo, change, now=lambda: NOW)


def confirmed(repo: Path, workload: str = "bench") -> ProjectModel:
    """Propose, give the workload a wall-time goal, confirm both."""

    def change(model: ProjectModel) -> tuple[ProjectModel, Records]:
        model, a = set_goal(
            model, workload, GoalUpdate(metric="wall_time"), NOW, VERSION
        )
        model, b = confirm(
            model,
            ["target-python_cpu", workload, f"goal-{workload}"],
            "test-session",
            NOW,
        )
        return model, [*a, *b]

    proposed(repo)
    return mutate(repo, change, now=lambda: NOW)
