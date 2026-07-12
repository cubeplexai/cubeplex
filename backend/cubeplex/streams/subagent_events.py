"""Bridge cubepi subagent events into cubeplex's shared SSE queue."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any

from loguru import logger

from cubeplex.agents.stream import convert_agent_event_to_sse

subagent_event_queue: ContextVar[asyncio.Queue[Any] | None] = ContextVar(
    "subagent_event_queue", default=None
)


def map_subagent_event(event: Any) -> list[dict[str, Any]]:
    """Map a cubepi AgentEvent into cubeplex SSE payload dicts."""
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
    try:
        queue.put_nowait(("subagent", agent_id, tagged))
    except asyncio.QueueFull:
        logger.warning("subagent_event_queue full - dropping event for {}", agent_id)
    except Exception as exc:
        logger.debug("subagent_event_queue put failed for {}: {}", agent_id, exc)
