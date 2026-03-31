"""SubAgentMiddleware — delegates tasks to ephemeral subagents."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import Any, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from loguru import logger
from pydantic import BaseModel

from cubebox.middleware._utils import append_to_system_message
from cubebox.prompts.subagents import SUBAGENT_PROMPT

# Queue for forwarding subagent streaming events to the SSE generator.
# Set per-request in the SSE event_generator; read inside _run_task.
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


class _TaskSchema(BaseModel):
    description: str
    subagent_type: str = "general-purpose"


def _create_task_tool(
    subagents: list[SubAgent],
    default_model: BaseChatModel | None = None,
) -> BaseTool:
    """Build the `task` tool that spawns subagent runs."""

    subagent_map: dict[str, SubAgent] = {s["name"]: s for s in subagents}
    # Always register general-purpose if not present
    if "general-purpose" not in subagent_map:
        subagent_map["general-purpose"] = SubAgent(
            name="general-purpose",
            description="A general-purpose AI assistant",
            system_prompt="You are a helpful AI assistant.",
        )

    available = ", ".join(f'"{k}"' for k in subagent_map)

    async def _run_task(description: str, subagent_type: str = "general-purpose") -> str:
        spec = subagent_map.get(subagent_type, subagent_map["general-purpose"])
        model = spec.get("model") or default_model
        if model is None:
            return f"[error: no model available for subagent '{subagent_type}']"

        tools: list[BaseTool] = list(spec.get("tools", []))
        middleware = list(spec.get("middleware", []))

        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=spec.get("system_prompt", ""),
            middleware=middleware,
        )

        queue = subagent_event_queue.get(None)

        try:
            if queue is not None:
                # Stream mode: forward tokens to SSE via queue, collect result
                sa_agent_id = f"subagent:{subagent_type}"
                last_ai_content: list[str] = []

                async for chunk in agent.astream(
                    {"messages": [{"role": "user", "content": description}]},
                    stream_mode="messages",
                ):
                    await queue.put(("subagent", sa_agent_id, chunk))

                    # Collect AI content for the tool return value
                    if isinstance(chunk, tuple) and len(chunk) >= 2:
                        msg = chunk[0]
                        c = getattr(msg, "content", "") or ""
                        name = getattr(msg, "name", None)
                        # Skip tool messages (they have a name attribute)
                        if c and not name:
                            last_ai_content.append(c)

                return "".join(last_ai_content) or "[subagent produced no output]"
            else:
                # No queue: use ainvoke (no streaming needed)
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": description}]},
                )
                messages = result.get("messages", [])
                last = messages[-1] if messages else None
                if last and hasattr(last, "content"):
                    content = last.content
                    return content if isinstance(content, str) else str(content)
                return "[subagent produced no output]"
        except Exception as e:
            logger.error("Subagent '{}' failed: {}", subagent_type, e)
            return f"[error: {e}]"

    return StructuredTool.from_function(
        coroutine=_run_task,
        name="task",
        description=(
            f"Delegate a task to a subagent. Available subagent types: {available}. "
            "Provide a self-contained description — the subagent has no conversation context."
        ),
        args_schema=_TaskSchema,
    )


class SubAgentMiddleware(AgentMiddleware[Any, Any, Any]):
    """Registers the task tool that spawns ephemeral subagents."""

    def __init__(
        self,
        *,
        subagents: list[SubAgent],
        default_model: BaseChatModel | None = None,
    ) -> None:
        self._subagents = subagents
        self._default_model = default_model
        self.tools: Sequence[BaseTool] = [_create_task_tool(subagents, default_model)]

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        new_system = append_to_system_message(request.system_message, SUBAGENT_PROMPT)
        return await handler(request.override(system_message=new_system))
