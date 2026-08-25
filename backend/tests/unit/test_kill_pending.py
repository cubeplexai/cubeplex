"""Unit tests: _kill_record uses kill_pending for retry on failure."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opensandbox.exceptions import SandboxApiException

from cubeplex.sandbox.manager import SandboxManager


def _make_manager() -> SandboxManager:
    factory = MagicMock()
    encryption = MagicMock()
    with patch("cubeplex.sandbox.manager.config") as mock_config:
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
        }.get(key, default)
        mgr = SandboxManager(factory, encryption)
    return mgr


def _make_record(
    *,
    record_id: str = "rec-1",
    sandbox_id: str | None = "sbx-1",
    org_id: str = "org-1",
    workspace_id: str = "ws-1",
    status: str = "running",
    last_activity_at: datetime | None = None,
) -> MagicMock:
    record = MagicMock()
    record.id = record_id
    record.sandbox_id = sandbox_id
    record.org_id = org_id
    record.workspace_id = workspace_id
    record.status = status
    record.last_activity_at = last_activity_at or datetime.now(UTC)
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

    with patch("cubeplex.sandbox.manager.opensandbox.Sandbox.connect", return_value=raw):
        await mgr._kill_record(session, repo, record, conn_config)

    repo.mark_terminated.assert_called_once_with(record.id, clear_sandbox_id=True)
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

    with patch("cubeplex.sandbox.manager.opensandbox.Sandbox.connect", return_value=raw):
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
        "cubeplex.sandbox.manager.opensandbox.Sandbox.connect",
        side_effect=Exception("DNS resolution failed"),
    ):
        await mgr._kill_record(session, repo, record, conn_config)

    repo.mark_kill_pending.assert_called_once_with(record.id)
    repo.mark_terminated.assert_not_called()


@pytest.mark.asyncio
async def test_kill_404_marks_terminated() -> None:
    """When kill() returns 404 (sandbox already gone), treat as successful kill."""
    mgr = _make_manager()
    session = MagicMock()
    repo = AsyncMock()
    record = _make_record()

    raw = AsyncMock()
    raw.kill = AsyncMock(
        side_effect=SandboxApiException("Not Found", status_code=404),
    )
    raw.close = AsyncMock()

    conn_config = mgr._build_connection_config()

    with patch("cubeplex.sandbox.manager.opensandbox.Sandbox.connect", return_value=raw):
        await mgr._kill_record(session, repo, record, conn_config)

    repo.mark_terminated.assert_called_once_with(record.id, clear_sandbox_id=True)
    repo.mark_kill_pending.assert_not_called()


class _AsyncSessionFactory:
    def __init__(self, session: MagicMock) -> None:
        self._session = session

    def __call__(self) -> "_AsyncSessionFactory":
        return self

    async def __aenter__(self) -> MagicMock:
        return self._session

    async def __aexit__(self, *args: object) -> None:
        return None


@pytest.mark.asyncio
async def test_cleanup_expired_skips_in_flight_provisioning() -> None:
    """ttl < create_timeout must not reap a live create/revive.

    list_expired_system can still return the row when the persisted ttl is
    shorter than Sandbox.create; last_activity_at is the start of this
    attempt, so the reaper must wait out create_timeout.
    """
    mgr = _make_manager()
    mgr._session_factory = _AsyncSessionFactory(MagicMock())  # type: ignore[assignment]
    record = _make_record(
        sandbox_id=None,
        status="provisioning",
        last_activity_at=datetime.now(UTC),
    )
    mark_terminated = AsyncMock()
    with (
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.list_expired_system",
            new=AsyncMock(return_value=[record]),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.get",
            new=AsyncMock(return_value=record),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.mark_terminated",
            new=mark_terminated,
        ),
        patch.object(mgr, "_kill_record", new=AsyncMock()) as kill,
    ):
        await mgr.cleanup_expired()
    mark_terminated.assert_not_called()
    kill.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_expired_reaps_stuck_provisioning_past_create_timeout() -> None:
    """A provisioning row older than create+ready budget is still an orphan."""
    mgr = _make_manager()
    mgr._session_factory = _AsyncSessionFactory(MagicMock())  # type: ignore[assignment]
    budget = mgr._create_timeout + mgr._ready_timeout
    record = _make_record(
        sandbox_id=None,
        status="provisioning",
        last_activity_at=datetime.now(UTC) - timedelta(seconds=budget + 10),
    )
    mark_terminated = AsyncMock()
    with (
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.list_expired_system",
            new=AsyncMock(return_value=[record]),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.get",
            new=AsyncMock(return_value=record),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.mark_terminated",
            new=mark_terminated,
        ),
        patch.object(mgr, "_kill_record", new=AsyncMock()) as kill,
    ):
        await mgr.cleanup_expired()
    mark_terminated.assert_awaited_once_with(record.id)
    kill.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_expired_uses_fresh_row_not_stale_snapshot() -> None:
    """A revive claimed after list_expired must not be reaped from the snapshot."""
    mgr = _make_manager()
    mgr._session_factory = _AsyncSessionFactory(MagicMock())  # type: ignore[assignment]
    budget = mgr._create_timeout + mgr._ready_timeout
    stale = _make_record(
        record_id="rec-1",
        sandbox_id=None,
        status="provisioning",
        last_activity_at=datetime.now(UTC) - timedelta(seconds=budget + 10),
    )
    fresh = _make_record(
        record_id="rec-1",
        sandbox_id=None,
        status="provisioning",
        last_activity_at=datetime.now(UTC),
    )
    mark_terminated = AsyncMock()
    with (
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.list_expired_system",
            new=AsyncMock(return_value=[stale]),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.get",
            new=AsyncMock(return_value=fresh),
        ),
        patch(
            "cubeplex.repositories.user_sandbox.UserSandboxRepository.mark_terminated",
            new=mark_terminated,
        ),
        patch.object(mgr, "_kill_record", new=AsyncMock()) as kill,
    ):
        await mgr.cleanup_expired()
    mark_terminated.assert_not_called()
    kill.assert_not_called()
