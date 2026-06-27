"""Object-store helper for materialized avatar PNGs.

Uploads and deletes avatar images from the S3-compatible object store, and
builds the public URL that the frontend uses to display them.

The URL is constructed from the store's endpoint, bucket, and object key
(``{endpoint}/{bucket}/{key}``).  This is the same scheme that
``ObjectStoreClient`` itself uses internally — the class stores
``_endpoint`` and ``_bucket`` for every S3 API call — but unlike
attachments (which proxy through the API server), avatars are served
directly from the object store so the frontend gets a static-image URL
that can be cached and embedded in avatar badges.
"""

from cubebox.objectstore.client import get_objectstore_client


def _avatar_public_url(key: str) -> str:
    """Build the direct public URL for an avatar object key.

    Mirroring the object store client's own endpoint/bucket configuration:
    ``{endpoint}/{bucket}/{key}``.

    There is no existing URL-builder on ``ObjectStoreClient`` — attachments
    use a different scheme (proxied through the cubebox API server) — so
    this helper constructs the URL from the client's private attributes.
    """
    client = get_objectstore_client()
    return f"{client._endpoint}/{client._bucket}/{key}"


async def save_avatar_png(user_id: str, data: bytes) -> str:
    """Upload a materialized avatar PNG and return its public URL.

    Stores the data at the key ``avatars/{user_id}.png`` in the configured
    bucket and returns a URL the frontend can use to display it.
    """
    key = f"avatars/{user_id}.png"
    client = get_objectstore_client()
    await client.upload_file(key, data, content_type="image/png")
    return _avatar_public_url(key)


async def delete_avatar_png(user_id: str) -> None:
    """Delete a materialized avatar PNG from the object store, if present."""
    key = f"avatars/{user_id}.png"
    await get_objectstore_client().delete_file(key)
