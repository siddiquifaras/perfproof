import tracemalloc
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

import pytest

from perfproof.commands import JsonValue
from perfproof.commands import profile as profile_command
from perfproof.commands._profile_show import (
    _render,  # pyright: ignore[reportPrivateUsage]
)
from perfproof.profilers.folded import (
    DEFAULT_TOP_N,
    FoldedStackError,
    _Normaliser,  # pyright: ignore[reportPrivateUsage]
    summarise,
    summarise_file,
)
from perfproof.schema import Hotspot

FIXTURES = Path(__file__).parent.parent / "fixtures"
PYSPY = FIXTURES / "py-spy" / "workload.txt"
PYSPY_NATIVE = FIXTURES / "py-spy" / "workload-native.txt"
PYSPY_LONG = FIXTURES / "py-spy" / "workload-native-long.txt"
SAMPLE = FIXTURES / "macos-sample" / "workload.folded.txt"
ALL_FIXTURES = (PYSPY, PYSPY_NATIVE, PYSPY_LONG, SAMPLE)

CONTAINER_ROOT = Path("/srv/project")
"""Where the workload was mounted when py-spy recorded it."""

EVERYTHING = 1_000_000


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def reference(path: Path) -> tuple[Counter[str], Counter[str], int]:
    """Self and total counts per raw frame text, computed naively."""
    self_counts: Counter[str] = Counter()
    total_counts: Counter[str] = Counter()
    samples = 0
    for line in read_lines(path):
        stack, _, count = line.rpartition(" ")
        samples += int(count)
        if not stack:
            continue
        frames = stack.split(";")
        self_counts[frames[-1]] += int(count)
        for frame in set(frames):
            total_counts[frame] += int(count)
    return self_counts, total_counts, samples


def find(
    hotspots: tuple[Hotspot, ...],
    function: str,
    file: str | None = None,
    line: int | None = None,
) -> Hotspot:
    matches = [
        h
        for h in hotspots
        if h.function == function and h.file == file and h.line == line
    ]
    assert len(matches) == 1, (function, file, line, matches)
    return matches[0]


