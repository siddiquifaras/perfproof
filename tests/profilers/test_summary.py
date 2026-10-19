import json
import re
from pathlib import Path
from typing import Any, cast

import pytest

from perfproof.profilers.folded import summarise_file
from perfproof.profilers.summary import (
    BRIEF_BYTES,
    amdahl_cap,
    brief,
    summarise_profile,
)
from perfproof.schema import Hotspot

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DIRECTION_WORDS = re.compile(
    r"\b(faster|slower|speedups?|gains?|wins?|better|worse|regress\w*|"
    r"improve\w*|slows?|benefit|improvement)\b",
    re.IGNORECASE,
)


def hotspot(
    function: str = "f",
    self_fraction: float = 0.2,
    total_fraction: float | None = 0.5,
    file: str | None = "a.py",
    line: int | None = 3,
) -> Hotspot:
    return Hotspot(
        function=function,
        file=file,
        line=line if file else None,
        frame_kind="python",
        is_library=False,
        self_fraction=self_fraction,
        total_fraction=total_fraction,
    )


def summary(hotspots: list[Hotspot], **overrides: Any) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "basis": "wall",
        "weight_unit": "microseconds",
        "wall_seconds": 2.0,
        "cpu_seconds": 1.0,
    }
    arguments.update(overrides)
    return cast("dict[str, Any]", summarise_profile(hotspots, **arguments))


@pytest.mark.parametrize(
    ("share", "cap"), [(0.0, 1.0), (0.5, 2.0), (0.75, 4.0), (0.9, 10.0)]
)
def test_amdahl_cap_is_one_over_one_minus_the_total_share(
    share: float, cap: float
) -> None:
    assert amdahl_cap(share) == pytest.approx(cap)


@pytest.mark.parametrize("share", [None, 1.0])
def test_cap_is_unknown_without_a_total_or_for_the_whole_run(
    share: float | None,
) -> None:
    assert amdahl_cap(share) is None


def test_cap_uses_the_total_fraction_not_the_self_fraction() -> None:
    frame = summary([hotspot(self_fraction=0.1, total_fraction=0.8)])[
        "frames"
    ][0]
    assert frame["cap"] == pytest.approx(5.0)
    assert frame["self_fraction"] == 0.1


def test_cap_above_the_bar_follows_min_effect() -> None:
    frames = summary(
        [
            hotspot("big", total_fraction=0.5),
            hotspot("tiny", self_fraction=0.01, total_fraction=0.02),
        ],
        min_effect=0.03,
    )["frames"]
    assert [f["cap_above_bar"] for f in frames] == [True, False]


def test_cap_above_the_bar_is_left_out_when_the_bar_is_unknown() -> None:
    assert "cap_above_bar" not in summary([hotspot()])["frames"][0]


def test_waiting_share_is_the_gap_between_wall_and_cpu() -> None:
    time = summary([hotspot()], wall_seconds=2.0, cpu_seconds=0.5)["time"]
    assert time["waiting_share_at_least"] == pytest.approx(0.75)
    assert time["cpu_over_wall"] == pytest.approx(0.25)
    assert "waiting" in time["reading"]


def test_cpu_above_wall_shows_no_waiting() -> None:
    time = summary([hotspot()], wall_seconds=1.0, cpu_seconds=3.0)["time"]
    assert time["waiting_share_at_least"] is None
    assert "cannot show waiting" in time["reading"]


def test_machine_loaded_compares_load_with_cpus() -> None:
    busy = summary([hotspot()], load_average=12.0, cpu_count=10)["time"]
    idle = summary([hotspot()], load_average=2.0, cpu_count=10)["time"]
    unknown = summary([hotspot()])["time"]
    assert busy["machine_loaded"] is True
    assert idle["machine_loaded"] is False
    assert "machine_loaded" not in unknown


