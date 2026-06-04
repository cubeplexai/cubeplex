"""CitationMiddleware.

Implements two cubepi ``Middleware`` hooks:

- ``transform_system_prompt``: appends ``CITATION_PROMPT`` to the system
  prompt when any citation configs are registered, instructing the LLM to
  emit ``【N-M】`` markers for the chunks it cites.
- ``after_tool_call``: after each tool returns, scans the tool result
  content per ``CitationConfig``, rewrites ``content`` so each chunk is
  prefixed with ``【N-M】 [meta_header]`` (LLM-visible), and attaches the
  extracted citation data to ``details`` (SSE-visible).

Pure helpers (``chunk_text``, ``CitationCounter``, ``CitationConfig``,
``_extract_text_content``) live alongside the middleware.

Citations land at ``AfterToolCallResult.details["citations"]`` (a list of
citation dicts).  The cubepi agent loop merges this into the final
``AgentToolResult.details`` so downstream stream handlers can emit
``citation`` SSE events.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from cubepi.agent.types import AfterToolCallContext, AfterToolCallResult, AgentContext
from cubepi.middleware.base import Middleware
from cubepi.providers.base import Content, TextContent
from loguru import logger

from cubebox.middleware.citations.chunker import chunk_text
from cubebox.middleware.citations.config import CitationConfig
from cubebox.middleware.citations.counter import citation_counter_var, citation_event_queue
from cubebox.prompts.citations import CITATION_PROMPT


def _extract_text_content(content: list[Any]) -> str:
    """Extract plain text from a cubepi ``Content`` list.

    cubepi content items are ``TextContent(text=...)`` objects (or dicts with
    ``type="text"`` from MCP adapters).
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


def _format_meta_header(metadata: dict[str, Any]) -> str:
    """Render the inline metadata header (url/title/...) shown to the LLM."""
    parts: list[str] = []
    for key, value in metadata.items():
        if key == "source_type":
            continue
        parts.append(f"{key}: {value}")
    return " | ".join(parts)


class CitationMiddleware(Middleware):
    """Intercepts tool results to chunk text and assign citation IDs.

    For tools with citation configuration (keyed by tool name in
    ``citation_configs``):
    - Parses tool output and extracts result items
    - Chunks text into ~200-300 char segments
    - Assigns session-level incrementing citation IDs
    - Rewrites tool result ``content`` so the LLM sees
      ``【N-M】 [meta_header] chunk`` for each chunk
    - Returns ``AfterToolCallResult`` carrying both the rewritten content
      and ``details={"citations": [...]}`` for SSE emission

    For tools without a matching config the hook returns ``None`` (pass-through).
    """

    def __init__(
        self,
        *,
        citation_configs: dict[str, CitationConfig],
        event_queue: asyncio.Queue[Any] | None = None,
    ) -> None:
        self._configs = citation_configs
        self._event_queue = event_queue

    async def transform_system_prompt(
        self,
        system_prompt: str,
        *,
        ctx: AgentContext,
        signal: asyncio.Event | None = None,
    ) -> str:
        """Append CITATION_PROMPT when any citation configs are registered.

        Skipped when ``_configs`` is empty so conversations without
        citation-eligible tools don't pay the prompt-cache cost.
        """
        del ctx, signal  # not used
        if not self._configs:
            return system_prompt
        return system_prompt + "\n\n" + CITATION_PROMPT

    async def after_tool_call(
        self,
        ctx: AfterToolCallContext,
        *,
        signal: asyncio.Event | None = None,
    ) -> AfterToolCallResult | None:
        """Extract citations from a tool result and inject markers.

        Args:
            ctx: Hook context providing ``tool_call`` (name/id/args) and
                 ``result`` (the ``AgentToolResult`` returned by the tool).
            signal: Optional cancellation signal (unused).

        Returns:
            ``AfterToolCallResult`` with rewritten ``content`` and
            ``details={"citations": [...]}`` when citations are extracted,
            or ``None`` for unrecognised tools / empty results.
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

        if config.content_type == "text":
            items = [{"text": raw_content}] if raw_content.strip() else []
        else:
            try:
                parsed = json.loads(raw_content)
                items = config.extract_items(parsed)
            except (json.JSONDecodeError, TypeError):
                if config.content_field is None and raw_content.strip():
                    # Non-JSON output with no content_field — treat raw text as a single item
                    items = [{"text": raw_content}]
                else:
                    logger.warning(
                        "CitationMiddleware: failed to parse JSON for tool '{}'", tool_name
                    )
                    return None

        all_citations: list[dict[str, Any]] = []
        chunks_for_llm: list[str] = []

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

            meta_header = _format_meta_header(metadata)
            for i, c in enumerate(chunks):
                if i == 0 and meta_header:
                    chunks_for_llm.append(f"【{citation_id}-{i}】 [{meta_header}] {c}")
                else:
                    chunks_for_llm.append(f"【{citation_id}-{i}】 {c}")

        if not all_citations:
            return None

        new_content: list[Content] = [TextContent(text="\n\n".join(chunks_for_llm))]

        logger.info(
            "CitationMiddleware: tool='{}' emitted {} citations, {} chunks",
            tool_name,
            len(all_citations),
            len(chunks_for_llm),
        )

        return AfterToolCallResult(
            content=new_content,
            details={
                "citations": all_citations,
                # Side channel for the SSE/frontend path: the raw tool output
                # before chunk rewriting, so previews (SearchResultView etc.)
                # still see parseable JSON. The LLM-visible content above is
                # the 【N-M】-marked chunk text. See `_stringify_tool_result`.
                "original_content": raw_content,
            },
        )
