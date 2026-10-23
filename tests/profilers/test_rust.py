"""The Rust profiler bridge: ``sample`` and ``perf script`` output.

The fixtures are the tools' own output for ``fd`` (see
``tests/fixtures/rust/SOURCE.md``): ``sample-fd.txt`` from macOS
``sample`` and ``perf-script-fd.txt`` from ``perf script`` in a Linux
container.
"""

import io
import itertools
import re
import shutil
import subprocess
import sys
import tracemalloc
from collections.abc import Iterator
from pathlib import Path

import pytest

from perfproof.profilers.folded import summarise
from perfproof.profilers.rust import (
    IDLE_LEAVES,
    ProjectCrates,
    SampleFormatError,
    binary_names,
    crate_of,
    demangle,
    fold_perf_script,
    fold_sample_text,
    implementing_crates,
    main,
    record,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "rust"
SAMPLE = FIXTURES / "sample-fd.txt"
PERF = FIXTURES / "perf-script-fd.txt"
CRATES = {"fd"}

HOT = """\
use std::hint::black_box;

#[inline(never)]
fn spin_hash(mut x: u64) -> u64 {
    for _ in 0..800_000_000u64 {
        x = x.wrapping_mul(6364136223846793005).wrapping_add(1);
    }
    x
}

fn main() {
    println!("{}", black_box(spin_hash(black_box(7))));
}
"""


def thread_counts(path: Path) -> int:
    """Sum of the thread headers of a ``sample`` report, read apart."""
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"    ([0-9]+) Thread_.*", line)
        if match:
            total += int(match.group(1))
    return total


def fold_sample(
    keep_idle: bool = False, crates: set[str] | None = None
) -> tuple[list[str], int, int]:
    out = io.StringIO()
    stats = fold_sample_text(
        SAMPLE.read_text(encoding="utf-8").splitlines(),
        "fd",
        CRATES if crates is None else crates,
        out,
        keep_idle=keep_idle,
    )
    return out.getvalue().splitlines(), stats.kept, stats.idle


def fold_perf() -> tuple[list[str], int]:
    out = io.StringIO()
    stats = fold_perf_script(
        PERF.read_text(encoding="utf-8").splitlines(), "fd", CRATES, out
    )
    return out.getvalue().splitlines(), stats.kept


def count_of(line: str) -> int:
    return int(line.rpartition(" ")[2])


def test_every_sample_is_kept_or_counted_as_idle() -> None:
    lines, kept, idle = fold_sample()

    assert kept + idle == thread_counts(SAMPLE)
    assert sum(count_of(line) for line in lines) == kept
    assert idle > 0


def test_parked_threads_are_dropped_and_working_ones_are_not() -> None:
    lines, _, _ = fold_sample()

    leaves = {
        line.rpartition(" ")[0].rpartition(";")[2].partition("`")[2]
        for line in lines
    }
    assert not leaves & IDLE_LEAVES
    # The signal-handler thread only ever waits in read(); the worker
    # that stats files reads nothing, so no read() survives.
    assert "read" not in leaves
    assert "lstat" in leaves


def test_keep_idle_keeps_every_sample() -> None:
    lines, kept, idle = fold_sample(keep_idle=True)

    assert (kept, idle) == (thread_counts(SAMPLE), 0)
    assert sum(count_of(line) for line in lines) == kept
    assert any("`__ulock_wait " in line for line in lines)


def test_stacks_are_root_first_without_the_thread() -> None:
    lines, _, _ = fold_sample()

    stack = next(line for line in lines if "fd::output::print_entry" in line)
    frames = stack.rpartition(" ")[0].split(";")
    assert frames[0] == "libsystem_pthread.dylib`thread_start"
    assert frames.index("fd`fd::output::print_entry") > frames.index(
        "libsystem_pthread.dylib`_pthread_start"
    )
    assert not any("Thread_" in frame for frame in frames)


def test_project_crates_are_the_binarys_and_everything_else_is_deps() -> None:
    lines, _, _ = fold_sample()

    text = "\n".join(lines)
    assert "fd`fd::output::print_entry" in text
    assert "fd.deps`std::sys::fs::symlink_metadata" in text
    assert (
        "fd.deps`<std::io::stdio::StdoutLock as std::io::Write>::write_all"
        in text
    )
    assert "libsystem_kernel.dylib`lstat" in text
    assert "fd`std::" not in text


