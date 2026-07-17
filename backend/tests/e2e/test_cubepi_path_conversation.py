"""E2E smoke test for the cubepi runtime path (M1.6).

Sends one conversation turn through cubeplex's public API with the cubepi
runtime active. Verifies the SSE stream emits at least one text_delta and a
final done — confirming end-to-end wiring through:

  ProviderConfig → cubepi.AnthropicProvider/OpenAIProvider →
  cubepi.Agent (no cubeplex middleware in M1) → AgentEvent stream →
  convert_agent_event_to_sse → cubeplex SSE.

The test environment (config.test.yaml) already sets agents.runtime = "cubepi"
so no app.state override is needed here — the route is active for all E2E
tests in this worktree.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import collect_sse_events

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_cubepi_path_round_trip_one_turn(
    member_client,  # type: ignore[no-untyped-def]
) -> None:
    """POST one message, consume SSE, assert text_delta + done, no errors."""
    client, ws_id = member_client

    # 1. Create a conversation
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "cubepi-m1-smoke"},
    )
    assert resp.status_code == 201, f"conversation creation failed: {resp.text}"
    conv_id = resp.json()["id"]

    # 2. POST a message and collect SSE events
    events = await collect_sse_events(
        client,
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json_data={"content": "Say hello in one word."},
    )

    # 3. Assertions
    seen_types = [e.get("type") for e in events]

    # "done" must be present (canonical stream terminator)
    assert "done" in seen_types, f"no 'done' event in stream; seen: {seen_types!r}"

    # At least one text_delta (model produced some text)
    text_deltas = [t for t in seen_types if t == "text_delta"]
    assert len(text_deltas) > 0, f"no text_delta events in stream; seen: {seen_types!r}"

    # No error events
    errors = [t for t in seen_types if t == "error"]
    assert errors == [], f"unexpected error events in stream; seen: {seen_types!r}"
