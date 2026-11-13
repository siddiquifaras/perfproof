"""The folded-stack scan is linear in stack depth and unchanged."""
# pyright: reportPrivateUsage=false

import random
from collections.abc import Sequence
from pathlib import Path

import pytest

from perfproof.find import mapping

Key = tuple[str, str | None]
Weights = dict[tuple[Key, str], float]
PREFIXES = ("/r/",)


def _old_scan(
    path: Path, wanted: set[Key], weights: Weights, prefixes: Sequence[str]
) -> None:
    """The quadratic scan used before, kept as the oracle."""
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            stack, _, count = line.rstrip("\n").rpartition(" ")
            try:
                weight = float(count)
            except ValueError:
                continue
            frames = [mapping._frame(t, prefixes) for t in stack.split(";")]
            for index, frame in enumerate(frames):
                if frame not in wanted:
                    continue
                for above in reversed(frames[:index]):
                    if above[1] is not None and mapping._is_test(above[1]):
                        key = (frame, f"{above[1]}::{above[0]}")
                        weights[key] = weights.get(key, 0.0) + weight
                        break


def test_scan_matches_the_quadratic_version_on_random_stacks(
    tmp_path: Path,
) -> None:
    rng = random.Random(7)  # noqa: S311
    pool = [
        "f (/r/app/m.py:3)",
        "g (/r/app/n.py:9)",
        "test_a (/r/tests/test_a.py:1)",
        "test_b (/r/tests/test_b.py:5)",
        "h (/opt/lib/x.py:2)",
        "bare",
        "k (/r/app/m.py:x)",
    ]
    lines = ["junk line with no count"]
    for _ in range(300):
        depth = rng.randint(1, 25)
        stack = ";".join(rng.choice(pool) for _ in range(depth))
        lines.append(f"{stack} {rng.randint(1, 9)}")
    path = tmp_path / "s.folded"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    wanted: set[Key] = {
        ("f", "app/m.py"),
        ("g", "app/n.py"),
        ("test_a", "tests/test_a.py"),
        ("bare", None),
    }
    old: Weights = {}
    new: Weights = {}
    _old_scan(path, wanted, old, PREFIXES)
    mapping._scan(path, wanted, new, PREFIXES)
    assert new == old
    assert old


def test_scan_work_is_linear_in_depth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"test": 0, "frame": 0}
    real_is_test = mapping._is_test
    real_frame = mapping._frame

    def is_test(file: str) -> bool:
        calls["test"] += 1
        return real_is_test(file)

    def frame(text: str, prefixes: Sequence[str]) -> Key:
        calls["frame"] += 1
        return real_frame(text, prefixes)

    monkeypatch.setattr(mapping, "_is_test", is_test)
    monkeypatch.setattr(mapping, "_frame", frame)
    depth = 2000
    stack = ";".join(
        ["test_x (/r/tests/test_a.py:1)"] + ["f (/r/app/m.py:3)"] * depth
    )
    path = tmp_path / "d.folded"
    path.write_text(f"{stack} 1\n", encoding="utf-8")
    weights: Weights = {}
    mapping._scan(path, {("f", "app/m.py")}, weights, PREFIXES)
    assert weights == {(("f", "app/m.py"), "tests/test_a.py::test_x"): depth}
    # The old scan made about depth**2 / 2 test checks and depth parses.
    assert calls["test"] <= 2 * depth
    assert calls["frame"] == 2
