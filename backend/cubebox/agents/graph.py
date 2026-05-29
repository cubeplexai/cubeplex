"""cubepi agent factory for cubebox runtime (M1.4, extended in M3.f).

Builds a cubepi.Agent wired with the full cubebox middleware stack.
Middleware composition is handled by _run_cubepi_path in run_manager.py;
this factory simply receives the pre-composed list and forwards it to
Agent(middleware=[...]).
"""

from __future__ import annotations

from typing import Any

from cubepi import Agent, Model
from cubepi.agent.types import AgentTool
from cubepi.hitl import HitlChannel
from cubepi.middleware.base import Middleware
from cubepi.providers.base import Provider, ThinkingLevel

from cubebox.middleware._compose import compose_after_tool_call


def create_cubebox_agent(
    *,
    provider: Provider,
    model_id: str,
    provider_name: str,
    system_prompt: str = "",
    tools: list[AgentTool[Any]] | None = None,
    checkpointer: Any = None,
    thread_id: str | None = None,
    middleware: list[Middleware] | None = None,
    max_tokens: int = 8192,
    temperature: float = 0.7,
    reasoning: bool = False,
    thinking: ThinkingLevel = "off",
    channel: HitlChannel | None = None,
) -> Agent[Any]:
    """Build a cubepi.Agent for cubebox's cubepi runtime path.

    Args:
        provider: cubepi Provider instance (built by LLMFactory).
        model_id: Model identifier string (e.g. "claude-3-5-sonnet-20241022").
        provider_name: Provider label for the Model object (e.g. "anthropic").
        system_prompt: Base system prompt; middleware may append to it.
        tools: Tool list assembled by the caller (builtin + MCP + middleware tools).
        checkpointer: cubepi checkpointer for conversation persistence.
        thread_id: Conversation ID used as the checkpointer thread key.
        middleware: Pre-composed list of cubepi.Middleware instances.  When
            None or empty, the agent runs without any cubebox middleware (bare
            mode, used in unit tests and subagent spawning without inheritance).
        max_tokens: Maximum output tokens forwarded to the provider (defaults to
            8192; callers should pass the model's configured max_tokens).
        temperature: Sampling temperature forwarded to the provider (default 0.7).
        reasoning: Whether the model is reasoning-capable. Must be True for the
            capability layer to apply the reasoning payload (cubepi guards the
            thinking toggle on Model.reasoning).
        thinking: Thinking level for the run ("off" disables it). Only takes
            effect when ``reasoning`` is True.
    """
    mw_list = middleware or []
    return Agent(
        provider=provider,
        model=Model(
            id=model_id,
            provider=provider_name,
            reasoning=reasoning,
            max_tokens=max_tokens,
            temperature=temperature,
        ),
        thinking=thinking,
        system_prompt=system_prompt,
        tools=tools or [],
        checkpointer=checkpointer,
        thread_id=thread_id,
        middleware=mw_list,
        channel=channel,
        # Override cubepi's default after_tool_call composer: the default keeps
        # only the last non-None AfterToolCallResult, so a middleware that
        # rewrites `content` (CitationMiddleware) is dropped the moment a later
        # middleware (TimestampMiddleware) returns a details-only result. Our
        # composer threads each middleware's return through to the next.
        after_tool_call=compose_after_tool_call(mw_list),
    )
