"""E2E: cubepi path writes via POST then reads via GET (M4.1).

Validates the round-trip after Codex#84 review #2: the cubepi path's
PostgresCheckpointer writes must be visible to GET /messages."""

import pytest

from tests.e2e.conftest import collect_sse_events

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_cubepi_history_round_trip(member_client) -> None:
    client, ws_id = member_client

    # 1. Create conversation
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations", params={"title": "cubepi-history-round-trip"}
    )
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    # 2. POST a message and consume the full SSE stream (cubepi writes via
    # PostgresCheckpointer; receiving the "done" event guarantees the write
    # to cubepi_messages is complete before we issue the GET).
    events = await collect_sse_events(
        client,
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json_data={"content": "Say hello in one word."},
    )
    seen_types = [e.get("type") for e in events]
    assert "done" in seen_types, f"no 'done' event in stream; seen: {seen_types!r}"
    assert "error" not in seen_types, f"error events in stream: {seen_types!r}"

    # 3. GET history — should return 2 messages (user + assistant)
    resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages")
    resp.raise_for_status()
    body = resp.json()
    messages = body["messages"]
    assert len(messages) >= 2, f"expected ≥2 messages, got {body!r}"
    assert body["has_more"] is False, f"two-message history shouldn't paginate: {body!r}"
    roles = [m.get("role") for m in messages]
    assert "user" in roles, f"no user message in history: {roles}"
    assert "assistant" in roles, f"no assistant message in history: {roles}"

    # 4. Wire-shape contract: every message uses cubepi's native pydantic dump.
    # `content` is a list of typed blocks, not a flat string; assistant carries
    # `usage` and `stop_reason` at the top level.
    for msg in messages:
        assert isinstance(msg.get("content"), list), (
            f"content must be a block list, got {type(msg.get('content')).__name__}: {msg!r}"
        )
        for block in msg["content"]:
            assert "type" in block, f"content block missing type discriminator: {block!r}"

    assistant_msg = next(m for m in messages if m["role"] == "assistant")
    assert "usage" in assistant_msg, f"assistant message missing usage: {assistant_msg!r}"
    assert "stop_reason" in assistant_msg, f"assistant missing stop_reason: {assistant_msg!r}"
