"""The Go profiler wrapper: pprof to folded stacks, and the command.

The fixtures under ``tests/fixtures/go`` are captures of the real Go
toolchain (see ``SOURCE.md`` there): the pprof files, and the output of
``go tool pprof -raw`` on the same files, which is the independent
reading the converter is checked against. Tests that run the real
``go`` are marked ``integration`` and skip where there is no Go.
"""

import gzip
import io
import json
import os
import re
import shutil
import subprocess
import tracemalloc
from collections import Counter
from pathlib import Path

import pytest

from perfproof.profilers import go as go_module
from perfproof.profilers.folded import summarise_file
from perfproof.profilers.go import PprofFormatError, fold_pprof, main
from perfproof.schema import Hotspot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "go"
PROG = FIXTURES / "prog"

type Stack = tuple[tuple[str, int], ...]


def _cpu_total() -> int:
    """The profile's total in nanoseconds, as ``go tool pprof -top``."""
    top = (FIXTURES / "cpu-top.txt").read_text()
    found = re.search(r"Total samples = ([0-9.]+)s", top)
    assert found is not None
    return round(float(found[1]) * 1e9)


CPU_TOTAL = _cpu_total()

_LOCATION = re.compile(
    r"\s+(?P<id>\d+): 0x[0-9a-f]+ M=\d+ (?P<name>.+?) "
    r"(?P<file>\S+):(?P<line>\d+):\d+ s=\d+"
)
_INLINED = re.compile(
    r"\s+(?P<name>\S.*?) (?P<file>\S+):(?P<line>\d+):\d+ s=\d+"
)
_SAMPLE = re.compile(r"\s+(?P<values>-?\d+(?: +-?\d+)*): ?(?P<ids>[\d ]*)")


def read_raw(path: Path, column: int) -> Counter[Stack]:
    """Read ``go tool pprof -raw`` into root-first stacks and weights.

    ``column`` is the index of the value to weight by. Each stack is a
    tuple of ``(function, line)``.
    """
    locations: dict[int, list[tuple[str, int]]] = {}
    samples: list[tuple[int, list[int]]] = []
    section = ""
    current: list[tuple[str, int]] = []
    for line in path.read_text().splitlines():
        if line in ("Samples:", "Locations", "Mappings"):
            section = line
            continue
        if section == "Samples:":
            found = _SAMPLE.fullmatch(line)
            if found is not None:
                values = [int(v) for v in found["values"].split()]
                ids = [int(i) for i in found["ids"].split()]
                samples.append((values[column], ids))
        elif section == "Locations":
            header = _LOCATION.fullmatch(line)
            if header is not None:
                current = [(header["name"], int(header["line"]))]
                locations[int(header["id"])] = current
                continue
            inlined = _INLINED.fullmatch(line)
            assert inlined is not None, line
            current.append((inlined["name"], int(inlined["line"])))
    stacks: Counter[Stack] = Counter()
    for weight, ids in samples:
        leaf_first = [frame for i in ids for frame in locations[i]]
        if weight > 0:
            stacks[tuple(reversed(leaf_first))] += weight
    return stacks


_FRAME = re.compile(r"(?P<name>.+) \((?P<file>.+):(?P<line>\d+)\)")


def read_folded(text: str) -> Counter[Stack]:
    """Read folded stacks as ``(function, line)`` tuples and weights."""
    stacks: Counter[Stack] = Counter()
    for row in text.splitlines():
        stack, _, weight = row.rpartition(" ")
        frames: list[tuple[str, int]] = []
        for frame in stack.split(";"):
            found = _FRAME.fullmatch(frame)
            assert found is not None, frame
            frames.append((found["name"], int(found["line"])))
        stacks[tuple(frames)] += int(weight)
    return stacks


def convert(
    profile: str,
    sample_type: str | None = None,
    project_root: Path | None = None,
) -> str:
    out = io.StringIO()
    fold_pprof(
        FIXTURES / profile,
        out,
        sample_type=sample_type,
        project_root=project_root,
    )
    return out.getvalue()


