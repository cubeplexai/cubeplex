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
async def test_finalize_empty_post_hitl_still_adds_check() -> None:
    """HITL resolved then done with no further text must still stamp ✅."""
    from cubeplex.im.card_model import PendingInput

    state = _make_state()
    state.card_state.streaming_content = "Pick one:"
    d, conn = _make_dispatcher(state)
    await d.dispatch_create(state)
    state.card_state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run-1",
        question="Pick?",
        choices=[("A", "a", "default")],
        question_id="qid",
        answer_key="k",
        resolved_choice="A",
    )
    state.card_state.hitl_resolved = True
    await d.dispatch_patch(state)
    conn.add_reaction.reset_mock()
    conn.remove_reaction.reset_mock()
    # No post_hitl_content — empty active stream.
    ok = await d.dispatch_finalize(state)
    assert ok is True
    conn.remove_reaction.assert_awaited()
    conn.add_reaction.assert_any_await("1234.5678", "white_check_mark")


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
async def test_hitl_reset_only_once_on_later_patches() -> None:
    """Later tool/artifact patches must not clear the post-HITL message again."""
    from cubeplex.im.card_model import PendingInput

    state = _make_state()
    state.card_state.streaming_content = "Pick one:"
    d, conn = _make_dispatcher(state)
    await d.dispatch_create(state)

    state.card_state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run-1",
        question="Pick?",
        choices=[("A", "a", "default")],
        question_id="qid-1",
        answer_key="k",
        resolved_choice="A",
    )
    state.card_state.hitl_resolved = True
    await d.dispatch_patch(state)
    assert state.bot_message_id is None

    # First post-HITL stream creates a message.
    state.card_state.post_hitl_content = "Answer"
    await d.dispatch_stream(state, "Answer")
    assert state.bot_message_id == "msg-ts-1"
    saved_id = state.bot_message_id

    # A later patch (tool result) still has resolved_choice set — must keep id.
    await d.dispatch_patch(state)
    assert state.bot_message_id == saved_id
    assert d.sent_char_offset == 0 or state.bot_message_id is not None


@pytest.mark.asyncio
async def test_stream_split_keeps_offset_at_segment_start() -> None:
    """After splitting, later deltas must edit the full current segment.

    Regression: advancing offset past the remainder made the next chat.update
    replace the new message with only the newly arrived suffix.
    """
    state = _make_state()
    # Just over the 2800 split threshold so stream seals msg1 and posts msg2.
    first = "A" * 2900
    state.card_state.streaming_content = first
    d, conn = _make_dispatcher(state)
    conn.send_message = AsyncMock(side_effect=["ts-1", "ts-2"])
    await d.dispatch_create(state)
    # Force a single-message create if create already split; reset for stream.
    state.bot_message_id = "ts-1"
    state.card_id = "ts-1"
    d.sent_char_offset = 0
    conn.send_message.reset_mock()
    conn.send_message.side_effect = ["ts-2"]
    conn.edit_message.reset_mock()

    ok = await d.dispatch_stream(state, first)
    assert ok is True
    # First message finalized; remainder on a new message; offset stays at
    # the start of the second segment (not past the remainder).
    assert conn.edit_message.await_count >= 1
    assert state.bot_message_id == "ts-2"
    offset_after_split = d.sent_char_offset
    assert offset_after_split > 0
    assert offset_after_split < len(first)
    remainder_on_slack = first[offset_after_split:]
    assert remainder_on_slack
    # New message holds the full remainder, not a truncated delta-only view.
    assert conn.send_message.await_args.args[0] == remainder_on_slack[:3000]

    # Grow the buffer — next edit must repaint full segment from offset.
    grown = first + "B" * 50
    state.card_state.streaming_content = grown
    conn.edit_message.reset_mock()
    ok = await d.dispatch_stream(state, grown)
    assert ok is True
    painted = conn.edit_message.await_args.args[1]
    assert painted.startswith(remainder_on_slack[:100])
    assert painted.endswith("B" * 50)
    assert d.sent_char_offset == offset_after_split


@pytest.mark.asyncio
async def test_finalize_long_split_edits_current_segment_first() -> None:
    """After a stream split, finalize must edit the open segment, not re-post it."""
    state = _make_state()
    # Simulate: first segment sealed at offset 2000; current msg has the rest.
    d, conn = _make_dispatcher(state)
    state.bot_message_id = "ts-seg2"
    state.card_id = "ts-seg2"
    d.sent_char_offset = 2000
    # remaining = 3500 chars → first chunk edits ts-seg2, second is a new post.
    state.card_state.streaming_content = "X" * 2000 + "Y" * 3500
    conn.edit_message.reset_mock()
    conn.send_message.reset_mock()
    conn.send_message.return_value = "ts-seg3"

    ok = await d.dispatch_finalize(state)
    assert ok is True
    # First 3000 of remaining → edit current segment message.
    conn.edit_message.assert_awaited()
    assert conn.edit_message.await_args.args[0] == "ts-seg2"
    assert conn.edit_message.await_args.args[1] == "Y" * 3000
    # Remainder 500 → new message (not a duplicate of the full remaining).
    conn.send_message.assert_awaited()
    assert conn.send_message.await_args.args[0] == "Y" * 500


