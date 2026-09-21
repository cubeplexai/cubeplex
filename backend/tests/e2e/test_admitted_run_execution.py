"""Durable source retries must not run the model twice, even without Redis history."""

import asyncio
import inspect
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from cubeloop.providers.base import AssistantMessage, Message, Model
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text, faux_tool_call
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.checkpointer import close_shared_checkpointer, shared_checkpointer
from cubeplex.config import config
from cubeplex.db.engine import engine
from cubeplex.models import ConversationExecutionAdmission
from cubeplex.services.conversation_execution import ExecutionConflictError, UserMessageIntent
from cubeplex.streams.run_events import create_run, get_run_meta
from cubeplex.streams.run_manager import RunContext, RunManager
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import _SYNC_ENCRYPTION_BACKEND, DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_background_task_reservation import ReservationContext
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

reservation_context = reservation_fixtures.reservation_context


@pytest_asyncio.fixture
async def run_manager() -> AsyncIterator[RunManager]:
    redis_client = Redis.from_url(config.get("redis.url"), decode_responses=True)
    app = FastAPI()
    app.state.encryption_backend = _SYNC_ENCRYPTION_BACKEND
    app.state.sandbox_factory = None
    prefix = f"test-admitted-run:{uuid4()}"
    manager = RunManager(app=app, redis=redis_client, key_prefix=prefix, run_event_ttl_seconds=60)
    try:
        yield manager
    finally:
        await manager.cancel_all()
        extras = [*manager._reflection_tasks, *manager._consolidation_tasks]
        for task in extras:
            task.cancel()
        await asyncio.gather(*extras, return_exceptions=True)
        keys = [key async for key in redis_client.scan_iter(match=f"{prefix}:*")]
        if keys:
            await redis_client.delete(*keys)
        http = getattr(app.state, "_mcp_oauth_http_client", None)
        if http is not None:
            await http.aclose()
        await close_shared_checkpointer()
        await engine.dispose()
        await redis_client.aclose()


@pytest.mark.parametrize("mismatch", ["content", "run_id", "actor", "conversation"])
async def test_run_manager_rejects_changed_admitted_identity_before_claiming_redis(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    mismatch: str,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    admitted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="once"),
        snapshot=snapshot(),
        now=datetime.now(UTC),
    )
    await db_session.commit()
    assert "admission_id" in inspect.signature(run_manager.start_run).parameters
    ctx = RunContext(
        user_id="wrong-actor" if mismatch == "actor" else actor,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=reservation_context.conversation_id,
    )
    with pytest.raises((ExecutionConflictError, LookupError)):
        await run_manager.start_run(
            conversation_id="wrong-conversation"
            if mismatch == "conversation"
            else reservation_context.conversation_id,
            content="different" if mismatch == "content" else "once",
            ctx=ctx,
            run_id="wrong-run" if mismatch == "run_id" else admitted.admission.run_id,
            admission_id=admitted.admission.id,
            llm_snapshot=snapshot(),
        )
    assert not run_manager._tasks
    await db_session.refresh(admitted.admission)
    assert admitted.admission.run_start_token is None


