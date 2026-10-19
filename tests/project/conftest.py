"""Fixtures shared by the project model tests."""

import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from project_helpers import EXAMPLES, MakeRepo, commit_all, git


@pytest.fixture(autouse=True)
def isolated_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "gitconfig"
    config.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n"
        "[commit]\n\tgpgsign = false\n[init]\n\tdefaultBranch = main\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


@pytest.fixture
def make_repo(tmp_path: Path) -> MakeRepo:
    counter = 0

    def make(files: Mapping[str, str]) -> Path:
        nonlocal counter
        counter += 1
        repo = tmp_path / f"repo{counter}"
        repo.mkdir()
        git(repo, "init", "-q")
        for name, text in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        commit_all(repo)
        return repo

    return make


@pytest.fixture
def example_repo(tmp_path: Path) -> Callable[[str], Path]:
    """Copy one of ``examples/`` into its own git repository."""

    def make(name: str) -> Path:
        repo = tmp_path / f"example-{name}"
        shutil.copytree(EXAMPLES / name, repo)
        shutil.rmtree(repo / "candidates", ignore_errors=True)
        git(repo, "init", "-q")
        commit_all(repo)
        return repo

    return make
