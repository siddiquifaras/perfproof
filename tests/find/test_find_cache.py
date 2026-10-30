"""Tool caches are not a tree change; real edits still are."""

import shutil
import subprocess
from pathlib import Path

import pytest
from find_helpers import FIXTURES, copy_fixture, init_repo, make_part

from perfproof.find import engine, gittree
from perfproof.find.engine import FindRequest, FindRun
from perfproof.find.probes import Outcome, PlannedProbe
from perfproof.profilers.run import default_profiler
from perfproof.schema import Part, ProbeRecord

CACHES = (
    "pkg/__pycache__/m.cpython-312.pyc",
    "x.pyc",
    ".pytest_cache/v/cache",
    ".mypy_cache/a",
    ".ruff_cache/a",
    ".coverage",
    ".coverage.host.123",
    "htmlcov/index.html",
    "web/node_modules/.cache/babel/a",
    "target/debug/app",
    ".gradle/x",
)


@pytest.fixture
def plain(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("x = 1\n")
    return init_repo(tmp_path)


ROOT = gittree.Exemptions(
    coverage=frozenset({""}), cargo=frozenset({""}), gradle=frozenset({""})
)


def test_new_untracked_tool_caches_are_not_a_probes_change(
    plain: Path,
) -> None:
    before = gittree.status_entries(plain, ROOT)
    for rel in CACHES:
        path = plain / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    after = gittree.status_entries(plain, ROOT)
    assert gittree.added_entries(before, after) == ()
    (plain / "notes.txt").write_text("x")
    after = gittree.status_entries(plain, ROOT)
    assert gittree.added_entries(before, after) == ("notes.txt",)


@pytest.mark.parametrize(
    "rel",
    [
        "src/target/x.rs",
        "target/x.rs",
        "src/.gradle/x",
        ".gradle/x",
        "src/htmlcov/index.html",
        "src/.coverage",
        "src/.coverage.1",
        "build/output.txt",
    ],
)
def test_root_only_exemptions_do_not_hold_elsewhere(
    plain: Path, rel: str
) -> None:
    before = gittree.status_entries(plain)
    path = plain / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    for exempt in (gittree.NO_EXEMPTIONS, ROOT):
        after = gittree.status_entries(plain, exempt)
        found = gittree.added_entries(before, after)
        if exempt is ROOT and rel in ("target/x.rs", ".gradle/x"):
            assert found == ()
        else:
            assert found == (rel,)


def test_a_part_root_below_the_top_exempts_its_own_target_only(
    plain: Path,
) -> None:
    exempt = gittree.Exemptions(cargo=frozenset({"crates/a"}))
    before = gittree.status_entries(plain, exempt)
    for rel in ("crates/a/target/x", "crates/b/target/x"):
        path = plain / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    after = gittree.status_entries(plain, exempt)
    assert gittree.added_entries(before, after) == ("crates/b/target/x",)


def test_part_roots_come_from_the_parts() -> None:
    parts = [
        make_part("root", manifests=("pyproject.toml",)),
        make_part("api", root="crates/api", manifests=("Cargo.toml",)),
        make_part("app", root="app", manifests=("app/build.gradle.kts",)),
    ]
    exempt = engine._exemptions(parts)  # pyright: ignore[reportPrivateUsage]
    assert exempt.coverage == {"", "crates/api", "app"}
    assert exempt.cargo == {"crates/api"}
    assert exempt.gradle == {"app"}


def test_a_tracked_file_in_a_cache_directory_still_counts(
    tmp_path: Path,
) -> None:
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "keep.txt").write_text("1")
    init_repo(tmp_path)
    before = gittree.status_entries(tmp_path)
    (tmp_path / "target" / "keep.txt").write_text("2")
    changed = gittree.status_entries(tmp_path)
    assert gittree.added_entries(before, changed) == ("target/keep.txt",)
    (tmp_path / "target" / "keep.txt").unlink()
    deleted = gittree.status_entries(tmp_path)
    assert gittree.added_entries(before, deleted) == ("target/keep.txt",)


class Recorder:
    def __init__(self) -> None:
        self.calls: list[PlannedProbe] = []

    def __call__(
        self, root: Path, planned: PlannedProbe, **kw: object
    ) -> Outcome:
        del root
        self.calls.append(planned)
        rec = ProbeRecord.model_validate(
            {
                "key": planned.key,
                "requirement": True,
                "spec_hash": planned.spec.spec_hash,
                "tree_id": kw["tree"],
                "status": "done",
                "run_ids": [],
                "reason": None,
                "log_tail": None,
            }
        )
        return Outcome(rec, 0.0)


def run(repo: Path, parts: list[Part], **over: object) -> FindRun:
    args: dict[str, object] = {
        "root": repo,
        "depth": "medium",
        "budget_seconds": 600.0,
        "size_knobs": {},
        "no_tests": False,
        "test_timeout": 120.0,
        "confirm_tests": ("root",),
        "waive": {},
    }
    args.update(over)
    return engine.run_find(
        FindRequest(**args),  # type: ignore[arg-type]
        detect=lambda _r, _m: parts,
    )


def test_python_probes_declare_no_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(engine, "run_probe", recorder)
    repo = copy_fixture("py_tests", tmp_path / "proj")
    bench = {"kind": "script", "name": "b", "argv": ["python"], "cwd": "."}
    run(repo, [make_part(benchmarks=(bench,))])
    assert [c.key for c in recorder.calls] == [
        "find-tests-root",
        "find-bench-root-b",
    ]
    assert all(
        c.spec.env == {"PYTHONDONTWRITEBYTECODE": "1"} for c in recorder.calls
    )


def test_other_languages_declare_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(engine, "run_probe", recorder)
    repo = copy_fixture("py_tests", tmp_path / "proj")
    run(repo, [make_part(language="javascript", test=("npm", "test"))])
    assert [dict(c.spec.env) for c in recorder.calls] == [{}]


def _need_profiler() -> None:
    if default_profiler() == "py-spy" and shutil.which("py-spy") is None:
        pytest.skip("py-spy is not installed")


@pytest.mark.integration
def test_bytecode_in_an_unignored_tree_is_not_a_modification(
    tmp_path: Path,
) -> None:
    _need_profiler()
    repo = tmp_path / "proj"
    shutil.copytree(FIXTURES / "py_tests", repo)
    (repo / ".gitignore").write_text(".venv/\n")
    init_repo(repo)
    import sys

    part = make_part(test=(sys.executable, "-m", "pytest", "-q"))
    result = run(repo, [part]).result
    assert result.skipped == ()
    runs = repo / ".perfproof" / "profiles" / "find-tests-root"
    assert len(list(runs.glob("*/result.json"))) == 1
    assert not list(repo.rglob("*.pyc"))


@pytest.mark.integration
def test_a_test_that_edits_a_tracked_file_is_still_caught(
    tmp_path: Path,
) -> None:
    _need_profiler()
    repo = tmp_path / "proj"
    shutil.copytree(FIXTURES / "py_tests", repo)
    (repo / "tests" / "test_edit.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_edit() -> None:\n"
        "    Path('app/work.py').write_text('X = 1\\n')\n"
    )
    init_repo(repo)
    import sys

    part = make_part(test=(sys.executable, "-m", "pytest", "-q"))
    (skipped,) = run(repo, [part]).result.skipped
    assert skipped.status == "tree_modified"
    assert "app/work.py" in (skipped.reason or "")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "--", "app/work.py"],
        check=True,
        timeout=30,
    )
