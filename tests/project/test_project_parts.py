"""`detect_parts`: parts, test commands, native code, tooling probes."""

import json
import shutil
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from perfproof.project import parts as parts_mod
from perfproof.project.detect import detect
from perfproof.project.parts import detect_parts, part_id
from perfproof.schema import Part, ProjectModel

FIXTURES = Path(__file__).parents[1] / "fixtures" / "parts"
NOW = datetime(2026, 10, 30, tzinfo=UTC)


def materialise(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def run(
    root: Path,
    *,
    with_targets: bool = False,
    **kwargs: object,
) -> dict[str, Part]:
    targets = (
        detect(root, now=NOW, version="t").targets if with_targets else ()
    )
    model = ProjectModel(updated_at=NOW, targets=targets)
    kwargs.setdefault("path", "")
    kwargs.setdefault("platform", "linux")
    kwargs.setdefault("environ", {})
    found = detect_parts(root, model, **kwargs)  # type: ignore[arg-type]
    return {p.id: p for p in found}


def fixture_parts(tmp_path: Path, name: str) -> dict[str, Part]:
    root = tmp_path / name
    shutil.copytree(FIXTURES / name, root)
    for path in list(root.rglob("*.fixture")):
        path.rename(path.with_suffix(""))
    return run(root, with_targets=True)


def venv(root: Path, name: str = ".venv") -> str:
    """Make an interpreter that can import pytest: this test run's."""
    python = root / name / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    return str(python)


def stub(bindir: Path, name: str, code: int = 0, out: str = "") -> None:
    bindir.mkdir(exist_ok=True)
    path = bindir / name
    path.write_text(f"#!/bin/sh\necho '{out}' >&2\nexit {code}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


# ------------------------------------------------------------ fixtures


def test_pybind_setuptools_extension(tmp_path: Path) -> None:
    root = fixture_parts(tmp_path, "python_example")["root"]
    assert root.language == "python"
    assert root.manifests == ("pyproject.toml", "setup.py")
    (ext,) = root.native
    assert (ext.kind, ext.name) == ("setuptools_ext", "python_example")
    assert ext.sources == ("src/main.cpp",)
    assert ext.own_module.startswith("python_example.")
    assert ext.own_module.endswith(".so")
    assert root.target_id == "target-python_cpu"
    # tests/test.py is not a name pytest discovers.
    assert root.test_command.value is None
    assert root.test_command.reason


def test_node_monorepo_workspaces(tmp_path: Path) -> None:
    found = fixture_parts(tmp_path, "jest")
    assert "packages.jest-cli" in found
    assert len(found) > 50
    cli = found["packages.jest-cli"]
    assert cli.language == "typescript"
    assert [(e.kind, e.name) for e in cli.entry_points] == [("bin", "jest")]
    assert cli.entry_points[0].file == "packages/jest-cli/bin/jest.js"
    assert cli.target_class.value == "node"
    root = found["root"]
    assert root.test_command.value is None
    assert "shell command line" in (root.test_command.reason or "")
    assert root.tooling.coverage == "missing"
    assert root.tooling.coverage_install == (
        "npm",
        "install",
        "--no-save",
        "--ignore-scripts",
        "c8",
    )


def test_rust_workspace(tmp_path: Path) -> None:
    found = fixture_parts(tmp_path, "ripgrep")
    assert {"root", "crates.globset", "crates.ignore", "fuzz"} <= set(found)
    root = found["root"]
    assert root.test_command.value is not None
    assert root.test_command.value.argv == ("cargo", "test")
    assert [(e.kind, e.name, e.line) for e in root.entry_points] == [
        ("bin", "rg", 43)
    ]
    assert [b.argv for b in found["crates.globset"].benchmarks] == [
        ("cargo", "bench", "--bench", "bench")
    ]
    assert found["crates.ignore"].benchmarks[0].name == "workloads"
    assert found["crates.ignore"].test_command.value is not None
    assert found["crates.ignore"].test_command.value.cwd == "crates/ignore"


def test_go_module_with_cgo(tmp_path: Path) -> None:
    root = fixture_parts(tmp_path, "go-sqlite3")["root"]
    assert root.language == "go"
    (cgo,) = root.native
    assert (cgo.kind, cgo.name, cgo.own_module) == (
        "cgo",
        "go-sqlite3",
        "go-sqlite3",
    )
    assert "sqlite3.go" in cgo.sources
    assert "sqlite3_opt_unlock_notify.c" in cgo.sources
    assert root.test_command.value is not None
    assert root.test_command.value.argv == ("go", "test", "./...")
    (bench,) = root.benchmarks
    assert bench.kind == "go_bench"
    assert bench.argv[:3] == ("go", "test", "-run=^$")
    assert [(e.kind, e.name, e.line) for e in root.entry_points] == [
        ("main_package", "simple", 11)
    ]
    assert root.target_id is None
    assert "no project-model target" in (root.target_class.reason or "")


def test_maven_project(tmp_path: Path) -> None:
    found = fixture_parts(tmp_path, "gson")
    gson = found["gson"]
    assert gson.language == "java"
    assert gson.test_command.value is not None
    assert gson.test_command.value.argv == ("mvn", "-q", "test")
    assert gson.test_command.value.cwd == "gson"
    assert found["root"].language == "other"
    assert "no src/test" in (found["metrics"].test_command.reason or "")
    assert gson.tooling.coverage == "missing"
    assert gson.tooling.coverage_install is not None
    assert gson.tooling.coverage_install[0] == "curl"


def test_mixed_python_backend_ts_frontend(tmp_path: Path) -> None:
    found = fixture_parts(tmp_path, "full-stack-fastapi-template")
    backend, frontend = found["backend"], found["frontend"]
    assert (backend.language, frontend.language) == ("python", "typescript")
    assert backend.target_class.value == "web_service"
    assert frontend.target_id is None
    assert backend.tooling.coverage == "not_needed"
    kinds = {e.kind for e in backend.entry_points}
    assert kinds == {"route", "public_module"}
    assert any(
        e.file == "backend/app/api/routes/items.py" and e.line is not None
        for e in backend.entry_points
        if e.kind == "route"
    )
    assert "app.main" in {e.name for e in backend.entry_points}
    # no .venv in the fixture: unknown, with the reason.
    assert backend.test_command.value is None
    assert "no interpreter" in (backend.test_command.reason or "")
    assert found["root"].language == "other"


# --------------------------------------------------------------- parts


@pytest.mark.parametrize(
    ("root", "expected"),
    [
        (".", "root"),
        ("a/b", "a.b"),
        ("my pkg", "my-pkg"),
        (".github", "p.github"),
        ("_internal", "p_internal"),
    ],
)
def test_part_id_slug(root: str, expected: str) -> None:
    assert part_id(root, set()) == expected


def test_part_id_collisions_take_a_suffix_in_order() -> None:
    taken: set[str] = set()
    ids = [part_id(r, taken) for r in ("a.b", "a/b", "a b", "a-b")]
    assert ids == ["a.b", "a.b-2", "a-b", "a-b-2"]
    assert part_id("a/b", taken) == "a.b-3"


def test_colliding_roots_become_separate_parts(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {"a.b/go.mod": "module x\n", "a/b/go.mod": "module y\n"},
    )
    assert sorted(run(root)) == ["a.b", "a.b-2"]


def test_dot_directory_part_gets_a_valid_id(tmp_path: Path) -> None:
    root = materialise(tmp_path, {".tools/go.mod": "module x\n"})
    assert list(run(root)) == ["p.tools"]


def test_vendored_and_build_directories_are_skipped(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "package.json": "{}",
            "node_modules/x/package.json": "{}",
            "vendor/y/go.mod": "module y\n",
            "build/z/Cargo.toml": "",
            "target/w/Cargo.toml": "",
        },
    )
    assert list(run(root)) == ["root"]


def test_cmake_subdirectory_is_not_a_part(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "CMakeLists.txt": "add_subdirectory(lib)\n",
            "lib/CMakeLists.txt": "add_library(l STATIC l.c)\n",
            "app/package.json": "{}",
            "app/CMakeLists.txt": "",
        },
    )
    assert sorted(run(root)) == ["app", "root"]


