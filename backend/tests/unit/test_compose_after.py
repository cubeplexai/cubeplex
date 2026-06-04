"""Tests for ``compose_after_tool_call`` chained composer."""

from __future__ import annotations

from typing import Any

import pytest
from cubepi.agent.types import (
    AfterToolCallContext,
    AfterToolCallResult,
    AgentContext,
    AgentToolResult,
)
from cubepi.middleware.base import Middleware
from cubepi.providers.base import AssistantMessage, TextContent, ToolCall, Usage

from cubebox.middleware._compose import compose_after_tool_call


def _ctx(content_text: str = "raw") -> AfterToolCallContext:
    tool_call = ToolCall(id="tc-1", name="web_search", arguments={})
    assistant = AssistantMessage(content=[tool_call], usage=Usage())
    agent_ctx = AgentContext(system_prompt="", messages=[])
    return AfterToolCallContext(
        assistant_message=assistant,
        tool_call=tool_call,
        args={},
        result=AgentToolResult(content=[TextContent(text=content_text)]),
        is_error=False,
        context=agent_ctx,
    )


class _ContentRewriter(Middleware):
    """First-in-chain middleware that rewrites content + adds dict details."""

    async def after_tool_call(self, ctx, *, signal=None) -> AfterToolCallResult:
        return AfterToolCallResult(
            content=[TextContent(text="rewritten")],
            details={"citations": [{"citation_id": 1}]},
        )


class _DetailsOnly(Middleware):
    """Second-in-chain middleware that adds different details — like Timestamp."""

    async def after_tool_call(self, ctx, *, signal=None) -> AfterToolCallResult:
        return AfterToolCallResult(details={"tool_ended_at": "T"})


class _NoOp(Middleware):
    async def after_tool_call(self, ctx, *, signal=None) -> None:
        return None


@pytest.mark.asyncio
async def test_compose_preserves_content_rewrite_through_later_details_only() -> None:
    composed = compose_after_tool_call([_ContentRewriter(), _DetailsOnly()])
    assert composed is not None
    out = await composed(_ctx())
    assert out is not None
    assert out.content is not None
    assert out.content[0].text == "rewritten"
    # Dict details merge — both contributions survive.
    assert out.details == {
        "citations": [{"citation_id": 1}],
        "tool_ended_at": "T",
    }


@pytest.mark.asyncio
async def test_compose_threads_running_result_to_next_middleware() -> None:
    """The second middleware sees the rewritten content via its derived ctx."""

    seen: dict[str, Any] = {}

    class _Inspect(Middleware):
        async def after_tool_call(self, ctx, *, signal=None) -> AfterToolCallResult:
            seen["content_text"] = ctx.result.content[0].text
            seen["details"] = ctx.result.details
            return AfterToolCallResult(details={"x": 1})

    composed = compose_after_tool_call([_ContentRewriter(), _Inspect()])
    assert composed is not None
    await composed(_ctx())
    assert seen["content_text"] == "rewritten"
    assert seen["details"] == {"citations": [{"citation_id": 1}]}


@pytest.mark.asyncio
async def test_compose_returns_none_when_every_middleware_returns_none() -> None:
    composed = compose_after_tool_call([_NoOp(), _NoOp()])
    assert composed is not None
    out = await composed(_ctx())
    assert out is None


@pytest.mark.asyncio
async def test_compose_filters_middleware_without_after_tool_call() -> None:
    """Middlewares that don't override after_tool_call must not appear in the chain."""

    class _BareMw(Middleware):
        # No after_tool_call override.
        async def transform_system_prompt(self, sp, *, ctx, signal=None) -> str:
            del ctx, signal
            return sp

    composed = compose_after_tool_call([_BareMw()])
    assert composed is None


def test_compose_returns_none_for_empty_middleware_list() -> None:
    assert compose_after_tool_call([]) is None


@pytest.mark.asyncio
async def test_compose_skips_none_returns_but_keeps_chain_running() -> None:
    """A None return mid-chain doesn't break later contributions."""
    composed = compose_after_tool_call([_NoOp(), _ContentRewriter(), _DetailsOnly()])
    assert composed is not None
    out = await composed(_ctx())
    assert out is not None
    assert out.content[0].text == "rewritten"
    assert out.details == {
        "citations": [{"citation_id": 1}],
        "tool_ended_at": "T",
    }


@pytest.mark.asyncio
async def test_compose_terminate_and_is_error_propagation() -> None:
    class _Terminator(Middleware):
        async def after_tool_call(self, ctx, *, signal=None) -> AfterToolCallResult:
            return AfterToolCallResult(terminate=True, is_error=True)

    composed = compose_after_tool_call([_ContentRewriter(), _Terminator()])
    assert composed is not None
    out = await composed(_ctx())
    assert out is not None
    assert out.terminate is True
    assert out.is_error is True
    # Content rewrite survives even though _Terminator returned no content.
    assert out.content[0].text == "rewritten"
