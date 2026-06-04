"""Session-level citation ID counter with ContextVar sharing.

The counter is created per-request in the SSE event generator and shared
between the main agent and any subagents via ContextVar inheritance.
"""

import asyncio
import re
from contextvars import ContextVar
from typing import Any

from cubepi.providers.base import Message

# Matches the 【N-M】 markers CitationMiddleware injects into tool result
# content. We only need group 1 (citation_id) to recover the watermark.
_MARKER_RE = re.compile(r"【(\d+)-\d+】")


class CitationCounter:
    """Thread-safe incrementing citation ID counter.

    Uses asyncio.Lock to ensure safe concurrent access from
    the main agent and subagents within the same event loop.
    """

    def __init__(self, start: int = 1) -> None:
        self._next = start
        self._lock = asyncio.Lock()

    async def next(self) -> int:
        """Return the next citation ID and increment the counter."""
        async with self._lock:
            val = self._next
            self._next += 1
            return val

    async def seed_from_messages(self, messages: list[Message]) -> None:
        """Advance ``_next`` past the highest citation id in tool-result history.

        Scans ``ToolResultMessage.content`` for ``【N-M】`` markers and ensures
        the next assigned id is strictly greater than any historical N, so
        cross-turn ids don't collide in the frontend citation store
        (which is keyed by id alone).

        Safe to call before the agent starts; no-op when history has no
        markers or the counter is already ahead.
        """
        max_id = 0
        for msg in messages:
            if getattr(msg, "role", None) != "tool_result":
                continue
            for block in getattr(msg, "content", []) or []:
                text = getattr(block, "text", None)
                if not text:
                    continue
                for match in _MARKER_RE.finditer(text):
                    n = int(match.group(1))
                    if n > max_id:
                        max_id = n
        if max_id == 0:
            return
        async with self._lock:
            if max_id >= self._next:
                self._next = max_id + 1


citation_counter_var: ContextVar[CitationCounter | None] = ContextVar(
    "citation_counter", default=None
)

citation_event_queue: ContextVar[asyncio.Queue[Any] | None] = ContextVar(
    "citation_event_queue", default=None
)
