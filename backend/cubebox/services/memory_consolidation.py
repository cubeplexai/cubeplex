"""Per-conversation background memory consolidation (Layer 2).

A cheap Redis gate (per-conversation run counter + last-consolidated timestamp +
lock) decides when to run a single OneShotLLM pass that distills the
conversation's recent history into the user's personal memory.
"""

from __future__ import annotations

import time
import uuid

from redis.asyncio import Redis

_TTL_S = 7 * 24 * 3600  # keep gate keys ~a week of inactivity


def _k(prefix: str, kind: str, conversation_id: str) -> str:
    return f"{prefix}:memcons:{kind}:{conversation_id}"


async def _counter(redis: Redis, prefix: str, conversation_id: str) -> int:
    raw = await redis.get(_k(prefix, "runs", conversation_id))
    return int(raw) if raw else 0


async def get_last(redis: Redis, prefix: str, conversation_id: str) -> float:
    raw = await redis.get(_k(prefix, "last", conversation_id))
    return float(raw) if raw else 0.0


async def note_run(redis: Redis, prefix: str, conversation_id: str) -> None:
    """Count one finished run for this conversation."""
    key = _k(prefix, "runs", conversation_id)
    await redis.incr(key)
    await redis.expire(key, _TTL_S)


async def should_consolidate(
    redis: Redis,
    prefix: str,
    conversation_id: str,
    *,
    min_hours: float,
    min_runs: int,
) -> bool:
    counter = await _counter(redis, prefix, conversation_id)
    if counter < min_runs:
        return False
    last = await get_last(redis, prefix, conversation_id)
    return (time.time() - last) >= min_hours * 3600


async def acquire_lock(
    redis: Redis, prefix: str, conversation_id: str, *, ttl_s: int
) -> str | None:
    """SET NX a holder token. Returns the token, or None if held."""
    token = uuid.uuid4().hex
    ok = await redis.set(_k(prefix, "lock", conversation_id), token, nx=True, ex=ttl_s)
    return token if ok else None


async def release_lock(redis: Redis, prefix: str, conversation_id: str, token: str) -> None:
    """Release only if we still hold it (compare-and-delete)."""
    key = _k(prefix, "lock", conversation_id)
    cur = await redis.get(key)
    if cur is None:
        return
    cur_str = cur.decode() if isinstance(cur, (bytes, bytearray)) else cur
    if cur_str == token:
        await redis.delete(key)


async def mark_consolidated(
    redis: Redis,
    prefix: str,
    conversation_id: str,
    *,
    cutoff: float,
    consumed: int,
) -> None:
    """High-water-mark: advance last to cutoff and DECRBY the consumed count
    (never reset-to-0), so runs that arrived during the pass stay counted."""
    last_key = _k(prefix, "last", conversation_id)
    await redis.set(last_key, repr(cutoff), ex=_TTL_S)
    if consumed > 0:
        await redis.decrby(_k(prefix, "runs", conversation_id), consumed)
