"""Transactional inbound core: receipt + thread link + run enqueue in one tx.

The atomic outbox is what keeps the spec's invariant ("either both commit or
neither does") true. A redelivered event hits ``uq_im_receipt_account_event``
and is acked as ``duplicate`` without a second enqueue; a concurrent
thread-link race hits ``uq_im_scope_link`` and recovers by re-entering and
finding the now-existing link.

**Constraint-name discrimination is load-bearing.** The Slack-plan helper
checked ``uq_im_thread_link``; the rename to ``uq_im_scope_link`` means a
verbatim copy would silently never match and turn a recoverable race into a
500 to Feishu. Do not assume.
"""

from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from cubebox.im.identity import IdentityResolver, RejectionNotifier, resolve_or_reject
from cubebox.im.types import InboundEvent
from cubebox.models.conversation import Conversation
from cubebox.models.conversation_participant import ConversationParticipant
from cubebox.models.im_channel_binding import IMChannelBinding
from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubebox.models.topic import Topic, TopicParticipant
from cubebox.repositories.im_connector import get_or_create_thread_link


@dataclass(slots=True)
class IngestResult:
    """Outcome of one ``ingest_inbound_event`` call.

    Possible ``outcome`` values:

    - ``"enqueued"``: receipt + conversation + queue row committed.
    - ``"duplicate"``: a previous call's receipt already covered this
      ``platform_event_id`` (Feishu retry / our own re-delivery).
    - ``"invalid"``: the inbound event was structurally unusable
      (e.g. empty ``platform_event_id``) and was deliberately dropped.
    - ``"retry_exhausted"``: the thread-link race retry cap was hit;
      the event was NOT enqueued but should be visible in observability
      so a stuck shard does not masquerade as healthy dedupe.
    """

    outcome: str
    conversation_id: str | None


def _constraint_name(exc: IntegrityError) -> str:
    """Best-effort constraint name from a psycopg/asyncpg IntegrityError."""
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name:
        return str(name)
    return str(orig) or str(exc)


def _is_receipt_unique_violation(exc: IntegrityError) -> bool:
    return "uq_im_receipt_account_event" in _constraint_name(exc)


def _is_thread_link_unique_violation(exc: IntegrityError) -> bool:
    # NEW schema's index is uq_im_scope_link, NOT uq_im_thread_link.
    return "uq_im_scope_link" in _constraint_name(exc)


