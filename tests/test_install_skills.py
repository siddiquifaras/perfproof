import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.cli import main
from perfproof.commands import _skills as skills
from perfproof.commands import doctor

type Run = Callable[..., tuple[int, dict[str, Any]]]
ROOT = Path(__file__).resolve().parents[1]


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


def outcomes(doc: dict[str, Any]) -> dict[str, str]:
    return {c["client"]: c["outcome"] for c in doc["result"]["clients"]}


def test_install_writes_both_clients_and_nothing_else(
    run: Run, home: Path
) -> None:
    status, doc = run("install-skills")
    assert status == 0
    assert outcomes(doc) == {"claude": "installed", "codex": "installed"}
    claude = home / ".claude" / "skills" / "perfproof"
    codex = home / ".agents" / "skills" / "perfproof"
    for target in (claude, codex):
        assert (target / "SKILL.md").read_text() == (
            ROOT / "skills" / "perfproof" / "SKILL.md"
        ).read_text()
        assert (target / "java.md").is_file()
        assert (target / skills.MARKER).is_file()
    assert {p.name for p in home.iterdir()} == {".claude", ".agents"}


def test_install_twice_is_unchanged_and_edit_is_updated(
    run: Run, home: Path
) -> None:
    run("install-skills")
    assert outcomes(run("install-skills")[1]) == {
        "claude": "unchanged",
        "codex": "unchanged",
    }
    skill = home / ".claude" / "skills" / "perfproof" / "SKILL.md"
    skill.write_text("old copy")
    assert skills.check("claude", skills.skill_source()).state == "stale"
    assert outcomes(run("install-skills")[1])["claude"] == "updated"
    assert skill.read_text() != "old copy"


def test_one_client_only(run: Run, home: Path) -> None:
    status, doc = run("install-skills", "--client", "codex")
    assert status == 0
    assert outcomes(doc) == {"codex": "installed"}
    assert not (home / ".claude").exists()


def test_foreign_directory_is_refused_then_forced(
    run: Run, home: Path
) -> None:
    mine = home / ".claude" / "skills" / "perfproof"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("someone else's")
    status, doc = run("install-skills", "--client", "claude")
    assert status == 1
    assert doc["error"]["code"] == "skill_dir_not_ours"
    assert (mine / "SKILL.md").read_text() == "someone else's"
    status, doc = run("install-skills", "--client", "claude", "--uninstall")
    assert status == 1
    assert (mine / "SKILL.md").read_text() == "someone else's"
    status, doc = run("install-skills", "--client", "claude", "--force")
    assert status == 0
    assert outcomes(doc) == {"claude": "updated"}


def test_uninstall_removes_only_our_copies(run: Run, home: Path) -> None:
    run("install-skills")
    other = home / ".claude" / "skills" / "other"
    other.mkdir()
    status, doc = run("install-skills", "--uninstall")
    assert status == 0
    assert doc["result"]["action"] == "uninstall"
    assert outcomes(doc) == {"claude": "removed", "codex": "removed"}
    assert other.is_dir()
    assert outcomes(run("install-skills", "--uninstall")[1]) == {
        "claude": "absent",
        "codex": "absent",
    }


def test_missing_source_is_an_error(
    run: Run, monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    monkeypatch.setattr(skills, "skill_source", lambda: None)
    status, doc = run("install-skills")
    assert status == 1
    assert doc["error"]["code"] == "skill_not_found"
    assert not (home / ".claude").exists()


def test_doctor_names_what_is_missing_and_the_fix(run: Run) -> None:
    status, doc = run("doctor")
    assert status == 0
    install = doc["result"]["install"]
    assert install["clients"]["claude"]["skill"] == "missing"
    fixes = [p["fix"] for p in install["problems"]]
    assert "perfproof install-skills --client claude" in fixes
    assert "perfproof install-skills --client codex" in fixes


def test_doctor_has_no_skill_problem_after_install(run: Run) -> None:
    run("install-skills")
    _, doc = run("doctor")
    install = doc["result"]["install"]
    assert install["clients"]["codex"]["skill"] == "current"
    assert not [p for p in install["problems"] if "skill" in p["what"]]


def _tool_prefix_with_perfproof(root: Path) -> Path:
    exe = root / "tool" / "bin" / "perfproof"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return root / "tool"


def _not_on_path(doc: dict[str, Any]) -> bool:
    whats = [p["what"] for p in doc["result"]["install"]["problems"]]
    return any("not on PATH" in w for w in whats)


def test_doctor_does_not_count_perfproofs_own_tool_environment(
    run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _tool_prefix_with_perfproof(tmp_path)
    monkeypatch.setattr(sys, "prefix", str(tool))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(tool / "bin"), "/bin"]))
    _, doc = run("doctor")
    assert _not_on_path(doc)


def test_doctor_finds_perfproof_a_users_shell_would(
    run: Run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = tmp_path / "tool"
    (tool / "bin").mkdir(parents=True)
    users = _tool_prefix_with_perfproof(tmp_path / "users")
    monkeypatch.setattr(sys, "prefix", str(tool))
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(tool / "bin"), str(users / "bin"), "/bin"]),
    )
    _, doc = run("doctor")
    assert not _not_on_path(doc)


def test_doctor_warns_on_an_untested_python(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, doc = run("doctor")
    whats = [p["what"] for p in doc["result"]["install"]["problems"]]
    assert not [w for w in whats if "Python" in w]
    monkeypatch.setattr(doctor, "TESTED_PYTHON", ("2.7",))
    _, doc = run("doctor")
    whats = [p["what"] for p in doc["result"]["install"]["problems"]]
    assert [w for w in whats if "not tested" in w]


def test_failed_copy_keeps_the_old_install(
    run: Run, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("install-skills", "--client", "claude")
    skill = home / ".claude" / "skills" / "perfproof" / "SKILL.md"
    skill.write_text("old copy")

    def broken(*_: object, **__: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(skills.shutil, "copytree", broken)
    status, doc = run("install-skills", "--client", "claude")
    assert (status, doc["error"]["code"]) == (1, "install_failed")
    assert skill.read_text() == "old copy"
    assert skills.check("claude", skills.skill_source()).state == "stale"
    assert not (skill.parent.parent / ".perfproof.installing").exists()


@pytest.mark.integration
def test_wheel_ships_the_skill(tmp_path: Path) -> None:
    done = subprocess.run(
        ["uv", "build", "--wheel", "-o", str(tmp_path), str(ROOT)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    wheel = next(tmp_path.glob("perfproof-*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = {
            n.split(".data/data/", 1)[1]
            for n in zf.namelist()
            if ".data/data/" in n
        }
    shipped = {
        f"perfproof/{p.name}"
        for p in (ROOT / "skills" / "perfproof").iterdir()
        if p.is_file()
    }
    assert shipped <= names
    assert not any(n.endswith(("AGENTS.md", "CLAUDE.md")) for n in names)
