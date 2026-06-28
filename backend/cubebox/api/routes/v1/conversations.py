"""Conversations API routes."""

import json
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from cubepi.providers.base import ThinkingLevel
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from cubebox.agents.schemas import AgentEvent
from cubebox.api.exceptions import InvalidInputError
from cubebox.api.schemas.conversations import InviteToGroupRequest
from cubebox.api.schemas.ws_topics import UpgradeToTopicRequest
from cubebox.api.serializers import serialize_conversation as _serialize_conversation
from cubebox.auth.context import RequestContext
from cubebox.auth.dependencies import require_member
from cubebox.cache import RedisHandle, redis_dep
from cubebox.config import config as _config
from cubebox.db import get_session
from cubebox.db.engine import _build_database_url, async_session_maker
from cubebox.models import Conversation
from cubebox.models.conversation_participant import ConversationParticipant
from cubebox.repositories import (
    ConversationParticipantRepository,
    ConversationRepository,
    MembershipRepository,
    UserSandboxRepository,
)
from cubebox.repositories.conversation import (
    ForkGroupChatError,
    ForkNewThreadExistsError,
    ForkRunNotCompletedError,
    ForkSourceMissingError,
)
from cubebox.services.avatar_store import resolve_avatar_url
from cubebox.skills.cache import SkillCache
from cubebox.streams.replay_coalescer import ReplayCoalescer
from cubebox.streams.run_events import (
    clear_active_run,
    create_run,
    get_active_run,
    get_conversation_last_error,
    get_latest_event_id,
    get_run_meta,
    is_stale_meta,
    iter_run_events_chunked,
    mark_run_stale,
    read_run_events_after,
)
from cubebox.streams.run_manager import (
    ResumeConflict,
    ResumeInFlight,
    ResumeNoPending,
    ResumeStaleAnswer,
    RunContext,
)
from cubebox.utils.time import utc_isoformat

_INSTALL_RE = re.compile(r"^install\s+([A-Za-z0-9_\-:]+)\s*$")

# Replay backlog is read in bounded batches so a large reconnect never stalls
# the event loop. Tunable; ~1000 keeps each XRANGE + JSON decode cheap.
REPLAY_CHUNK_SIZE = 1000

router = APIRouter(prefix="/ws/{workspace_id}/conversations", tags=["conversations"])

logger = logging.getLogger(__name__)


async def _resolve_topic_run_context(
    conversation: Conversation,
    ctx: RequestContext,
    session: AsyncSession | None = None,
) -> tuple[str | None, bool, str | None, str | None, str | None]:
    """Resolve topic fields needed to build a RunContext.

    Returns ``(topic_id, is_group_chat, sender_display_name, sandbox_mode,
    topic_creator_user_id)``. ``is_group_chat`` is read straight off the
    conversation row — ``Conversation.is_group_chat`` is the source of
    truth. For personal (non-topic) conversations, topic-only fields are
    ``None``.

    Pass ``session`` to reuse the caller's existing transaction (steer /
    HITL endpoints). ``send_message`` calls this without ``session`` —
    its request session is deliberately closed before SSE starts, so a
    fresh short-lived session is required.
    """
    topic_id: str | None = conversation.topic_id
    is_group_chat = bool(conversation.is_group_chat)
    sender_display_name: str | None = None
    sandbox_mode: str | None = None
    topic_creator_user_id: str | None = None

    if topic_id is not None:
        from cubebox.repositories.topic import TopicRepository

        async def _read(topic_session: AsyncSession) -> tuple[str | None, str | None]:
            topic_repo = TopicRepository(
                topic_session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
                user_id=ctx.user.id,
            )
            topic_obj = await topic_repo.get(topic_id)
            if topic_obj is None:
                return None, None
            return topic_obj.sandbox_mode, topic_obj.creator_user_id

        if session is not None:
            sandbox_mode, topic_creator_user_id = await _read(session)
        else:
            async with async_session_maker() as topic_session:
                sandbox_mode, topic_creator_user_id = await _read(topic_session)

    # Stamp the sender on every message (1:1 included) so a later 1:1→group
    # conversion can attribute past messages. Display is gated on is_group_chat
    # in the frontend, not on the presence of this field.
    sender_display_name = ctx.user.display_name or ctx.user.email

    return (
        topic_id,
        is_group_chat,
        sender_display_name,
        sandbox_mode,
        topic_creator_user_id,
    )


async def _require_topic_owner_if_topic(
    session: AsyncSession,
    ctx: RequestContext,
    conversation: Conversation,
) -> None:
    """Owner-only mutations on shared rows (pin / share).

    Personal 1:1 (no topic, not group_chat) — only creator can read the row
    via ``_scoped_select`` B1, so no extra check.
    Standalone group chat (no topic, is_group_chat=True) — any P(conv) can
    read via B2; restrict mutation to the creator.
    Topic conv — restrict to topic owner.
    """
    if conversation.topic_id is None:
        if conversation.is_group_chat and conversation.creator_user_id != ctx.user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the conversation creator can modify this conversation",
            )
        return
    from cubebox.repositories.topic import TopicRepository

    topic_repo = TopicRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    participant = await topic_repo.get_participant(conversation.topic_id, ctx.user.id)
    if participant is None or participant.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only topic owner can modify this conversation",
        )


async def _require_topic_owner_or_creator_if_topic(
    session: AsyncSession,
    ctx: RequestContext,
    conversation: Conversation,
) -> None:
    """Rename / delete: ``C(conv) ∨ O(topic)`` per the access matrix.

    Personal 1:1 — creator-only via ``_scoped_select`` B1; short-circuit.
    Standalone group chat — any P(conv) can read; restrict mutation to
    creator (matches the spec: "Rename/delete standalone group chat:
    C(conv)").
    Topic conv — creator OR topic owner.
    """
    if conversation.topic_id is None:
        if conversation.is_group_chat and conversation.creator_user_id != ctx.user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the conversation creator can modify this conversation",
            )
        return
    if conversation.creator_user_id == ctx.user.id:
        return
    from cubebox.repositories.topic import TopicRepository

    topic_repo = TopicRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    participant = await topic_repo.get_participant(conversation.topic_id, ctx.user.id)
    if participant is None or participant.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only conversation creator or topic owner can modify this conversation",
        )


async def _update_conversation_timestamp(
    conversation_id: str,
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
    model_setting: tuple[str | None, str] | None = None,
) -> None:
    """Mark conversation as active and refresh its timestamp.

    Uses a dedicated NullPool connection so post-stream persistence does not
    depend on the request-scoped pool state. Always sets has_messages=True
    and bumps updated_at, so the conversation is visible in ``list_all`` and
    its position in the recency-ordered list reflects the latest activity.

    Indexing is intentionally NOT triggered here. Callers enqueue the index
    job AFTER the message-write completion point (run-end persistence or
    install-fallback synthetic append) so the worker never claims a job
    against a still-empty history. See ``_enqueue_search_index`` below.
    """
    save_engine = create_async_engine(_build_database_url(), poolclass=NullPool)
    try:
        async with AsyncSession(save_engine, expire_on_commit=False) as save_session:
            save_conv_repo = ConversationRepository(
                save_session,
                org_id=org_id,
                workspace_id=workspace_id,
                user_id=user_id,
            )
            await save_conv_repo.mark_active(conversation_id, model_setting=model_setting)
    finally:
        await save_engine.dispose()


