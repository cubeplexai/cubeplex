from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from cubebox.im.discord.renderer import DiscordOpDispatcher
from cubebox.im.types import RenderState


@dataclass
class FakeConnector:
    sent: list[str] = field(default_factory=list)
    edited: list[tuple[str, str]] = field(default_factory=list)
    reactions_added: list[tuple[str, str]] = field(default_factory=list)
    reactions_removed: list[tuple[str, str]] = field(default_factory=list)

    async def send_message(self, text: str) -> str:
        self.sent.append(text)
        return f"msg_{len(self.sent)}"

    async def edit_message(self, msg_id: str, text: str) -> bool:
        self.edited.append((msg_id, text))
        return True

    async def add_reaction(self, msg_id: str, emoji: str) -> bool:
        self.reactions_added.append((msg_id, emoji))
        return True

    async def remove_reaction(self, msg_id: str, emoji: str) -> bool:
        self.reactions_removed.append((msg_id, emoji))
        return True

    async def _send_emergency_text(self, text: str) -> str | None:
        self.sent.append(text)
        return f"msg_{len(self.sent)}"


def _make_dispatcher() -> tuple[DiscordOpDispatcher, RenderState, FakeConnector]:
    state = RenderState(
        bot_name="test",
        run_id="r1",
        inbound_message_id="100",
        stream_interval=1.2,
        patch_interval=0.3,
    )
    connector = FakeConnector()
    dispatcher = DiscordOpDispatcher(connector=connector, state=state)
    return dispatcher, state, connector


class TestDiscordDispatchCreate:
    @pytest.mark.asyncio
    async def test_sends_initial_message(self) -> None:
        d, state, conn = _make_dispatcher()
        state.card_state.streaming_content = "Hello"
        result = await d.dispatch_create(state)
        assert result is True
        assert len(conn.sent) == 1
        assert conn.sent[0] == "Hello"
        assert state.bot_message_id == "msg_1"
        assert state.card_id == "msg_1"

    @pytest.mark.asyncio
    async def test_create_then_stream_edits(self) -> None:
        """After dispatch_create, dispatch_stream should edit not send."""
        d, state, conn = _make_dispatcher()
        state.card_state.streaming_content = "Hello"
        await d.dispatch_create(state)
        state.card_state.streaming_content = "Hello world"
        result = await d.dispatch_stream(state, "Hello world")
        assert result is True
        assert len(conn.sent) == 1
        assert len(conn.edited) == 1
        assert conn.edited[0] == ("msg_1", "Hello world")


class TestDiscordDispatchStream:
    @pytest.mark.asyncio
    async def test_edits_current_message(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        state.card_state.streaming_content = "Hello world"
        result = await d.dispatch_stream(state, "Hello world")
        assert result is True
        assert len(conn.edited) == 1
        assert conn.edited[0] == ("msg_1", "Hello world")

    @pytest.mark.asyncio
    async def test_split_at_2000_chars(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        long_text = "x" * 2500
        state.card_state.streaming_content = long_text
        result = await d.dispatch_stream(state, long_text)
        assert result is True
        assert d.sent_char_offset > 0


class TestDiscordDispatchPatchResumeNewMessage:
    @pytest.mark.asyncio
    async def test_resolved_pending_resets_card_state(self) -> None:
        """After AskUser is answered, follow-up reply should be a new message."""
        from cubebox.im.card_model import PendingInput

        d, state, conn = _make_dispatcher()
        state.card_id = "msg_1"
        state.bot_message_id = "msg_1"
        state.card_state.streaming_content = "Here is my question"
        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id="r1",
            question="Pick one",
            choices=[("A", "a", "primary")],
            resolved_choice="answered",
        )
        await d.dispatch_patch(state)
        assert state.card_id is None
        assert state.bot_message_id is None
        # Follow-up content should create a new message
        state.card_state.streaming_content += " — follow-up"
        await d.dispatch_create(state)
        assert len(conn.sent) == 1
        assert "follow-up" in conn.sent[0]
        assert state.card_id == "msg_1"


class TestDiscordDispatchFinalize:
    @pytest.mark.asyncio
    async def test_finalize_edits_final_content(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        state.card_state.streaming_content = "Final answer"
        result = await d.dispatch_finalize(state)
        assert result is True
        assert conn.reactions_removed  # ⏳ removed

    @pytest.mark.asyncio
    async def test_finalize_with_error(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        state.card_state.error = "something broke"
        result = await d.dispatch_finalize(state)
        assert result is True
