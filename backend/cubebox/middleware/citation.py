"""CitationMiddleware — cubepi port of CitationMiddleware (M3.a.3).

Implements the cubepi ``Middleware`` protocol with a single hook:

- ``after_tool_call``: after each tool returns, scans the tool result content
  for citation patterns per ``CitationConfig``, then returns an
  ``AfterToolCallResult`` carrying extracted citation data in ``details``.

The pure helpers (``chunk_text``, ``CitationCounter``, ``CitationConfig``,
``_extract_text_content``) are re-used unchanged from the LangChain path.

Citations land at ``AfterToolCallResult.details["citations"]`` (a list of
citation dicts).  The cubepi agent loop merges this into the final
``AgentToolResult.details`` so downstream stream handlers can emit
``citation`` SSE events.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cubepi.agent.types import AfterToolCallContext, AfterToolCallResult
from cubepi.middleware.base import Middleware
from loguru import logger

from cubebox.middleware.citations.chunker import chunk_text
from cubebox.middleware.citations.config import CitationConfig
from cubebox.middleware.citations.counter import citation_counter_var, citation_event_queue


def _extract_text_content(content: list[Any]) -> str:
    """Extract plain text from a cubepi ``Content`` list.

    cubepi content items are ``TextContent(text=...)`` objects (or dicts with
    ``type="text"`` from MCP adapters).  The function mirrors the behaviour of
    the langchain helper of the same name in ``citations/middleware.py``.
    """
    texts: list[str] = []
    for block in content:
        # cubepi TextContent pydantic model
        if hasattr(block, "text"):
            texts.append(str(block.text))
        # raw dict form (MCP content blocks)
        elif isinstance(block, dict) and block.get("type") == "text":
            texts.append(block.get("text", ""))
        else:
            texts.append(str(block))
    return "\n".join(texts)


class CitationMiddleware(Middleware):
    """Intercepts tool results to chunk text and assign citation IDs.

    For tools with citation configuration (keyed by tool name in
    ``citation_configs``):
    - Parses tool output and extracts result items
    - Chunks text into ~200-300 char segments
    - Assigns session-level incrementing citation IDs
    - Returns ``AfterToolCallResult(details={"citations": [...]})`` so the
      agent loop merges citation data into ``AgentToolResult.details``

    For tools without a matching config the hook returns ``None`` (pass-through).

    The LLM-visible content rewrite (【N-M】 markers) is **not** applied here
    because the cubepi hook cannot mutate the already-committed ``content``
    field of ``AgentToolResult`` mid-stream.  The citation data is available
    in ``details`` for the SSE stream handler to emit citation events.
    """

    def __init__(
        self,
        *,
        citation_configs: dict[str, CitationConfig],
        event_queue: asyncio.Queue[Any] | None = None,
    ) -> None:
        self._configs = citation_configs
        self._event_queue = event_queue

    async def after_tool_call(
        self,
        ctx: AfterToolCallContext,
        *,
        signal: Any = None,
    ) -> AfterToolCallResult | None:
        """Extract citations from a tool result and attach them to details.

        Args:
            ctx: Hook context providing ``tool_call`` (name/id/args) and
                 ``result`` (the ``AgentToolResult`` returned by the tool).
            signal: Optional cancellation signal (unused).

        Returns:
            ``AfterToolCallResult`` with ``details={"citations": [...]}`` when
            citations are extracted, or ``None`` for unrecognised tools.
        """
        del signal  # not used

        tool_name: str = ctx.tool_call.name
        config = self._configs.get(tool_name)
        if config is None:
            return None

        counter = citation_counter_var.get()
        if counter is None:
            logger.warning("CitationMiddleware: no CitationCounter in context, skipping")
            return None

        # Prefer direct queue reference; fall back to ContextVar
        queue = self._event_queue or citation_event_queue.get()
        tool_call_id: str = ctx.tool_call.id
        tool_args: dict[str, Any] = dict(ctx.tool_call.arguments)

        raw_content = _extract_text_content(ctx.result.content)

        try:
            parsed = json.loads(raw_content)
            items = config.extract_items(parsed)
        except (json.JSONDecodeError, TypeError):
            if config.content_field is None and raw_content.strip():
                # Non-JSON output with no content_field — treat raw text as a single item
                items = [{"text": raw_content}]
            else:
                logger.warning("CitationMiddleware: failed to parse JSON for tool '{}'", tool_name)
                return None

        all_citations: list[dict[str, Any]] = []

        for item in items:
            citation_id = await counter.next()
            metadata = config.extract_metadata(item, tool_args=tool_args)
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

        if not all_citations:
            return None

        logger.info(
            "CitationMiddleware: tool='{}' emitted {} citations",
            tool_name,
            len(all_citations),
        )

        return AfterToolCallResult(details={"citations": all_citations})
