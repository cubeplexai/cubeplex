"""E2E: OrganizationMembershipRepository CRUD + invariants."""

import secrets

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Organization, User
from cubeplex.models.organization_membership import OrgRole
from cubeplex.repositories import (
    OrganizationMembershipRepository,
    OrganizationRepository,
)

pytestmark = pytest.mark.e2e


def _unique_slug(prefix: str = "org") -> str:
    """Return a slug that is unique across test runs."""
    return f"{prefix}-{secrets.token_hex(6)}"


async def _make_org(session: AsyncSession, slug: str) -> Organization:
    return await OrganizationRepository(session).create(name=f"Org {slug}", slug=slug)


async def test_grant_and_get_role(session_factory):
    async with session_factory() as session:
        org = await _make_org(session, _unique_slug())
        user = User(
            id=f"u-{secrets.token_hex(4)}",
            email=f"{secrets.token_hex(4)}@e.com",
            hashed_password="x",
        )
        session.add(user)
        await session.commit()

        repo = OrganizationMembershipRepository(session)
        await repo.grant(user_id=user.id, org_id=org.id, role=OrgRole.OWNER)

        role = await repo.get_role(user_id=user.id, org_id=org.id)
        assert role is OrgRole.OWNER


async def test_is_admin_owner_and_admin(session_factory):
    async with session_factory() as session:
        org = await _make_org(session, _unique_slug())
        uid1, uid2, uid3 = (f"u-{secrets.token_hex(4)}" for _ in range(3))
        u1 = User(id=uid1, email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        u2 = User(id=uid2, email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        u3 = User(id=uid3, email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        session.add_all([u1, u2, u3])
        await session.commit()

        repo = OrganizationMembershipRepository(session)
        await repo.grant(user_id=u1.id, org_id=org.id, role=OrgRole.OWNER)
        await repo.grant(user_id=u2.id, org_id=org.id, role=OrgRole.ADMIN)
        await repo.grant(user_id=u3.id, org_id=org.id, role=OrgRole.MEMBER)

        assert await repo.is_admin(user_id=u1.id, org_id=org.id) is True
        assert await repo.is_admin(user_id=u2.id, org_id=org.id) is True
        assert await repo.is_admin(user_id=u3.id, org_id=org.id) is False


async def test_owner_uniqueness_db_enforced(session_factory):
    """Partial unique index forbids two owners in the same org."""
    from sqlalchemy.exc import IntegrityError

    async with session_factory() as session:
        org = await _make_org(session, _unique_slug())
        uid1, uid2 = (f"u-{secrets.token_hex(4)}" for _ in range(2))
        u1 = User(id=uid1, email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        u2 = User(id=uid2, email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        session.add_all([u1, u2])
        await session.commit()

        repo = OrganizationMembershipRepository(session)
        await repo.grant(user_id=u1.id, org_id=org.id, role=OrgRole.OWNER)

        with pytest.raises(IntegrityError):
            await repo.grant(user_id=u2.id, org_id=org.id, role=OrgRole.OWNER)


async def test_list_org_members(session_factory):
    async with session_factory() as session:
        org = await _make_org(session, _unique_slug())
        uid1, uid2 = (f"u-{secrets.token_hex(4)}" for _ in range(2))
        u1 = User(id=uid1, email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        u2 = User(id=uid2, email=f"{secrets.token_hex(4)}@e.com", hashed_password="x")
        session.add_all([u1, u2])
        await session.commit()

        repo = OrganizationMembershipRepository(session)
        await repo.grant(user_id=u1.id, org_id=org.id, role=OrgRole.OWNER)
        await repo.grant(user_id=u2.id, org_id=org.id, role=OrgRole.MEMBER)

        members = await repo.list_org_members(org.id)
        assert {(m.user_id, OrgRole(m.role)) for m in members} == {
            (u1.id, OrgRole.OWNER),
            (u2.id, OrgRole.MEMBER),
        }
