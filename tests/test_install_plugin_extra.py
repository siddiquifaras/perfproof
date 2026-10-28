import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.cli import main

type Run = Callable[..., tuple[int, dict[str, Any]]]
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "claude-plugin-home"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def run(capsys: pytest.CaptureFixture[str]) -> Run:
    def go(*argv: str) -> tuple[int, dict[str, Any]]:
        status = main(["--json", *argv])
        return status, cast(
            "dict[str, Any]", json.loads(capsys.readouterr().out)
        )

    return go


def _foreign_with_plugin(home: Path, *, disabled: bool) -> None:
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(
        (FIXTURE / "installed_plugins.json").read_text()
    )
    settings = (FIXTURE / "settings.json").read_text()
    if disabled:
        settings = settings.replace(": true", ": false")
    (home / ".claude" / "settings.json").write_text(settings)
    foreign = home / ".claude" / "skills" / "perfproof"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("someone else's")


@pytest.mark.parametrize("disabled", [False, True])
def test_foreign_directory_with_plugin_never_suggests_a_copy(
    run: Run, home: Path, disabled: bool
) -> None:
    _foreign_with_plugin(home, disabled=disabled)
    _, doc = run("doctor")
    problems = doc["result"]["install"]["problems"]
    fixes = [p["fix"] for p in problems]
    assert not [f for f in fixes if "install-skills --client claude" in f]
    foreign = [p for p in problems if "did not create" in p["what"]]
    assert len(foreign) == 1
    assert "shadow the plugin" in foreign[0]["what"]
    assert foreign[0]["fix"] == "move it away"


def test_wrapper_without_uv_says_so_and_exits_1(tmp_path: Path) -> None:
    done = subprocess.run(
        ["/bin/sh", str(ROOT / "bin" / "perfproof"), "doctor"],
        env={"PATH": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.returncode == 1
    assert "perfproof needs uv" in done.stderr
    assert "docs.astral.sh/uv" in done.stderr
