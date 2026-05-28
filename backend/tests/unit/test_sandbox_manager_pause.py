"""Unit tests for SandboxManager pause/resume orchestration (Task 5).

Covers:
(a) pause_idle on successful claim_pausing -> provider.pause() + mark_paused
(b) pause_idle claim_pausing False -> provider.pause() never called
(c) provider.pause() raises -> mark_running revert + kill fallback
(d) resume-on-reuse: resuming -> mark_running + last_resumed_at; resume raises
    -> mark_failed + fall through (caller creates new)
(e) capability gap: supports_pause()==False -> pause_idle is a no-op for that
    row (kill path).
"""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cubebox.sandbox.manager import SandboxManager


def _make_session_factory() -> tuple[MagicMock, MagicMock]:
    """Build a session_factory whose `async with ...()` yields a MagicMock session."""
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
) -> MagicMock:
    record = MagicMock(name="UserSandbox")
    record.id = record_id
    record.sandbox_id = sandbox_id
    record.org_id = org_id
    record.workspace_id = workspace_id
    return record


@pytest.fixture
def manager() -> SandboxManager:
    factory, _ = _make_session_factory()
    mgr = SandboxManager(factory)
    # Disable egress side effects in the manager paths; tests focus on
    # pause/resume orchestration, not egress refs.
    mgr._exchange_host = ""
    return mgr