def test_without_crate_names_the_whole_binary_is_the_binarys() -> None:
    lines, _, _ = fold_sample(crates=set())

    text = "\n".join(lines)
    assert "fd`std::sys::fs::symlink_metadata" in text
    assert ".deps`" not in text


def test_the_summary_ranks_syscalls_and_marks_library_code(
    tmp_path: Path,
) -> None:
    lines, _, _ = fold_sample()

    hotspots = summarise(
        lines, project_root=tmp_path, top_n=500, own_modules=["fd"]
    )

    by_name = {h.function: h for h in hotspots}
    assert hotspots[0].function == "libsystem_kernel.dylib`lstat"
    assert hotspots[0].is_library
    own = by_name["fd`fd::output::print_entry"]
    assert not own.is_library
    assert own.total_fraction is not None
    assert own.total_fraction > 0.9
    assert by_name["fd.deps`std::sys::fs::symlink_metadata"].is_library


def test_every_symbol_of_both_fixtures_is_decoded() -> None:
    for fixture, lines in ((SAMPLE, fold_sample()[0]), (PERF, fold_perf()[0])):
        text = "\n".join(lines)
        assert not re.search(r"::h[0-9a-f]{16}[;\s]", text), fixture
        assert "$LT$" not in text
        assert "$u7b$" not in text
        assert ".." not in text.replace("...", "")


def test_demangle_decodes_the_legacy_scheme() -> None:
    assert (
        demangle(
            "_$LT$std..io..stdio..StdoutLock$u20$as$u20$std..io..Write$GT$"
            "::write_all::h34cc11c080bf2685"
        )
        == "<std::io::stdio::StdoutLock as std::io::Write>::write_all"
    )
    assert demangle("fd::walk::scan::h6c5b57f148ddc2ba") == "fd::walk::scan"
    assert (
        demangle(
            "core::ops::function::FnOnce::call_once$u7b$$u7b$vtable.shim"
            "$u7d$$u7d$::ha396b8f9b044e434"
        )
        == "core::ops::function::FnOnce::call_once{{vtable.shim}}"
    )
    assert demangle("fd::walk::spawn_senders::_$u7b$$u7b$closure$u7d$$u7d$")


def test_demangle_leaves_c_symbols_and_readable_names_alone() -> None:
    for name in ("lstat", "_platform_memcmp", "DYLD-STUB$$memcpy", "main"):
        assert demangle(name) == name
    assert demangle("a::b::{{closure}}") == "a::b::{{closure}}"


def test_the_crate_of_a_symbol_is_the_type_s_for_a_trait_impl() -> None:
    assert crate_of("fd::walk::scan") == "fd"
    assert crate_of("<fd::output::Foo as core::fmt::Display>::fmt") == "fd"
    assert crate_of("<&mut regex::Foo as other::Tr>::go") == "regex"
    assert crate_of("<*const fd::Bar as x::Y>::z") == "fd"
    assert crate_of("main") is None
    assert crate_of("[unknown]") is None
    assert crate_of("") is None


def test_project_crates_come_from_every_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "my-tool"\n[lib]\nname = "mytool_core"\n'
        '[[bin]]\nname = "mt"\npath = "src/main.rs"\n'
    )
    (tmp_path / "member").mkdir()
    (tmp_path / "member" / "Cargo.toml").write_text(
        '[package]\nname = "helper-crate"\n'
    )
    (tmp_path / "target" / "x").mkdir(parents=True)
    (tmp_path / "target" / "x" / "Cargo.toml").write_text(
        '[package]\nname = "build-output"\n'
    )
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "Cargo.toml").write_text("not = [toml\n")

    assert ProjectCrates(tmp_path).names == {
        "my_tool",
        "mytool_core",
        "mt",
        "helper_crate",
    }