@pytest.mark.parametrize("scenario", ["completed", "stop", "stale_slot", "concurrent", "paused"])
async def test_model_runs_once_with_original_selection_after_default_changes_and_redis_expires(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    initial = snapshot()
    provider_config = initial.providers["provider"].model_copy(deep=True)
    for model in provider_config.models:
        model.context_window = 128_000
        model.max_tokens = 4096
    initial = replace(initial, providers={"provider": provider_config})
    current = replace(initial, model_presets=snapshot("next").model_presets)
    admitted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="one answer only"),
        snapshot=initial,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    assert "admission_id" in inspect.signature(run_manager.start_run).parameters
    provider = FauxProvider(provider_id="provider")
    models: list[str] = []

    def capture_request(payload: dict[str, Any], model: Model) -> None:
        models.append(model.id)

    provider.subscribe_request(capture_request)

    async def model_response(messages: list[Message], model: Model) -> AssistantMessage:
        if scenario == "paused":
            return faux_assistant_message(
                faux_tool_call(
                    "ask_user", {"questions": [{"key": "choice", "prompt": "Continue?"}]}
                ),
                stop_reason="tool_use",
            )
        if scenario == "stop":
            await service(db_session).close_generation(
                conversation_id=reservation_context.conversation_id,
                actor_user_id=actor,
                execution_generation=0,
                now=datetime.now(UTC),
            )
            await db_session.commit()
            return faux_assistant_message(
                faux_tool_call(
                    "write_todos",
                    {"todos": [{"content": "must not execute after Stop", "status": "completed"}]},
                ),
                stop_reason="tool_use",
            )
        return faux_assistant_message([faux_text("done once")], stop_reason="stop")

    provider.set_responses([model_response])
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    ctx = RunContext(
        user_id=actor,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=reservation_context.conversation_id,
        is_group_chat=True,
    )
    kwargs = {
        "conversation_id": reservation_context.conversation_id,
        "content": "one answer only",
        "ctx": ctx,
        "run_id": admitted.admission.run_id,
        "admission_id": admitted.admission.id,
        "llm_snapshot": current,
    }
    try:
        if scenario == "stale_slot":
            assert admitted.admission.run_id is not None
            assert await create_run(
                run_manager._redis,
                prefix=run_manager._key_prefix,
                run_id=admitted.admission.run_id,
                conversation_id=reservation_context.conversation_id,
                status="running",
                started_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
                ttl_seconds=60,
            )
        if scenario == "concurrent":
            run_id, duplicate_id = await asyncio.gather(
                run_manager.start_run(**kwargs), run_manager.start_run(**kwargs)
            )
            assert duplicate_id == run_id
        else:
            run_id = await run_manager.start_run(**kwargs)
        await run_manager.drain(timeout_seconds=30)
        meta = await get_run_meta(run_manager._redis, prefix=run_manager._key_prefix, run_id=run_id)
        assert meta is not None
        expected_status = {"stop": "cancelled", "paused": "paused_hitl"}.get(scenario, "completed")
        assert meta.status == expected_status, meta
        assert provider.call_count == 1
        if scenario == "stop":
            async with shared_checkpointer() as cp:
                checkpoint = await cp.load(reservation_context.conversation_id)
            assert checkpoint is not None and not checkpoint.extra.get("todos")
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_start_requested_at is not None
        assert admitted.admission.run_started_at is not None
        assert (admitted.admission.run_finished_at is None) == (scenario == "paused")
        assert models == ["first"]
        await db_session.rollback()
        keys = [
            key async for key in run_manager._redis.scan_iter(match=f"{run_manager._key_prefix}:*")
        ]
        if keys:
            await run_manager._redis.delete(*keys)
        assert await run_manager.start_run(**kwargs) == run_id
        assert not run_manager._tasks and provider.call_count == 1
        receipt = await db_session.get(ConversationExecutionAdmission, kwargs["admission_id"])
        assert receipt is not None
        assert (receipt.run_finished_at is None) == (scenario == "paused")
    finally:
        await run_manager.cancel_all()
        await db_session.rollback()
        await db_session.execute(
            text("DELETE FROM cubepi_threads WHERE thread_id = :thread"),
            {"thread": reservation_context.conversation_id},
        )
        await db_session.execute(
            text(
                "DELETE FROM billing_llm_events WHERE billing_event_id IN "
                "(SELECT id FROM billing_events WHERE conversation_id = :conversation)"
            ),
            {"conversation": reservation_context.conversation_id},
        )
        for table in ("billing_events", "embedding_jobs", "conversation_chunks"):
            await db_session.execute(
                text(f"DELETE FROM {table} WHERE conversation_id = :conversation"),
                {"conversation": reservation_context.conversation_id},
            )
        await db_session.commit()
