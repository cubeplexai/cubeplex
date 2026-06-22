"""Shared IM-conversation resolution: lazy topic + thread link + participant top-up.

This helper factors the entire shared-mode + thread-link conversation
resolution out of ``im/inbound.py`` so schedule and trigger dispatchers can
reuse it. Inbound and the dispatchers all need the same answer to "given an
IM channel/scope, what cubebox conversation should the next message land
in?" — including the lazy ``Topic`` creation for shared bindings, the
participant top-up for late joiners, and the soft-delete-driven repoint
inside ``get_or_create_thread_link``.

Side effects mirror inbound verbatim:

1. Look up ``IMChannelBinding(account_id, channel_id)`` to detect
   shared-mode / sandbox-mode / topic linkage.
2. Shared mode + no topic yet: create ``Topic``, set ``binding.topic_id``,
   insert owner ``TopicParticipant`` for ``account.acting_user_id`` and a
   member row for ``effective_user_id`` when different.
3. Shared mode + existing topic: auto-join ``effective_user_id`` as a
   ``TopicParticipant`` if missing.
4. ``get_or_create_thread_link`` with a closure that mints a new
   ``Conversation`` (carrying ``topic_id`` + ``is_group_chat=is_shared``)
   and inserts a ``ConversationParticipant`` for ``effective_user_id`` when
   shared.
5. If the link was already present and we're in shared mode: idempotent
   top-up of ``ConversationParticipant`` + ``TopicParticipant`` for the
   sender — covers late joiners whose first message arrives after the
   thread link was created by someone else.

The caller owns the surrounding transaction — this helper only ``flush``es,
never ``commit``s.
"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.conversation import Conversation
from cubebox.models.conversation_participant import ConversationParticipant
from cubebox.models.im_channel_binding import IMChannelBinding
from cubebox.models.im_connector import IMConnectorAccount
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

    binding = (
        await session.execute(
            select(IMChannelBinding).where(
                IMChannelBinding.account_id == account.id,  # type: ignore[arg-type]
                IMChannelBinding.channel_id == channel_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()

    is_shared = binding is not None and binding.mode == "shared"
    topic_id: str | None = None
    sandbox_mode: str | None = binding.sandbox_mode if binding is not None else None

    if is_shared:
        assert binding is not None  # mypy narrowing
        if binding.topic_id is None:
            topic = Topic(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                creator_user_id=account.acting_user_id,
                title=binding.channel_name or channel_id,
                sandbox_mode=binding.sandbox_mode or "dedicated",
                max_participants=100,
            )
            session.add(topic)
            await session.flush()
            binding.topic_id = topic.id
            session.add(binding)
            session.add(
                TopicParticipant(
                    topic_id=topic.id,
                    user_id=account.acting_user_id,
                    role="owner",
                )
            )
            if effective_user_id != account.acting_user_id:
                session.add(
                    TopicParticipant(
                        topic_id=topic.id,
                        user_id=effective_user_id,
                        role="member",
                    )
                )
            await session.flush()
        else:
            existing_tp = (
                await session.execute(
                    select(TopicParticipant).where(
                        TopicParticipant.topic_id == binding.topic_id,  # type: ignore[arg-type]
                        TopicParticipant.user_id == effective_user_id,  # type: ignore[arg-type]
                    )
                )
            ).scalar_one_or_none()
            if existing_tp is None:
                session.add(
                    TopicParticipant(
                        topic_id=binding.topic_id,
                        user_id=effective_user_id,
                        role="member",
                    )
                )
                await session.flush()
        topic_id = binding.topic_id

    async def _make_conversation_id() -> str:
        conv = Conversation(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            creator_user_id=effective_user_id,
            title=(title_hint[:80] or "IM conversation"),
            topic_id=topic_id,
            is_group_chat=is_shared,
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

    link, created = await get_or_create_thread_link(
        session,
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        make_conversation_id=_make_conversation_id,
    )

    if not created and is_shared:
        assert binding is not None
        existing_cp = (
            await session.execute(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == link.conversation_id,  # type: ignore[arg-type]
                    ConversationParticipant.user_id == effective_user_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if existing_cp is None:
            session.add(
                ConversationParticipant(
                    org_id=account.org_id,
                    workspace_id=account.workspace_id,
                    conversation_id=link.conversation_id,
                    user_id=effective_user_id,
                )
            )
        if binding.topic_id is not None:
            existing_tp = (
                await session.execute(
                    select(TopicParticipant).where(
                        TopicParticipant.topic_id == binding.topic_id,  # type: ignore[arg-type]
                        TopicParticipant.user_id == effective_user_id,  # type: ignore[arg-type]
                    )
                )
            ).scalar_one_or_none()
            if existing_tp is None:
                session.add(
                    TopicParticipant(
                        topic_id=binding.topic_id,
                        user_id=effective_user_id,
                        role="member",
                    )
                )
        await session.flush()

    return ResolvedIMConversation(
        conversation_id=link.conversation_id,
        topic_id=topic_id,
        is_group_chat=is_shared,
        sandbox_mode=sandbox_mode,
    )
