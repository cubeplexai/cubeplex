"""E2E test: full conversation lifecycle with real LLM and MemorySaver."""

import httpx
import pytest

from tests.e2e.conftest import DEFAULT_WS_ID, collect_sse_events

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
@pytest.mark.real_llm
async def test_send_message_returns_sse_stream(memory_client: httpx.AsyncClient) -> None:
    # Create conversation first
    resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "test"}
    )
    assert resp.status_code == 201
    conv_id = resp.json()["id"]

    events = await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
        json_data={"content": "Say the word 'hello' and nothing else."},
    )
    assert len(events) > 0
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
@pytest.mark.real_llm
async def test_stream_contains_text_delta(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "test"}
    )
    conv_id = resp.json()["id"]

    events = await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
        json_data={"content": "Say the word 'hello' and nothing else."},
    )
    text_events = [e for e in events if e["type"] == "text_delta"]
    assert len(text_events) > 0
    full_text = "".join(e["data"]["content"] for e in text_events)
    assert len(full_text) > 0


@pytest.mark.asyncio
@pytest.mark.real_llm
async def test_list_messages_returns_history_after_send(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "test"}
    )
    conv_id = resp.json()["id"]

    await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
        json_data={"content": "Say the word 'hello' and nothing else."},
    )

    resp = await memory_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) >= 2
    assert messages[0]["role"] == "user"
    assert messages[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_send_to_nonexistent_conversation_returns_404(
    memory_client: httpx.AsyncClient,
) -> None:
    resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/nonexistent-id/messages",
        json={"content": "hello"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_send_empty_content_returns_400(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "test"}
    )
    conv_id = resp.json()["id"]

    resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
        json={"content": ""},
    )
    assert resp.status_code == 400
