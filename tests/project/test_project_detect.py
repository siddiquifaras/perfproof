import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from project_helpers import (
    BENCH,
    NOW,
    PYTHON_FILES,
    ROOT,
    VERSION,
    MakeRepo,
    git,
)

from perfproof.project.detect import Proposal, detect, slug

SCRATCH = ROOT / "scratch"


def found(repo: Path, **kwargs: Any) -> Proposal:
    return detect(repo, now=NOW, version=VERSION, **kwargs)


def classes(p: Proposal) -> set[str]:
    return {t.target_class for t in p.targets}


def snapshot(root: Path) -> dict[str, tuple[int, float]]:
    out: dict[str, tuple[int, float]] = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            path = Path(dirpath, name)
            st = path.stat()
            out[str(path)] = (st.st_size, st.st_mtime_ns)
    return out


# The examples (T5.0's fixtures are built from these)


def test_membership_example(example_repo: Callable[[str], Path]) -> None:
    p = found(example_repo("membership"))
    assert classes(p) == {"python_cpu"}
    assert [(w.id, w.source, w.command) for w in p.workloads] == [
        ("bench", "existing_benchmark", ("python", "bench.py"))
    ]
    assert [f.path for f in p.workloads[0].provenance.files] == ["bench.py"]


def test_rolling_example(example_repo: Callable[[str], Path]) -> None:
    p = found(example_repo("rolling"))
    assert classes(p) == {"python_cpu"}
    assert [w.command for w in p.workloads] == [("python", "bench.py")]


def test_colsum_example_is_native(
    example_repo: Callable[[str], Path],
) -> None:
    p = found(example_repo("colsum"))
    assert classes(p) == {"native"}
    assert [(w.source, w.command) for w in p.workloads] == [
        ("existing_benchmark", ("make", "bench"))
    ]
    assert p.how_to_run[0].build == (("make",),)


# Proposals are all proposed, and unknowns are asked


def test_every_entry_is_proposed_and_goals_are_unknown(
    make_repo: MakeRepo,
) -> None:
    p = found(make_repo(PYTHON_FILES))
    entries = (*p.targets, *p.workloads, *p.goals, *p.how_to_run)
    assert {e.status for e in entries} == {"proposed"}
    assert {e.version for e in entries} == {1}
    (goal,) = p.goals
    assert (goal.metric, goal.direction, goal.unit) == (None, None, None)
    assert goal.bar is None
    assert goal.guardrails is None
    assert set(goal.missing) == {
        "metric",
        "direction",
        "unit",
        "bar",
        "guardrails",
    }
    assert "goal:bench" in {q.id for q in p.questions}
    assert "constraints" in {q.id for q in p.questions}


def test_how_to_run_leaves_unknowns_unknown(make_repo: MakeRepo) -> None:
    p = found(make_repo(PYTHON_FILES))
    (how,) = p.how_to_run
    assert how.setup is None
    assert how.build is None
    assert how.services is None
    assert how.reset is None
    assert set(how.missing) == {"setup", "build", "services", "reset"}


def test_uv_lock_gives_a_setup_and_launcher(make_repo: MakeRepo) -> None:
    p = found(make_repo({**PYTHON_FILES, "uv.lock": "version = 1\n"}))
    assert p.how_to_run[0].setup == (("uv", "sync", "--frozen"),)
    assert p.workloads[0].command == ("uv", "run", "python", "bench.py")
    assert "uv.lock" in [f.path for f in p.workloads[0].provenance.files]


# Workload sources


def test_benchmarks_package_with_main(make_repo: MakeRepo) -> None:
    p = found(
        make_repo(
            {
                **PYTHON_FILES,
                "benchmarks/__main__.py": "print('x')\n",
                "benchmarks/measure.py": "x = 1\n",
            }
        )
    )
    by_id = {w.id: w for w in p.workloads}
    assert by_id["benchmarks"].command == ("python", "-m", "benchmarks")
    assert by_id["benchmarks"].cwd == "."


