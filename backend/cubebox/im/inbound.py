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

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from cubebox.im.types import InboundEvent
from cubebox.models.conversation import Conversation
from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubebox.repositories.im_connector import get_or_create_thread_link


@dataclass(slots=True)
class IngestResult:
    """Outcome of one ``ingest_inbound_event`` call."""

    outcome: str  # "enqueued" | "duplicate"
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
) -> IngestResult:
    """Atomically insert receipt + reuse-or-create conversation+link + enqueue run.

    On unique-violation of the receipt index, ack as ``duplicate``. On
    unique-violation of the thread-link index (two first-events racing on
    the same scope), retry once — the second attempt will find the
    just-committed link and reuse it.
    """
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

        async def _make_conversation_id() -> str:
            conv = Conversation(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                creator_user_id=account.acting_user_id,
                title=(event.text[:80] or "IM conversation"),
            )
            session.add(conv)
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
        )
        session.add(item)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            if _is_thread_link_unique_violation(exc):
                # Concurrent first-message race: the winning link now
                # exists. Re-enter; the second attempt will find it and
                # reuse the conversation.
                return await ingest_inbound_event(
                    event, account=account, session_maker=session_maker
                )
            if not _is_receipt_unique_violation(exc):
                raise
            return IngestResult(outcome="duplicate", conversation_id=None)
        return IngestResult(outcome="enqueued", conversation_id=link.conversation_id)