def test_top_n_keeps_the_leading_frames_and_counts_the_rest() -> None:
    frames = [hotspot(f"f{i}", 0.1, 0.1) for i in range(8)]
    result = summary(frames, top_n=3)
    assert [f["function"] for f in result["frames"]] == ["f0", "f1", "f2"]
    assert result["frames_omitted"] == 5


@pytest.mark.parametrize(
    "arguments",
    [
        {"top_n": 0},
        {"wall_seconds": 0.0},
        {"wall_seconds": float("inf")},
        {"cpu_seconds": -1.0},
    ],
)
def test_bad_arguments_are_refused(arguments: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"top_n|wall time"):
        summary([hotspot()], **arguments)


def test_labels_name_a_cap_as_a_bound_and_a_share_as_an_estimate() -> None:
    labels = summary([hotspot()])["labels"]
    assert labels["cap"].startswith("bound")
    assert labels["share"].startswith("estimate")


def test_nothing_in_a_summary_gives_a_direction() -> None:
    text = json.dumps(
        summary(
            [hotspot()], wall_seconds=2.0, cpu_seconds=0.5, min_effect=0.03
        )
    )
    assert DIRECTION_WORDS.search(text) is None
    text = json.dumps(summary([hotspot()], wall_seconds=1.0, cpu_seconds=3.0))
    assert DIRECTION_WORDS.search(text) is None


def compact(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def test_brief_of_a_typical_summary_fits_the_cap_untouched() -> None:
    full = summarise_profile(
        [hotspot(f"f{i}", 0.1, 0.2) for i in range(5)],
        basis="wall",
        weight_unit="samples",
        wall_seconds=1.0,
        cpu_seconds=0.9,
        load_average=3.0,
        cpu_count=10,
        min_effect=0.03,
    )
    assert brief(full) == full
    assert len(compact(full)) <= BRIEF_BYTES


def test_brief_drops_trailing_frames_to_fit_and_says_how_many() -> None:
    long_name = "x" * 1_000
    frames = [hotspot(long_name + str(i), 0.1, 0.1) for i in range(5)]
    full = summarise_profile(
        frames,
        basis="wall",
        weight_unit="samples",
        wall_seconds=1.0,
        cpu_seconds=0.9,
        top_n=5,
    )
    cut = cast("dict[str, Any]", brief(full, max_bytes=1_500))
    kept = len(cut["frames"])
    assert 1 <= kept < 5
    assert cut["frames_omitted"] == 5 - kept
    assert len(compact(cut)) <= 1_500
    assert [f["function"][:1] for f in cut["frames"]] == ["x"] * kept


def test_a_name_is_cut_so_even_hostile_input_fits_the_default_cap() -> None:
    frames = [hotspot("y" * 50_000, file="z" * 50_000) for _ in range(20)]
    cut = brief(
        summarise_profile(
            frames,
            basis="unknown",
            weight_unit="unknown",
            wall_seconds=1.0,
            cpu_seconds=1.0,
            top_n=20,
        )
    )
    assert len(compact(cut)) <= BRIEF_BYTES


def test_brief_refuses_a_cap_that_cannot_hold_one_frame() -> None:
    full = summarise_profile(
        [hotspot()],
        basis="wall",
        weight_unit="samples",
        wall_seconds=1.0,
        cpu_seconds=1.0,
    )
    with pytest.raises(ValueError, match="exceed 10 bytes"):
        brief(full, max_bytes=10)


def test_a_real_py_spy_capture_gives_the_known_hotspot_its_cap() -> None:
    top = summarise_file(
        FIXTURES / "py-spy" / "rolling.txt",
        project_root=Path("/srv/rolling"),
        top_n=3,
    )
    frames = cast(
        "list[dict[str, Any]]",
        summarise_profile(
            top,
            basis="active",
            weight_unit="samples",
            wall_seconds=2.0,
            cpu_seconds=1.9,
        )["frames"],
    )
    assert frames[0]["function"] == "rolling_sum"
    share = top[0].total_fraction
    assert share is not None
    assert frames[0]["cap"] == pytest.approx(1 / (1 - share))
