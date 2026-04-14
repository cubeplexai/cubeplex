"""SubAgentMiddleware — delegates tasks to ephemeral subagents."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import Annotated, Any, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from loguru import logger
from pydantic import BaseModel

from cubebox.middleware._utils import append_to_system_message
from cubebox.middleware.skills import SkillSpec
from cubebox.prompts.subagents import SUBAGENT_PROMPT

# Queue for forwarding subagent streaming events to the SSE generator.
# Set per-request in the SSE event_generator; read inside _run_subagent.
subagent_event_queue: ContextVar[asyncio.Queue[Any] | None] = ContextVar(
    "subagent_event_queue", default=None
)


class SubAgent(TypedDict, total=False):
    """Specification for a subagent.

    Required keys: name, description, system_prompt
    Optional keys: tools, model, middleware
    """

    name: str  # required
    description: str  # required
    system_prompt: str  # required
    tools: list[BaseTool]
    model: BaseChatModel
    middleware: list[Any]


class _SubAgentSchema(BaseModel):
    name: str
    role: str
    task: str
    prompt: str
    subagent_type: str = "general-purpose"


def _collect_citations_from_update(
    update: Any,
    out: list[dict[str, Any]],
) -> None:
    """Extract citation data from ToolMessages in an updates stream chunk."""
    if not isinstance(update, dict):
        return
    for _node_name, state_update in update.items():
        messages = state_update.get("messages", []) if isinstance(state_update, dict) else []
        for msg in messages:
            additional_kwargs = (
                msg.get("additional_kwargs", {})
                if isinstance(msg, dict)
                else getattr(msg, "additional_kwargs", {}) or {}
            )
            citations = additional_kwargs.get("citations")
            if citations:
                out.extend(citations)


def _create_subagent_tool(
    subagents: list[SubAgent],
    default_model: BaseChatModel | None = None,
    shared_tools: list[BaseTool] | None = None,
    inherited_middleware: list[Any] | None = None,
) -> BaseTool:
    """Build the `subagent` tool that spawns subagent runs."""

    subagent_map: dict[str, SubAgent] = {s["name"]: s for s in subagents}
    # Always register general-purpose if not present
    if "general-purpose" not in subagent_map:
        subagent_map["general-purpose"] = SubAgent(
            name="general-purpose",
            description="A general-purpose AI assistant",
            system_prompt="You are a helpful AI assistant.",
        )

    # Shared tools excluding the subagent tool itself (no recursive spawning).
    _excluded = {"subagent", "load_skill"}
    _shared_tools = [t for t in (shared_tools or []) if t.name not in _excluded]

    available = ", ".join(f'"{k}"' for k in subagent_map)

    async def _run_subagent(
        name: str,
        role: str,
        task: str,
        prompt: str,
        subagent_type: str = "general-purpose",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str | ToolMessage:
        spec = subagent_map.get(subagent_type, subagent_map["general-purpose"])
        model = spec.get("model") or default_model
        if model is None:
            return f"[error: no model available for subagent '{subagent_type}']"

        tools: list[BaseTool] = _shared_tools + list(spec.get("tools", []))
        middleware = list(inherited_middleware or []) + list(spec.get("middleware", []))

        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=spec.get("system_prompt", ""),
            middleware=middleware,
        )

        queue = subagent_event_queue.get(None)

        try:
            from cubebox.agents.stream import (
                convert_messages_chunk,
                convert_updates_chunk,
            )

            sa_agent_id = f"subagent:{tool_call_id}"
            last_ai_content: list[str] = []
            subagent_events: list[dict[str, Any]] = []
            collected_citations: list[dict[str, Any]] = []

            if queue is not None:
                # Dual stream mode: forward events to SSE via queue, collect result
                async for event in agent.astream(
                    {"messages": [{"role": "user", "content": prompt}]},
                    stream_mode=["messages", "updates"],
                ):
                    await queue.put(("subagent", sa_agent_id, event))

                    # Collect events and AI content
                    if isinstance(event, tuple) and len(event) == 2:
                        mode, data = event
                        if mode == "messages":
                            evts = convert_messages_chunk(data, agent_id=sa_agent_id)
                            subagent_events.extend(evts)
                            # Collect AI text content from messages chunks
                            if isinstance(data, tuple) and len(data) >= 2:
                                msg = data[0]
                                c = getattr(msg, "content", "") or ""
                                msg_name = getattr(msg, "name", None)
                                if c and not msg_name:
                                    last_ai_content.append(c)
                        elif mode == "updates":
                            evts = convert_updates_chunk(data, agent_id=sa_agent_id)
                            subagent_events.extend(evts)
                            # Collect citations from inner ToolMessages
                            _collect_citations_from_update(data, collected_citations)
            else:
                # No queue: use ainvoke (no streaming needed)
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": prompt}]},
                )
                messages = result.get("messages", [])
                for m in messages:
                    citations = (getattr(m, "additional_kwargs", None) or {}).get("citations")
                    if citations:
                        collected_citations.extend(citations)
                last = messages[-1] if messages else None
                if last and hasattr(last, "content"):
                    content = last.content
                    last_ai_content.append(content if isinstance(content, str) else str(content))

            final_content = "".join(last_ai_content) or "[subagent produced no output]"

            # Return ToolMessage with events in additional_kwargs
            kwargs: dict[str, Any] = {"subagent_events": subagent_events}
            if collected_citations:
                kwargs["citations"] = collected_citations
            return ToolMessage(
                content=final_content,
                tool_call_id=tool_call_id,
                name="subagent",
                additional_kwargs=kwargs,
            )
        except Exception as e:
            logger.error("Subagent '{}' failed: {}", subagent_type, e)
            return f"[error: {e}]"

    return StructuredTool.from_function(
        coroutine=_run_subagent,
        name="subagent",
        description=(
            f"Delegate a task to a subagent. Available subagent types: {available}. "
            "Provide a name (short label), role (subagent's expertise), task (what to do), "
            "and a self-contained prompt — the subagent has no conversation context."
        ),
        args_schema=_SubAgentSchema,
    )


class SubAgentMiddleware(AgentMiddleware[Any, Any, Any]):
    """Registers the subagent tool that spawns ephemeral subagents."""

    def __init__(
        self,
        *,
        subagents: list[SubAgent],
        default_model: BaseChatModel | None = None,
        shared_tools: list[BaseTool] | None = None,
        shared_skills: list[SkillSpec] | None = None,
        inherited_middleware: list[Any] | None = None,
    ) -> None:
        self._subagents = subagents
        self._default_model = default_model
        self.tools: Sequence[BaseTool] = [
            _create_subagent_tool(
                subagents,
                default_model,
                shared_tools=shared_tools,
                inherited_middleware=inherited_middleware,
            )
        ]

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        new_system = append_to_system_message(request.system_message, SUBAGENT_PROMPT)
        return await handler(request.override(system_message=new_system))
