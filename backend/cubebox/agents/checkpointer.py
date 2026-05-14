"""cubepi-backed Postgres checkpointer for cubebox.

Thin wrapper around cubepi.PostgresCheckpointer. Owns the connection
pool lifecycle and exposes a context-manager init for use in cubebox's
agent factory (M1+).

This module is invoked when config.agents.runtime == "cubepi". For
runtime == "langgraph", cubebox/agents/checkpointer.py (the existing
LangGraph AsyncPostgresSaver wrapper) is used instead.
"""

from __future__ import annotations

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
