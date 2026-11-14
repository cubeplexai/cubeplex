"""Unit tests for SandboxManager.reconcile_transients (Task 5b / OQ-3).

Covers each branch the reconciler must handle when the provider is the source
of truth for a row stuck in ``pausing`` / ``resuming``:

- provider ``Paused``       -> mark_paused.
- provider ``Running``      -> mark_running (revert pause / complete resume).
- provider ``Failed``       -> mark_failed.
- provider ``Terminated``   -> _kill_record (mark_terminated + egress revoke).
- provider ``Pausing`` / ``Resuming`` / unknown -> no-op except
  ``touch_provider_check`` bump.
- ``get_info`` raises        -> no-op except ``touch_provider_check`` bump.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubeplex.sandbox.manager import SandboxManager
from cubeplex.sandbox.provider import ProviderInstanceState
from cubeplex.sandbox.registry import ProviderRegistry


def _make_registry(
    *,
    state: ProviderInstanceState | None = ProviderInstanceState.PAUSED,
    inspect_side_effect: Exception | None = None,
) -> tuple[ProviderRegistry, MagicMock]:
    provider = MagicMock(name="FakeProvider")
    provider.name = "opensandbox"
    provider.inspect_state = AsyncMock(return_value=state, side_effect=inspect_side_effect)
    provider.terminate = AsyncMock()
    return ProviderRegistry({"opensandbox": provider}, default_name="opensandbox"), provider


def _make_session_factory() -> tuple[MagicMock, MagicMock]:
    session = MagicMock(name="session")

    @asynccontextmanager
    async def _cm() -> Any:
        yield session

    factory = MagicMock(name="session_factory")
    factory.side_effect = lambda: _cm()
    return factory, session


def _make_record(
    *,
    record_id: str = "rec-1",
    sandbox_id: str = "sbx-1",
    org_id: str = "org-1",
    workspace_id: str = "ws-1",
    status: str = "pausing",
    last_activity_at: datetime | None = None,
) -> MagicMock:
    record = MagicMock(name="UserSandbox")
    record.id = record_id
    record.sandbox_id = sandbox_id
    record.org_id = org_id
    record.workspace_id = workspace_id
    record.provider = "opensandbox"
    record.status = status
    # Default: fresh activity so the reconciler's G11 stuck-pausing-grace check
    # doesn't fire. Tests that exercise the grace-elapsed branch pass an old
    # value explicitly.
    record.last_activity_at = last_activity_at or datetime.now(UTC)
    return record


def _scoped_repo() -> MagicMock:
    """A scoped UserSandboxRepository mock with the methods the reconciler uses."""
    repo = MagicMock(name="scoped_repo")
    repo.mark_paused = AsyncMock(return_value=True)
    repo.mark_running = AsyncMock(return_value=True)
    repo.mark_failed = AsyncMock()
    repo.mark_failed_from_transient = AsyncMock(return_value=True)
    repo.mark_terminated = AsyncMock()
    repo.touch_provider_check = AsyncMock()
    return repo


# ---------------------------------------------------------------------------
# Paused -> mark_paused
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_pausing_with_provider_paused_marks_paused(
    mock_encryption_backend: Any,
) -> None:
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(state=ProviderInstanceState.PAUSED)
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    record = _make_record(status="pausing")
    scoped = _scoped_repo()

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    scoped.mark_paused.assert_awaited_once()
    args, kwargs = scoped.mark_paused.call_args
    assert args[0] == record.id
    assert "paused_at" in kwargs
    scoped.mark_running.assert_not_called()
    scoped.mark_failed.assert_not_called()
    scoped.mark_terminated.assert_not_called()
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# Running with DB pausing -> mark_running revert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_pausing_with_provider_running_reverts_to_running(
    mock_encryption_backend: Any,
) -> None:
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(state=ProviderInstanceState.RUNNING)
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    record = _make_record(status="pausing")
    scoped = _scoped_repo()

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    scoped.mark_running.assert_awaited_once()
    args, kwargs = scoped.mark_running.call_args
    assert args[0] == record.id
    # DB says pausing -> no last_resumed_at stamp.
    assert kwargs.get("last_resumed_at") is None
    scoped.mark_paused.assert_not_called()
    scoped.mark_failed.assert_not_called()
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# G11 mitigation — pausing row stuck on a no-op-pause backend gets killed
# instead of reverting forever.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_pausing_stuck_past_grace_kills_instead_of_reverting(
    mock_encryption_backend: Any,
) -> None:
    """On backends where pause silently no-ops (provider stays ``Running``),
    repeatedly reverting ``pausing -> running`` leaks the idle sandbox
    forever. After the row has been idle past
    ``_pause_attempt_grace_seconds``, the reconciler must kill it.
    """
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(state=ProviderInstanceState.RUNNING)
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""
    mgr._pause_attempt_grace_seconds = 60

    # Row last touched well past the grace window.
    record = _make_record(
        status="pausing",
        last_activity_at=datetime.now(UTC) - timedelta(seconds=300),
    )
    scoped = _scoped_repo()

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch.object(mgr, "_kill_record", new=AsyncMock()) as kill_record,
    ):
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    kill_record.assert_awaited_once()
    # Crucially, NO mark_running revert — that would just leak the sandbox
    # again on the next pause_idle cycle.
    scoped.mark_running.assert_not_called()
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# Running with DB resuming -> mark_running with last_resumed_at
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_resuming_with_provider_running_stamps_last_resumed_at(
    mock_encryption_backend: Any,
) -> None:
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(state=ProviderInstanceState.RUNNING)
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    record = _make_record(status="resuming")
    scoped = _scoped_repo()

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    scoped.mark_running.assert_awaited_once()
    _, kwargs = scoped.mark_running.call_args
    assert kwargs.get("last_resumed_at") is not None
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# Failed -> mark_failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_provider_failed_marks_failed(mock_encryption_backend: Any) -> None:
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(state=ProviderInstanceState.FAILED)
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    record = _make_record(status="resuming")
    scoped = _scoped_repo()

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    # Guarded variant — only fires when the prior status is still transient,
    # so a concurrent ``_resume_record`` that just landed ``running`` is not
    # clobbered (codex P2 round 14 hardening).
    scoped.mark_failed_from_transient.assert_awaited_once_with(record.id)
    scoped.mark_failed.assert_not_called()
    scoped.mark_running.assert_not_called()
    scoped.mark_paused.assert_not_called()
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# Terminated -> _kill_record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider_state", [ProviderInstanceState.TERMINATED])
@pytest.mark.asyncio
async def test_reconcile_provider_terminal_kills_record(
    provider_state: ProviderInstanceState, mock_encryption_backend: Any
) -> None:
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(state=provider_state)
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    record = _make_record(status="pausing")
    scoped = _scoped_repo()

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch.object(mgr, "_kill_record", new=AsyncMock()) as kill_record,
    ):
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    kill_record.assert_awaited_once()
    scoped.mark_paused.assert_not_called()
    scoped.mark_running.assert_not_called()
    scoped.mark_failed.assert_not_called()
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# Pausing / Resuming / unknown -> TRANSITIONAL -> no-op + touch only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", [ProviderInstanceState.TRANSITIONAL])
@pytest.mark.asyncio
async def test_reconcile_transient_or_unknown_is_noop_except_touch(
    state: ProviderInstanceState, mock_encryption_backend: Any
) -> None:
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(state=state)
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    record = _make_record(status="pausing")
    scoped = _scoped_repo()

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    scoped.mark_paused.assert_not_called()
    scoped.mark_running.assert_not_called()
    scoped.mark_failed.assert_not_called()
    scoped.mark_terminated.assert_not_called()
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# inspect_state raises -> touch only, no transition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_get_info_failure_just_bumps_touch(mock_encryption_backend: Any) -> None:
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(inspect_side_effect=RuntimeError("nope"))
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    record = _make_record(status="resuming")
    scoped = _scoped_repo()

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    scoped.mark_paused.assert_not_called()
    scoped.mark_running.assert_not_called()
    scoped.mark_failed.assert_not_called()
    scoped.mark_terminated.assert_not_called()
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# 404 / None -> kill the row (codex P1 round 14)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_provider_404_kills_record(
    mock_encryption_backend: Any,
) -> None:
    """When the provider returns None (pod GC'd out-of-band), the reconciler
    must kill the row instead of leaving it transient forever."""
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry(state=None)
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    record = _make_record(status="resuming")
    scoped = _scoped_repo()

    with (
        patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch.object(mgr, "_kill_record", new=AsyncMock()) as kill_record,
    ):
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped

        await mgr.reconcile_transients(claim_timeout=60)

    kill_record.assert_awaited_once()
    scoped.mark_paused.assert_not_called()
    scoped.mark_running.assert_not_called()
    scoped.mark_failed_from_transient.assert_not_called()
    scoped.touch_provider_check.assert_awaited_once_with(record.id)


# ---------------------------------------------------------------------------
# claim_timeout forwarding (explicit call bypasses default)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_forwards_claim_timeout_to_repo_query(mock_encryption_backend: Any) -> None:
    factory, _session = _make_session_factory()
    registry, _provider = _make_registry()
    mgr = SandboxManager(factory, mock_encryption_backend, registry=registry)
    mgr._exchange_host = ""

    with patch("cubeplex.sandbox.manager.UserSandboxRepository") as repo_cls:
        repo_cls.list_transient_for_reconcile_system = AsyncMock(return_value=[])

        await mgr.reconcile_transients(claim_timeout=15)

    repo_cls.list_transient_for_reconcile_system.assert_awaited_once()
    _, kwargs = repo_cls.list_transient_for_reconcile_system.call_args
    assert kwargs.get("claim_timeout") == 15
