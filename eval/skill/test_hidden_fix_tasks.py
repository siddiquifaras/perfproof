"""Hidden-fix tasks: the skill's window, and no planted files.

Round 9 run 1 found three faults in the tasks rather than the skill: a
window of five where the skill says to read fifteen rows, task-made
workloads sitting in the agent's tree, and two wins too small to see.
"""

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import realrepo
import reporting
import runner
import sandbox
import scoring
import taskset
import transcript
from taskset import SKILL_READ_ROWS, Hotspot, Task

HERE = Path(__file__).resolve().parent
SKILL = HERE.parents[1] / "skills" / "perfproof" / "SKILL.md"
TASK_FILES = sorted(HERE.glob("tasks*.json"))


def _all_tasks() -> list[Task]:
    return [t for f in TASK_FILES for t in taskset.load(f).tasks]


def _hidden_fix() -> list[Task]:
    return [t for t in _all_tasks() if t.is_hidden_fix]


def test_skill_read_rows_matches_the_skill() -> None:
    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    counts = re.findall(r"Print the top (\d+)", text)
    assert len(counts) >= 2
    assert {int(n) for n in counts} == {SKILL_READ_ROWS}


def test_skill_window_is_the_constant() -> None:
    hot = Hotspot.model_validate(
        {"file": "a.py", "function": "f", "top_n": "skill"}
    )
    assert hot.top_n == SKILL_READ_ROWS
    assert Hotspot(file="a.py", function="f").top_n == 1


def test_every_real_hidden_fix_task_uses_the_skill_window() -> None:
    tasks = [
        t
        for t in _hidden_fix()
        if t.repo is not None and t.expect.hotspot is not None
    ]
    assert len(tasks) >= 9
    for task in tasks:
        assert task.expect.hotspot is not None
        assert task.expect.hotspot.top_n == SKILL_READ_ROWS, task.id


def _frames(names: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "function": n,
            "file": "pkg/mod.py" if n == "wanted" else "pkg/other.py",
            "line": i + 1,
            "self_fraction": 0.0,
            "total_fraction": round(0.9 - i * 0.01, 3),
        }
        for i, n in enumerate(names)
    ]


def _score_at_rank(
    rank: int, printed_rows: int = SKILL_READ_ROWS
) -> tuple[bool | None, str]:
    names = [f"other_{i}" for i in range(1, 30)]
    names[rank - 1] = "wanted"
    rows = _frames(names)[:printed_rows]
    printed = "frames_by_total:\n" + "\n".join(
        f"{r['function']} {r['file']} {r['line']} "
        f"{r['self_fraction']} {r['total_fraction']}"
        for r in rows
    )
    use = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "perfproof profile w --json"},
                }
            ]
        },
    }
    out = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": printed,
                    "is_error": False,
                }
            ]
        },
    }
    say = {
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "`wanted` is the cost."}]
        },
    }
    task = taskset.load(HERE / "tasks-real.json").tasks[0]
    hot = Hotspot(file="pkg/mod.py", function="wanted", top_n=SKILL_READ_ROWS)
    expect = task.expect.model_copy(update={"hotspot": hot})
    task = task.model_copy(update={"expect": expect})
    return scoring.hotspot_check(
        task, [transcript.Turn([use, out, say], None)]
    )


@pytest.mark.parametrize("rank", [6, 11, 14, SKILL_READ_ROWS])
def test_a_function_inside_the_rows_the_skill_reads_is_found(
    rank: int,
) -> None:
    found, why = _score_at_rank(rank)
    assert found is True, why


def test_a_function_below_the_rows_the_skill_reads_is_still_missed() -> None:
    found, _ = _score_at_rank(SKILL_READ_ROWS + 1, printed_rows=20)
    assert found is False


def test_the_naming_check_still_applies_in_the_wide_window() -> None:
    found, why = _score_at_rank(12)
    assert found is True
    assert "named" in why


# Planted workloads


def _hidden_specs() -> list[tuple[Task, realrepo.RepoSpec]]:
    return [
        (t, realrepo.load_spec(t.repo.manifest))
        for t in _hidden_fix()
        if t.repo is not None
    ]


def test_each_workload_step_of_a_hidden_fix_task_names_what_it_plants() -> (
    None
):
    for task, spec in _hidden_specs():
        assert task.repo is not None
        touches = [c for c in spec.setup if str(realrepo.WORKLOADS) in c]
        if touches:
            assert spec.planted, task.id
        for name in spec.planted:
            made_by = [
                *spec.setup,
                *(
                    f.read_text(encoding="utf-8")
                    for f in (realrepo.WORKLOADS / task.repo.manifest).glob(
                        "*"
                    )
                    if f.is_file() and f.suffix in {".py", ".sh"}
                ),
            ]
            assert any(name in c for c in made_by), (task.id, name)


