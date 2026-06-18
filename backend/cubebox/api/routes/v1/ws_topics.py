"""Workspace topic routes — CRUD, participants, upgrade, topic-scoped conversations."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.api.schemas.sandbox_policy import (
    SandboxStatusOut,
    SandboxStatusValue,
)
from cubebox.api.schemas.ws_topics import (
    TopicConversationCreateRequest,
    TopicCreateRequest,
    TopicParticipantAddRequest,
    TopicParticipantPatchRequest,
    TopicPatchRequest,
    TopicSetPinRequest,
)
from cubebox.api.serializers import serialize_conversation
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.db.session import get_session
from cubebox.models.conversation import Conversation
from cubebox.models.topic import TopicParticipant
from cubebox.repositories.conversation import ConversationRepository
from cubebox.repositories.conversation_participant import (
    ConversationParticipantRepository,
)
from cubebox.repositories.topic import TopicRepository
from cubebox.repositories.user_sandbox import UserSandboxRepository
from cubebox.utils.time import utc_isoformat

router = APIRouter(
    prefix="/ws/{workspace_id}/topics",
    tags=["topics"],
)


def _topic_repo(session: AsyncSession, ctx: RequestContext) -> TopicRepository:
    return TopicRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )


def _conv_repo(session: AsyncSession, ctx: RequestContext) -> ConversationRepository:
    return ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )


def _serialize_topic(topic: Any) -> dict[str, Any]:
    return {
        "id": topic.id,
        "title": topic.title,
        "sandbox_mode": topic.sandbox_mode,
        "max_participants": topic.max_participants,
        "creator_user_id": topic.creator_user_id,
        "is_archived": topic.is_archived,
        "is_pinned": topic.is_pinned,
        "created_at": utc_isoformat(topic.created_at),
        "updated_at": utc_isoformat(topic.updated_at),
        "last_activity_at": utc_isoformat(topic.last_activity_at),
    }


def _serialize_participant(p: Any, users_by_id: dict[str, Any] | None = None) -> dict[str, Any]:
    user = (users_by_id or {}).get(p.user_id)
    return {
        "id": p.id,
        "topic_id": p.topic_id,
        "user_id": p.user_id,
        "role": p.role,
        "joined_at": utc_isoformat(p.joined_at),
        "display_name": (user.display_name if user else None) or None,
        "email": user.email if user else None,
    }


async def _hydrate_participants(
    session: AsyncSession, participants: list[Any]
) -> list[dict[str, Any]]:
    """Single-query enrichment of participants with the user's display_name + email."""
    if not participants:
        return []
    from cubebox.models.user import User

    user_ids = list({p.user_id for p in participants})
    stmt = select(User).where(cast(Any, User.id).in_(user_ids))
    users = (await session.execute(stmt)).scalars().all()
    users_by_id = {u.id: u for u in users}
    return [_serialize_participant(p, users_by_id) for p in participants]