def workload_tree(root: Path) -> Path:
    """Create the workload's own files, as py-spy shortened them."""
    for name in ("run.py", "app/__init__.py", "app/kernels.py"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("", encoding="utf-8")
    return root


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_counts_match_a_naive_recount(path: Path) -> None:
    self_counts, total_counts, samples = reference(path)
    hotspots = summarise_file(
        path, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    expected = sorted(
        (self_counts[f], total_counts[f])
        for f in self_counts
        if self_counts[f]
    )
    got = sorted(
        (round(h.self_fraction * samples), round(h.total_fraction * samples))
        for h in hotspots
        if h.total_fraction is not None
    )
    assert len(got) == len(hotspots)
    assert got == expected


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_ranked_by_self_then_total(path: Path) -> None:
    hotspots = summarise_file(
        path, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    keys = [(h.self_fraction, h.total_fraction or 0.0) for h in hotspots]
    assert keys == sorted(keys, reverse=True)
    assert all(h.self_fraction > 0 for h in hotspots)


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_top_n_is_a_prefix_of_the_full_ranking(path: Path) -> None:
    full = summarise_file(path, project_root=CONTAINER_ROOT, top_n=EVERYTHING)
    assert len(full) > DEFAULT_TOP_N
    assert (
        summarise_file(path, project_root=CONTAINER_ROOT)
        == (full[:DEFAULT_TOP_N])
    )
    assert (
        summarise_file(path, project_root=CONTAINER_ROOT, top_n=3)
        == (full[:3])
    )


def test_pyspy_shortened_paths_are_project_code_only_if_the_file_exists(
    tmp_path: Path,
) -> None:
    hotspots = summarise_file(
        PYSPY, project_root=workload_tree(tmp_path), top_n=EVERYTHING
    )
    top = hotspots[0]
    assert (top.function, top.file, top.line) == (
        "<genexpr>",
        "app/kernels.py",
        25,
    )
    assert top.frame_kind == "python"
    assert not top.is_library
    assert not find(hotspots, "main", "run.py", 16).is_library
    assert find(hotspots, "iterencode", "json/encoder.py", 258).is_library

    elsewhere = summarise_file(
        PYSPY, project_root=tmp_path / "missing", top_n=EVERYTHING
    )
    assert all(h.is_library for h in elsewhere)


def test_pyspy_frozen_frames_are_library_python_frames(tmp_path: Path) -> None:
    hotspots = summarise(
        [line for line in read_lines(PYSPY) if "<frozen" in line],
        project_root=workload_tree(tmp_path),
        top_n=EVERYTHING,
    )
    frozen = [h for h in hotspots if (h.file or "").startswith("<frozen")]
    assert frozen
    assert all(h.is_library and h.frame_kind == "python" for h in frozen)


def test_pyspy_full_paths_under_the_root_become_relative() -> None:
    hotspots = summarise_file(
        PYSPY_NATIVE, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    own = [h for h in hotspots if not h.is_library]
    assert own
    assert {h.file for h in own} <= {"run.py", "app/kernels.py"}
    assert all(h.frame_kind == "python" for h in own)
    top = hotspots[0]
    assert (top.function, top.file, top.line) == (
        "<genexpr>",
        "app/kernels.py",
        25,
    )
    stdlib = find(
        hotspots,
        "iterencode",
        "/usr/local/lib/python3.12/json/encoder.py",
        258,
    )
    assert stdlib.is_library


def test_root_spelling_does_not_matter() -> None:
    plain = summarise_file(
        PYSPY_NATIVE, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    for spelling in ("/srv/project/", "/srv/x/../project", "//srv/project"):
        assert (
            summarise_file(
                PYSPY_NATIVE, project_root=Path(spelling), top_n=EVERYTHING
            )
            == plain
        )


def test_symlinked_root_matches_frames_written_with_the_real_path(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    lines = [
        line.replace(str(CONTAINER_ROOT), str(real.resolve()))
        for line in read_lines(PYSPY_NATIVE)
    ]
    hotspots = summarise(lines, project_root=link, top_n=EVERYTHING)
    assert not find(hotspots, "<genexpr>", "app/kernels.py", 25).is_library


def test_installed_packages_under_the_root_are_library() -> None:
    venv = "/srv/project/.venv/lib/python3.12/site-packages"
    lines = [
        line.replace("/usr/local/lib/python3.12/site-packages", venv)
        for line in read_lines(PYSPY_NATIVE)
    ]
    hotspots = summarise(lines, project_root=CONTAINER_ROOT, top_n=EVERYTHING)
    under_venv = [h for h in hotspots if (h.file or "").startswith(".venv/")]
    assert under_venv
    assert all(h.is_library for h in under_venv)


def test_pyspy_native_frames() -> None:
    hotspots = summarise_file(
        PYSPY_NATIVE, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    unresolved = find(hotspots, "0x13ffffa5a723e4")
    assert unresolved.frame_kind == "native"
    assert unresolved.is_library
    blas = find(hotspots, "libscipy_openblas64_-f552eb69.so`inner_thread")
    assert blas.frame_kind == "native"
    assert blas.is_library

    owned = summarise_file(
        PYSPY_NATIVE,
        project_root=CONTAINER_ROOT,
        top_n=EVERYTHING,
        own_modules={"libscipy_openblas64_-f552eb69.so"},
    )
    assert not find(owned, blas.function).is_library


@pytest.mark.parametrize(
    "symbol",
    [
        "foo(int, (anonymous namespace)::Bar)",
        "run(std::function<void (int)>)",
        "apply(void (*)(int))",
    ],
)
def test_demangled_cpp_symbols_are_kept_whole(symbol: str) -> None:
    (hotspot,) = summarise([f"main;{symbol} 3"], project_root=CONTAINER_ROOT)
    assert hotspot.function == symbol
    assert (hotspot.file, hotspot.line) == (None, None)
    assert hotspot.frame_kind == "native"
    assert hotspot.is_library


def test_demangled_cpp_symbol_in_an_own_module_is_project_code() -> None:
    frame = "libfoo.dylib`apply(void (*)(int))"
    (hotspot,) = summarise(
        [f"main;{frame} 3"],
        project_root=CONTAINER_ROOT,
        own_modules={"libfoo.dylib"},
    )
    assert hotspot.function == frame
    assert hotspot.frame_kind == "native"
    assert not hotspot.is_library


def one_frame(
    frame: str, root: Path = CONTAINER_ROOT, own: frozenset[str] = frozenset()
) -> tuple[object, ...]:
    (hotspot,) = summarise([f"{frame} 1"], project_root=root, own_modules=own)
    return (
        hotspot.function,
        hotspot.file,
        hotspot.line,
        hotspot.frame_kind,
        hotspot.is_library,
    )


@pytest.mark.parametrize(
    ("frame", "function"),
    [
        ("Foo::operator< (/srv/project/libfoo.so)", "Foo::operator<"),
        ("Foo::operator-> (/srv/project/libfoo.so)", "Foo::operator->"),
        ("Foo::operator>> (/srv/project/libfoo.so)", "Foo::operator>>"),
        (
            "std::ostream& std::operator<< <std::char_traits<char> >"
            "(std::ostream&, char const*) (/usr/lib/libstdc++.so.6)",
            "std::ostream& std::operator<< <std::char_traits<char> >"
            "(std::ostream&, char const*)",
        ),
    ],
)
def test_cpp_operator_frames_split_at_their_location(
    frame: str, function: str
) -> None:
    module = frame.rpartition("/")[2].removesuffix(")")
    own = frozenset({"libfoo.so", "libstdc++.so.6"})
    assert one_frame(frame, own=own) == (
        f"{module}`{function}",
        None,
        None,
        "native",
        function.startswith("std::"),
    )


def test_cpp_frame_with_parentheses_under_the_root_is_project_code() -> None:
    assert one_frame("apply(void (*)(int)) (/srv/project/libfoo.so)") == (
        "libfoo.so`apply(void (*)(int))",
        None,
        None,
        "native",
        False,
    )


def test_unbalanced_head_is_never_split() -> None:
    frame = "foo(int (/srv/project/libfoo.so)"
    assert one_frame(frame, own=frozenset({"libfoo.so"})) == (
        frame,
        None,
        None,
        "native",
        True,
    )


@pytest.mark.parametrize(
    ("frame", "root", "expected"),
    [
        (
            "slow_sum (/srv/project (copy)/app/kernels.py:18)",
            "/srv/project (copy)",
            ("slow_sum", "app/kernels.py", 18, "python", False),
        ),
        (
            "run (/home/u/proj (copy)/run.py:3)",
            "/home/u/proj (copy)",
            ("run", "run.py", 3, "python", False),
        ),
    ],
)
def test_paths_may_contain_parentheses(
    frame: str, root: str, expected: tuple[object, ...]
) -> None:
    assert one_frame(frame, root=Path(root)) == expected


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (
            "<module> (/srv/project/run.py:21)",
            ("<module>", "run.py", 21, "python", False),
        ),
        (
            "_call_with_frames_removed (<frozen importlib._bootstrap>:488)",
            (
                "_call_with_frames_removed",
                "<frozen importlib._bootstrap>",
                488,
                "python",
                True,
            ),
        ),
        (
            "0x13ffffa5a723e4 (?)",
            ("0x13ffffa5a723e4", None, None, "native", True),
        ),
        (
            "inner_thread (/usr/local/lib/python3.12/site-packages/"
            "numpy.libs/libscipy_openblas64_-f552eb69.so)",
            (
                "libscipy_openblas64_-f552eb69.so`inner_thread",
                None,
                None,
                "native",
                True,
            ),
        ),
    ],
)
def test_real_pyspy_frames_still_split_at_their_location(
    frame: str, expected: tuple[object, ...]
) -> None:
    assert frame in PYSPY_NATIVE.read_text(encoding="utf-8")
    (hotspot,) = summarise([f"{frame} 1"], project_root=CONTAINER_ROOT)
    assert (
        hotspot.function,
        hotspot.file,
        hotspot.line,
        hotspot.frame_kind,
        hotspot.is_library,
    ) == expected


def test_python_and_native_frames_of_one_name_stay_apart() -> None:
    hotspots = summarise_file(
        PYSPY_LONG, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    self_counts, _, _ = reference(PYSPY_LONG)
    python = "__init__ (/usr/local/lib/python3.12/typing.py:1470)"
    native = (
        "__init__ (/usr/local/lib/python3.12/site-packages/numpy/random/"
        "bit_generator.cpython-312-aarch64-linux-gnu.so)"
    )
    assert self_counts[python] > 0
    assert self_counts[native] > 0
    assert (
        find(
            hotspots, "__init__", "/usr/local/lib/python3.12/typing.py", 1470
        ).frame_kind
        == "python"
    )
    assert (
        find(
            hotspots,
            "bit_generator.cpython-312-aarch64-linux-gnu.so`__init__",
        ).frame_kind
        == "native"
    )


def test_recursive_frames_count_once_per_stack() -> None:
    frame = "fib (/srv/project/app/kernels.py:12)"
    occurrences = 0
    stacks_with_frame = 0
    samples = 0
    for line in read_lines(PYSPY_LONG):
        stack, _, count = line.rpartition(" ")
        samples += int(count)
        depth = stack.split(";").count(frame)
        occurrences += depth * int(count)
        stacks_with_frame += int(count) if depth else 0
    assert occurrences > 2 * stacks_with_frame

    hotspots = summarise_file(
        PYSPY_LONG, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    fib = find(hotspots, "fib", "app/kernels.py", 12)
    assert fib.total_fraction == stacks_with_frame / samples


def test_empty_pyspy_stacks_count_towards_the_total_only() -> None:
    lines = read_lines(PYSPY_LONG)
    empty = sum(int(line.split()[0]) for line in lines if line[0] == " ")
    assert empty > 0
    _, _, samples = reference(PYSPY_LONG)
    hotspots = summarise(lines, project_root=CONTAINER_ROOT, top_n=EVERYTHING)
    assert sum(h.self_fraction for h in hotspots) == pytest.approx(
        (samples - empty) / samples, rel=1e-12
    )


def test_macos_sample_frames_via_inferno() -> None:
    hotspots = summarise_file(
        SAMPLE, project_root=CONTAINER_ROOT, top_n=EVERYTHING
    )
    top = hotspots[0]
    assert top.function == "libpython3.12`list_contains"
    assert (top.file, top.line) == (None, None)
    assert top.frame_kind == "native"
    assert top.is_library
    assert all(h.frame_kind == "native" for h in hotspots)

    owned = summarise_file(
        SAMPLE,
        project_root=CONTAINER_ROOT,
        top_n=EVERYTHING,
        own_modules={"libpython3.12"},
    )
    assert not owned[0].is_library
    numpy = find(
        owned,
        "_pcg64.cpython-312-darwin.so"
        "`__pyx_f_5numpy_6random_6_pcg64_pcg64_double",
    )
    assert numpy.is_library


def test_sample_thread_root_with_spaces_is_one_frame() -> None:
    lines = read_lines(SAMPLE)
    root = lines[0].split(";")[0]
    assert " " in root
    _, total_counts, samples = reference(SAMPLE)
    assert total_counts[root] == samples
    single = summarise([f"{root} 5"], project_root=CONTAINER_ROOT)
    assert len(single) == 1
    assert single[0].function == root
    assert single[0].self_fraction == 1.0


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        ("main", "expected a stack"),
        ("main (run.py:1) x", "is not an integer"),
        ("main (run.py:1) -1", "is not an integer"),
        ("main (run.py:1) 1.5", "is not an integer"),
        ("main (run.py:1) ٣", "is not an integer"),
        ("main (run.py:1);;slow (run.py:2) 1", "empty frame"),
        (";main (run.py:1) 1", "empty frame"),
    ],
)
def test_malformed_lines_raise_with_their_number(
    line: str, reason: str
) -> None:
    with pytest.raises(FoldedStackError, match=reason) as raised:
        summarise(["a (run.py:1) 1", "", line], project_root=CONTAINER_ROOT)
    assert raised.value.line_number == 3


@pytest.mark.parametrize("lines", [[], ["", "  "], ["main (run.py:1) 0"]])
def test_no_samples_raises(lines: list[str]) -> None:
    with pytest.raises(FoldedStackError, match="no samples") as raised:
        summarise(lines, project_root=CONTAINER_ROOT)
    assert raised.value.line_number is None


@pytest.mark.parametrize("top_n", [0, -1])
def test_top_n_below_one_raises(top_n: int) -> None:
    with pytest.raises(ValueError, match="top_n"):
        summarise(
            read_lines(PYSPY),
            project_root=CONTAINER_ROOT,
            top_n=top_n,
        )


def test_crlf_line_endings_are_accepted() -> None:
    lines = [line + "\r\n" for line in read_lines(PYSPY_NATIVE)]
    assert summarise(lines, project_root=CONTAINER_ROOT) == summarise_file(
        PYSPY_NATIVE, project_root=CONTAINER_ROOT
    )


def repeated(path: Path, times: int) -> Iterator[str]:
    """Stream a real capture several times over, never all at once."""
    for _ in range(times):
        with path.open(encoding="utf-8") as stream:
            yield from stream


def peak_bytes(times: int) -> tuple[int, tuple[Hotspot, ...]]:
    tracemalloc.start()
    try:
        hotspots = summarise(
            repeated(PYSPY_LONG, times), project_root=CONTAINER_ROOT
        )
        return tracemalloc.get_traced_memory()[1], hotspots
    finally:
        tracemalloc.stop()


def test_large_capture_streams_in_bounded_memory() -> None:
    times = 50
    once_peak, once = peak_bytes(1)
    many_peak, many = peak_bytes(times)
    assert PYSPY_LONG.stat().st_size * times > 20_000_000
    assert many_peak < once_peak * 1.1 + 256 * 1024
    assert [(h.function, h.file, h.line) for h in many] == [
        (h.function, h.file, h.line) for h in once
    ]
    for a, b in zip(many, once, strict=True):
        assert a.self_fraction == pytest.approx(b.self_fraction, rel=1e-12)
        assert a.total_fraction == pytest.approx(b.total_fraction, rel=1e-12)


@pytest.mark.parametrize(
    ("frame", "is_library"),
    [
        ("core::Engine::run (/opt/app/libfoo.so)", False),
        ("alloc::Arena::grow (/opt/app/libfoo.so)", False),
        ("core::fmt::write::h0123456789abcdef (/opt/app/libfoo.so)", True),
        ("std::__1::basic_string::append (/opt/app/libfoo.so)", True),
    ],
)
def test_runtime_namespaces_in_an_own_binary(
    frame: str, is_library: bool
) -> None:
    """``core::`` is a Rust runtime only with Rust's hash suffix."""
    got = one_frame(frame, own=frozenset({"libfoo.so"}))
    assert got[-1] is is_library


OWN_BINARIES = frozenset({"libfoo.so", "fd"})


@pytest.mark.parametrize(
    "frame",
    [
        "libfoo.so`std::vector<int, std::allocator<int> >"
        " app::load<int>(char const*)",
        "fd`<std::path::Path as fd::PathExt>::ext::h0123456789abcdef",
        "fd`<alloc::string::String as fd::PathExt>::ext::h0123456789abcdef",
        "fd`_$LT$std..path..Path$u20$as$u20$fd..PathExt$GT$"
        "::ext::h0123456789abcdef",
        "fd`<core::option::Option<fd::Entry> as fd::OptExt>"
        "::or_skip::h0123456789abcdef",
        "libfoo.so`app::Foo::operator std::string() const",
        "libfoo.so`app::X::operator std::vector<int>*() const",
        "libfoo.so`std::string_view (*app::get())(int)",
        "libfoo.so`app::f(std::string)",
        "libfoo.so`std::sort<app::X*>(app::X*, app::X*)",
        "libfoo.so`process(std::vector<int>&)",
        "libfoo.so`std::string get_name()",
        "libfoo.so`void helper(std::string const&)",
        "libfoo.so`(anonymous namespace)::f(std::vector<int>&)",
        "libfoo.so`operator<<(std::ostream&, Foo const&)",
        "libfoo.so`foo(std::string) [clone .cold]",
        "libfoo.so`operator()(std::string)",
    ],
)
def test_project_code_that_mentions_std_types_stays_project(
    frame: str,
) -> None:
    """Impls of project traits and functions returning std types."""
    assert one_frame(frame, own=OWN_BINARIES)[-1] is False


@pytest.mark.parametrize(
    "frame",
    [
        "fd`std::sys::thread::unix::Thread::new::thread_start"
        "::h7cf39b7a7f135f48",
        "fd`core::ops::function::FnOnce::call_once$u7b$$u7b$vtable.shim"
        "$u7d$$u7d$::h46dc0d090c9b1ad6",
        "fd`_$LT$std..io..stdio..StdoutLock$u20$as$u20$std..io..Write$GT$"
        "::write_all::h34cc11c080bf2685",
        "fd`<std::path::Path as core::fmt::Debug>::fmt::h0123456789abcdef",
        "fd`<std::path::Path>::file_name::h0123456789abcdef",
        "fd`std::thread::lifecycle::JoinInner$LT$T$GT$::join"
        "::h3306ad4cb9995805",
        "libfoo.so`void std::f()",
        "libfoo.so`std::ostream& std::operator<< <std::char_traits<char> >"
        "(std::ostream&, char const*)",
        "libfoo.so`std::__1::basic_string<char>::append(char const*)",
        "libfoo.so`__gnu_cxx::__normal_iterator<int*>::operator++()",
    ],
)
def test_runtime_functions_in_an_own_binary_are_library(frame: str) -> None:
    assert one_frame(frame, own=OWN_BINARIES)[-1] is True


@pytest.mark.parametrize("project_first", [True, False])
def test_path_checks_do_not_share_a_cache_between_meanings(
    tmp_path: Path, project_first: bool
) -> None:
    """Ownership and harness status of a path are cached apart.

    A file in a library directory under ``.perfproof/`` is not "own" but
    is harness; asking about ownership first must not hide that.
    """
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    nested = tmp_path / ".perfproof" / "harness" / "site-packages"
    nested.mkdir(parents=True)
    (nested / "drv.py").write_text("x = 1\n", encoding="utf-8")
    project = "f (app.py:1)"
    harness = "g (.perfproof/harness/site-packages/drv.py:1)"
    normaliser = _Normaliser(tmp_path, ())
    order = (project, harness) if project_first else (harness, project)
    frames = {text: normaliser.frame(text) for text in order}
    assert not frames[project].is_library
    assert not frames[project].is_harness
    assert frames[harness].is_library
    assert frames[harness].is_harness


def test_harness_frames_read_harness_not_library_in_text() -> None:
    hotspot = Hotspot(
        function="drive",
        file=".perfproof/harness/driver.py",
        line=6,
        frame_kind="python",
        is_library=True,
        is_harness=True,
        self_fraction=0.0,
        total_fraction=0.5,
    )
    row = profile_command._row(hotspot)  # pyright: ignore[reportPrivateUsage]
    assert row.endswith("drive  (harness)")
    entry: dict[str, JsonValue] = {
        "function": "drive",
        "file": hotspot.file,
        "line": 6,
        "is_library": True,
        "is_harness": True,
        "self_fraction": 0.0,
        "total_fraction": 0.5,
        "total_samples": 5,
        "cap": 2.0,
    }
    result: dict[str, JsonValue] = {
        "run_id": "r",
        "name": "n",
        "pattern": "drive",
        "samples": 10,
        "found": True,
        "functions_omitted": 0,
    }
    assert _render(result, [entry]).endswith("drive  (harness)")