def test_perf_samples_are_counted_once_and_kernel_frames_collapse() -> None:
    lines, kept = fold_perf()

    headers = sum(
        1
        for line in PERF.read_text(encoding="utf-8").splitlines()
        if line and not line[0].isspace()
    )
    assert kept == headers
    assert sum(count_of(line) for line in lines) == kept
    for line in lines:
        frames = line.rpartition(" ")[0].split(";")
        pairs = list(itertools.pairwise(frames))
        assert ("[kernel]`[unknown]", "[kernel]`[unknown]") not in pairs
    assert any("[kernel]`[unknown]" in line for line in lines)


def test_perf_stacks_run_root_first_and_name_the_project_crate(
    tmp_path: Path,
) -> None:
    lines, _ = fold_perf()

    stack = next(line for line in lines if "fd::walk::scan" in line)
    frames = stack.rpartition(" ")[0].split(";")
    assert frames.index("fd`fd::main") < frames.index("fd`fd::walk::scan")
    hotspots = summarise(
        lines, project_root=tmp_path, top_n=500, own_modules=["fd"]
    )
    own = {h.function for h in hotspots if not h.is_library}
    assert "fd`fd::output::print_entry" in own


def test_perf_closures_are_decoded() -> None:
    lines, _ = fold_perf()

    assert any("{{closure}}" in line for line in lines)


def test_a_sample_with_no_frames_counts_and_names_none() -> None:
    out = io.StringIO()

    stats = fold_perf_script(["fd 5 ", "", "fd 5 ", ""], "fd", CRATES, out)

    assert out.getvalue() == " 2\n"
    assert (stats.samples, stats.kept) == (2, 2)


def test_a_perf_frame_without_an_address_is_refused() -> None:
    with pytest.raises(SampleFormatError, match="line 2"):
        fold_perf_script(
            ["fd 5 ", "\tnot a frame", ""], "fd", CRATES, io.StringIO()
        )


def test_a_call_graph_line_with_no_count_is_refused() -> None:
    lines = ["Call graph:", "    + start  (in dyld) + 1  [0x1]"]

    with pytest.raises(SampleFormatError, match="line 2"):
        fold_sample_text(lines, "fd", CRATES, io.StringIO())


def test_a_frame_before_any_thread_is_refused() -> None:
    lines = ["Call graph:", "    + 3 start  (in dyld) + 1  [0x1]"]

    with pytest.raises(SampleFormatError, match="line 2"):
        fold_sample_text(lines, "fd", CRATES, io.StringIO())


def test_a_report_without_a_call_graph_holds_no_samples() -> None:
    out = io.StringIO()

    stats = fold_sample_text(["Process: fd [1]", ""], "fd", CRATES, out)

    assert (stats.samples, stats.kept, out.getvalue()) == (0, 0, "")


def test_a_node_whose_children_outcount_it_is_reported() -> None:
    lines = [
        "Call graph:",
        "    3 Thread_1   x",
        "    + 2 main  (in fd) + 1  [0x1]",
        "    +   5 work  (in fd) + 1  [0x2]",
    ]
    out = io.StringIO()

    stats = fold_sample_text(lines, "fd", CRATES, out)

    assert stats.anomalies == 1
    assert out.getvalue().splitlines() == [
        "fd.deps`main;fd.deps`work 5",
        " 1",
    ]


def test_a_thread_that_waits_then_works_keeps_its_work_only() -> None:
    lines = [
        "Call graph:",
        "    10 Thread_1   x",
        "    + 10 run  (in fd) + 1  [0x1]",
        "    +   4 __ulock_wait  (in libsystem_kernel.dylib) + 8  [0x2]",
        "    +   3 read  (in libsystem_kernel.dylib) + 8  [0x3]",
        "    +   3 compute  (in fd) + 8  [0x4]",
    ]
    out = io.StringIO()

    stats = fold_sample_text(lines, "fd", CRATES, out)

    assert out.getvalue().splitlines() == [
        "fd.deps`run;libsystem_kernel.dylib`read 3",
        "fd.deps`run;fd.deps`compute 3",
    ]
    assert (stats.kept, stats.idle) == (6, 4)


