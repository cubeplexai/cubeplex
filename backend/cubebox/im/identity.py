"""Sender identity resolution + workspace-member gate.

Maps an IM sender onto the cubebox user that should drive the run, gated
by workspace membership. The mapping is cached in ``im_identity_links``
so steady-state we don't hit external APIs on every inbound.

Resolution order:
1. Cache hit in ``im_identity_links`` (by account_id + im_user_id).
2. Platform-specific email resolution (e.g. Feishu contact API).
3. ``/link`` command fallback — platforms without email APIs (Discord)
   use ``NullIdentityResolver`` which always returns None, forcing
   users to link manually via ``/link <email>``.
"""

from __future__ import annotations

from typing import Protocol

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.im.types import InboundEvent
from cubebox.models.im_connector import IMConnectorAccount, IMIdentityLink
from cubebox.models.membership import Membership
from cubebox.models.user import User


class IdentityResolver(Protocol):
    """Resolve an IM sender's email from their platform ID."""

    async def resolve_email(self, open_id: str) -> str | None: ...


class RejectionNotifier(Protocol):
    """Send a single rejection reply back to the chat."""

    async def send_to_chat(
        self, chat_id: str, reply_to_id: str | None, text: str
    ) -> str | None: ...


class NullIdentityResolver:
    """Always returns None — for platforms without email resolution APIs."""

    async def resolve_email(self, open_id: str) -> str | None:
        return None


_REJECTION_NOT_MEMBER = (
    "Sorry — your account isn't a member of this workspace, so I can't "
    "help here. Ask the workspace admin to add you, then try again."
)

_REJECTION_LINK_REQUIRED = (
    "I don't know who you are yet. Please run:\n"
    "  /link <your-cubebox-email>\n"
    "to bind your IM account, then try again."
)


async def resolve_or_reject(
    *,
    session: AsyncSession,
    account: IMConnectorAccount,
    event: InboundEvent,
    resolver: IdentityResolver,
    notifier: RejectionNotifier,
) -> str | None:
    """Return the cubebox user_id this run should execute as, or None if rejected.

    Order of checks:
    1. ``im_identity_links`` cache hit by (account_id, im_user_id).
    2. Resolve sender open_id → email via ``resolver``.
    3. Find a cubebox ``User`` row with that email.
    4. Confirm that user has a ``Membership`` in this account's workspace.
    5. On success, INSERT a cache row so subsequent messages are fast.

    The rejection text is sent once per rejected message (no caching on the
    negative path — Feishu side may have added the user to the workspace
    between attempts, and we want the next message to re-check).
    """
    im_user_id = event.sender_ref or event.sender_open_id or ""
    if not im_user_id:
        logger.warning("[IM identity] inbound event has no sender_ref or sender_open_id")
        await notifier.send_to_chat(
            event.channel_id,
            event.reply_to_id,
            _REJECTION_LINK_REQUIRED,
        )
        return None

    cached = (
        await session.execute(
            select(IMIdentityLink).where(
                IMIdentityLink.account_id == account.id,  # type: ignore[arg-type]
                IMIdentityLink.im_user_id == im_user_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if cached is not None:
        # Re-verify workspace membership on every cache hit. Without this,
        # a user removed from the workspace AFTER their first message would
        # keep sending IM messages forever (the cached link bypasses the
        # gate). Cheap O(1) PK lookup on memberships; well under the cost
        # of the Feishu API call we'd otherwise have to make.
        still_member = (
            await session.execute(
                select(Membership).where(
                    Membership.user_id == cached.user_id,  # type: ignore[arg-type]
                    Membership.workspace_id == account.workspace_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        if still_member is not None:
            return cached.user_id
        # Stale link: drop it so a future message goes through the full
        # resolver path again (which may match a re-added membership or
        # land in the not-member branch + reject).
        logger.info(
            "[IM identity] cached link {} stale — user {} no longer in workspace {}",
            cached.id,
            cached.user_id,
            account.workspace_id,
        )
        await session.delete(cached)
        await session.flush()
        await notifier.send_to_chat(
            event.channel_id,
            event.reply_to_id,
            _REJECTION_NOT_MEMBER,
        )
        return None

    if not event.sender_open_id:
        await notifier.send_to_chat(
            event.channel_id,
            event.reply_to_id,
            _REJECTION_LINK_REQUIRED,
        )
        return None

    email = await resolver.resolve_email(event.sender_open_id)
    if not email:
        logger.info(
            "[IM identity] no email available for sender {} on account {}",
            event.sender_open_id,
            account.id,
        )
        await notifier.send_to_chat(
            event.channel_id,
            event.reply_to_id,
            _REJECTION_LINK_REQUIRED,
        )
        return None

    # Email is stored lower-cased in cubebox; normalize on lookup to match
    # FastAPI-Users' default behavior.
    normalized = email.strip().lower()
    user = (
        await session.execute(select(User).where(User.email == normalized))  # type: ignore[arg-type]
    ).scalar_one_or_none()
    if user is None:
        logger.info(
            "[IM identity] no cubebox user for email {} (sender={})",
            normalized,
            event.sender_open_id,
        )
        await notifier.send_to_chat(
            event.channel_id,
            event.reply_to_id,
            _REJECTION_LINK_REQUIRED,
        )
        return None

    membership = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user.id,  # type: ignore[arg-type]
                Membership.workspace_id == account.workspace_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        logger.info(
            "[IM identity] user {} ({}) is not a member of workspace {}",
            user.id,
            normalized,
            account.workspace_id,
        )
        await notifier.send_to_chat(
            event.channel_id,
            event.reply_to_id,
            _REJECTION_NOT_MEMBER,
        )
        return None

    # Cache. Wrap the INSERT in a SAVEPOINT so a unique-key race against
    # a concurrent ingest for the same sender doesn't roll back the
    # outer ``ingest_inbound_event`` transaction (which already flushed
    # ``IMWebhookReceipt`` — losing that means the queue insert below
    # would reference a non-existent receipt_id and FK-fail, AND Feishu
    # retries would no longer dedupe). The savepoint lets us roll back
    # only the failed INSERT and re-read the winner's row.
    async with session.begin_nested():
        try:
            link = IMIdentityLink(
                org_id=account.org_id,
                workspace_id=account.workspace_id,
                account_id=account.id,
                im_user_id=im_user_id,
                user_id=user.id,
            )
            session.add(link)
            await session.flush()
            return user.id
        except IntegrityError:
            # SAVEPOINT-scoped rollback happens automatically on exit.
            pass
    existing = (
        await session.execute(
            select(IMIdentityLink).where(
                IMIdentityLink.account_id == account.id,  # type: ignore[arg-type]
                IMIdentityLink.im_user_id == im_user_id,  # type: ignore[arg-type]
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.user_id
    # Lost the race AND can't find the winner — shouldn't happen, but
    # don't silently corrupt the run by returning a wrong user_id.
    logger.warning(
        "[IM identity] link insert race for ({}, {}) lost, but winner not found",
        account.id,
        im_user_id,
    )
    await notifier.send_to_chat(
        event.channel_id,
        event.reply_to_id,
        _REJECTION_LINK_REQUIRED,
    )
    return None
