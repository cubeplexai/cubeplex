"""Synthetic IM enqueue used by schedule/trigger/background-notice handoffs.

When a schedule, trigger, or completed background task targets an IM-bound
conversation, the agent's reply should flow out the same IM-channel path a
user-driven inbound message uses. Rather than duplicate that machinery, we
synthesize the same outbox rows that
:func:`cubeplex.im.inbound.ingest_inbound_event` writes — a paired
``IMWebhookReceipt`` (for dedupe) and ``IMRunQueueItem`` (for the worker to
drain) — and let the existing :class:`IMRunQueueWorker` attach the outbound
tailer on the instance holding the connector's live lease.

These helpers do NOT commit. Callers compose them into their own transaction so
the caller's state transition and the synthetic enqueue land atomically.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.models.im_connector import (
    IMConnectorAccount,
    IMIdentityLink,
    IMRunQueueItem,
    IMWebhookReceipt,
)
from cubeplex.services.schedule_destination import resolve_im_destination_for_conversation

BACKGROUND_NOTICE_EVENT_PREFIX = "background_notice:"
_PLATFORMS_WITH_IDENTICAL_OPEN_ID = frozenset({"dingtalk", "discord", "slack", "wecom"})


@dataclass(frozen=True, slots=True)
class _ImHandoffTarget:
    account_id: str
    channel_id: str
    scope_key: str
    scope_kind: str
    reply_to_id: str | None = None
    sender_im_user_id: str | None = None
    sender_open_id: str | None = None


def background_notice_event_id(notice_id: str, attempt_id: str) -> str:
    """Build the receipt key for one background-notice run attempt."""
    return f"{BACKGROUND_NOTICE_EVENT_PREFIX}{notice_id}:{attempt_id}"


def parse_background_notice_attempt_id(platform_event_id: str, *, notice_id: str) -> str | None:
    """Extract ``attempt_id`` from ``background_notice:{notice_id}:{attempt_id}``."""
    prefix = f"{BACKGROUND_NOTICE_EVENT_PREFIX}{notice_id}:"
    if not platform_event_id.startswith(prefix):
        return None
    attempt_id = platform_event_id[len(prefix) :]
    return attempt_id or None


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
    execution_admission_id: str | None = None,
    reply_to_id: str | None = None,
    sender_im_user_id_override: str | None = None,
    sender_open_id: str | None = None,
) -> None:
    """Enqueue a synthetic inbound row that the IMRunQueueWorker will drain.

    Idempotent on ``platform_event_id``: callers should pass a deterministic
    key (e.g. ``f"schedule:{scheduled_task_run.id}"``) so that retried
    dispatcher ticks do not double-enqueue.

    The caller owns the transaction boundary — this helper only
    ``flush``es, never ``commit``s.
    """
    sender_im_user_id = sender_im_user_id_override
    if sender_im_user_id is None:
        identity_link = (
            await session.execute(
                select(IMIdentityLink).where(
                    IMIdentityLink.account_id == account.id,  # type: ignore[arg-type]
                    IMIdentityLink.user_id == owner_user_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        sender_im_user_id = identity_link.im_user_id if identity_link is not None else None

    existing_receipt = (
        await session.execute(
            select(IMWebhookReceipt).where(
                IMWebhookReceipt.account_id == account.id,  # type: ignore[arg-type]
                IMWebhookReceipt.platform_event_id == platform_event_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing_receipt is not None:
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
        actor_user_id=owner_user_id,
        execution_admission_id=execution_admission_id,
        content=content,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        reply_to_id=reply_to_id,
        inbound_message_id=None,
        sender_im_user_id=sender_im_user_id,
        sender_open_id=sender_open_id,
        status="pending",
    )
    session.add(item)
    await session.flush()


async def _resolve_im_handoff_target(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    actor_user_id: str,
) -> _ImHandoffTarget | None:
    """Resolve IM channel + reply metadata for a conversation, including post-``/new``."""
    is_human_rank = case((col(IMRunQueueItem.inbound_message_id).is_not(None), 1), else_=0)
    same_actor_rank = case(
        (
            col(IMRunQueueItem.actor_user_id).is_(None)
            | (col(IMRunQueueItem.actor_user_id) == actor_user_id),
            1,
        ),
        else_=0,
    )
    prior = (
        await session.execute(
            select(IMRunQueueItem)
            .where(
                col(IMRunQueueItem.org_id) == org_id,
                col(IMRunQueueItem.workspace_id) == workspace_id,
                col(IMRunQueueItem.conversation_id) == conversation_id,
            )
            .order_by(
                desc(is_human_rank),
                desc(same_actor_rank),
                desc(col(IMRunQueueItem.created_at)),
                desc(col(IMRunQueueItem.id)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior is not None:
        same_actor = prior.actor_user_id in (None, actor_user_id)
        return _ImHandoffTarget(
            account_id=prior.account_id,
            channel_id=prior.channel_id,
            scope_key=prior.scope_key,
            scope_kind=prior.scope_kind,
            reply_to_id=prior.reply_to_id,
            sender_im_user_id=prior.sender_im_user_id if same_actor else None,
            sender_open_id=prior.sender_open_id if same_actor else None,
        )

    dest = await resolve_im_destination_for_conversation(
        session,
        org_id=org_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
    )
    if dest is not None:
        return _ImHandoffTarget(
            account_id=dest.im_account_id,
            channel_id=dest.im_channel_id,
            scope_key=dest.im_scope_key,
            scope_kind=dest.im_scope_kind,
        )
    return None


async def enqueue_im_background_notice_handoff(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    conversation_id: str,
    actor_user_id: str,
    notice_id: str,
    attempt_id: str,
    admission_id: str,
    content: str,
) -> bool:
    """Enqueue an outbound IM handoff for an admitted background-notice run attempt."""
    target = await _resolve_im_handoff_target(
        session,
        org_id=org_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        actor_user_id=actor_user_id,
    )
    if target is None:
        return False

    account = await session.get(IMConnectorAccount, target.account_id)
    if (
        account is None
        or account.org_id != org_id
        or account.workspace_id != workspace_id
        or not account.enabled
    ):
        return False

    # WeCom's inbound reply_to_id is a one-shot callback req_id already finalized
    # in the originating turn; clearing it routes follow-up runs via proactive push.
    reply_to_id = None if account.platform == "wecom" else target.reply_to_id
    sender_im_user_id = target.sender_im_user_id
    if sender_im_user_id is None:
        link = (
            await session.execute(
                select(IMIdentityLink).where(
                    col(IMIdentityLink.account_id) == account.id,
                    col(IMIdentityLink.user_id) == actor_user_id,
                )
            )
        ).scalar_one_or_none()
        if link is not None:
            sender_im_user_id = link.im_user_id

    sender_open_id = target.sender_open_id
    if (
        sender_open_id is None
        and sender_im_user_id is not None
        and account.platform in _PLATFORMS_WITH_IDENTICAL_OPEN_ID
    ):
        sender_open_id = sender_im_user_id

    await enqueue_im_channel_run(
        session,
        account=account,
        conversation_id=conversation_id,
        content=content,
        channel_id=target.channel_id,
        scope_key=target.scope_key,
        scope_kind=target.scope_kind,
        owner_user_id=actor_user_id,
        platform_event_id=background_notice_event_id(notice_id, attempt_id),
        execution_admission_id=admission_id,
        reply_to_id=reply_to_id,
        sender_im_user_id_override=sender_im_user_id,
        sender_open_id=sender_open_id,
    )
    return True
