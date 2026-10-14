"""Unchanged conversation polls must be bodyless without hiding control changes."""

from datetime import UTC, datetime

import httpx
import pytest
from cubeloop.providers.base import AssistantMessage, TextContent

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.cache import get_redis
from cubeplex.streams.run_events import create_run


@pytest.mark.asyncio
async def test_bootstrap_revalidation_detects_history_and_run_changes(
    member_client: tuple[httpx.AsyncClient, str],
) -> None:
    client, workspace_id = member_client
    created = await client.post(f"/api/v1/ws/{workspace_id}/conversations")
    created.raise_for_status()
    conversation_id = created.json()["id"]
    url = f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/bootstrap"
    async with shared_checkpointer() as cp:
        await cp.append(
            conversation_id,
            [AssistantMessage(content=[TextContent(text="history " * 200_000)])],
        )

    initial = await client.get(url)
    assert initial.status_code == 200
    assert len(initial.content) > 1_000_000
    etag = initial.headers.get("etag")
    assert etag is not None
    assert "private" in initial.headers["cache-control"]
    unchanged = await client.get(url, headers={"If-None-Match": etag})
    assert unchanged.status_code == 304
    assert unchanged.content == b""
    assert unchanged.headers["etag"] == etag

    async with shared_checkpointer() as cp:
        await cp.append(conversation_id, [AssistantMessage(content=[TextContent(text="new")])])
    changed = await client.get(url, headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag
    assert changed.json()["messages"][-1]["content"][0]["text"] == "new"

    app = client._transport.app  # type: ignore[attr-defined]
    run_id = f"run-{conversation_id}"
    await create_run(
        get_redis(),
        prefix=app.state.redis_key_prefix,
        run_id=run_id,
        conversation_id=conversation_id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )
    control = await client.get(url, headers={"If-None-Match": changed.headers["etag"]})
    assert control.status_code == 200
    assert control.json()["messages"] == changed.json()["messages"]
    assert control.json()["active_run"]["run_id"] == run_id

    strong_tag = control.headers["etag"].removeprefix("W/")
    for validator in (f'"older", {strong_tag}', "*"):
        repeated = await client.get(url, headers={"If-None-Match": validator})
        assert repeated.status_code == 304
        assert repeated.content == b""


@pytest.mark.asyncio
async def test_bootstrap_checks_access_before_returning_not_modified(
    member_client_two_workspaces: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, workspace_id, other_workspace_id = member_client_two_workspaces
    created = await client.post(f"/api/v1/ws/{workspace_id}/conversations")
    created.raise_for_status()
    conversation_id = created.json()["id"]
    path = f"/conversations/{conversation_id}"
    initial = await client.get(f"/api/v1/ws/{workspace_id}{path}/bootstrap")
    etag = initial.headers.get("etag")
    assert etag is not None
    inaccessible = await client.get(
        f"/api/v1/ws/{other_workspace_id}{path}/bootstrap", headers={"If-None-Match": etag}
    )
    assert inaccessible.status_code == 404
    deleted = await client.delete(f"/api/v1/ws/{workspace_id}{path}")
    deleted.raise_for_status()
    missing = await client.get(
        f"/api/v1/ws/{workspace_id}{path}/bootstrap", headers={"If-None-Match": "*"}
    )
    assert missing.status_code == 404
