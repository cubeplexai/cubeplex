"""E2E: attachment lifecycle — cascade delete + orphan cleanup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _reset_loop_bound_singletons() -> AsyncIterator[None]:
    """Dispose pooled DB connections and reset objectstore/cache singletons.

    The module-level SQLAlchemy engine (cubeplex.db.engine.engine) uses a
    connection pool.  Its connections are bound to the asyncio event loop of
    the test that first opened them.  When pytest-asyncio creates a fresh
    event loop for each test function, stale pooled connections cause
    "Future from different loop" errors in the next test's lifespan startup.

    Disposing the engine here drains the pool and closes every connection
    cleanly, the same way memory_client does it in conftest.  The engine
    reconnects automatically from the new event loop on next use.

    The objectstore and cache singletons are also reset so that test N+1's
    lifespan does not inherit aioboto3 / redis-pool state from test N.
    """
    import cubeplex.cache as _cache
    import cubeplex.objectstore.client as _oc
    from cubeplex.db.engine import engine

    _oc._client = None
    _cache.reset_for_tests()
    yield
    await engine.dispose()
    _oc._client = None
    _cache.reset_for_tests()


async def _make_conv(client: httpx.AsyncClient, ws: str) -> str:
    r = await client.post(f"/api/v1/ws/{ws}/conversations", params={"title": "lc-test"})
    r.raise_for_status()
    return r.json()["id"]


async def _upload(
    client: httpx.AsyncClient, ws: str, conv: str, content: bytes, name: str = "a.png"
) -> dict[str, object]:
    files = {"file": (name, content, "image/png")}
    r = await client.post(f"/api/v1/ws/{ws}/conversations/{conv}/attachments", files=files)
    r.raise_for_status()
    return r.json()


async def test_delete_conversation_cascades_attachments(
    member_client_org_a, sample_png_bytes
) -> None:
    client, ws = member_client_org_a
    conv = await _make_conv(client, ws)
    att = await _upload(client, ws, conv, sample_png_bytes)
    att_id = att["id"]

    # Sanity: attachment listing returns 1 row before delete
    listing = (await client.get(f"/api/v1/ws/{ws}/conversations/{conv}/attachments")).json()
    assert listing["total"] == 1
    assert listing["attachments"][0]["id"] == att_id

    # Delete the conversation
    resp = await client.delete(f"/api/v1/ws/{ws}/conversations/{conv}")
    assert resp.status_code == 204, resp.text

    # Subsequent listing should 404
    resp2 = await client.get(f"/api/v1/ws/{ws}/conversations/{conv}/attachments")
    assert resp2.status_code == 404


async def test_orphan_cleanup_removes_old_pending(member_client_org_a, sample_png_bytes) -> None:
    from sqlalchemy import select as sa_select

    from cubeplex.db.engine import async_session_maker
    from cubeplex.models import Attachment
    from cubeplex.services.attachments import cleanup_orphan_attachments

    client, ws = member_client_org_a
    conv = await _make_conv(client, ws)
    att = await _upload(client, ws, conv, sample_png_bytes)
    att_id = att["id"]

    # Backdate created_at by 2 hours so the row qualifies as orphan
    async with async_session_maker() as session:
        stmt = sa_select(Attachment).where(Attachment.id == att_id)
        row = (await session.execute(stmt)).scalar_one_or_none()
        assert row is not None
        row.created_at = datetime.now(UTC) - timedelta(hours=2)
        await session.commit()

    removed = await cleanup_orphan_attachments()
    assert removed >= 1

    listing = (await client.get(f"/api/v1/ws/{ws}/conversations/{conv}/attachments")).json()
    assert all(a["id"] != att_id for a in listing["attachments"])
