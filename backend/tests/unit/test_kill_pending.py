"""Unit tests: _kill_record uses kill_pending for retry on failure."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.sandbox.manager import SandboxManager


def _make_manager() -> SandboxManager:
    factory = MagicMock()
    encryption = MagicMock()
    with patch("cubebox.sandbox.manager.config") as mock_config:
        mock_config.get.side_effect = lambda key, default=None: {
            "sandbox.domain": "localhost:8090",
            "sandbox.image": "ubuntu:22.04",
            "sandbox.api_key": None,
            "sandbox.request_timeout": 60,
            "sandbox.create_timeout": 300,
            "sandbox.ttl": 600,
            "sandbox.touch_interval": 60,
            "sandbox.ready_timeout": 60,
            "sandbox.use_server_proxy": False,
            "sandbox.secure_access": True,
            "sandbox.workdir": "/workspace",
            "sandbox.resource.cpu": "100m",
            "sandbox.resource.memory": "100Mi",
            "sandbox.volume.enabled": False,
            "sandbox.volume.mount_path": "/workspace",
            "sandbox.volume.pvc_prefix": "cubebox-user",
            "sandbox.egress_exchange_host": "",
            "sandbox.pause_on_idle": True,
            "sandbox.idle_ttl_seconds": 1800,
            "sandbox.paused_ttl_seconds": 1440,
            "sandbox.resume_timeout": 30,
            "sandbox.lease_seconds": 300,
            "sandbox.pause_attempt_grace_seconds": 5400,
            "sandbox.reserve_wait_timeout": 30.0,
            "sandbox.reserve_poll_interval": 0.5,
        }.get(key, default)
        mgr = SandboxManager(factory, encryption)
    return mgr


def _make_record(
    *,
    record_id: str = "rec-1",
    sandbox_id: str = "sbx-1",
    org_id: str = "org-1",
    workspace_id: str = "ws-1",
    status: str = "running",
) -> MagicMock:
    record = MagicMock()
    record.id = record_id
    record.sandbox_id = sandbox_id
    record.org_id = org_id
    record.workspace_id = workspace_id
    record.status = status
    return record


@pytest.mark.asyncio
async def test_kill_success_marks_terminated() -> None:
    """When raw.kill() succeeds, the row should end up as terminated."""
    mgr = _make_manager()
    session = MagicMock()
    repo = AsyncMock()
    record = _make_record()

    raw = AsyncMock()
    raw.kill = AsyncMock()
    raw.close = AsyncMock()

    conn_config = mgr._build_connection_config()

    with patch("cubebox.sandbox.manager.opensandbox.Sandbox.connect", return_value=raw):
        await mgr._kill_record(session, repo, record, conn_config)

    repo.mark_terminated.assert_called_once_with(record.id)
    repo.mark_kill_pending.assert_not_called()


@pytest.mark.asyncio
async def test_kill_failure_marks_kill_pending() -> None:
    """When raw.kill() raises, the row should be marked kill_pending (not terminated)."""
    mgr = _make_manager()
    session = MagicMock()
    repo = AsyncMock()
    record = _make_record()

    raw = AsyncMock()
    raw.kill = AsyncMock(side_effect=Exception("connection refused"))
    raw.close = AsyncMock()

    conn_config = mgr._build_connection_config()

    with patch("cubebox.sandbox.manager.opensandbox.Sandbox.connect", return_value=raw):
        await mgr._kill_record(session, repo, record, conn_config)

    repo.mark_kill_pending.assert_called_once_with(record.id)
    repo.mark_terminated.assert_not_called()


@pytest.mark.asyncio
async def test_kill_connect_failure_marks_kill_pending() -> None:
    """When even connect fails, mark kill_pending."""
    mgr = _make_manager()
    session = MagicMock()
    repo = AsyncMock()
    record = _make_record()

    conn_config = mgr._build_connection_config()

    with patch(
        "cubebox.sandbox.manager.opensandbox.Sandbox.connect",
        side_effect=Exception("DNS resolution failed"),
    ):
        await mgr._kill_record(session, repo, record, conn_config)

    repo.mark_kill_pending.assert_called_once_with(record.id)
    repo.mark_terminated.assert_not_called()