# The converter against ``go tool pprof -raw`` on the same files.


@pytest.mark.parametrize(
    ("profile", "raw", "sample_type", "column"),
    [
        ("cpu.pprof", "cpu-raw.txt", "cpu", 1),
        ("mem.pprof", "mem-raw.txt", "alloc_space", 1),
        ("mem.pprof", "mem-raw.txt", "alloc_objects", 0),
        ("mem.pprof", "mem-raw.txt", "inuse_space", 3),
        ("mutex.pprof", "mutex-raw.txt", "delay", 1),
        ("mutex.pprof", "mutex-raw.txt", "contentions", 0),
        ("block.pprof", "block-raw.txt", "delay", 1),
    ],
)
def test_the_stacks_and_weights_are_the_ones_pprof_prints(
    profile: str, raw: str, sample_type: str, column: int
) -> None:
    expected = read_raw(FIXTURES / raw, column)
    assert expected
    assert read_folded(convert(profile, sample_type=sample_type)) == expected


def test_the_cpu_total_is_the_one_pprof_top_prints() -> None:
    total = fold_pprof(FIXTURES / "cpu.pprof", io.StringIO())
    assert total == CPU_TOTAL > 1_000_000_000


def test_a_memory_profile_total_is_the_benchmarks_bytes() -> None:
    """400 iterations of 21,760,120 bytes, as ``-benchmem`` said."""
    total = fold_pprof(
        FIXTURES / "mem.pprof", io.StringIO(), sample_type="alloc_space"
    )
    assert abs(total - 400 * 21_760_120) / total < 0.01


def test_a_profile_of_several_types_with_a_cpu_one_defaults_to_cpu() -> None:
    assert fold_pprof(FIXTURES / "cpu.pprof", io.StringIO()) == CPU_TOTAL


def test_an_ambiguous_profile_is_refused_not_guessed() -> None:
    with pytest.raises(PprofFormatError, match="choose one"):
        fold_pprof(FIXTURES / "mem.pprof", io.StringIO())


def test_an_unknown_sample_type_names_the_types_there_are() -> None:
    with pytest.raises(PprofFormatError, match="alloc_space") as seen:
        fold_pprof(FIXTURES / "mem.pprof", io.StringIO(), sample_type="cpu")
    assert "'cpu'" in str(seen.value)


def test_an_inlined_callee_is_a_frame_of_its_own() -> None:
    """``sync.(*Mutex).Unlock`` is inlined into ``Contend.func1``.

    pprof lists the callee first within a location; folded stacks run
    from the root, so the caller comes before the callee.
    """
    stacks = read_folded(convert("mutex.pprof", sample_type="delay"))
    pairs = {
        (stack[i][0], stack[i + 1][0])
        for stack in stacks
        for i in range(len(stack) - 1)
    }
    assert (
        "example.com/prog.Contend.func1",
        "sync.(*Mutex).Unlock",
    ) in pairs


# Files named as the project's own.


def summary(tmp_path: Path, own_frames: bool) -> list[Hotspot]:
    folded = tmp_path / "cpu.folded"
    with folded.open("w") as out:
        fold_pprof(
            FIXTURES / "cpu.pprof",
            out,
            project_root=PROG,
            path_map=[("/src", str(PROG))],
            own_frames=own_frames,
        )
    return list(summarise_file(folded, project_root=PROG, top_n=60))


def test_a_mount_maps_to_the_project_so_its_frames_are_its_own(
    tmp_path: Path,
) -> None:
    """The profile names ``/src/work.go``; the project is elsewhere."""
    hotspots = summary(tmp_path, own_frames=False)

    # Only leaf frames are ranked, and every leaf is the runtime's:
    # the project's functions are callers, with no time of their own.
    assert hotspots[0].function == "runtime.memmove"
    assert all(h.is_library for h in hotspots)