async def ingest_inbound_event(
    event: InboundEvent,
    *,
    account: IMConnectorAccount,
    session_maker: async_sessionmaker[Any],
    identity_resolver: IdentityResolver | None = None,
    rejection_notifier: RejectionNotifier | None = None,
    _retry_depth: int = 0,
) -> IngestResult:
    """Atomically insert receipt + reuse-or-create conversation+link + enqueue run.

    On unique-violation of the receipt index, ack as ``duplicate``. On
    unique-violation of the thread-link index (two first-events racing on
    the same scope), retry once — the second attempt will find the
    just-committed link and reuse it.
    """
    # Guard against poison-pill events: a missing/empty platform_event_id
    # would otherwise insert a receipt with key (account_id, "") that wins
    # the unique index forever, silently shadowing every subsequent malformed
    # event from the same account as 'duplicate'.
    if not event.platform_event_id:
        logger.warning(
            "[IM ingest] dropping event with empty platform_event_id (account={})",
            account.id,
        )
        return IngestResult(outcome="invalid", conversation_id=None)
    # Cap retry recursion on the thread-link race path. A persistent
    # IntegrityError or a future constraint-name substring collision must
    # not unbounded-recurse the stack.
    if _retry_depth > 2:
        logger.warning(
            "[IM ingest] aborting thread-link retry storm (account={}, event={})",
            account.id,
            event.platform_event_id,
        )
        return IngestResult(outcome="retry_exhausted", conversation_id=None)
    async with session_maker() as session:
        receipt = IMWebhookReceipt(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            platform_event_id=event.platform_event_id,
            status="pending",
        )
        session.add(receipt)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            if not _is_receipt_unique_violation(exc):
                raise
            return IngestResult(outcome="duplicate", conversation_id=None)

        # Identity gate (sender → workspace member). When wired by the
        # caller, a non-member sender is rejected here: the receipt above
        # still commits (so Feishu retries dedupe) but no conversation /
        # queue row is created. ``acting_user_id`` is the fallback when no
        # resolver is configured — preserves existing behavior.
        effective_user_id: str = account.acting_user_id
        if identity_resolver is not None and rejection_notifier is not None:
            resolved = await resolve_or_reject(
                session=session,
                account=account,
                event=event,
                resolver=identity_resolver,
                notifier=rejection_notifier,
            )
            if resolved is None:
                # Mark receipt terminal so the worker (and dashboards)
                # don't conflate this with a still-pending row.
                receipt.status = "rejected"
                await session.commit()
                return IngestResult(outcome="rejected", conversation_id=None)
            effective_user_id = resolved

        # Look up channel binding for shared-mode detection
        binding = (
            await session.execute(
                select(IMChannelBinding).where(
                    IMChannelBinding.account_id == account.id,  # type: ignore[arg-type]
                    IMChannelBinding.channel_id == event.channel_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        is_shared = binding is not None and binding.mode == "shared"

        # Shared-mode first message: create topic + owner participant
        topic_id: str | None = None
        if is_shared:
            assert binding is not None  # mypy narrowing
            if binding.topic_id is None:
                topic = Topic(
                    org_id=account.org_id,
                    workspace_id=account.workspace_id,
                    creator_user_id=account.acting_user_id,
                    title=binding.channel_name or event.channel_id,
                    sandbox_mode=binding.sandbox_mode or "dedicated",
                    max_participants=100,
                )
                session.add(topic)
                await session.flush()
                binding.topic_id = topic.id
                session.add(binding)
                # Owner participant (the acting user who owns the bot)
                session.add(
                    TopicParticipant(
                        topic_id=topic.id,
                        user_id=account.acting_user_id,
                        role="owner",
                    )
                )
                # If sender is a different user, add as member
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
                # Existing topic: auto-join sender if not already present
                existing_tp = (
                    await session.execute(
                        select(TopicParticipant).where(
                            TopicParticipant.topic_id == binding.topic_id,
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
                title=(event.text[:80] or "IM conversation"),
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

        link, _created = await get_or_create_thread_link(
            session,
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            channel_id=event.channel_id,
            scope_key=event.scope_key,
            scope_kind=event.scope_kind,
            make_conversation_id=_make_conversation_id,
        )

        # Existing shared-mode conversation: auto-join sender as participant
        if not _created and is_shared:
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
            # Also ensure topic participant (idempotent with the block above,
            # but covers the case where the link already existed before this
            # sender's first message).
            if binding.topic_id is not None:
                existing_tp = (
                    await session.execute(
                        select(TopicParticipant).where(
                            TopicParticipant.topic_id == binding.topic_id,
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

        item = IMRunQueueItem(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            receipt_id=receipt.id,
            conversation_id=link.conversation_id,
            content=event.text,
            channel_id=event.channel_id,
            scope_key=event.scope_key,
            scope_kind=event.scope_kind,
            reply_to_id=event.reply_to_id,
            inbound_message_id=event.inbound_message_id,
            sender_im_user_id=event.sender_ref,
            sender_open_id=event.sender_open_id,
        )
        session.add(item)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if _is_thread_link_unique_violation(exc):
                # Concurrent first-message race: the winning link now
                # exists. Re-enter; the second attempt will find it and
                # reuse the conversation. ``_retry_depth`` caps spin.
                return await ingest_inbound_event(
                    event,
                    account=account,
                    session_maker=session_maker,
                    identity_resolver=identity_resolver,
                    rejection_notifier=rejection_notifier,
                    _retry_depth=_retry_depth + 1,
                )
            if not _is_receipt_unique_violation(exc):
                raise
            return IngestResult(outcome="duplicate", conversation_id=None)
        return IngestResult(outcome="enqueued", conversation_id=link.conversation_id)
