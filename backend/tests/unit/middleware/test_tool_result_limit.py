"""ToolResultLimitMiddleware on the cubeplex after_tool_call chain.

If this regresses, a ~1 MiB execute stdout kills the required consumer
instead of truncating and letting the run continue.
"""

from __future__ import annotations

import json

import pytest
from cubeloop.agent.types import (
    AfterToolCallContext,
    AgentContext,
    AgentToolResult,
)
from cubeloop.middleware import ToolResultLimitMiddleware
from cubeloop.providers.base import AssistantMessage, TextContent, ToolCall

from cubeplex.middleware._compose import compose_after_tool_call
from cubeplex.middleware.timestamps import TimestampMiddleware
from cubeplex.streams.execution_adapter import MAX_EVENT_BYTES


def _ctx(text: str, *, name: str = "execute") -> AfterToolCallContext:
    tool_call = ToolCall(id="tc-1", name=name, arguments={})
    return AfterToolCallContext(
        assistant_message=AssistantMessage(content=[tool_call]),
        tool_call=tool_call,
        args={},
        result=AgentToolResult(content=[TextContent(text=text)]),
        is_error=False,
        context=AgentContext(system_prompt="", messages=[]),
    )


@pytest.mark.asyncio
async def test_compose_keeps_truncated_content_and_timestamp_details() -> None:
    """Last-wins cubeloop compose would drop one of these; cubeplex must merge."""
    timestamps = TimestampMiddleware()
    timestamps._tool_started_at["tc-1"] = "2026-09-17T00:00:00+00:00"
    limit = ToolResultLimitMiddleware(max_chars=8)
    composed = compose_after_tool_call([timestamps, limit])
    assert composed is not None

    out = await composed(_ctx("0123456789abcdef"))
    assert out is not None
    assert out.content is not None
    assert isinstance(out.content[0], TextContent)
    assert out.content[0].text.startswith("01234567")
    assert "[truncated:" in out.content[0].text
    assert isinstance(out.details, dict)
    assert out.details["tool_started_at"] == "2026-09-17T00:00:00+00:00"
    assert "tool_ended_at" in out.details


@pytest.mark.asyncio
async def test_load_skill_is_excluded_from_the_host_cap() -> None:
    mw = ToolResultLimitMiddleware(max_chars=8, exclude_tool_names={"load_skill"})
    composed = compose_after_tool_call([mw])
    assert composed is not None
    huge = "s" * 200
    out = await composed(_ctx(huge, name="load_skill"))
    assert out is None


@pytest.mark.asyncio
async def test_truncated_execute_result_fits_projection_budget() -> None:
    """The 1.08 MiB execute event that killed run 01a0ad86… must now fit."""
    mw = ToolResultLimitMiddleware(exclude_tool_names={"load_skill"})
    composed = compose_after_tool_call([mw])
    assert composed is not None
    out = await composed(_ctx("x" * 2_000_000))
    assert out is not None
    assert out.content is not None
    payload = {
        "type": "tool_execution_end",
        "tool_call_id": "tc-1",
        "tool_name": "execute",
        "result": {"content": [b.model_dump(mode="json") for b in out.content]},
    }
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    assert size < MAX_EVENT_BYTES
