"""Conversation attachments API."""

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.exceptions import AttachmentAlreadyAttachedError
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.objectstore import get_objectstore_client
from cubebox.repositories import AttachmentRepository, ConversationRepository
from cubebox.services.attachments import AttachmentService

router = APIRouter(
    prefix="/ws/{workspace_id}/conversations/{conversation_id}/attachments",
    tags=["attachments"],
)


def _base_url(workspace_id: str, conversation_id: str) -> str:
    return f"/api/v1/ws/{workspace_id}/conversations/{conversation_id}/attachments"


def _content_disposition(filename: str) -> str:
    """Build a Content-Disposition header that survives non-ASCII filenames.

    HTTP headers are latin-1; raw UTF-8 (e.g. CJK characters) raises
    UnicodeEncodeError when starlette serializes. RFC 5987 lets us emit
    both an ASCII fallback and a percent-encoded UTF-8 form via
    ``filename*=UTF-8''<quoted>``; modern browsers prefer ``filename*``.
    """
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "")
    quoted = quote(filename, safe="")
    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"


async def _require_conversation(
    session: AsyncSession, ctx: RequestContext, conversation_id: str
) -> None:
    """Raise 404 if conversation is not in scope."""
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


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    workspace_id: str,
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    file: UploadFile = File(...),
) -> dict[str, object]:
    """Upload a file attachment to the conversation."""
    await _require_conversation(session, ctx, conversation_id)
    repo = AttachmentRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    service = AttachmentService(repo=repo)
    content = await file.read()
    att = await service.upload(
        conversation_id=conversation_id,
        uploader_user_id=ctx.user.id,
        filename=file.filename or "upload",
        content=content,
        mime_type=file.content_type,
    )
    return service.attachment_to_api_dto(
        att,
        base_url=_base_url(workspace_id, conversation_id),
    )


@router.get("")
async def list_attachments(
    workspace_id: str,
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    status_filter: Annotated[Literal["pending", "attached", "all"], Query(alias="status")] = "all",
) -> dict[str, object]:
    """List attachments for a conversation."""
    await _require_conversation(session, ctx, conversation_id)
    repo = AttachmentRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    rows = await repo.list_by_conversation(
        conversation_id=conversation_id,
        status=None if status_filter == "all" else status_filter,
    )
    base = _base_url(workspace_id, conversation_id)
    return {
        "attachments": [AttachmentService.attachment_to_api_dto(r, base_url=base) for r in rows],
        "total": len(rows),
    }


@router.get("/{attachment_id}")
async def get_attachment(
    workspace_id: str,
    conversation_id: str,
    attachment_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Get attachment metadata."""
    await _require_conversation(session, ctx, conversation_id)
    repo = AttachmentRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    row = await repo.get_with_fork_fallback(
        conversation_id=conversation_id,
        attachment_id=attachment_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attachment {attachment_id} not found",
        )
    return AttachmentService.attachment_to_api_dto(
        row,
        base_url=_base_url(workspace_id, conversation_id),
    )


@router.get("/{attachment_id}/content")
async def download_attachment(
    workspace_id: str,
    conversation_id: str,
    attachment_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> Response:
    """Stream the original uploaded file."""
    await _require_conversation(session, ctx, conversation_id)
    repo = AttachmentRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    row = await repo.get_with_fork_fallback(
        conversation_id=conversation_id,
        attachment_id=attachment_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attachment {attachment_id} not found",
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


@router.get("/{attachment_id}/thumbnail")
async def thumbnail_attachment(
    workspace_id: str,
    conversation_id: str,
    attachment_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> Response:
    """Stream the WebP thumbnail (image attachments only)."""
    await _require_conversation(session, ctx, conversation_id)
    repo = AttachmentRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    row = await repo.get_with_fork_fallback(
        conversation_id=conversation_id,
        attachment_id=attachment_id,
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


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment(
    workspace_id: str,
    conversation_id: str,
    attachment_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> Response:
    """Delete a pending attachment. attached state cannot be deleted."""
    await _require_conversation(session, ctx, conversation_id)
    repo = AttachmentRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    row = await repo.get_in_conversation(
        conversation_id=conversation_id,
        attachment_id=attachment_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Attachment {attachment_id} not found",
        )
    if row.status != "pending":
        raise AttachmentAlreadyAttachedError(attachment_id)
    service = AttachmentService(repo=repo)
    await service.delete_pending(
        conversation_id=conversation_id,
        attachment_id=attachment_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