def test_the_known_planted_files_are_listed() -> None:
    planted = {
        t.repo.manifest: realrepo.load_spec(t.repo.manifest).planted
        for t in _hidden_fix()
        if t.repo is not None
    }
    assert "eval_bench.js" in planted["fastify-fastify-pr7056"]
    assert "eval_servehttp_bench_test.go" in planted["labstack-echo-pr1799"]
    assert "eval-wl" in planted["fasterxml-jackson-core-pr1674"]


def _spec_planting(*names: str) -> realrepo.RepoSpec:
    steps = [f"python3 -c \"open('{n}','w').write('x')\"" for n in names]
    steps.append("python3 -c \"open('built.bin','w').write('x')\"")
    steps.append(
        "python3 -c \"import os; os.makedirs('wl/sub'); "
        "open('wl/sub/a','w').write('x')\""
    )
    return realrepo.RepoSpec(
        repository="o/r",
        task_commit="a",
        reference_commit="b",
        reference_number="1",
        reference_title="t",
        setup=steps,
        env={},
        oracle=None,
        planted=[*names, "wl"],
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "work" / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, timeout=30)
    return repo


def test_a_hidden_fix_tree_has_no_planted_file_and_no_trace(
    tmp_path: Path,
) -> None:
    spec = _spec_planting("eval_bench.js")
    repo = _repo(tmp_path)
    realrepo.run_setup(spec, repo, [], hide_planted=True)
    assert not (repo / "eval_bench.js").exists()
    assert not (repo / "wl").exists()
    assert (repo / "built.bin").exists()
    exclude = (repo / ".git" / "info" / "exclude").read_text()
    assert "eval_bench" not in exclude
    assert "wl" not in exclude.split()
    assert "/built.bin" in exclude


def test_a_task_that_hands_over_a_workload_keeps_its_files(
    tmp_path: Path,
) -> None:
    spec = _spec_planting("eval_bench.js")
    repo = _repo(tmp_path)
    realrepo.run_setup(spec, repo, [])
    assert (repo / "eval_bench.js").exists()
    assert (repo / "wl" / "sub" / "a").exists()


def test_a_planted_path_outside_the_repo_is_refused(tmp_path: Path) -> None:
    spec = _spec_planting("x").model_copy(update={"planted": ["../escape"]})
    repo = _repo(tmp_path)
    with pytest.raises(realrepo.SetupError, match="outside"):
        realrepo.remove_planted(spec, repo)


def test_only_hidden_fix_tasks_hide_what_they_plant() -> None:
    by_id = {t.id: t for t in _all_tasks()}
    assert by_id["real-fastify-hidden-fix"].is_hidden_fix
    assert not by_id["real-fastify-known-change"].is_hidden_fix
    assert not by_id["real-rich-nothing-to-keep"].is_hidden_fix
    assert not by_id["real-arrow-known-change"].is_hidden_fix


# Tasks left out of the pass bar


def test_two_micro_win_tasks_are_excluded_with_reason_and_evidence() -> None:
    excluded = {t.id: t.bar_exclusion for t in _all_tasks() if t.bar_exclusion}
    assert set(excluded) == {"real-django-hidden-fix", "ruby-rails-find"}
    for ex in excluded.values():
        assert ex is not None
        assert ex.reason
        assert ex.evidence


# Prompt variants

STYLES = {"bare", "vague", "rambling", "claim"}


def _variants() -> list[Task]:
    return [t for t in _all_tasks() if "-msg-" in t.id]


def _base_of(variant: Task) -> Task:
    same = [
        t
        for t in _all_tasks()
        if "-msg-" not in t.id
        and t.expect == variant.expect
        and t.example == variant.example
        and t.repo == variant.repo
    ]
    assert len(same) == 1, variant.id
    return same[0]


def test_prompt_variants_change_only_the_prompt() -> None:
    variants = _variants()
    assert 6 <= len(variants) <= 8
    for v in variants:
        base = _base_of(v)
        assert v.prompt != base.prompt, v.id
        for field in (
            "kind",
            "followups",
            "locked",
            "budget_usd",
            "task_budget_usd",
            "timeout_s",
            "max_turns",
            "branches",
            "expect",
            "repo",
            "example",
        ):
            assert getattr(v, field) == getattr(base, field), (v.id, field)


