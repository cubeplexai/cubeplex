"""cubepi-backed Postgres checkpointer for cubebox.

Thin wrapper around ``cubepi.PostgresCheckpointer``. Two access modes:

- ``shared_checkpointer()`` — the process-wide instance backed by one
  asyncpg pool, opened lazily and closed by the app lifespan. All
  request/run hot paths use this; opening a pool per call added a
  TCP+auth+schema-check round trip to every send.
- ``init_checkpointer()`` — owns a private pool for the duration of the
  context. For CLI scripts, workers with their own event loop, and tests
  that need isolation.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote_plus

from cubepi.checkpointer.postgres import PostgresCheckpointer

from cubebox.config import config as _config


def _build_dsn() -> str:
    """Construct the Postgres DSN from cubebox config."""
    host = _config.get("database.host", "localhost")
    port = _config.get("database.port", 5432)
    user = _config.get("database.user", "postgres")
    password = _config.get("database.password", "")
    name = _config.get("database.name", "cubebox")
    encoded_password = quote_plus(password)
    return f"postgresql://{user}:{encoded_password}@{host}:{port}/{name}"


@asynccontextmanager
async def init_checkpointer(
    dsn: str | None = None,
    *,
    min_pool_size: int = 1,
    max_pool_size: int = 10,
) -> AsyncIterator[PostgresCheckpointer]:
    """Open a cubepi.PostgresCheckpointer for cubebox's DB.

    Usage:
        async with init_checkpointer() as cp:
            agent = Agent(..., checkpointer=cp)
            ...

    Args:
        dsn: explicit Postgres DSN; defaults to cubebox config-derived value.
        min_pool_size / max_pool_size: asyncpg pool sizing.

    Yields:
        Open PostgresCheckpointer; schema version verified on entry.
    """
    dsn = dsn or _build_dsn()
    cp = PostgresCheckpointer(
        dsn=dsn,
        min_pool_size=min_pool_size,
        max_pool_size=max_pool_size,
    )
    async with cp:
        yield cp


# asyncpg pools are bound to the event loop that created them. In the app
# there is exactly one loop for the process lifetime, so this is a plain
# process-wide singleton. Tests (anyio) run each case on a fresh loop —
# keying by the running loop keeps a stale pool from a torn-down loop from
# poisoning the next case; entries for dead loops are dropped, not closed
# (their loop is gone; the OS reaps the sockets).
_shared_by_loop: dict[int, PostgresCheckpointer] = {}
_shared_locks: dict[int, asyncio.Lock] = {}


async def get_shared_checkpointer() -> PostgresCheckpointer:
    """Return this loop's shared checkpointer, opening its pool on first use.

    The app lifespan warms it at startup and closes it on shutdown. CLI
    scripts and workers with their own short-lived loops should prefer
    ``init_checkpointer()``.
    """
    loop_id = id(asyncio.get_running_loop())
    cp = _shared_by_loop.get(loop_id)
    if cp is not None:
        return cp
    lock = _shared_locks.setdefault(loop_id, asyncio.Lock())
    async with lock:
        cp = _shared_by_loop.get(loop_id)
        if cp is None:
            cp = PostgresCheckpointer(
                dsn=_build_dsn(),
                min_pool_size=int(_config.get("database.cubepi_pool_min", 1)),
                max_pool_size=int(_config.get("database.cubepi_pool_max", 10)),
            )
            await cp.__aenter__()
            _shared_by_loop[loop_id] = cp
    return cp


@asynccontextmanager
async def shared_checkpointer() -> AsyncIterator[PostgresCheckpointer]:
    """Context-manager view of the shared checkpointer (never closes it).

    Drop-in replacement for hot-path ``async with init_checkpointer()``
    blocks: same usage shape, no per-call pool churn.
    """
    yield await get_shared_checkpointer()


async def close_shared_checkpointer() -> None:
    """Close this loop's shared pool. Called by the app lifespan on shutdown."""
    loop_id = id(asyncio.get_running_loop())
    _shared_locks.pop(loop_id, None)
    cp = _shared_by_loop.pop(loop_id, None)
    if cp is not None:
        await cp.__aexit__(None, None, None)