def test_manifest_kinds(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "a/setup.cfg": "",
            "b/build.gradle.kts": "",
            "c/App.csproj": "",
            "d/Gemfile": "",
            "e/pom.xml": "",
            "f/App.sln": "",
        },
    )
    assert sorted(run(root)) == ["a", "b", "c", "d", "e", "f"]


def test_language_is_the_largest_share_of_owned_files(
    tmp_path: Path,
) -> None:
    root = materialise(
        tmp_path,
        {
            "pyproject.toml": "",
            "app.ts": "",
            "b.ts": "",
            "c.py": "",
            "sub/go.mod": "module s\n",
            "sub/a.go": "",
            "sub/b.go": "",
            "sub/c.go": "",
            "sub/tests/test_x.py": "",
            "x.test.ts": "",
        },
    )
    found = run(root)
    assert found["root"].language == "typescript"
    assert found["sub"].language == "go"


def test_part_with_no_source_files_is_other(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"Cargo.toml": "[workspace]\n"})
    assert run(root)["root"].language == "other"


def test_target_is_the_one_whose_evidence_the_part_owns(
    tmp_path: Path,
) -> None:
    root = materialise(
        tmp_path,
        {
            "pyproject.toml": "[project]\nname='r'\n",
            "svc/pyproject.toml": (
                "[project]\nname='s'\ndependencies=['fastapi']\n"
            ),
            "svc/app.py": "x = 1\n",
        },
    )
    found = run(root, with_targets=True)
    assert found["svc"].target_class.value == "web_service"
    assert found["root"].target_class.value == "python_cpu"


