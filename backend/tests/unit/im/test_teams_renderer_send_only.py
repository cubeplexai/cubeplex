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
async def test_editable_second_ask_is_not_an_empty_message() -> None:
    """After resolve, create has no new text. Do not send ""; deliver the prompt."""
    from cubeplex.im.outbound import fold_event

    sent: list[str] = []

    class _RejectEmpty:
        supports_message_edit = True

        async def send_message(self, text: str) -> str | None:
            if not text:
                return None
            sent.append(text)
            return f"msg-{len(sent)}"

        async def edit_message(self, activity_id: str, text: str) -> bool:
            del activity_id, text
            return True

    state = RenderState(bot_name="bot", run_id="run-edit")
    dispatcher = TeamsOpDispatcher(connector=_RejectEmpty(), state=state)

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
    assert all(text for text in sent)


@pytest.mark.asyncio
async def test_send_only_create_delivers_each_custom_choice() -> None:
    """Web Chat never creates a card, so the ask must go out from create."""
    from cubeplex.im.card_model import AskFormField, AskFormOption, PendingInput

    conn = _SendOnlyConnector()
    state = RenderState(bot_name="bot", run_id="run-custom")
    dispatcher = TeamsOpDispatcher(connector=conn, state=state)

    def ask(question_id: str, prompt: str) -> None:
        state.card_state.pending_input = PendingInput(
            kind="ask_user",
            run_id="run-custom",
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
    assert await dispatcher.dispatch_create(SimpleNamespace()) is True
    ask("q2", "Second?\n\n_(此问含自定义输入，请在 CubePlex 网页端继续。)_")
    assert await dispatcher.dispatch_create(SimpleNamespace()) is True
    assert conn.sent[0].startswith("First?")
    assert conn.sent[1].startswith("Second?")
    assert "网页端" in conn.sent[0] and "网页端" in conn.sent[1]


@pytest.mark.asyncio
async def test_allow_input_question_sends_web_notice_not_card() -> None:
    from cubeplex.im.card_model import AskFormField, AskFormOption, PendingInput

    class _Connector:
        supports_message_edit = True

        def __init__(self) -> None:
            self.sent: list[str] = []
            self.cards: list[dict[str, object]] = []

        async def send_message(self, text: str) -> str:
            self.sent.append(text)
            return "msg-1"

        async def send_card(self, card: dict[str, object]) -> str:
            self.cards.append(card)
            return "card-1"

    conn = _Connector()
    state = RenderState(bot_name="bot", run_id="run-custom")
    notice = "Which repository?\n\n_(此问含自定义输入，请在 CubePlex 网页端继续。)_"
    state.card_state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run-custom",
        question=notice,
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
    dispatcher = TeamsOpDispatcher(connector=conn, state=state)
    assert await dispatcher.dispatch_patch(SimpleNamespace()) is True
    assert conn.sent == [notice]
    assert conn.cards == []


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


@pytest.mark.asyncio
async def test_edit_channel_post_hitl_streams_and_finalizes_new_message() -> None:
    from cubeplex.im.outbound import fold_event

    class _FullEditConnector:
        supports_message_edit = True

        def __init__(self) -> None:
            self.sent: list[str] = []
            self.edits: list[tuple[str, str]] = []
            self.cards: list[dict[str, object]] = []

        async def send_message(self, text: str) -> str:
            self.sent.append(text)
            return f"msg-{len(self.sent)}"

        async def edit_message(self, activity_id: str, text: str) -> bool:
            self.edits.append((activity_id, text))
            return True

        async def send_card(self, card: dict[str, object]) -> str:
            self.cards.append(card)
            return f"card-{len(self.cards)}"

    conn = _FullEditConnector()
    state = RenderState(bot_name="bot", run_id="run-hitl")
    dispatcher = TeamsOpDispatcher(connector=conn, state=state)
    now = 1.0

    async def step(event: dict[str, object]) -> None:
        nonlocal now
        now += 2.0
        op = fold_event(event, state, now=now)
        if op is None:
            return
        if op.kind == "card_create":
            await dispatcher.dispatch_create(state)
        elif op.kind == "stream_text":
            await dispatcher.dispatch_stream(state, op.text)
        elif op.kind == "patch_card":
            await dispatcher.dispatch_patch(state)
        elif op.kind == "finalize":
            await dispatcher.dispatch_finalize(state)

    await step({"type": "text_delta", "data": {"content": "Pre-HITL text"}})
    assert conn.sent == ["Pre-HITL text"]

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
    assert len(conn.cards) == 1

    await step(
        {
            "type": "ask_user_resolved",
            "data": {"question_id": "q1", "answers": {"choice": "a"}},
        }
    )
    assert state.bot_message_id is None
    assert dispatcher.sent_char_offset == 0

    await step({"type": "text_delta", "data": {"content": "Post"}})
    assert conn.sent == ["Pre-HITL text", "Post"]
    assert state.bot_message_id == "msg-2"

    await step({"type": "text_delta", "data": {"content": " answer"}})
    assert conn.edits[-1] == ("msg-2", "Post answer")

    await step({"type": "done", "data": {}})
    assert conn.edits[-1] == ("msg-2", "Post answer")


@pytest.mark.asyncio
async def test_edit_channel_hitl_reset_only_once_and_second_hitl_boundary() -> None:
    from cubeplex.im.card_model import PendingInput

    class _EditConn:
        supports_message_edit = True

        def __init__(self) -> None:
            self.sent: list[str] = []
            self.edits: list[tuple[str, str]] = []

        async def send_message(self, text: str) -> str:
            self.sent.append(text)
            return f"msg-{len(self.sent)}"

        async def edit_message(self, activity_id: str, text: str) -> bool:
            self.edits.append((activity_id, text))
            return True

    conn = _EditConn()
    state = RenderState(bot_name="bot", run_id="run-multi")
    state.card_state.streaming_content = "Pre-HITL"
    d = TeamsOpDispatcher(connector=conn, state=state)
    await d.dispatch_create(state)

    state.card_state.pending_input = PendingInput(
        kind="ask_user",
        run_id="run-multi",
        question="First?",
        choices=[("A", "a", "default")],
        question_id="q1",
        answer_key="k",
        resolved_choice="A",
    )
    state.card_state.hitl_resolved = True
    await d.dispatch_patch(state)
    assert state.bot_message_id is None

    first_cont = "First post-HITL reply."
    state.card_state.post_hitl_content = first_cont
    await d.dispatch_stream(state, first_cont)
    assert state.bot_message_id == "msg-2"

    # Subsequent tool/artifact patch must NOT clear bot_message_id again.
    await d.dispatch_patch(state)
    assert state.bot_message_id == "msg-2"

    # Second distinct HITL pins offset to end of first continuation.
    state.card_state.pending_input = PendingInput(
        kind="sandbox_confirm",
        run_id="run-multi",
        question="Allow?",
        choices=[("Yes", "approve", "primary")],
        question_id="q2",
        resolved_choice="approve",
    )
    await d.dispatch_patch(state)
    assert state.bot_message_id is None
    assert d.sent_char_offset == len(first_cont)

    # Empty post-HITL create skips "..." placeholder.
    assert await d.dispatch_create(state) is False
    assert conn.sent[-1] == first_cont

    second_only = "Second post-HITL reply."
    state.card_state.post_hitl_content = first_cont + second_only
    await d.dispatch_stream(state, state.card_state.post_hitl_content)
    assert conn.sent[-1] == second_only
    assert first_cont not in conn.sent[-1]


@pytest.mark.asyncio
async def test_send_only_channel_flushes_pre_hitl_before_card_and_sends_post_hitl() -> None:
    """Send-only Web Chat must flush buffered pre-HITL text before HITL card."""
    from cubeplex.im.outbound import fold_event

    class _SendOnlyWithCards(_SendOnlyConnector):
        def __init__(self) -> None:
            super().__init__()
            self.events_log: list[str] = []

        async def send_message(self, text: str) -> str:
            self.events_log.append(f"msg:{text}")
            return await super().send_message(text)

        async def send_card(self, card: dict[str, object]) -> str:
            body = card.get("body")
            prompt = body[0]["text"] if isinstance(body, list) and body else ""
            self.events_log.append(f"card:{prompt}")
            return "card-1"

    conn = _SendOnlyWithCards()
    state = RenderState(bot_name="bot", run_id="run-send-only-hitl")
    d = TeamsOpDispatcher(connector=conn, state=state)
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

    await step({"type": "text_delta", "data": {"content": "Intro before ask"}})
    assert conn.events_log == []  # buffered while streaming

    await step(
        {
            "type": "ask_user_request",
            "data": {
                "question_id": "q1",
                "questions": [
                    {
                        "key": "choice",
                        "prompt": "Pick one?",
                        "options": [{"label": "A", "value": "a"}],
                    }
                ],
            },
        }
    )
    assert conn.events_log == ["msg:Intro before ask", "card:Pick one?"]

    await step(
        {
            "type": "ask_user_resolved",
            "data": {"question_id": "q1", "answers": {"choice": "a"}},
        }
    )
    await step({"type": "text_delta", "data": {"content": "Post-HITL final reply"}})
    assert conn.events_log == ["msg:Intro before ask", "card:Pick one?"]

    await step({"type": "done", "data": {}})
    assert conn.events_log == [
        "msg:Intro before ask",
        "card:Pick one?",
        "msg:Post-HITL final reply",
    ]


@pytest.mark.asyncio
async def test_edit_channel_finalize_placeholder_and_oversize_split() -> None:
    from cubeplex.im.teams.connector import TEAMS_MSG_LIMIT

    class _EditConn:
        supports_message_edit = True

        def __init__(self) -> None:
            self.sent: list[str] = []
            self.edits: list[tuple[str, str]] = []

        async def send_message(self, text: str) -> str:
            self.sent.append(text)
            return f"msg-{len(self.sent)}"

        async def edit_message(self, activity_id: str, text: str) -> bool:
            self.edits.append((activity_id, text))
            return True

    # Empty final content with an open "..." placeholder replaces it with "✓".
    conn1 = _EditConn()
    s1 = RenderState(bot_name="bot", run_id="run-dots")
    s1.bot_message_id = "msg-dots"
    d1 = TeamsOpDispatcher(connector=conn1, state=s1)
    assert await d1.dispatch_finalize(s1) is True
    assert conn1.edits == [("msg-dots", "✓")]

    # Oversize finalize edits open segment first, then sends overflow chunk.
    conn2 = _EditConn()
    s2 = RenderState(bot_name="bot", run_id="run-long")
    s2.bot_message_id = "msg-seg2"
    d2 = TeamsOpDispatcher(connector=conn2, state=s2)
    d2.sent_char_offset = 1000
    s2.card_state.streaming_content = "A" * 1000 + "B" * (TEAMS_MSG_LIMIT + 500)
    assert await d2.dispatch_finalize(s2) is True
    assert conn2.edits == [("msg-seg2", "B" * TEAMS_MSG_LIMIT)]
    assert conn2.sent == ["B" * 500]
    # Calling finalize again when remaining is empty is a clean no-op.
    assert await d2.dispatch_finalize(s2) is True


@pytest.mark.asyncio
async def test_edit_channel_debounced_pre_hitl_splits_and_error_fallbacks() -> None:
    from cubeplex.im.outbound import fold_event

    class _EditConn:
        supports_message_edit = True

        def __init__(self) -> None:
            self.sent: list[str] = []
            self.edits: list[tuple[str, str]] = []

        async def send_message(self, text: str) -> str:
            self.sent.append(text)
            return f"msg-{len(self.sent)}"

        async def edit_message(self, activity_id: str, text: str) -> bool:
            self.edits.append((activity_id, text))
            return True

    # Debounced pre-HITL tokens are flushed into msg-1 before the prompt is sent.
    conn = _EditConn()
    state = RenderState(bot_name="bot", run_id="run-debounce", stream_interval=1.2)
    d = TeamsOpDispatcher(connector=conn, state=state)
    op1 = fold_event({"type": "text_delta", "data": {"content": "Hi"}}, state, now=1.0)
    assert op1 is not None
    await d.dispatch_create(state)
    assert fold_event({"type": "text_delta", "data": {"content": " there"}}, state, now=1.1) is None
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
    assert op_ask is not None
    await d.dispatch_patch(state)
    assert conn.edits == [("msg-1", "Hi there")]
    assert any("Custom?" in m for m in conn.sent)

    # Create sealed_prefix split (>24000 chars) + stream split + empty segment.
    conn_split = _EditConn()
    s_split = RenderState(bot_name="bot", run_id="run-split")
    s_split.card_state.streaming_content = "X" * 25000
    d_split = TeamsOpDispatcher(connector=conn_split, state=s_split)
    assert await d_split.dispatch_create(s_split) is True
    assert s_split.bot_message_id is None
    assert d_split.sent_char_offset > 0

    s_split.bot_message_id = "msg-open"
    d_split.sent_char_offset = 0
    assert await d_split.dispatch_stream(s_split, s_split.card_state.streaming_content) is True
    assert s_split.bot_message_id == "msg-2"
    d_split.sent_char_offset = len(s_split.card_state.streaming_content)
    assert await d_split.dispatch_stream(s_split, s_split.card_state.streaming_content) is True

    # Finalize edit exception falls back to send_message (and placeholder clear doesn't raise).
    class _FailEditConn(_EditConn):
        async def edit_message(self, activity_id: str, text: str) -> bool:
            del activity_id, text
            raise RuntimeError("edit failed")

    c_err = _FailEditConn()
    s_err = RenderState(bot_name="bot", run_id="run-err")
    s_err.bot_message_id = "msg-dots"
    d_err = TeamsOpDispatcher(connector=c_err, state=s_err)
    assert await d_err.dispatch_finalize(s_err) is True

    s_err2 = RenderState(bot_name="bot", run_id="run-err2")
    s_err2.bot_message_id = "msg-open"
    s_err2.card_state.streaming_content = "Fallback message"
    d_err2 = TeamsOpDispatcher(connector=c_err, state=s_err2)
    assert await d_err2.dispatch_finalize(s_err2) is True
    assert c_err.sent == ["Fallback message"]
