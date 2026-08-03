"""Test SlackOpDispatcher logic (unit, no real Slack API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from cubeplex.im.slack.renderer import SlackOpDispatcher
from cubeplex.im.types import RenderState


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
    # Hourglass is owned by on_processing_start, not first content create.
    conn.add_reaction.assert_not_awaited()


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
async def test_dispatch_stream_skips_empty_segment() -> None:
    """No new chars since last offset → do not chat.update with empty blocks."""
    state = _make_state()
    state.card_state.streaming_content = "Hello"
    d, conn = _make_dispatcher(state)
    await d.dispatch_create(state)
    conn.edit_message.reset_mock()
    # Offset already at end of content (e.g. after a segment split).
    d.sent_char_offset = len(state.card_state.streaming_content)
    ok = await d.dispatch_stream(state, "Hello")
    assert ok is True
    conn.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_hitl_streams_new_message() -> None:
    """After ask_user is answered, stream post_hitl_content as a new message.

    Regression: offset was set to len(streaming_content) while only reading
    streaming_content, so every post-HITL edit was empty (invalid_blocks).
    """
    from cubeplex.im.card_model import PendingInput

    state = _make_state()
    state.card_state.streaming_content = "Pick one:"
    d, conn = _make_dispatcher(state)
    await d.dispatch_create(state)

    state.card_state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run-1",
        question="Pick one?",
        choices=[("A", "a", "default")],
        question_id="qid",
        answer_key="k",
        resolved_choice="A",
    )
    state.card_state.hitl_resolved = True
    await d.dispatch_patch(state)
    assert state.bot_message_id is None
    assert d.sent_char_offset == 0

    conn.send_message.reset_mock()
    conn.edit_message.reset_mock()
    state.card_state.post_hitl_content = "那"
    ok = await d.dispatch_stream(state, "那")
    assert ok is True
    conn.send_message.assert_awaited()
    assert "那" in conn.send_message.await_args.args[0]

    state.card_state.post_hitl_content = "那就玩真心话大冒险吧"
    ok = await d.dispatch_stream(state, "那就玩真心话大冒险吧")
    assert ok is True
    conn.edit_message.assert_awaited()
    assert "那就玩真心话大冒险吧" in conn.edit_message.await_args.args[1]


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