def test_no_target_has_a_reason(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"go.mod": "module x\n"})
    part = run(root)["root"]
    assert part.target_id is None
    assert part.target_class.value is None
    assert part.target_class.reason


# ------------------------------------------------------- test commands


def test_pytest_config_gives_the_normalised_interpreter_command(
    tmp_path: Path,
) -> None:
    root = materialise(
        tmp_path,
        {"pyproject.toml": "[tool.pytest.ini_options]\naddopts=''\n"},
    )
    python = venv(root)
    command = run(root)["root"].test_command.value
    assert command is not None
    assert command.argv == (
        python,
        "-m",
        "pytest",
        "-q",
        "-x",
        "-p",
        "no:cacheprovider",
    )
    assert command.cwd == "."
    assert "tool.pytest" in command.evidence


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("pytest.ini", "[pytest]\n"),
        ("tox.ini", "[pytest]\n"),
        ("setup.cfg", "[tool:pytest]\n"),
    ],
)
def test_pytest_config_files(tmp_path: Path, name: str, text: str) -> None:
    files = {name: text, "pyproject.toml": ""}
    root = materialise(tmp_path, files)
    venv(root)
    assert run(root)["root"].test_command.value is not None


def test_pytest_from_a_tests_directory(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "pyproject.toml": "",
            "tests/test_a.py": "",
            "tests/x/conftest.py": "",
        },
    )
    venv(root)
    command = run(root)["root"].test_command.value
    assert command is not None
    assert "test_a.py" in command.evidence


def test_pytest_without_evidence_is_unknown_with_reason(
    tmp_path: Path,
) -> None:
    root = materialise(tmp_path, {"pyproject.toml": "", "tests/x.py": ""})
    venv(root)
    test = run(root)["root"].test_command
    assert test.value is None
    assert "no pytest configuration" in (test.reason or "")


def test_pytest_without_interpreter_is_unknown_with_reason(
    tmp_path: Path,
) -> None:
    root = materialise(tmp_path, {"pytest.ini": "", "pyproject.toml": ""})
    test = run(root)["root"].test_command
    assert test.value is None
    assert "no interpreter" in (test.reason or "")


def test_explicit_interpreter_wins(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"pytest.ini": "", "pyproject.toml": ""})
    command = run(root, python=Path(sys.executable))["root"].test_command.value
    assert command is not None
    assert command.argv[0] == sys.executable


def _node(tmp_path: Path, script: str, *, bins: tuple[str, ...] = ()) -> Part:
    root = materialise(
        tmp_path,
        {
            "package.json": "{}",
            "pkg/package.json": json.dumps({"scripts": {"test": script}}),
            "pkg/index.js": "",
        },
    )
    for name in bins:
        path = root / "node_modules" / ".bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return run(root)["pkg"]


def test_node_runner_resolves_to_a_hoisted_binary(tmp_path: Path) -> None:
    part = _node(tmp_path, "vitest run", bins=("vitest",))
    command = part.test_command.value
    assert command is not None
    assert command.argv[0].endswith("node_modules/.bin/vitest")
    assert command.argv[1:] == ("run",)
    assert command.cwd == "pkg"


def test_node_runner_not_installed_is_unknown(tmp_path: Path) -> None:
    test = _node(tmp_path, "jest").test_command
    assert test.value is None
    assert "node_modules/.bin/jest" in (test.reason or "")


def test_node_test_runner_builtin(tmp_path: Path) -> None:
    command = _node(tmp_path, "node --test").test_command.value
    assert command is not None
    assert command.argv == ("node", "--test")


@pytest.mark.parametrize(
    ("script", "fragment"),
    [
        ("npm run a && npm run b", "shell command line"),
        ("echo Error: no test specified", "placeholder"),
        ("tap", "not vitest"),
    ],
)
def test_node_unresolvable_scripts_say_why(
    tmp_path: Path, script: str, fragment: str
) -> None:
    test = _node(tmp_path, script, bins=("mocha",)).test_command
    assert test.value is None
    assert fragment in (test.reason or "")


def test_node_without_test_script(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"package.json": "{}", "a.js": ""})
    assert "no scripts.test" in (run(root)["root"].test_command.reason or "")


def test_jvm_commands(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "m/pom.xml": "",
            "m/src/test/java/T.java": "",
            "g/build.gradle": "",
            "g/gradlew": "",
            "g/src/test/java/T.java": "",
            "n/build.gradle.kts": "",
            "n/src/test/kotlin/T.kt": "",
        },
    )
    found = run(root)
    assert found["m"].test_command.value is not None
    assert found["m"].test_command.value.argv == ("mvn", "-q", "test")
    assert found["g"].test_command.value is not None
    assert found["g"].test_command.value.argv == ("./gradlew", "test")
    assert found["n"].test_command.value is not None
    assert found["n"].test_command.value.argv == ("gradle", "test")


