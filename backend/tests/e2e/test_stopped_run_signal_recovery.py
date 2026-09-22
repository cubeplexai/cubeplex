"""Losing a Stop signal must not leave a live model request running indefinitely."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cubeloop.providers.base import AssistantMessage, Message, Model
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.services.conversation_execution import UserMessageIntent
from cubeplex.streams.run_events import get_active_run, get_run_meta
from cubeplex.streams.run_manager import RunContext, RunManager
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_admitted_run_execution import cleanup_run_rows
from tests.e2e.test_background_task_reservation import ReservationContext
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

reservation_context = reservation_fixtures.reservation_context
run_manager = run_fixtures.run_manager


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("all_work", [False, True])
async def test_recovery_resends_stop_to_live_worker_before_checkpoint_completion(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
    remote: bool,
    all_work: bool,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    conversation_id = reservation_context.conversation_id
    current = snapshot()
    provider_config = current.providers["provider"].model_copy(deep=True)
    for model in provider_config.models:
        model.context_window = 128_000
        model.max_tokens = 4096
    current = replace(current, providers={"provider": provider_config})
    admitted = await service(db_session).admit_user_message(
        conversation_id=conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="a slow model response"),
        snapshot=current,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow_response(_messages: list[Message], _model: Model) -> AssistantMessage:
        entered.set()
        await release.wait()
        return faux_assistant_message([faux_text("too late")], stop_reason="stop")

    provider = FauxProvider(provider_id="provider")
    provider.set_responses([slow_response])
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    ctx = RunContext(
        user_id=actor,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=conversation_id,
        is_group_chat=True,
    )
    replacement = RunManager(
        app=run_manager._app,
        redis=run_manager._redis,
        key_prefix=run_manager._key_prefix,
        run_event_ttl_seconds=60,
    )
    try:
        if remote:
            await run_manager.start_control_listeners()
        run_id = await run_manager.start_run(
            conversation_id=conversation_id,
            content="a slow model response",
            ctx=ctx,
            run_id=admitted.admission.run_id,
            admission_id=admitted.admission.id,
            llm_snapshot=current,
        )
        await asyncio.wait_for(entered.wait(), timeout=15)
        worker = run_manager._tasks[run_id]
        if all_work:
            await service(db_session).close_generation(
                conversation_id=conversation_id,
                execution_generation=admitted.admission.execution_generation,
                actor_user_id=actor,
                now=datetime.now(UTC),
            )
        else:
            await service(db_session).stop_run(
                conversation_id=conversation_id,
                run_id=run_id,
                actor_user_id=actor,
                now=datetime.now(UTC),
            )
        await db_session.commit()
        await (replacement if remote else run_manager).recover_stopped_run(admitted.admission.id)
        done, _ = await asyncio.wait([worker], timeout=3)
        assert done, "a persisted Stop must wake the live owner before its model reply arrives"
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_finished_at is not None
        assert provider.call_count == 1
        meta = await get_run_meta(run_manager._redis, prefix=run_manager._key_prefix, run_id=run_id)
        assert meta is not None and meta.status == "cancelled"
        assert (
            await get_active_run(
                run_manager._redis, prefix=run_manager._key_prefix, conversation_id=conversation_id
            )
            is None
        )
    finally:
        release.set()
        await run_manager.stop_control_listeners()
        await run_manager.cancel_all()
        await replacement.cancel_all()
        await cleanup_run_rows(db_session, conversation_id)
