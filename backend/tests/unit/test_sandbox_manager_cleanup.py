"""Restart and delete fence tasks before touching the provider instance."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubeplex.models.background_task import TaskStopReason
from cubeplex.sandbox.base import SandboxCapabilities
from cubeplex.sandbox.manager import SandboxManager
from cubeplex.sandbox.provider import ProviderInstanceState
from cubeplex.sandbox.registry import ProviderRegistry


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
    row.scope_type = "user"
    row.scope_id = "user-1"
    row.user_id = "user-1"
    row.sandbox_id = "provider-instance"
    row.status = status
    row.paused_at = None
    row.cleanup_action = cleanup_action
    row.cleanup_requested_at = None
    row.deleted_at = None
    return row


@pytest.mark.asyncio
async def test_restart_pauses_pause_capable_provider_after_fencing_tasks(
    mock_encryption_backend: Any,
) -> None:
    factory, _ = _session_factory()
    provider = MagicMock(name="CubePodProvider")
    provider.name = "cubepod"
    provider.capabilities = SandboxCapabilities(pause=True, reset_rootfs=True)
    provider.pause = AsyncMock()
    provider.inspect_state = AsyncMock(return_value=ProviderInstanceState.PAUSED)
    provider.terminate = AsyncMock()
    registry = ProviderRegistry({"cubepod": provider}, default_name="cubepod")

    manager = SandboxManager(factory, mock_encryption_backend, registry=registry)
    manager._exchange_host = ""
    row = _record()
    row.provider = "cubepod"
    repo = MagicMock()
    repo.request_cleanup = AsyncMock(return_value=True)
    repo.mark_paused = AsyncMock(return_value=True)
    task_service = MagicMock()
    task_service.request_environment_stop = AsyncMock(return_value=["task-1"])
    task_service.finalize_environment_reset = AsyncMock(return_value=["task-1"])
    manager._kill_record = AsyncMock()  # type: ignore[method-assign]

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch("cubeplex.sandbox.manager.BackgroundTaskService", return_value=task_service),
        patch(
            "cubeplex.sandbox.command_coordinator.kill_sandbox_commands",
            new=AsyncMock(),
        ),
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
    task_service.finalize_environment_reset.assert_awaited_once()
    provider.pause.assert_awaited_once_with("provider-instance")
    provider.terminate.assert_not_awaited()
    manager._kill_record.assert_not_awaited()
    repo.mark_paused.assert_awaited_once()


@pytest.mark.asyncio
async def test_restart_falls_back_to_kill_on_non_pausing_provider(
    mock_encryption_backend: Any,
) -> None:
    factory, _ = _session_factory()
    provider = MagicMock(name="LocalProvider")
    provider.name = "local"
    provider.capabilities = SandboxCapabilities(pause=False)
    registry = ProviderRegistry({"local": provider}, default_name="local")

    manager = SandboxManager(factory, mock_encryption_backend, registry=registry)
    row = _record()
    row.provider = "local"
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


@pytest.mark.asyncio
async def test_restart_falls_back_to_kill_on_opensandbox_or_provisioning(
    mock_encryption_backend: Any,
) -> None:
    """Providers without persistent RootFS (opensandbox) or un-bootstrapped provisioning rows kill."""
    factory, _ = _session_factory()
    os_provider = MagicMock(name="OpenSandboxProvider")
    os_provider.name = "opensandbox"
    os_provider.capabilities = SandboxCapabilities(pause=True, reset_rootfs=False)
    cpod_provider = MagicMock(name="CubePodProvider")
    cpod_provider.name = "cubepod"
    cpod_provider.capabilities = SandboxCapabilities(pause=True, reset_rootfs=True)
    registry = ProviderRegistry(
        {"opensandbox": os_provider, "cubepod": cpod_provider},
        default_name="opensandbox",
    )

    manager = SandboxManager(factory, mock_encryption_backend, registry=registry)
    task_service = MagicMock()
    task_service.request_environment_stop = AsyncMock(return_value=[])
    manager._kill_record = AsyncMock()  # type: ignore[method-assign]

    for provider_name, status in (("opensandbox", "running"), ("cubepod", "provisioning")):
        row = _record(status=status)
        row.provider = provider_name
        repo = MagicMock()
        repo.request_cleanup = AsyncMock(return_value=True)
        with (
            patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls,
            patch("cubeplex.sandbox.manager.BackgroundTaskService", return_value=task_service),
        ):
            repo_cls.get_by_id_system = AsyncMock(return_value=row)
            repo_cls.return_value = repo
            await manager.restart_user_sandbox(row.id)

    assert manager._kill_record.await_count == 2


@pytest.mark.asyncio
async def test_pause_record_for_restart_error_and_state_branches(
    mock_encryption_backend: Any,
) -> None:
    """Exercise 404, 409/422 SandboxError, transient error, and transitional branches."""
    from cubeplex.sandbox.base import (
        SandboxConflictError,
        SandboxError,
        SandboxInstanceGoneError,
    )

    factory, session = _session_factory()
    session.rollback = AsyncMock()
    provider = MagicMock(name="CubePodProvider")
    provider.name = "cubepod"
    provider.capabilities = SandboxCapabilities(pause=True, reset_rootfs=True)
    registry = ProviderRegistry({"cubepod": provider}, default_name="cubepod")
    manager = SandboxManager(factory, mock_encryption_backend, registry=registry)
    manager._exchange_host = "egress.internal"
    manager._revoke_egress = AsyncMock()  # type: ignore[method-assign]

    task_service = MagicMock()
    task_service.finalize_environment_reset = AsyncMock(return_value=[])

    def _mk_repo() -> MagicMock:
        r = MagicMock()
        r.mark_terminated_from_restart = AsyncMock(return_value=True)
        r.mark_kill_pending = AsyncMock()
        r.mark_paused = AsyncMock(return_value=True)
        r.mark_pausing_from_restart = AsyncMock(return_value=True)
        return r

    row = _record(status="kill_pending", cleanup_action="restart")
    row.provider = "cubepod"
    prior_ts = datetime.now(UTC) - timedelta(minutes=5)

    with (
        patch("cubeplex.sandbox.manager.BackgroundTaskService", return_value=task_service),
        patch(
            "cubeplex.sandbox.command_coordinator.kill_sandbox_commands",
            new=AsyncMock(side_effect=RuntimeError("db blip")),
        ),
    ):
        # 1. SandboxInstanceGoneError -> mark_terminated_from_restart + rollback on cmd error
        repo = _mk_repo()
        provider.pause = AsyncMock(side_effect=SandboxInstanceGoneError("provider-instance"))
        await manager._pause_record_for_restart(session, repo, row)
        repo.mark_terminated_from_restart.assert_awaited_once_with(row.id)
        session.rollback.assert_awaited()

        # 2. SandboxConflictError + inspect_state raises -> mark_kill_pending
        repo = _mk_repo()
        provider.pause = AsyncMock(side_effect=SandboxConflictError("conflict"))
        provider.inspect_state = AsyncMock(side_effect=RuntimeError("probe fail"))
        await manager._pause_record_for_restart(session, repo, row)
        repo.mark_kill_pending.assert_awaited_once_with(row.id)

        # 3. SandboxError + inspect_state -> TERMINATED -> mark_terminated_from_restart
        repo = _mk_repo()
        provider.pause = AsyncMock(side_effect=SandboxError("422"))
        provider.inspect_state = AsyncMock(return_value=ProviderInstanceState.TERMINATED)
        await manager._pause_record_for_restart(session, repo, row)
        repo.mark_terminated_from_restart.assert_awaited_once_with(row.id)

        # 4. SandboxError + inspect_state -> RUNNING -> mark_kill_pending
        repo = _mk_repo()
        provider.pause = AsyncMock(side_effect=SandboxError("409"))
        provider.inspect_state = AsyncMock(return_value=ProviderInstanceState.RUNNING)
        await manager._pause_record_for_restart(session, repo, row)
        repo.mark_kill_pending.assert_awaited_once_with(row.id)

        # 5. SandboxError + inspect_state -> PAUSED -> mark_paused preserving prior_paused_at
        repo = _mk_repo()
        provider.pause = AsyncMock(side_effect=SandboxError("already paused"))
        provider.inspect_state = AsyncMock(return_value=ProviderInstanceState.PAUSED)
        await manager._pause_record_for_restart(
            session, repo, row, prior_status="paused", prior_paused_at=prior_ts
        )
        assert repo.mark_paused.await_args.kwargs["paused_at"] == prior_ts

        # 6. Generic exception -> mark_kill_pending
        repo = _mk_repo()
        provider.pause = AsyncMock(side_effect=RuntimeError("network down"))
        await manager._pause_record_for_restart(session, repo, row)
        repo.mark_kill_pending.assert_awaited_once_with(row.id)

        # 7. Pause ok + inspect_state raises -> TRANSITIONAL -> mark_pausing_from_restart
        repo = _mk_repo()
        provider.pause = AsyncMock()
        provider.inspect_state = AsyncMock(side_effect=RuntimeError("probe timeout"))
        await manager._pause_record_for_restart(session, repo, row)
        repo.mark_pausing_from_restart.assert_awaited_once_with(row.id)

        # 8. Pause ok + inspect_state -> None -> mark_terminated_from_restart
        repo = _mk_repo()
        provider.pause = AsyncMock()
        provider.inspect_state = AsyncMock(return_value=None)
        await manager._pause_record_for_restart(session, repo, row)
        repo.mark_terminated_from_restart.assert_awaited_once_with(row.id)


@pytest.mark.asyncio
async def test_hard_delete_helpers_and_manager_singleton(
    mock_encryption_backend: Any,
) -> None:
    """Unit coverage for delete_workspace_sandboxes, delete_user_sandboxes, and singleton getter."""
    from cubeplex.sandbox import manager as manager_mod

    factory, session = _session_factory()
    provider = MagicMock(name="CubePodProvider")
    provider.name = "cubepod"
    provider.capabilities = SandboxCapabilities(pause=True, reset_rootfs=True)
    provider.terminate = AsyncMock()
    provider.delete_volume = AsyncMock()
    registry = ProviderRegistry({"cubepod": provider}, default_name="cubepod")

    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    live_row = _record(status="running")
    live_row.provider = "cubepod"
    deleted_after_kill = _record(status="terminated")
    deleted_after_kill.deleted_at = datetime.now(UTC)

    term_row = _record(status="terminated")
    term_row.id = "sbx-term"
    term_row.sandbox_id = None
    term_row.provider = "cubepod"

    exec_result = MagicMock()
    exec_result.scalars.return_value.all.return_value = [live_row, term_row]
    session.execute = AsyncMock(return_value=exec_result)

    repo = MagicMock()
    repo.request_cleanup = AsyncMock(return_value=True)
    repo.mark_terminated = AsyncMock()
    repo.soft_delete = AsyncMock(return_value=True)

    task_service = MagicMock()
    task_service.request_environment_stop = AsyncMock(return_value=[])

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch("cubeplex.sandbox.manager.BackgroundTaskService", return_value=task_service),
        patch(
            "cubeplex.sandbox.command_coordinator.kill_sandbox_commands",
            new=AsyncMock(),
        ),
    ):
        repo_cls.get_by_id_system = AsyncMock(side_effect=[live_row, deleted_after_kill, term_row])
        repo_cls.return_value = repo
        pending_ws = await mgr.delete_workspace_sandboxes("ws-1", session=session)
        assert pending_ws is False

    provider.terminate.assert_awaited_once_with("provider-instance", delete_volumes=True)
    assert provider.delete_volume.await_count == 2

    # Empty candidate set returns False on both session=None paths
    empty_exec = MagicMock()
    empty_exec.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=empty_exec)
    assert await mgr.delete_workspace_sandboxes("ws-empty") is False
    assert await mgr.delete_user_sandboxes("usr-empty") is False

    prev = manager_mod._sandbox_manager
    try:
        manager_mod._sandbox_manager = None
        assert manager_mod.get_sandbox_manager_or_none() is None
        with pytest.raises(RuntimeError, match="not initialized"):
            manager_mod.get_sandbox_manager()
        inited = manager_mod.init_sandbox_manager(
            factory, mock_encryption_backend, registry=registry
        )
        assert manager_mod.get_sandbox_manager_or_none() is inited
        assert manager_mod.get_sandbox_manager() is inited
    finally:
        manager_mod._sandbox_manager = prev