def test_go_needs_test_files(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {"a/go.mod": "module a\n", "a/x.go": "", "b/go.mod": "module b\n"},
    )
    found = run(root)
    assert found["b"].test_command.value is None
    assert "no *_test.go" in (found["b"].test_command.reason or "")
    assert found["a"].test_command.value is None


def test_dotnet_and_ruby_commands(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "t/T.csproj": "<Ref Include='Microsoft.NET.Test.Sdk'/>",
            "u/U.csproj": "<Project/>",
            "r/Gemfile": "gem 'rspec'\n",
            "k/Gemfile": "",
            "k/Rakefile": "",
            "k/test/a_test.rb": "",
        },
    )
    found = run(root)
    assert found["t"].test_command.value is not None
    assert found["t"].test_command.value.argv == ("dotnet", "test")
    assert found["u"].test_command.value is None
    assert found["r"].test_command.value is not None
    assert found["r"].test_command.value.argv == ("bundle", "exec", "rspec")
    assert found["k"].test_command.value is not None
    assert found["k"].test_command.value.argv == ("rake", "test")


def test_language_chooses_between_two_runners(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "Cargo.toml": "",
            "pyproject.toml": "",
            "x.rs": "",
            "y.rs": "",
            "z.py": "",
        },
    )
    command = run(root)["root"].test_command.value
    assert command is not None
    assert command.argv == ("cargo", "test")


def test_cmake_only_part_has_no_test_command(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"CMakeLists.txt": "", "a.c": ""})
    test = run(root)["root"].test_command
    assert test.value is None
    assert "CMake" in (test.reason or "")


# -------------------------------------------------------------- native


def test_setup_py_is_parsed_not_run(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "setup.py": (
                "import sys\nraise SystemExit('executed')\n"
                "from setuptools import setup, Extension\n"
                "setup(ext_modules=[Extension('pkg._core',"
                " sources=['src/a.c', 'src/b.c']),\n"
                " Extension(name='pkg.fast', sources=['f.cpp']),\n"
                " Extension(NAME, sources=[])])\n"
            )
        },
    )
    part = run(root)["root"]
    assert [(n.name, n.sources) for n in part.native] == [
        ("pkg._core", ("src/a.c", "src/b.c")),
        ("pkg.fast", ("f.cpp",)),
    ]
    assert part.native[0].own_module.startswith("_core.")


def test_setuptools_ext_modules_in_pyproject(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "pyproject.toml": (
                "[[tool.setuptools.ext-modules]]\nname='p.m'\n"
                "sources=['m.c']\n"
            )
        },
    )
    (ext,) = run(root)["root"].native
    assert (ext.name, ext.sources) == ("p.m", ("m.c",))


def test_ext_suffix_comes_from_the_project_interpreter(
    tmp_path: Path,
) -> None:
    root = materialise(
        tmp_path,
        {"setup.py": "setup(ext_modules=[Extension('p.m', ['m.c'])])\n"},
    )
    bindir = tmp_path / "py"
    stub(bindir, "python", out=".cpython-399-fake.so")
    part = run(root, python=bindir / "python")["root"]
    assert part.native[0].own_module == "m.cpython-399-fake.so"


def test_pyo3_maturin_and_cdylib(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "py/pyproject.toml": (
                "[build-system]\nrequires=['maturin>=1']\n"
                "[tool.maturin]\nmodule-name='pk._rs'\n"
            ),
            "py/Cargo.toml": (
                "[package]\nname='pk-rs'\n[dependencies]\n"
                "pyo3={version='0.22', features=['abi3-py39']}\n"
            ),
            "py/src/lib.rs": "",
            "lone/Cargo.toml": (
                "[package]\nname='lone'\n[dependencies]\npyo3='0.22'\n"
            ),
            "dyn/Cargo.toml": (
                "[package]\nname='my-dyn'\n[lib]\ncrate-type=['cdylib']\n"
            ),
            "dyn/src/lib.rs": "",
            "plain/Cargo.toml": "[package]\nname='plain'\n",
        },
    )
    found = run(root, platform="darwin")
    (maturin,) = found["py"].native
    assert (maturin.kind, maturin.name) == ("maturin", "pk._rs")
    assert maturin.own_module == "_rs.abi3.so"
    assert maturin.sources == ("py/src/lib.rs",)
    assert found["lone"].native[0].kind == "pyo3"
    (dyn,) = found["dyn"].native
    assert (dyn.kind, dyn.own_module) == ("cargo_cdylib", "libmy_dyn.dylib")
    assert dyn.sources == ("dyn/src/lib.rs",)
    assert found["plain"].native == ()
    linux = run(root, platform="linux")["dyn"].native[0]
    assert linux.own_module == "libmy_dyn.so"


