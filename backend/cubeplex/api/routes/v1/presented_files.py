"""Conversation presented-files API (agent → user durable media)."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.db import get_session
from cubeplex.objectstore import get_objectstore_client
from cubeplex.repositories import ConversationRepository, PresentedFileRepository

router = APIRouter(
    prefix="/ws/{workspace_id}/conversations/{conversation_id}/presented-files",
    tags=["presented-files"],
)


def _content_disposition(filename: str) -> str:
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "")
    quoted = quote(filename, safe="")
    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"


async def _require_conversation(
    session: AsyncSession, ctx: RequestContext, conversation_id: str
) -> None:
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    if (await repo.get_by_id(conversation_id)) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )


@router.get("/{presented_file_id}/content")
async def download_presented_file(
    workspace_id: str,
    conversation_id: str,
    presented_file_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> Response:
    """Stream the presented file bytes from ObjectStore."""
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    repo = PresentedFileRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    row = await repo.get_in_conversation(
        conversation_id=conversation_id,
        presented_file_id=presented_file_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Presented file {presented_file_id} not found",
        )
    data, content_type = await get_objectstore_client().download_file(row.object_key)
    return Response(
        content=data,
        media_type=row.mime_type or content_type,
        headers={
            "Content-Disposition": _content_disposition(row.filename),
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.get("/{presented_file_id}/thumbnail")
async def thumbnail_presented_file(
    workspace_id: str,
    conversation_id: str,
    presented_file_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> Response:
    """Stream the WebP thumbnail when present (images only)."""
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    repo = PresentedFileRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    row = await repo.get_in_conversation(
        conversation_id=conversation_id,
        presented_file_id=presented_file_id,
    )
    if row is None or row.thumbnail_object_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Thumbnail not available",
        )
    data, _ = await get_objectstore_client().download_file(row.thumbnail_object_key)
    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=3600"},
    )
