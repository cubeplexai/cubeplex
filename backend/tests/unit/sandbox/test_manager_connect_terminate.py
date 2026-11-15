"""Unit tests for SandboxManager._connect_existing unhealthy instance cleanup."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.sandbox.base import SandboxError
from cubeplex.sandbox.manager import SandboxManager


def _make_manager(provider: Any) -> SandboxManager:
    registry = MagicMock()
    registry.default.return_value = provider
    registry.get.return_value = provider
    manager = SandboxManager(MagicMock(), MagicMock(), registry=registry)
    manager._exchange_host = ""
    return manager


def _make_record(sandbox_id: str = "cpod-unhealthy-1") -> MagicMock:
    record = MagicMock()
    record.id = "rec-1"
    record.sandbox_id = sandbox_id
    record.org_id = "org-1"
    record.workspace_id = "ws-1"
    record.scope_type = "user"
    record.scope_id = "usr-1"
    record.provider = "cubepod"
    return record


async def test_connect_existing_terminates_unhealthy_backend() -> None:
    backend = MagicMock()
    backend.is_healthy = AsyncMock(return_value=False)
    backend.close = AsyncMock()

    provider = MagicMock()
    provider.resolve_workdir.return_value = "/workspace"
    provider.connect = AsyncMock(return_value=backend)
    provider.terminate = AsyncMock()

    repo = MagicMock()
    repo.mark_terminated = AsyncMock()

    manager = _make_manager(provider)
    with pytest.raises(SandboxError, match="unhealthy, will revive"):
        await manager._connect_existing(
            MagicMock(),
            repo,
            _make_record("cpod-bad-1"),
            provider=provider,
            user_id="usr-1",
        )

    backend.close.assert_awaited_once()
    provider.terminate.assert_awaited_once_with("cpod-bad-1")
    repo.mark_terminated.assert_awaited_once_with("rec-1", clear_sandbox_id=True)


async def test_connect_existing_terminates_when_connect_raises_and_suppresses_terminate_error() -> (
    None
):
    provider = MagicMock()
    provider.resolve_workdir.return_value = "/workspace"
    provider.connect = AsyncMock(side_effect=SandboxError("state is ''"))
    provider.terminate = AsyncMock(side_effect=RuntimeError("terminate network glitch"))

    repo = MagicMock()
    repo.mark_terminated = AsyncMock()

    manager = _make_manager(provider)
    with pytest.raises(SandboxError, match="unhealthy, will revive"):
        await manager._connect_existing(
            MagicMock(),
            repo,
            _make_record("cpod-bad-2"),
            provider=provider,
            user_id="usr-1",
        )

    provider.terminate.assert_awaited_once_with("cpod-bad-2")
    repo.mark_terminated.assert_awaited_once_with("rec-1", clear_sandbox_id=True)
