"""Agent graph factory — builds the cubebox agent using create_agent() + middleware."""

import asyncio
from collections.abc import Callable
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

from cubebox.agents.state import CubeboxState
from cubebox.config import config as _config
from cubebox.middleware.artifacts import ArtifactMiddleware
from cubebox.middleware.attachments import AttachmentHintMiddleware
from cubebox.middleware.citations import CitationConfig, CitationMiddleware
from cubebox.middleware.citations.config import load_builtin_citation_configs
from cubebox.middleware.memory import MemoryMiddleware
from cubebox.middleware.sandbox import SandboxMiddleware
from cubebox.middleware.skills import SkillsMiddleware
from cubebox.middleware.subagents import SubAgent, SubAgentMiddleware
from cubebox.middleware.timestamps import TimestampMiddleware
from cubebox.middleware.todo import TodoListMiddleware
from cubebox.prompts.system import BASE_SYSTEM_PROMPT
from cubebox.repositories.memory import MemoryRepository
from cubebox.sandbox.base import Sandbox
from cubebox.skills.cache import SkillCache
from cubebox.skills.service import SkillCatalogService
from cubebox.tools.builtin.load_skill import create_load_skill_tool


def create_cubebox_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    system_prompt: str = BASE_SYSTEM_PROMPT,
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
    memory_repo_factory: Callable[[], MemoryRepository] | None = None,
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

    # Build sandbox middleware first (if a sandbox is provided) so we can
    # extract citation metadata from its built-in tools (e.g. file_read).
    sandbox_middleware: SandboxMiddleware | None = None
    if sandbox is not None:
        sandbox_middleware = SandboxMiddleware(
            sandbox=sandbox,
            conversation_id=conversation_id,
        )

    # Citation middleware — chunks tool results and assigns citation IDs.
    # Built-in tools (file_read, etc.) carry their citation config on
    # tool.metadata['citation']; merge those alongside any caller-provided configs.
    _citation_configs = dict(citation_configs or {})
    if sandbox_middleware is not None:
        try:
            _citation_configs.update(load_builtin_citation_configs(list(sandbox_middleware.tools)))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load builtin citation configs: {}", exc)
    if _citation_configs:
        citation_middleware = CitationMiddleware(
            citation_configs=_citation_configs,
            event_queue=event_queue,
        )
        middleware.append(citation_middleware)
        inherited_subagent_middleware.append(citation_middleware)

    if sandbox_middleware is not None:
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
                    sandbox=sandbox_middleware.sandbox,
                    conversation_id=conversation_id,
                    org_id=org_id,
                    workspace_id=workspace_id,
                )
            )
        logger.debug(
            "SandboxMiddleware + ArtifactMiddleware added (sandbox id={})",
            sandbox_middleware.sandbox.id,
        )

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

    # MemoryMiddleware — must run before SkillsMiddleware so skills can read
    # memory state. Skipped when no repo factory is supplied (e.g. unit tests
    # that exercise the agent without a DB).
    if memory_repo_factory is not None:
        middleware.append(MemoryMiddleware(repo_factory=memory_repo_factory))

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

    if _config.get("compaction.enabled", False):
        from cubebox.llm.factory import LLMFactory
        from cubebox.middleware.compaction import CompactionMiddleware

        try:
            summary_provider = _config.get("compaction.summary_provider")
            summary_model_id = _config.get("compaction.summary_model")
            factory = LLMFactory()
            summary_llm = factory.create(
                provider_name=summary_provider,
                model_id=summary_model_id,
            )
            # LLMFactory.create() attaches _cubebox_provider/_cubebox_model_id but
            # NOT context_window — look up the real window via the model config so
            # smaller models (16k/32k) don't fall through to the 64k fallback and
            # silently overflow before compaction triggers.
            ctx_window: int | None = None
            main_provider = getattr(llm, "_cubebox_provider", None)
            main_model_id = getattr(llm, "_cubebox_model_id", None)
            if main_provider and main_model_id:
                try:
                    ctx_window = factory.get_model_config(
                        main_provider, main_model_id
                    ).context_window
                except Exception as cfg_exc:  # noqa: BLE001
                    logger.debug(
                        "Could not resolve context_window for {}/{}: {}",
                        main_provider,
                        main_model_id,
                        cfg_exc,
                    )
            if not ctx_window:
                ctx_window = int(_config.get("compaction.fallback_context_window", 64000))
            ratio = float(_config.get("compaction.threshold_ratio", 0.7))
            middleware.append(
                CompactionMiddleware(
                    summary_llm=summary_llm,
                    max_tokens_before_compact=int(ctx_window * ratio),
                    keep_recent_messages=int(_config.get("compaction.keep_recent_messages", 8)),
                    max_summary_tokens=int(_config.get("compaction.max_summary_tokens", 1024)),
                    min_compact_messages=int(_config.get("compaction.min_compact_messages", 4)),
                )
            )
            logger.info(
                "CompactionMiddleware enabled (threshold={} tokens, keep_recent={})",
                int(ctx_window * ratio),
                _config.get("compaction.keep_recent_messages", 8),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("CompactionMiddleware not loaded ({}); proceeding without it", exc)

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
        system_prompt=system_prompt,
        middleware=middleware,
        state_schema=CubeboxState,
        checkpointer=checkpointer,
    )

    # Enable graceful tool error handling: return error messages to the LLM
    # instead of crashing the entire agent stream on a single tool failure.
    tools_pregel = agent.nodes.get("tools")
    if tools_pregel and hasattr(tools_pregel.bound, "_handle_tool_errors"):
        tools_pregel.bound._handle_tool_errors = True

    return agent
