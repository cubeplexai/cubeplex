"""Agent graph factory — builds the cubebox agent using create_agent() + middleware."""

import asyncio
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.config import config as _config
from cubebox.middleware.artifacts import ArtifactMiddleware
from cubebox.middleware.attachments import AttachmentHintMiddleware
from cubebox.middleware.citations import CitationConfig, CitationMiddleware
from cubebox.middleware.sandbox import SandboxMiddleware
from cubebox.middleware.skills import SkillsMiddleware
from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware
from cubebox.middleware.timestamps import TimestampMiddleware
from cubebox.middleware.todo import TodoListMiddleware
from cubebox.prompts.system import BASE_SYSTEM_PROMPT
from cubebox.sandbox.base import Sandbox
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillCatalogService
from cubebox.tools.builtin.load_skill import create_load_skill_tool


def create_cubebox_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    sandbox: Sandbox | None = None,
    conversation_id: str | None = None,
    org_id: str | None = None,
    workspace_id: str | None = None,
    catalog_session: AsyncSession | None = None,
    user_id: str | None = None,
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
        org_id: Active org scope (required when catalog_session is provided so the
            skill catalog can be queried).
        workspace_id: Active workspace scope (required when catalog_session is
            provided so the skill catalog can be queried).
        catalog_session: SQLAlchemy AsyncSession used by the SkillCatalogService.
            When omitted, SkillsMiddleware + load_skill are not added (skills are
            simply unavailable for that run).
        user_id: If provided along with conversation_id, CostMiddleware is added.
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
        sandbox_middleware = SandboxMiddleware(
            sandbox=sandbox,
            conversation_id=conversation_id,
        )
        middleware.append(sandbox_middleware)
        inherited_subagent_middleware.append(sandbox_middleware)
        if conversation_id:
            if org_id is None or workspace_id is None:
                raise ValueError(
                    "org_id and workspace_id are required when sandbox + conversation_id "
                    "are provided (needed by ArtifactMiddleware)"
                )
            middleware.append(
                ArtifactMiddleware(
                    sandbox=sandbox,
                    conversation_id=conversation_id,
                    org_id=org_id,
                    workspace_id=workspace_id,
                )
            )
        logger.debug("SandboxMiddleware + ArtifactMiddleware added (sandbox id={})", sandbox.id)

    # Build CostMiddleware before SubAgentMiddleware so it can be inherited by subagents.
    # All four billing dimensions must be present; if any is None, skip silently
    # (callers in test contexts may not provide all params).
    cost_mw = None
    if (
        user_id is not None
        and conversation_id is not None
        and org_id is not None
        and workspace_id is not None
    ):
        from cubebox.middleware.cost import CostMiddleware

        cost_mw = CostMiddleware(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        inherited_subagent_middleware.append(cost_mw)

    # Skill catalog wiring — middleware injects available skills into the
    # system prompt; load_skill is registered as a request-scoped tool.
    if catalog_session is not None and workspace_id is not None and org_id is not None:
        cache_root = Path(_config.get("skills.cache_root", "skills_cache"))
        skill_catalog = SkillCatalogService(
            session=catalog_session, cache=SkillCache(cache_root=cache_root)
        )
        middleware.append(
            SkillsMiddleware(catalog=skill_catalog, workspace_id=workspace_id, org_id=org_id)
        )
        load_skill_tool = create_load_skill_tool(
            catalog=skill_catalog, workspace_id=workspace_id, org_id=org_id
        )
        tools = [*tools, load_skill_tool]

    # view_images — multimodal image-viewing tool, bound per-run to org/workspace scope.
    # Only registered when org_id + workspace_id are available (always true in production
    # runs; may be absent in unit tests that call this factory directly without a request
    # context).
    if org_id is not None and workspace_id is not None:
        from cubebox.llm.capabilities import LLMCapabilities
        from cubebox.llm.config import LLMConfig
        from cubebox.objectstore import get_objectstore_client
        from cubebox.tools.builtin.view_images import make_view_images_tool

        _llm_cfg = LLMConfig(**_config.llm)
        view_images_tool = make_view_images_tool(
            org_id=org_id,
            workspace_id=workspace_id,
            objectstore=get_objectstore_client(),
            capabilities=LLMCapabilities(_llm_cfg),
        )
        tools = [*tools, view_images_tool]

    middleware.append(TodoListMiddleware())
    middleware.append(
        SubAgentMiddleware(
            subagents=subagents or [],
            default_model=llm,
            shared_tools=tools,
            inherited_middleware=inherited_subagent_middleware,
        )
    )

    # Sits innermost (before CostMiddleware) so it's the last layer to modify
    # request.messages before the LLM call — keeps the [Attachments] hint
    # invisible to outer middlewares that scan history.
    middleware.append(AttachmentHintMiddleware())

    # Mount CostMiddleware last in the chain so it wraps all model calls.
    if cost_mw is not None:
        middleware.append(cost_mw)

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
