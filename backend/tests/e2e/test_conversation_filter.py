"""E2E: empty conversations are hidden from the list endpoint."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _reset_loop_bound_singletons() -> AsyncIterator[None]:
    import cubeplex.cache as _cache
    import cubeplex.objectstore.client as _oc
    from cubeplex.db.engine import engine

    _oc._client = None
    _cache.reset_for_tests()
    yield
    await engine.dispose()
    _oc._client = None
    _cache.reset_for_tests()


async def _make_conv(client: httpx.AsyncClient, ws: str, title: str, *, draft: bool = False) -> str:
    params: dict[str, object] = {"title": title}
    if draft:
        params["draft"] = "true"
    r = await client.post(f"/api/v1/ws/{ws}/conversations", params=params)
    r.raise_for_status()
    return r.json()["id"]


async def _drain_to_done(client: httpx.AsyncClient, ws: str, conv: str, content: str) -> None:
    headers = {"accept": "text/event-stream"}
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws}/conversations/{conv}/messages",
        json={"content": content},
        headers=headers,
    ) as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[len("data: ") :])
            if payload.get("type") in {"done", "error"}:
                return


async def test_empty_draft_conversation_not_listed(member_client_org_a) -> None:
    client, ws = member_client_org_a
    convo_id = await _make_conv(client, ws, "draft", draft=True)

    listed = (await client.get(f"/api/v1/ws/{ws}/conversations")).json()
    ids = [c["id"] for c in listed["conversations"]]
    assert convo_id not in ids


async def test_explicit_conversation_listed_immediately(member_client_org_a) -> None:
    """Explicit POSTs (no draft flag) appear in the list right away — only
    eager-create drafts are hidden until the user actually sends a message."""
    client, ws = member_client_org_a
    convo_id = await _make_conv(client, ws, "explicit")

    listed = (await client.get(f"/api/v1/ws/{ws}/conversations")).json()
    ids = [c["id"] for c in listed["conversations"]]
    assert convo_id in ids


@pytest.mark.real_llm
async def test_draft_conversation_listed_after_first_message(member_client_org_a) -> None:
    client, ws = member_client_org_a
    convo_id = await _make_conv(client, ws, "hi", draft=True)
    await _drain_to_done(client, ws, convo_id, "hello")

    listed = (await client.get(f"/api/v1/ws/{ws}/conversations")).json()
    ids = [c["id"] for c in listed["conversations"]]
    assert convo_id in ids
