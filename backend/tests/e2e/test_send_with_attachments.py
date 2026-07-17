"""E2E: send messages with attachments + verify history shape."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

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
    r = await client.post(f"/api/v1/ws/{ws}/conversations", params={"title": "send-atta-test"})
    r.raise_for_status()
    return r.json()["id"]


async def _upload(client: httpx.AsyncClient, ws: str, conv: str, content: bytes) -> str:
    files = {"file": ("a.png", content, "image/png")}
    r = await client.post(f"/api/v1/ws/{ws}/conversations/{conv}/attachments", files=files)
    r.raise_for_status()
    return r.json()["id"]


async def _drain_sse_to_done(
    client: httpx.AsyncClient, ws: str, conv: str, body: dict[str, object]
) -> list[dict[str, object]]:
    """Send a message expecting SSE; collect events until 'done' or 'error'."""
    headers = {"accept": "text/event-stream"}
    events: list[dict[str, object]] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws}/conversations/{conv}/messages",
        json=body,
        headers=headers,
    ) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            events.append(payload)
            if payload.get("type") in {"done", "error"}:
                return events
    return events


async def test_send_with_image_attachment_marks_attached_and_returns_history(
    member_client_org_a, sample_png_bytes
) -> None:
    client, ws = member_client_org_a
    conv = await _make_conv(client, ws)
    fid = await _upload(client, ws, conv, sample_png_bytes)

    events = await _drain_sse_to_done(
        client,
        ws,
        conv,
        {"content": "describe this image briefly", "attachments": [fid]},
    )
    assert any(e.get("type") in {"done", "error"} for e in events), events

    listing = (await client.get(f"/api/v1/ws/{ws}/conversations/{conv}/attachments")).json()
    statuses = {a["id"]: a["status"] for a in listing["attachments"]}
    assert statuses[fid] == "attached"

    history = (await client.get(f"/api/v1/ws/{ws}/conversations/{conv}/messages")).json()
    user_msgs = [m for m in history["messages"] if m.get("role") == "user"]
    assert user_msgs, history
    last = user_msgs[-1]
    attachments = last.get("metadata", {}).get("attachments") or []
    assert attachments, last
    assert any(a.get("file_id") == fid for a in attachments)


async def test_send_rejects_attachment_from_other_conversation(
    member_client_org_a, sample_png_bytes
) -> None:
    client, ws = member_client_org_a
    conv_a = await _make_conv(client, ws)
    conv_b = await _make_conv(client, ws)
    fid = await _upload(client, ws, conv_a, sample_png_bytes)

    resp = await client.post(
        f"/api/v1/ws/{ws}/conversations/{conv_b}/messages",
        json={"content": "look", "attachments": [fid]},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error_code"] == "INVALID_ATTACHMENT_REFERENCE"


async def test_send_rejects_too_many_attachments(member_client_org_a, sample_png_bytes) -> None:
    client, ws = member_client_org_a
    conv = await _make_conv(client, ws)
    fids = [await _upload(client, ws, conv, sample_png_bytes) for _ in range(11)]
    resp = await client.post(
        f"/api/v1/ws/{ws}/conversations/{conv}/messages",
        json={"content": "look", "attachments": fids},
    )
    assert resp.status_code == 400
    assert resp.json()["error_code"] == "TOO_MANY_ATTACHMENTS"
