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
        state.card_state.hitl_resolved = True
        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id="r1",
            question="Pick one",
            choices=[("A", "a", "primary")],
            question_id="q1",
            resolved_choice="answered",
        )
        await d.dispatch_patch(state)
        assert state.card_id is None
        assert state.bot_message_id is None
        # Follow-up content lands in post_hitl_content via fold_event
        state.card_state.post_hitl_content = "Here is the follow-up"
        await d.dispatch_create(state)
        assert len(conn.sent) == 1
        assert conn.sent[0] == "Here is the follow-up"
        assert state.card_id == "msg_1"


class TestDiscordPostHitlFlow:
    @pytest.mark.asyncio
    async def test_fold_and_dispatch_post_hitl_streams_and_finalizes(self) -> None:
        """Full fold_event HITL flow streams post_hitl_content as a new message."""
        from cubeplex.im.outbound import fold_event

        d, state, conn = _make_dispatcher()
        now = 1.0

        async def step(event: dict[str, object]) -> None:
            nonlocal now
            now += 2.0
            op = fold_event(event, state, now=now)
            if op is None:
                return
            if op.kind == "card_create":
                await d.dispatch_create(state)
            elif op.kind == "stream_text":
                await d.dispatch_stream(state, op.text)
            elif op.kind == "patch_card":
                await d.dispatch_patch(state)
            elif op.kind == "finalize":
                await d.dispatch_finalize(state)

        await step({"type": "text_delta", "data": {"content": "Pre-HITL intro"}})
        assert conn.sent == ["Pre-HITL intro"]
        await step(
            {
                "type": "ask_user_request",
                "data": {
                    "question_id": "q1",
                    "questions": [
                        {
                            "key": "choice",
                            "prompt": "Pick?",
                            "options": [{"label": "A", "value": "a"}],
                        }
                    ],
                },
            }
        )
        await step(
            {
                "type": "ask_user_resolved",
                "data": {"question_id": "q1", "answers": {"choice": "a"}},
            }
        )
        assert state.bot_message_id is None
        assert d.sent_char_offset == 0

        sent_before_post = list(conn.sent)
        await step({"type": "text_delta", "data": {"content": "Post"}})
        assert conn.sent[-1] == "Post"
        assert len(conn.sent) == len(sent_before_post) + 1
        post_msg_id = state.bot_message_id
        assert post_msg_id is not None

        await step({"type": "text_delta", "data": {"content": " answer"}})
        assert conn.edited[-1] == (post_msg_id, "Post answer")

        await step({"type": "done", "data": {}})
        assert conn.edited[-1] == (post_msg_id, "Post answer")
        assert ("100", "⏳") in conn.reactions_removed

    @pytest.mark.asyncio
    async def test_hitl_reset_only_once_across_post_hitl_tool_patches(self) -> None:
        """Tool/artifact patches after HITL resolve must not clear bot_message_id again."""
        from cubeplex.im.card_model import PendingInput

        d, state, conn = _make_dispatcher()
        state.card_state.streaming_content = "Pre-HITL"
        await d.dispatch_create(state)

        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id="r1",
            question="Pick?",
            choices=[("A", "a", "primary")],
            question_id="q1",
            answer_key="k",
            resolved_choice="A",
        )
        state.card_state.hitl_resolved = True
        await d.dispatch_patch(state)
        assert state.bot_message_id is None

        state.card_state.post_hitl_content = "Follow-up"
        await d.dispatch_stream(state, "Follow-up")
        post_id = state.bot_message_id
        assert post_id == "msg_2"

        # Subsequent tool_call / tool_result patch still carries resolved_choice.
        await d.dispatch_patch(state)
        assert state.bot_message_id == post_id

        state.card_state.post_hitl_content = "Follow-up extended"
        await d.dispatch_stream(state, "Follow-up extended")
        assert conn.edited[-1] == (post_id, "Follow-up extended")

    @pytest.mark.asyncio
    async def test_empty_post_hitl_skips_placeholder_and_finalize_clears_reaction(
        self,
    ) -> None:
        """HITL resolved with no post-HITL text skips '...' and still removes ⏳."""
        from cubeplex.im.card_model import PendingInput

        d, state, conn = _make_dispatcher()
        state.card_state.streaming_content = "Pick one:"
        await d.dispatch_create(state)
        conn.sent.clear()

        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id="r1",
            question="Pick?",
            choices=[("A", "a", "primary")],
            question_id="q1",
            answer_key="k",
            resolved_choice="A",
        )
        state.card_state.hitl_resolved = True
        state.card_id = None
        state.bot_message_id = None
        # Resolved event when card_id was None lands on dispatch_create.
        assert await d.dispatch_create(state) is False
        assert conn.sent == []
        assert d._hitl_reset_qid == "ask_user:r1:q1"

        assert await d.dispatch_finalize(state) is True
        assert conn.sent == []
        assert conn.edited == []
        assert ("100", "⏳") in conn.reactions_removed

    @pytest.mark.asyncio
    async def test_second_hitl_starts_at_post_hitl_boundary(self) -> None:
        """Second HITL in the same run must not repost the first post-HITL reply."""
        from cubeplex.im.card_model import PendingInput

        d, state, conn = _make_dispatcher()
        state.card_state.streaming_content = "Intro"
        await d.dispatch_create(state)

        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id="r1",
            question="First?",
            choices=[("A", "a", "primary")],
            question_id="q1",
            answer_key="k",
            resolved_choice="A",
        )
        state.card_state.hitl_resolved = True
        await d.dispatch_patch(state)

        first_cont = "First post-HITL answer."
        state.card_state.post_hitl_content = first_cont
        await d.dispatch_stream(state, first_cont)
        assert conn.sent[-1] == first_cont

        state.card_state.pending_input = PendingInput(
            kind="sandbox_confirm",
            run_id="r1",
            question="Allow?",
            choices=[("Yes", "approve", "primary")],
            question_id="q2",
            resolved_choice="approve",
        )
        await d.dispatch_patch(state)
        assert state.bot_message_id is None
        assert d.sent_char_offset == len(first_cont)

        # If done arrives with no further text after second HITL, nothing new is sent.
        assert await d.dispatch_finalize(state) is True
        assert conn.sent[-1] == first_cont
        assert ("100", "⏳") in conn.reactions_removed

        # Reset reaction tracker and stream second continuation.
        second_only = "Second post-HITL answer."
        state.card_state.post_hitl_content = first_cont + second_only
        await d.dispatch_stream(state, state.card_state.post_hitl_content)
        assert conn.sent[-1] == second_only
        assert first_cont not in conn.sent[-1]

    @pytest.mark.asyncio
    async def test_finalize_long_split_edits_open_segment_and_clears_placeholder(
        self,
    ) -> None:
        """Finalize edits open segment first on >2000 remainder and clears '...'."""
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_seg2"
        state.card_id = "msg_seg2"
        d.sent_char_offset = 1500
        state.card_state.streaming_content = "A" * 1500 + "B" * 2300
        assert await d.dispatch_finalize(state) is True
        assert conn.edited == [("msg_seg2", "B" * 2000)]
        assert conn.sent == ["B" * 300]

        # Empty final content with an open placeholder replaces "..." with "✓".
        d2, state2, conn2 = _make_dispatcher()
        state2.bot_message_id = "msg_dots"
        assert await d2.dispatch_finalize(state2) is True
        assert conn2.edited == [("msg_dots", "✓")]
        assert ("100", "⏳") in conn2.reactions_removed

    @pytest.mark.asyncio
    async def test_stream_skips_empty_segment(self) -> None:
        d, state, conn = _make_dispatcher()
        state.bot_message_id = "msg_1"
        state.card_state.streaming_content = "Hello"
        d.sent_char_offset = len("Hello")
        assert await d.dispatch_stream(state, "Hello") is True
        assert conn.edited == []

    @pytest.mark.asyncio
    async def test_debounced_pre_hitl_flushes_before_prompt_and_error_fallbacks(
        self,
    ) -> None:
        """Debounced tokens before HITL are painted before prompt; finalize handles edit errors."""
        from cubeplex.im.outbound import fold_event

        d, state, conn = _make_dispatcher()
        # First delta creates msg_1; second delta within stream_interval (1.2s) is debounced.
        op1 = fold_event({"type": "text_delta", "data": {"content": "Hi"}}, state, now=1.0)
        assert op1 is not None and op1.kind == "card_create"
        await d.dispatch_create(state)
        assert (
            fold_event({"type": "text_delta", "data": {"content": " there"}}, state, now=1.1)
            is None
        )

        op_ask = fold_event(
            {
                "type": "ask_user_request",
                "data": {
                    "question_id": "q1",
                    "questions": [
                        {
                            "key": "k",
                            "prompt": "Custom?",
                            "options": [{"label": "Other", "value": "o", "allow_input": True}],
                        }
                    ],
                },
            },
            state,
            now=1.2,
        )
        assert op_ask is not None and op_ask.kind == "patch_card"
        await d.dispatch_patch(state)
        # Debounced " there" flushed to msg_1 before prompt notice was sent.
        assert conn.edited == [("msg_1", "Hi there")]
        assert any("Custom?" in m for m in conn.sent)

        # Create sealed_prefix split (>1900 chars) seals first segment.
        d_split, s_split, c_split = _make_dispatcher()
        s_split.card_state.streaming_content = "S" * 2100
        assert await d_split.dispatch_create(s_split) is True
        assert s_split.bot_message_id is None
        assert d_split.sent_char_offset > 0

        # Finalize edit failure paths (placeholder, short edit, oversize edit) never strand ⏳.
        class _FailEditConn(FakeConnector):
            async def edit_message(self, msg_id: str, text: str) -> bool:
                del msg_id, text
                raise RuntimeError("edit failed")

        for content in ("", "Short reply", "L" * 2200):
            s_err = RenderState(bot_name="test", run_id="r-err", inbound_message_id="100")
            s_err.bot_message_id = "msg_open"
            s_err.card_state.streaming_content = content
            c_err = _FailEditConn()
            d_err = DiscordOpDispatcher(connector=c_err, state=s_err)
            assert await d_err.dispatch_finalize(s_err) is True
            assert ("100", "⏳") in c_err.reactions_removed


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
