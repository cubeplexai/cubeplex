"""Four-branch _scoped_select: creator, conv participant, topic participant."""

from __future__ import annotations

import secrets
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi_users.schemas import BaseUserCreate
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.auth.users import UserManager
from cubebox.credentials.encryption import FernetBackend
from cubebox.db.engine import _build_database_url
from cubebox.models import OrgRole, Role, User, Workspace
from cubebox.repositories import MembershipRepository, OrganizationMembershipRepository
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.sandbox.manager import SandboxManager

pytestmark = pytest.mark.e2e


FourLayerFixture = tuple[
    tuple[httpx.AsyncClient, str, str],
    tuple[httpx.AsyncClient, str, str],
]


_ENCRYPTION_BACKEND = FernetBackend([Fernet.generate_key()])


async def _add_extra_workspace_member(workspace_id: str) -> tuple[str, str, str]:
    """Create an extra user and add them to ``workspace_id`` as MEMBER."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            ws = await session.get(Workspace, workspace_id)
            assert ws is not None
            org_id = ws.org_id

            email = f"cp-extra-{secrets.token_hex(4)}@example.com"
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
    """Create a user with no workspace membership; returns user_id."""
    test_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            email = f"cp-outsider-{secrets.token_hex(4)}@example.com"
            password = secrets.token_urlsafe(16)
            user_db = SQLAlchemyUserDatabase(session, User)
            manager = UserManager(user_db)
            user = await manager.create(BaseUserCreate(email=email, password=password), safe=False)
            await session.commit()
            return user.id
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


def _engine_maker() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, maker


@pytest.mark.anyio
async def test_conv_participant_sees_standalone_group_chat(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    conv = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()
    conv_id = conv["id"]

    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["conversation"]["is_group_chat"] is True
    assert any(p["user_id"] == member_uid for p in payload["participants"])

    member_list = (await member_c.get(f"/api/v1/ws/{ws_id}/conversations")).json()
    assert any(c["id"] == conv_id for c in member_list["conversations"])


@pytest.mark.anyio
async def test_invite_promotes_personal_to_group_chat(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    (admin_c, ws_id, _), (_, _, member_uid) = four_layer_admin_and_member

    conv = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()
    conv_id = conv["id"]
    assert conv["is_group_chat"] is False

    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text

    get_resp = await admin_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json()["is_group_chat"] is True


@pytest.mark.anyio
async def test_invite_idempotent(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    (admin_c, ws_id, _), (_, _, member_uid) = four_layer_admin_and_member

    conv_id = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()["id"]

    first = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [member_uid]},
    )
    assert first.status_code == 201, first.text
    assert any(p["user_id"] == member_uid for p in first.json()["participants"])

    second = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [member_uid]},
    )
    assert second.status_code == 201, second.text
    assert second.json()["participants"] == []

    parts = await admin_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/participants")
    assert parts.status_code == 200, parts.text
    member_rows = [p for p in parts.json()["items"] if p["user_id"] == member_uid]
    assert len(member_rows) == 1


@pytest.mark.anyio
async def test_invite_validates_workspace_membership(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    (admin_c, ws_id, _), _ = four_layer_admin_and_member
    outsider_uid = await _make_non_workspace_user()

    conv_id = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()["id"]
    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [outsider_uid]},
    )
    assert resp.status_code == 400, resp.text


@pytest.mark.anyio
async def test_invitee_can_see_and_send(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    conv_id = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()["id"]
    invite = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [member_uid]},
    )
    assert invite.status_code == 201, invite.text

    listing = await member_c.get(f"/api/v1/ws/{ws_id}/conversations")
    assert listing.status_code == 200, listing.text
    assert any(c["id"] == conv_id for c in listing.json()["conversations"])

    msg_resp = await member_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": "hi from member"},
    )
    assert msg_resp.status_code != 404, msg_resp.text


@pytest.mark.anyio
async def test_non_invitee_404s_on_send(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    (admin_c, ws_id, _), (_, _, _) = four_layer_admin_and_member
    app = admin_c._transport.app  # type: ignore[attr-defined]

    extra_uid, extra_email, extra_pw = await _add_extra_workspace_member(ws_id)
    extra_c = await _login_extra(app, extra_email, extra_pw)
    try:
        conv_id = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()["id"]

        msg_resp = await extra_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
            json={"content": "should fail"},
        )
        assert msg_resp.status_code == 404, msg_resp.text
        assert extra_uid  # not part of the conv; silence unused warning
    finally:
        await extra_c.aclose()


@pytest.mark.anyio
async def test_send_auto_joins_topic_participant(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    """A topic participant who has never sent in a conv is auto-joined to
    ``conversation_participants`` on first send, and ``is_group_chat`` flips
    to True on the 1 -> 2 transition.
    """
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "AutoJoin", "member_user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text
    conv_id = resp.json()["conversation"]["id"]

    engine, maker = _engine_maker()
    try:
        async with maker() as session:
            count_before = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM conversation_participants "
                        "WHERE conversation_id=:c AND user_id=:u"
                    ),
                    {"c": conv_id, "u": member_uid},
                )
            ).scalar_one()
            assert count_before == 0

        msg_resp = await member_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
            json={"content": "hi"},
        )
        assert msg_resp.status_code != 404, msg_resp.text

        async with maker() as session:
            count_after = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM conversation_participants "
                        "WHERE conversation_id=:c AND user_id=:u"
                    ),
                    {"c": conv_id, "u": member_uid},
                )
            ).scalar_one()
            assert count_after == 1

            is_group_chat = (
                await session.execute(
                    text("SELECT is_group_chat FROM conversations WHERE id=:c"),
                    {"c": conv_id},
                )
            ).scalar_one()
            assert bool(is_group_chat) is True
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_topic_participant_cannot_answer_hitl_until_they_send(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    """P(topic) without P(conv) gets 404 on submit_sandbox_confirm — the
    conv-participant gate runs BEFORE the run lookup, so a topic-only member
    can't even probe for pending HITL.
    """
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "HITL Gate", "member_user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text
    conv_id = resp.json()["conversation"]["id"]

    hitl_before = await member_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/sandbox-confirm/q-fake",
        json={"decision": "approve"},
    )
    assert hitl_before.status_code == 404, hitl_before.text
    # Body must look like the conversation-not-found 404, not a {"code":...}
    # error from a downstream branch.
    body = hitl_before.json()
    assert isinstance(body.get("detail"), str), body


@pytest.mark.anyio
async def test_p_conv_can_answer_hitl_after_first_send(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    """After the topic-participant sends (auto-join), the HITL participant
    gate flips and the request advances past the conv-not-found 404 to the
    run-lookup branch (which 404s with structured ``no_pending`` since no
    paused run exists)."""
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "HITL After Send", "member_user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text
    conv_id = resp.json()["conversation"]["id"]

    # First send -> auto-join.
    msg_resp = await member_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": "join via send"},
    )
    assert msg_resp.status_code != 404, msg_resp.text

    hitl = await member_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/sandbox-confirm/q-fake",
        json={"decision": "approve"},
    )
    # Still 404 (no paused run) but with the run-lookup branch's structured
    # ``{"code": "no_pending"}`` detail — proving the participant gate is
    # past, not the bare-string conv-not-found body.
    assert hitl.status_code == 404, hitl.text
    body = hitl.json()
    detail = body.get("detail")
    assert isinstance(detail, dict) and detail.get("code") == "no_pending", body


@pytest.mark.anyio
async def test_topic_participant_can_subscribe_sse_without_sending(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    """Topic-only member should be able to load conversation context (the
    permission the SSE handler will gate on) without being added to
    ``conversation_participants``.

    The repo plans a `GET /conversations/{id}/stream` SSE endpoint behind a
    `P(topic) ∨ P(conv)` gate, but the current implementation only exposes
    per-run SSE (`/runs/{run_id}/stream`) which 404s without a real run id.
    Until the conversation-level stream lands, we exercise the same gate
    via the bootstrap + GET endpoints that share the conv visibility check,
    and assert no `conversation_participants` row is created.
    """
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "SSE Probe", "member_user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text
    conv_id = resp.json()["conversation"]["id"]

    get_resp = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
    assert get_resp.status_code == 200, get_resp.text
    boot_resp = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}/bootstrap")
    assert boot_resp.status_code == 200, boot_resp.text

    engine, maker = _engine_maker()
    try:
        async with maker() as session:
            count = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM conversation_participants "
                        "WHERE conversation_id=:c AND user_id=:u"
                    ),
                    {"c": conv_id, "u": member_uid},
                )
            ).scalar_one()
        assert count == 0
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_standalone_group_chat_dedicated_sandbox_keys_by_conversation(
    fake_opensandbox: None,
    session_factory: async_sessionmaker[AsyncSession],
    seeded_org_ws_user: tuple[str, str, str, str],
) -> None:
    """``SandboxManager.get_or_create`` with ``scope_type='conversation'``
    produces a row keyed by the conversation id — the manager layer is what
    the standalone-group-chat code path drives, so we assert at that seam
    instead of spinning up a real LLM.
    """
    del fake_opensandbox
    org_id, ws_a, _ws_b, user_id = seeded_org_ws_user
    mgr = SandboxManager(session_factory, _ENCRYPTION_BACKEND)

    conv_id = f"conv-grp-{secrets.token_hex(4)}"
    await mgr.get_or_create(
        scope_type="conversation",
        scope_id=conv_id,
        user_id=user_id,
        org_id=org_id,
        workspace_id=ws_a,
    )

    async with session_factory() as s:
        repo = UserSandboxRepository(s, org_id=org_id, workspace_id=ws_a)
        row = await repo.get_active_by_scope(scope_type="conversation", scope_id=conv_id)
    assert row is not None
    assert (row.scope_type, row.scope_id) == ("conversation", conv_id)


@pytest.mark.anyio
async def test_upgrade_to_topic_rekeys_sandbox(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    """Standalone group chat (conv-keyed sandbox) -> upgrade-to-topic ->
    sandbox row is rekeyed to (topic, topic_id) in the same transaction.
    """
    (admin_c, ws_id, admin_uid), (_, _, member_uid) = four_layer_admin_and_member

    conv_id = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()["id"]
    invite = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [member_uid]},
    )
    assert invite.status_code == 201, invite.text

    engine, maker = _engine_maker()
    try:
        # Seed a sandbox row keyed by (conversation, conv_id).
        from cubebox.models.user_sandbox import UserSandbox

        async with maker() as session:
            org_id = (
                await session.execute(
                    text("SELECT org_id FROM workspaces WHERE id=:w"), {"w": ws_id}
                )
            ).scalar_one()
            sbx = UserSandbox(
                org_id=org_id,
                workspace_id=ws_id,
                user_id=admin_uid,
                scope_type="conversation",
                scope_id=conv_id,
                sandbox_id=f"prov-{secrets.token_hex(6)}",
                status="running",
                image="ignored",
                ttl_seconds=3600,
            )
            session.add(sbx)
            await session.commit()
            sbx_id = sbx.id

        up = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={"title": "Upgraded", "sandbox_mode": "dedicated"},
        )
        assert up.status_code == 201, up.text
        topic_id = up.json()["topic"]["id"]

        async with maker() as session:
            row = await session.get(UserSandbox, sbx_id)
            assert row is not None
            assert (row.scope_type, row.scope_id) == ("topic", topic_id)
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_personal_to_topic_direct_rekey(
    four_layer_admin_and_member: FourLayerFixture,
) -> None:
    """Personal 1:1 (user-keyed sandbox) -> upgrade-to-topic (no intermediate
    group chat) -> sandbox row rekeyed from (user, creator_id) to
    (topic, topic_id).
    """
    (admin_c, ws_id, admin_uid), (_, _, member_uid) = four_layer_admin_and_member

    conv_id = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()["id"]

    engine, maker = _engine_maker()
    try:
        from cubebox.models.user_sandbox import UserSandbox

        async with maker() as session:
            org_id = (
                await session.execute(
                    text("SELECT org_id FROM workspaces WHERE id=:w"), {"w": ws_id}
                )
            ).scalar_one()
            sbx = UserSandbox(
                org_id=org_id,
                workspace_id=ws_id,
                user_id=admin_uid,
                scope_type="user",
                scope_id=admin_uid,
                sandbox_id=f"prov-{secrets.token_hex(6)}",
                status="running",
                image="ignored",
                ttl_seconds=3600,
            )
            session.add(sbx)
            await session.commit()
            sbx_id = sbx.id

        up = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations/{conv_id}/upgrade-to-topic",
            json={
                "title": "Direct Upgrade",
                "sandbox_mode": "dedicated",
                "member_user_ids": [member_uid],
            },
        )
        assert up.status_code == 201, up.text
        topic_id = up.json()["topic"]["id"]

        async with maker() as session:
            row = await session.get(UserSandbox, sbx_id)
            assert row is not None
            assert (row.scope_type, row.scope_id) == ("topic", topic_id)
    finally:
        await engine.dispose()
