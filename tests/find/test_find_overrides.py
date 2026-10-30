"""Medium's entry-point rule, --test-command, native scope, layers."""

import json
from pathlib import Path

import pytest
from find_helpers import copy_fixture, make_part
from test_find_engine import Fake, define_workload, find

from perfproof.cli import main
from perfproof.commands import CommandError
from perfproof.find import engine, mapping, plan
from perfproof.find.mapping import Ranking
from perfproof.find.state import load_test_commands


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return copy_fixture("py_tests", tmp_path / "proj")


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.setattr(engine, "run_probe", f)
    return f


def decide(
    entry_points: int,
    covered: int,
    tops: int,
    workloads: list[str] | None = None,
) -> list[str]:
    result = plan.decide(
        depth="medium",
        asked=plan.Asks((), (), ()),
        views=[],
        workloads=workloads or ["w1", "w2", "w3"],
        knobs=["w1"],
        uncovered_entries=[],
        entry_points=entry_points,
        covered_entries=covered,
        distinct_tops=tops,
        leads=[],
        waivers={},
    )
    return [s.code for s in result.next_steps]


@pytest.mark.parametrize(
    ("entries", "covered", "tops", "short"),
    [
        (0, 0, 2, False),
        (0, 0, 1, True),
        (0, 0, 0, True),
        (1, 1, 2, False),
        (1, 1, 1, True),
        (1, 0, 2, True),
        (2, 2, 1, False),
        (2, 1, 2, True),
        (5, 2, 1, False),
    ],
)
def test_medium_asks_min_two_entry_points_and_two_tops_for_a_library(
    entries: int, covered: int, tops: int, short: bool
) -> None:
    assert ("define_workloads" in decide(entries, covered, tops)) is short


def test_fewer_than_three_workloads_is_still_short() -> None:
    assert "define_workloads" in decide(0, 0, 2, workloads=["w1", "w2"])


def row(function: str, *seen: str, wrapper: bool = False) -> dict[str, object]:
    return {
        "function": function,
        "file": "a.py",
        "frame_kind": "python",
        "wrapper": wrapper,
        "seen_in": [{"benchmark": n} for n in seen],
    }


def test_distinct_tops_counts_each_workloads_first_function() -> None:
    ranking = Ranking(
        (
            row("main", "w1", "w2", wrapper=True),
            row("hot", "w1", "w2"),
            row("other", "w2"),
            row("third", "w3"),
        ),  # type: ignore[arg-type]
        (),
    )
    assert mapping.distinct_tops(ranking, ["w1", "w2"]) == 1
    assert mapping.distinct_tops(ranking, ["w1", "w2", "w3"]) == 2
    assert mapping.distinct_tops(ranking, []) == 0


@pytest.mark.parametrize(
    ("file", "kind", "language", "layer"),
    [
        ("lib/a.js", "python", "python", "javascript"),
        ("lib/a.mjs", "native", "javascript", "javascript"),
        ("lib/a.cjs", "python", "typescript", "javascript"),
        ("src/a.ts", "native", "javascript", "typescript"),
        ("src/a.tsx", "python", "javascript", "typescript"),
        ("src/A.java", "python", "kotlin", "java"),
        ("src/A.kt", "python", "java", "kotlin"),
        ("x.go", "native", "python", "go"),
        ("x.rs", "native", "python", "rust"),
        ("x.c", "python", "python", "native"),
        ("x.py", "native", "javascript", "python"),
        ("noext", "native", "python", "native"),
        ("noext", "python", "javascript", "javascript"),
    ],
)
def test_layer_prefers_the_file_extension(
    file: str, kind: str, language: str, layer: str
) -> None:
    part = make_part(language=language)
    assert mapping.layer_of(kind, part, file) == layer


def test_a_node_frame_in_a_node_part_is_not_native() -> None:
    part = make_part("web", root="web", language="javascript")
    assert mapping.layer_of("native", part, "web/lib/x.js") == "javascript"


NATIVE = {
    "kind": "setuptools_ext",
    "name": "fastsum",
    "own_module": "fastsum.so",
    "sources": ["fastsum.c"],
}


