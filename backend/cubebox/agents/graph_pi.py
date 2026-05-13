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
from cubepi.middleware.base import Middleware
from cubepi.providers.base import Provider


def create_cubebox_cubepi_agent(
    *,
    provider: Provider,
    model_id: str,
    provider_name: str,
    system_prompt: str = "",
    tools: list[AgentTool[Any]] | None = None,
    checkpointer: Any = None,
    thread_id: str | None = None,
    middleware: list[Middleware] | None = None,
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
    """
    return Agent(
        provider=provider,
        model=Model(id=model_id, provider=provider_name),
        system_prompt=system_prompt,
        tools=tools or [],
        checkpointer=checkpointer,
        thread_id=thread_id,
        middleware=middleware or [],
    )
