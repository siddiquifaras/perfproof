"""find-* specs, user specs, the profiler table, labels, and compare."""

import json
from pathlib import Path

import pytest
from find_helpers import init_repo, make_part

from perfproof.cli import main
from perfproof.find import mapping, profilers, specs
from perfproof.schema import CLASS_SCHEDULES, BenchmarkSpec


def spec(name: str = "find-tests-root", **over: object) -> BenchmarkSpec:
    args: dict[str, object] = {
        "name": name,
        "command": ("python", "-m", "pytest"),
        "cwd": ".",
        "harness": "pyproject.toml",
        "target_class": "python_cpu",
        "timeout_seconds": 300.0,
    }
    args.update(over)
    return specs.build_spec(**args)  # type: ignore[arg-type]


def test_spec_constants_follow_the_contract() -> None:
    s = spec(target_class="llm_serving", timeout_seconds=123.0)
    n0, k, looks, rounds = CLASS_SCHEDULES["llm_serving"]
    assert (s.looks.n0, s.looks.k, s.looks.max_looks) == (n0, k, looks)
    assert s.budget.max_rounds == rounds
    assert s.budget.max_seconds == 123.0
    assert s.timeout_seconds == 123.0
    assert s.metric.kind == "wall"
    assert s.warmup == 0
    assert s.correctness.command is None
    assert s.harness == ("pyproject.toml",)
    assert s.target_class == "llm_serving"


def test_an_unknown_class_uses_the_python_cpu_schedule() -> None:
    s = spec(target_class=None)
    n0, k, looks, rounds = CLASS_SCHEDULES["python_cpu"]
    assert (s.looks.n0, s.looks.k, s.looks.max_looks) == (n0, k, looks)
    assert s.budget.max_rounds == rounds
    assert s.target_class is None


def test_changing_the_test_timeout_changes_the_spec_hash() -> None:
    assert (
        spec(timeout_seconds=300.0).spec_hash
        != spec(timeout_seconds=60.0).spec_hash
    )


def test_names() -> None:
    assert specs.tests_spec_name("api.v1") == "find-tests-api.v1"
    assert specs.bench_spec_name("root", "a b/c") == "find-bench-root-a-b-c"
    assert specs.is_find_name("find-x")
    assert not specs.is_find_name("my-find")


def test_define_goes_through_bench_define(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    init_repo(tmp_path)
    s = spec()
    specs.define_spec(tmp_path, s)
    specs.define_spec(tmp_path, s)
    stored = tmp_path / ".perfproof" / "benchmarks" / s.name
    assert [p.stem for p in stored.glob("*.json")] == [s.spec_hash]


def test_user_specs_are_everything_but_find(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    init_repo(tmp_path)
    specs.define_spec(tmp_path, spec("find-tests-root"))
    specs.define_spec(tmp_path, spec("mine"))
    assert sorted(specs.user_specs(tmp_path)) == ["mine"]


def test_compare_refuses_find_specs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    init_repo(tmp_path)
    specs.define_spec(tmp_path, spec())
    code = main(
        [
            "compare",
            "find-tests-root",
            "--candidate",
            ".",
            "--repo",
            str(tmp_path),
            "--json",
        ]
    )
    document = json.loads(capsys.readouterr().out)
    assert code == 1
    assert document["error"]["code"] == "find_spec_not_comparable"


def test_python_has_no_template_and_others_do() -> None:
    root = Path("/r")
    assert profilers.profiler_template("python", root) == ()
    assert profilers.profiler_template("other", root) is None
    node = profilers.profiler_template("javascript", root)
    assert node is not None
    assert node[1:4] == ("-m", "perfproof.profilers.node", "run")
    assert "{command}" in node
    assert "{output}" in node
    java = profilers.profiler_template("java", root)
    assert java is not None
    assert java[-3:-1] == (str(root), "--")
    assert profilers.profiler_template("kotlin", root) == java
    assert profilers.profiler_template("go", root) is not None
    assert profilers.profiler_template("rust", root) is not None
    assert profilers.profiler_template("csharp", root) is not None
    assert profilers.profiler_template("ruby", root) is not None


def test_native_template_and_c_use_the_native_recorder() -> None:
    template = profilers.native_template()
    assert template[1:5] == (
        "-m",
        "perfproof.profilers.native",
        "record",
        "--output",
    )
    assert template[-4:] == ("--root", ".", "--", "{command}")
    assert profilers.profiler_template("c", Path("/r")) == template
    assert profilers.profiler_template("cpp", Path("/r")) == template


def test_part_of_takes_the_longest_root() -> None:
    parts = [
        make_part("root", root="."),
        make_part("web", root="web"),
        make_part("web.api", root="web/api"),
    ]
    assert mapping.part_of(parts, "a.py") == "root"
    assert mapping.part_of(parts, "web/x.js") == "web"
    assert mapping.part_of(parts, "web/api/x.js") == "web.api"
    assert mapping.part_of(parts, "webby/x.js") == "root"
    assert mapping.part_of(parts, None) is None
    assert mapping.part_of([make_part("web", root="web")], "other/x") is None


@pytest.mark.parametrize(
    ("kind", "language", "layer"),
    [
        ("python", "python", "python"),
        ("gpu_kernel", "python", "gpu"),
        ("native", "python", "native"),
        ("native", "rust", "rust"),
        ("native", "go", "go"),
        ("native", "cpp", "native"),
        ("python", "javascript", "javascript"),
        ("python", "typescript", "typescript"),
        ("python", "java", "java"),
        ("python", "kotlin", "kotlin"),
        ("python", "csharp", "csharp"),
        ("python", "ruby", "ruby"),
        ("python", "go", "python"),
    ],
)
def test_layer_labels(kind: str, language: str, layer: str) -> None:
    assert mapping.layer_of(kind, make_part(language=language)) == layer


def test_a_row_with_no_part_is_other() -> None:
    assert mapping.layer_of("python", None) == "other"
    assert mapping.layer_of("gpu_kernel", None) == "other"
