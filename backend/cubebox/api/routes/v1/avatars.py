"""Public avatar image proxy.

Serves a user's materialized avatar PNG from the private object store through
the API server, so the browser never needs direct (403'd) access to the store.
Avatars are not sensitive — they are visible to other workspace members in
group chats — so this route is unauthenticated. The object key is derived
deterministically from the user_id (``avatars/{user_id}.png``).
"""

from typing import Annotated

from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Path, status
from fastapi.responses import Response

from cubebox.objectstore import get_objectstore_client
from cubebox.services.avatar_store import avatar_object_key

router = APIRouter(prefix="/avatar", tags=["avatar"])


@router.get("/{user_id}")
async def get_avatar(
    user_id: Annotated[str, Path(min_length=1, max_length=64)],
) -> Response:
    """Stream a user's materialized avatar PNG from the object store."""
    try:
        data, content_type = await get_objectstore_client().download_file(
            avatar_object_key(user_id)
        )
    except ClientError as exc:
        # NoSuchKey (404) — avatar not yet materialized; the frontend falls
        # back to the live DiceBear render, so 404 is the right signal.
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="avatar not materialized",
            ) from exc
        raise
    return Response(
        content=data,
        media_type=content_type or "image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
