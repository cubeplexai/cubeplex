"""Module-level accessor for the shared async Redis client.

cubebox already constructs a single async Redis client in app lifespan
(see api/app.py for the streaming feature). This module exposes that
SAME client to non-route code (parsers/dedup, future filebox indexer)
that doesn't have a Request handle.

Lifespan calls ``set_redis(client)`` after building the connection;
consumers call ``get_redis()``. No second connection is opened.
"""

from __future__ import annotations

import redis.asyncio as redis_asyncio

_client: redis_asyncio.Redis | None = None


def set_redis(client: redis_asyncio.Redis) -> None:
    """Register the shared async Redis client (called by app lifespan)."""
    global _client
    _client = client


def get_redis() -> redis_asyncio.Redis:
    """Return the registered shared async Redis client.

    Raises RuntimeError if called before lifespan registered the client.
    """
    if _client is None:
        raise RuntimeError(
            "cubebox.cache.get_redis() called before lifespan set the client. "
            "Either app startup is incomplete or test fixture didn't inject one."
        )
    return _client


def reset_for_tests() -> None:
    """Tests call this between cases to clear the registered client."""
    global _client
    _client = None


__all__ = ["get_redis", "reset_for_tests", "set_redis"]