def test_pyproject_scripts_are_entry_points(make_repo: MakeRepo) -> None:
    files = {
        **PYTHON_FILES,
        "pyproject.toml": PYTHON_FILES["pyproject.toml"]
        + '[project.scripts]\ntile = "demo.cli:main"\n',
    }
    p = found(make_repo(files))
    entry = next(w for w in p.workloads if w.id == "tile")
    assert (entry.source, entry.command) == ("entry_point", ("tile",))


def test_main_package_is_an_entry_point(make_repo: MakeRepo) -> None:
    p = found(make_repo({**PYTHON_FILES, "src/demo/__main__.py": "x = 1\n"}))
    entry = next(w for w in p.workloads if w.id == "demo-main")
    assert entry.command == ("python", "-m", "demo")


def test_makefile_bench_target_and_cargo_benches(
    make_repo: MakeRepo,
) -> None:
    p = found(
        make_repo(
            {
                "Makefile": "all:\n\ttrue\nbench:\n\ttrue\n",
                "src/lib.c": "int x;\n",
                "crate/Cargo.toml": "[package]\nname='c'\n",
                "crate/src/lib.rs": "",
                "crate/benches/b.rs": "",
            }
        )
    )
    commands = {w.id: (w.command, w.cwd) for w in p.workloads}
    assert commands["bench"] == (("make", "bench"), ".")
    assert commands["crate-cargo-bench"] == (("cargo", "bench"), "crate")


def test_slowest_tests_come_from_a_junit_file(make_repo: MakeRepo) -> None:
    fixtures = ROOT / "tests" / "fixtures" / "pytest-junit"
    repo = make_repo(
        {
            "pyproject.toml": PYTHON_FILES["pyproject.toml"],
            "tests/test_demo.py": (fixtures / "test_demo.py.txt").read_text(),
            "junit.xml": (fixtures / "junit.xml").read_text(),
        }
    )
    p = found(repo)
    slow = [w for w in p.workloads if w.source == "slow_test"]
    assert [w.command[-1] for w in slow] == [
        "tests/test_demo.py::test_slow",
        "tests/test_demo.py::TestGroup::test_method",
        "tests/test_demo.py::test_fast",
    ]
    assert "0.41 s" in p.evidence[slow[0].id][0]
    assert "junit.xml" in [f.path for f in slow[0].provenance.files]
    assert "tests/test_demo.py" in [f.path for f in slow[0].provenance.files]


def test_a_guessed_interpreter_or_unknown_arguments_carry_a_caveat(
    make_repo: MakeRepo,
) -> None:
    p = found(
        make_repo(
            {
                **PYTHON_FILES,
                "benchmarks/__main__.py": "print('x')\n",
                "pyproject.toml": PYTHON_FILES["pyproject.toml"]
                + '[project.scripts]\ntile = "demo:main"\n',
            }
        )
    )
    assert p.caveats["bench"] == (
        "interpreter not established: bare `python` was assumed because "
        "there is no uv.lock"
    )
    assert "arguments not established" in p.caveats["benchmarks"]
    assert "interpreter not established" in p.caveats["benchmarks"]
    assert "installed on PATH" in p.caveats["tile"]
    assert any("caveat:" in e for e in p.evidence["bench"])
    uv = found(
        make_repo(
            {
                **PYTHON_FILES,
                "uv.lock": "version = 1\n",
                "benchmarks/__main__.py": "print('x')\n",
            }
        )
    )
    assert "bench" not in uv.caveats
    assert uv.caveats["benchmarks"] == "arguments not established"


