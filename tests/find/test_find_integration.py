"""`find` against the real tools: pytest, the profilers, git and node.

Each test copies a small fixture project to a temporary git repository
and runs the real probes through `perfproof.cli.main`, so the profiles
are real. They skip, with the reason, where a tool is missing.
"""

import json
import shutil
import subprocess
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from find_helpers import copy_fixture, make_part

from perfproof.cli import main
from perfproof.find import engine, specs
from perfproof.find.engine import FindRequest, FindRun
from perfproof.profilers.run import default_profiler
from perfproof.schema import Part, ProjectModel

pytestmark = pytest.mark.integration

PYTEST = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")


def _need_profiler() -> None:
    if default_profiler() == "py-spy" and shutil.which("py-spy") is None:
        pytest.skip("py-spy is not installed")


@pytest.fixture(autouse=True)
def parts_double(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the CLI the part the test below defines for the fixture."""

    def detect(_root: Path, _model: ProjectModel) -> list[Part]:
        return [py_part()]

    monkeypatch.setattr(engine, "default_detect", lambda: detect)


def cli(repo: Path, *args: str) -> tuple[int, dict[str, Any]]:
    """Run ``perfproof`` in-process; return its exit and document."""
    import io
    from contextlib import redirect_stdout

    out = io.StringIO()
    with redirect_stdout(out):
        code = main([*args, "--repo", str(repo), "--json"])
    return code, json.loads(out.getvalue())


def run_find(
    repo: Path,
    parts: Sequence[Part] | None,
    depth: str = "medium",
    **over: Any,
) -> FindRun:
    args: dict[str, Any] = {
        "root": repo,
        "depth": depth,
        "budget_seconds": 600.0,
        "size_knobs": {},
        "no_tests": False,
        "test_timeout": 120.0,
        "confirm_tests": (),
        "waive": {},
    }
    args.update(over)
    if parts is None:
        return engine.run_find(FindRequest(**args))
    return engine.run_find(FindRequest(**args), detect=lambda _r, _m: parts)


def py_part(**over: Any) -> Part:
    args: dict[str, Any] = {
        "test": PYTEST,
        "entry_points": (
            {
                "kind": "console_script",
                "name": "main",
                "file": "app/cli.py",
                "line": 8,
            },
        ),
    }
    args.update(over)
    return make_part(**args)


def keys(run: FindRun) -> dict[str, str]:
    return {s.key: s.status for s in run.result.skipped}


def profile_dirs(repo: Path, name: str) -> list[Path]:
    base = repo / ".perfproof" / "profiles" / name
    return sorted(p for p in base.glob("*") if p.is_dir())


def test_python_project_probes_map_leads_and_resume(tmp_path: Path) -> None:
    _need_profiler()
    repo = copy_fixture("py_tests", tmp_path / "proj")
    part = py_part()
    first = run_find(repo, [part], no_tests=False)
    assert first.result.question is not None
    assert profile_dirs(repo, "find-tests-root") == []
    second = run_find(repo, [part], confirm_tests=("root",))
    assert second.result.question is None
    runs = profile_dirs(repo, "find-tests-root")
    assert len(runs) == 1
    assert (runs[0] / "result.json").is_file()
    leads = [lead for lead in second.result.leads if lead.kind == "function"]
    names = [lead.name for lead in leads]
    assert "checksum" in names
    top = next(lead for lead in leads if lead.name == "checksum")
    assert top.file == "app/work.py"
    assert top.part == "root"
    assert top.seen_in == ("find-tests-root",)
    assert top.via_test is not None
    assert "tests/test_work.py" in top.via_test
    assert second.result.map == ()
    assert "app/cli.py" in second.result.not_reached
    assert second.result.budget.spent_seconds > 0
    assert any(s.code == "define_workloads" for s in second.result.next_steps)
    third = run_find(repo, [part])
    assert profile_dirs(repo, "find-tests-root") == runs
    assert third.result.budget.spent_seconds < 5


def test_a_user_workload_makes_the_map_and_removes_the_lead(
    tmp_path: Path,
) -> None:
    _need_profiler()
    repo = copy_fixture("py_tests", tmp_path / "proj")
    specs.define_spec(
        repo,
        specs.build_spec(
            name="main-run",
            command=(sys.executable, "-m", "app.cli", "--n", "400000"),
            cwd=".",
            harness="pyproject.toml",
            target_class="python_cpu",
            timeout_seconds=120.0,
        ),
    )
    part = py_part()
    run = run_find(repo, [part], confirm_tests=("root",))
    mapped = [row.function for row in run.result.map]
    assert "checksum" in mapped
    row = next(r for r in run.result.map if r.function == "checksum")
    assert row.seen_in == ("main-run",)
    assert row.part == "root"
    assert row.layer == "python"
    assert all("find-" not in n for r in run.result.map for n in r.seen_in)
    assert "checksum" not in [
        lead.name for lead in run.result.leads if lead.kind == "function"
    ]
    assert "app/cli.py" not in run.result.not_reached
    assert run.result.parts[0].entry_points[0].name == "main"


def test_a_size_knob_runs_the_workload_at_three_sizes(tmp_path: Path) -> None:
    _need_profiler()
    repo = copy_fixture("py_tests", tmp_path / "proj")
    specs.define_spec(
        repo,
        specs.build_spec(
            name="main-run",
            command=(sys.executable, "-m", "app.cli"),
            cwd=".",
            harness="pyproject.toml",
            target_class="python_cpu",
            timeout_seconds=120.0,
        ),
    )
    code, document = cli(
        repo,
        "find",
        "--no-tests",
        "--size-arg",
        "main-run:--n=200000",
    )
    assert code == 0, document
    state = json.loads(
        (repo / ".perfproof" / "find" / "state.json").read_text()
    )
    probe = next(p for p in state["probes"] if p["key"] == "main-run@sizes")
    assert probe["status"] == "done"
    assert len(probe["run_ids"]) == 3


def test_a_tree_modifying_probe_is_caught_and_never_run_again(
    tmp_path: Path,
) -> None:
    _need_profiler()
    repo = copy_fixture("py_tests", tmp_path / "proj")
    (repo / "tests" / "test_dirty.py").write_text(
        "from pathlib import Path\n\n\n"
        "def test_writes() -> None:\n"
        "    Path('leftover.txt').write_text('x')\n"
    )
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, timeout=30
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "dirty"],
        check=True,
        timeout=30,
    )
    part = py_part()
    run = run_find(repo, [part], confirm_tests=("root",))
    (skipped,) = run.result.skipped
    assert skipped.status == "tree_modified"
    assert "leftover.txt" in (skipped.reason or "")
    assert profile_dirs(repo, "find-tests-root") == []
    assert not [lead for lead in run.result.leads if lead.kind == "function"]
    (repo / "leftover.txt").unlink()
    again = run_find(repo, [part])
    assert [s.status for s in again.result.skipped] == ["tree_modified"]
    assert profile_dirs(repo, "find-tests-root") == []


def test_a_failing_test_command_leaves_no_partial_profile(
    tmp_path: Path,
) -> None:
    _need_profiler()
    repo = copy_fixture("py_tests", tmp_path / "proj")
    (repo / "boom.py").write_text(
        "import sys\nprint('out-line')\n"
        "sys.stderr.write('err-line\\n')\nraise SystemExit(3)\n"
    )
    part = py_part(test=(sys.executable, "boom.py"))
    run = run_find(repo, [part], confirm_tests=("root",))
    (skipped,) = run.result.skipped
    assert skipped.status == "failed"
    assert skipped.log_tail is not None
    assert "out-line" in skipped.log_tail
    assert "err-line" in skipped.log_tail
    assert ".perfproof" not in (skipped.reason or "")
    assert ".perfproof" not in skipped.log_tail
    assert profile_dirs(repo, "find-tests-root") == []
    assert "continue_find" not in [s.code for s in run.result.next_steps]


def test_the_probe_timeout_is_recorded(tmp_path: Path) -> None:
    _need_profiler()
    repo = copy_fixture("py_tests", tmp_path / "proj")
    (repo / "sleeper.py").write_text("import time\ntime.sleep(30)\n")
    part = py_part(test=(sys.executable, "sleeper.py"))
    run = run_find(repo, [part], confirm_tests=("root",), test_timeout=2.0)
    (skipped,) = run.result.skipped
    assert skipped.status == "timeout"
    assert profile_dirs(repo, "find-tests-root") == []


def test_a_stale_probe_deletes_its_old_runs(tmp_path: Path) -> None:
    _need_profiler()
    repo = copy_fixture("py_tests", tmp_path / "proj")
    part = py_part()
    run_find(repo, [part], confirm_tests=("root",))
    old = profile_dirs(repo, "find-tests-root")
    assert len(old) == 1
    (repo / "app" / "work.py").write_text(
        (repo / "app" / "work.py").read_text() + "\nEXTRA = 1\n"
    )
    run_find(repo, [part])
    new = profile_dirs(repo, "find-tests-root")
    assert len(new) == 1
    assert new != old


def test_cli_find_json_and_exit_codes(tmp_path: Path) -> None:
    repo = copy_fixture("py_tests", tmp_path / "proj")
    code, document = cli(repo, "find", "--depth", "light")
    assert code == 0 or document["error"]["code"] == "internal_error"
    if code == 0:
        result = document["result"]
        assert result["depth"] == "light"
        assert result["budget"]["limit_seconds"] == 300
        assert "profilers" in result
    outside = tmp_path / "plain"
    outside.mkdir()
    code, document = cli(outside, "find")
    assert code == 1
    assert document["error"]["code"] == "not_a_repository"


def _compile_extension(root: Path) -> Path:
    cc = shutil.which("cc") or shutil.which("gcc")
    if cc is None:
        pytest.skip("no C compiler")
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    include = sysconfig.get_paths()["include"]
    if not (Path(include) / "Python.h").is_file():
        pytest.skip("no Python.h")
    target = root / f"fastsum{suffix}"
    flags = (
        ["-undefined", "dynamic_lookup"]
        if sys.platform == "darwin"
        else ["-fPIC"]
    )
    subprocess.run(
        [
            cc,
            "-g",
            "-O1",
            "-shared",
            *flags,
            f"-I{include}",
            "-o",
            str(target),
            str(root / "fastsum.c"),
        ],
        check=True,
        timeout=120,
    )
    return target


def test_a_c_extension_gets_the_native_recorder(tmp_path: Path) -> None:
    _need_profiler()
    repo = tmp_path / "proj"
    shutil.copytree(
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "find"
        / "py_native",
        repo,
    )
    built = _compile_extension(repo)
    from find_helpers import init_repo

    init_repo(repo)
    native = {
        "kind": "setuptools_ext",
        "name": "fastsum",
        "own_module": built.name,
        "sources": ["fastsum.c"],
    }
    part = py_part(native=(native,), entry_points=())
    run = run_find(repo, [part], confirm_tests=("root",))
    statuses = keys(run)
    assert "find-tests-root" not in statuses
    state = json.loads(
        (repo / ".perfproof" / "find" / "state.json").read_text()
    )
    by_key = {p["key"]: p for p in state["probes"]}
    assert by_key["find-tests-root"]["status"] == "done"
    assert "find-tests-root@native" in by_key
    native_probe = by_key["find-tests-root@native"]
    if native_probe["status"] != "done":
        pytest.skip(f"native recorder unavailable: {native_probe['reason']}")
    run_dir = (
        repo
        / ".perfproof"
        / "profiles"
        / "find-tests-root"
        / native_probe["run_ids"][-1]
    )
    native_result = json.loads((run_dir / "result.json").read_text())
    frames = [
        str(h.get("function", ""))
        for h in native_result.get("frames_by_total", [])
    ] + [str(h.get("function", "")) for h in native_result.get("hotspots", [])]
    assert any("spin" in f for f in frames), frames[:20]
    assert built.stat().st_size > 0


def test_a_node_project_is_profiled_with_the_node_profiler(
    tmp_path: Path,
) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    repo = copy_fixture("node_tests", tmp_path / "proj")
    part = make_part(
        language="javascript",
        target_class="node",
        test=("node", "--test"),
        manifests=("package.json",),
    )
    run = run_find(repo, [part], confirm_tests=("root",))
    state = json.loads(
        (repo / ".perfproof" / "find" / "state.json").read_text()
    )
    by_key = {p["key"]: p for p in state["probes"]}
    probe = by_key["find-tests-root"]
    assert probe["status"] == "done", probe
    names = [lead.name for lead in run.result.leads]
    assert "checksum" in names
    assert run.profilers["root"][2] == "perfproof.profilers.node"


def test_the_real_detect_parts_feeds_a_light_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from perfproof.project.parts import detect_parts

    monkeypatch.setattr(engine, "default_detect", lambda: detect_parts)
    repo = copy_fixture("py_tests", tmp_path / "proj")
    run = run_find(repo, None, "light", budget_seconds=None)
    assert [p.id for p in run.result.parts] == ["root"]
    assert run.result.parts[0].language == "python"
    assert run.result.budget.limit_seconds == 300
    assert any(s.code == "define_workloads" for s in run.result.next_steps)


def _venv_with_pytest(repo: Path) -> None:
    """Make ``repo/.venv`` a venv that sees the running pytest."""
    done = subprocess.run(
        [sys.executable, "-m", "venv", str(repo / ".venv")],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        pytest.skip("cannot create a venv")
    site = next((repo / ".venv" / "lib").glob("python*/site-packages"))
    outer = sysconfig.get_paths()["purelib"]
    (site / "outer.pth").write_text(outer + "\n")


def test_medium_end_to_end_with_the_real_detect_parts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _need_profiler()
    from perfproof.project.parts import detect_parts

    monkeypatch.setattr(engine, "default_detect", lambda: detect_parts)
    repo = tmp_path / "proj"
    shutil.copytree(
        Path(__file__).resolve().parents[1] / "fixtures" / "find" / "py_tests",
        repo,
    )
    _venv_with_pytest(repo)
    from find_helpers import init_repo

    init_repo(repo)
    code, first = cli(repo, "find")
    assert code == 0, first
    result = first["result"]
    assert result["question"] is not None
    assert "pytest" in result["question"]
    assert [p["id"] for p in result["parts"]] == ["root"]
    assert result["skipped"][0]["status"] == "skipped_unconfirmed"
    code, second = cli(repo, "find", "--confirm-tests", "root")
    assert code == 0, second
    result = second["result"]
    assert result["skipped"] == []
    runs = repo / ".perfproof" / "profiles" / "find-tests-root"
    assert len(list(runs.glob("*/result.json"))) == 1
    leads = {lead["name"]: lead for lead in result["leads"]}
    assert leads["checksum"]["file"] == "app/work.py"
    assert leads["checksum"]["via_test"] == "tests/test_work.py::test_checksum"
    assert result["map"] == []
    specs.define_spec(
        repo,
        specs.build_spec(
            name="main-run",
            command=(
                str(repo / ".venv" / "bin" / "python"),
                "-m",
                "app.cli",
                "--n",
                "300000",
            ),
            cwd=".",
            harness="pyproject.toml",
            target_class="python_cpu",
            timeout_seconds=120.0,
        ),
    )
    code, third = cli(repo, "find")
    assert code == 0, third
    result = third["result"]
    assert result["skipped"] == []
    assert "checksum" in [row["function"] for row in result["map"]]
    assert "checksum" not in [
        lead["name"] for lead in result["leads"] if lead["kind"] == "function"
    ]
    assert result["budget"]["exhausted"] is False
    assert "continue_find" not in [s["code"] for s in result["next_steps"]]
