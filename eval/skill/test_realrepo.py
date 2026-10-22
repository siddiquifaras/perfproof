"""Real-repository seeding, against a real local git "remote"."""

import json
import os
import subprocess
from pathlib import Path

import pytest

import realrepo
import runner
import sandbox
import taskset

REAL = taskset.load(runner.REPO_ROOT / "eval" / "skill" / "tasks-real.json")
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> tuple[str, str, str]:
    """A repo of three commits served from ``<host>/o/r.git``.

    Returns the host URL and the shas of the second and third commit.
    """
    host = tmp_path / "host"
    src = host / "o" / "r.git"
    src.mkdir(parents=True)
    _git(src, "init", "-q", "-b", "main")
    shas: list[str] = []
    for n in range(3):
        (src / "f.txt").write_text(f"v{n}\n")
        _git(src, "add", "-A")
        _git(src, "commit", "-q", "-m", f"c{n}")
        shas.append(_git(src, "rev-parse", "HEAD"))
    return f"file://{host}", shas[1], shas[2]


def _spec(task: str, reference: str) -> realrepo.RepoSpec:
    return realrepo.RepoSpec(
        repository="o/r",
        task_commit=task,
        reference_commit=reference,
        reference_number="7",
        reference_title="t",
        setup=[],
        env={},
        oracle=None,
    )


def test_find_layout_has_one_commit_and_no_remote(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third)
    dest = tmp_path / "run"
    tips = realrepo.seed_repo(
        spec,
        taskset.RealRepo(manifest="x"),
        dest,
        tmp_path / "cache",
        host,
    )
    assert tips == {"main": second}
    assert _git(dest, "rev-list", "--count", "HEAD") == "1"
    assert (dest / "f.txt").read_text() == "v1\n"
    assert _git(dest, "remote") == ""
    assert _git(dest, "status", "--porcelain") == ""
    # The change after the task commit is not in the clone.
    probe = subprocess.run(
        ["git", "cat-file", "-e", third],
        cwd=dest,
        capture_output=True,
        check=False,
    )
    assert probe.returncode != 0


def test_prove_layout_cuts_the_branch_at_the_reference_change(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third)
    dest = tmp_path / "run"
    tips = realrepo.seed_repo(
        spec,
        taskset.RealRepo(manifest="x", branch="pr-7"),
        dest,
        tmp_path / "cache",
        host,
    )
    assert tips == {"main": second, "pr-7": third}
    assert _git(dest, "rev-parse", "pr-7^") == second
    assert _git(dest, "merge-base", "main", "pr-7") == second
    assert _git(dest, "rev-parse", "--abbrev-ref", "HEAD") == "main"


def test_a_cache_hit_needs_no_remote(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third)
    real = taskset.RealRepo(manifest="x")
    cache = tmp_path / "cache"
    realrepo.seed_repo(spec, real, tmp_path / "a", cache, host)
    tips = realrepo.seed_repo(
        spec, real, tmp_path / "b", cache, "file:///does/not/exist"
    )
    assert tips == {"main": second}


