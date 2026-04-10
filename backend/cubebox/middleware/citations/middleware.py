"""CitationMiddleware — chunks tool results and assigns citation IDs."""

import json
from collections.abc import Awaitable, Callable, Sequence
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
from loguru import logger

from cubebox.middleware._utils import append_to_system_message
from cubebox.middleware.citations.chunker import chunk_text
from cubebox.middleware.citations.config import CitationConfig
from cubebox.middleware.citations.counter import citation_counter_var, citation_event_queue
from cubebox.prompts.citations import CITATION_PROMPT


class CitationMiddleware(AgentMiddleware[Any, Any, Any]):
    """Intercepts tool results to chunk text and assign citation IDs.

    For tools with citation configuration:
    - Parses tool output and extracts result items
    - Chunks text into ~200-300 char segments
    - Assigns session-level incrementing citation IDs
    - Rewrites ToolMessage.content with 【N-M】 markers for LLM
    - Preserves original content in additional_kwargs for frontend
    - Pushes citation events to the SSE event queue

    For tools without citation configuration: passes through unchanged.
    """

    tools: Sequence[BaseTool] = []

    def __init__(self, *, citation_configs: dict[str, CitationConfig]) -> None:
        self._configs = citation_configs

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)

        if not isinstance(result, ToolMessage):
            return result

        tool_name = request.tool_call.get("name", "")
        config = self._configs.get(tool_name)
        if config is None:
            return result

        counter = citation_counter_var.get()
        if counter is None:
            logger.warning("CitationMiddleware: no CitationCounter in context, skipping")
            return result

        queue = citation_event_queue.get()
        tool_call_id = request.tool_call.get("id", "")

        try:
            raw_content = result.content if isinstance(result.content, str) else str(result.content)
            parsed = json.loads(raw_content)
            items = config.extract_items(parsed)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                "CitationMiddleware: failed to parse output for tool '{}': {}", tool_name, e
            )
            return result

        chunks_for_llm: list[str] = []

        for item in items:
            citation_id = await counter.next()
            metadata = config.extract_metadata(item)
            text = config.extract_text(item)
            chunks = chunk_text(text)

            if not chunks:
                continue

            citation_data: dict[str, Any] = {
                "citation_id": citation_id,
                "chunks": [{"chunk_index": i, "content": c} for i, c in enumerate(chunks)],
                "metadata": metadata,
                "tool_call_id": tool_call_id,
            }

            if queue is not None:
                await queue.put(("citation", None, citation_data))

            for i, c in enumerate(chunks):
                chunks_for_llm.append(f"【{citation_id}-{i}】 {c}")

        if chunks_for_llm:
            result.additional_kwargs["original_content"] = raw_content
            result.content = "\n\n".join(chunks_for_llm)

        return result

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        if not self._configs:
            return await handler(request)
        new_system = append_to_system_message(request.system_message, CITATION_PROMPT)
        return await handler(request.override(system_message=new_system))
