"""Search 50 stored sandboxes against a fake remote runtime API.

RemoteSandboxService.search_sandboxes reads the stored sandboxes from
the database and asks the runtime API for each one's state: one request
per sandbox before the reference change, one batch request after. The
fake API is in process (httpx.MockTransport) and, like a recorded
replay, answers every request after a fixed 5 ms, one request at a
time. The database is in-memory SQLite. Prints the statuses so both
trees can be compared for equal output.
"""

import asyncio
import sys
from datetime import UTC, datetime
from typing import Any

import httpx
from openhands.app_server.sandbox.remote_sandbox_service import (
    RemoteSandboxService,
    StoredRemoteSandbox,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

SANDBOXES = 50
SEARCHES = 10
LATENCY_S = 0.005
API = "http://runtime.invalid"


def _runtime(sandbox_id: str) -> dict[str, Any]:
    return {
        "session_id": sandbox_id,
        "session_api_key": f"key-{sandbox_id}",
        "pod_status": "ready",
        "status": "running",
        "url": f"https://{sandbox_id}.runtime.invalid",
    }


class FakeUser:
    """A user context with no user, so every sandbox is visible."""

    async def get_user_id(self) -> None:
        """Return no user id."""
        return


async def main() -> None:
    """Fill the store, then search it repeatedly."""
    lock = asyncio.Lock()

    async def handler(request: httpx.Request) -> httpx.Response:
        async with lock:
            await asyncio.sleep(LATENCY_S)
        path = request.url.path
        if path == "/sessions/batch":
            ids = request.url.params.get_list("ids")
            return httpx.Response(
                200, json={"runtimes": [_runtime(i) for i in ids]}
            )
        return httpx.Response(200, json=_runtime(path.rsplit("/", 1)[-1]))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(StoredRemoteSandbox.__table__.create)
    async with (
        AsyncSession(engine) as session,
        httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client,
    ):
        session.add_all(
            StoredRemoteSandbox(
                id=f"sb{i:03d}",
                created_by_user_id=None,
                sandbox_spec_id="spec",
                created_at=datetime(2025, 1, 1, tzinfo=UTC),
            )
            for i in range(SANDBOXES)
        )
        await session.commit()
        service = RemoteSandboxService(
            sandbox_spec_service=None,  # type: ignore[arg-type]
            api_url=API,
            api_key="local-fake",
            web_url=None,
            resource_factor=1,
            runtime_class=None,
            start_sandbox_timeout=60,
            max_num_sandboxes=SANDBOXES,
            user_context=FakeUser(),  # type: ignore[arg-type]
            httpx_client=client,
            db_session=session,
        )
        page = None
        for _ in range(SEARCHES):
            page = await service.search_sandboxes(limit=SANDBOXES)
    await engine.dispose()
    if page is None:
        sys.exit("no search ran")
    statuses = sorted({str(item.status) for item in page.items})
    sys.stdout.write(f"{len(page.items)} {statuses}\n")


asyncio.run(main())