def test_charging_library_time_to_the_project_line_that_made_it(
    tmp_path: Path,
) -> None:
    hotspots = summary(tmp_path, own_frames=True)

    top = hotspots[0]
    assert (top.function, top.file, top.is_library) == (
        "Join",
        "work.go",
        False,
    )
    join = sum(h.self_fraction for h in hotspots if h.function == top.function)
    assert join > 0.5
    own_total = sum(h.self_fraction for h in hotspots if not h.is_library)
    assert own_total > 0.5


def test_charging_to_the_project_keeps_the_total() -> None:
    plain = io.StringIO()
    own = io.StringIO()
    mounted = [("/src", str(PROG))]
    one = fold_pprof(
        FIXTURES / "cpu.pprof", plain, project_root=PROG, path_map=mounted
    )
    two = fold_pprof(
        FIXTURES / "cpu.pprof",
        own,
        project_root=PROG,
        path_map=mounted,
        own_frames=True,
    )
    assert one == two == CPU_TOTAL
    owned = f"({PROG}/"
    rows = [
        row.rpartition(" ")[0].split(";")
        for row in own.getvalue().splitlines()
    ]
    for frames in rows:
        at = [i for i, frame in enumerate(frames) if owned in frame]
        if at:
            assert at[-1] == len(frames) - 1
    # A sample with no project frame at all (the GC's workers) is kept.
    assert any(not any(owned in f for f in frames) for frames in rows)


def test_charging_to_the_project_needs_to_know_the_project() -> None:
    with pytest.raises(ValueError, match="needs project_root"):
        fold_pprof(FIXTURES / "cpu.pprof", io.StringIO(), own_frames=True)


def test_without_a_map_the_files_stay_as_the_profile_named_them() -> None:
    text = convert("cpu.pprof", project_root=PROG)
    assert "(/src/work.go:" in text
    assert "(/usr/local/go/src/runtime/" in text