def _serialize_conversation(conv: Any) -> dict[str, Any]:
    return serialize_conversation(conv)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_topic(
    body: TopicCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.create_topic(
        title=body.title,
        sandbox_mode=body.sandbox_mode,
    )

    conv = Conversation(
        title=body.title,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        creator_user_id=ctx.user.id,
        topic_id=topic.id,
        has_messages=False,
    )
    session.add(conv)
    await session.flush()

    # Seed the creator as P(conv) so their first message doesn't have to
    # auto-join itself; mirrors create_topic_conversation.
    cp_repo = ConversationParticipantRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    await cp_repo.ensure_participant(conv.id, ctx.user.id)

    if body.member_user_ids:
        await repo.add_participants(topic.id, body.member_user_ids)

    await session.commit()
    await session.refresh(conv)

    participants = await repo.list_participants(topic.id)

    return {
        "topic": _serialize_topic(topic),
        "conversation": _serialize_conversation(conv),
        "participants": await _hydrate_participants(session, list(participants)),
    }


@router.get("")
async def list_topics(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    """Return all topics the caller participates in, with participants
    embedded so the sidebar can render avatars on first paint without an
    N+1 per-topic detail fetch.
    """
    from cubebox.models.user import User

    repo = _topic_repo(session, ctx)
    topics = await repo.list_for_sidebar()
    if not topics:
        return {"items": []}
    topic_ids = [t.id for t in topics]
    counts = await repo.participant_counts(topic_ids)
    parts_by_topic = await repo.list_participants_bulk(topic_ids)

    # Hydrate all participant users in one query.
    all_uids = list({p.user_id for ps in parts_by_topic.values() for p in ps})
    users_by_id: dict[str, Any] = {}
    if all_uids:
        stmt = select(User).where(cast(Any, User.id).in_(all_uids))
        for u in (await session.execute(stmt)).scalars().all():
            users_by_id[u.id] = u

    items: list[dict[str, Any]] = []
    for t in topics:
        row = _serialize_topic(t)
        row["participant_count"] = counts.get(t.id, 0)
        row["participants"] = [
            _serialize_participant(p, users_by_id) for p in parts_by_topic.get(t.id, [])
        ]
        items.append(row)
    return {"items": items}


@router.get("/{topic_id}")
async def get_topic(
    topic_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic, participants = await repo.get_with_participants(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    conv_repo = _conv_repo(session, ctx)
    conversations = await conv_repo.list_by_topic(topic_id)

    return {
        "topic": _serialize_topic(topic),
        "participants": await _hydrate_participants(session, list(participants)),
        "conversations": [_serialize_conversation(c) for c in conversations],
    }


@router.patch("/{topic_id}")
async def update_topic(
    topic_id: str,
    body: TopicPatchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    participant = await repo.get_participant(topic_id, ctx.user.id)
    if participant is None or participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only topic owner can update")

    if body.title is not None:
        topic.title = body.title
    session.add(topic)
    await session.commit()
    await session.refresh(topic)
    return {"topic": _serialize_topic(topic)}


@router.patch("/{topic_id}/pin")
async def set_topic_pin(
    topic_id: str,
    body: TopicSetPinRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    """Toggle the topic's sidebar pin. Any participant can pin / unpin —
    the pin is a workspace-shared sidebar position; participants typically
    want to surface the same hot topics."""
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    topic.is_pinned = body.is_pinned
    session.add(topic)
    await session.commit()
    await session.refresh(topic)
    return {"topic": _serialize_topic(topic)}


@router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(
    topic_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    participant = await repo.get_participant(topic_id, ctx.user.id)
    if participant is None or participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only topic owner can delete")

    await repo.archive(topic_id)
    await session.commit()


# --- Participants ---


@router.post(
    "/{topic_id}/participants",
    status_code=status.HTTP_201_CREATED,
)
async def add_participants(
    topic_id: str,
    body: TopicParticipantAddRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    participant = await repo.get_participant(topic_id, ctx.user.id)
    if participant is None or participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only topic owner can add members")

    try:
        added = await repo.add_participants(topic_id, body.user_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()
    return {"participants": await _hydrate_participants(session, list(added))}


@router.delete(
    "/{topic_id}/participants/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_participant(
    topic_id: str,
    user_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    caller_participant = await repo.get_participant(topic_id, ctx.user.id)
    if caller_participant is None:
        raise HTTPException(status_code=403, detail="Not a participant")

    if user_id != ctx.user.id and caller_participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only owner can remove others")

    try:
        await repo.remove_participant(topic_id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session.commit()


@router.patch("/{topic_id}/participants/{user_id}")
async def update_participant_role(
    topic_id: str,
    user_id: str,
    body: TopicParticipantPatchRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    caller_participant = await repo.get_participant(topic_id, ctx.user.id)
    if caller_participant is None or caller_participant.role != "owner":
        raise HTTPException(status_code=403, detail="Only owner can change roles")

    target = await repo.get_participant(topic_id, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Participant not found")

    # Ownership transfer: promoting another member to owner DEMOTES the caller.
    # Spec § API line 303 labels this "Transfer ownership" — exactly one owner
    # at a time. Without the demotion the topic ends up with two owners, both
    # of whom can manage members, delete the topic, and remove the other.
    if body.role == "owner" and target.user_id != caller_participant.user_id:
        caller_participant.role = "member"
        session.add(caller_participant)

    # Demoting an owner (any owner, not just self) must leave at least one
    # owner standing. Two-owner states are reachable transiently (e.g. raced
    # transfer) or via admin scripts; without this guard one demote can
    # brick the topic.
    if body.role != "owner" and target.role == "owner":
        # Count other owners AFTER the transfer-demote above (caller may
        # have already been moved to "member" in this transaction).
        other_owners_stmt = (
            select(func.count())
            .select_from(TopicParticipant)
            .where(
                TopicParticipant.topic_id == topic_id,  # type: ignore[arg-type]
                TopicParticipant.role == "owner",  # type: ignore[arg-type]
                TopicParticipant.user_id != target.user_id,  # type: ignore[arg-type]
            )
        )
        other_owners = (await session.execute(other_owners_stmt)).scalar_one()
        if other_owners == 0:
            raise HTTPException(
                status_code=400,
                detail=("Cannot demote the last owner: promote another member to owner first"),
            )

    target.role = body.role
    session.add(target)
    await session.commit()
    await session.refresh(target)
    hydrated = await _hydrate_participants(session, [target])
    return {"participant": hydrated[0]}


# --- Topic-scoped conversation creation ---


@router.post(
    "/{topic_id}/conversations",
    status_code=status.HTTP_201_CREATED,
)
async def create_topic_conversation(
    topic_id: str,
    body: TopicConversationCreateRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Match the regular conversation-create flow: empty title triggers
    # auto-title from the first message; has_messages=True puts the row
    # in the sidebar immediately so users see it appear.
    conv = Conversation(
        title=body.title or "",
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        creator_user_id=ctx.user.id,
        topic_id=topic_id,
        has_messages=True,
    )
    session.add(conv)
    await session.flush()

    # Seed the creator as P(conv) so their first message doesn't have to
    # auto-join itself, and any conv-only invitees can be added in the
    # same transaction.
    cp_repo = ConversationParticipantRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    await cp_repo.ensure_participant(conv.id, ctx.user.id)

    # Optional member_user_ids: each invitee must already be a topic
    # participant. The conv-participant insert tags them as actors of
    # this specific conversation inside the topic.
    if body.member_user_ids:
        topic_participants = await repo.list_participants(topic_id)
        topic_member_ids = {p.user_id for p in topic_participants}
        for uid in body.member_user_ids:
            if uid not in topic_member_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"User {uid} is not a participant of topic {topic_id}",
                )
        await cp_repo.add_many(conv.id, body.member_user_ids)

    await session.commit()
    await session.refresh(conv)
    return {"conversation": _serialize_conversation(conv)}


# --- Topic-scoped sandbox status ---


@router.get("/{topic_id}/sandbox", response_model=SandboxStatusOut)
async def get_topic_sandbox_status(
    topic_id: str,
    workspace_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> SandboxStatusOut:
    """Return the active sandbox row for this topic, or absent."""
    repo = _topic_repo(session, ctx)
    topic = await repo.get(topic_id)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    sandbox_repo = UserSandboxRepository(session, org_id=ctx.org_id, workspace_id=workspace_id)
    row = await sandbox_repo.get_active_by_scope(scope_type="topic", scope_id=topic_id)
    if row is None:
        return SandboxStatusOut(
            status="absent",
            default_image=None,
            last_activity_at=None,
            browser_url=None,
        )
    return SandboxStatusOut(
        status=cast(SandboxStatusValue, row.status),
        default_image=row.image,
        last_activity_at=utc_isoformat(row.last_activity_at),
        browser_url=None,
    )