def test_node_gyp_targets(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "package.json": "{}",
            "binding.gyp": (
                "{ # comment\n 'targets': [{'target_name': 'addon',"
                " 'sources': ['src/addon.cc']}]}\n"
            ),
        },
    )
    (gyp,) = run(root)["root"].native
    assert (gyp.kind, gyp.own_module, gyp.sources) == (
        "node_gyp",
        "addon.node",
        ("src/addon.cc",),
    )


def test_cmake_targets(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "CMakeLists.txt": (
                "add_library(fast SHARED a.c b.cpp)\n"
                "add_library(mod MODULE m.c)\n"
                "add_library(stat STATIC s.c)\n"
                "add_library(dflt d.c)\n"
                "add_library(${NAME} SHARED x.c)\n"
                "add_executable(tool main.c util.h)\n"
            )
        },
    )
    mac = run(root, platform="darwin")["root"].native
    assert [(n.name, n.own_module) for n in mac] == [
        ("fast", "libfast.dylib"),
        ("mod", "libmod.so"),
        ("tool", "tool"),
    ]
    assert mac[2].sources == ("main.c", "util.h")
    linux = run(root, platform="linux")["root"].native
    assert linux[0].own_module == "libfast.so"


def test_cgo_only_in_non_test_go_files(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "go.mod": "module example.com/a/tool\n",
            "x_test.go": 'package a\nimport "C"\n',
            "y.go": "package a\n",
        },
    )
    assert run(root)["root"].native == ()


# ---------------------------------------------------------- benchmarks


def test_python_benchmarks(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "pyproject.toml": "[project]\ndependencies=['pytest-benchmark']\n",
            "asv.conf.json": "{}",
            "plain/pyproject.toml": "",
            "plain/tests/test_a.py": "def test_a(benchmark):\n    pass\n",
        },
    )
    python = venv(root)
    found = run(root)
    names = {b.kind: b for b in found["root"].benchmarks}
    assert names["pytest_benchmark"].argv[:3] == (python, "-m", "pytest")
    assert "--benchmark-only" in names["pytest_benchmark"].argv
    assert names["asv"].argv == (python, "-m", "asv", "run")
    assert [b.kind for b in found["plain"].benchmarks] == ["pytest_benchmark"]


def test_cargo_bench_from_bench_table_and_directory(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "Cargo.toml": "[[bench]]\nname='declared'\n",
            "benches/other.rs": "",
            "benches/sub/skip.rs": "",
        },
    )
    benches = run(root)["root"].benchmarks
    assert [b.name for b in benches] == ["declared", "other"]
    assert benches[0].argv == ("cargo", "bench", "--bench", "declared")


def test_go_benchmarks_are_per_directory(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "go.mod": "module m\n",
            "a_test.go": "func BenchmarkA(b *testing.B) {}\n",
            "pkg/b_test.go": "func BenchmarkB(b *testing.B) {}\n",
            "pkg/c_test.go": "func TestC(t *testing.T) {}\n",
        },
    )
    benches = run(root)["root"].benchmarks
    assert [(b.name, b.argv[-1]) for b in benches] == [
        ("go-bench", "./."),
        ("go-bench-pkg", "./pkg"),
    ]


def test_script_benchmarks_must_be_referenced(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "package.json": (
                '{"scripts": {"bench": "node bench/run.js",'
                ' "test": "node --test"}}'
            ),
            "bench/run.js": "",
            "bench_unused.js": "",
            "bench_used.js": "",
            "Makefile": "x:\n\tnode bench_used.js\n",
        },
    )
    benches = {b.name: b for b in run(root)["root"].benchmarks}
    assert benches["bench"].argv == ("npm", "run", "bench")
    assert benches["bench_used"].argv == ("node", "bench_used.js")
    assert "bench_unused" not in benches
    assert "test" not in benches


# --------------------------------------------------------- entry points


def test_python_entry_points(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "pyproject.toml": (
                "[project.scripts]\nrun='pkg.cli:main'\nghost='no.mod:f'\n"
            ),
            "src/pkg/__init__.py": "",
            "src/pkg/cli.py": "import x\n\ndef main():\n    pass\n",
            "src/pkg/public.py": "",
            "src/pkg/_private.py": "",
            "src/pkg/sub/__init__.py": "",
            "src/pkg/_hidden/__init__.py": "",
            "app.py": "@app.get('/x')\ndef x():\n    pass\n",
        },
    )
    entries = {(e.kind, e.name): e for e in run(root)["root"].entry_points}
    run_ep = entries[("console_script", "run")]
    assert (run_ep.file, run_ep.line) == ("src/pkg/cli.py", 3)
    ghost = entries[("console_script", "ghost")]
    assert (ghost.file, ghost.line) == ("pyproject.toml", None)
    assert ("public_module", "pkg.public") in entries
    assert ("public_module", "pkg.sub") in entries
    assert ("public_module", "pkg._private") not in entries
    assert ("public_module", "pkg._hidden") not in entries
    route = entries[("route", "@app.get('/x')")]
    assert (route.file, route.line) == ("app.py", 1)


