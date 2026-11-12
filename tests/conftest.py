"""Collection rules and shared fixtures for the whole test tree."""

import sys

import pytest

collect_ignore_glob = ["fixtures/find/*"]


@pytest.fixture(autouse=True)
def _outside_a_virtual_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run as if perfproof were not in a virtual environment.

    The suite runs inside one, and `benchmark_environment` takes
    perfproof's own environment off ``PATH``; tests that expect the
    process ``PATH`` to reach a command unchanged need it left alone.
    Tests of that removal set ``sys.prefix`` themselves.
    """
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
