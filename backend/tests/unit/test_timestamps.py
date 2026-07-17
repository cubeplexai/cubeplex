"""Unit tests for TimestampMiddleware (M3.d.2).

Covers:
- transform_context returns messages byte-identical (cache discipline).
- transform_context sets the turn-started-at ContextVar.
- before_tool_call stashes tool start time keyed by tool_call.id.
- after_tool_call writes tool_started_at + tool_ended_at into details.
- after_tool_call merges with existing dict details.
- after_tool_call handles missing start time (defensive path).
- after_model_response writes created_at to response.metadata.
- after_model_response writes turn_started_at to response.metadata.
- after_model_response does not overwrite existing created_at.
- after_model_response leaves reasoning_duration_ms untouched.
- after_model_response returns None (no response mutation / decision).
- Round trip: transform_context → before_tool_call → after_tool_call
  → after_model_response all wired together.
- No timestamp ever appears in message content (cache discipline).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from cubepi.agent.types import (
    AfterToolCallContext,
    AgentContext,
    AgentToolResult,
    BeforeToolCallContext,
)
from cubepi.providers.base import AssistantMessage, TextContent, ToolCall, UserMessage

from cubeplex.middleware.timestamps import TimestampMiddleware, _turn_started_at

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tool_call(tool_id: str = "tc-1", name: str = "execute") -> ToolCall:
    return ToolCall(id=tool_id, name=name, arguments={"command": "ls"})


def _make_assistant_message() -> AssistantMessage:
    return AssistantMessage(content=[TextContent(text="Hello")])


def _make_agent_context() -> AgentContext:
    return AgentContext(system_prompt="sys", messages=[])


def _make_user_message(text: str = "hello") -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _make_tool_result(details: Any = None) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text="ok")], details=details)


def _make_before_ctx(
    tool_id: str = "tc-1",
    agent_ctx: AgentContext | None = None,
) -> BeforeToolCallContext:
    return BeforeToolCallContext(
        assistant_message=_make_assistant_message(),
        tool_call=_make_tool_call(tool_id),
        args=MagicMock(),
        context=agent_ctx or _make_agent_context(),
    )


def _make_after_ctx(
    tool_id: str = "tc-1",
    existing_details: Any = None,
    agent_ctx: AgentContext | None = None,
) -> AfterToolCallContext:
    return AfterToolCallContext(
        assistant_message=_make_assistant_message(),
        tool_call=_make_tool_call(tool_id),
        args=MagicMock(),
        result=_make_tool_result(details=existing_details),
        is_error=False,
        context=agent_ctx or _make_agent_context(),
    )


# ---------------------------------------------------------------------------
# transform_context: cache discipline — message content must be untouched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_context_returns_messages_unchanged() -> None:
    """Messages returned byte-identical; no content modification."""
    mw = TimestampMiddleware()
    msgs = [_make_user_message("turn 1"), _make_user_message("turn 2")]
    original_content = [m.model_dump() for m in msgs]

    result = await mw.transform_context(msgs, ctx=object())

    assert result is msgs  # same list object
    for orig, got in zip(original_content, result, strict=True):
        assert got.model_dump() == orig


@pytest.mark.asyncio
async def test_transform_context_does_not_modify_content() -> None:
    """Strict check: content field of each message is untouched."""
    mw = TimestampMiddleware()
    msg = _make_user_message("do not touch me")
    await mw.transform_context([msg], ctx=object())
    assert msg.content[0].text == "do not touch me"


@pytest.mark.asyncio
async def test_transform_context_no_timestamp_in_message_metadata() -> None:
    """transform_context must not inject timestamps into message.metadata."""
    mw = TimestampMiddleware()
    msg = _make_user_message("cache probe")
    await mw.transform_context([msg], ctx=object())
    # metadata should be empty — no timestamp injected
    assert msg.metadata == {}


# ---------------------------------------------------------------------------
# transform_context: sets ContextVar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transform_context_sets_turn_started_at_contextvar() -> None:
    """After transform_context, _turn_started_at ContextVar holds an ISO timestamp."""
    mw = TimestampMiddleware()
    _turn_started_at.set(None)  # reset

    await mw.transform_context([], ctx=object())

    val = _turn_started_at.get()
    assert val is not None
    # Should be an ISO 8601 string with UTC offset
    assert "T" in val
    assert val.endswith("+00:00") or val.endswith("Z")


# ---------------------------------------------------------------------------
# before_tool_call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_before_tool_call_returns_none() -> None:
    """before_tool_call does not block — returns None."""
    mw = TimestampMiddleware()
    ctx = _make_before_ctx("tc-42")
    result = await mw.before_tool_call(ctx)
    assert result is None


@pytest.mark.asyncio
async def test_before_tool_call_stashes_start_time() -> None:
    """before_tool_call populates _tool_started_at keyed by tool_call.id."""
    mw = TimestampMiddleware()
    ctx = _make_before_ctx("tc-99")
    await mw.before_tool_call(ctx)
    assert "tc-99" in mw._tool_started_at
    started = mw._tool_started_at["tc-99"]
    assert isinstance(started, str)
    assert "T" in started


@pytest.mark.asyncio
async def test_before_tool_call_separate_ids_stored_independently() -> None:
    """Two parallel tool calls get separate start times."""
    mw = TimestampMiddleware()
    await mw.before_tool_call(_make_before_ctx("tc-a"))
    await mw.before_tool_call(_make_before_ctx("tc-b"))

    assert "tc-a" in mw._tool_started_at
    assert "tc-b" in mw._tool_started_at


# ---------------------------------------------------------------------------
# after_tool_call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_tool_call_writes_timing_to_details() -> None:
    """after_tool_call returns AfterToolCallResult with timing in details."""
    mw = TimestampMiddleware()
    # Stash a start time first
    mw._tool_started_at["tc-1"] = "2024-01-01T00:00:00+00:00"

    ctx = _make_after_ctx("tc-1")
    result = await mw.after_tool_call(ctx)

    assert result is not None
    assert isinstance(result.details, dict)
    assert result.details["tool_started_at"] == "2024-01-01T00:00:00+00:00"
    assert "tool_ended_at" in result.details
    assert result.details["tool_ended_at"] != ""


@pytest.mark.asyncio
async def test_after_tool_call_consumes_start_time() -> None:
    """after_tool_call removes the stashed start time (no memory leak)."""
    mw = TimestampMiddleware()
    mw._tool_started_at["tc-7"] = "2024-01-01T00:00:00+00:00"

    ctx = _make_after_ctx("tc-7")
    await mw.after_tool_call(ctx)

    assert "tc-7" not in mw._tool_started_at


@pytest.mark.asyncio
async def test_after_tool_call_merges_with_existing_dict_details() -> None:
    """Timing is merged with existing dict details; original keys preserved."""
    mw = TimestampMiddleware()
    mw._tool_started_at["tc-2"] = "2024-01-01T00:00:00+00:00"

    ctx = _make_after_ctx("tc-2", existing_details={"exit_code": 0, "output": "hello"})
    result = await mw.after_tool_call(ctx)

    assert result is not None
    assert result.details["exit_code"] == 0
    assert result.details["output"] == "hello"
    assert "tool_started_at" in result.details
    assert "tool_ended_at" in result.details


@pytest.mark.asyncio
async def test_after_tool_call_handles_non_dict_details() -> None:
    """Non-dict existing details are preserved under '_details' key."""
    mw = TimestampMiddleware()
    mw._tool_started_at["tc-3"] = "2024-01-01T00:00:00+00:00"

    ctx = _make_after_ctx("tc-3", existing_details="plain string")
    result = await mw.after_tool_call(ctx)

    assert result is not None
    assert result.details["_details"] == "plain string"
    assert "tool_started_at" in result.details
    assert "tool_ended_at" in result.details


@pytest.mark.asyncio
async def test_after_tool_call_returns_none_if_no_start_time() -> None:
    """If before_tool_call was not called (or id missing), returns None."""
    mw = TimestampMiddleware()
    ctx = _make_after_ctx("tc-unknown")
    result = await mw.after_tool_call(ctx)
    assert result is None


# ---------------------------------------------------------------------------
# after_model_response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_model_response_writes_created_at() -> None:
    """after_model_response stamps created_at on response.metadata."""
    mw = TimestampMiddleware()
    response = _make_assistant_message()
    ctx = MagicMock()

    await mw.after_model_response(response, ctx)

    assert "created_at" in response.metadata
    assert response.metadata["created_at"] != ""


@pytest.mark.asyncio
async def test_after_model_response_writes_turn_started_at_from_contextvar() -> None:
    """after_model_response copies turn_started_at from the ContextVar."""
    mw = TimestampMiddleware()
    _turn_started_at.set("2024-06-15T12:00:00+00:00")

    response = _make_assistant_message()
    await mw.after_model_response(response, MagicMock())

    assert response.metadata.get("turn_started_at") == "2024-06-15T12:00:00+00:00"


@pytest.mark.asyncio
async def test_after_model_response_returns_none() -> None:
    """after_model_response returns None — no response mutation via TurnAction."""
    mw = TimestampMiddleware()
    response = _make_assistant_message()
    result = await mw.after_model_response(response, MagicMock())
    assert result is None


@pytest.mark.asyncio
async def test_after_model_response_does_not_overwrite_existing_created_at() -> None:
    """setdefault semantics: existing created_at is preserved."""
    mw = TimestampMiddleware()
    response = _make_assistant_message()
    response.metadata["created_at"] = "2020-01-01T00:00:00+00:00"

    await mw.after_model_response(response, MagicMock())

    assert response.metadata["created_at"] == "2020-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_after_model_response_leaves_reasoning_duration_ms_untouched() -> None:
    """reasoning_duration_ms pre-set by LLM adapter is never overwritten."""
    mw = TimestampMiddleware()
    response = _make_assistant_message()
    response.metadata["reasoning_duration_ms"] = 1234

    await mw.after_model_response(response, MagicMock())

    assert response.metadata["reasoning_duration_ms"] == 1234


@pytest.mark.asyncio
async def test_after_model_response_turn_started_at_absent_when_contextvar_none() -> None:
    """turn_started_at is not written when transform_context was not called."""
    mw = TimestampMiddleware()
    _turn_started_at.set(None)

    response = _make_assistant_message()
    await mw.after_model_response(response, MagicMock())

    assert "turn_started_at" not in response.metadata


# ---------------------------------------------------------------------------
# Cache discipline: no timestamp in content or system-visible text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timestamps_never_in_message_content() -> None:
    """Full round trip — timestamps appear only in out-of-band metadata."""
    mw = TimestampMiddleware()
    user_msg = _make_user_message("what time is it?")

    # transform_context
    msgs = await mw.transform_context([user_msg], ctx=object())
    for msg in msgs:
        for content_item in msg.content:
            assert not any(
                keyword in getattr(content_item, "text", "")
                for keyword in ("created_at", "turn_started_at", "tool_started_at")
            )

    # before_tool_call + after_tool_call
    before_ctx = _make_before_ctx("tc-round")
    await mw.before_tool_call(before_ctx)
    after_ctx = _make_after_ctx("tc-round")
    tool_result = await mw.after_tool_call(after_ctx)
    if tool_result and tool_result.content:
        for content_item in tool_result.content:
            assert not any(
                keyword in getattr(content_item, "text", "")
                for keyword in ("created_at", "turn_started_at", "tool_started_at")
            )

    # after_model_response
    response = _make_assistant_message()
    await mw.after_model_response(response, MagicMock())
    for content_item in response.content:
        assert not any(
            keyword in getattr(content_item, "text", "")
            for keyword in ("created_at", "turn_started_at")
        )


# ---------------------------------------------------------------------------
# Round trip: full turn lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_turn_round_trip() -> None:
    """Simulate a complete turn: transform_context → before → after → amr."""
    mw = TimestampMiddleware()
    _turn_started_at.set(None)

    # 1. Turn starts — transform_context sets turn-start
    user_msg = _make_user_message("run something")
    await mw.transform_context([user_msg], ctx=object())
    turn_start_captured = _turn_started_at.get()
    assert turn_start_captured is not None

    # 2. Tool dispatch — before_tool_call stashes start
    before_ctx = _make_before_ctx("tc-rt")
    before_result = await mw.before_tool_call(before_ctx)
    assert before_result is None

    # 3. Tool finishes — after_tool_call returns timing
    after_ctx = _make_after_ctx("tc-rt")
    after_result = await mw.after_tool_call(after_ctx)
    assert after_result is not None
    assert after_result.details is not None
    assert "tool_started_at" in after_result.details
    assert "tool_ended_at" in after_result.details
    assert "tc-rt" not in mw._tool_started_at  # consumed

    # 4. Model responds — after_model_response writes metadata
    response = _make_assistant_message()
    amr_result = await mw.after_model_response(response, MagicMock())
    assert amr_result is None
    assert "created_at" in response.metadata
    assert response.metadata.get("turn_started_at") == turn_start_captured
