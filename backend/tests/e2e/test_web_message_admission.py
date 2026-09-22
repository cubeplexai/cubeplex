"""Web message retries keep one durable source, run, and model execution."""

import asyncio
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text
from sqlalchemy import select
from sqlmodel import col

import cubeplex.db as cubeplex_db
from cubeplex.models import ConversationExecutionAdmission
from cubeplex.streams.run_events import _run_events_key, _run_meta_key
from tests.e2e.conftest import DEFAULT_WS_ID

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_web_retry_reuses_durable_run_after_redis_expires(
    memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message([faux_text("one answer")], stop_reason="stop")])

    def build_provider(_snapshot: object, slug: str, **_kwargs: object) -> FauxProvider:
        provider.provider_id = slug
        return provider

    monkeypatch.setattr("cubeplex.llm.builder.build_provider", build_provider)
    created = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "durable web retry"}
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    client_message_id = str(uuid4())
    path = f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}/messages"
    request_body: dict[str, Any] = {
        "client_message_id": client_message_id,
        "content": "run exactly once",
    }

    first = await memory_client.post(path, json=request_body)
    assert first.status_code == 200, first.text
    run_id = first.json()["run_id"]
    app = memory_client._transport.app  # type: ignore[attr-defined]
    manager = app.state.run_manager
    await manager.drain(timeout_seconds=30)
    postprocessing = [*manager._reflection_tasks, *manager._consolidation_tasks]
    if postprocessing:
        await asyncio.gather(*postprocessing)
    calls_after_first_run = provider.call_count
    assert calls_after_first_run >= 1

    async with cubeplex_db.async_session_maker() as session:
        admission = await session.scalar(
            select(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.workspace_id) == DEFAULT_WS_ID,
                col(ConversationExecutionAdmission.source_kind) == "user_message",
                col(ConversationExecutionAdmission.source_id) == f"web:{client_message_id}",
            )
        )
        assert admission is not None
        assert admission.conversation_id == conversation_id
        assert admission.run_id == run_id
        assert admission.run_finished_at is not None
        assert admission.resolved_execution is not None

    await manager._redis.delete(
        _run_meta_key(manager._key_prefix, run_id),
        _run_events_key(manager._key_prefix, run_id),
    )
    retry = await memory_client.post(path, json=request_body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["run_id"] == run_id
    assert provider.call_count == calls_after_first_run

    changed = await memory_client.post(
        path,
        json={**request_body, "content": "changed after the first admission"},
    )
    assert changed.status_code == 409, changed.text
    assert provider.call_count == calls_after_first_run
