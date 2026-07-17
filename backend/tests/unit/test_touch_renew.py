"""Unit tests: touch() calls renew on the provider sandbox."""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubeplex.sandbox.manager import SandboxManager


def _make_manager(*, ttl: int = 600, touch_interval: int = 60) -> SandboxManager:
    factory = MagicMock(name="session_factory")
    encryption = MagicMock(name="encryption_backend")
    with patch("cubeplex.sandbox.manager.config") as mock_config:
        config_map: dict[str, Any] = {
            "sandbox.domain": "localhost:8090",
            "sandbox.image": "ubuntu:22.04",
            "sandbox.api_key": None,
            "sandbox.request_timeout": 60,
            "sandbox.create_timeout": 300,
            "sandbox.ttl": ttl,
            "sandbox.touch_interval": touch_interval,
            "sandbox.ready_timeout": 60,
            "sandbox.use_server_proxy": False,
            "sandbox.secure_access": True,
            "sandbox.workdir": "/workspace",
            "sandbox.resource.cpu": "100m",
            "sandbox.resource.memory": "100Mi",
            "sandbox.volume.enabled": False,
            "sandbox.volume.mount_path": "/workspace",
            "sandbox.volume.pvc_prefix": "cubeplex-user",
            "sandbox.egress_exchange_host": "",
            "sandbox.pause_on_idle": True,
            "sandbox.idle_ttl_seconds": 1800,
            "sandbox.paused_ttl_seconds": 1440,
            "sandbox.resume_timeout": 30,
            "sandbox.lease_seconds": 300,
            "sandbox.pause_attempt_grace_seconds": 5400,
            "sandbox.reserve_wait_timeout": 30.0,
            "sandbox.reserve_poll_interval": 0.5,
        }
        mock_config.get.side_effect = lambda key, default=None: config_map.get(key, default)
        mgr = SandboxManager(factory, encryption)
    return mgr


@pytest.mark.asyncio
async def test_touch_calls_renew_on_provider() -> None:
    """When touch fires (not throttled), it should call sandbox.renew."""
    mgr = _make_manager(ttl=600, touch_interval=0)

    mock_session = MagicMock()
    mock_repo = AsyncMock()
    mock_raw = AsyncMock()
    mock_raw.renew = AsyncMock()
    mock_raw.close = AsyncMock()

    @asynccontextmanager
    async def _session_cm():
        yield mock_session

    mgr._session_factory = MagicMock(side_effect=lambda: _session_cm())

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository", return_value=mock_repo),
        patch("cubeplex.sandbox.manager.opensandbox.Sandbox.connect", return_value=mock_raw),
    ):
        await mgr.touch(
            "sbx-123",
            org_id="org-1",
            workspace_id="ws-1",
        )

    mock_raw.renew.assert_called_once()
    call_args = mock_raw.renew.call_args[0]
    assert call_args[0].total_seconds() == 600


@pytest.mark.asyncio
async def test_touch_renew_failure_is_nonfatal() -> None:
    """If renew() raises, touch should log a warning and continue (not crash)."""
    mgr = _make_manager(ttl=600, touch_interval=0)

    mock_session = MagicMock()
    mock_repo = AsyncMock()
    mock_raw = AsyncMock()
    mock_raw.renew = AsyncMock(side_effect=Exception("server unavailable"))
    mock_raw.close = AsyncMock()

    @asynccontextmanager
    async def _session_cm():
        yield mock_session

    mgr._session_factory = MagicMock(side_effect=lambda: _session_cm())

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository", return_value=mock_repo),
        patch("cubeplex.sandbox.manager.opensandbox.Sandbox.connect", return_value=mock_raw),
    ):
        # Should not raise
        await mgr.touch(
            "sbx-123",
            org_id="org-1",
            workspace_id="ws-1",
        )

    mock_repo.update_activity_by_sandbox_id.assert_called_once()
