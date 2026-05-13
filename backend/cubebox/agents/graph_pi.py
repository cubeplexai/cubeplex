"""cubepi agent factory for cubebox runtime (M1.4).

Builds a bare cubepi.Agent without cubebox middleware. M3 will add the
11 middleware ports as opt-in *_pi modules and extend this factory to
compose them via Agent(middleware=[...]).
"""

from __future__ import annotations

from typing import Any

from cubepi import Agent, Model
from cubepi.agent.types import AgentTool
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
) -> Agent[Any]:
    """Build a cubepi.Agent for cubebox's cubepi runtime path.

    M1: bare agent, no cubebox middleware. M2 will wire tools through
    cubebox.tools.registry_pi; M3 will compose the 11 cubebox middlewares
    via the `middleware=[...]` kwarg on Agent.
    """
    return Agent(
        provider=provider,
        model=Model(id=model_id, provider=provider_name),
        system_prompt=system_prompt,
        tools=tools or [],
        checkpointer=checkpointer,
        thread_id=thread_id,
    )
