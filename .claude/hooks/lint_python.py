#!/usr/bin/env python3
"""Format and lint a Python file after Claude Code edits it.

Runs as a PostToolUse hook on Edit and Write. Reads the hook event from
stdin, formats the edited file with ruff, then lints it. Lint findings
that ruff cannot fix are written to stderr with exit code 2, which
Claude Code shows to the agent.

The project is located from the edited file rather than from
``CLAUDE_PROJECT_DIR``, because in worktree sessions that variable
points at the main checkout while the edit lands in the worktree.
"""

import json
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 50
BLOCK = 2
UV_RUN = ("uv", "run", "--quiet", "--frozen", "--directory")


def find_project(path: Path) -> Path | None:
    """Return the nearest ancestor of ``path`` holding a pyproject.toml.

    Parameters
    ----------
    path : Path
        Absolute path of the edited file.

    Returns
    -------
    Path or None
        Project root, or ``None`` when the file is outside any project.
    """
    for parent in path.parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return None


def ruff(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ruff from the project's locked environment.

    Parameters
    ----------
    project : Path
        Project root used as uv's working directory.
    *args : str
        Arguments passed to ruff.

    Returns
    -------
    subprocess.CompletedProcess of str
        Finished process with captured stdout and stderr.
    """
    return subprocess.run(
        [*UV_RUN, str(project), "ruff", *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
    )


def main() -> int:
    """Lint the file named in the hook event and report the outcome."""
    event = json.load(sys.stdin)
    file_path = event.get("tool_input", {}).get("file_path")
    if not file_path or not file_path.endswith(".py"):
        return 0
    path = Path(file_path).resolve()
    project = find_project(path)
    if project is None or not path.is_file():
        return 0

    try:
        ruff(project, "format", "--force-exclude", str(path))
        result = ruff(project, "check", "--fix", "--force-exclude", str(path))
    except FileNotFoundError:
        print("lint hook: uv is not on PATH", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired:
        print(f"lint hook: ruff exceeded {TIMEOUT_S}s", file=sys.stderr)
        return 1

    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        return BLOCK
    return 0


if __name__ == "__main__":
    sys.exit(main())
