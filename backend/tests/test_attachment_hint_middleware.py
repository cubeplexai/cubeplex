"""Unit tests for AttachmentHintMiddleware."""

from typing import Any

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage

from cubebox.middleware.attachments import AttachmentHintMiddleware


def _meta_block() -> dict[str, object]:
    return {
        "kind": "document",
        "filename": "report.xlsx",
        "sandbox_path": "/workspace/uploads/abc/def/report.xlsx",
        "size_bytes": 16000,
    }


def _build_request(messages: list[Any]) -> ModelRequest[Any]:
    fake_model = FakeMessagesListChatModel(responses=[AIMessage(content="ok")])
    return ModelRequest(
        model=fake_model,  # type: ignore[arg-type]
        messages=messages,
        tools=[],
        tool_choice=None,
        response_format=None,
        state={"messages": messages},
        runtime=None,
        model_settings={},
    )


@pytest.mark.asyncio
async def test_middleware_injects_hint_into_messages_seen_by_handler() -> None:
    """The handler must observe the augmented HumanMessage (hint appended)."""
    state_messages = [
        HumanMessage(
            content="how many companies",
            additional_kwargs={"attachments_meta": [_meta_block()]},
        )
    ]
    request = _build_request(state_messages)

    seen: dict[str, Any] = {}

    async def handler(req: ModelRequest[Any]) -> ModelResponse:
        seen["messages"] = req.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    mw = AttachmentHintMiddleware()
    await mw.awrap_model_call(request, handler)

    assert len(seen["messages"]) == 1
    seen_msg = seen["messages"][0]
    assert isinstance(seen_msg, HumanMessage)
    assert "how many companies" in seen_msg.content
    assert "[Attachments]" in seen_msg.content
    assert "report.xlsx" in seen_msg.content
    assert "/workspace/uploads/abc/def/report.xlsx" in seen_msg.content
    assert "file_read" in seen_msg.content


@pytest.mark.asyncio
async def test_middleware_does_not_mutate_state_messages() -> None:
    """state['messages'] must keep the original plain content (no hint leak)."""
    state_messages = [
        HumanMessage(
            content="how many companies",
            additional_kwargs={"attachments_meta": [_meta_block()]},
        )
    ]
    request = _build_request(state_messages)

    async def handler(_req: ModelRequest[Any]) -> ModelResponse:
        return ModelResponse(result=[AIMessage(content="ok")])

    mw = AttachmentHintMiddleware()
    await mw.awrap_model_call(request, handler)

    # Original list and original message untouched
    assert state_messages[0].content == "how many companies"
    assert "[Attachments]" not in state_messages[0].content


@pytest.mark.asyncio
async def test_middleware_no_op_when_no_attachments() -> None:
    """Plain HumanMessages pass through with the same request object."""
    state_messages = [HumanMessage(content="hi there")]
    request = _build_request(state_messages)

    received: dict[str, Any] = {}

    async def handler(req: ModelRequest[Any]) -> ModelResponse:
        received["request"] = req
        received["messages"] = req.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    mw = AttachmentHintMiddleware()
    await mw.awrap_model_call(request, handler)

    # No-op short-circuit — same request, same list
    assert received["request"] is request
    assert received["messages"] is state_messages
    assert state_messages[0].content == "hi there"


@pytest.mark.asyncio
async def test_middleware_skips_already_augmented_legacy_content() -> None:
    """Legacy checkpoints carry the hint in content AND attachments_meta — don't duplicate."""
    from cubebox.agents.convert import render_attachments_hint

    meta = [_meta_block()]
    legacy_content = "how many companies" + render_attachments_hint(meta)
    state_messages = [
        HumanMessage(content=legacy_content, additional_kwargs={"attachments_meta": meta})
    ]
    request = _build_request(state_messages)

    seen: dict[str, Any] = {}

    async def handler(req: ModelRequest[Any]) -> ModelResponse:
        seen["messages"] = req.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    mw = AttachmentHintMiddleware()
    await mw.awrap_model_call(request, handler)

    seen_msg = seen["messages"][0]
    # Hint appears exactly once, not twice
    assert seen_msg.content.count("[Attachments]") == 1
    assert seen_msg.content == legacy_content


@pytest.mark.asyncio
async def test_middleware_only_augments_messages_with_meta() -> None:
    """Mixed history: only messages carrying attachments_meta get the hint."""
    plain = HumanMessage(content="follow-up question")
    with_meta = HumanMessage(
        content="here is the file",
        additional_kwargs={"attachments_meta": [_meta_block()]},
    )
    state_messages = [plain, AIMessage(content="prior reply"), with_meta]
    request = _build_request(state_messages)

    seen: dict[str, Any] = {}

    async def handler(req: ModelRequest[Any]) -> ModelResponse:
        seen["messages"] = req.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    mw = AttachmentHintMiddleware()
    await mw.awrap_model_call(request, handler)

    msgs = seen["messages"]
    # Plain HumanMessage passes through unchanged (same identity)
    assert msgs[0] is plain
    # AIMessage passes through unchanged
    assert msgs[1] is state_messages[1]
    # Attachment-bearing message is replaced with an augmented copy
    assert msgs[2] is not with_meta
    assert "[Attachments]" in msgs[2].content
    assert "here is the file" in msgs[2].content