# -------------------------------------------------------------------------
# (a) Successful pause path
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_idle_pauses_on_successful_claim() -> None:
    factory, _session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()

    scoped_repo = MagicMock(name="scoped_repo")
    scoped_repo.claim_pausing = AsyncMock(return_value=True)
    scoped_repo.mark_paused = AsyncMock(return_value=True)
    scoped_repo.mark_running = AsyncMock(return_value=True)
    scoped_repo.mark_terminated = AsyncMock()

    raw_sandbox = MagicMock(name="raw_sandbox")
    raw_sandbox.kill = AsyncMock()
    raw_sandbox.close = AsyncMock()

    backend = MagicMock(name="OpenSandbox-backend")
    backend.supports_pause = MagicMock(return_value=True)
    backend.pause = AsyncMock()

    with (
        patch("cubebox.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch("cubebox.sandbox.manager.opensandbox") as op,
        patch("cubebox.sandbox.manager.OpenSandbox") as backend_cls,
    ):
        repo_cls.list_idle_to_pause_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped_repo
        op.Sandbox.connect = AsyncMock(return_value=raw_sandbox)
        backend_cls.return_value = backend

        await mgr.pause_idle()

    scoped_repo.claim_pausing.assert_awaited_once_with(
        record.id, idle_ttl_seconds=mgr._idle_ttl_seconds
    )
    backend.pause.assert_awaited_once()
    # Per internals-note G1, pause() is async (202) — the row stays
    # `pausing` until the reconciler reads provider state and advances
    # it. pause_idle must NOT call mark_paused synchronously.
    scoped_repo.mark_paused.assert_not_called()
    scoped_repo.mark_terminated.assert_not_called()
    scoped_repo.mark_running.assert_not_called()


# -------------------------------------------------------------------------
# (b) claim_pausing False -> no provider.pause()
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_idle_skips_when_claim_pausing_false() -> None:
    factory, _session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()

    scoped_repo = MagicMock(name="scoped_repo")
    scoped_repo.claim_pausing = AsyncMock(return_value=False)
    scoped_repo.mark_paused = AsyncMock()
    scoped_repo.mark_running = AsyncMock()
    scoped_repo.mark_terminated = AsyncMock()

    backend = MagicMock()
    backend.supports_pause = MagicMock(return_value=True)
    backend.pause = AsyncMock()

    with (
        patch("cubebox.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch("cubebox.sandbox.manager.opensandbox") as op,
        patch("cubebox.sandbox.manager.OpenSandbox") as backend_cls,
    ):
        repo_cls.list_idle_to_pause_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped_repo
        op.Sandbox.connect = AsyncMock()
        backend_cls.return_value = backend

        await mgr.pause_idle()

    scoped_repo.claim_pausing.assert_awaited_once()
    backend.pause.assert_not_called()
    op.Sandbox.connect.assert_not_called()
    scoped_repo.mark_paused.assert_not_called()
    scoped_repo.mark_terminated.assert_not_called()


# -------------------------------------------------------------------------
# (c) provider.pause() raises -> mark_running revert + kill fallback
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_idle_pause_raises_reverts_and_kills() -> None:
    factory, _session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()

    scoped_repo = MagicMock(name="scoped_repo")
    scoped_repo.claim_pausing = AsyncMock(return_value=True)
    scoped_repo.mark_paused = AsyncMock()
    scoped_repo.mark_running = AsyncMock(return_value=True)
    scoped_repo.mark_terminated = AsyncMock()

    raw_sandbox = MagicMock()
    raw_sandbox.kill = AsyncMock()
    raw_sandbox.close = AsyncMock()

    backend = MagicMock()
    backend.supports_pause = MagicMock(return_value=True)
    backend.pause = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch("cubebox.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch("cubebox.sandbox.manager.opensandbox") as op,
        patch("cubebox.sandbox.manager.OpenSandbox") as backend_cls,
    ):
        repo_cls.list_idle_to_pause_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped_repo
        op.Sandbox.connect = AsyncMock(return_value=raw_sandbox)
        backend_cls.return_value = backend

        await mgr.pause_idle()

    backend.pause.assert_awaited_once()
    scoped_repo.mark_paused.assert_not_called()
    # Don't flip the row back to `running` before killing — a concurrent
    # get_or_create could observe `running` and return a handle to a
    # sandbox we're about to terminate (codex P2 round 3).
    scoped_repo.mark_running.assert_not_called()
    # _kill_record killed the sandbox and marked terminated.
    raw_sandbox.kill.assert_awaited_once()
    scoped_repo.mark_terminated.assert_awaited_once_with(record.id)


# -------------------------------------------------------------------------
# (e) supports_pause()==False -> kill path
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_idle_no_capability_kills_record() -> None:
    factory, _session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()

    scoped_repo = MagicMock()
    scoped_repo.claim_pausing = AsyncMock(return_value=True)
    scoped_repo.mark_paused = AsyncMock()
    scoped_repo.mark_running = AsyncMock(return_value=True)
    scoped_repo.mark_terminated = AsyncMock()

    raw_sandbox = MagicMock()
    raw_sandbox.kill = AsyncMock()
    raw_sandbox.close = AsyncMock()

    backend = MagicMock()
    backend.supports_pause = MagicMock(return_value=False)
    backend.pause = AsyncMock()

    with (
        patch("cubebox.sandbox.manager.UserSandboxRepository") as repo_cls,
        patch("cubebox.sandbox.manager.opensandbox") as op,
        patch("cubebox.sandbox.manager.OpenSandbox") as backend_cls,
    ):
        repo_cls.list_idle_to_pause_system = AsyncMock(return_value=[record])
        repo_cls.return_value = scoped_repo
        op.Sandbox.connect = AsyncMock(return_value=raw_sandbox)
        backend_cls.return_value = backend

        await mgr.pause_idle()

    backend.pause.assert_not_called()
    # Same race rationale as the pause-failure path: never flip the
    # claimed row back to `running` before killing.
    scoped_repo.mark_running.assert_not_called()
    raw_sandbox.kill.assert_awaited_once()
    scoped_repo.mark_terminated.assert_awaited_once_with(record.id)


# -------------------------------------------------------------------------
# (d) resume-on-reuse
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_record_marks_running_and_stamps_last_resumed_at() -> None:
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    repo = MagicMock()
    repo.mark_resuming = AsyncMock(return_value=True)
    repo.mark_running = AsyncMock(return_value=True)
    repo.mark_failed = AsyncMock()
    repo.update_activity = AsyncMock()

    backend = MagicMock(name="OpenSandbox-backend")

    with patch(
        "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
        new=AsyncMock(return_value=backend),
    ) as connect_or_resume:
        result = await mgr._resume_record(
            session,
            repo,
            record,
            conn_config=MagicMock(),
            org_id="org-1",
            workspace_id="ws-1",
            user_id="user-1",
        )

    assert result is backend
    connect_or_resume.assert_awaited_once()
    repo.mark_resuming.assert_awaited_once_with(record.id)
    repo.mark_running.assert_awaited_once()
    _, kwargs = repo.mark_running.call_args
    assert "last_resumed_at" in kwargs and isinstance(kwargs["last_resumed_at"], datetime)
    repo.update_activity.assert_awaited_once_with(record.id)
    repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
async def test_resume_record_recovers_when_reconciler_reverts_mid_resume() -> None:
    """Race: the reconciler observes provider ``Paused`` while
    ``connect_or_resume`` is in flight and reverts the row ``resuming -> paused``.
    The first ``mark_running`` then returns False (prior-state guard rejects
    ``paused``). Provider IS running, so ``_resume_record`` re-claims
    ``paused -> resuming`` and tries ``mark_running`` again — landing the row
    in ``running`` rather than leaving it as ``paused`` (codex P1 round 5).
    """
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    repo = MagicMock()
    # First mark_resuming (paused -> resuming) succeeds at entry.
    # Second mark_resuming is the recovery call after the reconciler reverted.
    repo.mark_resuming = AsyncMock(side_effect=[True, True])
    # First mark_running fails (row is back at ``paused`` due to revert);
    # the recovery path bounces resuming again, then second mark_running wins.
    repo.mark_running = AsyncMock(side_effect=[False, True])
    repo.mark_failed = AsyncMock()
    repo.update_activity = AsyncMock()

    backend = MagicMock(name="OpenSandbox-backend")

    with patch(
        "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
        new=AsyncMock(return_value=backend),
    ):
        result = await mgr._resume_record(
            session,
            repo,
            record,
            conn_config=MagicMock(),
            org_id="org-1",
            workspace_id="ws-1",
            user_id="user-1",
        )

    assert result is backend  # Provider is running, caller gets the backend.
    assert repo.mark_resuming.await_count == 2  # Entry + recovery.
    assert repo.mark_running.await_count == 2  # First fails, second wins.
    repo.update_activity.assert_awaited_once_with(record.id)
    repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
async def test_resume_record_returns_none_when_recovery_also_fails() -> None:
    """If even the recovery bounce can't land ``running`` (row went to
    failed/terminated under us), ``_resume_record`` returns None so
    ``get_or_create`` falls through to create-new — never returns a backend
    whose DB row disagrees with the provider.
    """
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    repo = MagicMock()
    repo.mark_resuming = AsyncMock(side_effect=[True, False])
    repo.mark_running = AsyncMock(side_effect=[False, False])
    repo.update_activity = AsyncMock()
    repo.mark_failed = AsyncMock()

    backend = MagicMock(name="OpenSandbox-backend")

    with patch(
        "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
        new=AsyncMock(return_value=backend),
    ):
        result = await mgr._resume_record(
            session,
            repo,
            record,
            conn_config=MagicMock(),
            org_id="org-1",
            workspace_id="ws-1",
            user_id="user-1",
        )

    assert result is None
    repo.update_activity.assert_not_called()


@pytest.mark.asyncio
async def test_resume_record_failure_marks_failed_and_returns_none() -> None:
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    repo = MagicMock()
    repo.mark_resuming = AsyncMock(return_value=True)
    repo.mark_running = AsyncMock(return_value=True)
    repo.mark_failed = AsyncMock()
    repo.update_activity = AsyncMock()

    with patch(
        "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
        new=AsyncMock(side_effect=RuntimeError("resume failed")),
    ):
        result = await mgr._resume_record(
            session,
            repo,
            record,
            conn_config=MagicMock(),
            org_id="org-1",
            workspace_id="ws-1",
            user_id="user-1",
        )

    assert result is None
    repo.mark_resuming.assert_awaited_once_with(record.id)
    repo.mark_failed.assert_awaited_once_with(record.id)
    repo.mark_running.assert_not_called()


# -------------------------------------------------------------------------
# _kill_record direct test (shared helper)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_record_swallows_kill_failure_and_marks_terminated() -> None:
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory)
    mgr._exchange_host = ""

    record = _make_record()
    scoped = MagicMock()
    scoped.mark_terminated = AsyncMock()

    with patch("cubebox.sandbox.manager.opensandbox") as op:
        op.Sandbox.connect = AsyncMock(side_effect=RuntimeError("gone already"))
        await mgr._kill_record(session, scoped, record, conn_config=MagicMock())

    scoped.mark_terminated.assert_awaited_once_with(record.id)


# -------------------------------------------------------------------------
# Config knob defaults sanity
# -------------------------------------------------------------------------


def test_pause_resume_config_knob_defaults(manager: SandboxManager) -> None:
    """Verify the new config knobs are read at __init__ time."""
    assert isinstance(manager._pause_on_idle, bool)
    assert manager._idle_ttl_seconds > 0
    assert manager._paused_ttl_seconds > 0
    assert manager._resume_timeout > 0
    assert manager._lease_seconds > 0
