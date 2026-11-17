"""E2E regression tests for SandboxManager cleanup on real AsyncSession."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubeplex.credentials.encryption import FernetBackend
from cubeplex.models import EgressRef
from cubeplex.models.user_sandbox import UserSandbox
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.sandbox.base import SandboxCapabilities
from cubeplex.sandbox.manager import SandboxConflictError, SandboxManager
from cubeplex.sandbox.provider import ProviderInstanceState, SandboxProvider
from cubeplex.sandbox.registry import ProviderRegistry

pytestmark = pytest.mark.e2e


class _FakeCleanupProvider(SandboxProvider):
    """Minimal SandboxProvider stub recording terminate calls."""

    def __init__(self) -> None:
        self.terminated_ids: list[str] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(background=True, persistent_volume=True, egress_proxy=True)

    @property
    def default_workdir(self) -> str:
        return "/workspace"

    @property
    def default_image(self) -> str | None:
        return "img:1"

    async def create(self, spec: Any, *, on_created: Any = None) -> Any:
        raise NotImplementedError

    async def connect(
        self,
        sandbox_id: str,
        *,
        workdir: str | None = None,
        user_sandbox_id: str | None = None,
        run_identity: Any = None,
    ) -> Any:
        raise NotImplementedError

    async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
        del sandbox_id
        return ProviderInstanceState.TERMINATED

    async def pause(self, sandbox_id: str) -> None:
        del sandbox_id

    async def terminate(self, sandbox_id: str, *, delete_volumes: bool = False) -> None:
        del delete_volumes
        self.terminated_ids.append(sandbox_id)

    async def renew_ttl(self, sandbox_id: str, ttl_seconds: int) -> None:
        del sandbox_id, ttl_seconds

    def build_panel_upstream(self, *, sandbox_id: str, port: int, path: str, query: str) -> Any:
        raise NotImplementedError


@pytest.fixture
def enc_backend() -> FernetBackend:
    return FernetBackend([Fernet.generate_key().decode()])


def _make_manager(
    session_factory: async_sessionmaker[AsyncSession],
    enc_backend: FernetBackend,
    provider: _FakeCleanupProvider,
) -> SandboxManager:
    registry = ProviderRegistry(
        {
            "fake": provider,
            "opensandbox": provider,
            "cubepod": provider,
            "local": provider,
        },
        default_name="fake",
    )
    mgr = SandboxManager(session_factory, enc_backend, registry=registry)
    mgr._exchange_host = ""
    return mgr


async def _seed_row(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
    status: str,
    sandbox_id: str | None,
    cleanup_action: str | None = None,
    cleanup_requested_at: datetime | None = None,
    last_activity_at: datetime | None = None,
    ttl_seconds: int = 1800,
) -> str:
    async with session_factory() as session:
        repo = UserSandboxRepository(session, org_id=org_id, workspace_id=workspace_id)
        row = UserSandbox(
            user_id=user_id,
            scope_type="conversation",
            scope_id=f"conv-{secrets.token_hex(4)}",
            provider="fake",
            sandbox_id=sandbox_id,
            status=status,
            image="img:1",
            cleanup_action=cleanup_action,
            cleanup_requested_at=cleanup_requested_at,
            last_activity_at=last_activity_at or datetime.now(UTC),
            ttl_seconds=ttl_seconds,
        )
        added = await repo.add(row)
        return added.id


async def _fetch_row(session_factory: async_sessionmaker[AsyncSession], row_id: str) -> UserSandbox:
    async with session_factory() as session:
        return (
            await session.execute(
                sa.select(UserSandbox).where(UserSandbox.id == row_id)  # type: ignore[arg-type]
            )
        ).scalar_one()


async def test_delete_terminated_sandbox_without_instance_id_soft_deletes_immediately(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    """Deleting a terminated/cleared-sandbox_id row must not MissingGreenlet or stall."""
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    provider = _FakeCleanupProvider()
    mgr = _make_manager(session_factory, enc_backend, provider)
    row_id = await _seed_row(
        session_factory,
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        status="terminated",
        sandbox_id=None,
    )

    await mgr.delete_user_sandbox(row_id)

    row = await _fetch_row(session_factory, row_id)
    assert row.status == "terminated"
    assert row.deleted_at is not None
    assert row.sandbox_id is None
    assert row.cleanup_action is None


async def test_delete_provisioning_pending_waits_then_retry_finalizes(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    """Pending delete waits during create budget, then finalizes after budget elapses."""
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    provider = _FakeCleanupProvider()
    mgr = _make_manager(session_factory, enc_backend, provider)
    row_id = await _seed_row(
        session_factory,
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        status="provisioning",
        sandbox_id=f"pending-test-{secrets.token_hex(4)}",
    )

    await mgr.delete_user_sandbox(row_id)
    first = await _fetch_row(session_factory, row_id)
    assert first.status == "kill_pending"
    assert first.cleanup_action == "delete"
    assert first.deleted_at is None
    assert first.cleanup_requested_at is not None

    stale_ts = datetime.now(UTC) - timedelta(seconds=mgr._create_timeout + mgr._ready_timeout + 5)
    async with session_factory() as session:
        await session.execute(
            sa.update(UserSandbox)
            .where(UserSandbox.id == row_id)  # type: ignore[arg-type]
            .values(cleanup_requested_at=stale_ts)
        )
        await session.commit()

    await mgr.delete_user_sandbox(row_id)
    second = await _fetch_row(session_factory, row_id)
    assert second.status == "terminated"
    assert second.sandbox_id is None
    assert second.deleted_at is not None


async def test_restart_failed_noop_stale_settles_and_delete_conflicts(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    """Restart leaves failed stopped, settles stale pending restart, rejects delete."""
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    provider = _FakeCleanupProvider()
    mgr = _make_manager(session_factory, enc_backend, provider)
    failed_id = await _seed_row(
        session_factory,
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        status="failed",
        sandbox_id=None,
    )
    stale_ts = datetime.now(UTC) - timedelta(seconds=mgr._create_timeout + mgr._ready_timeout + 5)
    rst_pending_id = await _seed_row(
        session_factory,
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        status="kill_pending",
        sandbox_id=f"pending-rst-{secrets.token_hex(4)}",
        cleanup_action="restart",
        cleanup_requested_at=stale_ts,
    )
    del_pending_id = await _seed_row(
        session_factory,
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        status="kill_pending",
        sandbox_id=f"pending-del-{secrets.token_hex(4)}",
        cleanup_action="delete",
        cleanup_requested_at=datetime.now(UTC),
    )

    await mgr.restart_user_sandbox(failed_id)
    failed_row = await _fetch_row(session_factory, failed_id)
    assert failed_row.status == "failed"
    assert failed_row.deleted_at is None

    await mgr.restart_user_sandbox(rst_pending_id)
    settled = await _fetch_row(session_factory, rst_pending_id)
    assert settled.status == "terminated"
    assert settled.cleanup_action is None
    assert settled.sandbox_id is None
    assert settled.deleted_at is None

    with pytest.raises(SandboxConflictError, match="deletion is already pending"):
        await mgr.restart_user_sandbox(del_pending_id)


async def test_cleanup_expired_kill_pending_delete_soft_deletes_and_revokes_egress(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    """Reaping kill_pending+delete must soft-delete and revoke EgressRef even after clear."""
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    provider = _FakeCleanupProvider()
    mgr = _make_manager(session_factory, enc_backend, provider)
    mgr._exchange_host = "exchange.internal"
    sbx_inst_id = f"sbx-live-del-{secrets.token_hex(4)}"

    row_id = await _seed_row(
        session_factory,
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        status="kill_pending",
        sandbox_id=sbx_inst_id,
        cleanup_action="delete",
        cleanup_requested_at=datetime.now(UTC) - timedelta(seconds=60),
        last_activity_at=datetime.now(UTC) - timedelta(seconds=3600),
        ttl_seconds=60,
    )
    async with session_factory() as session:
        ref = EgressRef(
            ref_hash=secrets.token_hex(16),
            sandbox_id=sbx_inst_id,
            org_id=org_id,
            workspace_id=ws_a,
            user_id=user_id,
            status="valid",
            bindings=[{"env_name": "SECRET_A"}],
        )
        session.add(ref)
        await session.commit()
        ref_id = ref.id

    # Patch _kill_record wait on other rows doesn't hurt, just run cleanup_expired
    await mgr.cleanup_expired()

    assert sbx_inst_id in provider.terminated_ids
    row = await _fetch_row(session_factory, row_id)
    assert row.deleted_at is not None
    assert row.sandbox_id is None
    async with session_factory() as session:
        ref_row = (
            await session.execute(
                sa.select(EgressRef).where(EgressRef.id == ref_id)  # type: ignore[arg-type]
            )
        ).scalar_one()
        assert ref_row.status == "revoked"


async def test_get_or_create_revives_unhealthy_running_sandbox(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    """Dead running sandbox is terminated and re-provisioned in one get_or_create call."""
    from unittest.mock import AsyncMock, MagicMock

    from cubeplex.sandbox.base import SandboxError

    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    stale_id = f"stale-{secrets.token_hex(4)}"
    fresh_id = f"fresh-{secrets.token_hex(4)}"

    class _ReviveProvider(_FakeCleanupProvider):
        def __init__(self) -> None:
            super().__init__()
            self.connected_ids: list[str] = []

        async def create(self, spec: Any, *, on_created: Any = None) -> Any:
            if on_created is not None:
                await on_created(fresh_id)
            fake = MagicMock()
            fake.id = fresh_id
            fake.check_ready = AsyncMock()
            fake.is_healthy = AsyncMock(return_value=True)
            fake.ensure_workspace_owner = AsyncMock()
            fake.close = AsyncMock()
            return fake

        async def connect(
            self,
            sandbox_id: str,
            *,
            workdir: str | None = None,
            user_sandbox_id: str | None = None,
            run_identity: Any = None,
        ) -> Any:
            del workdir, user_sandbox_id, run_identity
            self.connected_ids.append(sandbox_id)
            if sandbox_id == stale_id:
                raise SandboxError("stale sandbox unreachable")
            fake = MagicMock()
            fake.id = sandbox_id
            fake.check_ready = AsyncMock()
            fake.is_healthy = AsyncMock(return_value=True)
            fake.ensure_workspace_owner = AsyncMock()
            fake.close = AsyncMock()
            return fake

    provider = _ReviveProvider()
    mgr = _make_manager(session_factory, enc_backend, provider)
    stale_row_id = await _seed_row(
        session_factory,
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        status="running",
        sandbox_id=stale_id,
    )
    seeded = await _fetch_row(session_factory, stale_row_id)

    attachment = await mgr.get_or_create(
        scope_type=seeded.scope_type,
        scope_id=seeded.scope_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )

    assert provider.connected_ids[0] == stale_id
    assert attachment.sandbox.id == fresh_id
    assert attachment.user_sandbox_id == stale_row_id

    revived_row = await _fetch_row(session_factory, stale_row_id)
    assert revived_row.status == "running"
    assert revived_row.sandbox_id == fresh_id


async def test_restart_pause_capable_sandbox_preserves_instance_and_resumes(
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
    enc_backend: FernetBackend,
) -> None:
    """Restart on a pause-capable provider pauses (retaining sandbox_id) and retries cleanly."""
    from unittest.mock import AsyncMock, MagicMock

    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    sbx_id = f"cpod-rst-{secrets.token_hex(4)}"

    class _PausingProvider(_FakeCleanupProvider):
        def __init__(self) -> None:
            super().__init__()
            self.paused_ids: list[str] = []
            self.resumed_ids: list[str] = []
            self.fail_next_pause = True

        @property
        def capabilities(self) -> SandboxCapabilities:
            return SandboxCapabilities(
                background=True,
                persistent_volume=True,
                pause=True,
                reset_rootfs=True,
                egress_proxy=True,
            )

        async def pause(self, sandbox_id: str) -> None:
            if self.fail_next_pause:
                self.fail_next_pause = False
                raise RuntimeError("transient pause error")
            self.paused_ids.append(sandbox_id)

        async def inspect_state(self, sandbox_id: str) -> ProviderInstanceState | None:
            if sandbox_id in self.paused_ids:
                return ProviderInstanceState.PAUSED
            return ProviderInstanceState.RUNNING

        async def resume(
            self,
            sandbox_id: str,
            *,
            workdir: str | None = None,
            user_sandbox_id: str | None = None,
            run_identity: Any = None,
        ) -> Any:
            del workdir, user_sandbox_id, run_identity
            self.resumed_ids.append(sandbox_id)
            fake = MagicMock()
            fake.id = sandbox_id
            fake.set_run_env = MagicMock()
            fake.renew = AsyncMock()
            fake.close = AsyncMock()
            return fake

    provider = _PausingProvider()
    mgr = _make_manager(session_factory, enc_backend, provider)
    mgr._exchange_host = "exchange.internal"

    row_id = await _seed_row(
        session_factory,
        org_id=org_id,
        workspace_id=ws_a,
        user_id=user_id,
        status="running",
        sandbox_id=sbx_id,
        last_activity_at=datetime.now(UTC) - timedelta(seconds=3600),
        ttl_seconds=60,
    )
    async with session_factory() as session:
        ref = EgressRef(
            ref_hash=secrets.token_hex(16),
            sandbox_id=sbx_id,
            org_id=org_id,
            workspace_id=ws_a,
            user_id=user_id,
            status="valid",
            bindings=[{"env_name": "SECRET_A"}],
        )
        session.add(ref)
        await session.commit()
        ref_id = ref.id

    # First attempt hits transient pause error -> stays kill_pending + restart with sandbox_id kept.
    await mgr.restart_user_sandbox(row_id)
    pending_row = await _fetch_row(session_factory, row_id)
    assert pending_row.status == "kill_pending"
    assert pending_row.cleanup_action == "restart"
    assert pending_row.sandbox_id == sbx_id
    assert provider.terminated_ids == []

    # Reaper retry pauses the instance (never terminates) and revokes old EgressRef.
    await mgr.cleanup_expired()
    paused_row = await _fetch_row(session_factory, row_id)
    assert paused_row.status == "paused"
    assert paused_row.cleanup_action is None
    assert paused_row.cleanup_requested_at is None
    assert paused_row.sandbox_id == sbx_id
    assert paused_row.paused_at is not None
    assert provider.paused_ids == [sbx_id]
    assert provider.terminated_ids == []
    async with session_factory() as session:
        ref_row = (
            await session.execute(sa.select(EgressRef).where(EgressRef.id == ref_id))  # type: ignore[arg-type]
        ).scalar_one()
        assert ref_row.status == "revoked"

    # Next get_or_create resumes the exact same provider sandbox_id (RootFS preserved).
    attachment = await mgr.get_or_create(
        scope_type=paused_row.scope_type,
        scope_id=paused_row.scope_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )
    assert attachment.sandbox.id == sbx_id
    assert provider.resumed_ids == [sbx_id]
    resumed_row = await _fetch_row(session_factory, row_id)
    assert resumed_row.status == "running"
    assert resumed_row.sandbox_id == sbx_id
