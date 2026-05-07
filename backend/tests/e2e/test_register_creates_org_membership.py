"""E2E: register creates OrganizationMembership(role=owner) in multi_tenant mode."""

import secrets

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.db.engine import _build_database_url
from cubebox.models import Organization, OrganizationMembership, OrgRole, User

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_register_inserts_org_membership_owner(unauthenticated_memory_client):
    email = f"newuser-{secrets.token_hex(4)}@example.com"
    resp = await unauthenticated_memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_maker() as session:
            user = (await session.execute(select(User).where(User.email == email))).scalar_one()
            org_member = (
                await session.execute(
                    select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
                )
            ).scalar_one()
            assert OrgRole(org_member.role) is OrgRole.OWNER

            org = (
                await session.execute(
                    select(Organization).where(Organization.id == org_member.org_id)
                )
            ).scalar_one()
            assert org.id == org_member.org_id
    finally:
        await engine.dispose()
