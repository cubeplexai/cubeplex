"""Unit tests for DingtalkOpDispatcher."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from cubeplex.im.dingtalk.renderer import DingtalkOpDispatcher
from cubeplex.im.types import RenderState


@pytest.fixture()
def state() -> RenderState:
    s = RenderState(bot_name="testbot", run_id="run_001", stream_interval=1.0)
    s.reply_to_id = "msg_inbound"
    s.inbound_message_id = "msg_inbound"
    return s


@pytest.fixture()
def connector() -> AsyncMock:
    mock = AsyncMock()
    mock.create_ai_card = AsyncMock(return_value=True)
    mock.streaming_update_card = AsyncMock(return_value=True)
    mock.update_card_actions = AsyncMock(return_value=True)
    mock.reply_markdown = AsyncMock(return_value="msg_reply")
    return mock


class TestDispatchCreate:
    @pytest.mark.anyio()
    async def test_creates_card(self, state: RenderState, connector: AsyncMock) -> None:
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            open_conversation_id="cid_123",
        )
        state.card_state.streaming_content = "Hello world"
        ok = await d.dispatch_create(state)
        assert ok is True
        connector.create_ai_card.assert_called_once()
        assert state.card_id is not None

    @pytest.mark.anyio()
    async def test_fallback_on_card_failure(self, state: RenderState, connector: AsyncMock) -> None:
        connector.create_ai_card = AsyncMock(return_value=False)
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            open_conversation_id="cid_123",
        )
        state.card_state.streaming_content = "Hello"
        ok = await d.dispatch_create(state)
        assert ok is True
        assert state.card_unavailable is True


class TestDispatchStream:
    @pytest.mark.anyio()
    async def test_streams_content(self, state: RenderState, connector: AsyncMock) -> None:
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            open_conversation_id="cid_123",
        )
        state.card_id = "track_001"
        state.card_state.streaming_content = "Hello streaming"
        ok = await d.dispatch_stream(state, "Hello streaming")
        assert ok is True
        connector.streaming_update_card.assert_called_once()
        call_kwargs = connector.streaming_update_card.call_args.kwargs
        assert call_kwargs["key"] == "msgContent"


class TestDispatchFinalize:
    @pytest.mark.anyio()
    async def test_finalizes_card(self, state: RenderState, connector: AsyncMock) -> None:
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            open_conversation_id="cid_123",
        )
        state.card_id = "track_001"
        state.card_state.streaming_content = "Done"
        ok = await d.dispatch_finalize(state)
        assert ok is True
        connector.streaming_update_card.assert_called()
        call_kwargs = connector.streaming_update_card.call_args.kwargs
        assert call_kwargs["is_final"] is True
        connector.update_card_actions.assert_called()
        finish_kwargs = connector.update_card_actions.call_args.kwargs
        assert finish_kwargs["card_data"]["flowStatus"] == "3"
        assert finish_kwargs["card_update_options"] == {"updateCardDataByKey": True}

    @pytest.mark.anyio()
    async def test_finalize_includes_post_hitl_content(
        self, state: RenderState, connector: AsyncMock
    ) -> None:
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            open_conversation_id="cid_123",
        )
        state.card_id = "track_001"
        state.card_state.streaming_content = "Pre-HITL answer"
        state.card_state.hitl_resolved = True
        state.card_state.post_hitl_content = "Post-HITL answer"
        ok = await d.dispatch_finalize(state)
        assert ok is True
        call_kwargs = connector.streaming_update_card.call_args.kwargs
        content = call_kwargs["content"]
        assert "Pre-HITL answer" in content
        assert "Post-HITL answer" in content

    @pytest.mark.anyio()
    async def test_stream_includes_post_hitl_content(
        self, state: RenderState, connector: AsyncMock
    ) -> None:
        d = DingtalkOpDispatcher(
            connector=connector,
            state=state,
            open_conversation_id="cid_123",
        )
        state.card_id = "track_001"
        state.card_state.streaming_content = "Pre-HITL answer"
        state.card_state.hitl_resolved = True
        state.card_state.post_hitl_content = "Post-HITL answer"
        ok = await d.dispatch_stream(state, "Post-HITL answer")
        assert ok is True
        call_kwargs = connector.streaming_update_card.call_args.kwargs
        content = call_kwargs["content"]
        assert "Pre-HITL answer" in content
        assert "Post-HITL answer" in content
