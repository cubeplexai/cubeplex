"""E2E test: SSE stream format validation."""

import json

import httpx
import pytest


@pytest.mark.asyncio
async def test_sse_response_content_type(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    async with memory_client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_every_event_is_valid_json(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    async with memory_client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                assert "type" in payload
                assert "timestamp" in payload


@pytest.mark.asyncio
async def test_stream_always_ends_with_done(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    events = []
    async with memory_client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_agent_id_is_null_for_main_agent(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post("/api/v1/conversations", params={"title": "test"})
    conv_id = resp.json()["id"]

    events = []
    async with memory_client.stream(
        "POST",
        f"/api/v1/conversations/{conv_id}/messages",
        json={"content": "Say hi."},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert len(text_events) > 0
    for e in text_events:
        assert e.get("agent_id") is None
