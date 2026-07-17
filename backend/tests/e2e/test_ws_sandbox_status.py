"""E2E for the workspace sandbox status read endpoint.

GET /api/v1/ws/{ws}/sandbox/status returns the caller's active UserSandbox
row (or status='absent' when none exists). Scope-isolated per workspace.
"""

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.repositories.user_sandbox import UserSandboxRepository


@pytest_asyncio.fixture
async def session_factory() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def test_status_absent_when_no_row(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
) -> None:
    client, ws_id, _user_id = admin_client_with_user_id
    resp = await client.get(f"/api/v1/ws/{ws_id}/sandbox/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "absent"
    assert body["default_image"] is None
    assert body["last_activity_at"] is None
    assert body["browser_url"] is None


async def test_status_running_when_row_exists(
    admin_client_with_user_id: tuple[httpx.AsyncClient, str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    client, ws_id, user_id = admin_client_with_user_id
    # Need org_id — fetch it from the workspace.
    async with session_factory() as s:
        from sqlalchemy import text

        org_id = (
            await s.execute(
                text("SELECT org_id FROM workspaces WHERE id = :w"),
                {"w": ws_id},
            )
        ).scalar_one()
        repo = UserSandboxRepository(s, org_id=org_id, workspace_id=ws_id)
        await repo.create(
            user_id=user_id,
            scope_type="user",
            scope_id=user_id,
            sandbox_id=f"sbx-test-{user_id[-6:]}",
            image="python:3.12",
        )
        await s.commit()

    resp = await client.get(f"/api/v1/ws/{ws_id}/sandbox/status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "running"
    assert body["default_image"] == "python:3.12"
    assert body["last_activity_at"] is not None
    # utc_isoformat() always attaches the UTC offset.
    assert body["last_activity_at"].endswith("+00:00")
    assert body["browser_url"] is None
