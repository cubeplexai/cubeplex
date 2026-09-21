"""Reservations keep authority, capacity and environment identity in one transaction."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col

from cubeplex.models import (
    BackgroundTask,
    BackgroundTaskEvent,
    Conversation,
    ConversationExecutionAdmission,
    SandboxCommand,
    Topic,
    TopicParticipant,
    User,
    UserSandbox,
)
from cubeplex.models.sandbox_command import SandboxCommandKind
from cubeplex.repositories.sandbox_command import SandboxCommandCapError
from cubeplex.services.background_tasks import (
    BackgroundTaskService,
    CommandExecutionDetails,
    TaskEnvironmentChangedError,
    TaskExecutionRevokedError,
    TaskReservation,
    TaskSpec,
)
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
)

NOW = datetime(2026, 9, 21, tzinfo=UTC)


@dataclass(frozen=True)
class ReservationContext:
    conversation_id: str
    admission_id: str
    details: CommandExecutionDetails
    spec: TaskSpec


def service(session: AsyncSession) -> BackgroundTaskService:
    return BackgroundTaskService(session, org_id=DEFAULT_ORG_ID, workspace_id=DEFAULT_WS_ID)


async def reserve(
    session: AsyncSession,
    context: ReservationContext,
    *,
    spec: TaskSpec | None = None,
    details: CommandExecutionDetails | None = None,
) -> TaskReservation:
    return await service(session).reserve_task(
        admission_id=context.admission_id,
        task_spec=spec or replace(context.spec, tool_call_id=str(uuid4())),
        execution_details=details or context.details,
        owner_token=str(uuid4()),
        owner_until=NOW + timedelta(seconds=30),
        now=NOW,
    )


@pytest_asyncio.fixture
async def reservation_context(db_session: AsyncSession) -> AsyncIterator[ReservationContext]:
    await _ensure_default_user_and_membership()
    actor = (
        await db_session.execute(select(User).where(col(User.email) == DEFAULT_TEST_EMAIL))
    ).scalar_one()
    conv = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=actor.id,
        title="background task reservation",
        is_group_chat=True,
    )
    db_session.add(conv)
    await db_session.flush()
    sandbox = UserSandbox(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_id=actor.id,
        scope_type="conversation",
        scope_id=conv.id,
        sandbox_id=f"original-{uuid4()}",
        status="running",
        provider="local",
        image="test",
    )
    admission = ConversationExecutionAdmission(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=conv.id,
        actor_user_id=actor.id,
        execution_generation=conv.execution_generation,
        source_kind="user_message",
        source_id=f"web:{uuid4()}",
        run_id=str(uuid4()),
    )
    db_session.add_all([sandbox, admission])
    await db_session.commit()
    assert sandbox.sandbox_id is not None and admission.run_id is not None
    context = ReservationContext(
        conversation_id=conv.id,
        admission_id=admission.id,
        details=CommandExecutionDetails(
            user_sandbox_id=sandbox.id,
            sandbox_instance_id=sandbox.sandbox_id,
            provider="local",
            command="build project",
            log_path="/workspace/.cubeplex/build.log",
        ),
        spec=TaskSpec(originating_run_id=admission.run_id, tool_call_id=str(uuid4())),
    )
    try:
        yield context
    finally:
        await db_session.rollback()
        for model in (BackgroundTaskEvent, SandboxCommand, BackgroundTask):
            await db_session.execute(
                delete(model).where(col(model.conversation_id) == context.conversation_id)
            )
        await db_session.execute(
            delete(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.conversation_id) == context.conversation_id
            )
        )
        await db_session.execute(
            delete(UserSandbox).where(col(UserSandbox.id) == context.details.user_sandbox_id)
        )
        await db_session.execute(
            delete(Conversation).where(col(Conversation.id) == context.conversation_id)
        )
        await db_session.commit()


@pytest.mark.parametrize("notify", [True, False])
async def test_deadline_and_instance_survive_configuration_change(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    monkeypatch: pytest.MonkeyPatch,
    notify: bool,
) -> None:
    from cubeplex.config import config

    monkeypatch.setattr(config, "get", lambda key: 3600)
    result = await reserve(
        db_session,
        reservation_context,
        spec=replace(reservation_context.spec, notify_on_complete=notify),
    )
    await db_session.commit()
    monkeypatch.setattr(config, "get", lambda key: 42)
    async with session_factory() as restarted:
        task = await service(restarted).tasks.get(result.task.id)
        command = await restarted.get(SandboxCommand, result.command.id)
        assert task is not None and command is not None
        assert task.deadline_at == NOW + timedelta(hours=1)
        assert task.notify_on_complete is notify
        assert task.state == "starting"
        assert task.backgrounded_at is None
        assert command.task_id == task.id
        assert command.sandbox_instance_id == reservation_context.details.sandbox_instance_id


async def test_explicit_long_deadline_and_persistent_monitor_are_separate(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    long = await reserve(
        db_session,
        reservation_context,
        details=replace(reservation_context.details, timeout_seconds=7200),
    )
    monitor = await reserve(
        db_session,
        reservation_context,
        details=replace(reservation_context.details, kind=SandboxCommandKind.monitor),
    )
    await db_session.commit()
    assert long.task.deadline_at == NOW + timedelta(hours=2)
    assert monitor.task.deadline_at is None


@pytest.mark.parametrize("revocation", ["closed", "deleted", "new_generation", "different_run"])
async def test_stale_admission_cannot_reserve(
    db_session: AsyncSession, reservation_context: ReservationContext, revocation: str
) -> None:
    conversation = await db_session.get(Conversation, reservation_context.conversation_id)
    assert conversation is not None
    spec = reservation_context.spec
    if revocation == "closed":
        conversation.execution_closed_at = NOW
    elif revocation == "deleted":
        conversation.deleted_at = NOW
    elif revocation == "new_generation":
        conversation.execution_generation += 1
    else:
        spec = replace(spec, originating_run_id="not-the-admitted-run")
    await db_session.commit()
    with pytest.raises(TaskExecutionRevokedError):
        await reserve(db_session, reservation_context, spec=spec)
    assert (
        not (
            await db_session.execute(
                select(BackgroundTask).where(
                    col(BackgroundTask.conversation_id) == reservation_context.conversation_id
                )
            )
        )
        .scalars()
        .all()
    )


async def test_revived_sandbox_does_not_rebind_old_task(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    result = await reserve(db_session, reservation_context)
    sandbox = await db_session.get(UserSandbox, reservation_context.details.user_sandbox_id)
    assert sandbox is not None
    sandbox.sandbox_id = f"replacement-{uuid4()}"
    await db_session.commit()
    with pytest.raises(TaskEnvironmentChangedError):
        await reserve(db_session, reservation_context)
    await db_session.refresh(result.command)
    assert result.command.sandbox_instance_id == reservation_context.details.sandbox_instance_id


async def test_reservation_cannot_use_another_conversations_sandbox(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    sandbox = await db_session.get(UserSandbox, reservation_context.details.user_sandbox_id)
    assert sandbox is not None
    sandbox.scope_id = "conv-someone-else"
    await db_session.commit()
    with pytest.raises(LookupError, match="sandbox"):
        await reserve(db_session, reservation_context)


async def test_rollback_removes_both_task_and_command(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    result = await reserve(db_session, reservation_context)
    task_id, command_id = result.task.id, result.command.id
    await db_session.rollback()
    async with session_factory() as fresh:
        assert await fresh.get(BackgroundTask, task_id) is None
        assert await fresh.get(SandboxCommand, command_id) is None


async def test_retry_reuses_the_original_reservation(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    first = await reserve(db_session, reservation_context, spec=reservation_context.spec)
    await db_session.commit()
    second = await reserve(db_session, reservation_context, spec=reservation_context.spec)
    assert second.task.id == first.task.id
    assert second.command.id == first.command.id
    assert first.created and not second.created


async def test_stopped_ancestor_blocks_new_descendants(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    parent = await reserve(db_session, reservation_context)
    child = await reserve(
        db_session,
        reservation_context,
        spec=replace(reservation_context.spec, parent_task_id=parent.task.id),
    )
    parent.task.stop_requested_at = NOW
    await db_session.commit()
    with pytest.raises(TaskExecutionRevokedError):
        await reserve(
            db_session,
            reservation_context,
            spec=replace(
                reservation_context.spec, parent_task_id=child.task.id, tool_call_id="new"
            ),
        )


async def test_unknown_and_legacy_commands_count_toward_shared_cap(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    for _ in range(7):
        result = await reserve(db_session, reservation_context)
        result.task.state = "unknown"
    legacy = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_sandbox_id=reservation_context.details.user_sandbox_id,
        conversation_id=reservation_context.conversation_id,
        run_id=reservation_context.spec.originating_run_id,
        started_by_user_id=result.task.started_by_user_id,
        command="legacy server",
        status="running",
    )
    db_session.add(legacy)
    await db_session.commit()
    with pytest.raises(SandboxCommandCapError):
        await reserve(db_session, reservation_context)


async def test_concurrent_reservations_cannot_take_the_same_last_slot(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    for _ in range(7):
        await reserve(db_session, reservation_context)
    await db_session.commit()

    async def compete() -> bool:
        async with session_factory() as session, session.begin():
            try:
                await reserve(session, reservation_context)
                return True
            except SandboxCommandCapError:
                return False

    assert sorted(await asyncio.gather(compete(), compete())) == [False, True]


@pytest.mark.parametrize("other_scope", ["org", "workspace"])
async def test_scoped_service_cannot_use_foreign_admission(
    db_session: AsyncSession, reservation_context: ReservationContext, other_scope: str
) -> None:
    foreign = BackgroundTaskService(
        db_session,
        org_id="org-unrelated" if other_scope == "org" else DEFAULT_ORG_ID,
        workspace_id="ws-unrelated" if other_scope == "workspace" else DEFAULT_WS_ID,
    )
    with pytest.raises(LookupError, match="admission not found"):
        await foreign.reserve_task(
            admission_id=reservation_context.admission_id,
            task_spec=reservation_context.spec,
            execution_details=reservation_context.details,
            owner_token="other-owner",
            owner_until=NOW + timedelta(seconds=30),
            now=NOW,
        )


async def test_retry_cannot_change_command_or_take_over_owner(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    first = await reserve(db_session, reservation_context, spec=reservation_context.spec)
    await db_session.commit()
    original_owner = first.task.owner_token
    with pytest.raises(ValueError, match="already reserved different work"):
        await reserve(
            db_session,
            reservation_context,
            spec=reservation_context.spec,
            details=replace(reservation_context.details, command="different command"),
        )
    again = await reserve(db_session, reservation_context, spec=reservation_context.spec)
    assert again.task.owner_token == original_owner
    assert not again.created


async def test_database_enforces_one_command_per_task(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    reserved = await reserve(db_session, reservation_context)
    await db_session.commit()
    duplicate = SandboxCommand(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        task_id=reserved.task.id,
        user_sandbox_id=reservation_context.details.user_sandbox_id,
        conversation_id=reservation_context.conversation_id,
        run_id=reservation_context.spec.originating_run_id,
        started_by_user_id=reserved.task.started_by_user_id,
        command="must not become a second process",
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError, match="uq_sandbox_commands_task_id"):
        await db_session.flush()
    await db_session.rollback()


async def test_source_identity_cannot_be_rebound(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    original = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert original is not None
    duplicate = ConversationExecutionAdmission(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=original.conversation_id,
        actor_user_id=original.actor_user_id,
        source_kind=original.source_kind,
        source_id=original.source_id,
        execution_generation=original.execution_generation + 1,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError, match="uq_execution_source"):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.parametrize("mode", ["personal", "dedicated", "creator"])
async def test_reservation_uses_the_existing_personal_and_topic_sandbox_rules(
    db_session: AsyncSession, reservation_context: ReservationContext, mode: str
) -> None:
    conversation = await db_session.get(Conversation, reservation_context.conversation_id)
    sandbox = await db_session.get(UserSandbox, reservation_context.details.user_sandbox_id)
    assert conversation is not None and sandbox is not None
    topic_id: str | None = None
    if mode == "personal":
        conversation.is_group_chat = False
        sandbox.scope_type = "user"
        sandbox.scope_id = conversation.creator_user_id
    else:
        topic = Topic(
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            creator_user_id=conversation.creator_user_id,
            title="reservation topic scope",
            sandbox_mode=mode,
        )
        db_session.add(topic)
        await db_session.flush()
        topic_id = topic.id
        db_session.add(TopicParticipant(topic_id=topic.id, user_id=conversation.creator_user_id))
        conversation.topic_id = topic.id
        sandbox.scope_type = "topic" if mode == "dedicated" else "user"
        sandbox.scope_id = topic.id if mode == "dedicated" else conversation.creator_user_id
    await db_session.commit()
    try:
        result = await reserve(db_session, reservation_context)
        await db_session.commit()
        assert result.command.user_sandbox_id == reservation_context.details.user_sandbox_id
    finally:
        await db_session.rollback()
        if topic_id is not None:
            fresh_conversation = await db_session.get(
                Conversation, reservation_context.conversation_id
            )
            assert fresh_conversation is not None
            fresh_conversation.topic_id = None
            await db_session.flush()
            await db_session.execute(
                delete(TopicParticipant).where(col(TopicParticipant.topic_id) == topic_id)
            )
            await db_session.execute(delete(Topic).where(col(Topic.id) == topic_id))
            await db_session.commit()
