"""Normal completion permits reflection; Stop and lost authority do not permit writes."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cubeloop.providers.base import AssistantMessage, Message, Model
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text, faux_tool_call
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.db.engine import async_session_maker
from cubeplex.models import Conversation, User
from cubeplex.models.memory import (
    MemoryItem,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    MemoryType,
)
from cubeplex.repositories.memory import MemoryRepository
from cubeplex.services.conversation_execution import UserMessageIntent
from cubeplex.services.memory import PERSONAL_ACTIVE_SOFT_CAP, CreateMemoryInput, MemoryService
from cubeplex.services.user_event_bus import UserEventBus
from cubeplex.streams.run_events import _run_meta_key, get_active_run
from cubeplex.streams.run_manager import RunContext, RunManager
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_background_task_reservation import ReservationContext
from tests.e2e.test_conversation_execution_control import actor_id, service, snapshot

reservation_context = run_fixtures.reservation_context
run_manager = run_fixtures.run_manager


async def stop_during_memory_write(
    *, conversation_id: str, actor: str, new_content: str, release_reflection: asyncio.Event
) -> bool:
    """The memory transaction either precedes Stop or must not commit after it."""
    async with (
        async_session_maker() as blocker,
        async_session_maker() as stop_session,
        async_session_maker() as observer,
    ):
        stop_task: asyncio.Task[bool] | None = None
        try:
            await blocker.execute(text("LOCK TABLE memory_items IN SHARE MODE"))
            blocker_pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
            stop_pid = await stop_session.scalar(text("SELECT pg_backend_pid()"))
            release_reflection.set()
            async with asyncio.timeout(10):
                while not await observer.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE :blocker = ANY(pg_blocking_pids(pid)))"
                    ),
                    {"blocker": blocker_pid},
                ):
                    await observer.rollback()
                    await asyncio.sleep(0.01)
            await observer.rollback()

            async def stop_and_read() -> bool:
                await service(stop_session).close_generation(
                    conversation_id=conversation_id,
                    actor_user_id=actor,
                    execution_generation=0,
                    now=datetime.now(UTC),
                )
                await stop_session.commit()
                return (
                    await stop_session.scalar(
                        select(MemoryItem.id).where(
                            col(MemoryItem.source_conversation_id) == conversation_id,
                            col(MemoryItem.content) == new_content,
                        )
                    )
                    is not None
                )

            stop_task = asyncio.create_task(stop_and_read())
            async with asyncio.timeout(10):
                while not stop_task.done() and not await observer.scalar(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": stop_pid}
                ):
                    await observer.rollback()
                    await asyncio.sleep(0.01)
            await blocker.rollback()
            return await asyncio.wait_for(stop_task, timeout=10)
        finally:
            await blocker.rollback()
            if stop_task is not None and not stop_task.done():
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)


@pytest.mark.parametrize(
    ("scenario", "operation"),
    [
        ("completed", "save"),
        ("completed", "update"),
        ("stop", "save"),
        ("stop", "update"),
        ("stop_before_model", "save"),
        ("commit_race", "save"),
        ("commit_race", "update"),
        ("reopened", "save"),
        ("replaced_claim", "save"),
        ("missing_meta", "save"),
        ("deleted", "save"),
    ],
)
async def test_reflection_rechecks_authority_after_the_main_run_finishes(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    operation: str,
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
        intent=UserMessageIntent(content="Please remember that I prefer short answers."),
        snapshot=original,
        now=datetime.now(UTC),
    )
    run_id = admitted.admission.run_id
    assert run_id is not None
    old_memory = MemoryItem(
        scope=MemoryScope.PERSONAL,
        owner_user_id=actor,
        workspace_id=DEFAULT_WS_ID,
        type=MemoryType.PREFERENCE,
        content=f"Previous preference {uuid4()}",
        source_conversation_id=conversation_id,
        created_by_user_id=actor,
    )
    if operation == "update":
        db_session.add(old_memory)
    await db_session.commit()
    old_content = old_memory.content
    new_content = f"Prefers short answers {uuid4()}"
    entered_reflection = asyncio.Event()
    release_reflection = asyncio.Event()

    async def reflection_response(messages: list[Message], model: Model) -> AssistantMessage:
        entered_reflection.set()
        await release_reflection.wait()
        args = (
            {"memory_id": old_memory.id, "content": new_content}
            if operation == "update"
            else {"scope": "personal", "type": "preference", "content": new_content}
        )
        return faux_assistant_message(
            faux_tool_call(f"memory_{operation}", args), stop_reason="tool_use"
        )

    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [
            faux_assistant_message(faux_text("I will keep replies concise."), stop_reason="stop"),
            reflection_response,
            faux_assistant_message(faux_text("Memory saved."), stop_reason="stop"),
        ]
    )
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    run_manager._app.state.user_event_bus = UserEventBus()
    scheduled_reflections: list[asyncio.Task[None]] = []
    stopped_before_model = False
    if scenario == "stop_before_model":
        original_eval = run_manager._redis.eval

        async def stop_after_terminal_commit(script: str, numkeys: int, *args: object) -> object:
            nonlocal stopped_before_model
            result = await original_eval(script, numkeys, *args)
            if not stopped_before_model and "completed" in args:
                stopped_before_model = True
                scheduled_reflections.extend(run_manager._reflection_tasks)
                await service(db_session).close_generation(
                    conversation_id=conversation_id,
                    actor_user_id=actor,
                    execution_generation=0,
                    now=datetime.now(UTC),
                )
                await db_session.commit()
            return result

        monkeypatch.setattr(run_manager._redis, "eval", stop_after_terminal_commit)
    try:
        await run_manager.start_run(
            conversation_id=conversation_id,
            content="Please remember that I prefer short answers.",
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
        await asyncio.wait_for(asyncio.shield(owner), timeout=15)
        if scenario != "stop_before_model":
            await asyncio.wait_for(entered_reflection.wait(), timeout=15)
        else:
            assert stopped_before_model and scheduled_reflections
        await db_session.refresh(admitted.admission)
        assert admitted.admission.run_finished_at is not None
        assert (
            await get_active_run(
                run_manager._redis,
                prefix=run_manager._key_prefix,
                conversation_id=conversation_id,
            )
            is None
        )
        if scenario in ("stop", "reopened"):
            await service(db_session).close_generation(
                conversation_id=conversation_id,
                actor_user_id=actor,
                execution_generation=0,
                now=datetime.now(UTC),
            )
            if scenario == "reopened":
                await service(db_session).admit_user_message(
                    conversation_id=conversation_id,
                    actor_user_id=actor,
                    namespace="web",
                    source_id=str(uuid4()),
                    intent=UserMessageIntent(content="New work, not the old reflection."),
                    snapshot=original,
                    now=datetime.now(UTC),
                )
        elif scenario == "replaced_claim":
            await run_manager._redis.hset(
                _run_meta_key(run_manager._key_prefix, run_id), "claim_token", "replacement"
            )
        elif scenario == "missing_meta":
            await run_manager._redis.delete(_run_meta_key(run_manager._key_prefix, run_id))
        elif scenario == "deleted":
            conversation = await db_session.get(Conversation, conversation_id)
            assert conversation is not None
            conversation.deleted_at = datetime.now(UTC)
        await db_session.commit()
        reflections = scheduled_reflections or list(run_manager._reflection_tasks)
        assert reflections
        stop_saw_memory = True
        if scenario == "commit_race":
            stop_saw_memory = await stop_during_memory_write(
                conversation_id=conversation_id,
                actor=actor,
                new_content=new_content,
                release_reflection=release_reflection,
            )
        else:
            release_reflection.set()
        await asyncio.wait_for(asyncio.gather(*reflections, return_exceptions=True), timeout=15)
        memories = list(
            (
                await db_session.scalars(
                    select(MemoryItem)
                    .where(col(MemoryItem.source_conversation_id) == conversation_id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        assert stop_saw_memory, "memory committed after Stop closed its execution generation"
        if scenario in ("completed", "commit_race"):
            assert len(memories) == 1 and memories[0].content == new_content
            if operation == "save":
                assert memories[0].source_type == MemorySourceType.REFLECTION
        elif operation == "update":
            assert len(memories) == 1 and memories[0].content == old_content
        else:
            assert memories == [], "revoked reflection wrote personal memory"
        expected_calls = 1 if scenario == "stop_before_model" else 2
        assert provider.call_count == (3 if scenario == "completed" else expected_calls)
    finally:
        release_reflection.set()
        await run_manager.cancel_all()
        reflections = list(run_manager._reflection_tasks)
        for task in reflections:
            task.cancel()
        await asyncio.gather(*reflections, return_exceptions=True)
        await db_session.rollback()
        await db_session.execute(
            delete(MemoryItem).where(col(MemoryItem.source_conversation_id) == conversation_id)
        )
        await db_session.execute(
            text("DELETE FROM user_events WHERE payload->>'conversation_id' = :conversation"),
            {"conversation": conversation_id},
        )
        await db_session.commit()
        await run_fixtures.cleanup_run_rows(db_session, conversation_id)


@pytest.mark.usefixtures("reservation_context")
@pytest.mark.parametrize("operation", ["save", "update", "dedup", "cap"])
async def test_memory_changes_do_not_escape_the_authorizing_transaction(
    db_session: AsyncSession, operation: str
) -> None:
    user = User(email=f"memory-transaction-{uuid4()}@example.com", hashed_password="test")
    db_session.add(user)
    await db_session.flush()
    user_id = user.id
    count = PERSONAL_ACTIVE_SOFT_CAP if operation == "cap" else 1
    originals = [
        MemoryItem(
            scope=MemoryScope.PERSONAL,
            owner_user_id=user_id,
            workspace_id=DEFAULT_WS_ID,
            type=MemoryType.PREFERENCE,
            content=f"Original preference {index}",
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        for index in range(count)
    ]
    db_session.add_all(originals)
    await db_session.commit()
    original_ids = {item.id for item in originals}
    original_id = originals[0].id
    try:
        repo = MemoryRepository(
            db_session,
            user_id=user_id,
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            auto_commit=False,
        )
        memory = MemoryService(
            repo, user_id=user_id, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID
        )
        if operation == "update":
            await memory.update(original_id, content="Changed preference")
        else:
            await memory.create(
                CreateMemoryInput(
                    scope=MemoryScope.PERSONAL,
                    type=MemoryType.PREFERENCE,
                    content="Original preference 0" if operation == "dedup" else "New preference",
                )
            )
        await db_session.rollback()
        after = list(
            (
                await db_session.scalars(
                    select(MemoryItem)
                    .where(col(MemoryItem.owner_user_id) == user_id)
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        assert {item.id for item in after} == original_ids
        assert all(item.status == MemoryStatus.ACTIVE for item in after)
        assert all(item.content.startswith("Original preference") for item in after)
        assert all(item.updated_at == datetime(2026, 1, 1, tzinfo=UTC) for item in after)
    finally:
        await db_session.rollback()
        await db_session.execute(delete(MemoryItem).where(col(MemoryItem.owner_user_id) == user_id))
        await db_session.execute(delete(User).where(col(User.id) == user_id))
        await db_session.commit()