async def _enqueue_search_index(
    conversation_id: str,
    *,
    org_id: str,
    workspace_id: str,
    user_id: str,
) -> None:
    """Best-effort enqueue of a search-index job after history is persisted.

    Indexing is best-effort: failure must not poison the calling path. The
    indexer already logs a structured ``event=search_index_enqueue_failed``
    line, so the failure is observable via log-based alerting.
    """
    try:
        from cubebox.config import config as _cfg
        from cubebox.services.conversation_search.indexer import enqueue_index_job

        if _cfg.get("search.enabled", True):
            await enqueue_index_job(
                org_id=org_id,
                workspace_id=workspace_id,
                creator_user_id=user_id,
                conversation_id=conversation_id,
            )
    except Exception:
        logger.exception("search index enqueue failed for %s", conversation_id)


def _skill_cache() -> SkillCache:
    return SkillCache(cache_root=Path(_config.get("skills.cache_root", "skills_cache")))


async def _maybe_install_from_user_message(
    *,
    session: AsyncSession,
    org_id: str,
    org_slug: str,
    workspace_id: str,
    actor_user_id: str,
    text: str,
) -> str | None:
    """If the user message is `install <canonical_name>`, install it and return
    a replacement assistant note. Otherwise return None and let the message flow.

    Resolves <canonical_name> against the workspace's catalog (local skills not
    yet installed) and any live candidates from registered remote sources. The
    same SkillInstallService.install backs both surfaces so the UI button and
    this parser share one code path.
    """
    m = _INSTALL_RE.match(text.strip())
    if m is None:
        return None
    from cubebox.repositories.organization import OrganizationRepository
    from cubebox.skills.discovery import (
        SkillDiscoveryService,
        SkillInstallError,
        SkillInstallService,
    )
    from cubebox.skills.service import SkillCatalogService, SkillPublishService
    from cubebox.skills.sources.registry import SkillsAdapterManager

    canonical = m.group(1)
    catalog = SkillCatalogService(session=session, cache=_skill_cache())
    org = await OrganizationRepository(session).get(org_id)
    if org is None:
        return f"Could not find organization while trying to install `{canonical}`."
    registry = await SkillsAdapterManager.build(
        session=session,
        catalog=catalog,
        org_id=org_id,
        org_slug=org_slug,
        workspace_id=workspace_id,
    )
    cands = await SkillDiscoveryService(registry).discover(canonical, limit=20)
    match_cand = next((c for c in cands if c.canonical_name == canonical), None)
    if match_cand is None:
        return f"Could not find a skill called `{canonical}` in your workspace catalog."
    install_svc = SkillInstallService(
        session=session,
        registry=registry,
        publisher=SkillPublishService(session=session, cache=_skill_cache()),
        org_id=org_id,
        org_slug=org_slug,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
    )
    try:
        result = await install_svc.install(match_cand.candidate_id)
    except SkillInstallError as e:
        return f"Failed to install `{canonical}`: {e}"
    return (
        f"Installed `{result.canonical_name}` (v{result.installed_version}). "
        f"Use `load_skill('{result.canonical_name}')` to load it in this conversation."
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_conversation(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    title: str = "",
    draft: bool = False,
) -> dict[str, object]:
    """Create a new conversation.

    When ``draft=true`` the conversation is created hidden — it stays out of
    the list endpoint until the user actually sends a message. The home page
    uses this for the eager-create-on-file-pick flow so abandoned drafts
    don't clutter the sidebar.
    """
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await repo.create(title=title, draft=draft)
    return _serialize_conversation(conversation)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Get a conversation by ID."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return _serialize_conversation(conversation)


@router.get("")
async def list_conversations(
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    limit: int = 20,
    offset: int = 0,
) -> dict[str, object]:
    """List conversations with pagination."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversations, total = await repo.list_all(limit=limit, offset=offset)
    return {
        "conversations": [_serialize_conversation(c) for c in conversations],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    title: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Update conversation title."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    existing = await repo.get_by_id(conversation_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    # Rename: C(conv) ∨ O(topic) per spec § Access matrix.
    await _require_topic_owner_or_creator_if_topic(session, ctx, existing)
    conversation = await repo.update_title(conversation_id, title)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return _serialize_conversation(conversation)


class PinRequest(BaseModel):
    """Request body for pin endpoint."""

    is_pinned: bool


@router.patch("/{conversation_id}/pin")
async def set_pin(
    conversation_id: str,
    body: PinRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Set the pinned state of a conversation (idempotent)."""
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    existing = await repo.get_by_id(conversation_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    await _require_topic_owner_if_topic(session, ctx, existing)
    conversation = await repo.set_pin(conversation_id, body.is_pinned)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    return _serialize_conversation(conversation)


class ForkConversationRequest(BaseModel):
    """Request body for forking a conversation."""

    after_run_id: str


@router.post("/{conversation_id}/fork", status_code=status.HTTP_201_CREATED)
async def fork_conversation(
    conversation_id: str,
    body: ForkConversationRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Fork a conversation after the given completed run.

    Copies all messages through the end of ``after_run_id`` into a new
    conversation owned by the caller, in the same workspace and topic as
    the source. Group-chat sources are rejected.
    """
    if not body.after_run_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_after_run_id"},
        )
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    src = await repo.get_by_id(conversation_id)
    if not src:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    try:
        new_conv = await repo.fork(src, after_run_id=body.after_run_id)
    except ForkGroupChatError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "group_chat_not_supported"},
        ) from exc
    except ForkRunNotCompletedError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "run_not_completed"},
        ) from exc
    except ForkSourceMissingError as exc:
        # Source conversation exists in cubebox but has no cubepi thread —
        # nothing to fork. Treat as a client error against the fork-point
        # contract (you can't fork a conversation that has no messages).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "source_has_no_messages"},
        ) from exc
    except ForkNewThreadExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "new_thread_exists"},
        ) from exc
    # Index the forked conversation so it shows up in conversation-search
    # right away — without this, the fork is invisible to search until the
    # caller sends another message. Best-effort; same shape as the
    # post-send-message index trigger.
    await _enqueue_search_index(
        new_conv.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    return _serialize_conversation(new_conv)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> None:
    """Soft-delete a conversation.

    Stamps ``deleted_at`` and hides the row from subsequent reads. Child
    rows (billing events for cost audit, artifacts, attachments) are kept
    so their FK targets stay valid and cost reports survive. A separate
    GC job is the right place to permanently purge old soft-deleted rows.
    """
    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    existing = await repo.get_by_id(conversation_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    # Delete: C(conv) ∨ O(topic) per spec § Access matrix.
    await _require_topic_owner_or_creator_if_topic(session, ctx, existing)
    deleted = await repo.delete_conversation(conversation_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )


async def _adopt_external_bindings(
    session: AsyncSession, conversation_id: str, topic_id: str
) -> None:
    """Sync IM thread links that point at this conversation into the new topic.

    Without this, ``IMThreadLink.topic_id`` stays NULL after a web-side
    upgrade, and ``/new`` treats the link as flat mode — creating a new
    standalone conversation instead of rotating under the topic.
    """
    from sqlalchemy import update

    from cubebox.models.im_connector import IMThreadLink

    await session.execute(
        update(IMThreadLink)
        .where(IMThreadLink.conversation_id == conversation_id)  # type: ignore[arg-type]
        .values(topic_id=topic_id)
    )


@router.post("/{conversation_id}/upgrade-to-topic", status_code=status.HTTP_201_CREATED)
async def upgrade_conversation_to_topic(
    conversation_id: str,
    body: UpgradeToTopicRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    from cubebox.api.routes.v1.ws_topics import (
        _hydrate_participants,
        _serialize_topic,
    )
    from cubebox.repositories.topic import TopicRepository

    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Lock the conversation row so two concurrent upgrades on the same
    # conversation serialize. Without this, both pass the topic_id-is-null
    # check, both create_topic() succeeds, only one wins the assignment,
    # and the loser's Topic + participant rows are orphaned.
    from sqlalchemy import select as _select

    from cubebox.models.conversation import Conversation as _Conv

    lock_stmt = (
        _select(_Conv).where(_Conv.id == conversation_id).with_for_update()  # type: ignore[arg-type]
    )
    locked = (await session.execute(lock_stmt)).scalar_one_or_none()
    if locked is None:
        # Row deleted between get_by_id and the lock acquisition.
        raise HTTPException(status_code=404, detail="Conversation not found")
    if locked.topic_id is not None:
        raise HTTPException(status_code=409, detail="Conversation already belongs to a topic")
    # Re-read into our working object after the lock so subsequent
    # mutations apply to the freshly-locked row.
    conversation = locked
    creator_user_id = conversation.creator_user_id

    topic_repo = TopicRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    topic = await topic_repo.create_topic(
        title=body.title,
        sandbox_mode=body.sandbox_mode,
    )

    conversation.topic_id = topic.id
    session.add(conversation)

    await _adopt_external_bindings(session, conversation_id, topic.id)

    if body.member_user_ids:
        await topic_repo.add_participants(topic.id, body.member_user_ids)

    # Copy current topic_participants into the conversation's
    # conversation_participants so the upgraded conv keeps the actor list
    # under the same per-conv tag the new ACL relies on.
    topic_participants = await topic_repo.list_participants(topic.id)
    cp_repo = ConversationParticipantRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    for tp in topic_participants:
        await cp_repo.ensure_participant(conversation_id, tp.user_id)

    # Sandbox rekey: standalone group chat (or personal) -> topic. The
    # running sandbox row (if any) is re-scoped in place under the topic
    # so subsequent operations from any participant hit the same row.
    # A single UPDATE that matches either the personal user-scope row OR
    # the standalone-group-chat conversation-scope row avoids a race with
    # a concurrent invite-to-group: even if the sandbox flips to
    # conversation-scope between our ``was_group_chat`` read and this
    # UPDATE, the OR-branch still catches the moved row.
    sbx_repo = UserSandboxRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    await sbx_repo.rekey_to_topic(
        creator_user_id=creator_user_id,
        conversation_id=conversation_id,
        topic_id=topic.id,
    )

    await session.commit()
    await session.refresh(conversation)
    participants = await topic_repo.list_participants(topic.id)

    return {
        "topic": _serialize_topic(topic),
        "conversation": _serialize_conversation(conversation),
        "participants": await _hydrate_participants(session, list(participants)),
    }


def _serialize_conv_participant(
    p: ConversationParticipant,
    users_by_id: dict[str, Any] | None = None,
    *,
    include_email: bool = False,
) -> dict[str, Any]:
    user = (users_by_id or {}).get(p.user_id)
    row: dict[str, Any] = {
        "id": p.id,
        "conversation_id": p.conversation_id,
        "user_id": p.user_id,
        "joined_at": utc_isoformat(p.joined_at),
        "display_name": (user.display_name if user else None) or None,
        "avatar_url": resolve_avatar_url(user.avatar_url, p.user_id, user.updated_at)
        if user
        else None,
        "avatar_seed": user.avatar_seed if user else None,
    }
    if include_email:
        row["email"] = user.email if user else None
    return row


async def _hydrate_conv_participants(
    session: AsyncSession,
    participants: list[ConversationParticipant],
    *,
    include_email: bool = False,
) -> list[dict[str, Any]]:
    """Single-query enrichment of conv participants with display_name (+ optional email).

    ``include_email`` defaults to ``False`` and every current call site uses
    the default — emails are dropped from all participant responses (GET
    /participants, POST /invite-to-group). The parameter stays as a hook for
    a future invite-confirmation UI that wants to echo back the addresses
    the caller just selected; until that lands, no response exposes email.
    """
    if not participants:
        return []
    from sqlalchemy import select as _select

    from cubebox.models.user import User

    user_ids = list({p.user_id for p in participants})
    stmt = _select(User).where(User.id.in_(user_ids))  # type: ignore[attr-defined]
    users = (await session.execute(stmt)).scalars().all()
    users_by_id = {u.id: u for u in users}
    return [
        _serialize_conv_participant(p, users_by_id, include_email=include_email)
        for p in participants
    ]


async def _hydrate_conv_participants_by_uids(
    session: AsyncSession,
    conversation_id: str,
    user_ids: list[str],
    *,
    include_email: bool = False,
) -> list[dict[str, Any]]:
    """Load + hydrate ConversationParticipant rows for the given users."""
    if not user_ids:
        return []
    from sqlalchemy import select as _select

    stmt = (
        _select(ConversationParticipant)
        .where(
            ConversationParticipant.conversation_id == conversation_id,  # type: ignore[arg-type]
            ConversationParticipant.user_id.in_(user_ids),  # type: ignore[attr-defined]
        )
        .order_by(ConversationParticipant.joined_at)  # type: ignore[arg-type]
    )
    rows = list((await session.execute(stmt)).scalars().all())
    return await _hydrate_conv_participants(session, rows, include_email=include_email)


@router.post(
    "/{conversation_id}/invite-to-group",
    status_code=status.HTTP_201_CREATED,
)
async def invite_to_conversation(
    conversation_id: str,
    body: InviteToGroupRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    """Add workspace members to a conversation as conversation_participants.

    On the 1 -> 2 transition for a personal (non-topic) conv, the running
    sandbox is re-scoped from user-key to conversation-key in the same
    transaction so subsequent file ops from any participant hit the same
    row.
    """
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    cp_repo = ConversationParticipantRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )

    # Seed the creator FIRST so a personal 1:1 becoming a group chat keeps
    # the original owner tagged as P(conv); without this the count crosses
    # 0 -> 1 on first invite and never flips is_group_chat.
    if not await cp_repo.is_participant(conversation_id, conversation.creator_user_id):
        await cp_repo.ensure_participant(conversation_id, conversation.creator_user_id)

    membership_repo = MembershipRepository(session)
    for uid in body.user_ids:
        role = await membership_repo.get_role(user_id=uid, workspace_id=ctx.workspace_id)
        if role is None:
            raise HTTPException(
                status_code=400,
                detail=f"User {uid} is not a member of this workspace",
            )

    await cp_repo.add_many(conversation_id, body.user_ids)

    # Sandbox rekey on the 1 -> 2 transition. Only applies to personal
    # (non-topic) conversations: topic conversations have their sandbox
    # keyed by topic_id and don't move.
    if conversation.topic_id is None:
        await session.refresh(conversation)
        if conversation.is_group_chat:
            sbx_repo = UserSandboxRepository(
                session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
            )
            await sbx_repo.rekey(
                from_scope_type="user",
                from_scope_id=conversation.creator_user_id,
                to_scope_type="conversation",
                to_scope_id=conversation_id,
            )

    await session.commit()
    await session.refresh(conversation)
    # Return the FULL participant list (not just newly inserted rows).
    # The frontend store replaces ``conversationParticipants[id]`` with
    # this response, so returning only the delta would drop the auto-
    # seeded creator on the 1 -> 2 transition.
    all_user_ids = await cp_repo.list_user_ids(conversation_id)
    return {
        "participants": await _hydrate_conv_participants_by_uids(
            session, conversation_id, all_user_ids
        ),
        "conversation": _serialize_conversation(conversation),
    }


@router.get("/{conversation_id}/participants")
async def list_conversation_participants(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, Any]:
    """List the participants of a conversation.

    Visibility is gated by the conversation's ``_scoped_select``; whoever
    can view the conversation can see its participants.
    """
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    cp_repo = ConversationParticipantRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
    )
    user_ids = await cp_repo.list_user_ids(conversation_id)
    return {"items": await _hydrate_conv_participants_by_uids(session, conversation_id, user_ids)}


class GenerateTitleRequest(BaseModel):
    """Request body for generate-title endpoint."""

    content: str


@router.post("/{conversation_id}/generate-title")
async def generate_title(
    conversation_id: str,
    body: GenerateTitleRequest,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> dict[str, object]:
    """Generate a short title from the user's first message.

    Thin wrapper — gating, LLM invocation, and atomic compare-and-set live
    in :mod:`cubebox.services.conversation_title`.
    """
    from cubebox.services.conversation_title import generate_and_apply_title

    repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    backend = getattr(raw_request.app.state, "encryption_backend", None)
    conversation = await generate_and_apply_title(
        repo=repo,
        session=session,
        org_id=ctx.org_id,
        encryption_backend=backend,
        conversation=conversation,
        content=body.content,
    )
    return _serialize_conversation(conversation)


class SteerMessageRequest(BaseModel):
    """Request body for steering an in-flight run."""

    content: str
    steer_id: str


class CancelSteerRequest(BaseModel):
    """Request body for cancelling a not-yet-drained steer."""

    steer_id: str


class SandboxConfirmAnswer(BaseModel):
    """Request body for answering a pending sandbox command confirmation."""

    decision: Literal["approve", "deny"]
    reason: str | None = None


class AskUserAnswer(BaseModel):
    """Request body for submitting ask_user form answers."""

    answers: dict[str, Any]


class SendMessageRequest(BaseModel):
    """Request body for sending a message."""

    content: str = ""
    attachments: list[str] = []
    model_key: str | None = None
    thinking: ThinkingLevel = "off"


class SendMessageResponse(BaseModel):
    """Response body for starting a new run."""

    run_id: str


def _build_run_streaming_response(
    *,
    raw_request: Request,
    conversation_id: str,
    run_id: str,
    redis_handle: RedisHandle,
) -> StreamingResponse:
    """Build an SSE response that replays and tails a run event stream."""
    redis = redis_handle.client
    prefix = redis_handle.key_prefix

    from cubebox.config import config

    # Clamp BLOCK to stay strictly under the Redis socket_timeout. redis-py
    # applies socket_timeout to blocking commands, so a BLOCK >= socket_timeout
    # causes XREAD to raise TimeoutError mid-read even though Redis is healthy.
    # Use 80% of the socket timeout so the invariant holds for any socket
    # timeout (a fixed subtraction would underflow for low timeouts and a
    # 1000ms floor would equal a 1s socket_timeout — both regress to TimeoutError).
    socket_timeout_s = config.get("redis.socket_timeout_seconds", 10)
    configured_block_ms = config.get("streaming.run_stream_block_ms", 5000)
    safe_block_ms = max(100, int(socket_timeout_s * 1000 * 0.8))
    block_ms = min(configured_block_ms, safe_block_ms)
    last_event_id = raw_request.headers.get("last-event-id")

    async def event_generator() -> AsyncIterator[str]:
        target_event_id = await get_latest_event_id(redis, prefix=prefix, run_id=run_id)
        replay_start = f"({last_event_id}" if last_event_id else None
        replay_cursor = last_event_id

        if target_event_id is not None:
            coalescer = ReplayCoalescer()
            async for batch in iter_run_events_chunked(
                redis,
                prefix=prefix,
                run_id=run_id,
                start=replay_start,
                stop=target_event_id,
                count=REPLAY_CHUNK_SIZE,
            ):
                for event in coalescer.feed(batch):
                    yield _format_sse_event(event.event_id, event.payload)
                    if event.payload.get("type") in {"done", "error"}:
                        return
                # Advance the live-tail cursor by the ORIGINAL last id of the
                # batch — coalesced events carry a synthetic (last-merged) id,
                # so the original id is what keeps the tail gap-free.
                replay_cursor = batch[-1].event_id
            for event in coalescer.flush():
                yield _format_sse_event(event.event_id, event.payload)
                if event.payload.get("type") in {"done", "error"}:
                    return

        live_cursor = replay_cursor or target_event_id or "$"
        while True:
            events = await read_run_events_after(
                redis,
                prefix=prefix,
                run_id=run_id,
                last_event_id=live_cursor,
                block_ms=block_ms,
            )
            if not events:
                active_run = await get_active_run(
                    redis,
                    prefix=prefix,
                    conversation_id=conversation_id,
                )
                latest_event_id = await get_latest_event_id(redis, prefix=prefix, run_id=run_id)
                if active_run is None and latest_event_id == live_cursor:
                    return
                yield ": heartbeat\n\n"
                continue

            for event in events:
                live_cursor = event.event_id
                yield _format_sse_event(event.event_id, event.payload)
                if event.payload.get("type") in {"done", "error"}:
                    return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _ns_to_agent_id(ns: tuple[Any, ...]) -> str | None:
    """Convert cubepi namespace tuple to agent_id string."""
    if not ns:
        return None
    return ":".join(str(part) for part in ns)


def _backfill_tool_call_delta_identity(
    evt_dict: dict[str, Any],
    delta_context: dict[tuple[str | None, int], dict[str, Any]],
) -> dict[str, Any]:
    """Fill missing tool_call_delta identity fields from prior chunks in the same stream."""
    if evt_dict.get("type") != "tool_call_delta":
        return evt_dict

    data = evt_dict.get("data")
    if not isinstance(data, dict):
        return evt_dict

    index = data.get("index")
    if not isinstance(index, int):
        return evt_dict

    key = (evt_dict.get("agent_id"), index)
    cached = delta_context.get(key, {})
    normalized_data = dict(data)

    if normalized_data.get("tool_call_id") is None and cached.get("tool_call_id") is not None:
        normalized_data["tool_call_id"] = cached["tool_call_id"]
    if normalized_data.get("name") is None and cached.get("name") is not None:
        normalized_data["name"] = cached["name"]

    delta_context[key] = {
        "tool_call_id": normalized_data.get("tool_call_id"),
        "name": normalized_data.get("name"),
    }
    return {**evt_dict, "data": normalized_data}


def _dicts_to_sse_events(
    event_dicts: list[dict[str, Any]],
    delta_context: dict[tuple[str | None, int], dict[str, Any]] | None = None,
) -> list[AgentEvent]:
    """Wrap raw event dicts from stream helpers into typed AgentEvent objects."""
    from cubebox.agents.schemas import (
        ArtifactEvent,
        ReasoningEvent,
        TextDeltaEvent,
        ToolCallDeltaEvent,
        ToolCallEvent,
        ToolResultEvent,
    )

    events: list[AgentEvent] = []
    for evt_dict in event_dicts:
        if delta_context is not None:
            evt_dict = _backfill_tool_call_delta_identity(evt_dict, delta_context)
        evt_type = evt_dict.get("type")
        if evt_type == "reasoning":
            events.append(
                ReasoningEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "tool_call":
            events.append(
                ToolCallEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "tool_result":
            events.append(
                ToolResultEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "text_delta":
            events.append(
                TextDeltaEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "tool_call_delta":
            events.append(
                ToolCallDeltaEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )
        elif evt_type == "artifact":
            events.append(
                ArtifactEvent(
                    timestamp=evt_dict["timestamp"],
                    data=evt_dict["data"],
                    agent_id=evt_dict.get("agent_id"),
                    agent_name=evt_dict.get("agent_name"),
                )
            )

    return events


@router.post(
    "/{conversation_id}/messages",
    status_code=status.HTTP_200_OK,
    response_model=None,
)
async def send_message(
    conversation_id: str,
    request_obj: SendMessageRequest,
    raw_request: Request,
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> SendMessageResponse | StreamingResponse:
    """Send a user message and start a background run."""
    # Use a short-lived session for the pre-check only.
    # Do NOT use Depends(get_session) here — it would hold a pooled connection
    # for the entire SSE stream duration, causing connection leaks on cancellation.
    #
    # Auto-join sequencing: ensure_participant runs right after the empty-body
    # validation and BEFORE the install-fallback branch and preset validation.
    # The install-fallback path commits to the checkpointer and returns early,
    # so deferring auto-join past it would let a topic-only caller mutate
    # history without becoming P(conv). All validations below either run-AFTER
    # auto-join (preset / attachments / etc.) or are pure read-only.
    async with async_session_maker() as session:
        conv_repo = ConversationRepository(
            session,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user.id,
        )
        conversation = await conv_repo.get_by_id(conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation {conversation_id} not found",
            )

    if not (request_obj.content and request_obj.content.strip()) and not request_obj.attachments:
        raise InvalidInputError(
            message="Message must include content or attachments",
            details="Provide content text and/or one or more file attachments",
        )

    # Auto-join runs BEFORE the install-fallback short-circuit so a topic-only
    # caller can't mutate conversation history via `install ...` without
    # becoming P(conv). The install-fallback path commits messages directly to
    # the checkpointer and returns early — if auto-join only fires after preset
    # validation (the round-1 placement), a topic-only caller hitting the
    # install path skips it entirely. Auto-join is idempotent and never fails
    # in a way that would let a malformed install elevate participation, so
    # the round-1 concern (bogus-preset hammering) is moot here: there is no
    # preset to validate on the install-fallback path.
    async with async_session_maker() as autojoin_session:
        cp_repo = ConversationParticipantRepository(
            autojoin_session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        )
        await cp_repo.ensure_participant(conversation.id, ctx.user.id)
        await autojoin_session.commit()
        refreshed_conv_repo = ConversationRepository(
            autojoin_session,
            org_id=ctx.org_id,
            workspace_id=ctx.workspace_id,
            user_id=ctx.user.id,
        )
        refreshed = await refreshed_conv_repo.get_by_id(conversation.id)
        if refreshed is not None:
            conversation = refreshed

    # Chat-fallback skill-install parser: `install <canonical_name>` short-circuits the
    # agent loop — persists a user + assistant message pair directly to the checkpointer
    # and returns early, so the agent never runs for this turn.
    #
    # We must claim the conversation's active-run slot BEFORE doing the install or
    # touching history: the normal path serializes turns through
    # run_manager.start_run, and a read-only check would still (a) let the catalog
    # install happen before refusing, and (b) race a concurrent run starting between
    # the check and the checkpointer append. create_run is an atomic CAS claim, so a
    # conflicting active run makes it return None → 409 with no side effects.
    _install_cmd = request_obj.content and not request_obj.attachments
    if _install_cmd and _INSTALL_RE.match(request_obj.content.strip()):
        fallback_run_id = f"install-fallback-{secrets.token_hex(6)}"
        ttl = int(_config.get("lifecycle.stale_run_threshold_seconds", 120))
        claimed = await create_run(
            rds.client,
            prefix=rds.key_prefix,
            run_id=fallback_run_id,
            conversation_id=conversation_id,
            status="running",
            started_at=utc_isoformat(datetime.now(UTC)),
            user_message=request_obj.content,
            ttl_seconds=ttl,
        )
        if claimed is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A run is already active for this conversation",
            )

        install_note: str | None = None
        try:
            async with async_session_maker() as install_session:
                from cubebox.repositories.organization import OrganizationRepository as _OrgRepo

                _org = await _OrgRepo(install_session).get(ctx.org_id)
                _org_slug = _org.slug if _org else ctx.org_id
                install_note = await _maybe_install_from_user_message(
                    session=install_session,
                    org_id=ctx.org_id,
                    org_slug=_org_slug,
                    workspace_id=ctx.workspace_id,
                    actor_user_id=ctx.user.id,
                    text=request_obj.content,
                )
            # _INSTALL_RE matched above, so the parser always returns a note here.
            if install_note is not None:
                from cubepi.providers.base import AssistantMessage, TextContent, UserMessage

                await _update_conversation_timestamp(
                    conversation_id,
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    user_id=ctx.user.id,
                )
                from cubebox.agents.checkpointer import init_checkpointer

                now = time.time()
                async with init_checkpointer() as _cp:
                    await _cp.append(
                        conversation_id,
                        [
                            UserMessage(
                                content=[TextContent(text=request_obj.content)],
                                timestamp=now,
                            ),
                            AssistantMessage(
                                content=[TextContent(text=install_note)],
                                timestamp=now + 0.001,
                            ),
                        ],
                    )
                # Enqueue indexing AFTER the synthetic messages land in
                # checkpointer storage. Doing it inside the timestamp hook
                # (or before this append) would let the worker claim the
                # job during the window when conversation history is still
                # empty and index nothing — no subsequent run-completion
                # hook covers this fallback path.
                await _enqueue_search_index(
                    conversation_id,
                    org_id=ctx.org_id,
                    workspace_id=ctx.workspace_id,
                    user_id=ctx.user.id,
                )
        finally:
            # The fallback spawns no background run — release the slot now that
            # the install + append (the only writes we needed to serialize) are done.
            await clear_active_run(
                rds.client,
                prefix=rds.key_prefix,
                conversation_id=conversation_id,
                run_id=fallback_run_id,
            )

        if install_note is not None:
            # Emit a one-shot SSE response so the frontend renders the assistant
            # reply and finalizes via its normal text_delta/done handlers. Returning
            # a fake run_id here would 404 the immediate GET /runs/{id}/stream the
            # web client issues for non-SSE JSON responses.
            note = install_note
            ts = utc_isoformat(datetime.now(UTC))

            async def _chat_install_fallback_stream() -> AsyncIterator[str]:
                yield _format_sse_event(
                    "0-1",
                    {
                        "type": "text_delta",
                        "timestamp": ts,
                        "agent_id": None,
                        "agent_name": None,
                        "data": {"content": note},
                    },
                )
                yield _format_sse_event(
                    "0-2",
                    {
                        "type": "done",
                        "timestamp": ts,
                        "agent_id": None,
                        "agent_name": None,
                        "data": {},
                    },
                )

            return StreamingResponse(
                _chat_install_fallback_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    from cubebox.api.exceptions import (
        AttachmentReferenceInvalidError,
        AttachmentTooManyError,
    )
    from cubebox.config import config as _cfg

    max_per_msg = int(_cfg.get("attachments.max_per_message", 10))
    if len(request_obj.attachments) > max_per_msg:
        raise AttachmentTooManyError(
            count=len(request_obj.attachments),
            limit=max_per_msg,
        )

    if request_obj.attachments:
        from cubebox.repositories import AttachmentRepository

        async with async_session_maker() as att_session:
            att_repo = AttachmentRepository(
                att_session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
            )
            for fid in request_obj.attachments:
                row = await att_repo.get_in_conversation(
                    conversation_id=conversation_id,
                    attachment_id=fid,
                )
                if row is None or row.status not in {"pending", "attached"}:
                    raise AttachmentReferenceInvalidError(fid)

    # Validate preset early so unknown_preset / broken_preset surface as HTTP
    # errors rather than mid-stream SSE error events. The actual run picks up
    # its own snapshot inside _build_agent_for_conversation.
    #
    # NOTE: this runs BEFORE the attachment mark_attached_bulk and conversation
    # timestamp bump below. resolve_model_preset can raise (UnknownPresetError /
    # BrokenPresetError / NoDefaultPresetError) and those need to surface as
    # 4xx without leaving orphaned attachment state or a bumped has_messages /
    # updated_at on a turn that never ran.
    from cubebox.llm.resolver import resolve_model_preset
    from cubebox.llm.snapshot import load_llm_snapshot

    async with async_session_maker() as _validate_session:
        _snap = await load_llm_snapshot(
            _validate_session,
            ctx.org_id,
            raw_request.app.state.encryption_backend,
        )
    # Validate the requested model selection. BrokenPresetError (the preset's
    # model refs are missing) and NoDefaultPresetError stay 4xx. An
    # UnknownPresetError means the chosen key (a tier/custom preset) was since
    # deleted — a stale client cache, or a key stored on the conversation whose
    # preset is gone. Fall back to the workspace default instead of bricking the
    # send with a 400, and persist the fallback so the conversation heals.
    from cubebox.llm.errors import UnknownPresetError

    try:
        resolve_model_preset(_snap, request_obj.model_key)
        effective_model_key = request_obj.model_key
    except UnknownPresetError:
        # Re-validate the default synchronously so BrokenPresetError /
        # NoDefaultPresetError still raise (4xx) BEFORE any mutation, exactly
        # like the non-fallback path — never let them slip into the stream after
        # has_messages / updated_at / model_key were already changed.
        resolve_model_preset(_snap, None)
        effective_model_key = None

    (
        _topic_id,
        _is_group_chat,
        _sender_display_name,
        _sandbox_mode,
        _topic_creator_user_id,
    ) = await _resolve_topic_run_context(conversation, ctx)

    if request_obj.attachments:
        from cubebox.repositories import AttachmentRepository

        async with async_session_maker() as att_session:
            att_repo = AttachmentRepository(
                att_session,
                org_id=ctx.org_id,
                workspace_id=ctx.workspace_id,
            )
            # Flip pending → attached synchronously, before the run starts.
            # The background _execute_run path also calls this (idempotent),
            # but doing it here closes a race where the client navigates to
            # the conversation page and rehydrates `pending` attachments
            # back into the InputBar staging area.
            await att_repo.mark_attached_bulk(
                conversation_id=conversation_id,
                attachment_ids=list(request_obj.attachments),
            )

    # Mark the conversation active synchronously, before the run starts.
    # This ensures the conversation becomes visible in list_all even if the
    # stream errors before the post-stream persistence runs, and bumps
    # updated_at on every send so recency ordering tracks activity.
    await _update_conversation_timestamp(
        conversation_id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
        model_setting=(effective_model_key, request_obj.thinking),
    )

    run_manager = raw_request.app.state.run_manager
    run_ctx = RunContext(
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        conversation_id=conversation_id,
        topic_id=_topic_id,
        is_group_chat=_is_group_chat,
        sender_display_name=_sender_display_name,
        sandbox_mode=_sandbox_mode,
        topic_creator_user_id=_topic_creator_user_id,
        conversation_creator_user_id=conversation.creator_user_id,
    )

    try:
        run_id = await run_manager.start_run(
            conversation_id=conversation_id,
            content=request_obj.content,
            attachments=list(request_obj.attachments),
            ctx=run_ctx,
            model_key=effective_model_key,
            thinking=request_obj.thinking,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    accept = raw_request.headers.get("accept", "")
    if "text/event-stream" in accept:
        return _build_run_streaming_response(
            raw_request=raw_request,
            conversation_id=conversation_id,
            run_id=run_id,
            redis_handle=rds,
        )

    return SendMessageResponse(run_id=run_id)


_DEFAULT_HISTORY_TAIL = 300
_MAX_HISTORY_WINDOW = 500


def _history_tail_limit() -> int:
    """Default tail size for bootstrap. Configurable via lifecycle config."""
    return int(_config.get("lifecycle.bootstrap_history_tail", _DEFAULT_HISTORY_TAIL))


async def _load_pending_hitl(
    conversation_id: str,
) -> tuple[Any, str | None]:
    """Read cubepi-persisted pending_request + persisted run_id in one checkpointer open."""
    from cubebox.agents.checkpointer import init_checkpointer

    async with init_checkpointer() as cp:
        pending_req = await cp.load_pending_request(conversation_id)
        persisted_run_id = await cp.load_pending_run_id(conversation_id)
    return pending_req, persisted_run_id


@router.get("/{conversation_id}/messages")
async def list_messages(
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    before_seq: int | None = None,
    limit: int | None = None,
) -> dict[str, object]:
    """Return a paginated slice of a conversation's history (newest tail by default).

    ``before_seq`` is an exclusive cursor (use the previous slice's ``oldest_seq``);
    ``limit`` is clamped to ``_MAX_HISTORY_WINDOW`` so a client cannot ask for the
    whole history in one shot.
    """
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    from cubebox.services.history_window import load_history_window

    effective_limit = min(limit or _history_tail_limit(), _MAX_HISTORY_WINDOW)
    window = await load_history_window(
        session, conversation_id, before_seq=before_seq, limit=effective_limit
    )
    return {
        "messages": window.messages,
        "oldest_seq": window.oldest_seq,
        "has_more": window.has_more,
    }


@router.get("/{conversation_id}/bootstrap")
async def get_conversation_bootstrap(
    conversation_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Return history tail + active-run metadata + usage panel data in one round trip.

    History is the most recent ``lifecycle.bootstrap_history_tail`` messages
    (default 300). The frontend uses ``oldest_seq`` + ``has_more`` to lazy-load
    older windows via ``/{conversation_id}/messages?before_seq=...``.
    """
    import asyncio

    from cubebox.services.history_window import find_latest_todos, load_history_window
    from cubebox.streams.hitl_resume import serialize_pending_hitl

    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    # Stage A: the session-bound history read runs concurrently with the
    # Redis + cubepi-pool work — those do not touch ``session``, so there is
    # no SQLAlchemy concurrency conflict.
    history, active_run, pending_pair, last_run_error_raw = await asyncio.gather(
        load_history_window(session, conversation_id, limit=_history_tail_limit()),
        get_active_run(rds.client, prefix=rds.key_prefix, conversation_id=conversation_id),
        _load_pending_hitl(conversation_id),
        get_conversation_last_error(
            rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
        ),
    )

    last_run_status: str | None = None
    if active_run is not None:
        threshold = int(_config.get("lifecycle.stale_run_threshold_seconds", 120))
        if is_stale_meta(active_run, threshold_seconds=threshold):
            await mark_run_stale(
                rds.client,
                prefix=rds.key_prefix,
                run_id=active_run.run_id,
                conversation_id=conversation_id,
            )
            active_run = None
            last_run_status = "stale"

    active_run_payload: dict[str, Any] | None = None
    if active_run is not None:
        active_run_payload = {
            "run_id": active_run.run_id,
            "status": active_run.status,
            "user_message": active_run.user_message,
            "last_event_id": active_run.last_event_id,
            # Disambiguates the active run's user message from a prior turn
            # with identical content: any history user message older than
            # this timestamp belongs to a completed turn, not this run.
            "started_at": active_run.started_at,
            "error_code": active_run.error_code,
            "error_params": _parse_error_params(active_run.error_params),
            "error_message": active_run.error_message,
        }

    pending_req, persisted_run_id = pending_pair
    pending_hitl: dict[str, Any] | None = None
    if pending_req is not None:
        # Run_id resolution order: Redis active-run (cheapest, hot path) first,
        # DB-persisted fallback for long-pause TTL recovery. When the stage-A
        # active_run was None (or got cleared by the staleness check above),
        # a fresh fetch may pick up a brand-new run a worker just created —
        # preserve the original semantics by re-checking Redis in that case.
        run_id_for_pending: str | None
        if active_run is not None:
            run_id_for_pending = active_run.run_id
        else:
            fresh = await get_active_run(
                rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
            )
            # Apply the same staleness gate stage-A does: a row that's already
            # past the heartbeat threshold would point pending_hitl at a dead
            # run, and every approve/decline from the client would 404.
            threshold = int(_config.get("lifecycle.stale_run_threshold_seconds", 120))
            if fresh is not None and not is_stale_meta(fresh, threshold_seconds=threshold):
                run_id_for_pending = fresh.run_id
            else:
                run_id_for_pending = persisted_run_id
        if run_id_for_pending is None:
            # Legacy row (pre-cubepi-v3) — log + degrade to null so the user
            # can at least see other conversation state.
            logger.warning(
                "pending_request for %s has no recoverable run_id; pending_hitl set to null",
                conversation_id,
            )
        else:
            pending_hitl = serialize_pending_hitl(pending_req, run_id=run_id_for_pending)

    last_run_error_payload: dict[str, Any] | None = None
    if last_run_error_raw is not None:
        last_run_error_payload = {
            "run_id": last_run_error_raw.get("run_id"),
            "error_code": last_run_error_raw.get("error_code"),
            "error_params": _parse_error_params(last_run_error_raw.get("error_params")),
            "error_message": last_run_error_raw.get("error_message"),
        }

    # Stage B: usage summary depends on (a) the session being free and (b)
    # the last user message timestamp from history.
    last_user_ts: str | None = None
    for msg in reversed(history.messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            ts = msg.get("timestamp")
            if isinstance(ts, (int, float)):
                last_user_ts = datetime.fromtimestamp(ts, tz=UTC).isoformat()
            break

    from cubebox.services.usage import build_usage_summary

    usage_summary = await build_usage_summary(
        session,
        conversation_id,
        org_id=ctx.org_id,
        encryption_backend=raw_request.app.state.encryption_backend,
        last_user_message_ts=last_user_ts,
    )
    # Todos can live below the bootstrap tail — walk the most recent assistant
    # rows directly so the panel hydrates correctly on long conversations.
    todos = await find_latest_todos(session, conversation_id)

    return {
        "messages": history.messages,
        "oldest_seq": history.oldest_seq,
        "has_more": history.has_more,
        "todos": todos,
        "active_run": active_run_payload,
        "last_run_status": last_run_status,
        "last_run_error": last_run_error_payload,
        "usage_summary": usage_summary,
        "pending_hitl": pending_hitl,
    }


def _parse_error_params(raw: str | None) -> dict[str, Any] | None:
    """Decode the JSON-encoded error_params string stored in RunMeta.

    Returns a dict when the raw value is valid JSON object, None otherwise.
    """
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _format_sse_event(event_id: str, payload: dict[str, Any]) -> str:
    event_payload = {**payload, "event_id": event_id}
    return f"id: {event_id}\ndata: {json.dumps(event_payload, ensure_ascii=False)}\n\n"


@router.get("/{conversation_id}/runs/{run_id}/stream")
async def stream_run(
    conversation_id: str,
    run_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> StreamingResponse:
    """Replay a run's event log, then continue with live blocking reads."""
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    run_meta = await get_run_meta(rds.client, prefix=rds.key_prefix, run_id=run_id)
    if run_meta is None or run_meta.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    from cubebox.config import config as _cfg

    threshold = int(_cfg.get("lifecycle.stale_run_threshold_seconds", 120))
    if is_stale_meta(run_meta, threshold_seconds=threshold):
        await mark_run_stale(
            rds.client,
            prefix=rds.key_prefix,
            run_id=run_id,
            conversation_id=conversation_id,
        )

        async def _stale_stream() -> AsyncIterator[bytes]:
            from datetime import UTC, datetime

            payload = {
                "type": "error",
                "timestamp": datetime.now(UTC).isoformat(),
                "data": {
                    "error_code": "run_stale",
                    "message": "This run died before finishing.",
                },
            }
            yield _format_sse_event("0-0", payload).encode()

        return StreamingResponse(_stale_stream(), media_type="text/event-stream")

    return _build_run_streaming_response(
        raw_request=raw_request,
        conversation_id=conversation_id,
        run_id=run_id,
        redis_handle=rds,
    )


@router.get("/{conversation_id}/runs/{run_id}/meta")
async def get_run_meta_route(
    conversation_id: str,
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, Any]:
    """Return the RunMeta hash for a single run (status + error fields).

    Used by the frontend to render the error bubble at a failed run's tail
    after a page reload, when the SSE replay path no longer covers the run.
    """
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    run_meta = await get_run_meta(rds.client, prefix=rds.key_prefix, run_id=run_id)
    if run_meta is None or run_meta.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run {run_id} not found",
        )

    return {
        "run_id": run_meta.run_id,
        "status": run_meta.status,
        "started_at": run_meta.started_at,
        "last_event_id": run_meta.last_event_id,
        "last_event_at": run_meta.last_event_at,
        "error_code": run_meta.error_code,
        "error_params": _parse_error_params(run_meta.error_params),
        "error_message": run_meta.error_message,
    }


@router.post("/{conversation_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_active_run(
    conversation_id: str,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Cancel the conversation's active run, if any."""
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )

    from cubebox.agents.checkpointer import init_checkpointer

    run_manager = raw_request.app.state.run_manager
    run_ctx = RunContext(
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        conversation_id=conversation_id,
        conversation_creator_user_id=conversation.creator_user_id,
    )

    # Cancel-on-paused dispatch — covers both the live paused_hitl case
    # AND the long-pause TTL-expired case where the Redis active-run row
    # is gone but cubepi_threads.pending_request + run_id still exist.
    # bootstrap + answer routes already fall back to the DB-persisted
    # run_id in this case; cancel needs the same fallback or the user
    # sees a card they can't cancel.
    paused_run_id: str | None = None
    if active_run is not None and active_run.status == "paused_hitl":
        paused_run_id = active_run.run_id
    elif active_run is None:
        async with init_checkpointer() as _cp:
            persisted_run_id = await _cp.load_pending_run_id(conversation_id)
        if persisted_run_id is not None:
            paused_run_id = persisted_run_id

    if paused_run_id is not None:
        try:
            await run_manager.cancel_paused_run(
                conversation_id=conversation_id,
                run_id=paused_run_id,
                reason="cancelled by user",
                ctx=run_ctx,
            )
        except ResumeNoPending:
            # Pending got cleared between our DB read and our claim —
            # treat as already done.
            return {"status": "no_active_run", "run_id": None}
        except ResumeInFlight as exc:
            raise HTTPException(status_code=409, detail={"code": "resume_in_flight"}) from exc
        except ResumeConflict as exc:
            raise HTTPException(status_code=409, detail={"code": "conversation_moved"}) from exc
        return {"status": "cancelled", "run_id": paused_run_id}

    if active_run is None or active_run.status != "running":
        return {"status": "no_active_run", "run_id": None}

    dispatch_status = await run_manager.dispatch_cancel(active_run.run_id)
    return {"status": dispatch_status, "run_id": active_run.run_id}


@router.post("/{conversation_id}/steer", status_code=status.HTTP_202_ACCEPTED)
async def steer_active_run(
    conversation_id: str,
    body: SteerMessageRequest,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Inject a steering message into the conversation's active run, if any."""
    if not body.content.strip():
        raise InvalidInputError(
            message="Steering message must not be empty",
            details="Provide non-empty content to steer the run",
        )

    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    # Auto-join: steering a paused run also counts as participating.
    cp_repo = ConversationParticipantRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    await cp_repo.ensure_participant(conversation.id, ctx.user.id)
    await session.commit()
    await session.refresh(conversation)

    (
        _topic_id,
        _is_group_chat,
        _sender_display_name,
        _sandbox_mode,
        _topic_creator_user_id,
    ) = await _resolve_topic_run_context(conversation, ctx, session=session)

    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )
    if active_run is not None and active_run.status == "paused_hitl":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "paused_hitl",
                "message": "answer or cancel the pending question first",
            },
        )
    if active_run is None or active_run.status != "running":
        return {"status": "no_active_run", "run_id": None}

    steer_metadata: dict[str, Any] = {}
    if _sender_display_name:
        steer_metadata["sender_user_id"] = ctx.user.id
        steer_metadata["sender_display_name"] = _sender_display_name

    run_manager = raw_request.app.state.run_manager
    dispatch_status = await run_manager.dispatch_steer(
        active_run.run_id,
        body.content,
        steer_id=body.steer_id,
        metadata=steer_metadata or None,
    )
    return {"status": dispatch_status, "run_id": active_run.run_id}


@router.post("/{conversation_id}/steer/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_steer(
    conversation_id: str,
    body: CancelSteerRequest,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Best-effort cancel of a not-yet-drained steer on the active run."""
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )
    if active_run is not None and active_run.status == "paused_hitl":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "paused_hitl",
                "message": "answer or cancel the pending question first",
            },
        )
    if active_run is None or active_run.status != "running":
        return {"status": "no_active_run", "run_id": None}

    run_manager = raw_request.app.state.run_manager
    dispatch_status = await run_manager.dispatch_cancel_steer(active_run.run_id, body.steer_id)
    return {"status": dispatch_status, "run_id": active_run.run_id}


@router.post(
    "/{conversation_id}/sandbox-confirm/{question_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_sandbox_confirm(
    conversation_id: str,
    question_id: str,
    body: SandboxConfirmAnswer,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Submit a human approve/deny for a pending sandbox command confirmation."""
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    # HITL is stricter than send: only conv participants (P(conv)) can
    # answer. A topic-only member who has never sent a message in this
    # conversation cannot answer HITL — they would have to send (which
    # auto-joins them) before they can resolve a pending question. 404
    # (not 403) to match the info-disclosure rule.
    cp_repo = ConversationParticipantRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    if not await cp_repo.is_participant(conversation.id, ctx.user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    (
        _topic_id,
        _is_group_chat,
        _sender_display_name,
        _sandbox_mode,
        _topic_creator_user_id,
    ) = await _resolve_topic_run_context(conversation, ctx, session=session)

    from cubepi.hitl.types import ApproveAnswer

    from cubebox.agents.checkpointer import init_checkpointer

    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )
    if active_run is not None:
        run_id = active_run.run_id
    else:
        async with init_checkpointer() as _cp:
            persisted_run_id = await _cp.load_pending_run_id(conversation_id)
        if persisted_run_id is None:
            # Distinguish 404 no_pending vs 500 missing_run_id legacy row.
            async with init_checkpointer() as _cp:
                if await _cp.load_pending_request(conversation_id) is None:
                    raise HTTPException(status_code=404, detail={"code": "no_pending"})
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "missing_run_id",
                    "message": "pending has no persisted run_id (legacy row)",
                },
            )
        run_id = persisted_run_id

    run_manager = raw_request.app.state.run_manager
    answer = ApproveAnswer(decision=body.decision, reason=body.reason)
    run_ctx = RunContext(
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        conversation_id=conversation_id,
        topic_id=_topic_id,
        is_group_chat=_is_group_chat,
        sender_display_name=_sender_display_name,
        sandbox_mode=_sandbox_mode,
        topic_creator_user_id=_topic_creator_user_id,
        conversation_creator_user_id=conversation.creator_user_id,
    )
    try:
        new_run_id = await run_manager.resume_run_with_answer(
            conversation_id=conversation_id,
            run_id=run_id,
            question_id=question_id,
            answer=answer,
            ctx=run_ctx,
        )
    except ResumeNoPending as exc:
        raise HTTPException(status_code=404, detail={"code": "no_pending"}) from exc
    except ResumeStaleAnswer as exc:
        raise HTTPException(status_code=409, detail={"code": "stale_answer"}) from exc
    except ResumeInFlight as exc:
        raise HTTPException(status_code=409, detail={"code": "resume_in_flight"}) from exc
    except ResumeConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "conversation_moved"}) from exc
    return {"status": "ok", "run_id": new_run_id}


