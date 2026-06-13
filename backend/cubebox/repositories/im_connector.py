"""IM connector repositories + queue claim primitives.

Account lookup at ingress time is unscoped by org on purpose: the
``(platform, external_account_id)`` pair is globally unique and is the
seam that *selects* the (org_id, workspace_id) for the inbound event.

Thread-link, identity-link, and queue helpers run inside the
``ingest_inbound_event`` transaction and use the account row's
(org_id, workspace_id) for scoping.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.models.conversation import Conversation
from cubebox.models.im_connector import (
    IMConnectorAccount,
    IMRunQueueItem,
    IMThreadLink,
    IMWebhookReceipt,
)


async def get_account_by_external_id_unscoped(
    session: AsyncSession,
    *,
    platform: str,
    external_account_id: str,
) -> IMConnectorAccount | None:
    """Look up an account by ``(platform, external_account_id)``.

    Unscoped because the inbound webhook / long-connection event arrives
    before we know which org/workspace it belongs to. The uniqueness of the
    pair is enforced by ``uq_im_account_platform_external``.
    """
    stmt = select(IMConnectorAccount).where(
        IMConnectorAccount.platform == platform,  # type: ignore[arg-type]
        IMConnectorAccount.external_account_id == external_account_id,  # type: ignore[arg-type]
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_or_create_thread_link(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str,
    account_id: str,
    channel_id: str,
    scope_key: str,
    scope_kind: str,
    make_conversation_id: Callable[[], Awaitable[str]],
) -> tuple[IMThreadLink, bool]:
    """Look up an existing thread link or create one and a fresh Conversation.

    Looks up on ``(account_id, channel_id, scope_key)``. On create, both
    ``scope_key`` and ``scope_kind`` are written — the model enforces NOT
    NULL on both, and a verbatim copy of the Slack-plan helper that ignored
    ``scope_kind`` would fail on first insert. Returns ``(link, created)``.
    """
    stmt = (
        select(IMThreadLink, Conversation)
        .join(Conversation, Conversation.id == IMThreadLink.conversation_id)  # type: ignore[arg-type]
        .where(
            IMThreadLink.account_id == account_id,  # type: ignore[arg-type]
            IMThreadLink.channel_id == channel_id,  # type: ignore[arg-type]
            IMThreadLink.scope_key == scope_key,  # type: ignore[arg-type]
        )
    )
    row = (await session.execute(stmt)).one_or_none()
    if row is not None:
        existing, conv = row
        # If the cubebox-side conversation was soft-deleted (user wiped
        # the thread in the UI), the next IM message would otherwise
        # land in that hidden conversation — the bot replies but the
        # user never sees them because the conversation reader filters
        # ``deleted_at IS NULL``. Mint a fresh conversation and repoint
        # the link.
        if conv.deleted_at is None:
            return existing, False
        existing.conversation_id = await make_conversation_id()
        session.add(existing)
        return existing, True
    conversation_id = await make_conversation_id()
    link = IMThreadLink(
        org_id=org_id,
        workspace_id=workspace_id,
        account_id=account_id,
        channel_id=channel_id,
        scope_key=scope_key,
        scope_kind=scope_kind,
        conversation_id=conversation_id,
    )
    session.add(link)
    return link, True


async def claim_pending_queue_item(
    session: AsyncSession,
    *,
    lease_seconds: int,
    max_attempts: int = 5,
) -> IMRunQueueItem | None:
    """Claim one pending or stale-leased queue row with FOR UPDATE SKIP LOCKED.

    Reclaims a stalled ``started`` row whose ``claim_lease_expires_at`` has
    passed — without this, a worker that crashed mid-claim would leave the
    row stranded forever. ``max_attempts`` caps retries on a permanently
    broken event so a janitor can park it as ``failed`` separately rather
    than letting it spin.
    """
    now = datetime.now(UTC)
    stmt = (
        select(IMRunQueueItem)
        .where(
            IMRunQueueItem.attempts < max_attempts,  # type: ignore[arg-type]
            or_(
                IMRunQueueItem.status == "pending",  # type: ignore[arg-type]
                and_(
                    IMRunQueueItem.status == "started",  # type: ignore[arg-type]
                    IMRunQueueItem.claim_lease_expires_at.is_not(None),  # type: ignore[union-attr]
                    IMRunQueueItem.claim_lease_expires_at < now,  # type: ignore[arg-type,operator]
                ),
            ),
        )
        .order_by(IMRunQueueItem.created_at)  # type: ignore[arg-type]
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        return None
    item.status = "started"
    item.claimed_at = now
    item.claim_lease_expires_at = now + timedelta(seconds=lease_seconds)
    item.attempts += 1
    session.add(item)
    return item


async def mark_receipt_completed(
    session: AsyncSession,
    *,
    receipt_id: str,
) -> None:
    """Flip a receipt's status to ``completed`` after the run starts."""
    receipt = (
        await session.execute(
            select(IMWebhookReceipt).where(IMWebhookReceipt.id == receipt_id)  # type: ignore[arg-type]
        )
    ).scalar_one()
    receipt.status = "completed"
    session.add(receipt)


