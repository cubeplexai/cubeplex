"""Redis-backed conversation-scoped SHA-256 file_state dedup cache.

Cache key: (conversation_id, path, options-signature). The options-signature
includes page_range + line_range so different range-slices land in different
cache slots and don't incorrectly return UnchangedOutput.

TTL: 6 hours of inactivity → auto-expire (Redis-managed; conversation has
no explicit "end" event in cubebox).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from uuid import UUID

from cubebox.cache import get_redis
from cubebox.parsers.schema import ParseOptions

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


def _key(conversation_id: UUID, path: str, options: ParseOptions) -> str:
    return f"{KEY_PREFIX}{conversation_id}:{path}:{_options_signature(options)}"


async def check(
    conversation_id: UUID,
    path: str,
    options: ParseOptions,
    digest: str,
) -> bool:
    """True if digest matches Redis-cached value (caller emits UnchangedOutput)."""
    redis = get_redis()
    cached = await redis.get(_key(conversation_id, path, options))
    if cached is None:
        return False
    if isinstance(cached, bytes):
        return cached == digest.encode()
    return bool(cached == digest)


async def update(
    conversation_id: UUID,
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