def two_parts() -> list[object]:
    return [
        make_part(native=(NATIVE,)),
        make_part("web", root="web", language="javascript", test=None),
    ]


def test_the_native_recorder_follows_the_workloads_own_part(
    repo: Path, fake: Fake
) -> None:
    define_workload(repo, "py")
    (repo / "web").mkdir()
    from perfproof.find import specs

    specs.define_spec(
        repo,
        specs.build_spec(
            name="js",
            command=("node", "w.js"),
            cwd="web",
            harness="pyproject.toml",
            target_class="node",
            timeout_seconds=60.0,
        ),
    )
    find(repo, two_parts(), no_tests=True)  # type: ignore[arg-type]
    assert fake.keys == ["js", "py", "py@native"]
    native = fake.calls[2]
    assert native.own_modules == ("fastsum.so",)


def test_the_leaf_rule_is_for_python_parts_and_reads_the_first_run(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class WithRuns(Fake):
        def __call__(self, root: Path, planned: object, **kw: object):  # type: ignore[no-untyped-def]
            out = super().__call__(root, planned, **kw)  # type: ignore[arg-type]
            assert out.record is not None
            data = out.record.model_dump(mode="json")
            data["run_ids"] = ["r1", "r2"]
            from perfproof.schema import ProbeRecord

            return type(out)(
                ProbeRecord.model_validate(data), out.spent_seconds
            )

    seen: list[str] = []

    def leaf(root: Path, name: str, run_id: str, bar: float) -> bool:
        del root, name, bar
        seen.append(run_id)
        return True

    f = WithRuns()
    monkeypatch.setattr(engine, "run_probe", f)
    monkeypatch.setattr(engine, "native_leaf", leaf)
    define_workload(repo, "py")
    find(repo, [make_part()], no_tests=True)
    assert f.keys == ["py", "py@native"]
    assert seen == ["r1"]
    f.calls.clear()
    (repo / "x").write_text("1")
    js = make_part(language="javascript", test=None)
    find(repo, [js], no_tests=True)
    assert f.keys == ["py"]


def test_no_native_recorder_at_light(repo: Path, fake: Fake) -> None:
    define_workload(repo, "py")
    find(repo, [make_part(native=(NATIVE,))], "light")
    assert fake.keys == ["py"]


def test_test_command_fills_in_a_missing_one_and_still_needs_confirming(
    repo: Path, fake: Fake
) -> None:
    part = make_part(test=None)
    argv = ("npm", "run", "test:unit")
    run = find(repo, [part], test_commands={"root": argv})
    assert fake.calls == []
    assert run.result.question is not None
    assert "npm run test:unit" in run.result.question
    assert load_test_commands(repo) == {"root": argv}
    find(repo, [part], confirm_tests=("root",))
    assert fake.keys == ["find-tests-root"]
    assert fake.calls[0].spec.command == argv


def test_the_stored_test_command_holds_in_later_calls_and_can_change(
    repo: Path, fake: Fake
) -> None:
    part = make_part(test=None)
    find(repo, [part], test_commands={"root": ("a", "b")})
    find(repo, [part], confirm_tests=("root",))
    fake.calls.clear()
    again = find(repo, [part])
    assert fake.calls == []
    assert again.result.question is None
    changed = find(repo, [part], test_commands={"root": ("a", "c")})
    assert fake.calls == []
    assert changed.result.question is not None
    assert "a c" in changed.result.question


def test_test_command_for_an_unknown_part_is_refused(
    repo: Path, fake: Fake
) -> None:
    with pytest.raises(CommandError) as raised:
        find(repo, [make_part()], test_commands={"nope": ("x",)})
    assert raised.value.code == "unknown_part"


def test_test_command_argument_parsing(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for bad in ("root", "root=oops", "root=[]", "root=[1]", '=["a"]'):
        code = main(
            ["find", "--test-command", bad, "--repo", str(repo), "--json"]
        )
        assert code == 2, bad
        assert json.loads(capsys.readouterr().out)["error"]["code"] == "usage"
