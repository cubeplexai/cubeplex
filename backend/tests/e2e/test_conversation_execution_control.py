"""Stop and retries cannot grant old sources a fresh execution identity."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.llm.config import ProviderConfig
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset
from cubeplex.models import (
    BackgroundTaskEvent,
    Conversation,
    ConversationExecutionAdmission,
    Membership,
    User,
)
from cubeplex.services.conversation_execution import (
    ConversationExecutionService,
    ExecutionConflictError,
    ExecutionRevokedError,
    UserMessageIntent,
)
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_WS_ID
from tests.e2e.test_background_task_reservation import (
    NOW,
    ReservationContext,
    reserve,
)

reservation_context = reservation_fixtures.reservation_context


def service(session: AsyncSession) -> ConversationExecutionService:
    return ConversationExecutionService(session, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID)


def snapshot(
    default: str = "first", *, available: tuple[str, ...] = ("first", "next")
) -> LLMSnapshot:
    return LLMSnapshot(
        providers={
            "provider": ProviderConfig.model_validate(
                {
                    "base_url": "https://example.invalid",
                    "api": "openai-completions",
                    "models": [
                        {"id": item, "name": item, "contextWindow": 1000, "maxTokens": 100}
                        for item in available
                    ],
                }
            )
        },
        model_presets=(
            ModelPreset(
                key="pro",
                primary=f"provider/{default}",
                fallbacks=(),
                kind="tier",
                is_default=True,
            ),
        ),
        task_routing={},
    )


async def actor_id(session: AsyncSession, context: ReservationContext) -> str:
    row = await session.get(ConversationExecutionAdmission, context.admission_id)
    assert row is not None
    return row.actor_user_id


async def test_retry_keeps_first_run_snapshot_and_does_not_overwrite_later_selection(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    source = str(uuid4())
    intent = UserMessageIntent(content="build")
    first = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=source,
        intent=intent,
        snapshot=snapshot(),
        now=NOW,
    )
    assert first.created and first.admission.run_id
    await db_session.commit()
    conv = await db_session.get(Conversation, reservation_context.conversation_id)
    assert conv is not None
    conv.model_key = "new-user-selection"
    await db_session.commit()
    retry = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=source,
        intent=intent,
        snapshot=snapshot("next"),
        now=NOW + timedelta(seconds=1),
    )
    assert not retry.created and retry.admission.run_id == first.admission.run_id
    assert retry.execution.primary == "provider/first"
    await db_session.refresh(conv)
    assert conv.model_key == "new-user-selection"


@pytest.mark.parametrize("change", ["content", "attachment_ids", "model_key", "reasoning"])
async def test_same_source_cannot_change_execution_request(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    change: str,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    source = str(uuid4())
    intent = UserMessageIntent(content="build")
    kwargs = {
        "conversation_id": reservation_context.conversation_id,
        "actor_user_id": actor,
        "namespace": "web",
        "source_id": source,
        "snapshot": snapshot(),
        "now": NOW,
    }
    await service(db_session).admit_user_message(**kwargs, intent=intent)
    await db_session.commit()
    payload = intent.model_dump()
    payload[change] = {
        "content": "different work",
        "attachment_ids": ["atch-missing"],
        "model_key": "pro",
        "reasoning": {"mode": "on"},
    }[change]
    with pytest.raises(ExecutionConflictError):
        await service(db_session).admit_user_message(
            **kwargs, intent=UserMessageIntent.model_validate(payload)
        )


async def test_retry_cannot_substitute_a_removed_original_model(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    source = str(uuid4())
    intent = UserMessageIntent(content="build")
    await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=source,
        intent=intent,
        snapshot=snapshot(),
        now=NOW,
    )
    await db_session.commit()
    with pytest.raises(ExecutionRevokedError, match="model"):
        await service(db_session).admit_user_message(
            conversation_id=reservation_context.conversation_id,
            actor_user_id=actor,
            namespace="web",
            source_id=source,
            intent=intent,
            snapshot=snapshot("next", available=("next",)),
            now=NOW,
        )


async def test_failed_admission_transaction_leaves_no_new_source(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    source = str(uuid4())
    await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=source,
        intent=UserMessageIntent(content="build"),
        snapshot=snapshot(),
        now=NOW,
    )
    await db_session.rollback()
    assert (
        await db_session.scalar(
            select(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.source_id) == f"web:{source}"
            )
        )
        is None
    )


async def test_stop_closes_idle_generation_without_faking_remote_exit(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    reserved = await reserve(db_session, reservation_context)
    reserved.task.state = "running"
    reserved.command.provider_ref = "original-process"
    reserved.command.status = "running"
    notice = BackgroundTaskEvent(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=reservation_context.conversation_id,
        task_id=reserved.task.id,
        execution_generation=0,
        reason="line",
        dedupe_key="line:1",
    )
    db_session.add(notice)
    await db_session.commit()
    result = await service(db_session).close_generation(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        execution_generation=0,
        now=NOW,
    )
    await db_session.commit()
    assert result.accepted and result.cleanup_pending
    await db_session.refresh(reserved.task)
    await db_session.refresh(reserved.command)
    await db_session.refresh(notice)
    assert reserved.task.stop_requested_at == NOW
    assert reserved.task.notifications_cancelled_at == NOW
    assert reserved.task.state == "running" and reserved.task.finished_at is None
    assert reserved.command.provider_ref == "original-process"
    assert notice.state == "discarded"
    with pytest.raises(ExecutionRevokedError):
        await service(db_session).claim_run_start(
            admission_id=reservation_context.admission_id,
            attempt_id="too-late",
            now=NOW,
        )


async def test_old_stop_and_retry_cannot_close_or_join_a_new_generation(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    source = str(uuid4())
    intent = UserMessageIntent(content="build")
    first = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=source,
        intent=intent,
        snapshot=snapshot(),
        now=NOW,
    )
    await db_session.commit()
    await service(db_session).close_generation(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        execution_generation=0,
        now=NOW,
    )
    await db_session.commit()
    new = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=intent,
        snapshot=snapshot(),
        now=NOW,
    )
    await db_session.commit()
    assert new.admission.execution_generation == 1
    retry = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=source,
        intent=intent,
        snapshot=snapshot(),
        now=NOW,
    )
    assert retry.admission.run_id == first.admission.run_id
    assert retry.admission.execution_generation == 0 and retry.admission.revoked_at == NOW
    await service(db_session).close_generation(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        execution_generation=0,
        now=NOW + timedelta(seconds=1),
    )
    await db_session.commit()
    conv = await db_session.get(Conversation, reservation_context.conversation_id)
    assert conv is not None and conv.execution_generation == 1
    assert conv.execution_closed_at is None
    assert await service(db_session).claim_run_start(
        admission_id=new.admission.id,
        attempt_id="new-start",
        now=NOW,
    )


async def test_lost_start_response_does_not_authorize_reexecution(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    accepted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="build"),
        snapshot=snapshot(),
        now=NOW,
    )
    await db_session.commit()
    assert await service(db_session).claim_run_start(
        admission_id=accepted.admission.id,
        attempt_id="first-start",
        now=NOW,
    )
    await db_session.commit()
    await db_session.refresh(accepted.admission)
    assert accepted.admission.run_started_at is None
    assert not await service(db_session).claim_run_start(
        admission_id=accepted.admission.id,
        attempt_id="retry-start",
        now=NOW,
    )


async def test_worker_receipts_are_owned_and_do_not_reexecute_a_started_attempt(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    accepted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="build"),
        snapshot=snapshot(),
        now=NOW,
    )
    admission_id = accepted.admission.id
    assert await service(db_session).claim_run_start(
        admission_id=admission_id, attempt_id="owner", now=NOW
    )
    await db_session.commit()
    controller = service(db_session)
    assert hasattr(controller, "record_run_started"), "worker start needs a separate receipt"
    assert not await controller.record_run_started(
        admission_id=admission_id, attempt_id="other", now=NOW
    )
    started_at = NOW + timedelta(seconds=2)
    assert await controller.record_run_started(
        admission_id=admission_id, attempt_id="owner", now=started_at
    )
    await db_session.commit()
    assert not await controller.record_run_started(
        admission_id=admission_id, attempt_id="owner", now=NOW + timedelta(seconds=3)
    )
    assert not await controller.record_run_finished(
        admission_id=admission_id,
        attempt_id="other",
        worker_started=True,
        now=NOW + timedelta(seconds=4),
    )
    assert not await controller.record_run_finished(
        admission_id=admission_id,
        attempt_id="owner",
        worker_started=False,
        now=NOW + timedelta(seconds=4),
    )
    await db_session.refresh(accepted.admission)
    assert accepted.admission.run_start_requested_at == NOW
    assert accepted.admission.run_started_at == started_at
    assert accepted.admission.run_finished_at is None
    await controller.close_generation(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        execution_generation=0,
        now=NOW + timedelta(seconds=5),
    )
    finished_at = NOW + timedelta(seconds=6)
    assert await controller.record_run_finished(
        admission_id=admission_id, attempt_id="owner", worker_started=True, now=finished_at
    )
    assert not await controller.record_run_finished(
        admission_id=admission_id,
        attempt_id="owner",
        worker_started=True,
        now=NOW + timedelta(seconds=7),
    )
    await db_session.commit()
    await db_session.refresh(accepted.admission)
    assert accepted.admission.run_finished_at == finished_at


async def test_stop_between_start_request_and_worker_entry_revokes_execution(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    accepted = await service(db_session).admit_user_message(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        namespace="web",
        source_id=str(uuid4()),
        intent=UserMessageIntent(content="build"),
        snapshot=snapshot(),
        now=NOW,
    )
    assert await service(db_session).claim_run_start(
        admission_id=accepted.admission.id, attempt_id="owner", now=NOW
    )
    await db_session.commit()
    controller = service(db_session)
    assert hasattr(controller, "record_run_started"), "worker entry must recheck Stop"
    closed = await controller.close_generation(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        execution_generation=0,
        now=NOW + timedelta(seconds=1),
    )
    await db_session.commit()
    assert closed.cleanup_pending
    with pytest.raises(ExecutionRevokedError):
        await controller.record_run_started(
            admission_id=accepted.admission.id, attempt_id="owner", now=NOW + timedelta(seconds=2)
        )
    await db_session.rollback()
    await db_session.refresh(accepted.admission)
    assert accepted.admission.run_started_at is None


async def test_concurrent_retries_bind_one_run_and_one_model_snapshot(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    await db_session.rollback()
    source = str(uuid4())
    barrier = asyncio.Barrier(2)

    async def admit(default: str) -> tuple[str | None, str, bool]:
        async with session_factory() as session, session.begin():
            await barrier.wait()
            result = await service(session).admit_user_message(
                conversation_id=reservation_context.conversation_id,
                actor_user_id=actor,
                namespace="web",
                source_id=source,
                intent=UserMessageIntent(content="build"),
                snapshot=snapshot(default),
                now=NOW,
            )
            return result.admission.run_id, result.execution.primary, result.created

    results = await asyncio.wait_for(asyncio.gather(admit("first"), admit("next")), timeout=10)
    assert results[0][:2] == results[1][:2]
    assert sorted(row[2] for row in results) == [False, True]


async def test_source_conflict_across_actors_and_conversations_is_a_domain_error(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    first_actor = await actor_id(db_session, reservation_context)
    other = User(email=f"source-race-{uuid4()}@example.invalid", hashed_password="not-a-login")
    db_session.add(other)
    await db_session.flush()
    other_id = other.id
    second = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=other_id,
        title="source identity race",
        is_group_chat=True,
    )
    db_session.add(second)
    db_session.add(Membership(user_id=other_id, workspace_id=DEFAULT_WS_ID, role="member"))
    await db_session.commit()
    second_id = second.id
    source_id = str(uuid4())
    barrier = asyncio.Barrier(2)

    async def admit(conversation_id: str, actor: str) -> str:
        try:
            async with session_factory() as session, session.begin():
                await barrier.wait()
                result = await service(session).admit_user_message(
                    conversation_id=conversation_id,
                    actor_user_id=actor,
                    namespace="web",
                    source_id=source_id,
                    intent=UserMessageIntent(content="immutable source"),
                    snapshot=snapshot(),
                    now=NOW,
                )
                return result.admission.id
        except ExecutionConflictError:
            return "conflict"

    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                admit(reservation_context.conversation_id, first_actor), admit(second_id, other_id)
            ),
            timeout=10,
        )
        assert results.count("conflict") == 1
        assert len(set(results)) == 2
    finally:
        await db_session.rollback()
        await db_session.execute(
            delete(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.conversation_id) == second_id
            )
        )
        await db_session.execute(delete(Conversation).where(col(Conversation.id) == second_id))
        await db_session.execute(delete(Membership).where(col(Membership.user_id) == other_id))
        await db_session.execute(delete(User).where(col(User.id) == other_id))
        await db_session.commit()


@pytest.mark.parametrize("first_operation", ["admit", "stop"])
async def test_stop_and_first_admission_have_one_serial_order(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    first_operation: str,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    source = str(uuid4())

    async def admit(session: AsyncSession) -> str:
        result = await service(session).admit_user_message(
            conversation_id=reservation_context.conversation_id,
            actor_user_id=actor,
            namespace="web",
            source_id=source,
            intent=UserMessageIntent(content="build"),
            snapshot=snapshot(),
            now=NOW,
        )
        return result.admission.id

    async def stop(session: AsyncSession) -> None:
        await service(session).close_generation(
            conversation_id=reservation_context.conversation_id,
            actor_user_id=actor,
            execution_generation=0,
            now=NOW,
        )

    admission_id: str | None = None
    if first_operation == "admit":
        admission_id = await admit(db_session)
    else:
        await stop(db_session)
    entered = asyncio.Event()

    async def second() -> str | None:
        async with session_factory() as session, session.begin():
            entered.set()
            if first_operation == "stop":
                return await admit(session)
            await stop(session)
            return None

    other = asyncio.create_task(second())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        await db_session.commit()
        second_id = await asyncio.wait_for(other, timeout=10)
    finally:
        if not other.done():
            other.cancel()
            await asyncio.gather(other, return_exceptions=True)
    admission_id = second_id if first_operation == "stop" else admission_id
    row = await db_session.get(ConversationExecutionAdmission, admission_id, populate_existing=True)
    assert row is not None
    assert row.execution_generation == (1 if first_operation == "stop" else 0)
    assert (row.revoked_at is not None) == (first_operation == "admit")


async def test_stop_rollback_preserves_execution_and_notification_authority(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    reserved = await reserve(db_session, reservation_context)
    task_id = reserved.task.id
    await db_session.commit()
    await service(db_session).close_generation(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        execution_generation=0,
        now=NOW,
    )
    await db_session.rollback()
    from cubeplex.models import BackgroundTask

    task = await db_session.get(BackgroundTask, task_id)
    conv = await db_session.get(Conversation, reservation_context.conversation_id)
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert task is not None and task.stop_requested_at is None
    assert task.notifications_cancelled_at is None
    assert conv is not None and conv.execution_closed_at is None
    assert admission is not None and admission.revoked_at is None


async def test_stop_keeps_claimed_notice_for_checkpoint_reconciliation(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
) -> None:
    actor = await actor_id(db_session, reservation_context)
    reserved = await reserve(db_session, reservation_context)
    reserved.task.state = "succeeded"
    notices = [
        BackgroundTaskEvent(
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            conversation_id=reservation_context.conversation_id,
            task_id=reserved.task.id,
            execution_generation=0,
            reason="line",
            dedupe_key=state,
            state=state,
            delivery_attempt_id="attempt",
            delivery_input_id=f"input-{state}",
        )
        for state in ("pending", "claimed", "delivered")
    ]
    db_session.add_all(notices)
    await db_session.commit()
    result = await service(db_session).close_generation(
        conversation_id=reservation_context.conversation_id,
        actor_user_id=actor,
        execution_generation=0,
        now=NOW,
    )
    assert result.cleanup_pending
    assert [notice.state for notice in notices] == ["pending", "claimed", "delivered"]
    assert all(notice.delivery_attempt_id == "attempt" for notice in notices)
