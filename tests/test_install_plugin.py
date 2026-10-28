import json
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
        out = capsys.readouterr().out
        return status, cast("dict[str, Any]", json.loads(out))

    return go


def _plugin_home(home: Path, *, disabled: bool = False) -> None:
    """Lay out ~/.claude as Claude Code left it after the install."""
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(
        (FIXTURE / "installed_plugins.json").read_text()
    )
    settings = (FIXTURE / "settings.json").read_text()
    if disabled:
        settings = settings.replace(": true", ": false")
    (home / ".claude" / "settings.json").write_text(settings)


def test_doctor_recognises_the_plugin_install(run: Run, home: Path) -> None:
    _plugin_home(home)
    _, doc = run("doctor")
    install = doc["result"]["install"]
    claude = install["clients"]["claude"]
    assert claude["skill"] == "plugin"
    assert claude["plugin"] == {
        "id": "perfproof@perfproof",
        "version": "0.1.0",
        "enabled": True,
    }
    whats = [p["what"] for p in install["problems"]]
    assert not [w for w in whats if "claude" in w or "PATH" in w]
    fixes = [p["fix"] for p in install["problems"]]
    assert "perfproof install-skills --client claude" not in fixes


def test_doctor_says_when_plugin_and_copy_both_exist(
    run: Run, home: Path
) -> None:
    _plugin_home(home)
    run("install-skills", "--client", "claude")
    _, doc = run("doctor")
    problems = doc["result"]["install"]["problems"]
    both = [p for p in problems if "installed twice" in p["what"]]
    assert len(both) == 1
    assert "not determined" in both[0]["what"]
    assert "--uninstall" in both[0]["fix"]


def test_doctor_reports_a_disabled_plugin(run: Run, home: Path) -> None:
    _plugin_home(home, disabled=True)
    _, doc = run("doctor")
    fixes = [p["fix"] for p in doc["result"]["install"]["problems"]]
    assert "claude plugin enable perfproof@perfproof" in fixes
    assert "perfproof install-skills --client claude" not in fixes
