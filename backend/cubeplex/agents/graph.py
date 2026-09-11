"""cubeloop agent factory for cubeplex runtime (M1.4, extended in M3.f + cubeloop 0.7).

Builds a cubeloop.Agent wired with the full cubeplex middleware stack.
Middleware composition is handled by _run_cubeloop_path in run_manager.py;
this factory simply receives the pre-composed list and forwards it to
Agent(middleware=[...]).
"""

from __future__ import annotations

from typing import Any

from cubeloop import Agent
from cubeloop.agent.types import AgentTool
from cubeloop.deferred import DeferredToolGroup
from cubeloop.hitl import HitlChannel
from cubeloop.middleware.base import Middleware
from cubeloop.providers.base import ReasoningControl

from cubeplex.middleware._compose import compose_after_tool_call


def create_cubeplex_agent(
    *,
    bound_model: Any,
    system_prompt: str = "",
    tools: list[AgentTool[Any]] | None = None,
    checkpointer: Any = None,
    thread_id: str | None = None,
    middleware: list[Middleware] | None = None,
    reasoning: ReasoningControl | None = None,
    channel: HitlChannel | None = None,
    deferred_tool_groups: list[DeferredToolGroup] | None = None,
) -> Agent[Any]:
    """Build a cubeloop.Agent for cubeplex's cubeloop runtime path.

    ``bound_model`` is the pre-built ``BoundModel`` or ``FallbackBoundModel``
    that drives the agent. It is passed through unchanged so chain-aware
    fallback survives all the way to cubeloop's agent loop. Callers must
    build it via ``cubeplex.llm.builder.build_chain_model(snap, preset)``
    (or ``provider.model(...)`` for single-leg tests) — there is no
    in-factory fallback that would silently collapse a multi-leg chain.
    """
    mw_list = middleware or []
    return Agent(
        model=bound_model,
        reasoning=reasoning or ReasoningControl(),
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
