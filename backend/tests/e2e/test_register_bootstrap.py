"""E2E test: registering a user auto-creates personal org + workspace + admin membership."""

import secrets

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.api.middleware.rate_limit import limiter
from cubebox.db.engine import _build_database_url
from cubebox.models import Role, User
from cubebox.repositories import MembershipRepository, WorkspaceRepository

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.mark.asyncio
async def test_register_creates_org_ws_and_admin_membership(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    assert body["email"] == email
    assert "default_workspace_id" in body, "register response must include default_workspace_id"
    ws_id = body["default_workspace_id"]

    # Verify DB side effects: workspace exists, user has admin membership there
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            ws = await WorkspaceRepository(session).get(ws_id)
            assert ws is not None, "workspace row must exist"
            assert ws.org_id is not None
            mem = await MembershipRepository(session).get_role(
                user_id=body["id"], workspace_id=ws_id
            )
            assert mem == Role.ADMIN, f"user must be admin of new workspace, got {mem}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_register_bootstrap_is_atomic_on_failure(unauthenticated_memory_client, monkeypatch):
    """If org/ws/membership creation blows up, the User row must not be left behind."""
    from cubebox.repositories import OrganizationRepository

    original_create = OrganizationRepository.create

    async def boom(self, name: str):
        raise RuntimeError("simulated org create failure")

    monkeypatch.setattr(OrganizationRepository, "create", boom)

    email = f"u-{secrets.token_hex(4)}@example.com"
    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": "correcthorse-12345"}
    )
    assert r.status_code >= 400, "should not succeed when bootstrap fails"

    # Restore and verify no orphan User row
    monkeypatch.setattr(OrganizationRepository, "create", original_create)

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            user_db = SQLAlchemyUserDatabase(session, User)
            u = await user_db.get_by_email(email)
            assert u is None, "User row must be rolled back when bootstrap fails"
    finally:
        await engine.dispose()
