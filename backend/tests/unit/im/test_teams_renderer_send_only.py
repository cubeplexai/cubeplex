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
