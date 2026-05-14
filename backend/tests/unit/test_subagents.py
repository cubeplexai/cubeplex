"""Unit tests for SubAgentMiddleware (M3.c.3).

Covers:
- Middleware exposes exactly one tool named 'subagent'.
- Happy path: inner agent (FauxProvider) returns text captured in result.
- Unknown subagent_type falls back to general-purpose.
- subagent_event_queue receives tagged events when set.
- Error path (inner agent raises) returns is_error=True.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from cubepi.providers.faux import FauxProvider, faux_assistant_message

from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware, subagent_event_queue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mw(
    subagent_map: dict[str, SubAgent] | None = None,
    *,
    provider: FauxProvider | None = None,
) -> SubAgentMiddleware:
    if provider is None:
        provider = FauxProvider()
    if subagent_map is None:
        subagent_map = {
            "general-purpose": SubAgent(
                name="general-purpose",
                description="general",
                system_prompt="You are a sub.",
            )
        }
    return SubAgentMiddleware(
        subagent_map=subagent_map,
        default_provider=provider,
        default_model_id="test-model",
        default_provider_name="faux",
    )


# ---------------------------------------------------------------------------
# Structure tests (synchronous)
# ---------------------------------------------------------------------------


def test_middleware_exposes_one_subagent_tool() -> None:
    mw = _make_mw()
    assert len(mw.tools) == 1
    assert mw.tools[0].name == "subagent"


def test_subagent_tool_has_parameters_schema() -> None:
    mw = _make_mw()
    [tool] = mw.tools
    # parameters must be a Pydantic model class
    schema = tool.parameters.model_json_schema()
    assert "prompt" in schema["properties"]
    assert "subagent_type" in schema["properties"]


def test_general_purpose_fallback_auto_registered() -> None:
    """Middleware with no general-purpose key auto-adds it."""
    mw = SubAgentMiddleware(
        subagent_map={
            "specialist": SubAgent(
                name="specialist",
                description="niche",
                system_prompt="You are special.",
            )
        },
        default_provider=FauxProvider(),
        default_model_id="test-model",
        default_provider_name="faux",
    )
    assert "general-purpose" in mw._subagent_map
    assert "specialist" in mw._subagent_map


def test_shared_tools_excludes_subagent_and_load_skill() -> None:
    """subagent and load_skill tools are filtered from shared tools to prevent recursion."""
    from cubepi import AgentTool, AgentToolResult, TextContent
    from pydantic import BaseModel

    class _NoArgs(BaseModel):
        pass

    async def _noop(
        tc_id: str, args: _NoArgs, *, signal: Any = None, on_update: Any = None
    ) -> AgentToolResult:
        return AgentToolResult(content=[TextContent(text="noop")])

    safe_tool = AgentTool(name="safe", description="safe", parameters=_NoArgs, execute=_noop)
    sub_tool = AgentTool(name="subagent", description="sub", parameters=_NoArgs, execute=_noop)
    skill_tool = AgentTool(
        name="load_skill", description="skill", parameters=_NoArgs, execute=_noop
    )

    mw = SubAgentMiddleware(
        subagent_map={},
        default_provider=FauxProvider(),
        default_model_id="test-model",
        default_provider_name="faux",
        shared_tools=[safe_tool, sub_tool, skill_tool],
    )
    assert mw._shared_tools == (safe_tool,)


# ---------------------------------------------------------------------------
# Async happy-path test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_tool_dispatches_to_inner_agent() -> None:
    """Inner agent (FauxProvider) returns text captured in tool result."""
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("subagent reply")])

    mw = _make_mw(provider=provider)
    [sub_tool] = mw.tools

    args = sub_tool.parameters(
        name="x",
        role="r",
        task="t",
        prompt="please reply",
        subagent_type="general-purpose",
    )
    result = await sub_tool.execute("tc-1", args, signal=None, on_update=None)

    assert not result.is_error
    assert "subagent reply" in result.content[0].text


@pytest.mark.asyncio
async def test_unknown_subagent_type_falls_back_to_general_purpose() -> None:
    """An unknown subagent_type triggers the general-purpose fallback."""
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("fallback reply")])

    mw = _make_mw(
        subagent_map={
            "general-purpose": SubAgent(
                name="general-purpose",
                description="general",
                system_prompt="You are general.",
            ),
            "specialist": SubAgent(
                name="specialist",
                description="niche",
                system_prompt="You are special.",
            ),
        },
        provider=provider,
    )
    [sub_tool] = mw.tools

    args = sub_tool.parameters(
        name="x",
        role="r",
        task="t",
        prompt="fallback test",
        subagent_type="nonexistent-type",
    )
    result = await sub_tool.execute("tc-2", args, signal=None, on_update=None)

    assert not result.is_error
    assert "fallback reply" in result.content[0].text


@pytest.mark.asyncio
async def test_subagent_event_queue_receives_events() -> None:
    """When subagent_event_queue ContextVar is set, events are forwarded."""
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("queued reply")])

    mw = _make_mw(provider=provider)
    [sub_tool] = mw.tools

    queue: asyncio.Queue[Any] = asyncio.Queue()
    token = subagent_event_queue.set(queue)
    try:
        args = sub_tool.parameters(
            name="x",
            role="r",
            task="t",
            prompt="queued test",
            subagent_type="general-purpose",
        )
        result = await sub_tool.execute("tc-3", args, signal=None, on_update=None)
    finally:
        subagent_event_queue.reset(token)

    assert not result.is_error
    assert "queued reply" in result.content[0].text

    # Queue should have received at least one event
    assert not queue.empty(), "Expected events in the subagent_event_queue"

    # Events must be 3-tuples ("subagent", agent_id, sse_dict)
    while not queue.empty():
        item = queue.get_nowait()
        assert isinstance(item, tuple) and len(item) == 3
        kind, agent_id, sse = item
        assert kind == "subagent"
        assert agent_id == "subagent:tc-3"
        assert isinstance(sse, dict)
        assert "type" in sse
        assert sse["agent_id"] == "subagent:tc-3"


@pytest.mark.asyncio
async def test_error_path_returns_is_error() -> None:
    """If inner agent raises, result has is_error=True."""
    from unittest.mock import AsyncMock, patch

    provider = FauxProvider()
    mw = _make_mw(provider=provider)
    [sub_tool] = mw.tools

    args = sub_tool.parameters(
        name="x",
        role="r",
        task="t",
        prompt="break please",
        subagent_type="general-purpose",
    )

    # Patch the factory at its definition site (lazily imported inside _execute)
    with patch("cubebox.agents.graph.create_cubebox_agent") as mock_factory:
        mock_agent = AsyncMock()
        mock_agent.subscribe = lambda listener: lambda: None
        mock_agent.prompt = AsyncMock(side_effect=RuntimeError("inner boom"))
        mock_factory.return_value = mock_agent

        result = await sub_tool.execute("tc-4", args, signal=None, on_update=None)

    assert result.is_error is True
    assert "inner boom" in result.content[0].text


@pytest.mark.asyncio
async def test_subagent_events_stored_in_details() -> None:
    """Details dict contains subagent_events list (may be empty for short runs)."""
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("detail check")])

    mw = _make_mw(provider=provider)
    [sub_tool] = mw.tools

    args = sub_tool.parameters(
        name="x",
        role="r",
        task="t",
        prompt="details test",
        subagent_type="general-purpose",
    )
    result = await sub_tool.execute("tc-5", args, signal=None, on_update=None)

    assert not result.is_error
    assert isinstance(result.details, dict)
    assert "subagent_events" in result.details
    assert isinstance(result.details["subagent_events"], list)
