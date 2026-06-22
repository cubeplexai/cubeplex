"""Synthetic IM enqueue used by schedule/trigger dispatchers in im_channel mode.

When a schedule or trigger targets ``im_channel``, the agent's reply should
flow out the same IM-channel path a user-driven inbound message uses. Rather
than duplicate that machinery, we synthesize the same outbox rows that
:func:`cubebox.im.inbound.ingest_inbound_event` writes — a paired
``IMWebhookReceipt`` (for dedupe) and ``IMRunQueueItem`` (for the worker to
drain) — and let the existing :class:`IMRunQueueWorker` start the run.

The helper does NOT commit. Callers compose this into their own dispatcher
transaction so the dispatcher's terminal-state update on the schedule/trigger
row and the synthetic enqueue land atomically.

Idempotency is owned by the caller: pass a stable
``platform_event_id`` such as ``f"schedule:{run.id}"`` or
``f"trigger:{event.id}"``. A retried tick that ran past the enqueue point
and crashed before committing its terminal state will find the prior
receipt on retry and short-circuit, preventing a second queue row.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMWebhookReceipt,
)


async def enqueue_im_channel_run(
    session: AsyncSession,
    *,
    account: IMConnectorAccount,
    conversation_id: str,
    content: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    owner_user_id: str,
    platform_event_id: str,
) -> None:
    """Enqueue a synthetic inbound row that the IMRunQueueWorker will drain.

    Idempotent on ``platform_event_id``: callers should pass a deterministic
    key (e.g. ``f"schedule:{scheduled_task_run.id}"``) so that retried
    dispatcher ticks do not double-enqueue.

    The caller owns the transaction boundary — this helper only
    ``flush``es, never ``commit``s.
    """
    identity_link = (
        await session.execute(
            select(IMIdentityLink).where(
                IMIdentityLink.account_id == account.id,  # type: ignore[arg-type]
                IMIdentityLink.user_id == owner_user_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    sender_im_user_id: str | None = identity_link.im_user_id if identity_link is not None else None

    existing_receipt = (
        await session.execute(
            select(IMWebhookReceipt).where(
                IMWebhookReceipt.account_id == account.id,  # type: ignore[arg-type]
                IMWebhookReceipt.platform_event_id == platform_event_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing_receipt is not None:
        # Prior dispatcher tick already enqueued (and likely crashed before
        # committing its terminal state). Short-circuit so we don't insert
        # a second queue row for the same logical occurrence.
        return

    receipt = IMWebhookReceipt(
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        platform_event_id=platform_event_id,
        status="completed",
    )
    session.add(receipt)
    await session.flush()

    item = IMRunQueueItem(
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        receipt_id=receipt.id,
        conversation_id=conversation_id,
        content=content,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        reply_to_id=None,
        inbound_message_id=None,
        sender_im_user_id=sender_im_user_id,
        sender_open_id=None,
        status="pending",
    )
    session.add(item)
    await session.flush()
