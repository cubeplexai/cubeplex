"""Startup recovery for runs stranded by a crashed / killed process.

Called once during app lifespan, after Redis and DB are ready but before
the app begins serving requests (before ``yield``).
"""

from __future__ import annotations

from loguru import logger
from redis.asyncio import Redis

from cubeplex.streams.run_events import get_run_meta, mark_run_stale


async def recover_stranded_runs(redis: Redis, *, prefix: str) -> int:
    """Scan Redis for stranded active-run keys and clean them up.

    Returns the number of stranded runs recovered.
    """
    pattern = f"{prefix}:conversation_active_run:*"
    prefix_len = len(f"{prefix}:conversation_active_run:")
    recovered: list[tuple[str, str]] = []

    async for key in redis.scan_iter(match=pattern, count=200):
        run_id = await redis.get(key)
        if run_id is None:
            continue
        meta = await get_run_meta(redis, prefix=prefix, run_id=run_id)
        if meta is None or meta.status != "running":
            continue
        conversation_id = key[prefix_len:]
        await mark_run_stale(
            redis,
            prefix=prefix,
            run_id=run_id,
            conversation_id=conversation_id,
        )
        recovered.append((conversation_id, run_id))
        logger.info(
            "Recovered stranded run {} on conversation {}",
            run_id,
            conversation_id,
        )

    if not recovered:
        return 0

    await _stamp_cubepi_runs(recovered)
    await _fail_stranded_scheduled_runs([rid for _, rid in recovered])
    await _repair_stranded_threads([cid for cid, _ in recovered])

    logger.info("Startup recovery: {} stranded run(s) cleaned up", len(recovered))
    return len(recovered)


async def _stamp_cubepi_runs(pairs: list[tuple[str, str]]) -> None:
    """Mark stranded cubepi_runs rows as completed so history is consistent."""
    from cubeplex.agents.checkpointer import shared_checkpointer

    try:
        async with shared_checkpointer() as cp:
            for thread_id, run_id in pairs:
                try:
                    await cp.mark_run_complete(thread_id, run_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to stamp cubepi_runs for {}/{}: {}",
                        thread_id,
                        run_id,
                        exc,
                    )
    except Exception as exc:
        logger.warning("Could not open checkpointer for recovery: {}", exc)


async def _fail_stranded_scheduled_runs(run_ids: list[str]) -> None:
    from cubeplex.schedules.completion_hook import (
        record_scheduled_run_terminal_state,
    )

    for run_id in run_ids:
        try:
            await record_scheduled_run_terminal_state(run_id=run_id, run_status="cancelled")
        except Exception as exc:
            logger.warning(
                "Failed to mark scheduled run {} as failed: {}",
                run_id,
                exc,
            )


async def _repair_stranded_threads(conversation_ids: list[str]) -> None:
    from cubeplex.streams.run_manager import _repair_dangling_tool_calls

    for conv_id in conversation_ids:
        try:
            await _repair_dangling_tool_calls(conv_id)
        except Exception as exc:
            logger.warning(
                "Failed to repair dangling tool_calls for {}: {}",
                conv_id,
                exc,
            )
