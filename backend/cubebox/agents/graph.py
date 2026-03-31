"""Agent graph factory — builds the cubebox agent using create_agent() + middleware."""

from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from loguru import logger

from cubebox.middleware.sandbox import SandboxMiddleware
from cubebox.middleware.skills import SkillsMiddleware, SkillSpec
from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware
from cubebox.prompts.system import BASE_SYSTEM_PROMPT
from cubebox.sandbox.base import Sandbox


def create_cubebox_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    sandbox: Sandbox | None = None,
    skills: list[SkillSpec] | None = None,
    subagents: list[SubAgent] | None = None,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Build the cubebox agent with the configured middleware stack.

    Returns a CompiledStateGraph (LangGraph) that supports .astream(),
    .ainvoke(), and checkpointer-based thread persistence.

    Args:
        llm: The language model to use.
        tools: Additional tools beyond what middleware provides.
        sandbox: If provided, SandboxMiddleware is added (registers execute tool).
        skills: If provided, SkillsMiddleware is added.
        subagents: If provided, SubAgentMiddleware is added.
        checkpointer: LangGraph checkpointer for conversation persistence.
    """
    middleware: list[AgentMiddleware[Any, Any]] = []

    if sandbox is not None:
        middleware.append(SandboxMiddleware(sandbox=sandbox))
        logger.debug("SandboxMiddleware added (sandbox id={})", sandbox.id)

    _skills = skills or []
    middleware.append(SkillsMiddleware(skills=_skills))
    middleware.append(
        SubAgentMiddleware(
            subagents=subagents or [],
            default_model=llm,
            shared_tools=tools,
            shared_skills=_skills,
        )
    )

    logger.info(
        "Creating cubebox agent: {} tools, {} middleware",
        len(tools),
        len(middleware),
    )

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=BASE_SYSTEM_PROMPT,
        middleware=middleware,
        checkpointer=checkpointer,
    )
