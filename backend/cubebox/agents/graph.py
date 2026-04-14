"""Agent graph factory — builds the cubebox agent using create_agent() + middleware."""

import asyncio
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from loguru import logger

from cubebox.middleware.artifacts import ArtifactMiddleware
from cubebox.middleware.citations import CitationConfig, CitationMiddleware
from cubebox.middleware.sandbox import SandboxMiddleware
from cubebox.middleware.skills import SkillsMiddleware, SkillSpec
from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware
from cubebox.middleware.timestamps import TimestampMiddleware
from cubebox.middleware.todo import TodoListMiddleware
from cubebox.prompts.system import BASE_SYSTEM_PROMPT
from cubebox.sandbox.base import Sandbox


def create_cubebox_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    sandbox: Sandbox | None = None,
    conversation_id: str | None = None,
    skills: list[SkillSpec] | None = None,
    subagents: list[SubAgent] | None = None,
    checkpointer: Checkpointer | None = None,
    citation_configs: dict[str, CitationConfig] | None = None,
    event_queue: asyncio.Queue[Any] | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the cubebox agent with the configured middleware stack.

    Returns a CompiledStateGraph (LangGraph) that supports .astream(),
    .ainvoke(), and checkpointer-based thread persistence.

    Args:
        llm: The language model to use.
        tools: Additional tools beyond what middleware provides.
        sandbox: If provided, SandboxMiddleware is added (registers execute tool).
        conversation_id: Required when sandbox is provided; used by ArtifactMiddleware.
        skills: If provided, SkillsMiddleware is added.
        subagents: If provided, SubAgentMiddleware is added.
        checkpointer: LangGraph checkpointer for conversation persistence.
    """
    middleware: list[AgentMiddleware[Any, Any]] = []
    inherited_subagent_middleware: list[AgentMiddleware[Any, Any]] = []

    middleware.append(TimestampMiddleware())

    # Citation middleware — chunks tool results and assigns citation IDs
    _citation_configs = citation_configs or {}
    if _citation_configs:
        citation_middleware = CitationMiddleware(
            citation_configs=_citation_configs,
            event_queue=event_queue,
        )
        middleware.append(citation_middleware)
        inherited_subagent_middleware.append(citation_middleware)

    if sandbox is not None:
        sandbox_middleware = SandboxMiddleware(sandbox=sandbox)
        middleware.append(sandbox_middleware)
        inherited_subagent_middleware.append(sandbox_middleware)
        if conversation_id:
            middleware.append(ArtifactMiddleware(sandbox=sandbox, conversation_id=conversation_id))
        logger.debug("SandboxMiddleware + ArtifactMiddleware added (sandbox id={})", sandbox.id)

    _skills = skills or []
    middleware.append(SkillsMiddleware(skills=_skills))
    middleware.append(TodoListMiddleware())
    middleware.append(
        SubAgentMiddleware(
            subagents=subagents or [],
            default_model=llm,
            shared_tools=tools,
            inherited_middleware=inherited_subagent_middleware,
        )
    )

    logger.info(
        "Creating cubebox agent: {} tools, {} middleware",
        len(tools),
        len(middleware),
    )

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=BASE_SYSTEM_PROMPT,
        middleware=middleware,
        checkpointer=checkpointer,
    )

    # Enable graceful tool error handling: return error messages to the LLM
    # instead of crashing the entire agent stream on a single tool failure.
    tools_pregel = agent.nodes.get("tools")
    if tools_pregel and hasattr(tools_pregel.bound, "_handle_tool_errors"):
        tools_pregel.bound._handle_tool_errors = True

    return agent