@pytest.mark.integration
def test_slowest_tests_from_one_real_pytest_run(make_repo: MakeRepo) -> None:
    repo = make_repo(
        {
            **PYTHON_FILES,
            "tests/test_demo.py": (
                "import time\n"
                "def test_slow():\n    time.sleep(0.3)\n"
                "def test_fast():\n    pass\n"
            ),
        }
    )
    p = found(repo, slow_tests=True, python=Path(sys.executable))
    slow = [w for w in p.workloads if w.source == "slow_test"]
    assert slow
    assert slow[0].command[-1] == "tests/test_demo.py::test_slow"
    assert slow[0].provenance.commands
    assert "--durations=3" in slow[0].provenance.commands[0]
    assert git(repo, "status", "--porcelain") == ""


def test_slow_tests_need_an_interpreter(make_repo: MakeRepo) -> None:
    p = found(make_repo(PYTHON_FILES), slow_tests=True)
    assert not [w for w in p.workloads if w.source == "slow_test"]
    assert any("no interpreter" in n for n in p.notes)


# Other classes get a target and a question, never an invented workload


def test_routes_make_a_web_target_without_a_workload(
    make_repo: MakeRepo,
) -> None:
    app = (
        "from fastapi import FastAPI\napp = FastAPI()\n"
        "@app.get('/a')\ndef a(): ...\n@app.post('/b')\ndef b(): ...\n"
    )
    p = found(
        make_repo(
            {
                "pyproject.toml": '[project]\nname="s"\nversion="1"\n'
                'dependencies=["fastapi"]\n',
                "app.py": app,
            }
        )
    )
    assert classes(p) == {"python_cpu", "web_service"}
    web = next(t for t in p.targets if t.target_class == "web_service")
    assert any("2 route declarations" in e for e in web.evidence)
    assert not [w for w in p.workloads if w.target_id == web.id]
    assert "workload:web_service" in {q.id for q in p.questions}


def test_serving_gpu_and_agent_evidence(make_repo: MakeRepo) -> None:
    p = found(
        make_repo(
            {
                "pyproject.toml": '[project]\nname="m"\nversion="1"\n'
                'dependencies=["vllm", "torch", "anthropic"]\n',
                "serve.sh": "vllm serve model --port 8000\n",
                "kernels/add.cu": "__global__ void add() {}\n",
            }
        )
    )
    assert classes(p) >= {"llm_serving", "gpu_kernel", "agentic"}
    serving = next(t for t in p.targets if t.target_class == "llm_serving")
    assert any("serve.sh" in e for e in serving.evidence)
    assert not p.workloads


def test_nothing_detected_says_so(make_repo: MakeRepo) -> None:
    p = found(make_repo({"README.md": "hello\n"}))
    assert not p.targets
    assert p.notes == ("no target class was detected",)
    assert not p.questions


def test_skipped_directories_and_candidates_are_not_workloads(
    make_repo: MakeRepo,
) -> None:
    p = found(
        make_repo(
            {
                **PYTHON_FILES,
                "candidates/fix/bench.py": BENCH,
                ".venv/lib/bench_x.py": BENCH,
                "node_modules/y/bench_y.py": BENCH,
            }
        )
    )
    assert [w.id for w in p.workloads] == ["bench"]


def test_detection_writes_nothing(make_repo: MakeRepo) -> None:
    repo = make_repo(PYTHON_FILES)
    before = snapshot(repo)
    found(repo)
    assert snapshot(repo) == before
    assert not (repo / ".perfproof").exists()


def test_ids_are_unique_and_valid(make_repo: MakeRepo) -> None:
    p = found(
        make_repo(
            {
                **PYTHON_FILES,
                "a/bench.py": BENCH,
                "b/bench.py": BENCH,
            }
        )
    )
    ids = [w.id for w in p.workloads]
    assert len(ids) == len(set(ids)) == 3


def test_slug() -> None:
    assert slug("Bench_Tile.py") == "bench_tile.py"
    assert slug("../") == "workload"


# Clones of the real case-study repositories, read-only


def read_only_clone(name: str) -> Path:
    clone = SCRATCH / name
    if not (clone / ".git").is_dir():
        pytest.skip(f"no clone at scratch/{name}")
    return clone


