from unittest.mock import AsyncMock, MagicMock

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