@router.post(
    "/{conversation_id}/ask-user/{question_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_ask_user_answer(
    conversation_id: str,
    question_id: str,
    body: AskUserAnswer,
    raw_request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    rds: Annotated[RedisHandle, Depends(redis_dep)],
) -> dict[str, object]:
    """Submit the user's answers for a pending ask_user form."""
    conv_repo = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    conversation = await conv_repo.get_by_id(conversation_id)
    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    # HITL is stricter than send: only conv participants (P(conv)) can
    # answer. A topic-only member who has never sent a message in this
    # conversation cannot answer HITL — they would have to send (which
    # auto-joins them) before they can resolve a pending question. 404
    # (not 403) to match the info-disclosure rule.
    cp_repo = ConversationParticipantRepository(
        session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
    )
    if not await cp_repo.is_participant(conversation.id, ctx.user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )

    (
        _topic_id,
        _is_group_chat,
        _sender_display_name,
        _sandbox_mode,
        _topic_creator_user_id,
    ) = await _resolve_topic_run_context(conversation, ctx, session=session)

    from cubebox.agents.checkpointer import init_checkpointer

    active_run = await get_active_run(
        rds.client, prefix=rds.key_prefix, conversation_id=conversation_id
    )
    if active_run is not None:
        run_id = active_run.run_id
    else:
        async with init_checkpointer() as _cp:
            persisted_run_id = await _cp.load_pending_run_id(conversation_id)
        if persisted_run_id is None:
            # Distinguish 404 no_pending vs 500 missing_run_id legacy row.
            async with init_checkpointer() as _cp:
                if await _cp.load_pending_request(conversation_id) is None:
                    raise HTTPException(status_code=404, detail={"code": "no_pending"})
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "missing_run_id",
                    "message": "pending has no persisted run_id (legacy row)",
                },
            )
        run_id = persisted_run_id

    run_manager = raw_request.app.state.run_manager
    run_ctx = RunContext(
        user_id=ctx.user.id,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        conversation_id=conversation_id,
        topic_id=_topic_id,
        is_group_chat=_is_group_chat,
        sender_display_name=_sender_display_name,
        sandbox_mode=_sandbox_mode,
        topic_creator_user_id=_topic_creator_user_id,
        conversation_creator_user_id=conversation.creator_user_id,
    )
    try:
        new_run_id = await run_manager.resume_run_with_answer(
            conversation_id=conversation_id,
            run_id=run_id,
            question_id=question_id,
            answer=body.answers,
            ctx=run_ctx,
        )
    except ResumeNoPending as exc:
        raise HTTPException(status_code=404, detail={"code": "no_pending"}) from exc
    except ResumeStaleAnswer as exc:
        raise HTTPException(status_code=409, detail={"code": "stale_answer"}) from exc
    except ResumeInFlight as exc:
        raise HTTPException(status_code=409, detail={"code": "resume_in_flight"}) from exc
    except ResumeConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "conversation_moved"}) from exc
    return {"status": "ok", "run_id": new_run_id}
