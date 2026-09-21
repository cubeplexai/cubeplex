"""A completed run cannot consolidate memory after its execution is revoked."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cubeloop.providers.base import AssistantMessage, Message, Model
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.memory import MemoryItem, MemorySourceType
from cubeplex.services import memory_consolidation as mc
from cubeplex.services.conversation_execution import UserMessageIntent
from cubeplex.streams.run_manager import RunContext, RunManager
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_admitted_reflection_execution import stop_during_memory_write
from tests.e2e.test_background_task_reservation import ReservationContext
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

reservation_context = run_fixtures.reservation_context
run_manager = run_fixtures.run_manager


@pytest.mark.parametrize("scenario", ["completed", "stop", "commit_race"])
@pytest.mark.parametrize("scope", ["personal", "workspace"])
async def test_consolidation_writes_require_original_execution_authority(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    scope: str,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    conversation_id = reservation_context.conversation_id
    original = snapshot()
    provider_config = original.providers["provider"].model_copy(deep=True)
    for model in provider_config.models:
        model.context_window = 128_000
        model.max_tokens = 4096
    original = replace(original, providers={"provider": provider_config})
    admitted = await service(db_session).admit_user_message(
        conversation_id=conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="We use concise output in this workspace."),
        snapshot=original,
        now=datetime.now(UTC),
    )
    await db_session.commit()
    run_id = admitted.admission.run_id
    assert run_id is not None
    for _ in range(mc.DEFAULT_MIN_RUNS):
        await mc.note_run(run_manager._redis, run_manager._key_prefix, conversation_id)
    entered = asyncio.Event()
    release = asyncio.Event()
    new_content = f"Uses concise output {uuid4()}"

    async def consolidate(messages: list[Message], model: Model) -> AssistantMessage:
        entered.set()
        await release.wait()
        return faux_assistant_message(
            faux_text(
                json.dumps(
                    {
                        "ops": [
                            {
                                "action": "extract",
                                "scope": scope,
                                "type": "preference" if scope == "personal" else "project_fact",
                                "content": new_content,
                            }
                        ]
                    }
                )
            ),
            stop_reason="stop",
        )

    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [faux_assistant_message(faux_text("Understood."), stop_reason="stop"), consolidate]
    )
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    try:
        await run_manager.start_run(
            conversation_id=conversation_id,
            content="We use concise output in this workspace.",
            ctx=RunContext(
                user_id=actor,
                org_id=DEFAULT_ORG_ID,
                workspace_id=DEFAULT_WS_ID,
                conversation_id=conversation_id,
                is_group_chat=True,
            ),
            run_id=run_id,
            admission_id=admitted.admission.id,
            llm_snapshot=original,
        )
        owner = run_manager._tasks[run_id]
        await asyncio.wait_for(asyncio.shield(owner), 15)
        await asyncio.wait_for(entered.wait(), 15)
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_finished_at is not None
        await db_session.rollback()
        tasks = list(run_manager._consolidation_tasks)
        assert tasks
        if scenario == "stop":
            await service(db_session).close_generation(
                conversation_id=conversation_id,
                actor_user_id=actor,
                execution_generation=0,
                now=datetime.now(UTC),
            )
            await db_session.commit()
        stop_saw_memory = True
        if scenario == "commit_race":
            stop_saw_memory = await stop_during_memory_write(
                conversation_id=conversation_id,
                actor=actor,
                new_content=new_content,
                release_reflection=release,
            )
        else:
            release.set()
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15)
        memories = list(
            (
                await db_session.scalars(
                    select(MemoryItem).where(
                        col(MemoryItem.source_conversation_id) == conversation_id
                    )
                )
            ).all()
        )
        assert stop_saw_memory, "consolidation committed after Stop"
        if scenario == "stop":
            assert memories == [], "revoked consolidation wrote memory"
        else:
            assert len(memories) == 1 and memories[0].content == new_content
            assert memories[0].source_type == MemorySourceType.CONSOLIDATION
            assert memories[0].source_run_id == run_id
        assert provider.call_count == 2
    finally:
        release.set()
        await run_manager.cancel_all()
        tasks = list(run_manager._consolidation_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await db_session.rollback()
        await db_session.execute(
            delete(MemoryItem).where(col(MemoryItem.source_conversation_id) == conversation_id)
        )
        await db_session.commit()
        await run_fixtures.cleanup_run_rows(db_session, conversation_id)
