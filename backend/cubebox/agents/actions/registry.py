"""Agent capability registry — the single entry point for run_manager.

Each capability is exposed to the **main** agent as a
:class:`DeferredToolGroup`: the catalog (group_id, display_name, description,
tool_names) lives in the system prompt, and the actual tool schemas only ship
when the model calls ``load_tools(group_id)``.

The same per-operation AgentTools are also exposed flat (no deferred wrapper)
so callers that need eager access — primarily ``SubagentMiddleware`` —
can pass them as ``shared_tools`` to short-lived child agents where the
catalog round-trip overhead would dominate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cubepi.agent.types import AgentTool
from cubepi.deferred import DeferredToolGroup

from cubebox.agents.actions.builder import ContextFactory, build_capability_tools
from cubebox.agents.actions.capabilities.artifacts import ARTIFACTS_CAPABILITY
from cubebox.agents.actions.capabilities.conversation_history import (
    ConversationHistoryDeps,
    build_conversation_history_capability,
)
from cubebox.agents.actions.capabilities.scheduled_tasks import SCHEDULED_TASKS_CAPABILITY
from cubebox.agents.actions.capabilities.skills import SkillDeps, build_skills_capability
from cubebox.agents.actions.types import AgentCapability

AGENT_CAPABILITIES: list[AgentCapability] = [
    SCHEDULED_TASKS_CAPABILITY,
    ARTIFACTS_CAPABILITY,
]


@dataclass(frozen=True)
class CapabilityToolSet:
    """Both views of the same per-op AgentTools.

    ``groups`` is what the main agent sees — one DeferredToolGroup per
    capability, schemas hidden until ``load_tools`` is called. ``flat_tools``
    is the union of every per-op tool across every group, ready to be
    passed eagerly to a subagent's ``shared_tools``. The two views reference
    the same AgentTool instances, so executing through either resolves to
    the same handler closure.
    """

    groups: list[DeferredToolGroup]
    flat_tools: list[AgentTool[Any]]


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
    history_deps: ConversationHistoryDeps | None = None,
) -> CapabilityToolSet:
    """Build deferred groups + flat per-op tools for all registered capabilities.

    Static capabilities (declared in AGENT_CAPABILITIES) are built
    unconditionally. The skills capability is dynamic: built only when
    skill_deps is supplied, because its handlers must close over run-scoped
    catalog / registry / session.

    Capabilities whose mutation gate drops every operation are omitted
    entirely (e.g. an automated run with a mutation-only capability would
    see no catalog entry and no flat tools for it).
    """
    groups: list[DeferredToolGroup] = []
    flat_tools: list[AgentTool[Any]] = []

    def _add(cap: AgentCapability) -> None:
        tools = build_capability_tools(cap, context_factory, allow_mutations=allow_mutations)
        if not tools:
            return
        groups.append(_make_group(cap, tools))
        flat_tools.extend(tools)

    for cap in AGENT_CAPABILITIES:
        _add(cap)

    if skill_deps is not None:
        _add(build_skills_capability(skill_deps))

    if history_deps is not None:
        _add(build_conversation_history_capability(history_deps))

    return CapabilityToolSet(groups=groups, flat_tools=flat_tools)
