"""MCP connector template seeder: upserts the v1 templates at startup.

Multi-replica safe via Redis named lock (same pattern as skill_seeder).
"""

from __future__ import annotations

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.credentials.encryption import EncryptionBackend

LOCK_KEY = "cubeplex:lock:mcp_template_seeder"
LOCK_TTL_SECONDS = 60


async def seed_mcp_templates(
    *,
    db_session: AsyncSession,
    backend: EncryptionBackend,
    redis: Redis,
) -> None:
    """Idempotently seed the MCP connector template catalog into the database.

    Multi-replica safe: only one process holding the Redis lock runs the
    seed; others log and return.
    """
    lock = redis.lock(LOCK_KEY, timeout=LOCK_TTL_SECONDS, blocking=False)
    acquired = await lock.acquire()
    if not acquired:
        logger.info("MCP template seeder: lock held by another replica; skipping this run")
        return

    try:
        from cubeplex.mcp.template_seed import seed_templates

        result = await seed_templates(db_session, backend)
        await db_session.commit()
        logger.info(
            "MCP template seed: upserted={} skipped={} deprecated={}",
            result.upserted,
            result.skipped,
            result.deprecated,
        )
        for warning in result.warnings:
            logger.warning("MCP template seed: {}", warning)
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            pass
