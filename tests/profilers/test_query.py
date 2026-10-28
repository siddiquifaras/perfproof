import json
import shutil
import tracemalloc
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.commands._profile_show import show
from perfproof.profilers.folded import rank_file
from perfproof.profilers.names import names_match
from perfproof.profilers.query import (
    FunctionQuery,
    query_file,
)
from perfproof.schema import Hotspot

FIXTURES = Path(__file__).parent.parent / "fixtures"
NATIVE = FIXTURES / "py-spy" / "workload-native.txt"
LONG = FIXTURES / "py-spy" / "workload-native-long.txt"
ROOT = Path("/srv/project")
"""Where the workload was mounted when py-spy recorded the fixtures."""
EVERYTHING = 1_000_000


def query(pattern: str, path: Path = NATIVE) -> FunctionQuery:
    return query_file(path, project_root=ROOT, pattern=pattern)


def test_finds_a_function_the_printed_top_list_leaves_out() -> None:
    printed = rank_file(NATIVE, project_root=ROOT, top_n=5)
    assert "encode" not in {h.function for h in printed.by_self}
    assert "encode" not in {h.function for h in printed.by_total}
    found = query("encode")
    assert {m.hotspot.function for m in found.matches} == {"encode"}
    (match,) = [m for m in found.matches if not m.hotspot.is_library]
    assert match.hotspot.file == "app/kernels.py"
    assert {m.hotspot.file for m in found.matches} == {
        "app/kernels.py",
        "/usr/local/lib/python3.12/json/encoder.py",
    }
    assert match.hotspot.self_fraction == 0.0
    assert match.hotspot.total_fraction == pytest.approx(0.041, abs=5e-4)
    assert found.samples == 512
    assert match.total_samples == 21
    assert match.self_samples == 0


def test_counts_match_the_folded_file() -> None:
    found = query("slow_sum").matches[0]
    lines = NATIVE.read_text(encoding="utf-8").splitlines()
    total = sum(
        int(line.rpartition(" ")[2]) for line in lines if "slow_sum (" in line
    )
    own = sum(
        int(line.rpartition(" ")[2])
        for line in lines
        if line.rpartition(" ")[0].split(";")[-1].startswith("slow_sum (")
    )
    assert found.total_samples == total
    assert found.self_samples == own


def test_a_function_with_no_samples_has_no_matches() -> None:
    found = query("createChildLogger")
    assert found.matches == ()
    assert found.omitted == 0
    assert found.samples == 512


def test_a_name_must_match_whole_not_by_suffix() -> None:
    assert query("sum").matches == ()
    assert query("slow_su").matches == ()


def test_an_owner_in_the_name_must_be_the_printed_owner() -> None:
    assert query("slow_sum").matches
    assert not names_match("slow_sum", "kernels.slow_sum")
    assert query("kernels.slow_sum").match == "file"
    assert query("slow_sum").match == "exact"


def test_a_frame_the_ranking_hides_can_still_be_found() -> None:
    hidden = {
        h.function
        for h in rank_file(
            NATIVE, project_root=ROOT, top_n=EVERYTHING
        ).by_total
    }
    every = rank_file(
        NATIVE, project_root=ROOT, top_n=EVERYTHING, keep_all=True
    ).by_total
    assert "<module>" not in hidden
    assert any(
        h.function == "<module>" and h.total_fraction == 1.0 for h in every
    )


def test_matches_are_limited_and_the_rest_counted() -> None:
    everything = query("fib")
    limited = query_file(NATIVE, project_root=ROOT, pattern="fib", limit=1)
    assert len(limited.matches) == 1
    assert limited.omitted == len(everything.matches) - 1
    assert limited.matches[0] == everything.matches[0]


def test_a_pattern_that_names_nothing_is_refused() -> None:
    with pytest.raises(ValueError, match="does not name a function"):
        query("  ")
    with pytest.raises(ValueError, match="at least 1"):
        query_file(NATIVE, project_root=ROOT, pattern="fib", limit=0)


def above(bar: float, limit: int = 50) -> tuple[Hotspot, ...]:
    return rank_file(
        NATIVE, project_root=ROOT, top_n=5, bar=bar, bar_limit=limit
    ).above_bar


