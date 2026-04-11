"""CitationMiddleware — chunks tool results and assigns citation IDs."""

import asyncio
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


def _extract_text_content(content: Any) -> str:
    """Extract text from ToolMessage content, handling both str and content-block formats.

    MCP tools via langchain-mcp-adapters return content as a list of content blocks
    like [{"type": "text", "text": "..."}]. Using str() on such a list produces Python
    repr with single quotes, which is not valid JSON. This helper extracts the actual
    text payload.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Content blocks: [{"type": "text", "text": "..."}, ...]
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        if texts:
            return "\n".join(texts)
    return str(content)


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

    def __init__(
        self,
        *,
        citation_configs: dict[str, CitationConfig],
        event_queue: asyncio.Queue[Any] | None = None,
    ) -> None:
        self._configs = citation_configs
        self._event_queue = event_queue

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

        # Prefer direct queue reference; fall back to ContextVar
        queue = self._event_queue or citation_event_queue.get()
        tool_call_id = request.tool_call.get("id", "")

        raw_content = _extract_text_content(result.content)

        try:
            parsed = json.loads(raw_content)
            items = config.extract_items(parsed)
        except (json.JSONDecodeError, TypeError):
            if config.content_field is None and raw_content.strip():
                # Non-JSON output with no content_field — treat raw text as a single item
                items = [{"text": raw_content}]
            else:
                logger.warning("CitationMiddleware: failed to parse JSON for tool '{}'", tool_name)
                return result

        chunks_for_llm: list[str] = []
        all_citations: list[dict[str, Any]] = []

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
            else:
                logger.warning(
                    "CitationMiddleware: no event queue available for citation_id={}",
                    citation_id,
                )
            all_citations.append(citation_data)

            for i, c in enumerate(chunks):
                chunks_for_llm.append(f"【{citation_id}-{i}】 {c}")

        if chunks_for_llm:
            result.additional_kwargs["original_content"] = raw_content
            result.additional_kwargs["citations"] = all_citations
            result.content = "\n\n".join(chunks_for_llm)
            logger.info(
                "CitationMiddleware: tool='{}' emitted {} citations, {} chunks",
                tool_name,
                len(all_citations),
                len(chunks_for_llm),
            )

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