def test_a_thread_that_only_reads_is_dropped_whole() -> None:
    lines = [
        "Call graph:",
        "    7 Thread_1   x",
        "    + 7 run  (in fd) + 1  [0x1]",
        "    +   7 read  (in libsystem_kernel.dylib) + 8  [0x2]",
    ]
    out = io.StringIO()

    stats = fold_sample_text(lines, "fd", CRATES, out)

    assert out.getvalue() == ""
    assert (stats.kept, stats.idle) == (0, 7)


def test_semicolons_in_a_symbol_cannot_split_a_frame() -> None:
    lines = [
        "Call graph:",
        "    2 Thread_1   x",
        "    + 2 <[u8; 4] as fd::T>::f  (in fd) + 1  [0x1]",
    ]
    out = io.StringIO()

    fold_sample_text(lines, "fd", CRATES, out)

    assert out.getvalue() == "fd`<[u8, 4] as fd::T>::f 2\n"


class _Sink(io.StringIO):
    """A sink that keeps the number of samples written, not the text."""

    def __init__(self) -> None:
        super().__init__()
        self.samples = 0

    def write(self, text: str) -> int:
        self.samples += int(text.rpartition(" ")[2])
        return len(text)


def test_sample_memory_is_held_to_a_byte_budget() -> None:
    n = 120_000

    def generate() -> Iterator[str]:
        yield "Call graph:"
        yield f"    {n} Thread_1   x"
        yield f"    + {n} main  (in fd) + 1  [0x1]"
        for i in range(n):
            yield f"    +   1 f{i}  (in fd) + 4  [0x10]"

    sink = _Sink()
    tracemalloc.start()
    try:
        stats = fold_sample_text(
            generate(),
            "fd",
            CRATES,
            sink,
            flush_bytes=1 << 20,
        )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert stats.kept == n
    assert sink.samples == n
    # Holding the 120,000 distinct stacks takes over 15 MiB.
    assert peak < 8 << 20


def test_perf_memory_is_held_to_a_byte_budget() -> None:
    n = 120_000

    def generate() -> Iterator[str]:
        for i in range(n):
            yield "fd 1 "
            yield f"\t    aaaab04d09c4 f{i} (/tmp/fd)"
            yield "\t    aaaab04d09c8 main (/tmp/fd)"
            yield ""

    sink = _Sink()
    tracemalloc.start()
    try:
        stats = fold_perf_script(
            generate(),
            "fd",
            CRATES,
            sink,
            flush_bytes=1 << 20,
        )
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert stats.kept == n
    assert sink.samples == n
    assert peak < 8 << 20


def test_a_tiny_budget_still_writes_every_sample() -> None:
    lines = [
        "Call graph:",
        "    5 Thread_1   x",
        "    + 5 main  (in fd) + 1  [0x1]",
        "    +   2 a  (in fd) + 4  [0x10]",
        "    +   3 b  (in fd) + 4  [0x10]",
    ]
    out = io.StringIO()

    stats = fold_sample_text(lines, "fd", CRATES, out, flush_bytes=1)

    assert stats.kept == 5
    assert sum(count_of(x) for x in out.getvalue().splitlines()) == 5


def test_record_rejects_a_bad_interval(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="interval_ms"):
        record(["x"], tmp_path / "out", interval_ms=0)


def _not_found(command: str, **options: object) -> None:
    return None


def test_record_without_the_profiler_says_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(shutil, "which", _not_found)

    status = record(["x"], tmp_path / "out.folded")

    assert status == 4
    assert "was not found on the PATH" in capsys.readouterr().err


def test_a_raw_report_from_an_earlier_run_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    suffix = ".sample.txt" if sys.platform == "darwin" else ".perf.data"
    (tmp_path / f"out.folded{suffix}").write_bytes(b"")

    status = record(["x"], tmp_path / "out.folded")

    assert status == 7
    assert "earlier run" in capsys.readouterr().err


def test_main_needs_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["record", "--output", "x"])

    assert raised.value.code == 2
    assert "give the command after --" in capsys.readouterr().err


def _tools_present() -> bool:
    tool = "sample" if sys.platform == "darwin" else "perf"
    return shutil.which(tool) is not None and shutil.which("rustc") is not None


