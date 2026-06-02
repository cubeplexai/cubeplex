"""Agent capability registry — the single entry point for run_manager."""

from __future__ import annotations

from typing import Any

from cubepi.agent.types import AgentTool

from cubebox.agents.actions.builder import ContextFactory, build_capability_tool
from cubebox.agents.actions.capabilities.scheduled_tasks import SCHEDULED_TASKS_CAPABILITY
from cubebox.agents.actions.capabilities.skills import SkillDeps, build_skills_capability
from cubebox.agents.actions.types import AgentCapability

AGENT_CAPABILITIES: list[AgentCapability] = [
    SCHEDULED_TASKS_CAPABILITY,
]


def tools_for_run(
    context_factory: ContextFactory,
    *,
    allow_mutations: bool,
    skill_deps: SkillDeps | None = None,
) -> list[AgentTool[Any]]:
    """Build agent tools for all registered capabilities.

    Static capabilities (declared in AGENT_CAPABILITIES) are built unconditionally.
    The skills capability is dynamic: built only when skill_deps is supplied,
    because its handlers must close over run-scoped catalog / registry / session.
    """
    tools: list[AgentTool[Any]] = []
    for cap in AGENT_CAPABILITIES:
        tool = build_capability_tool(cap, context_factory, allow_mutations=allow_mutations)
        if tool is not None:
            tools.append(tool)

    if skill_deps is not None:
        skills_cap = build_skills_capability(skill_deps)
        skills_tool = build_capability_tool(
            skills_cap,
            context_factory,
            allow_mutations=allow_mutations,
        )
        if skills_tool is not None:
            tools.append(skills_tool)

    return tools
