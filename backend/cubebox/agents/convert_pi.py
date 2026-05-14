"""cubepi.Message ↔ cubebox API wire format conversion (M1.2).

Mirrors cubebox/agents/convert.py (LangChain version). Used by the
cubepi-runtime path; M3 will extend with attachment rendering, citations,
etc. once those middlewares are ported.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def _join_text(content: Sequence[Any]) -> str:
    """Concatenate all TextContent text values; ignore non-text blocks."""
    parts = [c.text for c in content if isinstance(c, TextContent)]
    return "".join(parts)


def cubepi_message_to_wire(msg: Message) -> dict[str, Any]:
    """Convert a cubepi.Message into cubebox's API response dict shape."""
    if isinstance(msg, UserMessage):
        return {
            "role": "user",
            "content": _join_text(msg.content),
            "metadata": dict(msg.metadata),
        }

    if isinstance(msg, AssistantMessage):
        tool_calls = [
            {"id": c.id, "name": c.name, "arguments": c.arguments}
            for c in msg.content
            if isinstance(c, ToolCall)
        ]
        meta: dict[str, Any] = dict(msg.metadata)
        if tool_calls:
            meta["tool_calls"] = tool_calls
        meta["usage"] = {
            "input_tokens": msg.usage.input_tokens if msg.usage else 0,
            "output_tokens": msg.usage.output_tokens if msg.usage else 0,
        }
        return {
            "role": "assistant",
            "content": _join_text(msg.content),
            "metadata": meta,
        }

    if isinstance(msg, ToolResultMessage):
        meta = dict(msg.metadata)
        meta["tool_call_id"] = msg.tool_call_id
        meta["tool_name"] = msg.tool_name
        return {
            "role": "tool",
            "content": _join_text(msg.content),
            "metadata": meta,
        }

    raise TypeError(f"unknown cubepi Message type: {type(msg).__name__}")


def wire_input_to_cubepi_user_message(
    text: str,
    *,
    attachments: list[dict[str, Any]] | None = None,
    memory_snapshot: dict[str, Any] | None = None,
) -> UserMessage:
    """Build a cubepi.UserMessage from an API-shaped user input.

    Attachments are stored in metadata for M3's AttachmentMiddleware port
    to render later. M1 doesn't render them — the bare cubepi path sends
    only text.

    memory_snapshot (M3.b.1): when provided, the pre-computed relevance-
    memory snapshot is frozen onto ``metadata["memory_snapshot"]``.  The
    snapshot is computed once at append time by ``compute_relevance_snapshot``
    (never re-derived from the live MemoryItem table) so
    ``MemoryMiddleware.transform_context`` can replay it byte-identically
    on subsequent turns.
    """
    metadata: dict[str, Any] = {}
    if attachments:
        metadata["attachments"] = list(attachments)
    if memory_snapshot is not None:
        metadata["memory_snapshot"] = memory_snapshot
    return UserMessage(
        content=[TextContent(text=text)],
        metadata=metadata,
    )
