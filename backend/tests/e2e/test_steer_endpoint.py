"""E2E: steering an in-flight run injects a user message that reaches history."""

import asyncio

import pytest

from tests.e2e.conftest import collect_sse_events

pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_steer_injects_user_message_into_active_run(member_client) -> None:
    client, ws_id = member_client

    resp = await client.post(f"/api/v1/ws/{ws_id}/conversations", params={"title": "steer-e2e"})
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    async def _run() -> list[dict]:
        return await collect_sse_events(
            client,
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
            {
                "content": "Run a shell command that sleeps for 3 seconds, "
                "then tell me the current directory."
            },
        )

    run_task = asyncio.create_task(_run())

    steered = False
    for _ in range(50):
        b = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap")
        if b.json().get("active_run"):
            s = await client.post(
                f"/api/v1/ws/{ws_id}/conversations/{conv_id}/steer",
                json={"content": "STEER_MARKER_42: also print 'hello from steer'"},
            )
            assert s.status_code == 202
            steered = s.json()["steered"]
            break
        await asyncio.sleep(0.1)
    assert steered is True, "run never became active / agent not registered"

    await run_task

    resp = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages")
    resp.raise_for_status()
    messages = resp.json()["messages"]
    user_texts = [
        block.get("text", "")
        for m in messages
        if m.get("role") == "user"
        for block in m.get("content", [])
    ]
    assert any("STEER_MARKER_42" in t for t in user_texts), (
        f"steered message not found in history user turns: {user_texts!r}"
    )
