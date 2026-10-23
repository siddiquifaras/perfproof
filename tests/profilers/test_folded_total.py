"""The total (inclusive) ranking of `folded.rank`."""

import re
from pathlib import Path

import pytest

from perfproof.profilers.folded import rank, rank_file, summarise_file

FIXTURES = Path(__file__).parent.parent / "fixtures"
PYSPY_NATIVE = FIXTURES / "py-spy" / "workload-native.txt"
PYSPY = FIXTURES / "py-spy" / "workload.txt"
ECHO = FIXTURES / "go-echo" / "benchmark.folded.txt"
SAMPLE = FIXTURES / "macos-sample" / "workload.folded.txt"
CONTAINER_ROOT = Path("/srv/project")
GO_ROOT = Path("/src")
EVERYTHING = 1_000_000
PSEUDO = re.compile(r"<[^<>\s]+>")
LINE = re.compile(r":[0-9]+\)$")
ALL = (
    (PYSPY, CONTAINER_ROOT),
    (PYSPY_NATIVE, CONTAINER_ROOT),
    (ECHO, GO_ROOT),
    (SAMPLE, CONTAINER_ROOT),
)


@pytest.mark.parametrize(("path", "root"), ALL, ids=lambda p: str(p.name))
def test_total_ranking_matches_a_naive_recount(path: Path, root: Path) -> None:
    """Totals, order and exclusions agree with a raw-text recount."""
    totals: dict[str, int] = {}
    samples = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stack, _, count = line.rpartition(" ")
        samples += int(count)
        if not stack:
            continue
        for frame in {LINE.sub(")", f) for f in stack.split(";")}:
            totals[frame] = totals.get(frame, 0) + int(count)
    ranking = rank_file(path, project_root=root, top_n=EVERYTHING)
    listed = [h.total_fraction or 0.0 for h in ranking.by_total]
    assert listed == sorted(listed, reverse=True)
    assert not any(
        PSEUDO.fullmatch(h.function) or (h.file or "").startswith("<")
        for h in ranking.by_total
    )
    naive = sorted(
        (
            n / samples
            for text, n in totals.items()
            if n < samples and not text.startswith("<")
        ),
        reverse=True,
    )
    partial = [fraction for fraction in listed if fraction < 1]
    assert partial[:5] == pytest.approx(naive[:5])


@pytest.mark.parametrize(("path", "root"), ALL, ids=lambda p: str(p.name))
def test_by_self_is_what_summarise_returns(path: Path, root: Path) -> None:
    ranking = rank_file(path, project_root=root, top_n=7)
    assert ranking.by_self == summarise_file(path, project_root=root, top_n=7)


@pytest.mark.parametrize(("path", "root"), ALL, ids=lambda p: str(p.name))
def test_top_n_is_a_prefix_of_the_full_total_ranking(
    path: Path, root: Path
) -> None:
    full = rank_file(path, project_root=root, top_n=EVERYTHING).by_total
    for n in (1, 3, 10):
        got = rank_file(path, project_root=root, top_n=n).by_total
        assert got == full[:n]


def test_go_frame_with_no_self_time_ranks_first_and_is_project_code() -> None:
    """``ServeHTTP`` does no work itself; its callees take it all."""
    ranking = rank_file(ECHO, project_root=GO_ROOT, top_n=EVERYTHING)
    serve = ranking.by_total[0]
    assert serve.function.endswith("(*Echo).ServeHTTP")
    assert serve.file == "echo.go"
    assert serve.self_fraction == 0
    assert serve.total_fraction is not None
    assert serve.total_fraction > 0.9
    assert not serve.is_library
    assert all(
        not h.function.endswith("(*Echo).ServeHTTP") for h in ranking.by_self
    )
    callee = ranking.by_total[1]
    assert callee.function == "net/url.(*URL).EscapedPath"
    assert callee.is_library


def test_entry_frames_on_every_stack_are_left_out() -> None:
    """Go's ``testing`` harness and the benchmark are on every stack."""
    ranking = rank_file(ECHO, project_root=GO_ROOT, top_n=EVERYTHING)
    names = [h.function for h in ranking.by_total]
    assert not any(n.startswith("testing.") for n in names)
    assert not any(n.endswith(".BenchmarkEvalServeHTTPPath") for n in names)


