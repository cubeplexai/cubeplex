"""TimestampMiddleware — stamps created_at on AIMessage and ToolMessage.

Ensures all messages stored by the LangGraph checkpointer carry accurate
created_at timestamps in response_metadata so that reasoning duration and
tool call duration survive page refreshes.

AIMessage.created_at is stamped when the model finishes responding (after
reasoning + token generation).  reasoning_duration_ms, if already present
in response_metadata (set by the LLM client's chunk-level tracking), is
left untouched.

ToolMessage.created_at is stamped when the tool finishes executing.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command


class TimestampMiddleware(AgentMiddleware[Any, Any, Any]):
    """Add created_at timestamps to AIMessage and ToolMessage."""

    tools: Sequence[BaseTool] = []

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any] | AIMessage:
        response = await handler(request)
        ts = datetime.now(UTC).isoformat()
        for msg in response.result:
            if isinstance(msg, AIMessage):
                if not msg.response_metadata:
                    msg.response_metadata = {}
                msg.response_metadata.setdefault("created_at", ts)
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        started_at = datetime.now(UTC).isoformat()
        result = await handler(request)
        if isinstance(result, ToolMessage):
            if not result.response_metadata:
                result.response_metadata = {}
            result.response_metadata.setdefault("tool_started_at", started_at)
            result.response_metadata.setdefault("created_at", datetime.now(UTC).isoformat())
        return result
