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
def manager(mock_encryption_backend: Any) -> SandboxManager:
    factory, _ = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    # Disable egress side effects in the manager paths; tests focus on
    # pause/resume orchestration, not egress refs.
    mgr._exchange_host = ""
    return mgr


# -------------------------------------------------------------------------
# (a) Successful pause path
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_idle_pauses_on_successful_claim(mock_encryption_backend: Any) -> None:
    factory, _session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""
    mgr._pause_on_idle = True  # exercise the pause path explicitly

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
    # Per internals-note G8, pause() does NOT tear down the SDK transport;
    # pause_idle must close the raw handle to avoid leaking httpx clients
    # across the long-running cleanup loop.
    raw_sandbox.close.assert_awaited_once()


# -------------------------------------------------------------------------
# (b) claim_pausing False -> no provider.pause()
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_idle_skips_when_claim_pausing_false(mock_encryption_backend: Any) -> None:
    factory, _session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""
    mgr._pause_on_idle = True  # exercise the pause path explicitly

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
async def test_pause_idle_pause_raises_reverts_and_kills(mock_encryption_backend: Any) -> None:
    factory, _session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""
    mgr._pause_on_idle = True  # exercise the pause path explicitly

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
    scoped_repo.mark_terminated.assert_awaited_once_with(record.id, clear_sandbox_id=True)


# -------------------------------------------------------------------------
# (e) supports_pause()==False -> kill path
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_idle_no_capability_kills_record(mock_encryption_backend: Any) -> None:
    factory, _session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""
    mgr._pause_on_idle = True  # exercise the pause path explicitly

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
    scoped_repo.mark_terminated.assert_awaited_once_with(record.id, clear_sandbox_id=True)


