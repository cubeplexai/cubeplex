"""E2E: cubeplex admin grant-admin / revoke-admin via subprocess."""

import os
import secrets
import subprocess

import pytest
from sqlalchemy import select

from cubeplex.models import Organization, OrgRole, User
from cubeplex.repositories import OrganizationMembershipRepository

pytestmark = pytest.mark.e2e


def _run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("ENV_FOR_DYNACONF", "test")
    return subprocess.run(
        ["uv", "run", "cubeplex", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.join(os.path.dirname(__file__), "..", ".."),
        check=False,
    )


async def test_grant_admin_promotes_member(memory_client, session_factory):
    """Register a fresh user; grant-admin promotes an explicit org member."""
    from tests.e2e.conftest import DEFAULT_ORG_ID

    email = f"member-{secrets.token_hex(4)}@example.com"
    resp = await memory_client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert resp.status_code == 201, resp.text

    slug: str
    user_id: str
    org_id = DEFAULT_ORG_ID
    async with session_factory() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user_id = user.id
        org = (
            await session.execute(select(Organization).where(Organization.id == org_id))
        ).scalar_one()
        slug = org.slug
        await OrganizationMembershipRepository(session).grant(
            user_id=user.id,
            org_id=org.id,
            role=OrgRole.MEMBER,
        )
        await session.commit()

    proc = _run_cli(["admin", "grant-admin", email, "--org-slug", slug])
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "Promoted" in proc.stdout

    async with session_factory() as session:
        role = await OrganizationMembershipRepository(session).get_role(
            user_id=user_id, org_id=org_id
        )
        assert role is OrgRole.ADMIN


async def test_grant_admin_refuses_to_revoke_owner(memory_client, session_factory):
    """The default test user is owner of their fixture org; revoke-admin must refuse."""
    from tests.e2e.conftest import DEFAULT_ORG_ID, DEFAULT_TEST_EMAIL

    # The default test user is OWNER of DEFAULT_ORG_ID (granted in conftest's
    # _ensure_default_user_and_membership). They also own a personal auto-bootstrapped
    # org via on_after_register, so we look up DEFAULT_ORG_ID's slug explicitly.
    async with session_factory() as session:
        org = (
            await session.execute(select(Organization).where(Organization.id == DEFAULT_ORG_ID))
        ).scalar_one()
        slug = org.slug

    proc = _run_cli(["admin", "revoke-admin", DEFAULT_TEST_EMAIL, "--org-slug", slug])
    assert proc.returncode != 0
    assert "owner" in (proc.stderr.lower() + proc.stdout.lower())
