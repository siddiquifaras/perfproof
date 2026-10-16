"""Tests for the miner's text rules and the committed manifests.

Part of the root suite (``uv run pytest``). Every test is offline:
GitHub reads are stubbed. The strings below are written to exercise
each rule, not captured GitHub output: the rules take plain text.
"""

from collections import Counter
from pathlib import Path
from typing import Any

import pytest

import classify
import mine
from mine import GH_PR, benchmark_pr
from task import load_tasks


def _history(
    monkeypatch: pytest.MonkeyPatch,
    commits: dict[str, tuple[list[str], str]],
    heads: list[str],
    diffs: dict[tuple[str, str], set[str]],
) -> dict[str, Any]:
    # Stubs for the three GitHub reads pr_base makes: commit lookups,
    # the pull request's headlines, and compare file lists.
    def info(_repo: str, sha: str) -> dict[str, Any]:
        parents, message = commits[sha]
        return {
            "sha": sha,
            "parents": parents,
            "message": message,
            "html_url": f"u/{sha}",
        }

    def headlines(_repo: str, _number: int) -> list[str]:
        return heads

    def compare(_repo: str, base: str, head: str) -> set[str] | None:
        return diffs.get((base, head))

    monkeypatch.setattr(mine, "commit_info", info)
    monkeypatch.setattr(mine, "_pr_headlines", headlines)
    monkeypatch.setattr(mine, "_compare_files", compare)
    return info("o/r", "M")


PR: dict[str, Any] = {
    "number": 1,
    "files": [{"path": "a.py"}, {"path": "b.py"}],
}


def test_pr_base_squash(monkeypatch: pytest.MonkeyPatch) -> None:
    commits = {"M": (["B"], "Speed up a (#1)")}
    diffs = {("B", "M"): {"a.py", "b.py"}}
    merge = _history(monkeypatch, commits, ["first", "second"], diffs)
    found = mine.pr_base("o/r", PR, merge)
    assert found is not None
    assert found[0] == "B"


def test_pr_base_two_parent_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    commits = {"M": (["B", "C2"], "Merge pull request #1")}
    diffs = {("B", "M"): {"a.py", "b.py"}}
    merge = _history(monkeypatch, commits, ["first", "second"], diffs)
    found = mine.pr_base("o/r", PR, merge)
    assert found is not None
    assert found[0] == "B"


def test_pr_base_rebase_with_shortened_headline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    long = "Introduce a hash result and make the params required in _find"
    commits = {
        "M": (["C1"], "second\n\nbody"),
        "C1": (["B"], long + "_common\n"),
    }
    # The last commit alone touches both files, so a file-set check on
    # the first parent would wrongly accept C1.
    diffs = {("C1", "M"): {"a.py", "b.py"}, ("B", "M"): {"a.py", "b.py"}}
    merge = _history(monkeypatch, commits, [long + "\u2026", "second"], diffs)
    found = mine.pr_base("o/r", PR, merge)
    assert found is not None
    assert found[0] == "B"


def test_pr_base_drops_a_diff_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commits = {"M": (["B"], "Speed up a (#1)")}
    diffs = {("B", "M"): {"a.py"}}
    merge = _history(monkeypatch, commits, ["only"], diffs)
    assert mine.pr_base("o/r", PR, merge) is None


def test_no_pull_request_backs_two_tasks() -> None:
    keys: list[tuple[str, int]] = []
    for task in load_tasks():
        match = GH_PR.search(task.reference_change.url)
        if match is not None:
            keys.append((match.group(1).lower(), int(match.group(2))))
        elif (key := benchmark_pr(task)) is not None:
            keys.append(key)
    assert [k for k, n in Counter(keys).items() if n > 1] == []


def test_perf_title_matches_words_only() -> None:
    assert classify.is_perf_title("PERF: faster groupby")
    assert classify.is_perf_title("Speed up parsing")
    assert not classify.is_perf_title("Fix typo in superfluous docs")


@pytest.mark.parametrize(
    ("title", "perf"),
    [
        ("Vectorize adjacency matrix construction", True),
        ("Optimize Leiden data handling", True),
        ("Skip parse/plan/optimize for BeginTransaction", True),
        ("ENH: Enabled the use of numpy.vectorize as a decorator", False),
        ("Clean _clean_inputs in optimize.linprog", False),
        ("ENH: optimize: add HiGHS methods to linprog", False),
        ("ENH: optimize/minimize: combine hess and hessp", False),
    ],
)
def test_optimize_and_vectorize_as_names(title: str, perf: bool) -> None:
    assert classify.is_perf_title(title) is perf


@pytest.mark.parametrize(
    "body",
    [
        "This makes it 2.5x faster on the benchmark.",
        "About 30% faster for large inputs.",
        "before: 120 ms\nafter: 80 ms (timeit)",
        "n | old | new | speedup\r\n10 | 0.96 | 0.80 | 1.20x\r\n"
        "50 | 1.9 | 1.7 | 1.11x",
    ],
)
def test_numbers_count_as_evidence(body: str) -> None:
    assert classify.evidence(body, []).has_numbers