def _run_perf_here() -> bool:
    """Whether ``perf record`` can run in this environment."""
    if not sys.platform.startswith("linux") or shutil.which("perf") is None:
        return False
    done = subprocess.run(
        ["perf", "stat", "-e", "cpu-clock", "--", "true"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    return done.returncode == 0


@pytest.fixture
def hot_binary(tmp_path: Path) -> Path:
    """A built Rust program with one hot function and a Cargo.toml."""
    if not _tools_present():
        pytest.skip("sample or perf, and rustc, are needed")
    if sys.platform != "darwin" and not _run_perf_here():
        pytest.skip("perf_event_open is not allowed here")
    source = tmp_path / "hotprog.rs"
    source.write_text(HOT)
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "hotprog"\n')
    binary = tmp_path / "hotprog"
    subprocess.run(
        [
            "rustc",
            "-C",
            "opt-level=1",
            "-C",
            "force-frame-pointers=yes",
            "--crate-name",
            "hotprog",
            "-o",
            str(binary),
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    # A new executable's first run can be slow (docs/method.md, "Warm-up
    # (Rust)"); run it once, so the profile is of a steady run.
    subprocess.run([str(binary)], check=True, capture_output=True, timeout=300)
    return binary


@pytest.mark.integration
def test_the_real_profiler_finds_the_hot_function(
    hot_binary: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out.folded"

    status = record(
        [str(hot_binary)],
        output,
        project_root=tmp_path,
        timeout_seconds=120,
    )

    assert status == 0
    hotspots = summarise(
        output.read_text(encoding="utf-8").splitlines(),
        project_root=tmp_path,
        top_n=5,
        own_modules=["hotprog"],
    )
    assert hotspots[0].function == "hotprog`hotprog::spin_hash"
    assert not hotspots[0].is_library
    assert hotspots[0].self_fraction > 0.5


@pytest.mark.integration
def test_the_wrapper_runs_as_a_module(
    hot_binary: Path, tmp_path: Path
) -> None:
    output = tmp_path / "out.folded"

    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "perfproof.profilers.rust",
            "record",
            "--output",
            str(output),
            "--project-root",
            str(tmp_path),
            "--",
            str(hot_binary),
        ],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert "hotprog`hotprog::spin_hash" in output.read_text(encoding="utf-8")


def _listing() -> str:
    return subprocess.run(
        ["ps", "-A", "-o", "args="],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout


@pytest.mark.integration
def test_a_timeout_kills_the_command_and_the_profiler(
    hot_binary: Path, tmp_path: Path
) -> None:
    marker = "47131"

    status = record(
        ["sh", "-c", f"sleep {marker} & wait"],
        tmp_path / "out.folded",
        timeout_seconds=1,
    )

    assert status == 124
    assert f"sleep {marker}" not in _listing()


@pytest.mark.integration
def test_a_command_that_keeps_forking_leaves_nothing_behind(
    hot_binary: Path, tmp_path: Path
) -> None:
    marker = "47133"
    loop = f"while true; do sleep {marker} & sleep 0.01; done"

    for round_number in range(20):
        status = record(
            ["sh", "-c", loop],
            tmp_path / f"out{round_number}.folded",
            timeout_seconds=1,
        )
        assert status == 124

    assert f"sleep {marker}" not in _listing()


def test_a_trait_impl_may_be_written_in_the_type_s_or_the_trait_s_crate() -> (
    None
):
    assert implementing_crates("fd::walk::scan") == ("fd",)
    assert implementing_crates("<fd::Foo as core::fmt::Display>::fmt") == (
        "fd",
        "core",
    )
    assert implementing_crates("<std::path::Path as fd::Ext>::foo") == (
        "std",
        "fd",
    )
    assert implementing_crates("<[u8; 4] as fd::T>::f") == ("fd",)
    assert implementing_crates("<fd::Foo>::inherent") == ("fd",)
    assert implementing_crates(
        "<alloc::vec::Vec<fd::X> as core::clone::Clone>::clone"
    ) == ("alloc", "core")
    assert implementing_crates("<F as core::ops::FnOnce<()>>::call") == (
        "core",
    )
    assert implementing_crates("main") == ()
    assert implementing_crates("<unclosed as x") == ()


PERF_PATHOLOGIES = FIXTURES / "perf-script-pathologies.txt"


def fold_pathologies() -> list[str]:
    out = io.StringIO()
    fold_perf_script(
        PERF_PATHOLOGIES.read_text(encoding="utf-8").splitlines(),
        "pathologies",
        {"pathologies"},
        out,
    )
    return out.getvalue().splitlines()


def test_raw_legacy_and_v0_names_of_a_real_perf_run_are_decoded() -> None:
    raw = PERF_PATHOLOGIES.read_text(encoding="utf-8")
    assert "_ZN3std2io5Write9write_all" in raw
    assert "_RNvCs1Y7DaGC1cwg_7___rustc11___rdl_alloc" in raw

    text = "\n".join(fold_pathologies())

    assert "_ZN" not in text
    assert "_RN" not in text
    assert "pathologies.deps`std::io::Write::write_all" in text
    assert "pathologies.deps`__rustc::__rdl_alloc" in text
    assert (
        "pathologies.deps`<std::io::default_write_fmt::Adapter<T> as "
        "core::fmt::Write>::write_str"
    ) in text
    assert "pathologies.deps`std::rt::lang_start::{{closure}}" in text


def test_a_dso_with_parentheses_of_its_own_does_not_leak_into_the_symbol() -> (
    None
):
    raw = PERF_PATHOLOGIES.read_text(encoding="utf-8")
    assert "[unknown] (/ (deleted))" in raw

    text = "\n".join(fold_pathologies())

    assert "(deleted)" not in text
    assert "`[unknown] (" not in text


def test_demangle_reads_the_raw_legacy_form() -> None:
    assert (
        demangle(
            "_ZN3std2io5Write9write_all17h3f38f68bb91af2f5E"
            ".llvm.10387064478905841067"
        )
        == "std::io::Write::write_all"
    )
    assert (
        demangle(
            "_ZN3std2rt10lang_start28_$u7b$$u7b$closure$u7d$$u7d$"
            "17hdd72b962a9e7adf8E.llvm.13417045834856443964"
        )
        == "std::rt::lang_start::{{closure}}"
    )


def test_demangle_reads_a_simple_v0_name_and_leaves_the_rest() -> None:
    assert (
        demangle("_RNvCs1Y7DaGC1cwg_7___rustc11___rdl_alloc")
        == "__rustc::__rdl_alloc"
    )
    # Generic arguments are outside the part of v0 that is decoded.
    generic = "_RINvCs1Y7DaGC1cwg_4core3fooIjEB2_"
    assert demangle(generic) == generic
    assert demangle("_ZN3std") == "_ZN3std"
    assert demangle("_RNvC") == "_RNvC"


def test_binary_names_include_the_hard_link_cargo_makes(
    tmp_path: Path,
) -> None:
    deps = tmp_path / "target" / "release" / "deps"
    deps.mkdir(parents=True)
    built = deps / "tool-0a551fc1db80cb8a"
    built.write_bytes(b"\x7fELF")
    (deps.parent / "tool").hardlink_to(built)
    (deps / "tool-other").write_bytes(b"\x7fELF")
    (deps.parent / "copy").write_bytes(b"\x7fELF")

    names = binary_names(deps.parent / "tool")

    assert names == {"tool", "tool-0a551fc1db80cb8a"}


def test_binary_names_of_a_missing_file_are_its_own_name(
    tmp_path: Path,
) -> None:
    assert binary_names(tmp_path / "nothing") == {"nothing"}


def test_a_perf_dso_under_another_name_is_the_binary_when_aliased() -> None:
    text = PERF.read_text(encoding="utf-8").splitlines()
    plain = io.StringIO()
    aliased = io.StringIO()

    fold_perf_script(text, "tool", CRATES, plain)
    fold_perf_script(text, "tool", CRATES, aliased, aliases={"fd"})

    assert "tool`" not in plain.getvalue()
    assert "tool`fd::output::print_entry" in aliased.getvalue()
    assert "tool.deps`std::" in aliased.getvalue()
    assert "fd`" not in aliased.getvalue()
