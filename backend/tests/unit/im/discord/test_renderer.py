from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from cubeplex.im.discord.renderer import DiscordOpDispatcher
from cubeplex.im.types import RenderState


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
        from cubeplex.im.card_model import PendingInput

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


class TestDiscordSecondAskAfterResolve:
    @pytest.mark.asyncio
    async def test_empty_create_after_resolve_delivers_the_next_prompt(self) -> None:
        """An empty follow-up create must not be sent; the platform rejects it."""
        from cubeplex.im.outbound import fold_event

        sent: list[str] = []

        class _RejectEmpty:
            async def send_message(self, text: str) -> str | None:
                if not text:
                    return None
                sent.append(text)
                return f"msg_{len(sent)}"

            async def edit_message(self, msg_id: str, text: str) -> bool:
                del msg_id, text
                return True

        state = RenderState(bot_name="test", run_id="r1", inbound_message_id="100")
        dispatcher = DiscordOpDispatcher(connector=_RejectEmpty(), state=state)

        async def dispatch(event: dict[str, object]) -> None:
            op = fold_event(event, state, now=1.0)
            if op is None:
                return
            if op.kind == "card_create":
                await dispatcher.dispatch_create(state)
            elif op.kind == "patch_card":
                await dispatcher.dispatch_patch(state)

        await dispatch({"type": "text_delta", "data": {"content": "intro"}})
        await dispatch(
            {
                "type": "ask_user_request",
                "data": {
                    "question_id": "q1",
                    "questions": [
                        {
                            "key": "repo",
                            "prompt": "First?",
                            "options": [{"label": "Other", "value": "other", "allow_input": True}],
                        }
                    ],
                },
            }
        )
        await dispatch(
            {
                "type": "ask_user_resolved",
                "data": {"question_id": "q1", "answers": {"repo": "https://example.com"}},
            }
        )
        await dispatch(
            {
                "type": "ask_user_request",
                "data": {
                    "question_id": "q2",
                    "questions": [
                        {
                            "key": "branch",
                            "prompt": "Second?",
                            "options": [{"label": "Other", "value": "other", "allow_input": True}],
                        }
                    ],
                },
            }
        )
        assert any(text.startswith("Second?") for text in sent)
        assert "" not in sent


class TestDiscordAllowInputNotice:
    @pytest.mark.asyncio
    async def test_second_custom_choice_in_the_same_run_is_sent(self) -> None:
        from cubeplex.im.card_model import AskFormField, AskFormOption, PendingInput

        d, state, conn = _make_dispatcher()

        def ask(question_id: str, prompt: str) -> None:
            state.card_state.pending_input = PendingInput(
                kind="ask_user",
                run_id="r1",
                question=prompt,
                choices=[],
                fields=[
                    AskFormField(
                        key="repo",
                        prompt=prompt,
                        kind="single_select",
                        options=[AskFormOption(label="Other", value="repo_url", allow_input=True)],
                    )
                ],
                question_id=question_id,
                answer_key="repo",
            )

        ask("q1", "First?\n\n_(此问含自定义输入，请在 CubePlex 网页端继续。)_")
        await d.dispatch_patch(state)
        first = state.card_state.pending_input
        assert first is not None
        first.resolved_choice = "done"
        await d.dispatch_patch(state)
        ask("q2", "Second?\n\n_(此问含自定义输入，请在 CubePlex 网页端继续。)_")
        await d.dispatch_patch(state)
        assert any(text.startswith("Second?") for text in conn.sent)

    @pytest.mark.asyncio
    async def test_custom_choice_sends_web_notice_not_buttons(self) -> None:
        from cubeplex.im.card_model import AskFormField, AskFormOption, PendingInput

        d, state, conn = _make_dispatcher()
        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id="r1",
            question="Which repository?\n\n_(此问含自定义输入，请在 CubePlex 网页端继续。)_",
            choices=[],
            fields=[
                AskFormField(
                    key="repo",
                    prompt="Which repository?",
                    kind="single_select",
                    options=[AskFormOption(label="Other", value="repo_url", allow_input=True)],
                )
            ],
            question_id="q1",
            answer_key="repo",
        )
        await d.dispatch_patch(state)
        assert any("网页端" in text for text in conn.sent)


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
