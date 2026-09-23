"""Restart and delete fence tasks before touching the provider instance."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubeplex.models.background_task import TaskStopReason
from cubeplex.sandbox.manager import SandboxManager


def _session_factory() -> tuple[MagicMock, MagicMock]:
    session = MagicMock(name="session")
    session.commit = AsyncMock()

    @asynccontextmanager
    async def _session() -> Any:
        yield session

    factory = MagicMock(name="session_factory", side_effect=lambda: _session())
    return factory, session


def _record(*, status: str = "running", cleanup_action: str | None = None) -> MagicMock:
    row = MagicMock(name="UserSandbox")
    row.id = "sbx-row"
    row.org_id = "org-1"
    row.workspace_id = "ws-1"
    row.sandbox_id = "provider-instance"
    row.status = status
    row.cleanup_action = cleanup_action
    row.cleanup_requested_at = None
    row.deleted_at = None
    return row


@pytest.mark.asyncio
async def test_restart_stops_original_instance_tasks_before_provider_cleanup(
    mock_encryption_backend: Any,
) -> None:
    factory, _ = _session_factory()
    manager = SandboxManager(factory, mock_encryption_backend)
    row = _record()
    repo = MagicMock()
    repo.request_cleanup = AsyncMock(return_value=True)
    task_service = MagicMock()
    task_service.request_environment_stop = AsyncMock(return_value=["task-1"])
    manager._kill_record = AsyncMock()  # type: ignore[method-assign]

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch("cubeplex.sandbox.manager.BackgroundTaskService", return_value=task_service),
    ):
        repo_cls.get_by_id_system = AsyncMock(return_value=row)
        repo_cls.return_value = repo
        await manager.restart_user_sandbox(row.id)

    repo.request_cleanup.assert_awaited_once()
    requested_at = task_service.request_environment_stop.await_args.kwargs["now"]
    assert requested_at.utcoffset() is not None
    task_service.request_environment_stop.assert_awaited_once_with(
        user_sandbox_id=row.id,
        sandbox_instance_id="provider-instance",
        reason=TaskStopReason.user_stop,
        now=requested_at,
    )
    manager._kill_record.assert_awaited_once()
    assert manager._kill_record.await_args.kwargs["delete_on_success"] is False


@pytest.mark.asyncio
async def test_delete_remains_visible_until_provider_cleanup_is_confirmed(
    mock_encryption_backend: Any,
) -> None:
    factory, _ = _session_factory()
    manager = SandboxManager(factory, mock_encryption_backend)
    row = _record()
    repo = MagicMock()
    repo.request_cleanup = AsyncMock(return_value=True)
    repo.soft_delete = AsyncMock()
    task_service = MagicMock()
    task_service.request_environment_stop = AsyncMock(return_value=[])
    manager._kill_record = AsyncMock()  # type: ignore[method-assign]

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch("cubeplex.sandbox.manager.BackgroundTaskService", return_value=task_service),
    ):
        repo_cls.get_by_id_system = AsyncMock(return_value=row)
        repo_cls.return_value = repo
        await manager.delete_user_sandbox(row.id)

    repo.soft_delete.assert_not_awaited()
    manager._kill_record.assert_awaited_once()
    assert manager._kill_record.await_args.kwargs["delete_on_success"] is True


@pytest.mark.asyncio
async def test_delete_during_provisioning_waits_for_late_provider_handle(
    mock_encryption_backend: Any,
) -> None:
    factory, _ = _session_factory()
    manager = SandboxManager(factory, mock_encryption_backend)
    row = _record(status="provisioning")
    row.sandbox_id = "pending-sbx-row"
    repo = MagicMock()
    repo.request_cleanup = AsyncMock(return_value=True)
    repo.soft_delete = AsyncMock()
    manager._kill_record = AsyncMock()  # type: ignore[method-assign]

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.get_by_id_system = AsyncMock(return_value=row)
        repo_cls.return_value = repo
        await manager.delete_user_sandbox(row.id)

    repo.soft_delete.assert_not_awaited()
    manager._kill_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_finalizes_stale_cleanup_that_never_received_a_provider_handle(
    mock_encryption_backend: Any,
) -> None:
    factory, _ = _session_factory()
    manager = SandboxManager(factory, mock_encryption_backend)
    row = _record(status="kill_pending", cleanup_action="delete")
    row.sandbox_id = "pending-sbx-row"
    row.cleanup_requested_at = datetime.now(UTC) - timedelta(
        seconds=manager._create_timeout + manager._ready_timeout + 1
    )
    repo = MagicMock()
    repo.request_cleanup = AsyncMock(return_value=True)
    repo.mark_terminated = AsyncMock()
    repo.soft_delete = AsyncMock()
    manager._kill_record = AsyncMock()  # type: ignore[method-assign]

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.get_by_id_system = AsyncMock(return_value=row)
        repo_cls.return_value = repo
        await manager.delete_user_sandbox(row.id)

    repo.mark_terminated.assert_awaited_once_with(row.id, clear_sandbox_id=True)
    repo.soft_delete.assert_awaited_once_with(row.id)
    manager._kill_record.assert_not_awaited()