def test_frames2py_clone() -> None:
    clone = read_only_clone("frames2py")
    before = snapshot(clone)
    p = found(clone)
    assert snapshot(clone) == before
    assert classes(p) == {"python_cpu"}
    bench = next(w for w in p.workloads if w.id == "benchmarks")
    assert bench.command == ("uv", "run", "python", "-m", "benchmarks")
    assert bench.source == "existing_benchmark"
    assert "uv.lock" in [f.path for f in bench.provenance.files]
    assert p.how_to_run[0].setup == (("uv", "sync", "--frozen"),)


def test_picolm_clone() -> None:
    clone = read_only_clone("picolm")
    before = snapshot(clone)
    p = found(clone)
    assert snapshot(clone) == before
    assert classes(p) == {"native"}
    assert p.how_to_run[0].build == (("make", "-C", "picolm"),)
    assert not p.workloads
    assert "workload:native" in {q.id for q in p.questions}


# Node, JVM and the web frameworks of Node, Go and Ruby


def target(p: Proposal, cls: str) -> Any:
    return next(t for t in p.targets if t.target_class == cls)


def test_package_json_with_sources_is_node(make_repo: MakeRepo) -> None:
    p = found(
        make_repo(
            {
                "package.json": '{"name":"lib","dependencies":{"lodash":"4"}}',
                "src/index.js": "module.exports = 1;\n",
                "src/index.test.js": "test();\n",
            }
        )
    )
    assert classes(p) == {"node"}
    evidence = target(p, "node").evidence
    assert evidence[0] == "package.json: package.json"
    assert "1 of 1 source files (100%)" in evidence[1]
    assert "workload:node" in {q.id for q in p.questions}
    assert not p.workloads


def test_package_json_without_sources_is_not_node(
    make_repo: MakeRepo,
) -> None:
    # Only tests, declarations, configs and dot directories: no source.
    p = found(
        make_repo(
            {
                "package.json": '{"name":"tool"}',
                "test/a.js": "x\n",
                "types/a.d.ts": "x\n",
                "eslint.config.js": "x\n",
                ".github/scripts/a.js": "x\n",
            }
        )
    )
    assert not p.targets


@pytest.mark.parametrize(
    "dep",
    ["express", "fastify", "@nestjs/core", "koa", "@hapi/hapi"],
)
def test_node_web_frameworks_are_web_service_not_node(
    make_repo: MakeRepo, dep: str
) -> None:
    p = found(
        make_repo(
            {
                "package.json": f'{{"dependencies":{{"{dep}":"1"}}}}',
                "src/server.js": "x\n",
            }
        )
    )
    assert classes(p) == {"web_service"}
    assert dep in target(p, "web_service").evidence[0]


@pytest.mark.parametrize(
    "manifest", ["pom.xml", "build.gradle", "build.gradle.kts"]
)
def test_jvm_build_manifests_are_jvm(
    make_repo: MakeRepo, manifest: str
) -> None:
    p = found(make_repo({manifest: "x\n", "src/Main.java": "class Main {}\n"}))
    assert classes(p) == {"jvm"}
    assert target(p, "jvm").evidence[0] == f"build manifests: {manifest}"
    assert "workload:jvm" in {q.id for q in p.questions}


@pytest.mark.parametrize(
    "module",
    [
        "github.com/gin-gonic/gin v1.10.0",
        "github.com/labstack/echo/v4 v4.12.0",
        "github.com/gofiber/fiber/v2 v2.52.0",
    ],
)
def test_go_web_framework_requirement_is_web_service(
    make_repo: MakeRepo, module: str
) -> None:
    mod = f"module example.com/app\n\ngo 1.22\n\nrequire (\n\t{module}\n)\n"
    p = found(make_repo({"go.mod": mod, "main.go": "package main\n"}))
    assert classes(p) == {"web_service"}
    assert "Go web framework in go.mod" in target(p, "web_service").evidence[0]


