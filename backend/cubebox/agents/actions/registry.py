"""Agent capability registry — the single entry point for run_manager.

Each capability is exposed to the model as a :class:`DeferredToolGroup`. The
catalog (group_id, display_name, description, tool_names) lives in the system
prompt; the actual tool schemas only ship when the model calls
``load_tools(group_id)``.
"""

from __future__ import annotations

from typing import Any

from cubepi.agent.types import AgentTool
from cubepi.deferred import DeferredToolGroup

from cubebox.agents.actions.builder import ContextFactory, build_capability_tools
from cubebox.agents.actions.capabilities.scheduled_tasks import SCHEDULED_TASKS_CAPABILITY
from cubebox.agents.actions.capabilities.skills import SkillDeps, build_skills_capability
from cubebox.agents.actions.types import AgentCapability

AGENT_CAPABILITIES: list[AgentCapability] = [
    SCHEDULED_TASKS_CAPABILITY,
]


def _make_group(cap: AgentCapability, tools: list[AgentTool[Any]]) -> DeferredToolGroup:
    """Build a DeferredToolGroup whose loader returns the pre-built per-op tools.

    The loader is called once per run when the model expands the group; we
    pre-build the tools at run-assembly time because their handlers close over
    cheap context — there is no expensive setup to defer.
    """

    async def _loader() -> list[AgentTool[Any]]:
        return tools

    return DeferredToolGroup(
        group_id=f"cubebox:{cap.name}",
        display_name=cap.name,
        description=cap.description,
        tool_names=[t.name for t in tools],
        loader=_loader,
    )


def tools_for_run(
    context_factory: ContextFactory,
    *,
    allow_mutations: bool,
    skill_deps: SkillDeps | None = None,
) -> list[DeferredToolGroup]:
    """Build deferred groups for all registered capabilities.

    Static capabilities (declared in AGENT_CAPABILITIES) are built
    unconditionally. The skills capability is dynamic: built only when
    skill_deps is supplied, because its handlers must close over run-scoped
    catalog / registry / session.

    Groups whose mutation gate drops every operation are omitted entirely
    (e.g. an automated run with a mutation-only capability would see no
    catalog entry for it).
    """
    groups: list[DeferredToolGroup] = []

    for cap in AGENT_CAPABILITIES:
        tools = build_capability_tools(cap, context_factory, allow_mutations=allow_mutations)
        if tools:
            groups.append(_make_group(cap, tools))

    if skill_deps is not None:
        skills_cap = build_skills_capability(skill_deps)
        skills_tools = build_capability_tools(
            skills_cap,
            context_factory,
            allow_mutations=allow_mutations,
        )
        if skills_tools:
            groups.append(_make_group(skills_cap, skills_tools))

    return groups
