"""E2E: sync event recording — cold, failed, noop.

If LazySandbox stops calling event_service.record OR if the success path
stops bumping the UserSandbox snapshot, this fails.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.models import UserSandbox, UserSandboxSyncEvent
from cubebox.sandbox.lazy import _sync_skills
from cubebox.sandbox.sync_events import UserSandboxSyncEventService
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillCatalogService


@pytest.mark.asyncio
async def test_cold_start_writes_success_event_and_updates_snapshot(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Install a probe skill, run sync → 1 success event + snapshot filled."""
    from tests.e2e.conftest import MemSandbox, install_skill_for_workspace

    ns = fresh_workspace_and_sandbox
    async with session_factory() as s:
        await install_skill_for_workspace(
            s,
            org_id=ns.org_id,
            org_slug=ns.org_slug,
            workspace_id=ns.workspace_id,
            user_id=ns.user_id,
            slug="probe-1",
        )

    mem = MemSandbox()
    cache_dir = Path(tempfile.mkdtemp())
    async with session_factory() as s:
        catalog = SkillCatalogService(session=s, cache=SkillCache(cache_root=cache_dir))
        result = await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=mem,
        )
    assert result.status == "success"

    assert ns.user_sandbox_id is not None
    svc = UserSandboxSyncEventService(session_factory)
    await svc.record(
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id,
        workspace_id=ns.workspace_id,
        result=result,
    )

    async with session_factory() as s:
        events = (
            (
                await s.execute(
                    select(UserSandboxSyncEvent).where(
                        UserSandboxSyncEvent.user_sandbox_id == ns.user_sandbox_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        e = events[0]
        assert e.status == "success"
        assert e.n_pushed >= 1
        assert "skills" in (e.manifest_snapshot or {})

        sb = (
            await s.execute(select(UserSandbox).where(UserSandbox.id == ns.user_sandbox_id))
        ).scalar_one()
        assert sb.skills_manifest_hash is not None
        assert sb.skills_count >= 1
        assert sb.last_skill_sync_at == e.finished_at
        assert sb.last_skill_sync_event_id == e.id


@pytest.mark.asyncio
async def test_failed_writes_failed_event_without_snapshot_bump(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Force tar -xzf to raise → status='failed' → event written, snapshot unchanged."""
    from tests.e2e.conftest import MemSandbox, install_skill_for_workspace

    ns = fresh_workspace_and_sandbox

    async with session_factory() as s:
        await install_skill_for_workspace(
            s,
            org_id=ns.org_id,
            org_slug=ns.org_slug,
            workspace_id=ns.workspace_id,
            user_id=ns.user_id,
            slug="probe-fail",
        )

    mem = MemSandbox()
    original_execute = mem.execute

    async def flaky_execute(cmd: str, **kw: object) -> object:
        if "tar -xzf" in cmd:
            raise RuntimeError("simulated extract failure")
        return await original_execute(cmd, **kw)

    mem.execute = flaky_execute  # type: ignore[method-assign]

    cache_dir = Path(tempfile.mkdtemp())
    async with session_factory() as s:
        catalog = SkillCatalogService(session=s, cache=SkillCache(cache_root=cache_dir))
        result = await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=mem,
        )
    assert result.status == "failed"

    assert ns.user_sandbox_id is not None
    svc = UserSandboxSyncEventService(session_factory)
    await svc.record(
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id,
        workspace_id=ns.workspace_id,
        result=result,
    )

    async with session_factory() as s:
        events = (
            (
                await s.execute(
                    select(UserSandboxSyncEvent).where(
                        UserSandboxSyncEvent.user_sandbox_id == ns.user_sandbox_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].status == "failed"
        assert events[0].manifest_snapshot is None
        assert events[0].error_type is not None

        sb = (
            await s.execute(select(UserSandbox).where(UserSandbox.id == ns.user_sandbox_id))
        ).scalar_one()
        # Snapshot must NOT have been updated on failure.
        assert sb.skills_manifest_hash is None
        assert sb.last_skill_sync_at is None
        assert sb.last_skill_sync_event_id is None


@pytest.mark.asyncio
async def test_hot_path_noop_writes_no_event(
    fresh_workspace_and_sandbox: SimpleNamespace,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two consecutive syncs on the same MemSandbox: 1st = success, 2nd = noop.

    Verify the 2nd sync writes NO new event row. The same MemSandbox instance
    is reused so the manifest file written on the first sync is still present
    for the second sync — modelling PVC persistence.
    """
    from tests.e2e.conftest import MemSandbox, install_skill_for_workspace

    ns = fresh_workspace_and_sandbox

    async with session_factory() as s:
        await install_skill_for_workspace(
            s,
            org_id=ns.org_id,
            org_slug=ns.org_slug,
            workspace_id=ns.workspace_id,
            user_id=ns.user_id,
            slug="probe-hot",
        )

    # One shared MemSandbox so the manifest written by the first sync persists.
    mem = MemSandbox()
    cache_dir = Path(tempfile.mkdtemp())
    assert ns.user_sandbox_id is not None
    svc = UserSandboxSyncEventService(session_factory)

    # First sync — success.
    async with session_factory() as s:
        catalog = SkillCatalogService(session=s, cache=SkillCache(cache_root=cache_dir))
        r1 = await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=mem,
        )
    assert r1.status == "success"
    await svc.record(
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id,
        workspace_id=ns.workspace_id,
        result=r1,
    )

    # Second sync — should be noop (manifest matches sandbox state).
    async with session_factory() as s:
        catalog = SkillCatalogService(session=s, cache=SkillCache(cache_root=cache_dir))
        r2 = await _sync_skills(
            catalog=catalog,
            workspace_id=ns.workspace_id,
            org_id=ns.org_id,
            sandbox=mem,
        )
    assert r2.status == "noop"
    # Defensive guard — record is a no-op for noop results, so this is safe.
    await svc.record(
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id,
        workspace_id=ns.workspace_id,
        result=r2,
    )

    # Still exactly 1 event row (the noop must not have written one).
    async with session_factory() as s:
        events = (
            (
                await s.execute(
                    select(UserSandboxSyncEvent).where(
                        UserSandboxSyncEvent.user_sandbox_id == ns.user_sandbox_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
