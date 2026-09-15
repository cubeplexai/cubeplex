from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis
import pytest

from cubeplex.streams.steering_delivery import (
    DurableSteeringCoordinator,
    SteeringRunScope,
)


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
