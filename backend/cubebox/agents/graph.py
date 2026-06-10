"""cubepi agent factory for cubebox runtime (M1.4, extended in M3.f + cubepi 0.7).

Builds a cubepi.Agent wired with the full cubebox middleware stack.
Middleware composition is handled by _run_cubepi_path in run_manager.py;
this factory simply receives the pre-composed list and forwards it to
Agent(middleware=[...]).
"""

from __future__ import annotations

from typing import Any

from cubepi import Agent
from cubepi.agent.types import AgentTool
from cubepi.deferred import DeferredToolGroup
from cubepi.hitl import HitlChannel
from cubepi.middleware.base import Middleware
from cubepi.providers.base import BaseProvider, ThinkingLevel

from cubebox.middleware._compose import compose_after_tool_call


def create_cubebox_agent(
    *,
    provider: BaseProvider,
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
    deferred_tool_groups: list[DeferredToolGroup] | None = None,
) -> Agent[Any]:
    """Build a cubepi.Agent for cubebox's cubepi runtime path.

    ``provider_name`` is accepted for telemetry parity with older call
    sites, but the cubepi 0.7 API now reads ``provider_id`` off the
    provider instance — set it via
    ``LLMFactory.build_cubepi_provider(provider_config, provider_name=...)``.
    """
    mw_list = middleware or []
    model = provider.model(
        model_id,
        reasoning=reasoning,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return Agent(
        model=model,
        thinking=thinking,
        system_prompt=system_prompt,
        tools=tools or [],
        checkpointer=checkpointer,
        thread_id=thread_id,
        middleware=mw_list,
        channel=channel,
        deferred_tool_groups=deferred_tool_groups,
        # See compose_after_tool_call for why we override the default.
        after_tool_call=compose_after_tool_call(mw_list),
    )
