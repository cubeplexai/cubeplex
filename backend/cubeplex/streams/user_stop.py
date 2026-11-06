"""Append the user-stop marker through the public checkpointer API."""

from __future__ import annotations

from cubeloop.checkpointer.exceptions import RunAlreadyCompletedError
from cubeloop.providers.base import Message
from loguru import logger

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.agents.user_stop import user_stop_message, user_stop_needed


async def mark_user_stopped_run(conversation_id: str, run_id: str) -> bool:
    """Append an empty aborted assistant for a run the user stopped.

    Returns True when a marker was written. Returns False when this run
    does not need one, or when the run is already complete and the
    checkpointer rejects another append. Other errors propagate so the
    caller can log them without failing the stop.
    """
    async with shared_checkpointer() as cp:
        data = await cp.load(conversation_id)
        messages = list(data.messages) if data is not None else []
        if not user_stop_needed(messages, run_id):
            return False
        marker: list[Message] = [user_stop_message(run_id)]
        try:
            await cp.append(conversation_id, marker)
        except RunAlreadyCompletedError:
            logger.warning(
                "user-stop marker skipped; run already complete conversation={} run={}",
                conversation_id,
                run_id,
            )
            return False
    return True