def test_setup_cfg_and_setup_py_console_scripts(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "a/setup.cfg": (
                "[options.entry_points]\nconsole_scripts =\n"
                "    acmd = a.m:main\n"
            ),
            "b/setup.py": (
                "setup(entry_points={'console_scripts': ['bcmd=b.m:f']})\n"
            ),
        },
    )
    found = run(root)
    assert [e.name for e in found["a"].entry_points] == ["acmd"]
    assert [e.name for e in found["b"].entry_points] == ["bcmd"]


def test_rust_and_node_bins(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "r/Cargo.toml": (
                "[package]\nname='rr'\n[[bin]]\nname='tool'\n"
                "path='cli/tool.rs'\n"
            ),
            "r/src/main.rs": "fn main() {}\n",
            "r/src/bin/extra.rs": "\nfn main() {}\n",
            "r/cli/tool.rs": "pub fn main() {}\n",
            "n/package.json": '{"name": "@s/cli", "bin": "bin/x.js"}',
            "m/package.json": '{"bin": {"a": "./a.js"}}',
        },
    )
    found = run(root)
    assert [(e.name, e.file, e.line) for e in found["r"].entry_points] == [
        ("extra", "r/src/bin/extra.rs", 2),
        ("rr", "r/src/main.rs", 1),
        ("tool", "r/cli/tool.rs", 1),
    ]
    assert [(e.name, e.file) for e in found["n"].entry_points] == [
        ("cli", "n/bin/x.js")
    ]
    assert found["m"].entry_points[0].file == "m/a.js"


def test_go_main_package(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "go.mod": "module m\n",
            "cmd/tool/main.go": "package main\n\nfunc main() {}\n",
            "lib/x.go": "package lib\n",
            "cmd/tool/main_test.go": "package main\n",
        },
    )
    (entry,) = run(root)["root"].entry_points
    assert (entry.kind, entry.name, entry.file, entry.line) == (
        "main_package",
        "tool",
        "cmd/tool/main.go",
        3,
    )


# ------------------------------------------------------------ tooling


def test_python_profiler_follows_the_platform(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"pyproject.toml": "", "a.py": ""})
    bindir = tmp_path / "bin"
    stub(bindir, "py-spy")
    linux = run(root, path=str(bindir), platform="linux")["root"]
    assert linux.tooling.profiler.name == "py-spy"
    assert linux.tooling.profiler.available
    missing = run(root, path="", platform="linux")["root"].tooling.profiler
    assert not missing.available
    assert "py-spy" in (missing.reason or "")
    mac = run(root, path=str(bindir), platform="darwin")["root"]
    assert mac.tooling.profiler.name == "uv"
    assert not mac.tooling.profiler.available
    assert mac.tooling.coverage == "not_needed"


def test_profiler_binaries_per_language(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "js/package.json": "{}",
            "js/a.js": "",
            "go/go.mod": "module g\n",
            "go/a.go": "",
            "jv/pom.xml": "",
            "jv/A.java": "",
        },
    )
    bindir = tmp_path / "bin"
    for name in ("node", "go", "java"):
        stub(bindir, name)
    found = run(root, path=str(bindir))
    assert found["js"].tooling.profiler.available
    assert found["go"].tooling.profiler.available
    jvm = found["jv"].tooling.profiler
    assert not jvm.available
    assert "jfr" in (jvm.reason or "")
    stub(bindir, "jfr")
    assert run(root, path=str(bindir))["jv"].tooling.profiler.available


def test_native_recorder_on_linux_checks_perf_event_open(
    tmp_path: Path,
) -> None:
    root = materialise(tmp_path, {"go.mod": "module g\n"})
    ok, refused, absent = tmp_path / "ok", tmp_path / "no", tmp_path / "x"
    stub(ok, "perf")
    stub(refused, "perf", code=255, out="perf_event_open: Permission denied")
    absent.mkdir()
    good = run(root, path=str(ok))["root"].tooling.native_recorder
    assert (good.name, good.available) == ("perf", True)
    bad = run(root, path=str(refused))["root"].tooling.native_recorder
    assert not bad.available
    assert "perf_event_open" in (bad.reason or "")
    gone = run(root, path=str(absent))["root"].tooling.native_recorder
    assert not gone.available
    assert gone.reason


