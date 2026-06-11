"""Unified conversation share routes — auth handled per-endpoint."""

from __future__ import annotations

import mimetypes
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.auth.dependencies import current_active_user, optional_current_user
from cubebox.db import get_session
from cubebox.models import Conversation, Membership, OrganizationMembership, User, Workspace
from cubebox.models.conversation_share import ConversationShare, ShareScope
from cubebox.objectstore.client import get_objectstore_client
from cubebox.repositories import ArtifactRepository
from cubebox.repositories.conversation_share import ConversationShareRepository
from cubebox.services.conversation_sharing import build_snapshot, copy_artifacts_to_share
from cubebox.utils.time import utc_isoformat

router = APIRouter(prefix="/shares", tags=["shares"])


def _serialize(s: ConversationShare) -> dict[str, object]:
    return {
        "id": s.id,
        "conversation_id": s.conversation_id,
        "title": s.title,
        "creator_display_name": s.creator_display_name,
        "scope": s.scope.value,
        "is_active": s.is_active,
        "url": f"/share/{s.id}",
        "created_at": utc_isoformat(s.created_at),
    }


# ── helpers ──────────────────────────────────────────────────────────────


async def _check_scope_access(
    share: ConversationShare,
    user: User | None,
    session: AsyncSession,
) -> None:
    """Raise 404 if the viewer lacks access for the share's scope."""
    if share.scope == ShareScope.public:
        return

    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    if share.scope == ShareScope.org:
        org_stmt = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
            OrganizationMembership.org_id == share.org_id,  # type: ignore[arg-type]
        )
        if (await session.execute(org_stmt)).scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    elif share.scope == ShareScope.workspace:
        ws_stmt = select(Membership).where(
            Membership.user_id == user.id,  # type: ignore[arg-type]
            Membership.workspace_id == share.workspace_id,  # type: ignore[arg-type]
        )
        if (await session.execute(ws_stmt)).scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")


# ── routes ───────────────────────────────────────────────────────────────


class CreateShareRequest(BaseModel):
    conversation_id: str
    scope: ShareScope = ShareScope.public


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_share(
    body: CreateShareRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    conv_stmt = select(Conversation).where(
        Conversation.id == body.conversation_id  # type: ignore[arg-type]
    )
    conv = (await session.execute(conv_stmt)).scalar_one_or_none()
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    mem_stmt = select(Membership).where(
        Membership.user_id == user.id,  # type: ignore[arg-type]
        Membership.workspace_id == conv.workspace_id,  # type: ignore[arg-type]
    )
    if (await session.execute(mem_stmt)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")

    ws_stmt = select(Workspace).where(
        Workspace.id == conv.workspace_id  # type: ignore[arg-type]
    )
    workspace = (await session.execute(ws_stmt)).scalar_one()

    messages = await build_snapshot(body.conversation_id)

    art_repo = ArtifactRepository(session, org_id=workspace.org_id, workspace_id=workspace.id)
    artifacts = await art_repo.list_by_conversation(body.conversation_id)
    artifacts_data = [a.to_dict() for a in artifacts]

    display_name = user.email.split("@")[0] if user.email else "Anonymous"

    share_repo = ConversationShareRepository(session)
    share = await share_repo.create(
        org_id=workspace.org_id,
        workspace_id=workspace.id,
        conversation_id=body.conversation_id,
        creator_user_id=user.id,
        creator_display_name=display_name,
        title=conv.title,
        scope=body.scope,
        snapshot={"messages": messages},
        artifacts_snapshot=artifacts_data,
        is_active=False,
    )

    await copy_artifacts_to_share(share.id, body.conversation_id, artifacts_data)

    share = await share_repo.activate(share.id)
    return _serialize(share)


@router.get("")
async def list_shares(
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    repo = ConversationShareRepository(session)
    items, total = await repo.list_by_creator(user.id, limit=limit, offset=offset)
    return {
        "items": [_serialize(s) for s in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/conversation/{conversation_id}")
async def list_conversation_shares(
    conversation_id: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, object]]:
    repo = ConversationShareRepository(session)
    shares = await repo.list_by_conversation(conversation_id, user.id)
    return [_serialize(s) for s in shares]


@router.get("/{share_id}")
async def get_share(
    share_id: str,
    user: Annotated[User | None, Depends(optional_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    repo = ConversationShareRepository(session)
    share = await repo.get_active(share_id)
    if not share:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    await _check_scope_access(share, user, session)

    return {
        "id": share.id,
        "title": share.title,
        "creator_display_name": share.creator_display_name,
        "scope": share.scope.value,
        "created_at": utc_isoformat(share.created_at),
        "messages": share.snapshot.get("messages", []),
        "artifacts": share.artifacts_snapshot,
    }


@router.patch("/{share_id}")
async def revoke_share(
    share_id: str,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, object]:
    repo = ConversationShareRepository(session)
    share = await repo.revoke(share_id, user.id)
    if not share:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")
    return _serialize(share)


@router.get("/{share_id}/artifacts/{artifact_id}/v{version:int}/{file_path:path}")
async def get_share_artifact(
    share_id: str,
    artifact_id: str,
    version: int,
    file_path: str,
    user: Annotated[User | None, Depends(optional_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid path")

    repo = ConversationShareRepository(session)
    share = await repo.get_active(share_id)
    if not share:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    await _check_scope_access(share, user, session)

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
