"""Workspace-scoped conversation share routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db import get_session
from cubebox.repositories import ArtifactRepository, ConversationRepository
from cubebox.repositories.conversation_share import ConversationShareRepository
from cubebox.services.conversation_sharing import build_snapshot, copy_artifacts_to_share
from cubebox.utils.time import utc_isoformat

router = APIRouter(tags=["shares"])


def _serialize_share(s: Any) -> dict[str, object]:
    return {
        "id": s.id,
        "conversation_id": s.conversation_id,
        "title": s.title,
        "creator_display_name": s.creator_display_name,
        "is_active": s.is_active,
        "created_at": utc_isoformat(s.created_at),
    }


def _serialize_share_with_url(s: Any) -> dict[str, object]:
    d = _serialize_share(s)
    d["url"] = f"/share/{s.id}"
    return d


@router.post(
    "/ws/{workspace_id}/conversations/{conversation_id}/shares",
    status_code=status.HTTP_201_CREATED,
)
async def create_share(
    workspace_id: str,
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=workspace_id,
        user_id=ctx.user.id,
    )
    conv = await conv_repo.get_by_id(conversation_id)
    if not conv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    messages = await build_snapshot(conversation_id)

    art_repo = ArtifactRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=workspace_id,
    )
    artifacts = await art_repo.list_by_conversation(conversation_id)
    artifacts_data = [a.to_dict() for a in artifacts]

    display_name = ctx.user.email.split("@")[0] if ctx.user.email else "Anonymous"

    share_repo = ConversationShareRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=workspace_id,
        user_id=ctx.user.id,
    )
    # Create share row as inactive first
    share = await share_repo.create(
        conversation_id=conversation_id,
        creator_display_name=display_name,
        title=conv.title,
        snapshot={"messages": messages},
        artifacts_snapshot=artifacts_data,
        is_active=False,
    )

    # Copy artifacts to share-scoped storage (S3 failure won't leave
    # an accessible share — the row stays inactive)
    await copy_artifacts_to_share(share.id, conversation_id, artifacts_data)

    # Activate only after artifact copy succeeds
    share = await share_repo.activate(share.id)

    return _serialize_share_with_url(share)


@router.get("/ws/{workspace_id}/conversations/{conversation_id}/shares")
async def list_conversation_shares(
    workspace_id: str,
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> list[dict[str, object]]:
    share_repo = ConversationShareRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=workspace_id,
        user_id=ctx.user.id,
    )
    shares = await share_repo.list_by_conversation(conversation_id)
    return [_serialize_share_with_url(s) for s in shares]


@router.get("/ws/{workspace_id}/shares")
async def list_shares(
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    share_repo = ConversationShareRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=workspace_id,
        user_id=ctx.user.id,
    )
    items, total = await share_repo.list_all(limit=limit, offset=offset)
    return {
        "items": [_serialize_share_with_url(s) for s in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


class RevokeRequest(BaseModel):
    is_active: bool


@router.patch("/ws/{workspace_id}/shares/{share_id}")
async def revoke_share(
    workspace_id: str,
    share_id: str,
    body: RevokeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    if body.is_active:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Re-activation is not supported",
        )
    share_repo = ConversationShareRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=workspace_id,
        user_id=ctx.user.id,
    )
    share = await share_repo.revoke(share_id)
    if not share:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")
    return _serialize_share_with_url(share)
