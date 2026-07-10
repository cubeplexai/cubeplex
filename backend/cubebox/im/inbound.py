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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from cubebox.im.conversation_resolver import resolve_im_conversation
from cubebox.im.identity import IdentityResolver, RejectionNotifier, resolve_or_reject
from cubebox.im.types import InboundEvent
from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMWebhookReceipt,
)


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
            resolved_user_id = await resolve_or_reject(
                session=session,
                account=account,
                event=event,
                resolver=identity_resolver,
                notifier=rejection_notifier,
            )
            if resolved_user_id is None:
                # Mark receipt terminal so the worker (and dashboards)
                # don't conflate this with a still-pending row.
                receipt.status = "rejected"
                await session.commit()
                return IngestResult(outcome="rejected", conversation_id=None)
            effective_user_id = resolved_user_id

        # Live settings: the long-connection / gateway transports captured
        # this account at startup, so its ``config`` can be stale relative to
        # a settings change. Reload it at this boundary — every transport
        # funnels through here — so saved routing/topic settings take effect
        # without a reconnect. (The webhook path already loads fresh; this is
        # a cheap PK re-read.) Use the fresh copy ONLY for resolve — don't
        # reassign ``account``: the thread-link retry below rolls back this
        # session, which would expire a session-bound instance, and the
        # recursive retry must read the caller's stable scalars (id/org/ws).
        resolve_account = await session.get(IMConnectorAccount, account.id) or account
        resolved = await resolve_im_conversation(
            session,
            resolve_account,
            channel_id=event.channel_id,
            scope_key=event.scope_key,
            scope_kind=event.scope_kind,
            effective_user_id=effective_user_id,
            title_hint=event.text,
            origin="inbound",
            channel_name=event.channel_name,
        )

        item = IMRunQueueItem(
            org_id=account.org_id,
            workspace_id=account.workspace_id,
            account_id=account.id,
            receipt_id=receipt.id,
            conversation_id=resolved.conversation_id,
            content=event.text,
            channel_id=event.channel_id,
            scope_key=event.scope_key,
            scope_kind=event.scope_kind,
            reply_to_id=event.reply_to_id,
            inbound_message_id=event.inbound_message_id,
            sender_im_user_id=event.sender_ref,
            sender_open_id=event.sender_open_id,
            attachment_refs=[r.to_json() for r in event.attachments] or None,
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
        return IngestResult(outcome="enqueued", conversation_id=resolved.conversation_id)
