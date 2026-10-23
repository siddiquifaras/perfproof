# pyright: basic, reportMissingImports=false
"""One web round: start the server, burst requests at it, stop it.

Prints ``PERFPROOF {...}`` with the burst's latency percentiles and
throughput. Every run starts a fresh server process and a fresh load
generator, so the whole command is one fresh-process launch.
"""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable

import httpx

REQUESTS = 2000
CONCURRENCY = 16
CPU_ROUNDS = 500
MAX_ATTEMPTS = 3
BODY = {"a": 1, "b": [1, 2, 3], "c": {"d": "text"}, "e": None}


def free_port() -> int:
    """Return a port that was free a moment ago."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_healthy(port: int, deadline: float) -> None:
    """Poll the server until it accepts a connection."""
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.2):
                return
        except OSError:
            time.sleep(0.01)
    msg = "server did not come up"
    raise RuntimeError(msg)


async def with_retries(
    call: Callable[[], Awaitable[httpx.Response]], retries: list[int]
) -> httpx.Response:
    """Run ``call``, retrying a transport error ``MAX_ATTEMPTS`` times.

    A transient connection error under load is a property of the run,
    not a reason to lose it, so each retry is counted in ``retries[0]``
    and reported with the metrics. The last error is raised when every
    attempt fails, so a broken server still fails the round.
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            return await call()
        except httpx.TransportError:
            if attempt == MAX_ATTEMPTS - 1:
                raise
            retries[0] += 1
    msg = "unreachable"
    raise AssertionError(msg)


async def burst(port: int) -> tuple[list[float], int]:
    """Fire the fixed request mix.

    Returns the latencies in seconds (retries included) and the number
    of retries.
    """
    base = f"http://127.0.0.1:{port}"
    latencies: list[float] = [0.0] * REQUESTS
    retries = [0]
    queue: asyncio.Queue[int] = asyncio.Queue()
    for i in range(REQUESTS):
        queue.put_nowait(i)

    async def worker(client: httpx.AsyncClient) -> None:
        while True:
            try:
                i = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            kind = i % 3
            start = time.perf_counter()
            if kind == 0:
                r = await with_retries(
                    lambda: client.post(f"{base}/echo", json=BODY), retries
                )
            elif kind == 1:
                r = await with_retries(
                    lambda: client.get(f"{base}/cpu/{CPU_ROUNDS}"), retries
                )
            else:
                r = await with_retries(
                    lambda i=i: client.get(f"{base}/db/{i}"), retries
                )
            r.raise_for_status()
            latencies[i] = time.perf_counter() - start

    limits = httpx.Limits(max_connections=CONCURRENCY)
    async with httpx.AsyncClient(limits=limits, timeout=30) as client:
        await asyncio.gather(*(worker(client) for _ in range(CONCURRENCY)))
    return latencies, retries[0]


def main() -> int:
    """Run one round and print its metrics."""
    port = free_port()
    t0 = time.perf_counter()
    server = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app:app",
            "--host", "127.0.0.1", "--port", str(port),
            "--log-level", "warning",
        ],
        cwd=os.getcwd(),
        stdin=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        wait_healthy(port, time.monotonic() + 30)
        t1 = time.perf_counter()
        lat, retried = asyncio.run(burst(port))
        t2 = time.perf_counter()
    finally:
        server.send_signal(signal.SIGTERM)
        server.wait(timeout=30)
    lat.sort()
    metrics = {
        "value": t2 - t1,
        "startup_s": t1 - t0,
        "p50_ms": 1000 * lat[len(lat) // 2],
        "p99_ms": 1000 * lat[int(len(lat) * 0.99)],
        "rps": REQUESTS / (t2 - t1),
        "retries": retried,
    }
    sys.stdout.write(f"PERFPROOF {json.dumps(metrics)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