def test_python_frame_with_no_self_time_is_listed_by_total_only() -> None:
    """``vector_work`` only calls; it is never in the self list."""
    ranking = rank_file(
        PYSPY_NATIVE, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    assert not any(h.function == "vector_work" for h in ranking.by_self)
    work = next(h for h in ranking.by_total if h.function == "vector_work")
    assert work.self_fraction == 0
    assert work.total_fraction is not None
    assert work.total_fraction > 0.1
    assert not work.is_library


def test_synthetic_frames_are_in_self_but_not_total() -> None:
    """``<genexpr>`` burns the time; it is not a function to change."""
    ranking = rank_file(
        PYSPY_NATIVE, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    assert any(h.function == "<genexpr>" for h in ranking.by_self)
    assert not any(h.function == "<genexpr>" for h in ranking.by_total)


def test_pseudo_file_frames_are_left_out() -> None:
    lines = [
        "MainThread (<thread>);run (/srv/project/a.py:3);f (<frozen x>:9) 4",
        "MainThread (<thread>);run (/srv/project/a.py:3);"
        "g (/srv/project/a.py:7) 4",
        "MainThread (<thread>);other (/srv/project/a.py:1) 2",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    assert [h.function for h in ranking.by_total] == ["run", "g", "other"]


def test_entry_frame_is_one_on_every_sample_not_every_stack() -> None:
    """With empty-stack samples, ``main`` is on 80% and is listed."""
    lines = [
        "main (/srv/project/a.py:1);work (/srv/project/a.py:2) 6",
        "main (/srv/project/a.py:1);idle (/srv/project/a.py:5) 2",
        " 2",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    assert [h.function for h in ranking.by_total] == ["main", "work", "idle"]
    assert ranking.by_total[0].total_fraction == pytest.approx(0.8)


def test_chain_of_single_callers_on_every_sample_is_left_out() -> None:
    """Only the first frame where the time divides is kept."""
    lines = [
        "a (/srv/project/a.py:1);b (/srv/project/a.py:2);"
        "c (/srv/project/a.py:3);x (/srv/project/a.py:4) 6",
        "a (/srv/project/a.py:1);b (/srv/project/a.py:2);"
        "c (/srv/project/a.py:3);y (/srv/project/a.py:5) 4",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    assert [h.function for h in ranking.by_total] == ["c", "x", "y"]
    assert ranking.by_total[0].total_fraction == pytest.approx(1.0)


def test_frame_that_ends_a_stack_is_not_an_entry_frame() -> None:
    lines = [
        "a (/srv/project/a.py:1);b (/srv/project/a.py:2) 6",
        "a (/srv/project/a.py:1) 4",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    assert [h.function for h in ranking.by_total] == ["a", "b"]


def test_recursion_counts_a_stack_once_in_the_total_ranking() -> None:
    lines = [
        "main (/srv/project/a.py:1);r (/srv/project/a.py:2);"
        "r (/srv/project/a.py:2) 5",
        "main (/srv/project/a.py:1);q (/srv/project/a.py:9) 5",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    r = next(h for h in ranking.by_total if h.function == "r")
    assert r.total_fraction == pytest.approx(0.5)
    assert r.self_fraction == pytest.approx(0.5)


def test_a_profile_of_one_stack_lists_only_its_last_frame() -> None:
    ranking = rank(
        ["main (/srv/project/a.py:1);f (/srv/project/a.py:2) 3"],
        project_root=CONTAINER_ROOT,
    )
    assert [h.function for h in ranking.by_total] == ["f"]


def test_a_function_calling_from_several_lines_is_one_entry() -> None:
    """Inclusive time is summed over a function's lines, not split."""
    lines = [
        "main (/srv/project/a.py:10);work (/srv/project/a.py:3);"
        "x (/srv/project/a.py:20) 30",
        "main (/srv/project/a.py:11);work (/srv/project/a.py:3);"
        "y (/srv/project/a.py:21) 30",
        "main (/srv/project/a.py:12);work (/srv/project/a.py:3);"
        "z (/srv/project/a.py:22) 40",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    assert [h.function for h in ranking.by_total] == ["work", "z", "x", "y"]


def test_branching_entry_function_called_from_many_lines_is_listed() -> None:
    lines = [
        "main (/srv/project/a.py:10);w (/srv/project/a.py:3) 30",
        "main (/srv/project/a.py:11);v (/srv/project/a.py:4) 30",
        "main (/srv/project/a.py:12);w (/srv/project/a.py:3) 40",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    main = ranking.by_total[0]
    assert main.function == "main"
    assert main.total_fraction == pytest.approx(1.0)
    assert main.line == 12


def test_function_on_several_lines_shows_the_busiest_line() -> None:
    lines = [
        "main (/srv/project/a.py:1);f (/srv/project/a.py:5);"
        "g (/srv/project/a.py:9) 2",
        "main (/srv/project/a.py:1);f (/srv/project/a.py:6);"
        "g (/srv/project/a.py:9) 3",
        "main (/srv/project/a.py:1);h (/srv/project/a.py:7) 5",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    f = next(h for h in ranking.by_total if h.function == "f")
    assert (f.line, f.total_fraction) == (6, pytest.approx(0.5))
    assert f.self_fraction == 0


def test_go_function_total_is_the_sum_over_its_call_lines() -> None:
    ranking = rank_file(ECHO, project_root=GO_ROOT, top_n=EVERYTHING)
    serve = ranking.by_total[0]
    assert serve.function.endswith("(*Echo).ServeHTTP")
    assert serve.total_fraction == pytest.approx(1.0)
    assert serve.line == 632


def test_rust_trait_impl_symbols_are_not_synthetic() -> None:
    lines = [
        "main;app::run;<alloc::vec::Vec<T> as core::ops::drop::Drop>::drop 70",
        "main;app::run;other 30",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    assert [h.function for h in ranking.by_total] == [
        "app::run",
        "<alloc::vec::Vec<T> as core::ops::drop::Drop>::drop",
        "other",
    ]


def test_mutually_recursive_frames_are_not_each_others_wrappers() -> None:
    lines = [
        "main (/srv/project/a.py:1);visit (/srv/project/a.py:10);"
        "accept (/srv/project/a.py:20);visit (/srv/project/a.py:10);"
        "accept (/srv/project/a.py:20);leaf (/srv/project/a.py:30) 5",
        "main (/srv/project/a.py:1);visit (/srv/project/a.py:10);"
        "accept (/srv/project/a.py:20);visit (/srv/project/a.py:10) 5",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    visit = next(h for h in ranking.by_total if h.function == "visit")
    assert visit.self_fraction == pytest.approx(0.5)
    assert visit.total_fraction == pytest.approx(1.0)
    assert "leaf" in [h.function for h in ranking.by_total]


def test_a_stack_that_is_all_recursion_keeps_its_last_frame() -> None:
    lines = [
        "A (/srv/project/a.py:1);B (/srv/project/a.py:2);"
        "A (/srv/project/a.py:1);B (/srv/project/a.py:2) 10"
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    assert [h.function for h in ranking.by_total] == ["B"]


def test_a_shared_frame_with_self_time_is_kept() -> None:
    lines = [
        "main (/srv/project/a.py:1);f (/srv/project/a.py:2);"
        "g (/srv/project/a.py:3);f (/srv/project/a.py:2) 5",
        "main (/srv/project/a.py:1);f (/srv/project/a.py:2);"
        "g (/srv/project/a.py:3);h (/srv/project/a.py:4) 5",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    names = [h.function for h in ranking.by_total]
    assert "f" in names
    assert "main" not in names


def test_a_line_with_no_samples_does_not_shorten_the_entry_chain() -> None:
    lines = [
        "main (/srv/project/a.py:1);A (/srv/project/a.py:2);"
        "B (/srv/project/a.py:3) 4",
        "main (/srv/project/a.py:1);A (/srv/project/a.py:2);"
        "C (/srv/project/a.py:4) 0",
    ]
    ranking = rank(lines, project_root=CONTAINER_ROOT, top_n=10)
    assert [h.function for h in ranking.by_total] == ["B"]


THREADS_DIR = FIXTURES / "native-threads"
THREADS = THREADS_DIR / "threads.folded.txt"
THREAD_ENTRY = ("thread_start", "_pthread_start")


def test_thread_entry_frames_do_not_head_the_total_ranking() -> None:
    """Frames before the first project frame are not hotspots.

    Every worker's stack starts ``thread_start;_pthread_start``; the
    main thread's does not, so they are no common prefix and used to
    head the list above the project's own functions.
    """
    ranking = rank_file(
        THREADS,
        project_root=THREADS_DIR,
        top_n=EVERYTHING,
        own_modules=["threads"],
    )
    names = [h.function for h in ranking.by_total]
    assert not [n for n in names if n.endswith(THREAD_ENTRY)]
    assert names[:3] == ["worker", "checksum", "process_entry"]
    assert not ranking.by_total[2].is_library


def test_library_frames_below_a_project_frame_are_kept() -> None:
    """Regression guard: the old rule keeps them too; this pins it."""
    ranking = rank_file(
        THREADS,
        project_root=THREADS_DIR,
        top_n=EVERYTHING,
        own_modules=["threads"],
    )
    names = {h.function for h in ranking.by_total}
    assert "libsystem_c.dylib`time" in names
    assert "libsystem_m.dylib`fegetenv" in names


def test_without_a_project_frame_the_common_prefix_rule_applies() -> None:
    """With the project unknown, thread entry frames stay, as before."""
    ranking = rank_file(
        THREADS, project_root=Path("/nowhere"), top_n=EVERYTHING
    )
    names = [h.function for h in ranking.by_total]
    assert all(h.is_library for h in ranking.by_total)
    assert names[:2] == [
        "libsystem_pthread.dylib`thread_start",
        "libsystem_pthread.dylib`_pthread_start",
    ]


def test_go_test_runner_frames_do_not_head_the_total_ranking() -> None:
    """``testing.(*B).launch`` is above the benchmark, not under it.

    Regression guard: the old common-prefix rule also passes."""
    ranking = rank_file(ECHO, project_root=GO_ROOT, top_n=EVERYTHING)
    names = [h.function for h in ranking.by_total]
    assert not [n for n in names if n.startswith("testing.")]
    assert "github.com/labstack/echo/v4.(*Echo).ServeHTTP" in names[:2]


@pytest.mark.parametrize("count", [5, 0])
def test_a_stack_with_no_project_frame_does_not_bring_entry_frames_back(
    count: int,
) -> None:
    """An idle helper thread shares ``thread_start`` with workers."""
    idle = (
        "libsystem_pthread.dylib`thread_start;"
        "libsystem_pthread.dylib`_pthread_start;"
        f"libsystem_kernel.dylib`__workq_kernreturn {count}"
    )
    lines = [*THREADS.read_text(encoding="utf-8").splitlines(), idle]
    ranking = rank(
        lines,
        project_root=THREADS_DIR,
        top_n=EVERYTHING,
        own_modules=["threads"],
    )
    names = [h.function for h in ranking.by_total]
    assert not [n for n in names if n.endswith(THREAD_ENTRY)]
    assert names[:3] == ["worker", "checksum", "process_entry"]


FD = FIXTURES / "rust" / "fd-threads.folded.txt"
RUNTIME = ("fd`std::", "fd`core::", "fd`alloc::", "fd`_$LT$std..")


def test_runtime_frames_in_the_project_binary_are_library_code() -> None:
    """Rust ``std::``/``core::`` copies in ``fd`` are not fd's code."""
    ranking = rank_file(
        FD, project_root=CONTAINER_ROOT, top_n=EVERYTHING, own_modules=["fd"]
    )
    seen = {h.function: h.is_library for h in ranking.by_total}
    seen.update({h.function: h.is_library for h in ranking.by_self})
    runtime = [f for f in seen if f.startswith(RUNTIME)]
    assert runtime
    assert all(seen[f] for f in runtime)
    assert not seen["fd`fd::output::print_entry::ha19644765a9c40d9"]
    assert not seen["fd`fd::walk::scan::h6c5b57f148ddc2ba"]
    names = [h.function for h in ranking.by_total]
    assert not [n for n in names if "thread_start" in n]
    assert names.index("fd`fd::output::print_entry::ha19644765a9c40d9") < 10


TRAMPOLINE = "fd`std::sys::backtrace::__rust_begin_short_backtrace::h"
FD_PRINT_ENTRY = "fd`fd::output::print_entry::ha19644765a9c40d9"


def _fd_total_names() -> list[str]:
    ranking = rank_file(
        FD, project_root=CONTAINER_ROOT, top_n=EVERYTHING, own_modules=["fd"]
    )
    return [h.function for h in ranking.by_total]


def test_trampoline_frames_between_project_frames_are_left_out() -> None:
    """``main -> __rust_begin_short_backtrace -> fd::main`` is not work.

    The frame has no self time and every stack it is in has a project
    frame deeper, but the entry rule kept it because ``fd`main`` is
    above it. It ranked 4th and pushed ``print_entry`` from 8th to 9th.
    """
    names = _fd_total_names()
    assert not [n for n in names if n.startswith(TRAMPOLINE)]
    assert not [n for n in names if n.startswith("fd`std::rt::lang_start")]
    assert names.index(FD_PRINT_ENTRY) + 1 == 8


def test_library_frames_with_self_time_or_a_library_subtree_are_kept() -> None:
    """Only frames that merely call back into the project go."""
    names = _fd_total_names()
    for kept in (
        "libsystem_kernel.dylib`read",
        "libsystem_kernel.dylib`lstat",
        "libsystem_kernel.dylib`write",
        "fd`std::sync::mpmc::Receiver$LT$T$GT$::recv::h551cf746036ed279",
        "fd`std::thread::lifecycle::JoinInner$LT$T$GT$::join::h3306ad4cb9995805",
        "libsystem_pthread.dylib`_pthread_join",
        "fd`std::sys::fs::symlink_metadata::hd0e44a06c5789f14",
    ):
        assert kept in names, kept


def test_project_frames_are_never_left_out_as_trampolines() -> None:
    """Every project frame the profile holds is in the total ranking."""
    ranking = rank_file(
        FD, project_root=CONTAINER_ROOT, top_n=EVERYTHING, own_modules=["fd"]
    )
    listed = {h.function for h in ranking.by_total}
    text = FD.read_text(encoding="utf-8")
    for line in text.splitlines():
        for frame in line.rpartition(" ")[0].split(";"):
            if frame.startswith("fd`fd::") or frame == "fd`main":
                assert frame in listed, frame
