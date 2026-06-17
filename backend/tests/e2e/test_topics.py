"""E2E tests for Topics API — lifecycle, participants, access control, upgrade."""

from __future__ import annotations

import secrets
from typing import Any

import httpx
import pytest
from fastapi_users.schemas import BaseUserCreate
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.auth.users import UserManager
from cubebox.db.engine import _build_database_url
from cubebox.models import OrgRole, Role, User, Workspace
from cubebox.repositories import MembershipRepository, OrganizationMembershipRepository

pytestmark = pytest.mark.e2e


FourLayerFixture = tuple[
    tuple[httpx.AsyncClient, str, str],
    tuple[httpx.AsyncClient, str, str],
]


async def _add_extra_workspace_member(workspace_id: str) -> tuple[str, str, str]:
    """Create an extra user and add them to ``workspace_id`` as MEMBER.

    Returns ``(user_id, email, password)``. Useful for tests that need a
    third participant in addition to the admin/member yielded by the
    ``four_layer_admin_and_member`` fixture.
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            ws = await session.get(Workspace, workspace_id)
            assert ws is not None
            org_id = ws.org_id

            email = f"4l-extra-{secrets.token_hex(4)}@example.com"
            password = secrets.token_urlsafe(16)
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)
            await session.commit()

            mem_repo = MembershipRepository(session)
            await mem_repo.grant(user_id=user.id, workspace_id=workspace_id, role=Role.MEMBER)
            om_repo = OrganizationMembershipRepository(session)
            if await om_repo.get_role(user_id=user.id, org_id=org_id) is None:
                await om_repo.grant(user_id=user.id, org_id=org_id, role=OrgRole.MEMBER)
            await session.commit()
            return user.id, email, password
    finally:
        await test_engine.dispose()


async def _make_non_workspace_user() -> str:
    """Create a user with no membership in any cubebox workspace.

    Returns the user_id. Used to verify the topic invite path rejects
    user_ids that aren't workspace members.
    """
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            email = f"4l-outsider-{secrets.token_hex(4)}@example.com"
            password = secrets.token_urlsafe(16)
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)
            await session.commit()
            return user.id
    finally:
        await test_engine.dispose()


async def _insert_scheduled_task_binding(
    *, org_id: str, workspace_id: str, owner_user_id: str, conversation_id: str
) -> None:
    """Insert a ScheduledTask row pointing at ``conversation_id``.

    Used to verify upgrade-to-topic rejects conversations bound to a
    scheduler target (the external binding guard).
    """
    from cubebox.models.scheduled_task import ScheduledTask

    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            task = ScheduledTask(
                org_id=org_id,
                workspace_id=workspace_id,
                owner_user_id=owner_user_id,
                name="binding-guard-fixture",
                schedule_kind="interval",
                interval_seconds=3600,
                prompt="ping",
                target_mode="fixed",
                target_conversation_id=conversation_id,
            )
            session.add(task)
            await session.commit()
    finally:
        await test_engine.dispose()


async def _login_extra(app: Any, email: str, password: str) -> httpx.AsyncClient:
    """Return a logged-in client for an extra workspace user."""
    transport = httpx.ASGITransport(app=app)
    c = httpx.AsyncClient(transport=transport, base_url="http://test")
    login = await c.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    assert login.status_code in (200, 204), login.text
    me = await c.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text
    csrf = c.cookies.get("cubebox_csrf_50") or c.cookies.get("cubebox_csrf")
    if csrf:
        c.headers["X-CSRF-Token"] = csrf
    return c


class TestTopicConversationAccess:
    """Topic conversations are visible to all participants."""

    @pytest.mark.anyio
    async def test_topic_conversation_visible_to_member(
        self,
        four_layer_admin_and_member: FourLayerFixture,
    ) -> None:
        (admin_c, ws_id, _admin_uid), (member_c, _, member_uid) = four_layer_admin_and_member

        # Admin creates a topic and adds member
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": "Shared Topic",
                "sandbox_mode": "dedicated",
                "member_user_ids": [member_uid],
            },
        )
        assert resp.status_code == 201, resp.text
        topic_data = resp.json()
        conv_id = topic_data["conversation"]["id"]

        # Member can see the conversation
        conv_resp = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert conv_resp.status_code == 200, conv_resp.text

    @pytest.mark.anyio
    async def test_non_participant_cannot_see_topic_conversation(
        self,
        four_layer_admin_and_member: FourLayerFixture,
    ) -> None:
        (admin_c, ws_id, _admin_uid), (member_c, _, _member_uid) = four_layer_admin_and_member

        # Admin creates a topic WITHOUT adding member
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Private Topic"},
        )
        assert resp.status_code == 201, resp.text
        conv_id = resp.json()["conversation"]["id"]

        # Member cannot see it
        conv_resp = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert conv_resp.status_code == 404, conv_resp.text

    @pytest.mark.anyio
    async def test_personal_conversation_still_private(
        self,
        four_layer_admin_and_member: FourLayerFixture,
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, _member_uid) = four_layer_admin_and_member

        # Admin creates a personal conversation (no topic)
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Admin Private"},
        )
        assert resp.status_code == 201, resp.text
        conv_id = resp.json()["id"]

        # Member cannot see it
        conv_resp = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert conv_resp.status_code == 404, conv_resp.text


class TestTopicCRUD:
    """Topic create, list, get, update, delete."""

    @pytest.mark.anyio
    async def test_create_topic_returns_topic_and_conversation(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), _ = four_layer_admin_and_member
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "My Topic", "sandbox_mode": "dedicated"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["topic"]["title"] == "My Topic"
        assert data["topic"]["sandbox_mode"] == "dedicated"
        assert data["conversation"]["topic_id"] == data["topic"]["id"]
        assert len(data["participants"]) == 1
        assert data["participants"][0]["role"] == "owner"

    @pytest.mark.anyio
    async def test_list_topics_shows_only_participant_topics(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        # Admin creates two topics, only one includes member
        r1 = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Shared", "member_user_ids": [member_uid]},
        )
        assert r1.status_code == 201, r1.text
        r2 = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Admin Only"},
        )
        assert r2.status_code == 201, r2.text

        # Member sees only the shared topic
        resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics")
        assert resp.status_code == 200, resp.text
        titles = [t["title"] for t in resp.json()["items"]]
        assert "Shared" in titles
        assert "Admin Only" not in titles

    @pytest.mark.anyio
    async def test_get_topic_by_non_participant_returns_404(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, _) = four_layer_admin_and_member
        create = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Hidden"},
        )
        topic_id = create.json()["topic"]["id"]
        resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert resp.status_code == 404, resp.text

    @pytest.mark.anyio
    async def test_update_topic_owner_only(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member
        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Old Title", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Member cannot update
        resp = await member_c.patch(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}",
            json={"title": "Hacked"},
        )
        assert resp.status_code == 403, resp.text

        # Owner can
        resp = await admin_c.patch(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}",
            json={"title": "New Title"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["topic"]["title"] == "New Title"

    @pytest.mark.anyio
    async def test_delete_topic_owner_only(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member
        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Owner-only Delete", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Member cannot delete
        resp = await member_c.delete(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert resp.status_code == 403, resp.text

    @pytest.mark.anyio
    async def test_delete_topic_archives(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), _ = four_layer_admin_and_member
        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Doomed"},
        )
        topic_id = create_resp.json()["topic"]["id"]

        del_resp = await admin_c.delete(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert del_resp.status_code == 204, del_resp.text

        # Archived topic no longer visible in list
        list_resp = await admin_c.get(f"/api/v1/ws/{ws_id}/topics")
        ids = [t["id"] for t in list_resp.json()["items"]]
        assert topic_id not in ids

        # And the single-topic GET also hides it (consistent with list).
        get_resp = await admin_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp.status_code == 404, get_resp.text


class TestTopicParticipants:
    """Participant add, remove, role transfer."""

    @pytest.mark.anyio
    async def test_add_participant_owner_only(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        # Member creates a topic (becomes owner)
        create_resp = await member_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Member-owned"},
        )
        assert create_resp.status_code == 201, create_resp.text
        topic_id = create_resp.json()["topic"]["id"]

        # Admin is not a participant — cannot add (404 because they don't
        # see the topic at all under the participant-scoped repo).
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [member_uid]},
        )
        assert resp.status_code in (403, 404), resp.text

    @pytest.mark.anyio
    async def test_add_and_remove_participant(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Team"},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Add member
        add_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [member_uid]},
        )
        assert add_resp.status_code == 201, add_resp.text

        # Verify member can now see the topic
        get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp.status_code == 200, get_resp.text
        assert len(get_resp.json()["participants"]) == 2

        # Member leaves (self-removal)
        leave_resp = await member_c.delete(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{member_uid}"
        )
        assert leave_resp.status_code == 204, leave_resp.text

        # Member can no longer see the topic
        get_resp2 = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp2.status_code == 404, get_resp2.text

    @pytest.mark.anyio
    async def test_owner_removes_other_participant(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Cleanup", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Owner removes member
        resp = await admin_c.delete(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{member_uid}"
        )
        assert resp.status_code == 204, resp.text

        # Member can no longer see the topic
        get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp.status_code == 404, get_resp.text

    @pytest.mark.anyio
    async def test_non_owner_cannot_remove_other(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, admin_uid), (member_c, _, member_uid) = four_layer_admin_and_member

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Boundary", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Member (non-owner) tries to remove admin → 403
        resp = await member_c.delete(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{admin_uid}"
        )
        assert resp.status_code == 403, resp.text

    @pytest.mark.anyio
    async def test_last_owner_leaves_promotes_oldest_member(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, admin_uid), (member_c, _, member_uid) = four_layer_admin_and_member

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Handoff", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Sole owner (admin) leaves
        resp = await admin_c.delete(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{admin_uid}"
        )
        assert resp.status_code == 204, resp.text

        # Member is now the auto-promoted owner
        get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp.status_code == 200, get_resp.text
        participants = get_resp.json()["participants"]
        assert len(participants) == 1
        assert participants[0]["user_id"] == member_uid
        assert participants[0]["role"] == "owner"

    @pytest.mark.anyio
    async def test_transfer_ownership_demotes_previous_owner(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, admin_uid), (member_c, _, member_uid) = four_layer_admin_and_member

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Transfer", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Admin promotes member to owner. Round-3 fix: this MUST demote
        # admin to member, leaving exactly one owner.
        resp = await admin_c.patch(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{member_uid}",
            json={"role": "owner"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["participant"]["role"] == "owner"

        # Verify only one owner exists, and it's member
        get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert get_resp.status_code == 200, get_resp.text
        participants = get_resp.json()["participants"]
        owners = [p for p in participants if p["role"] == "owner"]
        assert len(owners) == 1
        assert owners[0]["user_id"] == member_uid

        # Admin can no longer rename — they were demoted
        rename = await admin_c.patch(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}",
            json={"title": "Should Fail"},
        )
        assert rename.status_code == 403, rename.text

        # Member (new owner) can rename
        rename2 = await member_c.patch(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}",
            json={"title": "Owned by Member"},
        )
        assert rename2.status_code == 200, rename2.text

    @pytest.mark.anyio
    async def test_sole_owner_cannot_self_demote(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, admin_uid), (_, _, member_uid) = four_layer_admin_and_member

        # Single-owner topic (member is just a member)
        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Sole", "member_user_ids": [member_uid]},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Sole owner tries to step down → 400 with the spec error message
        resp = await admin_c.patch(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants/{admin_uid}",
            json={"role": "member"},
        )
        assert resp.status_code == 400, resp.text
        assert "promote another member to owner first" in resp.text

    @pytest.mark.anyio
    async def test_duplicate_user_ids_idempotent(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (_, _, member_uid) = four_layer_admin_and_member

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Dedup"},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Duplicates in the same request → only one row created
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [member_uid, member_uid]},
        )
        assert resp.status_code == 201, resp.text
        assert len(resp.json()["participants"]) == 1

        # Idempotent: adding an already-member returns 201 with empty list
        resp2 = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [member_uid]},
        )
        assert resp2.status_code == 201, resp2.text
        assert resp2.json()["participants"] == []

        # Total participants is still owner + 1 member = 2
        get_resp = await admin_c.get(f"/api/v1/ws/{ws_id}/topics/{topic_id}")
        assert len(get_resp.json()["participants"]) == 2

    @pytest.mark.anyio
    async def test_add_non_workspace_member_rejected(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), _ = four_layer_admin_and_member
        outsider_uid = await _make_non_workspace_user()

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Gate"},
        )
        topic_id = create_resp.json()["topic"]["id"]

        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [outsider_uid]},
        )
        assert resp.status_code == 400, resp.text
        assert "not a member of this workspace" in resp.text

    @pytest.mark.anyio
    async def test_max_participant_cap_enforced(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (_, _, member_uid) = four_layer_admin_and_member

        create_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={"title": "Cap"},
        )
        topic_id = create_resp.json()["topic"]["id"]

        # Add the available second user — succeeds (2 of 20)
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [member_uid]},
        )
        assert resp.status_code == 201, resp.text

        # Provision 19 more workspace members to push us to the 20 cap.
        # The 20th invite (1 owner + 19 added = 20) should fit, and the
        # 21st must fail.
        added_uids: list[str] = []
        for _ in range(19):
            uid, _email, _pw = await _add_extra_workspace_member(ws_id)
            added_uids.append(uid)

        # Add 18 more (current_count=2, cap=20 → room for 18 more)
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": added_uids[:18]},
        )
        assert resp.status_code == 201, resp.text

        # 21st add must exceed the cap
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics/{topic_id}/participants",
            json={"user_ids": [added_uids[18]]},
        )
        assert resp.status_code == 400, resp.text
        assert "exceed max" in resp.text


class TestUpgradeToTopic:
    """Convert a 1:1 conversation to a topic group chat."""

    @pytest.mark.anyio
    async def test_upgrade_conversation(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

        # Create a personal conversation
        conv_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Personal Chat"},
        )
        assert conv_resp.status_code == 201, conv_resp.text
        conv_id = conv_resp.json()["id"]

        # Upgrade to topic
        upgrade_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={
                "title": "Group Chat",
                "sandbox_mode": "dedicated",
                "member_user_ids": [member_uid],
            },
        )
        assert upgrade_resp.status_code == 201, upgrade_resp.text
        data = upgrade_resp.json()
        assert data["conversation"]["topic_id"] == data["topic"]["id"]

        # Member can now see the conversation
        get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert get_resp.status_code == 200, get_resp.text

    @pytest.mark.anyio
    async def test_double_upgrade_fails(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), _ = four_layer_admin_and_member

        conv_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Already Upgraded"},
        )
        conv_id = conv_resp.json()["id"]

        first = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={"title": "Group"},
        )
        assert first.status_code == 201, first.text

        # Second upgrade fails
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={"title": "Again"},
        )
        assert resp.status_code == 409, resp.text

    @pytest.mark.anyio
    async def test_upgrade_blocked_when_bound_to_scheduled_task(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, admin_uid), _ = four_layer_admin_and_member

        # Get the admin's org_id via /auth/me-style lookup through a topic
        # create — the topic response is workspace-scoped so we can recover
        # org_id from the DB. Easier: use a tiny helper that loads it.
        from cubebox.models import Workspace as _Workspace

        engine = create_async_engine(_build_database_url(), poolclass=NullPool)
        try:
            async with async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )() as session:
                ws = await session.get(_Workspace, ws_id)
                assert ws is not None
                org_id = ws.org_id
        finally:
            await engine.dispose()

        conv_resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Bound"},
        )
        conv_id = conv_resp.json()["id"]

        await _insert_scheduled_task_binding(
            org_id=org_id,
            workspace_id=ws_id,
            owner_user_id=admin_uid,
            conversation_id=conv_id,
        )

        # Upgrade is blocked because of the scheduler binding (round-3 fix).
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={"title": "Try"},
        )
        assert resp.status_code == 409, resp.text
        assert "binding" in resp.text.lower()