def test_cpu_model_is_not_a_speedup() -> None:
    ev = classify.evidence("**CPU:** AMD Ryzen 7 5800X", [])
    assert ev.speedups == ()
    assert classify.evidence("50X faster", []).speedups == ("50X faster",)


@pytest.mark.parametrize(
    "body",
    [
        "Tested on 1x GPU: 5.7 GiB peak",
        "Runs on 8x H100 nodes",
        "Uses 4x less memory per request",
    ],
)
def test_multiplier_without_speed_context_is_not_a_speedup(body: str) -> None:
    assert classify.evidence(body, []).speedups == ()


@pytest.mark.parametrize(
    ("title", "non_runtime"),
    [
        ("perf: speed up make tests with xdist", True),
        ("Speed up prepare CI job", True),
        ("fix(tests): slow tests caused by pytest-socket", True),
        ("[Docs] Document latency tradeoffs", True),
        ("Speed up parsing of large test inputs", False),
        ("PERF: faster groupby", False),
    ],
)
def test_non_runtime_titles(title: str, non_runtime: bool) -> None:
    assert classify.is_non_runtime_title(title) is non_runtime


@pytest.mark.parametrize(
    "path",
    [
        "uv.lock",
        "docs/cookbook/Qwen3.mdx",
        "README.md",
        "scripts/ci/run_unit_tests.py",
        "examples/deepseek_v32/topk_selector.py",
        "cookbook/performance/baseline.json",
        "codex-rs/models-manager/models.json",
        ".github/workflows/tests.yml",
    ],
)
def test_non_source_paths(path: str) -> None:
    assert mine.source_files([path]) == []


def test_source_path_kept() -> None:
    assert mine.source_files(["pandas/core/groupby.py"]) == [
        "pandas/core/groupby.py"
    ]


def test_dispatch_versions_ignore_helper_kernel_numbers() -> None:
    source = """
__global__ void softmax_forward_kernel5(float* x) {}
__global__ void attention_query_key_kernel1(float* x) {}
void attention_forward(int kernel_num) {
    switch (kernel_num) {
        case 1:
            attention_forward1(out, inp);
            break;
        case 2: {
            attention_forward2(out, inp);
            break;
        }
        case 3:
            attention_forward1(out, inp, launch_softmax_3);
            break;
        case 4:
            return attention_forward4(out, inp);
//        case 5:
    }
}
"""
    assert mine.dispatch_versions(source) == {"attention_forward": {1, 2, 4}}


def test_spec_decode_is_not_a_test_path() -> None:
    files = [
        "lmdeploy/pytorch/spec_decode/spec_agent.py",
        "tests/pytorch/nn/test_nsa.py",
        "tests/conftest.py",
    ]
    assert classify.test_files(files) == files[1:]
    assert [f for f in files if classify.PYTEST_FILE.search(f)] == [
        "tests/pytorch/nn/test_nsa.py"
    ]


def test_frontend_only_change_is_outside_gpu() -> None:
    files = ["studio/frontend/src/app/low-device-scale.ts"]
    assert not mine.in_class(files, "gpu")
    assert mine.in_class(files, "web")
    assert mine.in_class(["unsloth/kernels/rope.py", *files], "gpu")


def test_large_pull_request_files_are_paged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed = [{"path": f"f{i}.py"} for i in range(100)]
    pages = [
        [{"filename": f"f{i}.py"} for i in range(100)],
        [{"filename": f"f{i}.py"} for i in range(100, 195)],
    ]
    calls: list[list[str]] = []

    def fake(argv: list[str], search: bool = False) -> object:
        calls.append(argv)
        return pages

    monkeypatch.setattr(mine, "gh_json", fake)
    pr = {"url": "https://github.com/o/r/pull/7", "files": listed}
    assert len(mine.pr_files(pr)) == 195
    assert not mine.files_truncated(pr)
    assert calls[0][-1] == "repos/o/r/pulls/7/files?per_page=100"


def test_vllm_bench_needs_a_subcommand() -> None:
    body = (
        "vllm bench deepseek-ai/DeepSeek-V3 --tp 1\n\n"
        "vllm bench serve --model m --dataset-name random"
    )
    assert (
        classify.workload_line(body)
        == "vllm bench serve --model m --dataset-name random"
    )


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_captured_table_under_a_performance_heading() -> None:
    body = (FIXTURES / "lmdeploy-4830-performance.md").read_text()
    assert "0.55x" in classify.evidence(body, []).speedups


def test_captured_tok_per_second_line() -> None:
    body = (FIXTURES / "unsloth-13404-line.md").read_text()
    assert classify.evidence(body, []).speedups == ("1.062x",)


