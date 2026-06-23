"""Shared IM-conversation resolution: lazy topic + thread link + participant top-up.

This helper factors the entire shared-mode + thread-link conversation
resolution out of ``im/inbound.py`` so schedule and trigger dispatchers can
reuse it. Inbound and the dispatchers all need the same answer to "given an
IM channel/scope, what cubebox conversation should the next message land
in?" — including the lazy ``Topic`` creation for shared bindings, the
participant top-up for late joiners, and the soft-delete-driven repoint
inside ``get_or_create_thread_link``.

Side effects:

1. Read account-level ``IMBotSettings`` (``routing_mode`` isolated/shared,
   ``topic_mode`` topic/flat, ``sandbox_mode``) off ``account.config``.
   Routing is uniform per bot — no per-channel binding.
2. The Topic anchor lives on ``IMThreadLink.topic_id`` and survives ``/new``.
   Reuse an existing scope's topic; only mint a new ``Topic`` (stamped with
   ``attributes.im`` source metadata) when the scope has none yet. Owner is
   the bot (shared) or the sender (isolated, "各自名下").
3. ``get_or_create_thread_link`` with a closure that mints a new
   ``Conversation`` (carrying ``topic_id`` + ``attributes.im`` +
   ``is_group_chat=is_shared``). Backfill ``link.topic_id`` afterwards.
4. Shared mode: idempotent top-up of ``ConversationParticipant`` +
   ``TopicParticipant`` for the sender — covers late joiners whose first
   message arrives after the thread link was created by someone else.

The caller owns the surrounding transaction — this helper only ``flush``es,
never ``commit``s.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.im.bot_settings import (
    bot_display_name,
    build_im_attributes,
    im_topic_title,
    load_bot_settings,
    wants_topic,
)
from cubebox.models.conversation import Conversation
from cubebox.models.conversation_participant import ConversationParticipant
from cubebox.models.im_connector import IMConnectorAccount, IMThreadLink
from cubebox.models.topic import Topic, TopicParticipant
from cubebox.repositories.im_connector import get_or_create_thread_link


@dataclass(frozen=True)
class ResolvedIMConversation:
    """The cubebox-side state derived from an inbound or dispatched IM event."""

    conversation_id: str
    topic_id: str | None
    is_group_chat: bool
    sandbox_mode: str | None


async def resolve_im_conversation(
    session: AsyncSession,
    account: IMConnectorAccount,
    *,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    effective_user_id: str,
    title_hint: str,
    origin: Literal["inbound", "schedule", "trigger"],
) -> ResolvedIMConversation:
    """Resolve the cubebox conversation that should host the next message.

    See module docstring for the full side-effect contract. ``origin`` is
    kept on the signature even though it is not used in v1: future
    observability (per-source counters, structured logs) needs to
    differentiate dispatcher entrypoints without changing this signature
    again.
    """
    del origin  # currently unused; reserved for future observability.

    settings = load_bot_settings(account.config)
    is_shared = settings.routing_mode == "shared"
    should_topic = wants_topic(settings)
    sandbox_mode: str | None = settings.sandbox_mode

    # Source metadata stamped on both the Topic and the Conversation. Its
    # presence under "im" is the IM-origin marker read by worker/resume.
    bot_name = bot_display_name(account.config)
    # PR1: channel_name is not yet fetched from the platform — group topics
    # fall back to the channel id. Lazy fetch is a follow-up (see spec).
    channel_name = None if scope_kind == "dm" else channel_id
    im_attrs = build_im_attributes(
        platform=account.platform,
        account_id=account.id,
        scope_kind=scope_kind,
        bot_name=bot_name,
        bot_avatar_url=None,
        channel_id=channel_id,
        channel_name=channel_name,
    )

    # The Topic anchor lives on the IMThreadLink and survives /new (which
    # rotates conversation_id but keeps topic_id). Reuse it across rotation;
    # only mint a new Topic when this scope has none yet.
    existing_link = (
        await session.execute(
            select(IMThreadLink).where(
                IMThreadLink.account_id == account.id,  # type: ignore[arg-type]
                IMThreadLink.channel_id == channel_id,  # type: ignore[arg-type]
                IMThreadLink.scope_key == scope_key,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    topic_id: str | None = existing_link.topic_id if existing_link is not None else None

    if should_topic and topic_id is None:
        topic = Topic(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            creator_user_id=account.acting_user_id if is_shared else effective_user_id,
            title=im_topic_title(
                scope_kind=scope_kind, bot_name=bot_name, channel_name=channel_name
            ),
            sandbox_mode=sandbox_mode or "dedicated",
            attributes=dict(im_attrs),
            max_participants=100 if is_shared else 20,
        )
        session.add(topic)
        await session.flush()
        topic_id = topic.id
        owner_uid = account.acting_user_id if is_shared else effective_user_id
        session.add(TopicParticipant(topic_id=topic_id, user_id=owner_uid, role="owner"))
        await session.flush()

    async def _make_conversation_id() -> str:
        conv = Conversation(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            creator_user_id=effective_user_id,
            title=(title_hint[:80] or "IM conversation"),
            topic_id=topic_id,
            is_group_chat=is_shared,
            attributes=dict(im_attrs),
        )
        session.add(conv)
        await session.flush()
        if is_shared:
            session.add(
                ConversationParticipant(
                    org_id=account.org_id,
                    workspace_id=account.workspace_id,
                    conversation_id=conv.id,
                    user_id=effective_user_id,
                )
            )
            await session.flush()
        return conv.id

    link, _created = await get_or_create_thread_link(
        session,
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        make_conversation_id=_make_conversation_id,
    )

    # Backfill the anchor onto the link (new links, or links that predate
    # topic_mode being enabled).
    if topic_id is not None and link.topic_id != topic_id:
        link.topic_id = topic_id
        session.add(link)

    # Shared channels: auto-join the sender to the topic + conversation so a
    # late joiner whose first message arrives after the link was created is
    # still a participant. Idempotent.
    if is_shared:
        if topic_id is not None:
            await _ensure_topic_participant(session, topic_id, effective_user_id)
        await _ensure_conversation_participant(
            session, account, link.conversation_id, effective_user_id
        )

    return ResolvedIMConversation(
        conversation_id=link.conversation_id,
        topic_id=topic_id,
        is_group_chat=is_shared,
        sandbox_mode=sandbox_mode,
    )


async def _ensure_topic_participant(
    session: AsyncSession, topic_id: str, user_id: str
) -> None:
    """Idempotently add ``user_id`` to a Topic as a member."""
    existing = (
        await session.execute(
            select(TopicParticipant).where(
                TopicParticipant.topic_id == topic_id,  # type: ignore[arg-type]
                TopicParticipant.user_id == user_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(TopicParticipant(topic_id=topic_id, user_id=user_id, role="member"))
        await session.flush()


async def _ensure_conversation_participant(
    session: AsyncSession,
    account: IMConnectorAccount,
    conversation_id: str,
    user_id: str,
) -> None:
    """Idempotently add ``user_id`` to a Conversation."""
    existing = (
        await session.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,  # type: ignore[arg-type]
                ConversationParticipant.user_id == user_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            ConversationParticipant(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                conversation_id=conversation_id,
                user_id=user_id,
            )
        )
        await session.flush()


async def reset_im_conversation(
    session: AsyncSession,
    *,
    account_id: str,
    channel_id: str,
    scope_key: str,
) -> Literal["none", "flat", "rotated"]:
    """Apply ``/new`` to an IM scope. Caller owns the commit.

    - ``none``: no active link — nothing to reset.
    - ``flat``: flat mode (link has no Topic) — delete the link; the next
      message starts a brand-new conversation, as before.
    - ``rotated``: topic mode — repoint the link to a fresh ``Conversation``
      under the SAME Topic. The old conversation is left intact so it stays
      as history under the Topic (this is why we don't soft-delete it).
    """
    link = (
        await session.execute(
            select(IMThreadLink).where(
                IMThreadLink.account_id == account_id,  # type: ignore[arg-type]
                IMThreadLink.channel_id == channel_id,  # type: ignore[arg-type]
                IMThreadLink.scope_key == scope_key,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if link is None:
        return "none"
    if link.topic_id is None:
        await session.delete(link)
        return "flat"
    old = (
        await session.execute(
            select(Conversation).where(Conversation.id == link.conversation_id)  # type: ignore[arg-type]
        )
    ).scalar_one_or_none()
    if old is None:
        # Defensive: link points at a missing conversation — treat as flat.
        await session.delete(link)
        return "flat"
    new_conv = Conversation(
        org_id=old.org_id,
        workspace_id=old.workspace_id,
        creator_user_id=old.creator_user_id,
        title=old.title,
        topic_id=old.topic_id,
        is_group_chat=old.is_group_chat,
        attributes=dict(old.attributes or {}),
    )
    session.add(new_conv)
    await session.flush()
    link.conversation_id = new_conv.id
    session.add(link)
    return "rotated"
