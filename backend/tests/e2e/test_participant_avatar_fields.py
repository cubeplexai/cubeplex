"""E2E test: participant serializers include avatar_url and avatar_seed."""

import secrets

import pytest
from sqlalchemy import select

from cubebox.models import Membership, Organization, OrganizationMembership, OrgRole, Role
from cubebox.models.user import User


async def _create_second_user(db_session, ws_id: str):
    """Create a second user with avatar fields set, add to workspace."""
    suffix = secrets.token_hex(4)
    user = User(
        email=f"second-{suffix}@example.com",
        hashed_password="notused",
        display_name="Second User",
        avatar_url="https://example.com/avatar.png",
        avatar_seed="test-seed-123",
    )
    db_session.add(user)
    await db_session.flush()

    org = (await db_session.execute(select(Organization).limit(1))).scalar_one()

    # Add org membership
    db_session.add(OrganizationMembership(user_id=user.id, org_id=org.id, role=OrgRole.MEMBER))
    # Add workspace membership
    db_session.add(Membership(user_id=user.id, workspace_id=ws_id, role=Role.MEMBER))
    await db_session.commit()

    return user


@pytest.mark.asyncio
async def test_topic_participants_include_avatar_fields(
    admin_client,
    session_factory,
    db_session,
) -> None:
    """GET topic participants includes avatar_url and avatar_seed for each."""
    client, ws_id = admin_client

    # Create second user with avatar fields
    second_user = await _create_second_user(db_session, ws_id)

    # Create a topic
    topic_resp = await client.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "Test Topic"},
    )
    assert topic_resp.status_code == 201, f"Create topic failed: {topic_resp.text}"
    topic = topic_resp.json()
    topic_id = topic["topic"]["id"]

    # Add second user as a participant
    add_resp = await client.post(
        f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
        json={"user_ids": [str(second_user.id)]},
    )
    assert add_resp.status_code == 201, f"Add participant failed: {add_resp.text}"

    # Fetch participants via GET topic (participants are embedded in response)
    topic_resp = await client.get(
        f"/api/v1/ws/{ws_id}/topics/{topic_id}",
    )
    assert topic_resp.status_code == 200, f"Get topic failed: {topic_resp.text}"
    participants = topic_resp.json()["participants"]

    assert len(participants) >= 2, f"Expected at least 2 participants, got {len(participants)}"

    for p in participants:
        assert "avatar_url" in p, f"Participant {p.get('user_id')} missing avatar_url"
        assert "avatar_seed" in p, f"Participant {p.get('user_id')} missing avatar_seed"

    # Second user should have non-null values
    second = next(p for p in participants if p.get("user_id") == str(second_user.id))
    assert second["avatar_url"] == "https://example.com/avatar.png"
    assert second["avatar_seed"] == "test-seed-123"

    # Default user (no avatar) should have null values
    first = next(p for p in participants if p.get("user_id") != str(second_user.id))
    assert first["avatar_url"] is None
    assert first["avatar_seed"] is None