async def mark_queue_item_completed(
    session: AsyncSession,
    *,
    item_id: str,
) -> None:
    """Flip a queue row to ``completed`` after start_run succeeds.

    Without this, the row would stay in 'started' status with a finite
    ``claim_lease_expires_at`` set; once the lease expires the row would be
    re-claimed by the next poll and start_run would fire again — producing
    duplicate runs every ``lease_seconds`` until ``max_attempts`` parks the
    item. The receipt is a separate row with separate semantics; both must
    be flipped.
    """
    item = (
        await session.execute(
            select(IMRunQueueItem).where(IMRunQueueItem.id == item_id)  # type: ignore[arg-type]
        )
    ).scalar_one()
    item.status = "completed"
    item.claim_lease_expires_at = None
    session.add(item)


async def mark_queue_item_for_retry_or_fail(
    session: AsyncSession,
    *,
    item_id: str,
    max_attempts: int = 5,
) -> bool:
    """Handle a queue row whose ``start_run`` raised.

    The retry policy designed into ``claim_pending_queue_item`` expects:
    - Transient failure (DB blip, LLM provider down, network hiccup) →
      let the next poll re-claim after the lease expires, up to
      ``max_attempts`` times.
    - Persistent failure (broken event, code bug) → after
      ``attempts >= max_attempts`` the row is parked as ``failed``.

    Parking on the FIRST exception (the previous behaviour after the
    round-1 fix) would defeat ``max_attempts`` entirely: any transient
    error becomes a permanent silent drop. So this helper only flips to
    ``failed`` once the cap is reached; otherwise it rewinds to
    ``pending`` + clears the lease so the next poll re-claims.

    Returns ``True`` iff the row was permanently parked (caller should
    also flip the receipt to ``failed`` for symmetric observability).
    """
    item = (
        await session.execute(
            select(IMRunQueueItem).where(IMRunQueueItem.id == item_id)  # type: ignore[arg-type]
        )
    ).scalar_one()
    if item.attempts >= max_attempts:
        item.status = "failed"
        session.add(item)
        return True
    item.status = "pending"
    item.claim_lease_expires_at = None
    session.add(item)
    return False


async def mark_receipt_failed(
    session: AsyncSession,
    *,
    receipt_id: str,
) -> None:
    """Flip a receipt to ``failed`` when its queue row was permanently parked.

    Without this, the receipt would stay ``pending`` forever after a
    persistent failure — operators looking at the receipts table can't
    distinguish 'in-flight' from 'parked' rows. The receipt's unique
    constraint on (account_id, platform_event_id) is unaffected; a Feishu
    retry would still be dedupe-acked against the failed receipt.
    """
    receipt = (
        await session.execute(
            select(IMWebhookReceipt).where(IMWebhookReceipt.id == receipt_id)  # type: ignore[arg-type]
        )
    ).scalar_one()
    receipt.status = "failed"
    session.add(receipt)