@pytest.mark.parametrize(
    "body",
    [
        "| M | Triton \u00b5s | Gluon \u00b5s | ratio |\n|---|---|---|---|\n"
        "| 1 | 24.3 | 13.4 | 0.55x |",
        "| M | Gluon TFLOP/s | ratio |\n|---|---|---|\n| 1 | 2.5 | 0.55x |",
        "## Results\n\np50 latency per call\n\n| M | ratio |\n|---|---|\n"
        "| 1 | 0.55x |",
        "measured on the Zoo PR: 1.34x",
    ],
)
def test_unit_words_and_lead_paragraph_count(body: str) -> None:
    assert classify.evidence(body, []).speedups != ()


def test_lone_timing_without_bench_words_is_not_evidence() -> None:
    ev = classify.evidence("Timeout raised to 30 s.", [])
    assert not ev.has_numbers
    assert not ev.any


def test_benchmark_file_counts_as_evidence() -> None:
    ev = classify.evidence(None, ["asv_bench/benchmarks/groupby.py"])
    assert ev.bench_files == ("asv_bench/benchmarks/groupby.py",)
    assert ev.any


def test_workload_line_strips_prompt() -> None:
    body = "Results:\n```\n$ python -m timeit -s 'import x' 'x.f()'\n```"
    assert (
        classify.workload_line(body)
        == "python -m timeit -s 'import x' 'x.f()'"
    )
    assert classify.workload_line("no command here") is None


def test_workload_line_joins_continuations() -> None:
    body = "```\npython3 -m timeit \\\n  -s 'import m' \\\n  'm.f()'\n```"
    assert (
        classify.workload_line(body)
        == "python3 -m timeit -s 'import m' 'm.f()'"
    )


def test_workload_line_fragment_is_unknown() -> None:
    assert classify.workload_line("vllm bench serve \\") is None
    assert classify.workload_line("python -m timeit 'unclosed") is None


@pytest.mark.parametrize(
    ("title", "body", "expected"),
    [
        ("Revert x", "Reverts owner/repo#123", ("pr", "123")),
        ("Revert x", "Reverts #45", ("pr", "45")),
        (
            "Revert x",
            "This reverts commit abcdef1234.",
            ("commit", "abcdef1234"),
        ),
        ('Revert "perf: thing (#77)"', "", ("pr", "77")),
        ("Revert x", "Reverts other/repo#9", None),
    ],
)
def test_reverted_ref(
    title: str, body: str, expected: tuple[str, str] | None
) -> None:
    assert classify.reverted_ref("owner/repo", title, body) == expected


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Revert: turned out slower on CI machines", "reverted-no-gain"),
        ("Revert: this causes a segfault on ARM", "reverted-breaking"),
        (
            "Revert: it breaks the wheel build on musl",
            "reverted-unknown-reason",
        ),
        ("Revert: pylint import errors on main", "reverted-unknown-reason"),
        ("Revert This breaks tutorials", "reverted-unknown-reason"),
        (
            "This broke multi-column lookups on Oracle. See the Oracle CI.",
            "reverted-breaking",
        ),
        ("Revert: slower and also crashes", "reverted-unknown-reason"),
        ("Revert", "reverted-unknown-reason"),
    ],
)
def test_revert_reason(text: str, kind: str) -> None:
    assert classify.revert_reason(text)[0] == kind


def test_revert_text_drops_the_reverted_title() -> None:
    text = classify.revert_text(
        'Revert "BUG/PERF: Series.replace with dtype=category"',
        "Reverts pandas-dev/pandas#49404\n\nIt made replace slower.",
    )
    assert "BUG" not in text
    assert classify.revert_reason(text)[0] == "reverted-no-gain"


def test_revert_text_keeps_the_reason_on_the_reverts_line() -> None:
    text = classify.revert_text(
        "Revert x", "Reverts #12 because it was slower"
    )
    assert classify.revert_reason(text)[0] == "reverted-no-gain"


@pytest.mark.parametrize(
    ("body", "num"),
    [
        ("Fixes a slowdown introduced in #321.", "321"),
        ("Fixes the regression caused by #77", "77"),
        ("perf regression from #5 in the parser", "5"),
        ("Removes the thread-safety properties introduced in #53890", None),
        ("A low-latency optimization introduced by #29354", None),
        ("Fixes a slowdown.", None),
    ],
)
def test_culprit_pr(body: str, num: str | None) -> None:
    found = classify.culprit_pr(body)
    assert (found[0] if found else None) == num


def test_committed_manifests_are_consistent() -> None:
    tasks = load_tasks()
    ids = Counter(t.id for t in tasks)
    assert [i for i, n in ids.items() if n > 1] == []
    for task in tasks:
        assert task.split == "development-candidate"
        if task.status == "candidate":
            assert not task.runs, task.id
        else:
            assert task.runs, task.id
        if task.status == "runs":
            assert task.workload.verified, task.id
            assert task.oracle.verified, task.id
        if task.family == "published-benchmark":
            assert task.audit is not None, task.id
