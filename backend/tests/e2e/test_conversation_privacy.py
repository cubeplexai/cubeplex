"""E2E test: conversations are private to their creator even inside a shared workspace.

Product rule (2026-04): a conversation is only visible to the user who
created it. Two members of the same workspace must not be able to see
each other's conversations via list/get/update/delete/messages.
"""

import secrets

import pytest

from cubeplex.api.middleware.rate_limit import limiter
from tests.e2e.helpers import csrf_cookie_name as _csrf_cookie_name

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


async def _seed_csrf(client) -> str:
    await client.get("/api/v1/auth/me")
    csrf = client.cookies.get(_csrf_cookie_name())
    assert csrf, "cubeplex_csrf cookie not set after GET /api/v1/auth/me"
    return csrf


async def _login(client, email: str, password: str) -> str:
    client.cookies.clear()
    r = await client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert r.status_code in (200, 204), r.text
    return await _seed_csrf(client)


@pytest.mark.asyncio
async def test_conversation_invisible_to_other_member_same_workspace(
    unauthenticated_memory_client,
    session_factory,
):
    """B (a member of the same workspace) cannot see A's conversation."""
    client = unauthenticated_memory_client

    a_email = f"a-{secrets.token_hex(4)}@example.com"
    b_email = f"b-{secrets.token_hex(4)}@example.com"
    pw = "passwordpassword"

    for email in (a_email, b_email):
        r = await client.post("/api/v1/auth/register", json={"email": email, "password": pw})
        assert r.status_code == 201, r.text

    # --- A: onboard (creates org + workspace), create a conversation ------
    csrf_a = await _login(client, a_email, pw)
    r = await client.post(
        "/api/v1/onboarding",
        json={
            "org_name": "SharedOrg",
            "org_slug": f"shared-{secrets.token_hex(2)}",
            "workspace_name": "Shared",
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    ws_id = r.json()["workspace_id"]
    a_me = await client.get("/api/v1/auth/me")
    assert a_me.status_code == 200, a_me.text
    a_org_id = a_me.json()["org_memberships"][0]["org_id"]

    r = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": "A's private chat"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert r.status_code == 201, r.text
    conv_id = r.json()["id"]

    # --- B: added to A's org + workspace as a member ----------------------
    # Workspace invites were removed (org invites are the new path). This test
    # is about conversation privacy, not invitation, so seed the membership
    # directly: grant B org membership, then workspace membership.
    csrf_b = await _login(client, b_email, pw)
    b_me = await client.get("/api/v1/auth/me")
    assert b_me.status_code == 200, b_me.text
    b_user_id = b_me.json()["id"]

    from cubeplex.models import OrgRole, Role
    from cubeplex.repositories import (
        MembershipRepository,
        OrganizationMembershipRepository,
    )

    async with session_factory() as session:
        await OrganizationMembershipRepository(session).grant(
            user_id=b_user_id, org_id=a_org_id, role=OrgRole.MEMBER
        )
        await MembershipRepository(session).grant(
            user_id=b_user_id, workspace_id=ws_id, role=Role.MEMBER
        )
        await session.commit()

    # B's list must be empty — A's conversation is filtered out by creator_user_id.
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 0
    assert body["conversations"] == []

    # Direct read: 404 (structurally invisible, not 403).
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
    assert r.status_code == 404, r.text

    # Update: 404.
    r = await client.patch(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}",
        params={"title": "Hijacked"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 404, r.text

    # Delete: 404.
    r = await client.delete(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 404, r.text

    # Messages list: 404 (the conversation-existence pre-check fires first).
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages")
    assert r.status_code == 404, r.text

    # Send message: 404 (same pre-check gate).
    r = await client.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": "sneaky"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert r.status_code == 404, r.text

    # --- A positive control: A still sees their own conversation ------------
    csrf_a = await _login(client, a_email, pw)
    r = await client.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "A's private chat"

    r = await client.get(f"/api/v1/ws/{ws_id}/conversations")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["conversations"][0]["id"] == conv_id