def test_native_recorder_on_macos_is_sample(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"go.mod": "module g\n"})
    bindir = tmp_path / "bin"
    stub(bindir, "sample")
    tool = run(root, path=str(bindir), platform="darwin")[
        "root"
    ].tooling.native_recorder
    assert (tool.name, tool.available) == ("sample", True)


def test_node_coverage_is_present_when_c8_is_installed(
    tmp_path: Path,
) -> None:
    root = materialise(
        tmp_path,
        {"package.json": "{}", "pkg/package.json": "{}", "pkg/a.js": ""},
    )
    assert run(root)["pkg"].tooling.coverage == "missing"
    c8 = root / "node_modules" / ".bin" / "c8"
    c8.parent.mkdir(parents=True)
    c8.write_text("")
    tooling = run(root)["pkg"].tooling
    assert (tooling.coverage, tooling.coverage_install) == ("present", None)


def test_rust_coverage_probes_cargo_llvm_cov(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"Cargo.toml": "", "a.rs": ""})
    have, lack = tmp_path / "have", tmp_path / "lack"
    stub(have, "cargo")
    stub(lack, "cargo", code=101, out="no such command")
    assert run(root, path=str(have))["root"].tooling.coverage == "present"
    tooling = run(root, path=str(lack))["root"].tooling
    assert tooling.coverage == "missing"
    assert tooling.coverage_install == (
        "cargo",
        "install",
        "cargo-llvm-cov",
        "--locked",
    )


def test_go_coverage_is_the_go_tool(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"go.mod": "module g\n", "a.go": ""})
    bindir = tmp_path / "bin"
    stub(bindir, "go")
    assert run(root, path=str(bindir))["root"].tooling.coverage == "present"
    missing = run(root, path="")["root"].tooling
    assert (missing.coverage, missing.coverage_install) == ("missing", None)


def test_clang_tools_cover_c_and_python_c_extensions(
    tmp_path: Path,
) -> None:
    root = materialise(
        tmp_path,
        {
            "lib/CMakeLists.txt": "add_executable(t a.c)\n",
            "lib/a.c": "",
            "py/setup.py": "setup(ext_modules=[Extension('p.m', ['m.c'])])\n",
            "py/p.py": "",
        },
    )
    bindir = tmp_path / "bin"
    stub(bindir, "llvm-cov")
    found = run(root, path=str(bindir))
    assert found["lib"].tooling.coverage == "missing"
    assert found["py"].tooling.coverage == "missing"
    stub(bindir, "llvm-profdata")
    found = run(root, path=str(bindir))
    assert found["lib"].tooling.coverage == "present"
    assert found["py"].tooling.coverage == "present"
    mac = run(root, path="", platform="darwin")["lib"].tooling
    assert mac.coverage_install == ("xcode-select", "--install")


def test_pyo3_python_part_needs_cargo_llvm_cov(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {
            "pyproject.toml": "[tool.maturin]\n",
            "Cargo.toml": "[package]\nname='p'\n[dependencies]\npyo3='1'\n",
            "p.py": "",
        },
    )
    tooling = run(root, path="")["root"].tooling
    assert tooling.coverage == "missing"
    assert tooling.coverage_install is not None
    assert tooling.coverage_install[2] == "cargo-llvm-cov"


def test_jacoco_jars_are_looked_for_in_one_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jars = tmp_path / "jars"
    monkeypatch.setattr(parts_mod, "JACOCO_DIR", jars)
    root = materialise(tmp_path, {"pom.xml": "", "A.java": ""})
    missing = run(root)["root"].tooling
    assert missing.coverage == "missing"
    assert missing.coverage_install is not None
    assert str(jars) in missing.coverage_install
    jars.mkdir()
    (jars / "org.jacoco.agent-0.8.12-runtime.jar").write_text("")
    assert run(root)["root"].tooling.coverage == "missing"
    (jars / "org.jacoco.cli-0.8.12-nodeps.jar").write_text("")
    assert run(root)["root"].tooling.coverage == "present"


def test_probe_timeouts_become_reasons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parts_mod, "PROBE_TIMEOUT_SECONDS", 0.2)
    root = materialise(tmp_path, {"go.mod": "module g\n"})
    bindir = tmp_path / "bin"
    bindir.mkdir()
    slow = bindir / "perf"
    slow.write_text("#!/bin/sh\nexec sleep 5\n")
    slow.chmod(0o755)
    tool = run(root, path=f"{bindir}:/bin:/usr/bin")[
        "root"
    ].tooling.native_recorder
    assert not tool.available
    assert "did not finish" in (tool.reason or "")


# --------------------------------- probes never run project code


def _shadow_repo(tmp_path: Path) -> tuple[Path, Path]:
    marker = tmp_path / "marker"
    root = materialise(
        tmp_path,
        {
            "sysconfig.py": f"open({str(marker)!r}, 'w').write('ran')\n",
            "setup.py": "setup(ext_modules=[Extension('p.m', ['m.c'])])\n",
            "pytest.ini": "",
        },
    )
    return root, marker


