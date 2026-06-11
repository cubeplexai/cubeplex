"""Public (unauthenticated) conversation share routes."""

from __future__ import annotations

import mimetypes
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.db import get_session
from cubebox.models.conversation_share import ConversationShare
from cubebox.objectstore.client import get_objectstore_client
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/public/shares", tags=["public-shares"])


@router.get("/{share_id}")
async def get_public_share(
    share_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    from sqlalchemy import select, true

    stmt = select(ConversationShare).where(
        ConversationShare.id == share_id,  # type: ignore[arg-type]
        ConversationShare.is_active == true(),  # type: ignore[arg-type]
    )
    result = await session.execute(stmt)
    share = result.scalar_one_or_none()
    if not share:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    return {
        "id": share.id,
        "title": share.title,
        "creator_display_name": share.creator_display_name,
        "created_at": utc_isoformat(share.created_at),
        "messages": share.snapshot.get("messages", []),
        "artifacts": share.artifacts_snapshot,
    }


@router.get("/{share_id}/artifacts/{artifact_id}/v{version:int}/{file_path:path}")
async def get_public_share_artifact(
    share_id: str,
    artifact_id: str,
    version: int,
    file_path: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid path")

    from sqlalchemy import select, true

    stmt = select(ConversationShare).where(
        ConversationShare.id == share_id,  # type: ignore[arg-type]
        ConversationShare.is_active == true(),  # type: ignore[arg-type]
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    key = f"shares/{share_id}/artifacts/{artifact_id}/v{version}/{file_path}"
    store = get_objectstore_client()
    try:
        data, content_type = await store.download_file(key)
    except Exception:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact file not found") from None

    mime, _ = mimetypes.guess_type(file_path)
    media_type = mime or content_type or "application/octet-stream"

    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "private, no-store"},
    )
