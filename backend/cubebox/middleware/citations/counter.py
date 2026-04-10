"""Session-level citation ID counter with ContextVar sharing.

The counter is created per-request in the SSE event generator and shared
between the main agent and any subagents via ContextVar inheritance.
"""

import asyncio
from contextvars import ContextVar
from typing import Any


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


citation_counter_var: ContextVar[CitationCounter | None] = ContextVar(
    "citation_counter", default=None
)

citation_event_queue: ContextVar[asyncio.Queue[Any] | None] = ContextVar(
    "citation_event_queue", default=None
)