def test_a_trimpath_name_is_found_through_the_module_path(
    tmp_path: Path,
) -> None:
    """``-trimpath`` records ``<module>/work.go``, not ``/src``."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "go.mod").write_text("module example.com/prog // comment\n")
    mapper = go_module._PathMapper(  # pyright: ignore[reportPrivateUsage]
        root, [], go_module.module_path(root)
    )
    assert mapper("example.com/prog/work.go") == f"{root}/work.go"
    assert mapper("example.com/prog/sub/x.go") == f"{root}/sub/x.go"
    assert mapper("example.com/other/work.go") == "example.com/other/work.go"
    assert mapper("runtime/proc.go") == "runtime/proc.go"


@pytest.mark.parametrize(
    ("text", "module"),
    [
        ("module example.com/a\n", "example.com/a"),
        ('module "example.com/q"\n\ngo 1.25\n', "example.com/q"),
        ("// module nope\ngo 1.25\n", None),
        ("", None),
    ],
)
def test_the_module_path_is_read_from_go_mod(
    tmp_path: Path, text: str, module: str | None
) -> None:
    (tmp_path / "go.mod").write_text(text)
    assert go_module.module_path(tmp_path) == module


def test_a_directory_with_no_go_mod_has_no_module_path(
    tmp_path: Path,
) -> None:
    assert go_module.module_path(tmp_path) is None


# Bad and large files.


def encode(fields: list[tuple[int, bytes]]) -> bytes:
    """Encode length-delimited fields: tag, length, bytes."""

    def varint(value: int) -> bytes:
        out = bytearray()
        while value >= 0x80:
            out.append(value & 0x7F | 0x80)
            value >>= 7
        out.append(value)
        return bytes(out)

    return b"".join(
        varint(number << 3 | 2) + varint(len(body)) + body
        for number, body in fields
    )


def split(data: bytes) -> list[tuple[int, bytes]]:
    """Split a message into its length-delimited top-level fields."""

    def varint(at: int) -> tuple[int, int]:
        value = shift = 0
        while True:
            byte = data[at]
            at += 1
            value |= (byte & 0x7F) << shift
            if byte < 0x80:
                return value, at
            shift += 7

    fields: list[tuple[int, bytes]] = []
    at = 0
    while at < len(data):
        tag, at = varint(at)
        if tag & 7 == 0:
            _, at = varint(at)
            continue
        assert tag & 7 == 2
        size, at = varint(at)
        fields.append((tag >> 3, data[at : at + size]))
        at += size
    return fields


def real_fields() -> list[tuple[int, bytes]]:
    return split(gzip.decompress((FIXTURES / "cpu.pprof").read_bytes()))


def test_a_profile_that_is_not_gzip_or_protobuf_is_refused(
    tmp_path: Path,
) -> None:
    junk = tmp_path / "junk.pprof"
    junk.write_bytes(b"this is not a profile\n" * 20)
    with pytest.raises(PprofFormatError):
        fold_pprof(junk, io.StringIO())


def test_a_truncated_profile_is_refused(tmp_path: Path) -> None:
    whole = (FIXTURES / "cpu.pprof").read_bytes()
    cut = tmp_path / "cut.pprof"
    cut.write_bytes(whole[: len(whole) // 2])
    with pytest.raises(PprofFormatError):
        fold_pprof(cut, io.StringIO())


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PprofFormatError, match="cannot be read"):
        fold_pprof(tmp_path / "none.pprof", io.StringIO())


def test_a_sample_naming_an_undefined_location_is_refused(
    tmp_path: Path,
) -> None:
    fields = [(n, b) for n, b in real_fields() if n != 4]
    broken = tmp_path / "broken.pprof"
    broken.write_bytes(gzip.compress(encode(fields)))
    with pytest.raises(PprofFormatError, match="not defined"):
        fold_pprof(broken, io.StringIO())


def test_an_uncompressed_profile_is_read(tmp_path: Path) -> None:
    plain = tmp_path / "plain.pprof"
    plain.write_bytes(gzip.decompress((FIXTURES / "cpu.pprof").read_bytes()))
    assert fold_pprof(plain, io.StringIO()) == CPU_TOTAL


def test_a_field_beyond_the_readers_limit_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(go_module, "_MAX_FIELD_BYTES", 50)
    with pytest.raises(PprofFormatError, match="larger than the reader"):
        fold_pprof(FIXTURES / "cpu.pprof", io.StringIO())


def test_a_string_table_beyond_the_readers_limit_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(go_module, "_MAX_STRING_BYTES", 100)
    with pytest.raises(PprofFormatError, match="string table"):
        fold_pprof(FIXTURES / "cpu.pprof", io.StringIO())


def big_profile(path: Path, copies: int) -> int:
    """Write the real profile with its samples repeated; return bytes.

    The profile's own bytes: its tables once, its samples ``copies``
    times each, so the stacks and their order are the real ones.
    """
    fields = real_fields()
    samples = [(n, b) for n, b in fields if n == 2]
    rest = [(n, b) for n, b in fields if n != 2]
    body = encode(rest) + encode(samples) * copies
    with gzip.open(path, "wb", compresslevel=1) as handle:
        handle.write(body)
    return len(body)


def test_reading_many_samples_uses_bounded_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(go_module, "_CHUNK_BYTES", 1 << 14)
    profile = tmp_path / "big.pprof"
    size = big_profile(profile, 1500)
    assert size > 3 << 20
    tracemalloc.start()
    try:
        total = fold_pprof(profile, io.StringIO())
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert total == 1500 * CPU_TOTAL
    # Holding the decompressed profile, or every sample, would peak
    # above its size.
    assert peak < size // 4


def test_stacks_are_flushed_when_the_budget_is_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stacks repeated across flushes add up to the same weights."""
    profile = tmp_path / "big.pprof"
    big_profile(profile, 5)
    monkeypatch.setattr(go_module, "_FLUSH_BYTES", 1)
    out = io.StringIO()
    total = fold_pprof(profile, out)
    assert total == 5 * CPU_TOTAL
    lines = out.getvalue().splitlines()
    assert len(lines) > 5 * 100  # each sample written on its own
    stacks = read_folded(out.getvalue())
    expected = read_raw(FIXTURES / "cpu-raw.txt", 1)
    assert stacks == Counter({s: 5 * w for s, w in expected.items()})