def test_project_frames_above_the_bar_reach_past_the_top_list() -> None:
    printed = rank_file(NATIVE, project_root=ROOT, top_n=5)
    listed = above(0.03)
    assert "encode" in [h.function for h in listed]
    assert "encode" not in {h.function for h in printed.by_total}
    assert all(not h.is_library for h in listed)
    assert all((h.total_fraction or 0.0) >= 0.03 for h in listed)
    shares = [h.total_fraction or 0.0 for h in listed]
    assert shares == sorted(shares, reverse=True)


def test_library_frames_above_the_bar_are_not_listed() -> None:
    full = rank_file(NATIVE, project_root=ROOT, top_n=EVERYTHING)
    heavy_library = [
        h
        for h in full.by_total
        if h.is_library and h.total_fraction and h.total_fraction >= 0.03
    ]
    assert heavy_library
    assert not set(above(0.03)) & set(heavy_library)


def test_a_frame_below_the_bar_is_left_out() -> None:
    names = {h.function for h in above(0.05)}
    assert "encode" not in names
    assert "slow_sum" in names


def test_no_bar_means_no_list() -> None:
    assert rank_file(NATIVE, project_root=ROOT).above_bar == ()


def test_the_list_is_bounded() -> None:
    assert len(above(0.0, limit=3)) == 3
    with pytest.raises(ValueError, match="at least 1"):
        above(0.0, limit=0)


def big_file(directory: Path, times: int) -> Path:
    """Write a real capture several times over, one line at a time."""
    path = directory / f"repeated-{times}.txt"
    text = LONG.read_text(encoding="utf-8")
    with path.open("w", encoding="utf-8") as out:
        for _ in range(times):
            out.write(text)
    return path


def peak_bytes(path: Path) -> tuple[int, FunctionQuery]:
    tracemalloc.start()
    try:
        found = query("encode", path)
        return tracemalloc.get_traced_memory()[1], found
    finally:
        tracemalloc.stop()


def test_a_query_on_a_large_file_runs_in_bounded_memory(
    tmp_path: Path,
) -> None:
    times = 50
    once_peak, once = peak_bytes(big_file(tmp_path, 1))
    many_file = big_file(tmp_path, times)
    many_peak, many = peak_bytes(many_file)
    assert many_file.stat().st_size > 20_000_000
    assert many_peak < once_peak * 1.1 + 256 * 1024
    assert many.samples == once.samples * times
    assert many.matches[0].hotspot.total_fraction == pytest.approx(
        once.matches[0].hotspot.total_fraction, rel=1e-12
    )
    assert (
        many.matches[0].total_samples == once.matches[0].total_samples * times
    )


PYSPY = FIXTURES / "py-spy" / "workload.txt"


def test_an_owner_the_frame_does_not_carry_matches_by_file() -> None:
    bare = query_file(PYSPY, project_root=ROOT, pattern="fib")
    assert bare.match == "exact"
    assert bare.note is None
    owned = query_file(PYSPY, project_root=ROOT, pattern="kernels.fib")
    assert owned.match == "file"
    assert owned.note is not None
    assert owned.matches == bare.matches
    assert bare.matches[0].total_samples > 0


def test_a_wrong_owner_falls_back_to_the_bare_name() -> None:
    bare = query_file(PYSPY, project_root=ROOT, pattern="fib")
    other = query_file(PYSPY, project_root=ROOT, pattern="other.fib")
    assert other.match == "bare_name"
    assert other.note is not None
    assert other.matches == bare.matches


def test_an_absent_name_has_no_match_kind() -> None:
    gone = query_file(PYSPY, project_root=ROOT, pattern="kernels.nothing")
    assert gone.matches == ()
    assert (gone.match, gone.note) == (None, None)


def test_the_whole_profile_has_no_finite_cap_but_is_above_the_bar(
    tmp_path: Path,
) -> None:
    run = (
        tmp_path / ".perfproof" / "profiles" / "w" / "20261028T000000Z-aaaaaa"
    )
    run.mkdir(parents=True)
    shutil.copyfile(PYSPY, run / "folded.txt")
    (run / "result.json").write_text(
        json.dumps({"folded": str(run / "folded.txt"), "min_effect": 0.03}),
        encoding="utf-8",
    )
    result = show(tmp_path, "w", None, "<module>").result
    functions = cast("list[dict[str, Any]]", result["functions"])
    top = functions[0]
    assert top["total_fraction"] == 1.0
    assert top["cap"] is None
    assert top["cap_above_bar"] is True
    owned = show(tmp_path, "w", None, "kernels.fib").result
    assert (owned["found"], owned["match"]) == (True, "file")
    assert owned["note"]
