"""Calls the C extension the test builds first."""

import fastsum  # pyright: ignore[reportMissingImports]


def test_spin() -> None:
    """Spin long enough for a sampling profiler to see it."""
    assert fastsum.spin(2_000_000_000) >= 0  # pyright: ignore[reportUnknownMemberType]
