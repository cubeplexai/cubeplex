"""Public (unauthenticated) artifact download via one-time token."""

import mimetypes
from typing import Annotated
from urllib.parse import quote

import orjson
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from loguru import logger

from cubebox.cache import RedisHandle, redis_dep
from cubebox.objectstore import get_objectstore_client

router = APIRouter(prefix="/public/artifacts", tags=["public-artifacts"])


@router.get("/dl/{token}/{filename:path}")
async def public_download(
    token: str,
    filename: str,
    rh: Annotated[RedisHandle, Depends(redis_dep)],
) -> Response:
    """Serve an artifact file using a one-time download token.

    Microsoft Office Online Viewer calls this URL exactly once to fetch the
    file. The token is atomically deleted on first use (GETDEL).
    """
    key = f"{rh.key_prefix}:otk:{token}"
    raw: bytes | str | None = await rh.client.getdel(key)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found or already used",
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

    ascii_fallback = stored_filename.encode("ascii", "ignore").decode() or "download"
    disposition = (
        f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(stored_filename)}"
    )

    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": disposition},
    )
