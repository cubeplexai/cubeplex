"""Web Chat has no updateActivity — stream buffers; finalize sends full text."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from cubeplex.im.teams.renderer import TeamsOpDispatcher
from cubeplex.im.types import RenderState


class _SendOnlyConnector:
    supports_message_edit = False

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.edits: list[tuple[str, str]] = []

    async def send_message(self, text: str) -> str:
        self.sent.append(text)
        return f"msg-{len(self.sent)}"

    async def edit_message(self, activity_id: str, text: str) -> bool:
        self.edits.append((activity_id, text))
        return False


@pytest.mark.asyncio
async def test_send_only_channel_finalize_posts_full_reply() -> None:
    conn = _SendOnlyConnector()
    state = RenderState(bot_name="bot", run_id="run-1")
    state.card_state.streaming_content = "你好呀～我是莉莉，见到你很开心。"
    dispatcher = TeamsOpDispatcher(connector=conn, state=state)

    assert await dispatcher.dispatch_create(SimpleNamespace()) is True
    assert conn.sent == []  # no partial first token
    assert await dispatcher.dispatch_stream(SimpleNamespace(), "x") is True
    assert conn.sent == []
    assert await dispatcher.dispatch_finalize(SimpleNamespace()) is True
    assert conn.sent == ["你好呀～我是莉莉，见到你很开心。"]
    assert conn.edits == []


@pytest.mark.asyncio
async def test_edit_channel_still_streams_via_edit() -> None:
    class _EditConnector:
        supports_message_edit = True

        def __init__(self) -> None:
            self.sent: list[str] = []
            self.edits: list[tuple[str, str]] = []

        async def send_message(self, text: str) -> str:
            self.sent.append(text)
            return "msg-1"

        async def edit_message(self, activity_id: str, text: str) -> bool:
            self.edits.append((activity_id, text))
            return True

    conn = _EditConnector()
    state = RenderState(bot_name="bot", run_id="run-2")
    state.card_state.streaming_content = "你好"
    dispatcher = TeamsOpDispatcher(connector=conn, state=state)
    assert await dispatcher.dispatch_create(SimpleNamespace()) is True
    assert conn.sent == ["你好"]
    state.card_state.streaming_content = "你好呀"
    assert await dispatcher.dispatch_stream(SimpleNamespace(), "呀") is True
    assert conn.edits == [("msg-1", "你好呀")]