def test_a_parent_that_is_not_the_task_commit_is_refused(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(third, third)
    with pytest.raises(realrepo.SetupError, match="not the task commit"):
        realrepo.seed_repo(
            spec,
            taskset.RealRepo(manifest="x", branch="pr-7"),
            tmp_path / "run",
            tmp_path / "cache",
            host,
        )
    assert second


def test_setup_hides_what_the_build_made_from_the_status(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third).model_copy(
        update={"setup": ["python3 -c \"open('built.bin','w').write('x')\""]}
    )
    dest = tmp_path / "run"
    realrepo.seed_repo(
        spec, taskset.RealRepo(manifest="x"), dest, tmp_path / "c", host
    )
    realrepo.run_setup(spec, dest, [])
    assert (dest / "built.bin").exists()
    assert _git(dest, "status", "--porcelain") == ""


def test_a_failing_setup_step_names_itself(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    spec = _spec(second, third).model_copy(
        update={"setup": ["python3 -c 'raise SystemExit(3)'"]}
    )
    dest = tmp_path / "run"
    realrepo.seed_repo(
        spec, taskset.RealRepo(manifest="x"), dest, tmp_path / "c", host
    )
    with pytest.raises(realrepo.SetupError, match="exited 3"):
        realrepo.run_setup(spec, dest, [])


@pytest.mark.skipif(
    not sandbox.confinement_available(), reason="needs macOS sandbox-exec"
)
def test_the_oracle_reports_its_exit_status(
    tmp_path: Path, remote: tuple[str, str, str]
) -> None:
    host, second, third = remote
    base = _spec(second, third)
    dest = tmp_path / "run"
    realrepo.seed_repo(
        base, taskset.RealRepo(manifest="x"), dest, tmp_path / "c", host
    )
    ok = base.model_copy(update={"oracle": "python3 -c 'print(1)'"})
    bad = base.model_copy(
        update={"oracle": "python3 -c 'raise SystemExit(1)'"}
    )
    assert realrepo.run_oracle(ok, dest, [])[0] is True
    assert realrepo.run_oracle(bad, dest, [])[0] is False
    assert realrepo.run_oracle(base, dest, []) == (
        False,
        "the manifest has no oracle command",
    )


def test_a_missing_toolchain_is_named() -> None:
    real = taskset.RealRepo(manifest="x", requires=["no-such-tool-xyz"])
    assert realrepo.missing_requirements(real) == ["no-such-tool-xyz"]


def test_shims_mount_the_enclosing_work_tree(tmp_path: Path) -> None:
    realrepo.write_shims(tmp_path, "go")
    body = (tmp_path / "go").read_text()
    assert os.access(tmp_path / "go", os.X_OK)
    assert "rev-parse --show-toplevel" in body
    assert "golang:1.25" in body
    realrepo.write_shims(tmp_path, "ruby")
    assert {p.name for p in tmp_path.iterdir()} == {
        "go",
        "ruby",
        "bundle",
        "gem",
        "rake",
    }


@pytest.mark.parametrize("task", REAL.tasks, ids=lambda t: t.id)
def test_every_real_task_has_a_development_manifest(
    task: taskset.Task,
) -> None:
    assert task.repo is not None
    found = sorted(
        (runner.REPO_ROOT / "eval" / "tasks").glob(
            f"*/{task.repo.manifest}.json"
        )
    )
    assert len(found) == 1
    manifest = json.loads(found[0].read_text())
    assert manifest["split"] == "development", "held-out stays unseen"
    assert manifest["status"] == "runs"
    family = {"prove": "known-change", "find": None}[task.kind]
    if family is not None:
        assert manifest["family"] == family
        assert task.repo.branch is not None
    else:
        assert manifest["family"] in {"hidden-fix", "nothing-to-keep"}
        assert task.repo.branch is None
    spec = realrepo.load_spec(task.repo.manifest)
    assert spec.setup
    assert spec.oracle


def test_batch_one_covers_the_languages_and_families() -> None:
    by_family: dict[str, int] = {}
    for task in REAL.tasks:
        assert task.repo is not None
        manifest = realrepo.load_spec(task.repo.manifest)
        assert manifest.repository
        found = next(
            (runner.REPO_ROOT / "eval" / "tasks").glob(
                f"*/{task.repo.manifest}.json"
            )
        )
        family = json.loads(found.read_text())["family"]
        by_family[family] = by_family.get(family, 0) + 1
    assert by_family == {
        "hidden-fix": 3,
        "known-change": 3,
        "nothing-to-keep": 2,
    }
    assert len(REAL.tasks) == 8


def test_a_held_out_manifest_cannot_be_loaded() -> None:
    with pytest.raises(ValueError, match="development"):
        realrepo.load_spec("psf-black-pr3467")
    assert realrepo.load_spec("sharkdp-fd-pr853").setup
