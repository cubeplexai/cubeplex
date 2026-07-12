"""E2E: multi_tenant register creates the user ONLY — no org/workspace/membership.

Bootstrap is deferred to the /onboarding wizard (see test_onboarding.py for the
org/workspace creation + atomicity + slug assertions). Register just creates
the User row and returns an empty default_workspace_id.
"""

import secrets

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.api.middleware.rate_limit import limiter
from cubeplex.db.engine import _build_database_url
from cubeplex.models import Membership, OrganizationMembership, User

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.mark.asyncio
async def test_register_creates_user_only_no_bootstrap(unauthenticated_memory_client):
    email = f"u-{secrets.token_hex(4)}@example.com"
    pw = "correcthorsebatterystaple"

    r = await unauthenticated_memory_client.post(
        "/api/v1/auth/register", json={"email": email, "password": pw}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == email
    assert body["default_workspace_id"] == "", "register must not pre-allocate a workspace"

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as session:
            user = (await session.execute(sa.select(User).where(User.email == email))).scalar_one()

            # No org membership, no workspace membership for this user.
            om = (
                (
                    await session.execute(
                        sa.select(OrganizationMembership).where(
                            OrganizationMembership.user_id == user.id  # type: ignore[arg-type]
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert om == [], "register must not create an org membership"

            mem = (
                (
                    await session.execute(
                        sa.select(Membership).where(Membership.user_id == user.id)  # type: ignore[arg-type]
                    )
                )
                .scalars()
                .all()
            )
            assert mem == [], "register must not create a workspace membership"
    finally:
        await engine.dispose()
