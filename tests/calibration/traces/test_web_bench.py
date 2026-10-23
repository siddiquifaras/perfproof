# pyright: basic, reportMissingImports=false
"""The web target retries transient transport errors and counts them."""

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

pytest.importorskip("httpx")
import httpx

sys.path.insert(0, str(Path(__file__).parent / "targets" / "web"))
import web_bench  # pyright: ignore[reportMissingImports]


def _flaky(
    failures: int,
) -> tuple[list[int], Callable[[], Awaitable[httpx.Response]]]:
    calls = [0]

    async def call() -> httpx.Response:
        calls[0] += 1
        if calls[0] <= failures:
            raise httpx.ReadError("boom")
        return httpx.Response(200)

    return calls, call


def test_a_transient_error_is_retried_and_counted() -> None:
    calls, call = _flaky(2)
    retries = [0]
    r = asyncio.run(web_bench.with_retries(call, retries))
    assert r.status_code == 200
    assert retries == [2]
    assert calls == [3]


def test_retries_are_bounded_and_the_last_error_is_raised() -> None:
    calls, call = _flaky(10)
    retries = [0]
    with pytest.raises(httpx.ReadError):
        asyncio.run(web_bench.with_retries(call, retries))
    assert calls == [web_bench.MAX_ATTEMPTS]
    assert retries == [web_bench.MAX_ATTEMPTS - 1]


def test_no_retry_on_success() -> None:
    _, call = _flaky(0)
    retries = [0]
    asyncio.run(web_bench.with_retries(call, retries))
    assert retries == [0]