# The command: what ``record`` runs and what it does with the result.


def stub(directory: Path, name: str, body: str) -> Path:
    """A script that stands in for a Go command and records its use."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)
    return path


COPY_FROM_FLAG = (
    'for a in "$@"; do case "$a" in\n'
    '  -cpuprofile=*|-test.cpuprofile=*) cp {fixture} "${{a#*=}}";;\n'
    "esac; done\n"
    'prev=""; for a in "$@"; do\n'
    '  if [ "$prev" = -cpuprofile ]; then cp {fixture} "$a"; fi\n'
    "  prev=$a; done"
)


def run_main(tmp_path: Path, *argv: str) -> int:
    return main(["record", "--output", str(tmp_path / "out.folded"), *argv])


def test_go_test_gets_the_profile_flags_and_a_directory_for_its_binary(
    tmp_path: Path,
) -> None:
    log = tmp_path / "argv.txt"
    fake = stub(
        tmp_path / "bin",
        "go",
        f'echo "$@" > {log}\n'
        + COPY_FROM_FLAG.format(fixture=FIXTURES / "cpu.pprof"),
    )

    code = run_main(
        tmp_path,
        "--project-root",
        str(PROG),
        "--",
        str(fake),
        "test",
        "-run",
        "^$",
        "-bench",
        ".",
        ".",
    )

    assert code == 0
    raw = tmp_path / "out.folded.pprof.d" / "cpu.pprof"
    assert log.read_text().split() == [
        "test",
        "-cpuprofile",
        str(raw),
        "-o",
        f"{raw.parent}/",
        "-run",
        "^$",
        "-bench",
        ".",
        ".",
    ]
    assert raw.is_file()
    assert (tmp_path / "out.folded").read_text().count("\n") > 50


@pytest.mark.parametrize(
    ("kind", "flags"),
    [
        ("mem", ["-memprofile", "-memprofilerate", "4096"]),
        ("mutex", ["-mutexprofile", "-mutexprofilefraction", "1"]),
        ("block", ["-blockprofile", "-blockprofilerate", "1"]),
    ],
)
def test_each_profile_kind_gets_its_own_flags(
    tmp_path: Path, kind: str, flags: list[str]
) -> None:
    log = tmp_path / "argv.txt"
    fake = stub(tmp_path / "bin", "go", f'echo "$@" > {log}')

    run_main(tmp_path, "--profile", kind, "--", str(fake), "test", ".")

    words = log.read_text().split()
    assert words[0] == "test"
    for flag in flags:
        assert flag in words
    assert words[words.index(flags[0]) + 1].endswith(f"{kind}.pprof")


def test_a_users_own_o_flag_is_left_alone(tmp_path: Path) -> None:
    log = tmp_path / "argv.txt"
    fake = stub(tmp_path / "bin", "go", f'echo "$@" > {log}')

    run_main(tmp_path, "--", str(fake), "test", "-o", "mine/", ".")

    assert log.read_text().split().count("-o") == 1


def test_a_test_binary_gets_the_test_dot_flags(tmp_path: Path) -> None:
    log = tmp_path / "argv.txt"
    fake = stub(
        tmp_path / "bin",
        "prog.test",
        f'echo "$@" > {log}\n'
        + COPY_FROM_FLAG.format(fixture=FIXTURES / "cpu.pprof"),
    )

    code = run_main(tmp_path, "--", str(fake), "-test.bench", ".")

    assert code == 0
    raw = tmp_path / "out.folded.pprof.d" / "cpu.pprof"
    assert log.read_text().split() == [
        "-test.bench",
        ".",
        f"-test.cpuprofile={raw}",
    ]


def test_a_program_gets_the_profile_in_its_environment(
    tmp_path: Path,
) -> None:
    log = tmp_path / "env.txt"
    fake = stub(
        tmp_path / "bin",
        "hot",
        f'echo "$PERFPROOF_PPROF $PERFPROOF_PPROF_KIND $@" > {log}\n'
        f'cp {FIXTURES / "hot-cpu.pprof"} "$PERFPROOF_PPROF"',
    )

    code = run_main(tmp_path, "--", str(fake), "arg")

    assert code == 0
    raw = tmp_path / "out.folded.pprof.d" / "cpu.pprof"
    assert log.read_text().split() == [str(raw), "cpu", "arg"]


def test_a_mount_is_spelled_as_the_command_sees_it(tmp_path: Path) -> None:
    """The command runs where the repository is ``/src``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    log = tmp_path / "argv.txt"
    fake = stub(tmp_path / "bin", "go", f'echo "$@" > {log}')

    run_main(
        tmp_path,
        "--profile-dir",
        str(repo / ".prof"),
        "--path-map",
        f"/src={repo}",
        "--",
        str(fake),
        "test",
        ".",
    )

    words = log.read_text().split()
    assert words[words.index("-cpuprofile") + 1] == "/src/.prof/cpu.pprof"
    assert words[words.index("-o") + 1] == "/src/.prof/"


