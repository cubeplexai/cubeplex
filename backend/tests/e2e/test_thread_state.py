"""E2E test: multi-turn conversation state persistence."""

import httpx
import pytest

from tests.e2e.conftest import DEFAULT_WS_ID, collect_sse_events

pytestmark = [pytest.mark.e2e, pytest.mark.real_llm]


@pytest.mark.asyncio
async def test_multi_turn_context_is_retained(memory_client: httpx.AsyncClient) -> None:
    """Agent should remember context from previous turns."""
    resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "test"}
    )
    conv_id = resp.json()["id"]

    await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
        json_data={"content": "My name is TestUser. Just acknowledge this."},
    )

    events = await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
        json_data={"content": "What is my name? Reply with just the name."},
    )

    text_events = [e for e in events if e["type"] == "text_delta"]
    full_text = "".join(e["data"]["content"] for e in text_events)
    assert "TestUser" in full_text


@pytest.mark.asyncio
async def test_message_count_after_two_turns(memory_client: httpx.AsyncClient) -> None:
    resp = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "test"}
    )
    conv_id = resp.json()["id"]

    await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
        json_data={"content": "First message."},
    )
    await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages",
        json_data={"content": "Second message."},
    )

    resp = await memory_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv_id}/messages")
    messages = resp.json()["messages"]
    assert len(messages) >= 4
    user_msgs = [m for m in messages if m["role"] == "user"]
    assert len(user_msgs) == 2


@pytest.mark.asyncio
async def test_separate_conversations_have_independent_state(
    memory_client: httpx.AsyncClient,
) -> None:
    resp1 = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "conv1"}
    )
    resp2 = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "conv2"}
    )
    conv1_id = resp1.json()["id"]
    conv2_id = resp2.json()["id"]

    await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv1_id}/messages",
        json_data={"content": "My secret word is ALPHA."},
    )

    events = await collect_sse_events(
        memory_client,
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conv2_id}/messages",
        json_data={"content": "Do you know my secret word? Just say no if you don't."},
    )

    text = "".join(e["data"]["content"] for e in events if e["type"] == "text_delta")
    assert "ALPHA" not in text
