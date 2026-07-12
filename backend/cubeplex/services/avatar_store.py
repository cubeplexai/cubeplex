"""Object-store helper for materialized avatar PNGs.

Uploads and deletes avatar images from the S3-compatible object store.

The object store bucket is private (a direct URL returns 403), so avatars are
served through an API proxy route (``GET /api/v1/avatar/{user_id}``) — the same
approach attachments use. The DB stores the **object key**
(``avatars/{user_id}.png``) for self-uploaded/generated avatars; the route layer
turns that key into the proxy URL at serialization time. SSO ``picture`` URLs
are external https URLs and are stored verbatim.
"""

from cubeplex.objectstore.client import get_objectstore_client

AVATAR_KEY_PREFIX = "avatars/"


def avatar_object_key(user_id: str) -> str:
    """The object-store key for a user's materialized avatar PNG."""
    return f"avatars/{user_id}.png"


async def save_avatar_png(user_id: str, data: bytes) -> str:
    """Upload a materialized avatar PNG and return its **object key**.

    The caller persists the key in ``users.avatar_url``; the serialization
    layer turns it into a proxy URL for the frontend.
    """
    key = avatar_object_key(user_id)
    await get_objectstore_client().upload_file(key, data, content_type="image/png")
    return key


async def delete_avatar_png(user_id: str) -> None:
    """Delete a materialized avatar PNG from the object store, if present."""
    await get_objectstore_client().delete_file(avatar_object_key(user_id))


def resolve_avatar_url(
    stored: str | None,
    user_id: str,
    updated_at: object | None = None,
) -> str | None:
    """Turn a stored avatar value into a browser-displayable URL.

    - ``None`` → ``None`` (frontend renders the live DiceBear fallback).
    - An object key (``avatars/{...}.png``) → a relative proxy URL
      ``/api/v1/avatar/{user_id}?v=<ts>``. The ``v`` cache-buster is the
      user's ``updated_at`` epoch seconds: the proxy URL is otherwise stable
      across avatar changes (same user_id, same key prefix), so without it
      the browser serves a stale cached PNG after a swap. The frontend
      serves ``/api/*`` via the Next rewrite to the backend, so a relative
      URL resolves same-origin.
    - Anything else (an external https URL, e.g. an SSO ``picture``) → as-is.
    """
    if stored is None:
        return None
    if stored.startswith(AVATAR_KEY_PREFIX):
        ts = _epoch_seconds(updated_at)
        suffix = f"?v={ts}" if ts is not None else ""
        return f"/api/v1/avatar/{user_id}{suffix}"
    return stored


def _epoch_seconds(updated_at: object | None) -> int | None:
    """Best-effort epoch-seconds of a tz-aware datetime; None if unobtainable."""
    if updated_at is None:
        return None
    try:
        from datetime import datetime

        if isinstance(updated_at, datetime):
            return int(updated_at.timestamp())
    except Exception:
        pass
    return None