def test_a_command_that_writes_no_profile_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quiet = stub(tmp_path / "bin", "hot", "exit 0")

    code = run_main(tmp_path, "--", str(quiet))

    assert code == 5
    assert "did not write the file named by PERFPROOF_PPROF" in (
        capsys.readouterr().err
    )
    assert not (tmp_path / "out.folded").exists()


def test_go_test_that_writes_no_profile_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    quiet = stub(tmp_path / "bin", "go", "exit 0")

    assert run_main(tmp_path, "--", str(quiet), "test", ".") == 5
    assert "no benchmark matched" in capsys.readouterr().err


def test_a_failing_command_returns_its_status_and_converts_nothing(
    tmp_path: Path,
) -> None:
    failing = stub(
        tmp_path / "bin",
        "hot",
        f'cp {FIXTURES / "hot-cpu.pprof"} "$PERFPROOF_PPROF"\nexit 3',
    )

    assert run_main(tmp_path, "--", str(failing)) == 3
    assert not (tmp_path / "out.folded").exists()


def test_a_directory_left_by_an_earlier_run_is_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "out.folded.pprof.d").mkdir()
    marker = tmp_path / "ran.txt"
    fake = stub(tmp_path / "bin", "hot", f"touch {marker}")

    assert run_main(tmp_path, "--", str(fake)) == 7
    assert "exists, from an earlier run" in capsys.readouterr().err
    assert not marker.exists()


def test_a_profile_with_no_samples_is_refused(tmp_path: Path) -> None:
    """A profile whose every sample weighs nothing."""
    fields = real_fields()
    types = [b for n, b in fields if n == 1]
    assert len(types) == 2
    empty = encode(
        [(n, b) for n, b in fields if n not in (2,)]
        + [(2, b"\x0a\x01\x01\x12\x02\x00\x00")]
    )
    crafted = tmp_path / "empty.pprof"
    crafted.write_bytes(gzip.compress(empty))
    fake = stub(tmp_path / "bin", "hot", f'cp {crafted} "$PERFPROOF_PPROF"')

    assert run_main(tmp_path, "--", str(fake)) == 6


def test_a_missing_program_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_main(tmp_path, "--", "no-such-program-here") == 127
    assert "not on the PATH" in capsys.readouterr().err


def test_convert_writes_folded_stacks_from_an_existing_file(
    tmp_path: Path,
) -> None:
    out = tmp_path / "mem.folded"
    code = main(
        [
            "convert",
            "--input",
            str(FIXTURES / "mem.pprof"),
            "--output",
            str(out),
            "--sample-type",
            "alloc_objects",
        ]
    )
    assert code == 0
    expected = read_raw(FIXTURES / "mem-raw.txt", 0)
    assert read_folded(out.read_text()) == expected