def test_go_listen_and_serve_outside_tests_is_web_service(
    make_repo: MakeRepo,
) -> None:
    serve = 'package main\nfunc main() { http.ListenAndServe(":80", nil) }\n'
    p = found(
        make_repo(
            {
                "go.mod": "module example.com/app\n",
                "main.go": serve,
                "main_test.go": serve,
            }
        )
    )
    assert classes(p) == {"web_service"}
    evidence = target(p, "web_service").evidence
    assert evidence == (
        "1 non-test Go files call http.ListenAndServe, first: main.go:2",
    )


@pytest.mark.parametrize("gem", ["rails", "sinatra", "roda"])
def test_ruby_web_gems_are_web_service(make_repo: MakeRepo, gem: str) -> None:
    gemfile = f'source "https://rubygems.org"\ngem "{gem}", "~> 7"\n'
    p = found(make_repo({"Gemfile": gemfile, "app.rb": "x\n"}))
    assert classes(p) == {"web_service"}
    assert (
        f"Ruby web framework in Gemfile: {gem}"
        in (target(p, "web_service").evidence[0])
    )


def test_plain_go_dotnet_and_ruby_libraries_get_no_class(
    make_repo: MakeRepo,
) -> None:
    p = found(
        make_repo(
            {
                "go.mod": "module example.com/lib\n\nrequire (\n"
                "\tgithub.com/stretchr/testify v1.9.0\n)\n",
                "lib.go": "package lib\nfunc F() {}\n",
                "App.csproj": "<Project />\n",
                "Program.cs": "class P {}\n",
                "Gemfile": 'gem "rake"\ngem "rubocop-rails"\n',
                "lib.rb": "x\n",
            }
        )
    )
    assert not p.targets


def test_tooling_package_json_in_another_language_repo_is_not_node(
    make_repo: MakeRepo,
) -> None:
    files = {f"lib/m{i}.rb": "x\n" for i in range(5)}
    files |= {"Gemfile": 'gem "rake"\n', "package.json": '{"name":"ui"}'}
    files |= {"app/a.js": "x\n", "app/b.js": "x\n"}
    p = found(make_repo(files))
    assert "node" not in classes(p)


def test_mostly_js_repo_with_a_few_other_files_is_node(
    make_repo: MakeRepo,
) -> None:
    files = {f"src/m{i}.ts": "x\n" for i in range(4)}
    files |= {"package.json": '{"name":"lib"}', "scripts/tool.py": "x\n"}
    p = found(make_repo(files))
    assert "node" in classes(p)
    assert "4 of 5 source files (80%)" in target(p, "node").evidence[1]


def many(pattern: str, n: int, text: str = "x\n") -> dict[str, str]:
    return {pattern.format(i): text for i in range(n)}


def test_python_with_a_tooling_pom_is_not_jvm(make_repo: MakeRepo) -> None:
    files = many("pkg/m{}.py", 20)
    files |= {"tools/helper/pom.xml": "x\n", "tools/helper/src/H.java": "x\n"}
    assert "jvm" not in classes(found(make_repo(files)))


def test_cpp_with_an_android_gradle_file_is_not_jvm(
    make_repo: MakeRepo,
) -> None:
    files = many("src/m{}.cpp", 20)
    files |= {"CMakeLists.txt": "x\n", "android/build.gradle": "x\n"}
    assert "jvm" not in classes(found(make_repo(files)))


def test_a_manifest_under_examples_or_tests_does_not_make_jvm(
    make_repo: MakeRepo,
) -> None:
    files = many("pkg/m{}.py", 5)
    files |= {"examples/java/pom.xml": "x\n", "tests/fixtures/pom.xml": "x\n"}
    files |= many("examples/java/src/E{}.java", 9)
    assert "jvm" not in classes(found(make_repo(files)))


