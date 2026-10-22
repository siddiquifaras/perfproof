"""The runtime gate for C and C++: the compiler and what it built.

``tests/fixtures/native`` holds executables built by real compilers
(``SOURCE.md`` there): Apple clang on this Mac and gcc 14 and clang 19
in Linux containers. The tests that run a compiler skip where there is
none on the ``PATH``.
"""

import shutil
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest
from test_lock_runtimes import gate, make_spec, tree

from perfproof.bench import lock as lock_module
from perfproof.bench.nativebuildinfo import compiler_family, native_build_info

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "native"
PROG = FIXTURES / "prog"


def test_a_gcc_elf_names_gcc_in_its_comment_section() -> None:
    lines = native_build_info(FIXTURES / "hot-gcc-elf")

    assert lines == ("GCC: (GNU) 14.4.0",)
    assert compiler_family(lines) == "gcc"
    versions = (FIXTURES / "linux-versions.txt").read_text()
    assert "gcc (GCC) 14.4.0" in versions


def test_a_clang_elf_names_clang_and_the_gcc_runtime_objects() -> None:
    lines = native_build_info(FIXTURES / "hot-clang-elf")

    assert "Debian clang version 19.1.7 (3+b1)" in lines
    assert any(line.startswith("GCC:") for line in lines)
    assert compiler_family(lines) == "clang"
    assert "Debian clang version 19.1.7" in (
        (FIXTURES / "linux-versions.txt").read_text()
    )