def test_interpreter_probes_do_not_import_repository_modules(
    tmp_path: Path,
) -> None:
    root, marker = _shadow_repo(tmp_path)
    part = run(root, python=Path(sys.executable))["root"]
    assert not marker.exists()
    assert part.native[0].own_module.endswith(".so")
    assert part.test_command.value is not None


def test_interpreter_probes_ignore_pythonpath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, marker = _shadow_repo(tmp_path)
    monkeypatch.setenv("PYTHONPATH", str(root))
    run(root, python=Path(sys.executable))
    assert not marker.exists()


def test_tool_probes_run_outside_the_repository(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"Cargo.toml": "", "a.rs": ""})
    seen = tmp_path / "cwd"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    cargo = bindir / "cargo"
    cargo.write_text(f"#!/bin/sh\npwd > {seen}\n")
    cargo.chmod(0o755)
    run(root, path=str(bindir))
    assert seen.read_text().strip() != ""
    assert Path(seen.read_text().strip()).resolve() != root.resolve()


# --------------------------------------- interpreter, fail closed


def test_venv_directory_names_and_virtual_env(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"pytest.ini": "", "pyproject.toml": ""})
    python = venv(root, "venv")
    command = run(root)["root"].test_command.value
    assert command is not None
    assert command.argv[0] == python
    other = tmp_path / "other"
    other.mkdir()
    root2 = materialise(other, {"pytest.ini": "", "pyproject.toml": ""})
    active = venv(other, "ve")
    found = run(root2, environ={"VIRTUAL_ENV": str(other / "ve")})["root"]
    assert found.test_command.value is not None
    assert found.test_command.value.argv[0] == active


def test_per_part_venv_is_preferred(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {"pyproject.toml": "", "svc/pytest.ini": "", "svc/pyproject.toml": ""},
    )
    venv(root)
    part_python = venv(root / "svc")
    command = run(root)["svc"].test_command.value
    assert command is not None
    assert command.argv[0] == part_python


def test_interpreter_without_pytest_is_unknown(tmp_path: Path) -> None:
    root = materialise(tmp_path, {"pytest.ini": "", "pyproject.toml": ""})
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\necho 'No module named pytest' >&2\nexit 1\n")
    python.chmod(0o755)
    test = run(root)["root"].test_command
    assert test.value is None
    assert "cannot import pytest" in (test.reason or "")
    assert "--python" not in (test.reason or "")


def test_no_interpreter_reason_does_not_name_a_missing_flag(
    tmp_path: Path,
) -> None:
    root = materialise(tmp_path, {"pytest.ini": "", "pyproject.toml": ""})
    test = run(root)["root"].test_command
    assert "--python" not in (test.reason or "")
    assert "virtual" in (test.reason or "")


# ------------------------------ the project's own runner


def test_nested_tests_directory_is_not_the_parts_tests(
    tmp_path: Path,
) -> None:
    root = materialise(
        tmp_path,
        {
            "pyproject.toml": "",
            "scripts/q/tests/test_x.py": "",
            "lib/tests/test_y.py": "",
        },
    )
    venv(root)
    test = run(root)["root"].test_command
    assert test.value is None
    assert "no test_*.py under" in (test.reason or "")


def test_own_runner_without_pytest_config_is_not_replaced_by_pytest(
    tmp_path: Path,
) -> None:
    root = materialise(
        tmp_path,
        {"pyproject.toml": "", "tests/test_a.py": "", "manage.py": ""},
    )
    venv(root)
    test = run(root)["root"].test_command
    assert test.value is None
    assert "manage.py" in (test.reason or "")


def test_pytest_config_wins_over_a_runner_file(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {"pytest.ini": "", "pyproject.toml": "", "runtests.py": ""},
    )
    venv(root)
    assert run(root)["root"].test_command.value is not None


def test_django_layout_gets_no_confident_wrong_command(
    tmp_path: Path,
) -> None:
    root = tmp_path / "django"
    shutil.copytree(FIXTURES / "django", root)
    for path in list(root.rglob("*.fixture")):
        path.rename(path.with_suffix(""))
    venv(root)
    part = run(root, with_targets=True)["root"]
    assert part.language == "python"
    assert part.test_command.value is None
    assert "tests/runtests.py" in (part.test_command.reason or "")


def test_asv_config_in_asv_bench(tmp_path: Path) -> None:
    root = materialise(
        tmp_path,
        {"pyproject.toml": "", "asv_bench/asv.conf.json": "{}"},
    )
    venv(root)
    (bench,) = run(root)["root"].benchmarks
    assert (bench.kind, bench.cwd) == ("asv", "asv_bench")