def test_deep_java_packages_beat_a_few_js_files(make_repo: MakeRepo) -> None:
    files = many("src/main/java/org/apache/foo/bar/C{}.java", 8)
    files |= {"pom.xml": "x\n", "package.json": '{"name":"site"}'}
    files |= many("site/s{}.js", 5)
    p = found(make_repo(files))
    assert classes(p) == {"jvm"}
    assert "8 of 13 source files (62%)" in target(p, "jvm").evidence[1]


def test_python_with_js_only_under_non_source_dirs_is_not_node(
    make_repo: MakeRepo,
) -> None:
    files = many("app/m{}.py", 3) | {"package.json": '{"name":"ui"}'}
    for folder in ("spec", "e2e", "app/static/js", "public", "fixtures"):
        files |= many(folder + "/s{}.js", 4)
    assert "node" not in classes(found(make_repo(files)))


def test_go_library_with_an_example_server_is_not_a_web_service(
    make_repo: MakeRepo,
) -> None:
    serve = 'package main\nfunc main() { http.ListenAndServe(":80", nil) }\n'
    files = {
        "go.mod": "module x\n",
        "lib.go": "package x\n",
        "examples/server/main.go": serve,
    }
    assert not found(make_repo(files)).targets


@pytest.mark.parametrize(
    "text",
    [
        "package x\n// call http.ListenAndServe(addr, h)\n",
        "package x\n/*\nhttp.ListenAndServe(addr, h)\n*/\n",
        'package x\nvar p = "/*"\n// http.ListenAndServe(a, h)\n',
    ],
)
def test_a_listen_and_serve_in_a_comment_is_not_a_web_service(
    make_repo: MakeRepo, text: str
) -> None:
    files = {"go.mod": "module x\n", "lib.go": text}
    assert not found(make_repo(files)).targets


def test_a_truncated_walk_proposes_neither_node_nor_jvm(
    make_repo: MakeRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = many("src/m{}.ts", 10) | {"package.json": '{"name":"lib"}'}
    files |= {"pom.xml": "x\n"} | many("src/J{}.java", 3)
    repo = make_repo(files)
    monkeypatch.setattr(
        sys.modules["perfproof.project.detect"], "MAX_FILES", 5
    )
    p = found(repo)
    assert not classes(p) & {"node", "jvm"}
    assert any("stopped early" in n for n in p.notes)


@pytest.mark.parametrize(
    ("many_files", "few_files"),
    [
        ("app/M{}.php", "resources/js/a{}.js"),
        ("Sources/M{}.swift", "web/a{}.js"),
        ("lib/m{}.ex", "web/a{}.js"),
    ],
    ids=["php", "swift", "elixir"],
)
def test_a_language_outside_the_old_list_still_outvotes_a_few_js_files(
    make_repo: MakeRepo, many_files: str, few_files: str
) -> None:
    files = many(many_files, 40) | many(few_files, 3)
    files["package.json"] = '{"devDependencies":{"vite":"5"}}'
    assert "node" not in classes(found(make_repo(files)))


def test_a_jvm_manifest_beside_a_mostly_swift_repo_is_not_jvm(
    make_repo: MakeRepo,
) -> None:
    files = many("Sources/M{}.swift", 40) | many("src/J{}.java", 3)
    files["build.gradle"] = "x\n"
    assert "jvm" not in classes(found(make_repo(files)))


def test_a_mostly_ts_repo_gets_node_with_the_share_in_the_evidence(
    make_repo: MakeRepo,
) -> None:
    files = many("src/m{}.ts", 9) | many("src/v{}.vue", 3)
    files |= many("scripts/t{}.py", 2) | {"package.json": '{"name":"app"}'}
    p = found(make_repo(files))
    assert "node" in classes(p)
    assert "12 of 14 source files (86%)" in target(p, "node").evidence[1]


def test_exactly_half_is_not_more_than_half(make_repo: MakeRepo) -> None:
    files = many("src/m{}.ts", 5) | many("lib/m{}.php", 5)
    files["package.json"] = '{"name":"app"}'
    assert "node" not in classes(found(make_repo(files)))
