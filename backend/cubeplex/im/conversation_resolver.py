"""Shared IM-conversation resolution: lazy topic + thread link + participant top-up.

This helper factors the entire shared-mode + thread-link conversation
resolution out of ``im/inbound.py`` so schedule and trigger dispatchers can
reuse it. Inbound and the dispatchers all need the same answer to "given an
IM channel/scope, what cubeplex conversation should the next message land
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
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.im.bot_settings import (
    bot_display_name,
    build_im_attributes,
    im_topic_title,
    load_bot_settings,
    wants_topic,
)
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_participant import ConversationParticipant
from cubeplex.models.im_connector import IMConnectorAccount, IMThreadLink
from cubeplex.models.topic import Topic, TopicParticipant
from cubeplex.repositories.im_connector import get_or_create_thread_link


@dataclass(frozen=True)
class ResolvedIMConversation:
    """The cubeplex-side state derived from an inbound or dispatched IM event."""

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
    channel_name: str | None = None,
) -> ResolvedIMConversation:
    """Resolve the cubeplex conversation that should host the next message.

    See module docstring for the full side-effect contract. ``origin`` is
    kept on the signature even though it is not used in v1: future
    observability (per-source counters, structured logs) needs to
    differentiate dispatcher entrypoints without changing this signature
    again.

    ``channel_name`` is the human-readable group title when the platform
    supplied one (DingTalk ``conversationTitle``, Feishu ``im.v1.chats.get``).
    Group topics use it as the title; never fall back to the raw channel id.
    """
    del origin  # currently unused; reserved for future observability.

    settings = load_bot_settings(account.config)
    # A DM is always 1:1 — never a shared/group conversation, even on a bot
    # configured for shared routing (the topic is the sender's, personal
    # memory applies, HITL binds to the one user).
    is_shared = settings.routing_mode == "shared" and scope_kind != "dm"
    should_topic = wants_topic(settings)
    sandbox_mode: str | None = settings.sandbox_mode

    # Source metadata stamped on both the Topic and the Conversation. Its
    # presence under "im" is the IM-origin marker read by worker/resume.
    bot_name = bot_display_name(account.config)
    # The bot avatar is hydrated into account.config at connect time; the
    # sidebar renders it as the Topic avatar via attributes.im.
    bot_avatar_url = (account.config or {}).get("bot_avatar_url")
    # Display name for group topics. Prefer the platform-supplied name;
    # never substitute the opaque channel_id (that produced unreadable
    # ``oc_…`` / ``cid…`` titles). Missing name → empty title; the UI
    # localizes via ``t('newGroupChat')``.
    resolved_channel_name: str | None
    if scope_kind == "dm":
        resolved_channel_name = None
    else:
        stripped = (channel_name or "").strip()
        resolved_channel_name = stripped or None
    im_attrs = build_im_attributes(
        platform=account.platform,
        account_id=account.id,
        scope_kind=scope_kind,
        bot_name=bot_name,
        bot_avatar_url=bot_avatar_url,
        channel_id=channel_id,
        channel_name=resolved_channel_name,
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
    # Reuse an existing scope's Topic only while topic mode is on. In flat
    # mode we deliberately ignore (and below, clear) any prior anchor so a
    # topic→flat settings change actually takes effect for active scopes.
    topic_id: str | None = None
    if existing_link is not None and should_topic and existing_link.topic_id is not None:
        anchored = await session.get(Topic, existing_link.topic_id)
        if anchored is not None and not anchored.is_archived:
            topic_id = anchored.id
            # Refresh / clear platform-derived titles on every inbound.
            # With a real name: promote placeholder or platform-tracked titles.
            # Without a name: still clear legacy channel-id / "群聊" placeholders
            # so the UI can localize — otherwise failed lookups leave oc_…
            # titles stuck forever.
            _maybe_refresh_topic_channel_name(
                anchored, channel_id=channel_id, channel_name=resolved_channel_name
            )
        else:
            # The linked Topic was archived/deleted in the UI. Don't keep
            # appending under a Topic the user removed (topic + conversation
            # reads filter archived) — drop the stale anchor and mint a
            # fresh, visible one below.
            existing_link.topic_id = None
            session.add(existing_link)
            # Also soft-delete the conversation tied to the archived Topic so
            # get_or_create_thread_link repoints to a FRESH conversation. Were
            # it left live, the adoption step below would re-home the archived
            # Topic's hidden history under the new Topic, making it visible again.
            old_conv = await session.get(Conversation, existing_link.conversation_id)
            if old_conv is not None and old_conv.deleted_at is None:
                old_conv.deleted_at = datetime.now(UTC)
                session.add(old_conv)
                await session.flush()

    if should_topic and topic_id is None:
        topic = Topic(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            creator_user_id=account.acting_user_id if is_shared else effective_user_id,
            title=im_topic_title(
                scope_kind=scope_kind,
                bot_name=bot_name,
                channel_name=resolved_channel_name,
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

    link, created, reused = await get_or_create_thread_link(
        session,
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        make_conversation_id=_make_conversation_id,
    )

    if should_topic:
        # Backfill the anchor onto the link (new links, or links that predate
        # topic_mode being enabled).
        if topic_id is not None and link.topic_id != topic_id:
            link.topic_id = topic_id
            session.add(link)

        # When we reused an EXISTING conversation (``_make_conversation_id``
        # never ran), it still carries whatever topic/group/attributes it had
        # before — e.g. after a flat→topic change, or a link predating topic
        # mode. Adopt it into the resolved Topic so the link, the conversation,
        # and the Topic agree; otherwise the just-created Topic is orphaned,
        # the conversation stays ungrouped, and (shared) the worker rejects it
        # for lacking attributes.im. We reconcile whenever the row lags, which
        # also repairs any row left split by an earlier resolve.
        if topic_id is not None and reused is not None:
            if reused.topic_id != topic_id or reused.is_group_chat != is_shared:
                reused.topic_id = topic_id
                reused.is_group_chat = is_shared
                merged = dict(reused.attributes or {})
                merged.update(im_attrs)
                reused.attributes = merged
                session.add(reused)
                await session.flush()
    else:
        # Flat mode: drop any prior Topic anchor so existing scopes become
        # standalone — clear it off the link (so /new deletes rather than
        # rotates) and detach the reused conversation from its old Topic.
        # shared+flat keeps is_group_chat=True (still a multi-person convo,
        # just ungrouped); only true isolated+flat flips it back to personal
        # and drops participant rows.
        if link.topic_id is not None:
            link.topic_id = None
            session.add(link)
        if reused is not None:
            wants_group = is_shared
            leaving_group = reused.is_group_chat and not wants_group
            dirty = reused.topic_id is not None or reused.is_group_chat != wants_group
            if dirty:
                reused.topic_id = None
                reused.is_group_chat = wants_group
                session.add(reused)
                if leaving_group:
                    # A topicless personal conversation is visible via
                    # ConversationParticipant rows; without this delete, former
                    # channel members would still see the now-personal chat.
                    await session.execute(
                        delete(ConversationParticipant).where(
                            ConversationParticipant.conversation_id == reused.id  # type: ignore[arg-type]
                        )
                    )
                await session.flush()

    # After /new rotation the fresh conversation starts with an empty title.
    # When the first real message reuses that row (``_make_conversation_id``
    # does not run), stamp a provisional title from the message text — same
    # as brand-new create via title_hint. Never overwrite a non-empty title.
    if reused is not None and not (reused.title or "").strip():
        hint = (title_hint or "").strip()
        if hint:
            reused.title = hint[:80]
            session.add(reused)
            await session.flush()

    # Topic visibility is gated on TopicParticipant (a topic conversation is
    # NOT visible to its creator unless they're also a participant). So ensure
    # the sender is a participant of ANY topic they're routed into — shared
    # late joiners AND isolated senders whose own participant row went missing
    # (otherwise ConversationRepository would hide their replies). Idempotent.
    if topic_id is not None:
        await _ensure_topic_participant(session, topic_id, effective_user_id)
    # Shared channels also carry per-conversation participants.
    if is_shared:
        await _ensure_conversation_participant(
            session, account, link.conversation_id, effective_user_id
        )

    return ResolvedIMConversation(
        conversation_id=link.conversation_id,
        topic_id=topic_id,
        is_group_chat=is_shared,
        sandbox_mode=sandbox_mode,
    )


def _maybe_refresh_topic_channel_name(
    topic: Topic, *, channel_id: str, channel_name: str | None
) -> None:
    """Update a live Topic's title / ``attributes.im`` from platform group name.

    When ``channel_name`` is set:
    - rewrite title if it still looks platform-derived (empty, channel id,
      legacy ``群聊``, or equal to the previously stored channel_name);
    - keep ``attributes.im.channel_name`` in sync.

    When ``channel_name`` is missing (lookup failed / no scope granted):
    - only clear *legacy placeholder* titles (channel id / ``群聊``) to ``""``
      so the UI can localize; never touch user-edited titles;
    - clear ``attributes.im.channel_name`` if it still holds the opaque id.

    Mutates ``topic`` in place (already session-tracked).
    """
    im_blob = (topic.attributes or {}).get("im")
    stored_name = im_blob.get("channel_name") if isinstance(im_blob, dict) else None
    title_is_legacy_id = topic.title == channel_id
    title_is_legacy_label = topic.title == "群聊"
    title_is_empty = not topic.title

    if channel_name:
        desired = channel_name[:255]
        title_is_placeholder = title_is_legacy_id or title_is_legacy_label or title_is_empty
        title_tracks_platform = stored_name is not None and topic.title == stored_name
        name_changed = stored_name != desired
        if not title_is_placeholder and not name_changed:
            return
        if title_is_placeholder or title_tracks_platform:
            topic.title = desired
        if name_changed or not isinstance(im_blob, dict) or stored_name is None:
            attrs = dict(topic.attributes or {})
            im = dict(attrs.get("im") or {})
            im["channel_name"] = desired
            im["channel_id"] = channel_id
            attrs["im"] = im
            topic.attributes = attrs
        return

    # No resolved name: clear only opaque / legacy-label placeholders.
    if title_is_legacy_id or title_is_legacy_label:
        topic.title = ""
    if stored_name in (channel_id, "群聊"):
        attrs = dict(topic.attributes or {})
        im = dict(attrs.get("im") or {})
        im["channel_name"] = None
        im["channel_id"] = channel_id
        attrs["im"] = im
        topic.attributes = attrs


async def _ensure_topic_participant(session: AsyncSession, topic_id: str, user_id: str) -> None:
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

    # Honor the bot's CURRENT topic mode, not just the link's anchor. After a
    # topic→flat switch the link can still carry a topic_id that no normal
    # message has cleared yet; in flat mode /new must delete (start a fresh
    # standalone conversation), not rotate under the stale Topic.
    account = await session.get(IMConnectorAccount, account_id)
    flat = account is None or not wants_topic(load_bot_settings(account.config))

    if flat or link.topic_id is None:
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
        # Fresh conversation — do not copy the old title. A non-empty title
        # would also block web auto-title (generate_and_apply_title skips
        # when title != ""). The next inbound message stamps a provisional
        # title from title_hint when the field is still empty.
        title="",
        # The link is the authoritative Topic anchor — old.topic_id can lag it
        # (e.g. a conversation adopted into a topic after a mode change).
        topic_id=link.topic_id,
        is_group_chat=old.is_group_chat,
        attributes=dict(old.attributes or {}),
    )
    session.add(new_conv)
    await session.flush()
    link.conversation_id = new_conv.id
    session.add(link)
    return "rotated"