@pytest.mark.skipif(sys.platform != "darwin", reason="otool is macOS-only")
def test_a_macho_build_version_is_the_one_otool_prints() -> None:
    lines = native_build_info(FIXTURES / "hot-macho")
    shown = subprocess.run(
        ["otool", "-l", str(FIXTURES / "hot-macho")],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    section = shown.split("LC_BUILD_VERSION")[1].split("Load command")[0]
    fields = dict(
        part.split(None, 1)
        for part in section.splitlines()
        if part.strip() and not part.strip().startswith("cmd")
    )

    assert lines == (
        f"macos minos {fields['minos'].strip()}",
        f"sdk {fields['sdk'].strip()}",
        f"ld {fields['version'].strip()}",
    )
    assert compiler_family(lines) is None


def test_a_file_that_is_not_an_executable_names_nothing(
    tmp_path: Path,
) -> None:
    text = tmp_path / "run.sh"
    text.write_text("#!/bin/sh\necho hi\n")

    assert native_build_info(text) == ()


@pytest.mark.parametrize("keep", [8, 20, 64, 70, 500, 4000])
def test_a_truncated_elf_names_nothing(tmp_path: Path, keep: int) -> None:
    cut = tmp_path / "cut"
    cut.write_bytes((FIXTURES / "hot-gcc-elf").read_bytes()[:keep])

    assert native_build_info(cut) == ()


def test_reading_a_large_executable_uses_bounded_memory(
    tmp_path: Path,
) -> None:
    big = tmp_path / "big"
    big.write_bytes((FIXTURES / "hot-macho").read_bytes() + b"\0" * (64 << 20))
    tracemalloc.start()
    try:
        lines = native_build_info(big)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert lines
    assert peak < 1 << 20


@pytest.mark.parametrize(
    "name",
    [
        "cc",
        "c++",
        "gcc",
        "g++",
        "clang",
        "clang++",
        "gcc-14",
        "clang-19",
        "x86_64-linux-gnu-gcc-12",
        "aarch64-linux-gnu-g++",
    ],
)
def test_compiler_driver_names_are_recognised(name: str) -> None:
    assert lock_module._COMPILER_NAME.fullmatch(  # pyright: ignore[reportPrivateUsage]
        name
    )


@pytest.mark.parametrize(
    "name", ["ccache", "cc1", "cpp", "make", "cmake", "ld", "gccgo", "ccls"]
)
def test_other_build_tools_are_not_compiler_drivers(name: str) -> None:
    assert not lock_module._COMPILER_NAME.fullmatch(  # pyright: ignore[reportPrivateUsage]
        name
    )


def toolchains(
    base: Path, cand: Path
) -> tuple[str | None, list[str], list[str], list[str]]:
    problems: list[str] = []
    unverified: list[str] = []
    differences: list[object] = []
    found = lock_module._native_toolchains(  # pyright: ignore[reportPrivateUsage]
        "bin/hot",
        {"baseline": base, "candidate": cand},
        problems,
        unverified,
        differences,  # pyright: ignore[reportArgumentType]
    )
    return found, problems, unverified, [str(d) for d in differences]


def test_an_elf_built_by_gcc_and_one_by_clang_differ() -> None:
    found, problems, unverified, differences = toolchains(
        FIXTURES / "hot-gcc-elf", FIXTURES / "hot-clang-elf"
    )

    assert found is None
    assert unverified == []
    assert len(problems) == 1
    assert "built by different toolchains" in problems[0]
    assert "GCC: (GNU) 14.4.0" in problems[0]
    assert "Debian clang version 19.1.7" in problems[0]
    assert len(differences) == 1


def test_two_elf_files_of_one_toolchain_name_it() -> None:
    found, problems, unverified, _ = toolchains(
        FIXTURES / "hot-gcc-elf", FIXTURES / "hot-gcc-elf"
    )

    assert found == "gcc: GCC: (GNU) 14.4.0"
    assert problems == [] == unverified


def test_one_arm_naming_a_toolchain_is_unverified(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_text("not an executable")

    found, problems, unverified, _ = toolchains(
        FIXTURES / "hot-gcc-elf", plain
    )

    assert found is None
    assert problems == []
    assert len(unverified) == 1
    assert "only the baseline bin/hot names" in unverified[0]


def test_neither_arm_naming_one_adds_nothing(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_text("not an executable")

    assert toolchains(plain, plain) == (None, [], [], [])


def clang() -> Path:
    found = shutil.which("clang")
    if found is None:
        pytest.skip("no clang on the PATH")
    return Path(found)


def repo(path: Path) -> Path:
    subprocess.run(
        ["git", "-C", str(path), "init", "-q"],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path


def built(root: Path) -> Path:
    """Build the fixture program into ``root/bin/hot`` with clang."""
    out = root / "bin" / "hot"
    subprocess.run(
        [
            str(clang()),
            "-O1",
            "-I",
            str(PROG),
            str(PROG / "hot.c"),
            str(PROG / "work.c"),
            "-o",
            str(out),
        ],
        check=True,
        capture_output=True,
        timeout=300,
    )
    return out


@pytest.mark.integration
def test_clang_as_the_command_is_identified_and_hashed(
    tmp_path: Path,
) -> None:
    exe = clang()
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    spec = make_spec((str(exe), "--version"))

    result = gate(spec, base, cand)

    assert result.status == "passed", result.detail
    banner = subprocess.run(
        [str(exe), "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout.splitlines()[0]
    assert banner in result.detail
    assert "; sha256 " in result.detail


@pytest.mark.integration
def test_a_compiler_script_is_a_shell_not_a_compiler(tmp_path: Path) -> None:
    """A ``gcc`` that is a wrapper script, as ccache's can be."""
    base, cand = tree(tmp_path, "a"), tree(tmp_path, "b")
    for root in (base, cand):
        shim = root / "bin" / "gcc"
        shim.write_text('#!/bin/sh\nexec ccache clang "$@"\n')
        shim.chmod(0o755)
    spec = make_spec(("bin/gcc", "-c", "x.c"))

    result = gate(spec, base, cand)

    assert result.status == "not_available"
    assert "is a shell" in result.detail


@pytest.mark.integration
def test_a_built_binary_records_the_toolchain_that_built_it(
    tmp_path: Path,
) -> None:
    base, cand = repo(tree(tmp_path, "a")), repo(tree(tmp_path, "b"))
    files = [built(base), built(cand)]
    spec = make_spec(("bin/hot",))

    result = gate(spec, base, cand)

    assert result.status == "passed", result.detail
    lines = native_build_info(files[0])
    assert lines
    assert f"built by {'; '.join(lines)} in both" in result.detail


@pytest.mark.integration
def test_binaries_that_name_different_toolchains_fail(tmp_path: Path) -> None:
    """One bit of the candidate's recorded linker version is changed."""
    base, cand = repo(tree(tmp_path, "a")), repo(tree(tmp_path, "b"))
    built(base)
    other = built(cand)
    if native_build_info(other)[-1].split()[0] != "ld":
        pytest.skip("this platform records no linker version in the file")
    data = bytearray(other.read_bytes())
    # LC_BUILD_VERSION's tool entry: (tool id 3 = ld, packed version).
    tool = bytes.fromhex("03000000")
    at = data.index(tool, data.index(b"\x32\x00\x00\x00"))
    data[at + 4] ^= 0x01
    other.write_bytes(bytes(data))
    spec = make_spec(("bin/hot",))

    result = gate(spec, base, cand)

    assert result.status == "failed", result.detail
    assert [d.item for d in result.differences] == ["bin/hot native toolchain"]
