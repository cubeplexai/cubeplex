"""Bridge cubeloop subagent events into cubeplex's shared SSE queue."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any

from cubeplex.agents.stream import convert_agent_event_to_sse
from cubeplex.streams.execution_adapter import enqueue_host_event

subagent_event_queue: ContextVar[asyncio.Queue[Any] | None] = ContextVar(
    "subagent_event_queue", default=None
)


def map_subagent_event(event: Any) -> list[dict[str, Any]]:
    """Map a cubeloop AgentEvent into cubeplex SSE payload dicts."""
    return convert_agent_event_to_sse(event)


async def forward_subagent_event(agent_id: str, payload: Any) -> None:
    """Tag a mapped subagent payload and enqueue it for live SSE delivery."""
    tagged = payload
    if isinstance(payload, dict):
        payload["agent_id"] = agent_id
        tagged = payload

    queue = subagent_event_queue.get(None)
    if queue is None:
        return
    await enqueue_host_event(queue, ("subagent", agent_id, tagged))
