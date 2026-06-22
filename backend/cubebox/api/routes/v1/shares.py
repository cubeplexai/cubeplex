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
from cubebox.models import Conversation, Membership, OrganizationMembership, User
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


async def _is_topic_owner_of_conversation(
    session: AsyncSession, conversation_id: str, user_id: str
) -> bool:
    """True when the conversation belongs to a topic and the user is an owner."""
    from typing import Any, cast

    from cubebox.models.topic import TopicParticipant

    conv_stmt = select(cast(Any, Conversation.topic_id)).where(
        cast(Any, Conversation.id) == conversation_id,
    )
    topic_id = (await session.execute(conv_stmt)).scalar_one_or_none()
    if topic_id is None:
        return False
    part_stmt = select(cast(Any, TopicParticipant.role)).where(
        cast(Any, TopicParticipant.topic_id) == topic_id,
        cast(Any, TopicParticipant.user_id) == user_id,
    )
    role = (await session.execute(part_stmt)).scalar_one_or_none()
    return role == "owner"


async def _is_conv_participant_of_share(
    session: AsyncSession, conversation_id: str, user_id: str
) -> bool:
    """True when the conv has no topic and the user is a conversation_participant.

    Mirrors the standalone-group-chat branch in ``list_conversation_shares`` so
    LIST and REVOKE stay symmetric: any member of the group chat may revoke any
    share minted on that conversation.
    """
    from typing import cast as _cast

    from cubebox.models.conversation_participant import ConversationParticipant

    conv_stmt = select(_cast(Any, Conversation.topic_id)).where(
        _cast(Any, Conversation.id) == conversation_id,
    )
    topic_id = (await session.execute(conv_stmt)).scalar_one_or_none()
    if topic_id is not None:
        return False
    part_stmt = select(_cast(Any, ConversationParticipant.user_id)).where(
        _cast(Any, ConversationParticipant.conversation_id) == conversation_id,
        _cast(Any, ConversationParticipant.user_id) == user_id,
    )
    return (await session.execute(part_stmt)).scalar_one_or_none() is not None


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
        Conversation.id == body.conversation_id,  # type: ignore[arg-type]
        Conversation.deleted_at.is_(None),  # type: ignore[union-attr]
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

    if body.scope == ShareScope.org:
        org_check = select(OrganizationMembership).where(
            OrganizationMembership.user_id == user.id,  # type: ignore[arg-type]
            OrganizationMembership.org_id == conv.org_id,  # type: ignore[arg-type]
        )
        if (await session.execute(org_check)).scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Org membership required for org scope")

    # Topic conversations: only the topic owner may mint a share.
    if conv.topic_id is not None:
        from cubebox.repositories.topic import TopicRepository

        topic_repo = TopicRepository(
            session,
            org_id=conv.org_id,
            workspace_id=conv.workspace_id,
            user_id=user.id,
        )
        participant = await topic_repo.get_participant(conv.topic_id, user.id)
        if participant is None or participant.role != "owner":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Only topic owner can share this conversation",
            )

    messages = await build_snapshot(body.conversation_id)

    art_repo = ArtifactRepository(session, org_id=conv.org_id, workspace_id=conv.workspace_id)
    artifacts = await art_repo.list_by_conversation(body.conversation_id)
    artifacts_data = [a.to_dict() for a in artifacts]

    display_name = user.email.split("@")[0] if user.email else "Anonymous"

    share_repo = ConversationShareRepository(session)
    share = await share_repo.create(
        org_id=conv.org_id,
        workspace_id=conv.workspace_id,
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
    workspace_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    repo = ConversationShareRepository(session)
    items, total = await repo.list_by_creator(
        user.id, workspace_id=workspace_id, limit=limit, offset=offset
    )
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
    # Visibility rules:
    # - Topic owner of the conv's topic → all shares on the conv.
    # - Standalone group chat participant (no topic, but user is a
    #   conversation_participant) → all shares on the conv. This matches
    #   the conv-participant view: each member should see shares minted
    #   by other members in the same group chat.
    # - Otherwise → only the caller's own shares (1:1 behavior unchanged).
    visible_to: str | None = user.id
    if await _is_topic_owner_of_conversation(session, conversation_id, user.id):
        visible_to = None
    else:
        from typing import cast as _cast

        from cubebox.models.conversation_participant import ConversationParticipant

        conv_stmt = select(_cast(Any, Conversation.topic_id)).where(
            _cast(Any, Conversation.id) == conversation_id,
        )
        topic_id = (await session.execute(conv_stmt)).scalar_one_or_none()
        if topic_id is None:
            part_stmt = select(_cast(Any, ConversationParticipant.user_id)).where(
                _cast(Any, ConversationParticipant.conversation_id) == conversation_id,
                _cast(Any, ConversationParticipant.user_id) == user.id,
            )
            if (await session.execute(part_stmt)).scalar_one_or_none() is not None:
                visible_to = None
    shares = await repo.list_by_conversation(conversation_id, visible_to)
    return [_serialize(s) for s in shares]


@router.get("/{share_id}")
async def get_share(
    share_id: str,
    user: Annotated[User | None, Depends(optional_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    from cubebox.agents.stream import unwrap_deferred_in_message_dicts

    repo = ConversationShareRepository(session)
    share = await repo.get_active(share_id)
    if not share:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    await _check_scope_access(share, user, session)

    # Legacy snapshots (created before the build_snapshot fix) still hold the
    # dispatcher form; unwrap on read so old share links don't render
    # mismatched cards. Newer snapshots are already unwrapped — the helper
    # is a no-op for them.
    messages = unwrap_deferred_in_message_dicts(share.snapshot.get("messages", []))

    return {
        "id": share.id,
        "title": share.title,
        "creator_display_name": share.creator_display_name,
        "scope": share.scope.value,
        "created_at": utc_isoformat(share.created_at),
        "messages": messages,
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
    if share is not None:
        return _serialize(share)

    # Look up the share unfiltered, then check the two widened revoke paths:
    # - topic owners may revoke any share on a conv inside their topic
    #   (cleanup after transfers / departures).
    # - standalone-group-chat participants may revoke any share on that conv,
    #   so REVOKE stays symmetric with LIST (round-1 widened LIST to include
    #   P(conv); without this widening, the UI shows the row but DELETE 404s).
    existing = await repo.get_active(share_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")
    allowed = await _is_topic_owner_of_conversation(
        session, existing.conversation_id, user.id
    ) or await _is_conv_participant_of_share(session, existing.conversation_id, user.id)
    if not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Share not found")

    existing.is_active = False
    await session.commit()
    await session.refresh(existing)
    return _serialize(existing)


@router.get("/{share_id}/artifacts/{artifact_id}/v{version:int}/{file_path:path}")
async def get_share_artifact(
    share_id: str,
    artifact_id: str,
    version: int,
    file_path: str,
    user: Annotated[User | None, Depends(optional_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    if "/.." in f"/{file_path}" or file_path.startswith("/"):
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
