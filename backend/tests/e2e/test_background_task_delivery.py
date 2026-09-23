import asyncio
import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import fakeredis.aioredis
import msgpack
import pytest
import pytest_asyncio
from cubeloop.providers.base import ReasoningControl, TextContent, UserMessage
from cubeloop.providers.faux import FauxProvider, faux_assistant_message, faux_text
from cubeloop.session.input import InputReceipt
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.models import (
    BackgroundTask,
    BackgroundTaskEvent,
    Conversation,
    ConversationExecutionAdmission,
    SteeringMessage,
    User,
)
from cubeplex.models.steering_message import SteeringMessageState
from cubeplex.repositories.steering_message import (
    MAX_ACTIVE_ROWS_PER_RUN,
    SteeringMessageRepository,
)
from cubeplex.services.background_task_delivery import (
    BackgroundTaskDeliveryCoordinator,
    BackgroundTaskDeliveryService,
    render_background_task_notice,
)
from cubeplex.services.conversation_execution import (
    ConversationExecutionService,
    ExecutionRevokedError,
    UserMessageIntent,
)
from cubeplex.streams.run_events import create_run, get_active_run
from cubeplex.streams.run_manager import RunContext, RunManager
from cubeplex.streams.steering_delivery import DurableSteeringCoordinator, SteeringRunScope
from tests.e2e import test_admitted_run_execution as run_fixtures
from tests.e2e import test_background_task_reservation as reservation_fixtures
from tests.e2e.test_background_task_reservation import NOW, ReservationContext, reserve
from tests.e2e.test_conversation_execution_control import snapshot

reservation_context = reservation_fixtures.reservation_context
run_manager = run_fixtures.run_manager


@pytest_asyncio.fixture(autouse=True)
async def cleanup_background_inputs(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
):  # noqa: ANN201
    yield
    await db_session.rollback()
    await db_session.execute(
        delete(SteeringMessage).where(SteeringMessage.source_kind == "background_task")
    )
    await db_session.commit()


async def _event(
    session: AsyncSession,
    context: ReservationContext,
    *,
    readiness: str,
) -> BackgroundTaskEvent:
    item = await reserve(session, context)
    item.task.backgrounded_at = NOW
    item.task.state = "succeeded"
    item.task.result_readiness = readiness
    item.task.result_summary = "final output"
    item.task.result_ref = "/tmp/final.log"
    event = BackgroundTaskEvent(
        org_id=item.task.org_id,
        workspace_id=item.task.workspace_id,
        conversation_id=item.task.conversation_id,
        task_id=item.task.id,
        execution_generation=item.task.execution_generation,
        reason="completion",
        dedupe_key="completion",
    )
    session.add(event)
    await session.commit()
    return event


async def _stage_checkpoint_under_advisory_lock(
    session: AsyncSession,
    *,
    conversation_id: str,
    run_id: str,
    notice_id: str,
    input_id: str,
) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:thread_id))"),
        {"thread_id": conversation_id},
    )
    await session.execute(
        text(
            "INSERT INTO cubepi_threads (thread_id) VALUES (:thread_id) "
            "ON CONFLICT (thread_id) DO NOTHING"
        ),
        {"thread_id": conversation_id},
    )
    await session.execute(
        text(
            "INSERT INTO cubepi_runs (thread_id, run_id) VALUES (:thread_id, :run_id) "
            "ON CONFLICT (thread_id, run_id) DO NOTHING"
        ),
        {"thread_id": conversation_id, "run_id": run_id},
    )
    last_seq = int(
        await session.scalar(
            text("SELECT COALESCE(MAX(seq), 0) FROM cubepi_messages WHERE thread_id = :thread_id"),
            {"thread_id": conversation_id},
        )
        or 0
    )
    checkpoint_message = UserMessage(
        content=[TextContent(text="Background task result")],
        metadata={"notice_id": notice_id, "input_id": input_id},
        run_id=run_id,
    )
    await session.execute(
        text(
            "INSERT INTO cubepi_messages "
            "(thread_id, seq, role, metadata, payload, run_id) "
            "VALUES (:thread_id, :seq, 'user', CAST(:metadata AS jsonb), :payload, :run_id)"
        ),
        {
            "thread_id": conversation_id,
            "seq": last_seq + 1,
            "metadata": json.dumps(checkpoint_message.metadata),
            "payload": msgpack.packb(checkpoint_message.model_dump(mode="json"), use_bin_type=True),
            "run_id": run_id,
        },
    )


async def test_pending_result_is_not_claimed(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    event = await _event(db_session, reservation_context, readiness="pending")

    claims = await BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )

    assert claims == []
    await db_session.refresh(event)
    assert event.state == "pending"


