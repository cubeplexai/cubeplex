"""Postgres contracts for the durable HITL steering queue."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import fakeredis.aioredis
import pytest
import pytest_asyncio
from cubepi.providers.base import UserMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import cubeplex.repositories.steering_message as steering_repository
from cubeplex.models import Conversation, SteeringMessageState, User
from cubeplex.repositories import ConversationRepository, SteeringMessageRepository
from cubeplex.repositories.steering_message import (
    SteeringMessageConflictError,
    SteeringMessageQueueFullError,
    list_active_steering_for_reconciliation,
    purge_terminal_steering_tombstones,
)
from cubeplex.streams.run_events import create_run, update_run_meta
from cubeplex.streams.steering_delivery import (
    DurableSteeringCoordinator,
    SteeringRunScope,
)
from tests.e2e.conftest import (
    DEFAULT_ORG_ID,
    DEFAULT_TEST_EMAIL,
    DEFAULT_WS_ID,
    _ensure_default_user_and_membership,
)

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def steering_conversation(
    db_session: AsyncSession,
) -> AsyncIterator[tuple[Conversation, User]]:
    await _ensure_default_user_and_membership()
    user = (
        await db_session.execute(select(User).where(User.email == DEFAULT_TEST_EMAIL))
    ).scalar_one()
    conversation = Conversation(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        creator_user_id=user.id,
        title="durable steering repository test",
    )
    db_session.add(conversation)
    await db_session.commit()
    await db_session.refresh(conversation)
    try:
        yield conversation, user
    finally:
        persisted = await db_session.get(Conversation, conversation.id)
        if persisted is not None:
            await db_session.delete(persisted)
            await db_session.commit()


def _repo(session: AsyncSession) -> SteeringMessageRepository:
    return SteeringMessageRepository(
        session,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
    )


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_but_rejects_mismatched_retry(
    db_session: AsyncSession,
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, user = steering_conversation
    repo = _repo(db_session)

    first, created = await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-original",
        client_steer_id="steer-idempotent",
        content="Use the smaller dataset",
        sender_user_id=user.id,
        sender_display_name="Test User",
        hitl_question_id="question-original",
    )
    await db_session.commit()

    retry, retry_created = await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-later",
        client_steer_id="steer-idempotent",
        content="Use the smaller dataset",
        sender_user_id=user.id,
        sender_display_name="Changed display name",
        hitl_question_id="question-later",
    )

    assert created is True
    assert retry_created is False
    assert retry.id == first.id
    assert retry.run_id == "run-original"
    assert retry.hitl_question_id == "question-original"

    with pytest.raises(SteeringMessageConflictError):
        await repo.enqueue(
            conversation_id=conversation.id,
            run_id="run-original",
            client_steer_id="steer-idempotent",
            content="Different content",
            sender_user_id=user.id,
            sender_display_name=None,
            hitl_question_id="question-original",
        )


@pytest.mark.asyncio
async def test_claim_preserves_acceptance_order_and_cancel_is_terminal(
    db_session: AsyncSession,
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, user = steering_conversation
    repo = _repo(db_session)
    first, _ = await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-order",
        client_steer_id="steer-first",
        content="first",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-order",
    )
    second, _ = await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-order",
        client_steer_id="steer-second",
        content="second",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-order",
    )
    await db_session.commit()

    claimed = await repo.claim_queued(run_id="run-order", owner="worker-a")
    await db_session.commit()

    assert [row.id for row in claimed] == [first.id, second.id]
    assert all(row.state == SteeringMessageState.dispatched for row in claimed)

    cancelled = await repo.request_cancel(
        conversation_id=conversation.id,
        client_steer_id="steer-first",
    )
    assert cancelled is not None
    assert cancelled.state == SteeringMessageState.cancel_requested
    assert await repo.mark_owned_cancelled(row_id=first.id, owner="worker-a") is True
    await db_session.commit()

    visible = await repo.list_for_bootstrap(conversation.id)
    assert [row.client_steer_id for row in visible] == ["steer-second"]


@pytest.mark.asyncio
async def test_enqueue_bounds_do_not_insert_rejected_row(
    db_session: AsyncSession,
    steering_conversation: tuple[Conversation, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation, user = steering_conversation
    repo = _repo(db_session)
    monkeypatch.setattr(steering_repository, "MAX_ACTIVE_ROWS_PER_RUN", 1)
    await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-full",
        client_steer_id="steer-accepted",
        content="accepted",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-full",
    )
    await db_session.commit()

    with pytest.raises(SteeringMessageQueueFullError):
        await repo.enqueue(
            conversation_id=conversation.id,
            run_id="run-full",
            client_steer_id="steer-rejected",
            content="rejected",
            sender_user_id=user.id,
            sender_display_name=None,
            hitl_question_id="question-full",
        )

    assert await repo.count_for_conversation(conversation.id) == 1


@pytest.mark.asyncio
async def test_soft_delete_removes_queued_text(
    db_session: AsyncSession,
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, user = steering_conversation
    steering_repo = _repo(db_session)
    await steering_repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-delete",
        client_steer_id="steer-delete",
        content="private queued text",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-delete",
    )
    await db_session.commit()

    conversation_repo = ConversationRepository(
        db_session,
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        user_id=user.id,
    )
    assert await conversation_repo.delete_conversation(conversation.id) is True
    assert await steering_repo.count_for_conversation(conversation.id) == 0


class _QueueingAgent:
    def __init__(self) -> None:
        self.messages: list[UserMessage] = []

    def steer(self, message: UserMessage) -> None:
        self.messages.append(message)

    def cancel_steer(self, steer_id: str) -> bool:
        for index, message in enumerate(self.messages):
            metadata = getattr(message, "metadata", {})
            if metadata.get("steer_id") == steer_id:
                self.messages.pop(index)
                return True
        return False


class _FailsFirstDeliveryAgent(_QueueingAgent):
    def __init__(self) -> None:
        super().__init__()
        self.attempted_steer_ids: list[str] = []
        self._failed = False

    def steer(self, message: UserMessage) -> None:
        steer_id = message.metadata["steer_id"]
        self.attempted_steer_ids.append(steer_id)
        if not self._failed:
            self._failed = True
            raise RuntimeError("synchronous delivery failed")
        super().steer(message)


@pytest.mark.asyncio
async def test_coordinator_delivers_once_then_acknowledges_after_checkpoint_event(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, user = steering_conversation
    repo = _repo(db_session)
    row, _ = await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-delivery",
        client_steer_id="steer-delivery",
        content="deliver me",
        sender_user_id=user.id,
        sender_display_name="Test User",
        hitl_question_id="question-delivery",
    )
    await db_session.commit()

    async def empty_history(_conversation_id: str) -> set[str]:
        return set()

    coordinator = DurableSteeringCoordinator(
        session_factory,
        history_loader=empty_history,
    )
    agent = _QueueingAgent()
    await coordinator.register_and_drain(
        run_id="run-delivery",
        scope=SteeringRunScope(
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            conversation_id=conversation.id,
        ),
        agent=agent,
    )
    await coordinator.drain("run-delivery")

    assert len(agent.messages) == 1
    message = agent.messages[0]
    assert message.content[0].text == "deliver me"
    assert message.metadata == {
        "steer_id": "steer-delivery",
        "sender_user_id": user.id,
        "sender_display_name": "Test User",
    }
    await db_session.refresh(row)
    assert row.state == SteeringMessageState.dispatched

    await coordinator.acknowledge_injected("run-delivery", "steer-delivery")
    await db_session.refresh(row)
    assert row.state == SteeringMessageState.injected


@pytest.mark.asyncio
async def test_synchronous_delivery_failure_requeues_the_remaining_batch_in_order(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, user = steering_conversation
    repo = _repo(db_session)
    rows = []
    for steer_id in ("steer-batch-first", "steer-batch-second"):
        row, _ = await repo.enqueue(
            conversation_id=conversation.id,
            run_id="run-batch-order",
            client_steer_id=steer_id,
            content=steer_id,
            sender_user_id=user.id,
            sender_display_name=None,
            hitl_question_id="question-batch-order",
        )
        rows.append(row)
    await db_session.commit()

    async def empty_history(_conversation_id: str) -> set[str]:
        return set()

    coordinator = DurableSteeringCoordinator(session_factory, history_loader=empty_history)
    agent = _FailsFirstDeliveryAgent()
    await coordinator.register_and_drain(
        run_id="run-batch-order",
        scope=SteeringRunScope(
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            conversation_id=conversation.id,
        ),
        agent=agent,
    )

    assert agent.messages == []
    for row in rows:
        await db_session.refresh(row)
        assert row.state == SteeringMessageState.queued

    await coordinator.drain("run-batch-order")

    assert agent.attempted_steer_ids == [
        "steer-batch-first",
        "steer-batch-first",
        "steer-batch-second",
    ]
    assert [message.metadata["steer_id"] for message in agent.messages] == [
        "steer-batch-first",
        "steer-batch-second",
    ]


@pytest.mark.asyncio
async def test_unregister_does_not_remove_a_replacement_agent(
    session_factory: async_sessionmaker[AsyncSession],
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, _user = steering_conversation

    async def empty_history(_conversation_id: str) -> set[str]:
        return set()

    coordinator = DurableSteeringCoordinator(session_factory, history_loader=empty_history)
    scope = SteeringRunScope(
        org_id=DEFAULT_ORG_ID,
        workspace_id=DEFAULT_WS_ID,
        conversation_id=conversation.id,
    )
    old_agent = _QueueingAgent()
    replacement_agent = _QueueingAgent()
    await coordinator.register_and_drain(
        run_id="run-replacement",
        scope=scope,
        agent=old_agent,
    )
    await coordinator.register_and_drain(
        run_id="run-replacement",
        scope=scope,
        agent=replacement_agent,
    )

    await coordinator.unregister("run-replacement", agent=old_agent)

    assert coordinator._agents["run-replacement"] is replacement_agent


@pytest.mark.asyncio
async def test_unregister_quiesces_an_in_flight_drain_before_detach(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    steering_conversation: tuple[Conversation, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conversation, user = steering_conversation

    async def empty_history(_conversation_id: str) -> set[str]:
        return set()

    coordinator = DurableSteeringCoordinator(session_factory, history_loader=empty_history)
    agent = _QueueingAgent()
    await coordinator.register_and_drain(
        run_id="run-unregister-race",
        scope=SteeringRunScope(
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            conversation_id=conversation.id,
        ),
        agent=agent,
    )

    row, _ = await _repo(db_session).enqueue(
        conversation_id=conversation.id,
        run_id="run-unregister-race",
        client_steer_id="steer-unregister-race",
        content="deliver before detach completes",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-unregister-race",
    )
    await db_session.commit()

    drain_entered = asyncio.Event()
    release_drain = asyncio.Event()
    original_repair = coordinator._repair_expired_claims

    async def blocked_repair(
        repo: SteeringMessageRepository,
        *,
        scope: SteeringRunScope,
        run_id: str,
    ) -> None:
        drain_entered.set()
        await release_drain.wait()
        await original_repair(repo, scope=scope, run_id=run_id)

    monkeypatch.setattr(coordinator, "_repair_expired_claims", blocked_repair)

    drain_task = asyncio.create_task(coordinator.drain("run-unregister-race"))
    await drain_entered.wait()
    detached = asyncio.Event()

    async def detach_after_unregister() -> None:
        await coordinator.unregister("run-unregister-race", agent=agent)
        detached.set()

    unregister_task = asyncio.create_task(detach_after_unregister())
    await asyncio.sleep(0)

    assert unregister_task.done() is False
    assert detached.is_set() is False

    release_drain.set()
    await asyncio.gather(drain_task, unregister_task)

    assert [message.metadata["steer_id"] for message in agent.messages] == ["steer-unregister-race"]
    assert detached.is_set() is True
    await db_session.refresh(row)
    assert row.state == SteeringMessageState.dispatched


@pytest.mark.asyncio
async def test_owner_poll_processes_committed_cancel_without_redis_wakeup(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, user = steering_conversation
    repo = _repo(db_session)
    row, _ = await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-cancel-poll",
        client_steer_id="steer-cancel-poll",
        content="do not inject me",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-cancel-poll",
    )
    await db_session.commit()

    async def empty_history(_conversation_id: str) -> set[str]:
        return set()

    coordinator = DurableSteeringCoordinator(session_factory, history_loader=empty_history)
    agent = _QueueingAgent()
    await coordinator.register_and_drain(
        run_id="run-cancel-poll",
        scope=SteeringRunScope(
            org_id=DEFAULT_ORG_ID,
            workspace_id=DEFAULT_WS_ID,
            conversation_id=conversation.id,
        ),
        agent=agent,
    )
    assert len(agent.messages) == 1

    async with session_factory() as cancel_session:
        cancel_repo = _repo(cancel_session)
        cancelled = await cancel_repo.request_cancel(
            conversation_id=conversation.id,
            client_steer_id="steer-cancel-poll",
        )
        assert cancelled is not None
        assert cancelled.state == SteeringMessageState.cancel_requested
        await cancel_session.commit()
    await coordinator.poll_once()

    assert agent.messages == []
    await db_session.refresh(row)
    assert row.state == SteeringMessageState.cancelled


@pytest.mark.asyncio
async def test_terminal_tombstones_are_purged_only_after_24_hours(
    db_session: AsyncSession,
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, user = steering_conversation
    repo = _repo(db_session)
    old, _ = await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-purge",
        client_steer_id="steer-old",
        content="old",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-purge",
    )
    recent, _ = await repo.enqueue(
        conversation_id=conversation.id,
        run_id="run-purge",
        client_steer_id="steer-recent",
        content="recent",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-purge",
    )
    await repo.request_cancel(
        conversation_id=conversation.id,
        client_steer_id=old.client_steer_id,
    )
    await repo.request_cancel(
        conversation_id=conversation.id,
        client_steer_id=recent.client_steer_id,
    )
    old.updated_at = datetime.now(UTC) - timedelta(hours=25)
    db_session.add(old)
    await db_session.commit()

    assert await purge_terminal_steering_tombstones(db_session) == 1
    await db_session.commit()
    assert await repo.count_for_conversation(conversation.id) == 1


@pytest.mark.asyncio
async def test_reconciliation_scan_can_advance_past_retained_rows(
    db_session: AsyncSession,
    steering_conversation: tuple[Conversation, User],
) -> None:
    conversation, user = steering_conversation
    repo = _repo(db_session)
    baseline = datetime.now(UTC) + timedelta(days=365)
    for index in range(3):
        row, _ = await repo.enqueue(
            conversation_id=conversation.id,
            run_id=f"run-retained-{index}",
            client_steer_id=f"steer-retained-{index}",
            content=str(index),
            sender_user_id=user.id,
            sender_display_name=None,
            hitl_question_id="question-retained",
        )
        row.updated_at = baseline + timedelta(seconds=index)
        db_session.add(row)
    await db_session.commit()

    first = await list_active_steering_for_reconciliation(
        db_session,
        limit=2,
        after=(baseline - timedelta(seconds=1), ""),
    )
    cursor = (first[-1].updated_at, first[-1].id)
    second = await list_active_steering_for_reconciliation(
        db_session,
        limit=2,
        after=cursor,
    )

    assert [row.client_steer_id for row in first] == ["steer-retained-0", "steer-retained-1"]
    assert [row.client_steer_id for row in second] == ["steer-retained-2"]


@pytest.mark.asyncio
async def test_maintenance_preserves_a_stale_run_with_matching_pending_hitl(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    steering_conversation: tuple[Conversation, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.agents import checkpointer as checkpointer_module

    conversation, user = steering_conversation
    run_id = "run-stale-resumable"
    row, _ = await _repo(db_session).enqueue(
        conversation_id=conversation.id,
        run_id=run_id,
        client_steer_id="steer-stale-resumable",
        content="keep for retry",
        sender_user_id=user.id,
        sender_display_name=None,
        hitl_question_id="question-stale-resumable",
    )
    await db_session.commit()

    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    prefix = f"test-steering-maintenance:{conversation.id}"
    await create_run(
        redis_client,
        prefix=prefix,
        run_id=run_id,
        conversation_id=conversation.id,
        status="running",
        started_at=datetime.now(UTC).isoformat(),
        ttl_seconds=60,
    )
    await update_run_meta(redis_client, prefix=prefix, run_id=run_id, status="stale")

    class _PendingCheckpointer:
        async def load_pending_run_id(self, _conversation_id: str) -> str:
            return run_id

    @asynccontextmanager
    async def fake_shared_checkpointer():
        yield _PendingCheckpointer()

    monkeypatch.setattr(
        checkpointer_module,
        "shared_checkpointer",
        fake_shared_checkpointer,
    )

    async def empty_history(_conversation_id: str) -> set[str]:
        return set()

    coordinator = DurableSteeringCoordinator(
        session_factory,
        history_loader=empty_history,
        redis=redis_client,
        redis_key_prefix=prefix,
    )
    await coordinator.maintain_once()

    await db_session.refresh(row)
    assert row.state == SteeringMessageState.queued
    await redis_client.aclose()
