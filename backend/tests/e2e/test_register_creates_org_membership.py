"""E2E: multi_tenant register creates the user ONLY — no org membership yet.

Onboarding (org/workspace bootstrap) is deferred to the /onboarding wizard,
so a freshly registered multi_tenant user has no OrganizationMembership.
"""

import secrets

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.db.engine import _build_database_url
from cubeplex.models import OrganizationMembership, User

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_register_creates_no_org_membership(unauthenticated_memory_client):
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
            memberships = (
                (
                    await session.execute(
                        select(OrganizationMembership).where(
                            OrganizationMembership.user_id == user.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert memberships == [], "register must not create any org membership"
    finally:
        await engine.dispose()
