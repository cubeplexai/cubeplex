from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from cubeplex.streams.steering_delivery import (
    DurableSteeringCoordinator,
    SteeringRunScope,
    steering_message_to_cubeloop,
)


def test_wake_steer_attempt_keeps_canonical_notice_id() -> None:
    row = MagicMock(
        client_steer_id="scmw-123:run-attempt",
        source_kind="user_message",
        notice_id=None,
        sender_user_id="user-1",
        sender_display_name=None,
        content="wake",
    )

    message = steering_message_to_cubeloop(row)

    assert message.metadata == {
        "steer_id": "scmw-123:run-attempt",
        "sender_user_id": "user-1",
        "notice_id": "scmw-123",
    }


def test_background_notice_is_not_projected_as_user_steering() -> None:
    row = MagicMock(
        client_steer_id="btse-input-1",
        source_kind="background_task",
        notice_id="btse-123",
        execution_generation=4,
        sender_user_id="user-1",
        sender_display_name=None,
        content="background result",
    )

    message = steering_message_to_cubeloop(row)

    assert message.metadata == {
        "source": "background_task",
        "notice_id": "btse-123",
        "execution_generation": 4,
    }


async def test_checkpoint_ack_retries_transient_database_failure(monkeypatch) -> None:  # noqa: ANN001
    coordinator = DurableSteeringCoordinator(MagicMock())
    scope = SteeringRunScope(
        org_id="org-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
    )
    coordinator._scopes["run-1"] = scope
    write_ack = AsyncMock(side_effect=[RuntimeError("database unavailable"), None])
    monkeypatch.setattr(coordinator, "_acknowledge_injected_once", write_ack)
    sleep = AsyncMock()
    monkeypatch.setattr("cubeplex.streams.steering_delivery.asyncio.sleep", sleep)

    await coordinator.acknowledge_injected("run-1", "steer-1")

    assert write_ack.await_count == 2
    sleep.assert_awaited_once()


async def test_checkpoint_ack_propagates_after_retry_budget(monkeypatch) -> None:  # noqa: ANN001
    coordinator = DurableSteeringCoordinator(MagicMock())
    scope = SteeringRunScope(
        org_id="org-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
    )
    coordinator._scopes["run-1"] = scope
    write_ack = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr(coordinator, "_acknowledge_injected_once", write_ack)
    monkeypatch.setattr(
        "cubeplex.streams.steering_delivery.asyncio.sleep",
        AsyncMock(),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await coordinator.acknowledge_injected("run-1", "steer-1")

    assert write_ack.await_count == 3


async def test_registration_repairs_checkpointed_owned_claims_before_drain(
    monkeypatch,
) -> None:  # noqa: ANN001
    coordinator = DurableSteeringCoordinator(MagicMock())
    scope = SteeringRunScope(
        org_id="org-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
    )
    order: list[str] = []
    repair = AsyncMock(side_effect=lambda **_kwargs: order.append("repair"))
    drain = AsyncMock(side_effect=lambda _run_id: order.append("drain"))
    monkeypatch.setattr(coordinator, "_repair_checkpointed_owned_claims", repair)
    monkeypatch.setattr(coordinator, "drain", drain)

    await coordinator.register_and_drain(
        run_id="run-1",
        scope=scope,
        session=MagicMock(),
    )

    assert order == ["repair", "drain"]


async def test_drain_skips_session_after_registered_claim_is_replaced() -> None:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    session_maker = MagicMock()
    coordinator = DurableSteeringCoordinator(
        session_maker,
        redis=redis,
        redis_key_prefix="t",
    )
    scope = SteeringRunScope(
        org_id="org-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
    )
    registered_session = MagicMock()
    coordinator._sessions["run-1"] = registered_session
    coordinator._scopes["run-1"] = scope
    coordinator._claim_tokens["run-1"] = "old-token"
    await redis.hset("t:run_meta:v2:run-1", "claim_token", "replacement-token")

    await coordinator.drain("run-1")

    session_maker.assert_not_called()
    registered_session.submit_input.assert_not_called()


async def test_drain_waits_for_initial_background_checkpoint_gate() -> None:
    session_maker = MagicMock()
    coordinator = DurableSteeringCoordinator(session_maker)
    coordinator._sessions["run-1"] = MagicMock()
    coordinator._scopes["run-1"] = SteeringRunScope(
        org_id="org-1",
        workspace_id="workspace-1",
        conversation_id="conversation-1",
    )
    coordinator._claim_tokens["run-1"] = None
    coordinator._input_gates["run-1"] = False

    await coordinator.drain("run-1")

    session_maker.assert_not_called()
