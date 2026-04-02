"""Agent graph factory — builds the cubebox agent using create_agent() + middleware."""

from datetime import UTC, datetime
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware.todo import TodoListMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from loguru import logger

from cubebox.middleware.sandbox import SandboxMiddleware
from cubebox.middleware.skills import SkillsMiddleware, SkillSpec
from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware
from cubebox.prompts.system import BASE_SYSTEM_PROMPT
from cubebox.sandbox.base import Sandbox


def _stamp_tool_messages(result: dict[str, Any]) -> dict[str, Any]:
    """Add created_at timestamp to ToolMessages returned by the tools node."""
    messages = result.get("messages")
    if messages:
        ts = datetime.now(UTC).isoformat()
        for msg in messages:
            if isinstance(msg, ToolMessage):
                if not msg.response_metadata:
                    msg.response_metadata = {}
                msg.response_metadata.setdefault("created_at", ts)
    return result


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
    middleware.append(TodoListMiddleware())
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

    # Wrap the tools node to add created_at timestamps to ToolMessages
    if tools_pregel:
        original_ainvoke = tools_pregel.bound.ainvoke

        async def _timestamped_ainvoke(
            input_: Any, config: Any = None, **kwargs: Any
        ) -> dict[str, Any]:
            result = await original_ainvoke(input_, config, **kwargs)
            return _stamp_tool_messages(result)

        tools_pregel.bound.ainvoke = _timestamped_ainvoke  # type: ignore[assignment]

    return agent
