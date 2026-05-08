"""Checkpointer module for LangGraph conversation persistence."""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from cubebox.config import config

_pool: AsyncConnectionPool[AsyncConnection[DictRow]] | None = None
_saver: AsyncPostgresSaver | None = None


def _build_conn_string() -> str:
    host = config.get("database.host", "localhost")
    port = config.get("database.port", 5432)
    user = config.get("database.user", "postgres")
    password = config.get("database.password", "")
    name = config.get("database.name", "cubebox")
    encoded_password = quote_plus(password)
    return f"postgresql://{user}:{encoded_password}@{host}:{port}/{name}"


async def init_checkpointer() -> AsyncPostgresSaver:
    """Open the shared connection pool and run idempotent setup.

    Called once at application startup from the FastAPI lifespan.
    Cleans up the partial pool on failure so subsequent retries start fresh —
    FastAPI does not call the lifespan shutdown clause when startup raises,
    so transactional cleanup is owned here.
    """
    global _pool, _saver
    if _saver is not None:
        return _saver
    conn_str = _build_conn_string()
    pool_size = int(config.get("database.pool_size", 10))
    pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
        conn_str,
        min_size=1,
        max_size=pool_size,
        # AsyncPostgresSaver issues cross-connection DDL during setup() that
        # conflicts with cached prepared plans; disabling the cache is the
        # documented LangGraph workaround.
        kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
        open=False,
    )
    try:
        await pool.open(wait=True)
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
    except Exception:
        # Best-effort cleanup of the half-built pool; FastAPI's lifespan
        # shutdown clause does not run when startup raises.
        try:
            await pool.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    _pool = pool
    _saver = saver
    logger.info("LangGraph checkpointer initialized (pg pool max_size={})", pool_size)
    return _saver


async def shutdown_checkpointer() -> None:
    """Close the shared connection pool. Called from lifespan shutdown.

    Swallows `CancelledError` from the pool's worker shutdown. Under
    pytest-asyncio per-test fixtures, the finalizer task can be cancelled
    while `_pool.close()` is mid-flight (sentinels enqueued, some workers
    still parked on `q.get()`), and the cancellation surfaces from a worker
    coroutine through `_pool.close()` → `agather` → `wait_for`. We're
    already unwinding the lifespan; let the pool's own close logic run to
    completion and don't propagate the cancel to the rest of teardown.
    Production never triggers this path: real shutdown is uvicorn's signal
    handler waiting for lifespan, not a peer task cancelling the close.
    """
    global _pool, _saver
    if _pool is not None:
        try:
            await _pool.close()
            logger.debug("Checkpointer connection pool closed")
        except asyncio.CancelledError:
            logger.debug(
                "Checkpointer pool close was cancelled mid-shutdown; "
                "ignoring (test-fixture teardown race, harmless)"
            )
    _pool = None
    _saver = None


async def create_checkpointer() -> AsyncPostgresSaver:
    """Return the shared checkpointer.

    Production path: lifespan has already called `init_checkpointer`.
    Fallback (unusual): if called before lifespan startup, we initialize
    on demand so test harnesses that bypass the lifespan still work.
    """
    if _saver is None:
        return await init_checkpointer()
    return _saver
