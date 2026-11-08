"""E2E test verifying topic context resolution and message context injection."""

from __future__ import annotations

import pytest
from cubeloop.providers.base import TextContent, UserMessage
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from cubeplex.api.routes.v1.conversations import _resolve_topic_run_context
from cubeplex.auth.context import RequestContext
from cubeplex.db.engine import _build_database_url
from cubeplex.middleware.topic_context import TopicContextMiddleware
from cubeplex.models.conversation import Conversation
from cubeplex.models.membership import Role
from cubeplex.models.topic import Topic
from cubeplex.models.user import User

pytestmark = pytest.mark.e2e

FourLayerFixture = tuple[
    tuple[AsyncClient, str, str],
    tuple[AsyncClient, str, str],
]


class TestConversationTopicContextE2E:
    @pytest.mark.anyio
    async def test_resolve_topic_run_context_with_topic_and_non_topic(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        (admin_c, ws_id, _), (_, _, member_uid) = four_layer_admin_and_member

        # 1. Create a topic conversation
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": "Backend Optimization & Refactor",
                "member_user_ids": [member_uid],
            },
        )
        assert resp.status_code == 201, resp.text
        topic_data = resp.json()
        topic_conv_id = topic_data["conversation"]["id"]
        topic_id = topic_data["topic"]["id"]

        # 2. Create a solo non-topic conversation
        resp_solo = await admin_c.post(
            f"/api/v1/ws/{ws_id}/conversations",
            params={"title": "Personal Scratchpad"},
        )
        assert resp_solo.status_code == 201, resp_solo.text
        solo_conv_id = resp_solo.json()["id"]

        engine = create_async_engine(_build_database_url(), poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with maker() as session:
                topic_conv = await session.get(Conversation, topic_conv_id)
                assert topic_conv is not None
                solo_conv = await session.get(Conversation, solo_conv_id)
                assert solo_conv is not None
                user_row = await session.get(User, topic_conv.creator_user_id)
                assert user_row is not None
                org_id = topic_conv.org_id
        finally:
            await engine.dispose()

        ctx = RequestContext(
            user=user_row,
            org_id=org_id,
            workspace_id=ws_id,
            role=Role.ADMIN,
        )

        # Check topic conversation resolution
        (
            res_topic_id,
            res_is_group,
            res_sender,
            res_sbx_mode,
            res_creator_id,
            res_topic_title,
        ) = await _resolve_topic_run_context(topic_conv, ctx)

        assert res_topic_id == topic_id
        assert res_topic_title == "Backend Optimization & Refactor"

        # Check non-topic conversation resolution
        (
            solo_topic_id,
            solo_is_group,
            solo_sender,
            solo_sbx_mode,
            solo_creator_id,
            solo_topic_title,
        ) = await _resolve_topic_run_context(solo_conv, ctx)

        assert solo_topic_id is None
        assert solo_topic_title is None

    @pytest.mark.anyio
    async def test_topic_turn_metadata_and_middleware_replay(
        self, four_layer_admin_and_member: FourLayerFixture
    ) -> None:
        """Verify topic context in stored metadata and deterministic replay through middleware."""
        (admin_c, ws_id, _), (_, _, member_uid) = four_layer_admin_and_member

        original_title = "API Modernization"
        resp = await admin_c.post(
            f"/api/v1/ws/{ws_id}/topics",
            json={
                "title": original_title,
                "member_user_ids": [member_uid],
            },
        )
        assert resp.status_code == 201, resp.text
        topic_data = resp.json()
        conv_id = topic_data["conversation"]["id"]
        topic_id = topic_data["topic"]["id"]

        engine = create_async_engine(_build_database_url(), poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with maker() as session:
                conv = await session.get(Conversation, conv_id)
                assert conv is not None
                user = await session.get(User, conv.creator_user_id)
                assert user is not None
                org_id = conv.org_id
        finally:
            await engine.dispose()

        ctx = RequestContext(
            user=user,
            org_id=org_id,
            workspace_id=ws_id,
            role=Role.ADMIN,
        )

        # 1. Resolve topic context on the conversation
        (
            t_id,
            _,
            _,
            _,
            _,
            t_title,
        ) = await _resolve_topic_run_context(conv, ctx)
        assert t_id == topic_id
        assert t_title == original_title

        # 2. Verify stored UserMessage: raw content MUST NOT have <conversation_context> baked in
        raw_prompt = "Migrate endpoints to v2"
        stored_user_msg = UserMessage(
            id="msg-turn-1",
            content=[TextContent(text=raw_prompt)],
            metadata={
                "topic_context": {
                    "topic_id": t_id,
                    "topic_title": t_title,
                }
            },
        )
        assert "<conversation_context" not in stored_user_msg.content[0].text  # type: ignore[union-attr]

        # 3. TopicContextMiddleware injects tag dynamically into message content
        mw = TopicContextMiddleware()
        transformed = await mw.transform_context([stored_user_msg], ctx=None)  # type: ignore[arg-type]
        expected_tag = f'<conversation_context topic_id="{t_id}" topic_title="{original_title}" />'
        assert transformed[0].content[0].text == f"{expected_tag}\n\n{raw_prompt}"  # type: ignore[union-attr]
        # Preserves message identity
        assert transformed[0].id == "msg-turn-1"

        # 4. Simulate topic rename in DB: change title to 'API Modernization V2'
        async with maker() as session:
            topic_obj = await session.get(Topic, topic_id)
            assert topic_obj is not None
            topic_obj.title = "API Modernization V2"
            session.add(topic_obj)
            await session.commit()

        # 5. Replay from stored_user_msg: historical replay reads the frozen metadata,
        # ensuring the rendered tag keeps the original title (preserving prompt-cache hits)
        replay_transformed = await mw.transform_context([stored_user_msg], ctx=None)  # type: ignore[arg-type]
        assert (
            replay_transformed[0].content[0].text
            == f'<conversation_context topic_id="{t_id}" topic_title="{original_title}" />\n\n{raw_prompt}'
        )
        assert replay_transformed[0].content[0].text == transformed[0].content[0].text  # type: ignore[union-attr]