async def test_ready_result_claims_one_internal_notice(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")

    claims = await BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    await db_session.commit()

    assert len(claims) == 1
    assert claims[0].notice.model_dump() == {
        "source": "background_task",
        "notice_id": event.id,
        "task_id": event.task_id,
        "task_kind": "command",
        "originating_run_id": reservation_context.spec.originating_run_id,
        "execution_generation": 0,
        "reason": "completion",
        "summary": "final output",
        "result_ref": "/tmp/final.log",
    }
    await db_session.refresh(event)
    assert event.state == "claimed"
    assert event.owner_token == "worker-1"
    assert event.delivery_input_id == event.id
    assert event.summary == "final output"
    assert event.result_ref == "/tmp/final.log"


async def test_expired_unbound_claim_is_taken_over_once(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    await db_session.commit()

    claims = await service.claim_ready(
        owner_token="worker-2",
        now=NOW + timedelta(seconds=31),
        owner_until=NOW + timedelta(seconds=61),
    )
    await db_session.commit()

    assert [claim.notice.notice_id for claim in claims] == [event.id]
    await db_session.refresh(event)
    assert event.owner_token == "worker-2"


async def test_closed_generation_discards_notice_instead_of_reopening_work(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    conversation = await db_session.get(Conversation, event.conversation_id)
    assert conversation is not None
    conversation.execution_closed_at = NOW
    await db_session.commit()

    claims = await BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    await db_session.commit()

    assert claims == []
    await db_session.refresh(event)
    assert event.state == "discarded"
    assert event.discard_reason == "generation_closed"


async def test_checkpoint_ack_is_fenced_to_bound_attempt(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    event = await _event(db_session, reservation_context, readiness="unavailable")
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    [claim] = await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    assert await service.bind_attempt(
        notice_id=event.id,
        owner_token="worker-1",
        run_id="delivery-run",
        attempt_id="attempt-1",
    )
    assert not await service.acknowledge_checkpoint(
        notice_id=event.id,
        run_id="delivery-run",
        attempt_id="stale-attempt",
        input_id=claim.input_id,
        now=NOW,
    )
    assert await service.acknowledge_checkpoint(
        notice_id=event.id,
        run_id="delivery-run",
        attempt_id="attempt-1",
        input_id=claim.input_id,
        now=NOW,
    )
    await db_session.commit()

    await db_session.refresh(event)
    assert event.state == "delivered"
    assert event.checkpoint_run_id == "delivery-run"
    assert event.checkpoint_input_id == event.id
    assert event.delivered_at == NOW


async def test_active_run_delivery_is_internal_and_hidden_from_user_steering(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    await db_session.commit()
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )

    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    await db_session.commit()

    assert steering_id is not None
    queued = await SteeringMessageRepository(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).get_by_client_id(
        conversation_id=event.conversation_id,
        client_steer_id=event.id,
    )
    assert queued is not None
    assert queued.source_kind == "background_task"
    assert queued.notice_id == event.id
    assert queued.execution_generation == 0
    assert (
        await SteeringMessageRepository(
            db_session, org_id=event.org_id, workspace_id=event.workspace_id
        ).list_for_bootstrap(event.conversation_id)
        == []
    )
    await db_session.refresh(event)
    assert event.delivery_run_id == admission.run_id
    assert event.delivery_attempt_id == "attempt-1"


async def test_input_checkpoint_ack_finishes_notice_outbox(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    queued = await db_session.get(SteeringMessage, steering_id)
    assert queued is not None
    coordinator = DurableSteeringCoordinator(async_session_maker)
    queued.state = SteeringMessageState.dispatched
    queued.delivery_owner = coordinator._owner
    await db_session.commit()

    await coordinator.acknowledge_injected(
        admission.run_id,
        event.id,
        scope=SteeringRunScope(
            org_id=event.org_id,
            workspace_id=event.workspace_id,
            conversation_id=event.conversation_id,
        ),
    )

    await db_session.refresh(event)
    await db_session.refresh(queued)
    assert event.state == "delivered"
    assert event.checkpoint_input_id == event.id
    assert queued.state == SteeringMessageState.injected


async def test_synchronous_checkpoint_receipt_finishes_notice_outbox(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    await db_session.commit()
    execution_session = MagicMock()
    execution_session.submit_input.return_value = InputReceipt(
        input_id=event.id,
        status="committed",
        durability="checkpoint",
    )
    coordinator = DurableSteeringCoordinator(async_session_maker)

    await coordinator.register_and_drain(
        run_id=admission.run_id,
        scope=SteeringRunScope(
            org_id=event.org_id,
            workspace_id=event.workspace_id,
            conversation_id=event.conversation_id,
        ),
        session=execution_session,
    )

    await db_session.refresh(event)
    steering = await db_session.get(SteeringMessage, steering_id)
    assert event.state == "delivered"
    assert steering is not None
    assert steering.state == SteeringMessageState.injected


async def test_coordinator_routes_ready_notice_to_same_actor_active_run(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    await db_session.commit()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await create_run(
        redis,
        prefix="delivery-test",
        run_id=admission.run_id,
        conversation_id=event.conversation_id,
        status="running",
        started_at=NOW.isoformat(),
        ttl_seconds=300,
        claim_token="attempt-1",
    )
    active = await get_active_run(
        redis,
        prefix="delivery-test",
        conversation_id=event.conversation_id,
    )
    assert active is not None and active.run_id == admission.run_id
    run_manager = MagicMock()
    run_manager.drain_durable_steering = AsyncMock()
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager,
        redis=redis,
        redis_key_prefix="delivery-test",
    )

    routed = await coordinator.deliver_once(
        now=NOW,
        lease_until=NOW + timedelta(seconds=30),
    )

    await db_session.refresh(event)
    assert routed == [event.id], (
        event.state,
        event.discard_reason,
        event.owner_token,
        event.delivery_run_id,
        event.delivery_attempt_id,
    )
    run_manager.drain_durable_steering.assert_awaited_once_with(admission.run_id)
    assert event.delivery_run_id == admission.run_id


async def test_full_active_run_queue_releases_notice_for_retry(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    repo = SteeringMessageRepository(
        db_session,
        org_id=event.org_id,
        workspace_id=event.workspace_id,
    )
    for index in range(MAX_ACTIVE_ROWS_PER_RUN):
        await repo.enqueue(
            conversation_id=event.conversation_id,
            run_id=admission.run_id,
            client_steer_id=f"full-{index}",
            content=f"queued input {index}",
            sender_user_id=admission.actor_user_id,
            sender_display_name=None,
            hitl_question_id=None,
        )
    await db_session.commit()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await create_run(
        redis,
        prefix="delivery-full",
        run_id=admission.run_id,
        conversation_id=event.conversation_id,
        status="running",
        started_at=NOW.isoformat(),
        ttl_seconds=300,
        claim_token="attempt-1",
    )
    run_manager_mock = MagicMock()
    run_manager_mock.drain_durable_steering = AsyncMock()
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager_mock,
        redis=redis,
        redis_key_prefix="delivery-full",
    )

    assert (
        await coordinator.deliver_once(
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
        )
        == []
    )

    await db_session.refresh(event)
    assert event.state == "pending"
    assert event.delivery_run_id is None
    run_manager_mock.drain_durable_steering.assert_not_awaited()


async def test_notice_waits_while_another_actor_owns_the_active_run(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    other = User(email=f"notice-other-{uuid4()}@example.com", hashed_password="unused")
    db_session.add(other)
    await db_session.flush()
    other_admission = ConversationExecutionAdmission(
        org_id=event.org_id,
        workspace_id=event.workspace_id,
        conversation_id=event.conversation_id,
        actor_user_id=other.id,
        source_kind="user_message",
        source_id=f"web:{uuid4()}",
        execution_generation=event.execution_generation,
        run_id=str(uuid4()),
        run_start_token="other-attempt",
        run_started_at=NOW,
    )
    db_session.add(other_admission)
    await db_session.commit()
    assert other_admission.run_id is not None
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await create_run(
        redis,
        prefix="delivery-other-actor",
        run_id=other_admission.run_id,
        conversation_id=event.conversation_id,
        status="running",
        started_at=NOW.isoformat(),
        ttl_seconds=300,
        claim_token="other-attempt",
    )
    run_manager_mock = MagicMock()
    run_manager_mock.drain_durable_steering = AsyncMock()
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager_mock,
        redis=redis,
        redis_key_prefix="delivery-other-actor",
    )

    assert (
        await coordinator.deliver_once(
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
        )
        == []
    )

    await db_session.refresh(event)
    assert event.state == "pending"
    assert event.delivery_run_id is None
    run_manager_mock.drain_durable_steering.assert_not_awaited()
    await db_session.delete(other_admission)
    await db_session.delete(other)
    await db_session.commit()


async def test_notice_waits_while_conversation_is_paused_for_hitl(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    await db_session.commit()
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await create_run(
        redis,
        prefix="delivery-hitl",
        run_id=admission.run_id,
        conversation_id=event.conversation_id,
        status="paused_hitl",
        started_at=NOW.isoformat(),
        ttl_seconds=300,
        claim_token="attempt-1",
    )
    run_manager_mock = MagicMock()
    run_manager_mock.start_background_notice = AsyncMock()
    run_manager_mock.drain_durable_steering = AsyncMock()
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager_mock,
        redis=redis,
        redis_key_prefix="delivery-hitl",
    )

    assert (
        await coordinator.deliver_once(
            now=NOW,
            lease_until=NOW + timedelta(seconds=30),
        )
        == []
    )

    await db_session.refresh(event)
    assert event.state == "pending"
    run_manager_mock.start_background_notice.assert_not_awaited()
    run_manager_mock.drain_durable_steering.assert_not_awaited()


async def test_coordinator_starts_idle_notice_without_releasing_claim(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    run_manager_mock = MagicMock()
    run_manager_mock.start_background_notice = AsyncMock(return_value=True)
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager_mock,
        redis=redis,
        redis_key_prefix="delivery-idle-test",
    )

    routed = await coordinator.deliver_once(
        now=NOW,
        lease_until=NOW + timedelta(seconds=30),
    )

    assert routed == [event.id]
    run_manager_mock.start_background_notice.assert_awaited_once_with(
        notice_id=event.id,
        owner_token=coordinator.owner_token,
        org_id=event.org_id,
        workspace_id=event.workspace_id,
    )
    await db_session.refresh(event)
    assert event.state == "claimed"
    assert event.owner_token == coordinator.owner_token


async def test_stopped_idle_notice_is_discarded_instead_of_retried(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    assert task is not None
    run_manager_mock = MagicMock()
    run_manager_mock.start_background_notice = AsyncMock(
        side_effect=ExecutionRevokedError("background notice execution was stopped")
    )
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager_mock,
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
        redis_key_prefix="delivery-stopped-idle",
    )
    delivery = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    [claim] = await delivery.claim_ready(
        owner_token=coordinator.owner_token,
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    admitted = await ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token=coordinator.owner_token,
        execution_generation=event.execution_generation,
        intent=UserMessageIntent(content=render_background_task_notice(claim.notice)),
        snapshot=snapshot(),
        now=NOW,
    )
    admitted.admission.run_stop_requested_at = NOW
    admitted.admission.run_terminal_status = "cancelled"
    admitted.admission.run_terminal_at = NOW
    admitted.admission.run_finished_at = NOW
    assert await delivery.release_unbound(
        notice_id=event.id,
        owner_token=coordinator.owner_token,
    )
    await db_session.commit()

    assert (
        await coordinator.deliver_once(
            now=NOW + timedelta(seconds=1),
            lease_until=NOW + timedelta(seconds=31),
        )
        == []
    )

    await db_session.refresh(event)
    assert event.state == "discarded"
    assert event.discard_reason == "run_stop"


@pytest.mark.parametrize("recovery_path", ["registration", "pause"])
@pytest.mark.parametrize("checkpointed", [False, True])
async def test_session_recovery_defers_background_proof_to_delivery_coordinator(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    recovery_path: str,
    checkpointed: bool,
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    delivery = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await delivery.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    steering_id = await delivery.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    coordinator = DurableSteeringCoordinator(
        async_session_maker,
        history_loader=AsyncMock(return_value={event.id} if checkpointed else set()),
    )
    steering = await db_session.get(SteeringMessage, steering_id)
    assert steering is not None
    steering.state = SteeringMessageState.dispatched
    steering.delivery_owner = coordinator._owner
    steering.delivery_lease_until = NOW + timedelta(seconds=30)
    await db_session.commit()
    scope = SteeringRunScope(
        org_id=event.org_id,
        workspace_id=event.workspace_id,
        conversation_id=event.conversation_id,
    )

    if recovery_path == "registration":
        await coordinator._repair_checkpointed_owned_claims(
            run_id=admission.run_id,
            scope=scope,
        )
    else:
        execution_session = MagicMock()
        execution_session.cancel_input.return_value = InputReceipt(
            input_id=event.id,
            status="committed" if checkpointed else "cancelled",
            durability="checkpoint" if checkpointed else None,
        )
        await coordinator._reconcile_owned_before_pause(
            run_id=admission.run_id,
            scope=scope,
            session=execution_session,
        )

    await db_session.refresh(event)
    await db_session.refresh(steering)
    assert event.state == "claimed"
    assert steering.state == (
        SteeringMessageState.dispatched if checkpointed else SteeringMessageState.queued
    )


async def test_idle_run_binds_notice_before_execution_task_is_scheduled(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch,
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    conversation = await db_session.get(Conversation, event.conversation_id)
    assert task is not None and conversation is not None
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    [claim] = await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    content = "Background task result:\n" + claim.notice.model_dump_json()
    admitted = await ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token="worker-1",
        execution_generation=event.execution_generation,
        intent=UserMessageIntent(content=content),
        snapshot=snapshot(),
        now=NOW,
    )
    await db_session.commit()
    execute = AsyncMock(return_value=None)
    monkeypatch.setattr(run_manager, "_execute_run", execute)
    assert admitted.admission.run_id is not None

    await run_manager.start_run(
        conversation_id=event.conversation_id,
        content=content,
        ctx=RunContext(
            user_id=task.started_by_user_id,
            org_id=event.org_id,
            workspace_id=event.workspace_id,
            conversation_id=event.conversation_id,
        ),
        run_id=admitted.admission.run_id,
        model_key=conversation.model_key,
        reasoning=ReasoningControl.model_validate(conversation.reasoning),
        llm_snapshot=snapshot(),
        input_metadata={
            **claim.notice.model_dump(mode="json"),
            "input_id": claim.input_id,
        },
        admission_id=admitted.admission.id,
        background_notice_id=event.id,
        background_notice_owner_token="worker-1",
    )
    await run_manager._tasks[admitted.admission.run_id]

    await db_session.refresh(event)
    await db_session.refresh(admitted.admission)
    assert event.delivery_run_id == admitted.admission.run_id
    assert event.delivery_attempt_id == admitted.admission.run_start_token
    assert event.delivery_input_id == event.id


async def test_run_manager_admits_idle_notice_as_original_actor(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch,
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    assert task is not None
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    await db_session.commit()
    start_run = AsyncMock(return_value="delivery-run")
    monkeypatch.setattr(run_manager, "start_run", start_run)
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        AsyncMock(return_value=snapshot()),
    )

    assert await run_manager.start_background_notice(
        notice_id=event.id,
        owner_token="worker-1",
        org_id=event.org_id,
        workspace_id=event.workspace_id,
    )

    admission = await db_session.scalar(
        select(ConversationExecutionAdmission).where(
            ConversationExecutionAdmission.source_kind == "background_task",
            ConversationExecutionAdmission.source_id == event.id,
        )
    )
    assert admission is not None
    assert admission.actor_user_id == task.started_by_user_id
    kwargs = start_run.await_args.kwargs
    assert kwargs["admission_id"] == admission.id
    assert kwargs["background_notice_id"] == event.id
    assert kwargs["background_notice_owner_token"] == "worker-1"
    assert kwargs["input_metadata"]["source"] == "background_task"
    assert kwargs["input_metadata"]["notice_id"] == event.id


async def test_idle_notice_is_delivered_only_after_initial_checkpoint(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch,
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    await db_session.commit()
    provider = FauxProvider(provider_id="provider")
    provider.set_responses(
        [faux_assistant_message([faux_text("notice handled")], stop_reason="stop")]
    )
    monkeypatch.setattr("cubeplex.llm.builder.build_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        AsyncMock(return_value=snapshot()),
    )

    try:
        assert await run_manager.start_background_notice(
            notice_id=event.id,
            owner_token="worker-1",
            org_id=event.org_id,
            workspace_id=event.workspace_id,
        )
        await run_manager.drain(timeout_seconds=30)

        await db_session.refresh(event)
        assert event.state == "delivered"
        assert event.checkpoint_run_id == event.delivery_run_id
        assert event.checkpoint_input_id == event.id
        async with shared_checkpointer() as checkpointer:
            checkpoint = await checkpointer.load(event.conversation_id)
        assert checkpoint is not None
        initial = checkpoint.messages[0]
        assert initial.metadata["source"] == "background_task"
        assert initial.metadata["notice_id"] == event.id
        assert provider.call_count == 1
    finally:
        await run_fixtures.cleanup_run_rows(db_session, event.conversation_id)


async def test_failed_admission_finish_keeps_uncheckpointed_initial_binding(
    db_session: AsyncSession,
    reservation_context: ReservationContext,
    run_manager: RunManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    delivery = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await delivery.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    await db_session.commit()
    monkeypatch.setattr(
        "cubeplex.llm.snapshot.load_llm_snapshot",
        AsyncMock(return_value=snapshot()),
    )
    monkeypatch.setattr(
        "cubeplex.llm.builder.build_provider",
        lambda *args, **kwargs: FauxProvider(provider_id="provider"),
    )
    monkeypatch.setattr(
        "cubeplex.streams.execution_adapter.execute_session",
        AsyncMock(side_effect=RuntimeError("failed before initial checkpoint")),
    )
    finish = AsyncMock(return_value=False)
    monkeypatch.setattr(ConversationExecutionService, "record_run_finished", finish)

    try:
        assert await run_manager.start_background_notice(
            notice_id=event.id,
            owner_token="worker-1",
            org_id=event.org_id,
            workspace_id=event.workspace_id,
        )
        admission = await db_session.scalar(
            select(ConversationExecutionAdmission).where(
                ConversationExecutionAdmission.source_kind == "background_task",
                ConversationExecutionAdmission.source_id == event.id,
            )
        )
        assert admission is not None and admission.run_id is not None
        execution_task = run_manager._tasks[admission.run_id]
        [result] = await asyncio.gather(execution_task, return_exceptions=True)
        assert result is None

        await db_session.refresh(event)
        await db_session.refresh(admission)
        assert event.state == "claimed"
        assert event.delivery_run_id == admission.run_id
        assert event.delivery_attempt_id == admission.run_start_token
        assert admission.run_finished_at is None
        finish.assert_awaited_once()
    finally:
        await run_fixtures.cleanup_run_rows(db_session, event.conversation_id)


async def test_uncommitted_append_returns_to_notice_queue_after_run_ends(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    await db_session.commit()
    coordinator = DurableSteeringCoordinator(
        async_session_maker,
        history_loader=AsyncMock(return_value=set()),
    )

    await coordinator.finalize_run(
        admission.run_id,
        scope=SteeringRunScope(
            org_id=event.org_id,
            workspace_id=event.workspace_id,
            conversation_id=event.conversation_id,
        ),
    )

    await db_session.refresh(event)
    assert event.state == "pending"
    assert event.delivery_run_id is None
    assert event.delivery_attempt_id is None
    assert (
        await db_session.scalar(select(SteeringMessage).where(SteeringMessage.id == steering_id))
        is None
    )


async def test_steering_maintenance_defers_all_background_proof_to_fenced_recovery(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    await db_session.commit()
    coordinator = DurableSteeringCoordinator(
        async_session_maker,
        history_loader=AsyncMock(return_value={event.id}),
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
        redis_key_prefix="steering-defers-background",
    )

    await coordinator.maintain_once()

    await db_session.refresh(event)
    steering = await db_session.get(SteeringMessage, steering_id)
    assert event.state == "claimed"
    assert event.delivery_run_id == admission.run_id
    assert steering is not None
    assert steering.state == SteeringMessageState.queued


async def test_task_stop_discards_queued_append_without_touching_other_inputs(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    other_event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert task is not None and admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    other_steering_id = await service.enqueue_for_active_run(
        notice_id=other_event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    assert steering_id is not None and other_steering_id is not None
    task.notifications_cancelled_at = NOW
    task.stop_reason = "user_stop"
    await db_session.commit()
    run_manager_mock = MagicMock()
    run_manager_mock.notify_durable_cancel = AsyncMock()
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager_mock,
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
        redis_key_prefix="delivery-cancel",
    )

    assert await coordinator.cancel_revoked_appends_once() == [event.id]

    await db_session.refresh(event)
    assert event.state == "discarded"
    assert event.discard_reason == "user_stop"
    assert (
        await db_session.scalar(select(SteeringMessage).where(SteeringMessage.id == steering_id))
        is None
    )
    other_steering = await db_session.get(SteeringMessage, other_steering_id)
    await db_session.refresh(other_event)
    assert other_steering is not None
    assert other_steering.state == SteeringMessageState.queued
    assert other_event.state == "claimed"
    run_manager_mock.notify_durable_cancel.assert_not_awaited()
    await db_session.delete(other_steering)
    await db_session.commit()


async def test_queued_notice_cancellation_commits_before_settlement(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert task is not None and admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    task.notifications_cancelled_at = NOW
    task.stop_reason = "user_stop"
    await db_session.commit()

    original = BackgroundTaskDeliveryService.settle_uncommitted_attempt
    settlement_started = asyncio.Event()

    async def observe_unlocked_steering(self, **kwargs):  # noqa: ANN001, ANN003, ANN202
        async with session_factory() as probe, probe.begin():
            row = await probe.scalar(
                select(SteeringMessage)
                .where(SteeringMessage.id == steering_id)
                .with_for_update(nowait=True)
            )
            assert row is not None
        settlement_started.set()
        return await original(self, **kwargs)

    monkeypatch.setattr(
        BackgroundTaskDeliveryService,
        "settle_uncommitted_attempt",
        observe_unlocked_steering,
    )
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=MagicMock(),
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
        redis_key_prefix="delivery-cancel-order",
    )

    assert await coordinator.cancel_revoked_appends_once() == [event.id]
    assert settlement_started.is_set()


async def test_task_stop_requests_cancel_for_dispatched_append_then_reconciles(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert task is not None and admission is not None and admission.run_id is not None
    admission.run_start_token = "attempt-1"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    steering = await db_session.get(SteeringMessage, steering_id)
    assert steering is not None
    steering.state = SteeringMessageState.dispatched
    steering.delivery_owner = "session-owner"
    steering.delivery_lease_until = NOW + timedelta(seconds=30)
    task.notifications_cancelled_at = NOW
    task.stop_reason = "user_stop"
    await db_session.commit()
    run_manager_mock = MagicMock()
    run_manager_mock.notify_durable_cancel = AsyncMock()
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=run_manager_mock,
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
        redis_key_prefix="delivery-cancel-dispatched",
    )

    assert await coordinator.cancel_revoked_appends_once() == []

    await db_session.refresh(steering)
    assert steering.state == SteeringMessageState.cancel_requested
    run_manager_mock.notify_durable_cancel.assert_awaited_once_with(
        admission.run_id,
        event.id,
    )
    durable = DurableSteeringCoordinator(
        async_session_maker,
        history_loader=AsyncMock(return_value=set()),
    )
    await durable.finalize_run(
        admission.run_id,
        scope=SteeringRunScope(
            org_id=event.org_id,
            workspace_id=event.workspace_id,
            conversation_id=event.conversation_id,
        ),
    )
    await db_session.refresh(event)
    assert event.state == "discarded"
    assert event.discard_reason == "user_stop"
    assert (
        await db_session.scalar(select(SteeringMessage).where(SteeringMessage.id == steering_id))
        is None
    )


async def test_cancelled_uncommitted_initial_notice_is_discarded(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    assert task is not None
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    [claim] = await service.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    admitted = await ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token="worker-1",
        execution_generation=event.execution_generation,
        intent=UserMessageIntent(content="Background task result"),
        snapshot=snapshot(),
        now=NOW,
    )
    assert admitted.admission.run_id is not None
    admitted.admission.run_start_token = "attempt-1"
    admitted.admission.run_started_at = NOW
    assert await service.bind_initial_attempt(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=admitted.admission.run_id,
        attempt_id="attempt-1",
    )

    assert await service.settle_uncommitted_attempt(
        notice_id=event.id,
        run_id=admitted.admission.run_id,
        input_id=claim.input_id,
        discard_cancelled_initial=True,
    )
    await db_session.commit()

    await db_session.refresh(event)
    assert event.state == "discarded"
    assert event.discard_reason == "run_stop"


async def test_failed_uncommitted_initial_notice_gets_a_fresh_run(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    assert task is not None
    delivery = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    [claim] = await delivery.claim_ready(
        owner_token="worker-1",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    execution = ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    intent = UserMessageIntent(content="Background task result")
    first = await execution.admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token="worker-1",
        execution_generation=event.execution_generation,
        intent=intent,
        snapshot=snapshot(),
        now=NOW,
    )
    first_run_id = first.admission.run_id
    assert first_run_id is not None
    first.admission.run_start_token = "attempt-1"
    first.admission.run_start_requested_at = NOW
    first.admission.run_started_at = NOW
    first.admission.run_terminal_status = "failed"
    first.admission.run_terminal_at = NOW
    first.admission.run_finished_at = NOW
    assert await delivery.bind_initial_attempt(
        notice_id=event.id,
        owner_token="worker-1",
        run_id=first_run_id,
        attempt_id="attempt-1",
    )
    assert await delivery.settle_uncommitted_attempt(
        notice_id=event.id,
        run_id=first_run_id,
        input_id=claim.input_id,
        discard_cancelled_initial=False,
    )
    await db_session.commit()
    await delivery.claim_ready(
        owner_token="worker-2",
        now=NOW + timedelta(seconds=1),
        owner_until=NOW + timedelta(seconds=31),
    )

    retried = await execution.admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token="worker-2",
        execution_generation=event.execution_generation,
        intent=intent,
        snapshot=snapshot(),
        now=NOW + timedelta(seconds=1),
    )

    assert retried.admission.id == first.admission.id
    assert retried.admission.run_id != first_run_id
    assert retried.admission.run_start_token is None
    assert retried.admission.run_started_at is None
    assert retried.admission.run_finished_at is None
    assert retried.admission.run_terminal_status is None


async def test_worker_restart_releases_uncheckpointed_initial_notice(
    db_session: AsyncSession, reservation_context: ReservationContext
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    assert task is not None
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    claims = await service.claim_ready(
        owner_token="dead-worker",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    claim = next(item for item in claims if item.notice.notice_id == event.id)
    admitted = await ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token="dead-worker",
        execution_generation=event.execution_generation,
        intent=UserMessageIntent(content="Background task result"),
        snapshot=snapshot(),
        now=NOW,
    )
    assert admitted.admission.run_id is not None
    initial_run_id = admitted.admission.run_id
    admitted.admission.run_start_token = "dead-attempt"
    admitted.admission.run_started_at = NOW
    assert await service.bind_initial_attempt(
        notice_id=event.id,
        owner_token="dead-worker",
        run_id=admitted.admission.run_id,
        attempt_id="dead-attempt",
    )
    await db_session.commit()
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=MagicMock(),
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
        redis_key_prefix="delivery-restart",
    )

    assert await coordinator.reconcile_bound_once(now=NOW + timedelta(minutes=1)) == [event.id]

    await db_session.refresh(event)
    assert event.state == "pending"
    assert event.delivery_run_id is None
    assert event.delivery_attempt_id is None
    assert event.delivery_input_id == claim.input_id

    await db_session.refresh(admitted.admission)
    assert admitted.admission.run_finished_at == NOW + timedelta(minutes=1)
    assert admitted.admission.run_terminal_status == "failed"

    retry_claims = await service.claim_ready(
        owner_token="replacement-worker",
        now=NOW + timedelta(minutes=1, seconds=1),
        owner_until=NOW + timedelta(minutes=1, seconds=31),
    )
    retry_claim = next(item for item in retry_claims if item.notice.notice_id == event.id)
    retried = await ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token="replacement-worker",
        execution_generation=event.execution_generation,
        intent=UserMessageIntent(content="Background task result"),
        snapshot=snapshot(),
        now=NOW + timedelta(minutes=1, seconds=1),
    )
    assert retried.admission.id == admitted.admission.id
    assert retried.admission.run_id != initial_run_id
    assert retried.admission.run_id is not None
    assert await ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).claim_run_start(
        admission_id=retried.admission.id,
        attempt_id="replacement-attempt",
        now=NOW + timedelta(minutes=1, seconds=2),
    )
    assert await service.bind_initial_attempt(
        notice_id=event.id,
        owner_token="replacement-worker",
        run_id=retried.admission.run_id,
        attempt_id="replacement-attempt",
    )
    assert await service.acknowledge_checkpoint(
        notice_id=event.id,
        run_id=retried.admission.run_id,
        attempt_id="replacement-attempt",
        input_id=retry_claim.input_id,
        now=NOW + timedelta(minutes=1, seconds=3),
    )
    await db_session.commit()

    await db_session.refresh(event)
    assert event.state == "delivered"


async def test_worker_restart_fences_inflight_checkpoint_before_retirement(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    assert task is not None
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    claims = await service.claim_ready(
        owner_token="dead-worker",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    claim = next(item for item in claims if item.notice.notice_id == event.id)
    admitted = await ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token="dead-worker",
        execution_generation=event.execution_generation,
        intent=UserMessageIntent(content="Background task result"),
        snapshot=snapshot(),
        now=NOW,
    )
    assert admitted.admission.run_id is not None
    admitted.admission.run_start_token = "dead-attempt"
    admitted.admission.run_started_at = NOW
    assert await service.bind_initial_attempt(
        notice_id=event.id,
        owner_token="dead-worker",
        run_id=admitted.admission.run_id,
        attempt_id="dead-attempt",
    )
    await db_session.commit()

    blocker = session_factory()
    await blocker.begin()
    await _stage_checkpoint_under_advisory_lock(
        blocker,
        conversation_id=event.conversation_id,
        run_id=admitted.admission.run_id,
        notice_id=event.id,
        input_id=claim.input_id,
    )
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=MagicMock(),
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
        redis_key_prefix="delivery-checkpoint-race",
    )
    reconciliation = asyncio.create_task(
        coordinator.reconcile_bound_once(now=NOW + timedelta(minutes=1))
    )
    try:
        await asyncio.sleep(0.1)
        assert not reconciliation.done()
        await blocker.commit()
        assert await asyncio.wait_for(reconciliation, timeout=2) == [event.id]

        await db_session.refresh(event)
        await db_session.refresh(admitted.admission)
        assert event.state == "delivered"
        assert event.checkpoint_input_id == claim.input_id
        assert admitted.admission.run_finished_at == NOW + timedelta(minutes=1)
        assert admitted.admission.run_terminal_status == "failed"
    finally:
        if blocker.in_transaction():
            await blocker.rollback()
        await blocker.close()
        if not reconciliation.done():
            reconciliation.cancel()
            await asyncio.gather(reconciliation, return_exceptions=True)
        await run_fixtures.cleanup_run_rows(db_session, event.conversation_id)


async def test_worker_restart_fences_inflight_appended_notice(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "foreground-attempt"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    claims = await service.claim_ready(
        owner_token="dead-worker",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    claim = next(item for item in claims if item.notice.notice_id == event.id)
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="dead-worker",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    await db_session.commit()

    blocker = session_factory()
    await blocker.begin()
    await _stage_checkpoint_under_advisory_lock(
        blocker,
        conversation_id=event.conversation_id,
        run_id=admission.run_id,
        notice_id=event.id,
        input_id=claim.input_id,
    )
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=MagicMock(),
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
        redis_key_prefix="delivery-appended-checkpoint-race",
    )
    reconciliation = asyncio.create_task(
        coordinator.reconcile_bound_once(now=NOW + timedelta(minutes=1))
    )
    try:
        await asyncio.sleep(0.1)
        assert not reconciliation.done()
        await blocker.commit()
        assert await asyncio.wait_for(reconciliation, timeout=2) == [event.id]

        await db_session.refresh(event)
        await db_session.refresh(admission)
        assert event.state == "delivered"
        assert event.checkpoint_input_id == claim.input_id
        assert admission.run_finished_at == NOW + timedelta(minutes=1)
        assert admission.run_terminal_status == "failed"
        assert (
            await db_session.scalar(
                select(SteeringMessage).where(SteeringMessage.id == steering_id)
            )
            is None
        )
    finally:
        if blocker.in_transaction():
            await blocker.rollback()
        await blocker.close()
        if not reconciliation.done():
            reconciliation.cancel()
            await asyncio.gather(reconciliation, return_exceptions=True)
        await run_fixtures.cleanup_run_rows(db_session, event.conversation_id)


async def test_existing_checkpoint_proof_does_not_finish_live_run(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
) -> None:
    from cubeplex.db.engine import async_session_maker

    event = await _event(db_session, reservation_context, readiness="ready")
    admission = await db_session.get(
        ConversationExecutionAdmission, reservation_context.admission_id
    )
    assert admission is not None and admission.run_id is not None
    admission.run_start_token = "live-attempt"
    admission.run_started_at = NOW
    service = BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    )
    claims = await service.claim_ready(
        owner_token="delivery-worker",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    claim = next(item for item in claims if item.notice.notice_id == event.id)
    steering_id = await service.enqueue_for_active_run(
        notice_id=event.id,
        owner_token="delivery-worker",
        run_id=admission.run_id,
    )
    assert steering_id is not None
    await db_session.commit()

    async with session_factory() as checkpoint_session, checkpoint_session.begin():
        await _stage_checkpoint_under_advisory_lock(
            checkpoint_session,
            conversation_id=event.conversation_id,
            run_id=admission.run_id,
            notice_id=event.id,
            input_id=claim.input_id,
        )
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await create_run(
        redis,
        prefix="delivery-live-proof",
        run_id=admission.run_id,
        conversation_id=event.conversation_id,
        status="running",
        started_at=NOW.isoformat(),
        ttl_seconds=300,
        claim_token="live-attempt",
    )
    coordinator = BackgroundTaskDeliveryCoordinator(
        async_session_maker,
        run_manager=MagicMock(),
        redis=redis,
        redis_key_prefix="delivery-live-proof",
    )
    try:
        assert await coordinator.reconcile_bound_once(now=NOW + timedelta(seconds=1)) == [event.id]

        await db_session.refresh(event)
        await db_session.refresh(admission)
        assert event.state == "delivered"
        assert event.checkpoint_input_id == claim.input_id
        assert admission.run_finished_at is None
        assert admission.run_terminal_status is None
        assert (
            await db_session.scalar(
                select(SteeringMessage).where(SteeringMessage.id == steering_id)
            )
            is None
        )
    finally:
        await run_fixtures.cleanup_run_rows(db_session, event.conversation_id)


async def test_retry_locks_existing_admission_before_notice(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    reservation_context: ReservationContext,
    monkeypatch,
) -> None:
    event = await _event(db_session, reservation_context, readiness="ready")
    task = await db_session.get(BackgroundTask, event.task_id)
    assert task is not None
    [claim] = await BackgroundTaskDeliveryService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).claim_ready(
        owner_token="retry-worker",
        now=NOW,
        owner_until=NOW + timedelta(seconds=30),
    )
    admitted = await ConversationExecutionService(
        db_session, org_id=event.org_id, workspace_id=event.workspace_id
    ).admit_background_notice(
        conversation_id=event.conversation_id,
        actor_user_id=task.started_by_user_id,
        notice_id=event.id,
        owner_token="retry-worker",
        execution_generation=event.execution_generation,
        intent=UserMessageIntent(content="Background task result"),
        snapshot=snapshot(),
        now=NOW,
    )
    await db_session.commit()

    from cubeplex.repositories.background_task import (
        ConversationExecutionAdmissionRepository,
    )

    original = ConversationExecutionAdmissionRepository.get_source_locked
    admission_locked = asyncio.Event()

    async def observe_admission_lock(self, *, source_kind: str, source_id: str):  # noqa: ANN001
        row = await original(self, source_kind=source_kind, source_id=source_id)
        admission_locked.set()
        return row

    monkeypatch.setattr(
        ConversationExecutionAdmissionRepository,
        "get_source_locked",
        observe_admission_lock,
    )
    blocker = session_factory()
    await blocker.begin()
    await blocker.scalar(
        select(BackgroundTaskEvent).where(BackgroundTaskEvent.id == event.id).with_for_update()
    )

    async def retry():  # noqa: ANN202
        async with session_factory() as session, session.begin():
            return await ConversationExecutionService(
                session, org_id=event.org_id, workspace_id=event.workspace_id
            ).admit_background_notice(
                conversation_id=event.conversation_id,
                actor_user_id=task.started_by_user_id,
                notice_id=event.id,
                owner_token="retry-worker",
                execution_generation=event.execution_generation,
                intent=UserMessageIntent(content="Background task result"),
                snapshot=snapshot(),
                now=NOW + timedelta(seconds=1),
            )

    retry_task = asyncio.create_task(retry())
    reached_lock = False
    try:
        await asyncio.wait_for(admission_locked.wait(), timeout=2)
        reached_lock = True
    except TimeoutError:
        pass
    finally:
        await blocker.rollback()
        await blocker.close()
    if not reached_lock:
        retry_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await retry_task
    assert reached_lock, "retry blocked on the notice before locking its admission"
    retried = await retry_task
    assert retried.admission.id == admitted.admission.id
    assert claim.input_id == event.id
