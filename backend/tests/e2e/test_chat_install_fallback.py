"""E2E test for the chat-fallback skill-install parser.

When a user sends "install <canonical_name>" as the sole message content,
the conversation route installs the skill and persists a user + assistant
message pair directly to the checkpointer — the agent loop is skipped.
"""

import httpx
import pytest
from redis.asyncio import Redis

from tests.e2e.conftest import DEFAULT_WS_ID


@pytest.mark.asyncio
async def test_user_message_install_command_installs_skill_and_replaces_message(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, ws_id = member_client

    # Create a fresh conversation for this test.
    convo_resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        json={"title": "install-fallback-test"},
    )
    assert convo_resp.status_code == 201
    cid = convo_resp.json()["id"]

    # Send the install command.
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{cid}/messages",
        json={"content": "install deep-research"},
    )
    assert resp.status_code in (200, 201)

    # Fetch conversation messages — the checkpointer now holds a user + assistant pair.
    msgs_resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{cid}/messages")
    assert msgs_resp.status_code == 200
    data = msgs_resp.json()
    messages = data["messages"]

    # The assistant message should contain the install-result note.
    def _extract_text(msg: dict) -> str:
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        # list[{type, text}] from AssistantMessage serialization
        if isinstance(content, list):
            return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        return ""

    all_text = " ".join(_extract_text(m) for m in messages)
    assert "Installed `deep-research`" in all_text, (
        f"Expected install note in messages; got: {all_text!r}"
    )

    # The skill should now be in the workspace enabled set.
    enabled_resp = await client.get(f"/api/v1/ws/{ws_id}/skills", params={"scope": "workspace"})
    assert enabled_resp.status_code == 200
    assert any(s["name"] == "deep-research" for s in enabled_resp.json())


@pytest.mark.asyncio
async def test_install_command_unknown_skill_returns_note_not_agent_run(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    """An `install <nonexistent>` message short-circuits the agent loop and returns
    a "not found" note rather than starting an agent run."""
    client, ws_id = member_client

    convo_resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        json={"title": "install-fallback-not-found"},
    )
    assert convo_resp.status_code == 201
    cid = convo_resp.json()["id"]

    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{cid}/messages",
        json={"content": "install nonexistent-skill-xyz"},
    )
    assert resp.status_code in (200, 201)
    # Chat-fallback returns a one-shot SSE response (not the usual JSON+run_id pair)
    # so the web client renders the assistant note via its normal text_delta/done
    # handlers without trying to subscribe to a fake run_id.
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "Could not find" in resp.text

    msgs_resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{cid}/messages")
    messages = msgs_resp.json()["messages"]

    def _extract_text(msg: dict) -> str:
        content = msg.get("content", "")
        if isinstance(content, list):
            return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        return str(content)

    all_text = " ".join(_extract_text(m) for m in messages)
    assert "Could not find" in all_text


@pytest.mark.asyncio
async def test_install_command_409s_when_a_run_is_already_active(
    memory_client: httpx.AsyncClient,
    redis_client: Redis,
) -> None:
    """The install fallback must honor the same active-run lock the normal path
    enforces: appending a user/assistant pair while another run is writing would
    corrupt history, so it returns 409 instead."""
    from datetime import UTC, datetime

    from cubeplex.streams.run_events import _active_run_key, create_run

    convo = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", json={"title": "install-active-run"}
    )
    assert convo.status_code == 201
    cid = convo.json()["id"]

    # Plant a fresh (non-stale) running active run for this conversation.
    app = memory_client._transport.app  # type: ignore[attr-defined]
    prefix = app.state.redis_key_prefix
    meta = await create_run(
        redis_client,
        prefix=prefix,
        run_id="active-run-1",
        conversation_id=cid,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=120,
    )
    assert meta is not None

    try:
        resp = await memory_client.post(
            f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{cid}/messages",
            json={"content": "install deep-research"},
        )
        assert resp.status_code == 409, resp.text

        # No history side effect: because we claim the run slot before the
        # install/append, a refused command must not have written the
        # user/assistant pair to the checkpointer.
        msgs = await memory_client.get(f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{cid}/messages")
        assert msgs.json()["messages"] == []
    finally:
        await redis_client.delete(_active_run_key(prefix, cid))
