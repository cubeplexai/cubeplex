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
