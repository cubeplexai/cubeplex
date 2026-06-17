"""E2E tests for Topics API — lifecycle and access control."""

import httpx
import pytest

pytestmark = pytest.mark.e2e


class TestTopicConversationAccess:
    """Topic conversations are visible to all participants."""

    @pytest.mark.anyio
    async def test_topic_conversation_visible_to_member(
        self,
        four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
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
        assert conv_resp.status_code == 200

    @pytest.mark.anyio
    async def test_non_participant_cannot_see_topic_conversation(
        self,
        four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
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
        assert conv_resp.status_code == 404

    @pytest.mark.anyio
    async def test_personal_conversation_still_private(
        self,
        four_layer_admin_and_member: tuple[
            tuple[httpx.AsyncClient, str, str],
            tuple[httpx.AsyncClient, str, str],
        ],
    ) -> None:
        (admin_c, ws_id, _), (member_c, _, _member_uid) = four_layer_admin_and_member

        # Admin creates a personal conversation (no topic)
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Admin Private"},
        )
        assert resp.status_code == 201
        conv_id = resp.json()["id"]

        # Member cannot see it
        conv_resp = await member_c.get(f"/api/v1/ws/{ws_id}/conversations/{conv_id}")
        assert conv_resp.status_code == 404
