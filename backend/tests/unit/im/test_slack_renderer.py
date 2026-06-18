"""Test SlackOpDispatcher logic (unit, no real Slack API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubebox.im.slack.renderer import SlackOpDispatcher
from cubebox.im.types import RenderState


def _make_state(run_id: str = "run-1") -> RenderState:
    state = RenderState(bot_name="testbot", run_id=run_id)
    state.inbound_message_id = "1234.5678"
    return state


def _make_dispatcher(
    state: RenderState | None = None,
) -> tuple[SlackOpDispatcher, MagicMock]:
    s = state or _make_state()
    connector = MagicMock()
    connector.send_message = AsyncMock(return_value="msg-ts-1")
    connector.edit_message = AsyncMock(return_value=True)
    connector.add_reaction = AsyncMock()
    connector.remove_reaction = AsyncMock()
    connector.send_message_with_blocks = AsyncMock(return_value="btn-ts-1")
    connector.update_message_with_blocks = AsyncMock(return_value=True)
    dispatcher = SlackOpDispatcher(connector=connector, state=s)
    return dispatcher, connector


@pytest.mark.asyncio
async def test_dispatch_create() -> None:
    state = _make_state()
    state.card_state.streaming_content = "Hello world"
    d, conn = _make_dispatcher(state)
    ok = await d.dispatch_create(state)
    assert ok is True
    assert state.bot_message_id is not None
    conn.send_message.assert_awaited_once()
    conn.add_reaction.assert_awaited_once_with("1234.5678", "hourglass_flowing_sand")


@pytest.mark.asyncio
async def test_dispatch_stream_edits() -> None:
    state = _make_state()
    state.card_state.streaming_content = "Hello"
    d, conn = _make_dispatcher(state)
    await d.dispatch_create(state)
    state.card_state.streaming_content = "Hello world extended"
    ok = await d.dispatch_stream(state, "Hello world extended")
    assert ok is True
    conn.edit_message.assert_awaited()


@pytest.mark.asyncio
async def test_dispatch_finalize() -> None:
    state = _make_state()
    state.card_state.streaming_content = "Final answer"
    d, conn = _make_dispatcher(state)
    await d.dispatch_create(state)
    ok = await d.dispatch_finalize(state)
    assert ok is True
    conn.remove_reaction.assert_awaited()
    conn.add_reaction.assert_any_await("1234.5678", "white_check_mark")


@pytest.mark.asyncio
async def test_dispatch_finalize_with_error() -> None:
    state = _make_state()
    state.card_state.streaming_content = "Partial"
    state.card_state.error = "something broke"
    d, conn = _make_dispatcher(state)
    await d.dispatch_create(state)
    ok = await d.dispatch_finalize(state)
    assert ok is True
    conn.add_reaction.assert_any_await("1234.5678", "x")