def test_prompt_variants_cover_the_four_styles_in_each_set() -> None:
    def styles(prefix: str) -> set[str]:
        return {
            t.id.rsplit("-", 1)[-1]
            for t in _variants()
            if t.id.startswith(prefix)
        }

    assert styles("find-") == STYLES
    assert styles("real-") == STYLES


def test_a_variant_prompt_never_names_the_hidden_function() -> None:
    for v in _variants():
        hot = v.expect.hotspot
        assert hot is not None
        names = [e.function for e in hot.accepted()]
        for name in names:
            for part in {name, re.split(r"[.:#]+", name)[-1]}:
                assert part.lower() not in v.prompt.lower(), (v.id, part)


# Reporting


def _score(
    task_id: str,
    *,
    passed: bool,
    bar_excluded: str | None = None,
    wrong_keep: bool = False,
) -> scoring.TaskScore:
    return scoring.TaskScore(
        task_id=task_id,
        kind="find",
        trigger=None,
        trigger_ok=None,
        verdict=None,
        verdict_source="none",
        verdict_ok=None,
        wrong_keep=wrong_keep,
        result_line=None,
        result_line_ok=None,
        hotspot_named=None,
        hotspot_detail="",
        fix_ok=None,
        fix_detail="",
        tree_ok=None,
        tree_detail="",
        wording=[],
        cost_usd=1.0,
        api_seconds=1.0,
        wall_seconds=1.0,
        agent_turns=1,
        calls=1,
        load_start=0.0,
        passed=passed,
        bar_excluded=bar_excluded,
    )


def test_an_excluded_task_is_scored_but_not_in_the_bar() -> None:
    summary = reporting.summarize(
        [
            _score("a", passed=True),
            _score("b", passed=False),
            _score("c", passed=False, bar_excluded="too small"),
            _score("d-msg-bare", passed=True),
        ]
    )
    assert (summary.tasks, summary.passed) == (3, 2)
    assert (summary.excluded, summary.excluded_passed) == (1, 0)
    assert summary.message_variants == [1, 1]
    assert summary.cost_usd == 4.0


def test_an_excluded_task_still_counts_its_wrong_keep() -> None:
    summary = reporting.summarize(
        [_score("c", passed=False, bar_excluded="x", wrong_keep=True)]
    )
    assert summary.wrong_keeps == 1
    assert summary.tasks == 0


def test_the_runner_hides_planted_files_for_hidden_fix_tasks_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, bool] = {}
    spec = realrepo.RepoSpec(
        repository="o/r",
        task_commit="a",
        reference_commit="b",
        reference_number="",
        reference_title="",
        setup=[],
        env={},
        oracle=None,
    )

    def seed(_s: Any, _r: Any, dest: Path, _c: Any) -> dict[str, str]:
        (dest / ".git").mkdir(parents=True)
        return {}

    def setup(*_: Any, **kwargs: Any) -> None:
        seen["hide"] = bool(kwargs.get("hide_planted"))

    monkeypatch.setattr(realrepo, "seed_repo", seed)
    monkeypatch.setattr(realrepo, "run_setup", setup)
    by_id = {t.id: t for t in _all_tasks()}
    expected = {
        "real-fastify-hidden-fix": True,
        "real-fastify-known-change": False,
        "real-rich-nothing-to-keep": False,
        "real-arrow-known-change": False,
    }
    for task_id, hide in expected.items():
        runner.build_real_repo(
            by_id[task_id],
            spec,
            tmp_path / task_id,
            runner.SKILL_SOURCE,
            tmp_path,
            [],
        )
        assert seen["hide"] is hide, task_id


# The eval user's .NET tools directory


def test_dotnet_tools_are_on_the_path_and_readable_for_the_eval_user_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    dotnet = home / ".local" / "dotnet" / "dotnet"
    dotnet.parent.mkdir(parents=True)
    shutil.copy("/bin/ls", dotnet)
    tools = home / ".dotnet" / "tools"
    tools.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    net = taskset.RealRepo(manifest="x", requires=["dotnet"])
    monkeypatch.delenv(sandbox.TOKEN_FILE_ENV, raising=False)
    assert realrepo.toolchain_dirs(net) == [dotnet.parent]
    text = sandbox.profile(home, tmp_path / "root", allow_docker=False)
    assert str(tools) not in text
    monkeypatch.setenv(sandbox.TOKEN_FILE_ENV, "/x/token")
    assert realrepo.toolchain_dirs(net) == [dotnet.parent, tools]
    text = sandbox.profile(home, tmp_path / "root", allow_docker=False)
    assert str(tools) in text
