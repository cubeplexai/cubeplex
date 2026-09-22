"""Best-effort runtime wakeups after durable execution control commits."""

import asyncio
import logging

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.streams.run_manager import RunContext, RunManager

logger = logging.getLogger(__name__)


async def signal_stopped_runs(
    run_manager: RunManager,
    *,
    conversation_id: str,
    run_ids: tuple[str, ...],
    user_id: str,
    org_id: str,
    workspace_id: str,
) -> None:
    """Wake active workers without making delivery part of the durable contract."""
    try:
        async with asyncio.timeout(3):
            async with shared_checkpointer() as checkpointer:
                pending_run_id = await checkpointer.load_pending_run_id(conversation_id)
            for run_id in run_ids:
                if run_id == pending_run_id:
                    await run_manager.cancel_paused_run(
                        conversation_id=conversation_id,
                        run_id=run_id,
                        ctx=RunContext(
                            user_id=user_id,
                            org_id=org_id,
                            workspace_id=workspace_id,
                            conversation_id=conversation_id,
                        ),
                    )
                else:
                    await run_manager.notify_run_stop(run_id)
    except Exception:
        logger.warning("Stop signal deferred for conversation %s", conversation_id, exc_info=True)
