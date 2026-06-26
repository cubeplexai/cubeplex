"""E2E: admin sandbox observability routes.

If RBAC regresses (non-admin gets 200) or any route stops projecting the
4 snapshot columns / manifest_snapshot, this fails.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from cubebox.sandbox.sync_events import UserSandboxSyncEventService
from cubebox.sandbox.sync_result import SyncResult

pytestmark = pytest.mark.e2e


async def _seed_success_event(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    user_sandbox_id: str,
    org_id: str,
    workspace_id: str,
    n_pushed: int = 1,
    manifest_hash: str = "sha256:abc",
) -> None:
    """Inject a SyncResult through the writer service (real DB writes)."""
    now = datetime.now(UTC)
    result = SyncResult(
        started_at=now,
        finished_at=now,
        status="success",
        n_pushed=n_pushed,
        n_removed=0,
        tar_size_bytes=1024,
        manifest={"schema_version": 1, "skills": {"probe": {"version": "1.0.0"}}},
        manifest_hash=manifest_hash,
        skills_count=1,
    )
    svc = UserSandboxSyncEventService(session_factory)
    await svc.record(
        user_sandbox_id=user_sandbox_id,
        org_id=org_id,
        workspace_id=workspace_id,
        result=result,
    )


@pytest.mark.asyncio
async def test_list_sandboxes_returns_snapshot_cols(
    admin_client_and_sandbox: tuple[httpx.AsyncClient, SimpleNamespace],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, ns = admin_client_and_sandbox
    await _seed_success_event(
        session_factory,
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id,
        workspace_id=ns.workspace_id,
    )

    r = await client.get("/api/v1/admin/sandboxes")
    assert r.status_code == 200
    body = r.json()
    assert any(s["id"] == ns.user_sandbox_id for s in body)
    me = next(s for s in body if s["id"] == ns.user_sandbox_id)
    assert me["skills_manifest_hash"] == "sha256:abc"
    assert me["skills_count"] == 1
    assert me["last_skill_sync_at"] is not None


@pytest.mark.asyncio
async def test_get_sandbox_404_for_cross_org(
    admin_client_and_sandbox: tuple[httpx.AsyncClient, SimpleNamespace],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Real sandbox in a different org must look like 404, not 200.

    If the route ever drops the org_id filter from its WHERE clause, an admin
    could fetch arbitrary sandboxes across orgs. This guard catches that.
    """
    import secrets as _secrets

    from sqlalchemy import delete as sa_delete

    from cubebox.models import Organization, UserSandbox
    from cubebox.models.user import User
    from cubebox.models.workspace import Workspace

    client, _ns = admin_client_and_sandbox

    suffix = _secrets.token_hex(6)

    foreign_org = Organization(name="cross-org-probe", slug=f"xorg-{suffix}")
    foreign_ws = Workspace(org_id="", name="foreign-ws")  # org_id patched after org insert
    foreign_user = User(
        email=f"xorguser-{suffix}@example.com",
        hashed_password="$2b$12$placeholder",
    )
    foreign_sbx: UserSandbox | None = None

    async with session_factory() as s:
        s.add(foreign_org)
        await s.flush()  # foreign_org.id populated
        foreign_ws.org_id = foreign_org.id
        s.add(foreign_ws)
        s.add(foreign_user)
        await s.flush()  # foreign_ws.id and foreign_user.id populated
        foreign_sbx = UserSandbox(
            org_id=foreign_org.id,
            workspace_id=foreign_ws.id,
            user_id=foreign_user.id,
            scope_type="user",
            scope_id=foreign_user.id,
            sandbox_id=f"sb-xorg-{suffix}",
            status="running",
            image="img",
        )
        s.add(foreign_sbx)
        await s.commit()

    assert foreign_sbx is not None
    foreign_sandbox_id = foreign_sbx.id

    try:
        r = await client.get(f"/api/v1/admin/sandboxes/{foreign_sandbox_id}")
        assert r.status_code == 404, (
            f"cross-org admin should get 404, got {r.status_code} body={r.text}"
        )
    finally:
        async with session_factory() as s:
            await s.execute(sa_delete(UserSandbox).where(UserSandbox.id == foreign_sandbox_id))
            await s.execute(sa_delete(Workspace).where(Workspace.id == foreign_ws.id))
            await s.execute(sa_delete(User).where(User.id == foreign_user.id))
            await s.execute(sa_delete(Organization).where(Organization.id == foreign_org.id))
            await s.commit()


@pytest.mark.asyncio
async def test_list_sandbox_events_returns_event(
    admin_client_and_sandbox: tuple[httpx.AsyncClient, SimpleNamespace],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, ns = admin_client_and_sandbox
    await _seed_success_event(
        session_factory,
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id,
        workspace_id=ns.workspace_id,
    )

    r = await client.get(f"/api/v1/admin/sandboxes/{ns.user_sandbox_id}/sync-events")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["status"] == "success"
    assert "probe" in body[0]["manifest_snapshot"]["skills"]


@pytest.mark.asyncio
async def test_cross_sandbox_events_with_filters(
    admin_client_and_sandbox: tuple[httpx.AsyncClient, SimpleNamespace],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, ns = admin_client_and_sandbox
    await _seed_success_event(
        session_factory,
        user_sandbox_id=ns.user_sandbox_id,
        org_id=ns.org_id,
        workspace_id=ns.workspace_id,
    )

    r = await client.get(
        "/api/v1/admin/sandbox-sync-events",
        params={"workspace_id": ns.workspace_id, "status": "success"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body) >= 1
    assert all(e["status"] == "success" for e in body)
    assert body[0]["org_id"] == ns.org_id
    assert body[0]["workspace_id"] == ns.workspace_id


@pytest.mark.asyncio
async def test_non_admin_gets_403(
    non_admin_client: httpx.AsyncClient,
) -> None:
    r = await non_admin_client.get("/api/v1/admin/sandboxes")
    assert r.status_code == 403