# -------------------------------------------------------------------------
# (d) resume-on-reuse
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_record_marks_running_and_stamps_last_resumed_at(
    mock_encryption_backend: Any,
) -> None:
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
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
async def test_resume_record_recovers_when_reconciler_reverts_to_paused(
    mock_encryption_backend: Any,
) -> None:
    """Race (a): the reconciler observes provider ``Paused`` mid-resume and
    reverts the row ``resuming -> paused``. The first ``mark_running`` returns
    False; the re-fetch probe sees status ``paused``; ``_resume_record``
    re-claims ``paused -> resuming`` and the second ``mark_running`` wins.
    """
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    repo = MagicMock()
    repo.mark_resuming = AsyncMock(side_effect=[True, True])
    repo.mark_running = AsyncMock(side_effect=[False, True])
    repo.mark_failed = AsyncMock()
    repo.update_activity = AsyncMock()

    # Probe session re-fetches the row on a fresh session and observes the
    # reconciler-reverted ``paused`` status.
    paused_view = _make_record()
    paused_view.status = "paused"
    probe_repo = MagicMock()
    probe_repo.get = AsyncMock(return_value=paused_view)

    backend = MagicMock(name="OpenSandbox-backend")

    with (
        patch(
            "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(return_value=backend),
        ),
        patch("cubebox.sandbox.manager.UserSandboxRepository", return_value=probe_repo),
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

    assert result is backend
    assert repo.mark_resuming.await_count == 2  # Entry + recovery.
    assert repo.mark_running.await_count == 2  # First fails, second wins.
    repo.update_activity.assert_awaited_once_with(record.id)
    repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
async def test_resume_record_accepts_reconciler_won_running(mock_encryption_backend: Any) -> None:
    """Race (b): the reconciler observes provider ``Running`` and commits
    ``resuming -> running`` itself before this caller's ``mark_running`` lands.
    The first ``mark_running`` returns False (status is already ``running``,
    not in the prior-state set); the re-fetch probe sees ``running``;
    ``_resume_record`` accepts it instead of bouncing through ``paused`` (which
    would fail because the row is ``running``, not ``paused``).
    """
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    repo = MagicMock()
    repo.mark_resuming = AsyncMock(return_value=True)  # Only called at entry.
    repo.mark_running = AsyncMock(return_value=False)  # Reconciler already won.
    repo.mark_failed = AsyncMock()
    repo.update_activity = AsyncMock()

    running_view = _make_record()
    running_view.status = "running"
    probe_repo = MagicMock()
    probe_repo.get = AsyncMock(return_value=running_view)

    backend = MagicMock(name="OpenSandbox-backend")

    with (
        patch(
            "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(return_value=backend),
        ),
        patch("cubebox.sandbox.manager.UserSandboxRepository", return_value=probe_repo),
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

    assert result is backend
    repo.mark_resuming.assert_awaited_once_with(record.id)  # NO recovery bounce.
    repo.mark_running.assert_awaited_once()
    repo.update_activity.assert_awaited_once_with(record.id)
    repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
async def test_resume_record_returns_none_when_row_terminal(mock_encryption_backend: Any) -> None:
    """If the probe sees the row already in a terminal state (failed /
    terminated / row deleted), ``_resume_record`` returns None so
    ``get_or_create`` falls through to create-new instead of returning a
    backend whose DB row disagrees with the provider.
    """
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    repo = MagicMock()
    repo.mark_resuming = AsyncMock(return_value=True)
    repo.mark_running = AsyncMock(return_value=False)
    repo.update_activity = AsyncMock()
    repo.mark_failed = AsyncMock()

    failed_view = _make_record()
    failed_view.status = "failed"
    probe_repo = MagicMock()
    probe_repo.get = AsyncMock(return_value=failed_view)

    backend = MagicMock(name="OpenSandbox-backend")

    with (
        patch(
            "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(return_value=backend),
        ),
        patch("cubebox.sandbox.manager.UserSandboxRepository", return_value=probe_repo),
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
async def test_resume_record_recovery_bounce_tolerates_flapping_reconciler(
    mock_encryption_backend: Any,
) -> None:
    """If the reconciler reverts our row to ``paused`` two ticks in a row
    while we're trying to land ``running``, the bounded retry loop keeps
    re-claiming until either we win or we hit the attempt ceiling. The
    previous single-shot bounce would have given up after one revert
    (codex P2 round 14).
    """
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    paused_view = _make_record()
    paused_view.status = "paused"
    probe_repo = MagicMock()
    probe_repo.get = AsyncMock(return_value=paused_view)

    repo = MagicMock()
    # mark_resuming: paused -> resuming (entry), then 2x recovery bounce.
    repo.mark_resuming = AsyncMock(side_effect=[True, True, True])
    # mark_running: first two fail (reconciler revert), third wins.
    repo.mark_running = AsyncMock(side_effect=[False, False, True])
    repo.update_activity = AsyncMock()
    repo.mark_failed = AsyncMock()

    backend = MagicMock(name="OpenSandbox-backend")

    with (
        patch(
            "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(return_value=backend),
        ),
        patch("cubebox.sandbox.manager.UserSandboxRepository", return_value=probe_repo),
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

    assert result is backend  # Eventually lands.
    assert repo.mark_resuming.await_count == 3  # 1 entry + 2 recovery
    assert repo.mark_running.await_count == 3
    repo.update_activity.assert_awaited_once_with(record.id)
    repo.mark_failed.assert_not_called()


@pytest.mark.asyncio
async def test_resume_record_recovery_gives_up_at_ceiling(mock_encryption_backend: Any) -> None:
    """If the reconciler reverts past ``MAX_RUN_ATTEMPTS`` (3) in a row, the
    bounded loop surfaces the failure so the caller can create-new rather
    than livelocking. (codex P2 round 14 bounded-recovery requirement)
    """
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    paused_view = _make_record()
    paused_view.status = "paused"
    probe_repo = MagicMock()
    probe_repo.get = AsyncMock(return_value=paused_view)

    repo = MagicMock()
    repo.mark_resuming = AsyncMock(return_value=True)
    repo.mark_running = AsyncMock(return_value=False)  # reverted forever
    repo.update_activity = AsyncMock()
    repo.mark_failed = AsyncMock()

    backend = MagicMock(name="OpenSandbox-backend")

    with (
        patch(
            "cubebox.sandbox.manager.OpenSandbox.connect_or_resume",
            new=AsyncMock(return_value=backend),
        ),
        patch("cubebox.sandbox.manager.UserSandboxRepository", return_value=probe_repo),
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

    assert result is None  # Gave up after the ceiling, caller will create new.
    # 1 initial + 2 recovery before ceiling -> 3 total mark_running calls.
    assert repo.mark_running.await_count == 3
    repo.update_activity.assert_not_called()


@pytest.mark.asyncio
async def test_resume_record_exception_leaves_row_resuming_for_reconciler(
    mock_encryption_backend: Any,
) -> None:
    """``connect_or_resume`` raises. Client-side exceptions are ambiguous
    (the provider may still be transitioning to ``Running`` after a
    client timeout), so ``_resume_record`` must NOT terminalize the row
    to ``failed`` here — that would remove it from
    ``list_transient_for_reconcile_system`` and the reconciler could
    never observe a late ``Running``. Leave the row at ``resuming`` and
    return None; the reconciler settles state from the provider.
    (codex P1 round 13)
    """
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""

    record = _make_record()
    record.status = "paused"

    repo = MagicMock()
    repo.mark_resuming = AsyncMock(return_value=True)
    repo.mark_running = AsyncMock(return_value=True)
    repo.mark_failed_from_resuming = AsyncMock(return_value=True)
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
    # Crucially: no terminal write happens here. The reconciler will
    # settle state from the provider.
    repo.mark_failed_from_resuming.assert_not_called()
    repo.mark_failed.assert_not_called()
    repo.mark_running.assert_not_called()
    repo.update_activity.assert_not_called()


# -------------------------------------------------------------------------
# _kill_record direct test (shared helper)
# -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kill_record_marks_kill_pending_on_failure(
    mock_encryption_backend: Any,
) -> None:
    factory, session = _make_session_factory()
    mgr = SandboxManager(factory, mock_encryption_backend)
    mgr._exchange_host = ""

    record = _make_record()
    scoped = MagicMock()
    scoped.mark_terminated = AsyncMock()
    scoped.mark_kill_pending = AsyncMock()

    with patch("cubebox.sandbox.manager.opensandbox") as op:
        op.Sandbox.connect = AsyncMock(side_effect=RuntimeError("gone already"))
        await mgr._kill_record(session, scoped, record, conn_config=MagicMock())

    scoped.mark_kill_pending.assert_awaited_once_with(record.id)
    scoped.mark_terminated.assert_not_awaited()


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
