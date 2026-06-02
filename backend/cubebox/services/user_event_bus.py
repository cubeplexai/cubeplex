"""In-process pub/sub for user-scoped async events.

Single-instance only — when cubebox scales horizontally, swap the body for
Redis pub/sub keeping the same publish_local / subscribe interface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cubebox.models.user_event import UserEventType


@dataclass(frozen=True)
class UserEventDTO:
    id: str
    user_id: str
    workspace_id: str | None
    type: UserEventType
    payload: dict[str, Any]
    created_at_iso: str


class UserEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[UserEventDTO]]] = {}

    async def publish_local(self, event: UserEventDTO) -> None:
        """Fan out to live subscribers. Caller is responsible for DB persist."""
        queues = list(self._subscribers.get(event.user_id, ()))
        for q in queues:
            q.put_nowait(event)

    def subscribe(self, user_id: str) -> tuple[asyncio.Queue[UserEventDTO], Callable[[], None]]:
        """Return a (queue, unsubscribe) pair.

        The caller MUST call the returned ``unsubscribe`` callable from a
        ``try/finally`` to avoid leaking the queue into ``_subscribers``.

        Queue.get() is a plain coroutine that works safely with
        asyncio.wait_for, unlike async_generator.__anext__ on Python 3.13+.
        """
        q: asyncio.Queue[UserEventDTO] = asyncio.Queue()
        self._subscribers.setdefault(user_id, set()).add(q)

        def unsubscribe() -> None:
            bucket = self._subscribers.get(user_id)
            if bucket is not None:
                bucket.discard(q)
                if not bucket:
                    del self._subscribers[user_id]

        return q, unsubscribe
