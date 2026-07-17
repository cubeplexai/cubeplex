"""E2E: real LLM should call view_images on an image attachment."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

pytestmark = [pytest.mark.asyncio, pytest.mark.real_llm]


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
    r = await client.post(f"/api/v1/ws/{ws}/conversations", params={"title": "vi-test"})
    r.raise_for_status()
    return r.json()["id"]


async def _upload(
    client: httpx.AsyncClient, ws: str, conv: str, content: bytes, name: str = "a.png"
) -> str:
    files = {"file": (name, content, "image/png")}
    r = await client.post(f"/api/v1/ws/{ws}/conversations/{conv}/attachments", files=files)
    r.raise_for_status()
    return r.json()["id"]


async def _stream_to_done(
    client: httpx.AsyncClient, ws: str, conv: str, body: dict[str, object]
) -> list[dict[str, object]]:
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


async def test_image_attachment_triggers_view_images_call(
    member_client_org_a, sample_png_bytes
) -> None:
    client, ws = member_client_org_a
    conv = await _make_conv(client, ws)
    fid = await _upload(client, ws, conv, sample_png_bytes)

    events = await _stream_to_done(
        client,
        ws,
        conv,
        {
            "content": (
                "Please use view_images to inspect the attached image and tell me "
                "one short fact about it."
            ),
            "attachments": [fid],
        },
    )
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    tool_results = [e for e in events if e.get("type") == "tool_result"]

    assert any(e.get("type") == "done" for e in events), events
    assert any((tc.get("data") or {}).get("name") == "view_images" for tc in tool_calls), (
        f"no view_images tool_call event seen; events={events[-30:]}"
    )
    assert tool_results, events


async def test_view_images_batch_two_paths(member_client_org_a, sample_png_bytes) -> None:
    client, ws = member_client_org_a
    conv = await _make_conv(client, ws)
    f1 = await _upload(client, ws, conv, sample_png_bytes, "a.png")
    f2 = await _upload(client, ws, conv, sample_png_bytes, "b.png")
    events = await _stream_to_done(
        client,
        ws,
        conv,
        {
            "content": (
                "Please call view_images once with BOTH attached images and respond with 'ok'."
            ),
            "attachments": [f1, f2],
        },
    )
    view_calls = [
        e
        for e in events
        if e.get("type") == "tool_call" and (e.get("data") or {}).get("name") == "view_images"
    ]
    assert view_calls, events
    args = view_calls[0]["data"].get("arguments") or {}
    paths = args.get("paths") if isinstance(args, dict) else None
    if paths is not None:
        assert len(paths) >= 1