def test_convert_names_a_bad_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junk = tmp_path / "junk"
    junk.write_bytes(b"x" * 50)
    code = main(
        [
            "convert",
            "--input",
            str(junk),
            "--output",
            str(tmp_path / "o"),
        ]
    )
    assert code == 6
    assert "perfproof.profilers.go:" in capsys.readouterr().err


# Timeouts kill the whole tree.


def listing() -> str:
    """The arguments of every process on the machine."""
    return subprocess.run(
        ["ps", "-A", "-o", "args="],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout


def test_a_timeout_kills_the_commands_descendants(tmp_path: Path) -> None:
    marker = "47231"
    code = run_main(
        tmp_path, "--timeout", "1", "--", "sh", "-c", f"sleep {marker} & wait"
    )
    assert code == 124
    assert f"sleep {marker}" not in listing()


def test_a_command_that_keeps_forking_leaves_nothing_behind(
    tmp_path: Path,
) -> None:
    marker = "47233"
    loop = f"while true; do sleep {marker} & sleep 0.01; done"
    for i in range(30):
        code = main(
            [
                "record",
                "--output",
                str(tmp_path / f"out{i}.folded"),
                "--timeout",
                "0.2",
                "--",
                "sh",
                "-c",
                loop,
            ]
        )
        assert code == 124
    assert f"sleep {marker}" not in listing()


def test_a_timeout_is_not_a_profile(tmp_path: Path) -> None:
    code = run_main(tmp_path, "--timeout", "0.2", "--", "sleep", "30")
    assert code == 124
    assert not (tmp_path / "out.folded").exists()


# The real toolchain.


def real_go() -> str:
    found = shutil.which("go")
    if found is None:
        pytest.skip("no go on the PATH")
    return found


def go_prog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the fixture module and work in it, as a benchmark does."""
    shutil.copytree(PROG, tmp_path / "prog")
    monkeypatch.chdir(tmp_path / "prog")
    return tmp_path / "prog"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("kind", "bench", "expect", "own"),
    [
        ("cpu", "BenchmarkJoin", "Join", "work.go"),
        ("mem", "BenchmarkJoin", "Join", "work.go"),
        (
            "mutex",
            "BenchmarkContend",
            "Contend.func1",
            "work.go",
        ),
        (
            "block",
            "BenchmarkContend",
            "Contend",
            "work.go",
        ),
    ],
)
def test_record_on_go_test_finds_the_project_function(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    bench: str,
    expect: str,
    own: str,
) -> None:
    go = real_go()
    repo = go_prog(tmp_path, monkeypatch)
    out = tmp_path / "out.folded"
    benchtime = "300x" if bench == "BenchmarkJoin" else "5x"

    code = main(
        [
            "record",
            "--output",
            str(out),
            "--profile",
            kind,
            "--project-root",
            str(repo),
            "--timeout",
            "300",
            "--",
            go,
            "test",
            "-run",
            "^$",
            "-bench",
            f"^{bench}$",
            "-benchtime",
            benchtime,
            ".",
        ]
    )

    assert code == 0
    names = {
        name for stack in read_folded(out.read_text()) for name, _ in stack
    }
    assert expect in names
    own_files = {
        h.file
        for h in summarise_file(out, project_root=repo, top_n=200)
        if not h.is_library
    }
    assert own_files <= {own, "work_test.go"}
    assert not list(repo.glob("*.test")), "go test left its binary here"


@pytest.mark.integration
def test_record_on_a_built_program_uses_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    go = real_go()
    repo = go_prog(tmp_path, monkeypatch)
    subprocess.run(
        [go, "build", "-o", "hot", "./cmd/hot"],
        cwd=repo,
        check=True,
        capture_output=True,
        timeout=300,
    )
    out = tmp_path / "out.folded"

    code = main(
        [
            "record",
            "--output",
            str(out),
            "--project-root",
            str(repo),
            "--own-frames",
            "--",
            str(repo / "hot"),
        ]
    )

    assert code == 0
    ranked = summarise_file(out, project_root=repo, top_n=20)
    # Background runtime work (the scavenger's madvise) can rank first;
    # of the project's own frames, the hot one is Join or Count.
    top = next(h for h in ranked if not h.is_library)
    assert top.function in {"Join", "Count"}
    assert (top.file, top.is_library) == ("work.go", False)


@pytest.mark.integration
def test_record_on_go_run_profiles_the_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    go = real_go()
    repo = go_prog(tmp_path, monkeypatch)
    out = tmp_path / "out.folded"

    code = main(
        [
            "record",
            "--output",
            str(out),
            "--project-root",
            str(repo),
            "--timeout",
            "300",
            "--",
            go,
            "run",
            "./cmd/hot",
        ]
    )

    assert code == 0
    assert "Join (" in out.read_text()


@pytest.mark.integration
def test_a_run_that_times_out_leaves_no_go_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``go run`` starts the program as a child: both are killed."""
    go = real_go()
    repo = go_prog(tmp_path, monkeypatch)
    (repo / "cmd" / "spin").mkdir()
    (repo / "cmd" / "spin" / "main.go").write_text(
        "package main\n\nfunc main() {\n\tfor {\n\t}\n}\n"
    )
    out = tmp_path / "out.folded"

    code = main(
        [
            "record",
            "--output",
            str(out),
            "--timeout",
            "20",
            "--",
            go,
            "run",
            "./cmd/spin",
        ]
    )

    assert code == 124
    assert "/exe/spin" not in listing()


# The Go the playbook gives out is the Go that is tested.

PLAYBOOK = Path(__file__).resolve().parents[2] / "skills/perfproof/go.md"


def playbook_block(marker: str) -> str:
    """Return the playbook's Go code block that holds ``marker``."""
    blocks = re.findall(
        r"```go\n(.*?)```", PLAYBOOK.read_text(), flags=re.DOTALL
    )
    found = [b for b in blocks if marker in b]
    assert len(found) == 1, marker
    return found[0]


def test_the_helper_in_the_playbook_is_the_one_the_fixture_runs() -> None:
    fixture = (PROG / "cmd" / "hot" / "profile.go").read_text()
    assert (
        playbook_block("func startProfile()")
        == fixture[fixture.index("// startProfile writes") :]
    )


@pytest.mark.integration
def test_the_blocks_template_in_the_playbook_prints_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    go = real_go()
    repo = go_prog(tmp_path, monkeypatch)
    (repo / "template_test.go").write_text(
        'package prog\n\nimport (\n\t"fmt"\n\t"os"\n\t"sort"\n'
        '\t"strconv"\n\t"testing"\n\t"time"\n)\n\n'
        "var words = Words(300)\n\n"
        "func work() int { return len(Join(words)) }\n\n"
        + playbook_block("func TestPerfproof")
    )
    done = subprocess.run(
        [go, "test", "-count", "1", "-run", "^TestPerfproof$", "-v", "."],
        cwd=repo,
        env={**os.environ, "PERFPROOF": "1"},
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = [r for r in done.stdout.splitlines() if r.startswith("PERFPROOF")]
    assert len(lines) == 1
    value = json.loads(lines[0].removeprefix("PERFPROOF"))["value"]
    assert 0 < value < 1


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("example.com/prog.Join", "Join"),
        ("example.com/prog.(*T).Run", "T.Run"),
        ("example.com/prog/sub.(*T).Run", "sub.T.Run"),
        ("example.com/prog.Contend.func1", "Contend.func1"),
        ("example.com/prog.Map[go.shape.int]", "Map[go.shape.int]"),
        ("example.com/progx.Join", "example.com/progx.Join"),
        ("runtime.mallocgc", "runtime.mallocgc"),
        ("net/url.(*URL).EscapedPath", "net/url.(*URL).EscapedPath"),
    ],
)
def test_the_projects_function_names_lose_the_module_path(
    name: str, short: str
) -> None:
    mapper = go_module._PathMapper(  # pyright: ignore[reportPrivateUsage]
        PROG, [], "example.com/prog"
    )
    assert mapper.short(name) == short
