"""cubeplex request DTO → cubepi.UserMessage builder.

The API response side returns cubepi's native message shape directly
(``Message.model_dump(mode="json")``) — there is no cubeplex-specific wire
format. This module only handles the request-body → cubepi conversion,
which has a meaningfully different shape (text + attachment ids) from
the persisted message.
"""

from __future__ import annotations

from typing import Any

from cubepi.providers.base import TextContent, UserMessage


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
