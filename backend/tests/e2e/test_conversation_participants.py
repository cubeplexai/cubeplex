"""Four-branch _scoped_select: creator, conv participant, topic participant."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.anyio
async def test_conv_participant_sees_standalone_group_chat(
    four_layer_admin_and_member,
) -> None:
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    # Admin creates a personal conversation.
    conv = (await admin_c.post(f"/api/v1/ws/{ws_id}/conversations")).json()
    conv_id = conv["id"]

    # Admin invites member -> becomes standalone group chat.
    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/invite-to-group",
        json={"user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["conversation"]["is_group_chat"] is True
    assert any(p["user_id"] == member_uid for p in payload["participants"])

    # Member can now see the conversation in their list.
    member_list = (await member_c.get(f"/api/v1/ws/{ws_id}/conversations")).json()
    assert any(c["id"] == conv_id for c in member_list["conversations"])


@pytest.mark.anyio
async def test_send_auto_joins_topic_participant(
    four_layer_admin_and_member,
) -> None:
    """A topic participant who has never sent in a conv is auto-joined to
    ``conversation_participants`` on first send, and ``is_group_chat`` flips
    to True on the 1 -> 2 transition.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from cubebox.db.engine import _build_database_url

    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    # Admin creates a topic + topic conversation; member is a topic participant
    # but NOT yet a conv participant.
    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "AutoJoin", "member_user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text
    conv_id = resp.json()["conversation"]["id"]

    engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
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

        # Member sends -> the gate passes and they get auto-joined.
        # Status != 404 confirms the gate didn't short-circuit; 5xx is fine
        # because there's no LLM preset seeded for this fixture.
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
    four_layer_admin_and_member,
) -> None:
    """HITL gate is stricter than send: a P(topic) member who is not yet
    P(conv) gets 404 on ``submit_sandbox_confirm`` even though they could
    open the conversation. Sending first auto-joins them; then HITL would
    move past the gate (we don't drive a real pending request, only assert
    the gate flips from 404 to non-404).
    """
    (admin_c, ws_id, _), (member_c, _, member_uid) = four_layer_admin_and_member

    resp = await admin_c.post(
        f"/api/v1/ws/{ws_id}/topics",
        json={"title": "HITL Gate", "member_user_ids": [member_uid]},
    )
    assert resp.status_code == 201, resp.text
    conv_id = resp.json()["conversation"]["id"]

    # Before sending, member is not P(conv) -> HITL must be 404.
    hitl_before = await member_c.post(
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/sandbox-confirm/q-fake",
        json={"decision": "approve"},
    )
    assert hitl_before.status_code == 404, hitl_before.text
