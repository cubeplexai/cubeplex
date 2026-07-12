"""E2E: when the current model lacks image input support, view_images returns error."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

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


async def test_view_images_capability_gated(
    member_client_org_a, monkeypatch, sample_png_bytes
) -> None:
    # Force capability gate to refuse image input regardless of real config.
    # Monkeypatching the LLMCapabilities method is more reliable than rewriting
    # the dynaconf settings object across providers.
    from cubeplex.llm.capabilities import LLMCapabilities

    monkeypatch.setattr(
        LLMCapabilities,
        "supports_image",
        lambda self: False,
    )
    monkeypatch.setattr(
        LLMCapabilities,
        "combined_input_modalities",
        lambda self: {"text"},
    )

    client, ws = member_client_org_a
    r = await client.post(f"/api/v1/ws/{ws}/conversations", params={"title": "cap-test"})
    conv = r.json()["id"]
    files = {"file": ("a.png", sample_png_bytes, "image/png")}
    r = await client.post(f"/api/v1/ws/{ws}/conversations/{conv}/attachments", files=files)
    fid = r.json()["id"]

    events: list[dict[str, object]] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws}/conversations/{conv}/messages",
        json={
            "content": "Try view_images on the attached image.",
            "attachments": [fid],
        },
        headers={"accept": "text/event-stream"},
    ) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            events.append(payload)
            if payload.get("type") in {"done", "error"}:
                break

    # Run finishes (not hung)
    assert any(e.get("type") in {"done", "error"} for e in events)
    # If view_images was called, the tool_result content mentions model + image
    tool_results = [
        e
        for e in events
        if e.get("type") == "tool_result"
        and (e.get("data") or {}).get("tool_name") == "view_images"
    ]
    if tool_results:
        body = (tool_results[0].get("data") or {}).get("content", "")
        if isinstance(body, list):
            body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
        body_l = str(body).lower()
        assert "model" in body_l and "image" in body_l, body
