"""MCP catalog seeder: upserts the v1 catalog at startup. Multi-replica safe
via Redis named lock (same pattern as skill_seeder).
"""

from __future__ import annotations

from loguru import logger
from redis.asyncio import Redis
from redis.exceptions import LockNotOwnedError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.credentials.encryption import EncryptionBackend

LOCK_KEY = "cubebox:lock:mcp_catalog_seeder"
LOCK_TTL_SECONDS = 60


async def seed_mcp_catalog(
    *,
    db_session: AsyncSession,
    backend: EncryptionBackend,
    redis: Redis,
) -> None:
    """Idempotently seed the MCP catalog into the database.

    Multi-replica safe: only one process holding the Redis lock runs the seed;
    others log and return.
    """
    lock = redis.lock(LOCK_KEY, timeout=LOCK_TTL_SECONDS, blocking=False)
    acquired = await lock.acquire()
    if not acquired:
        logger.info("MCP catalog seeder: lock held by another replica; skipping this run")
        return

    try:
        from cubebox.mcp.catalog_seed import seed_catalog

        result = await seed_catalog(db_session, backend)
        await db_session.commit()
        logger.info(
            "MCP catalog seed: upserted={} skipped={} deprecated={}",
            result.upserted,
            result.skipped,
            result.deprecated,
        )
        for warning in result.warnings:
            logger.warning("MCP catalog seed: {}", warning)
    finally:
        try:
            await lock.release()
        except LockNotOwnedError:
            pass