@pytest.mark.asyncio
async def test_empty_post_hitl_create_skips_placeholder() -> None:
    """Do not post '...' when post-HITL buffer is still empty."""
    state = _make_state()
    state.card_state.hitl_resolved = True
    state.card_state.post_hitl_content = ""
    d, conn = _make_dispatcher(state)
    ok = await d.dispatch_create(state)
    assert ok is False
    conn.send_message.assert_not_awaited()
    assert state.bot_message_id is None


@pytest.mark.asyncio
async def test_empty_post_hitl_create_still_sends_pending_buttons() -> None:
    """Second HITL with no interim text must still get Slack buttons.

    After the first HITL resolution clears card_id, fold returns card_create
    for the next ask_user/sandbox_confirm. Create must not skip the pending
    path entirely when post_hitl is empty.
    """
    from cubeplex.im.card_model import PendingInput

    state = _make_state()
    state.card_state.hitl_resolved = True
    state.card_state.post_hitl_content = ""
    state.card_id = None
    state.bot_message_id = None
    state.card_state.pending_input = PendingInput(
        kind="sandbox_confirm",
        run_id="run-1",
        question="Allow?",
        choices=[("Yes", "approve", "primary"), ("No", "deny", "danger")],
        question_id="qid-2",
        answer_key="",
    )
    d, conn = _make_dispatcher(state)
    ok = await d.dispatch_create(state)
    assert ok is False
    conn.send_message.assert_not_awaited()
    conn.send_message_with_blocks.assert_awaited()
    assert state.bot_message_id is None


@pytest.mark.asyncio
async def test_finalize_clears_empty_placeholder() -> None:
    """If a placeholder bot message exists with no final text, replace it."""
    state = _make_state()
    state.card_state.hitl_resolved = True
    state.card_state.post_hitl_content = ""
    d, conn = _make_dispatcher(state)
    state.bot_message_id = "ts-dots"
    ok = await d.dispatch_finalize(state)
    assert ok is True
    conn.edit_message.assert_awaited_with("ts-dots", "✓")
    conn.add_reaction.assert_any_await("1234.5678", "white_check_mark")


@pytest.mark.asyncio
async def test_second_hitl_starts_at_post_hitl_boundary() -> None:
    """Second distinct HITL must not repost the first post-HITL continuation."""
    from cubeplex.im.card_model import PendingInput

    state = _make_state()
    state.card_state.streaming_content = "Pre-HITL answer"
    d, conn = _make_dispatcher(state)
    await d.dispatch_create(state)

    # First HITL resolution.
    state.card_state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run-1",
        question="Pick?",
        choices=[("A", "a", "default")],
        question_id="qid-1",
        answer_key="k",
        resolved_choice="A",
    )
    state.card_state.hitl_resolved = True
    await d.dispatch_patch(state)
    assert d.sent_char_offset == 0

    first_continuation = "First continuation after ask_user"
    state.card_state.post_hitl_content = first_continuation
    conn.send_message.reset_mock()
    conn.send_message.return_value = "ts-cont-1"
    await d.dispatch_stream(state, first_continuation)
    assert state.bot_message_id == "ts-cont-1"
    assert first_continuation in conn.send_message.await_args.args[0]

    # Second distinct HITL (sandbox confirm) resolves — pin offset to buffer end.
    state.card_state.pending_input = PendingInput(
        kind="sandbox_confirm",
        run_id="run-1",
        question="Allow?",
        choices=[("Yes", "yes", "primary"), ("No", "no", "danger")],
        question_id="qid-2",
        answer_key="k",
        resolved_choice="Yes",
    )
    await d.dispatch_patch(state)
    assert state.bot_message_id is None
    assert d.sent_char_offset == len(first_continuation)

    second_only = "Second continuation only"
    state.card_state.post_hitl_content = first_continuation + second_only
    conn.send_message.reset_mock()
    conn.send_message.return_value = "ts-cont-2"
    ok = await d.dispatch_stream(state, state.card_state.post_hitl_content)
    assert ok is True
    posted = conn.send_message.await_args.args[0]
    assert posted == second_only
    assert first_continuation not in posted


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
