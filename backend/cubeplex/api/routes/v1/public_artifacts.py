"""Public (unauthenticated) artifact download via short-lived token."""

import mimetypes
from typing import Annotated

import orjson
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from loguru import logger

from cubeplex.cache import RedisHandle, redis_dep
from cubeplex.objectstore import get_objectstore_client
from cubeplex.utils.http import content_disposition

router = APIRouter(prefix="/public/artifacts", tags=["public-artifacts"])


@router.get("/dl/{token}/{filename:path}")
async def public_download(
    token: str,
    filename: str,
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> Response:
    """Serve an artifact file using a short-lived download token.

    Microsoft Office Online Viewer may fetch this URL more than once (its
    probe and conversion nodes pull independently), so the token stays valid
    for its full Redis TTL instead of being consumed on first use.
    """
    key = f"{rh.key_prefix}:otk:{token}"
    raw: bytes | str | None = await rh.client.get(key)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found or expired",
        )

    payload = orjson.loads(raw if isinstance(raw, bytes) else raw.encode())
    stored_filename: str = payload["filename"]
    if filename != stored_filename:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Filename mismatch",
        )

    conversation_id: str = payload["conversation_id"]
    artifact_id: str = payload["artifact_id"]
    version: int = payload["version"]
    obj_key = f"artifacts/{conversation_id}/{artifact_id}/v{version}/{stored_filename}"

    try:
        store = get_objectstore_client()
        data, stored_content_type = await store.download_file(obj_key)
    except Exception as e:
        logger.error("OTK download failed for {}: {}", obj_key, e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found in storage",
        ) from None

    mime, _ = mimetypes.guess_type(stored_filename)
    media_type = mime or stored_content_type or "application/octet-stream"

    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": content_disposition(stored_filename, inline=True)},
    )
