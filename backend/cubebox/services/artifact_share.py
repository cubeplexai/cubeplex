"""Mint and resolve public artifact share tokens.

Extracted as a service so two consumers can use it: the workspace-scoped
HTTP route (``require_member``) AND the IM outbound tailer (a background
task with no user session). The tailer cannot call the HTTP route — there
is no auth context — so anything route-only would have stranded the IM
artifact-link path.
"""

from __future__ import annotations

import secrets

import orjson
from redis.asyncio import Redis

# Default share-link lifetime. Long enough for a chat conversation, short
# enough that a leaked link is bounded (≤ 1 week).
SHARE_TTL_SECONDS = 60 * 60 * 24 * 7


async def mint_share_token(
    *,
    redis: Redis,
    key_prefix: str,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    artifact_id: str,
    version: int,
    ttl_seconds: int = SHARE_TTL_SECONDS,
) -> str:
    """Return a fresh nonce that maps to the artifact reference in Redis.

    Callers compose the public URL themselves
    (``{base}/api/v1/public/artifacts/share/{nonce}``).
    """
    nonce = secrets.token_hex(32)
    payload = orjson.dumps(
        {
            "org_id": org_id,
            "workspace_id": workspace_id,
            "conversation_id": conversation_id,
            "artifact_id": artifact_id,
            "version": version,
        }
    )
    key = f"{key_prefix}:share:{nonce}"
    await redis.set(key, payload, ex=ttl_seconds)
    return nonce


async def resolve_share_token(
    *,
    redis: Redis,
    key_prefix: str,
    nonce: str,
) -> dict[str, object] | None:
    """Return the artifact reference for ``nonce``, or None if expired/missing."""
    key = f"{key_prefix}:share:{nonce}"
    raw = await redis.get(key)
    if raw is None:
        return None
    decoded = orjson.loads(raw)
    if not isinstance(decoded, dict):
        return None
    return decoded
