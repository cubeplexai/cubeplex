"""Module-level accessor for the shared async Redis client.

cubeplex constructs a single async Redis client in app lifespan
(see api/app.py for the streaming feature). This module exposes that
SAME client to:

* non-route code (parsers/dedup, future filebox indexer) via
  ``get_redis()`` — uses a module-level reference set at lifespan.
* routes via ``redis_dep`` — a FastAPI dependency that pulls the
  client off ``request.app.state.redis``; prefer this over reading
  ``app.state.redis`` directly in route handlers.

Both paths return the same object; no second connection is opened.
"""

from __future__ import annotations

from dataclasses import dataclass

import redis.asyncio as redis_asyncio
from fastapi import Request

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
            "cubeplex.cache.get_redis() called before lifespan set the client. "
            "Either app startup is incomplete or test fixture didn't inject one."
        )
    return _client


def reset_for_tests() -> None:
    """Tests call this between cases to clear the registered client."""
    global _client
    _client = None


@dataclass(slots=True)
class RedisHandle:
    """Bundle of (client, key_prefix) so routes don't need to read app.state twice."""

    client: redis_asyncio.Redis
    key_prefix: str


def redis_dep(request: Request) -> RedisHandle:
    """FastAPI dependency: the canonical Redis accessor for route handlers."""
    return RedisHandle(
        client=request.app.state.redis,
        key_prefix=request.app.state.redis_key_prefix,
    )


__all__ = [
    "RedisHandle",
    "get_redis",
    "redis_dep",
    "reset_for_tests",
    "set_redis",
]
