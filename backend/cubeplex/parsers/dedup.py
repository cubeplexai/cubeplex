"""Redis-backed conversation-scoped SHA-256 file_state dedup cache.

Cache key: (conversation_id, sha1(path), options-signature). We hash the path
so raw path separators don't interact badly with Redis SCAN patterns.

TTL: 6 hours of inactivity → auto-expire. We refresh the TTL on every hit
so frequently-reused files don't evict mid-session.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from cubeplex.cache import get_redis
from cubeplex.parsers.schema import ParseOptions

DEDUP_TTL_SECONDS = 6 * 3600
KEY_PREFIX = "parsers:dedup:v1:"


async def hash_bytes(data: bytes) -> str:
    """Compute SHA-256 hex; offload to thread (CPU-bound for large inputs)."""
    return await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest())


def _options_signature(options: ParseOptions) -> str:
    """JSON-serialize range params (sorted) so equivalent options → same key."""
    return json.dumps(
        {"page_range": options.page_range, "line_range": options.line_range},
        sort_keys=True,
    )


def _path_digest(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8"), usedforsecurity=False).hexdigest()


def _key(conversation_id: str, path: str, options: ParseOptions) -> str:
    return f"{KEY_PREFIX}{conversation_id}:{_path_digest(path)}:{_options_signature(options)}"


async def check(
    conversation_id: str,
    path: str,
    options: ParseOptions,
    digest: str,
) -> bool:
    """True if digest matches Redis-cached value (caller emits UnchangedOutput).

    Refreshes the TTL on a hit so active files don't expire mid-session.
    """
    redis = get_redis()
    key = _key(conversation_id, path, options)
    cached = await redis.get(key)
    if cached is None or cached != digest:
        return False
    await redis.expire(key, DEDUP_TTL_SECONDS)
    return True


async def update(
    conversation_id: str,
    path: str,
    options: ParseOptions,
    digest: str,
) -> None:
    redis = get_redis()
    await redis.set(
        _key(conversation_id, path, options),
        digest,
        ex=DEDUP_TTL_SECONDS,
    )
