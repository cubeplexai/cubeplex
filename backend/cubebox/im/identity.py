"""Sender identity resolution + workspace-member gate.

Maps a Feishu sender (open_id) onto the cubebox user that should drive the
run, gated by workspace membership. The mapping is cached in
``im_identity_links`` so steady-state we don't hit the Feishu contact API
on every inbound.

Policy (per spec: A+C):
- If the sender's email matches a cubebox user AND that user is a member
  of the bot's workspace → run as that user.
- Otherwise → reject with a one-shot reply, drop the event.
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
    """Resolve a Feishu sender's email; one method only."""

    async def resolve_email(self, open_id: str) -> str | None: ...


class RejectionNotifier(Protocol):
    """Send a single rejection reply back to the chat."""

    async def send_to_chat(
        self, chat_id: str, reply_to_id: str | None, text: str
    ) -> str | None: ...


_REJECTION_TEXT = (
    "Sorry — your account isn't a member of this workspace, so I can't help "
    "here. Ask the workspace admin to add you, then try again."
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
        # No stable handle — can't even cache. Reject loudly.
        logger.warning("[IM identity] inbound event has no sender_ref or sender_open_id")
        await notifier.send_to_chat(event.channel_id, event.reply_to_id, _REJECTION_TEXT)
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
        return cached.user_id

    if not event.sender_open_id:
        # No open_id → can't ask Feishu. Reject.
        await notifier.send_to_chat(event.channel_id, event.reply_to_id, _REJECTION_TEXT)
        return None

    email = await resolver.resolve_email(event.sender_open_id)
    if not email:
        logger.info(
            "[IM identity] no email available for sender {} on account {}",
            event.sender_open_id,
            account.id,
        )
        await notifier.send_to_chat(event.channel_id, event.reply_to_id, _REJECTION_TEXT)
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
        await notifier.send_to_chat(event.channel_id, event.reply_to_id, _REJECTION_TEXT)
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
        await notifier.send_to_chat(event.channel_id, event.reply_to_id, _REJECTION_TEXT)
        return None

    # Cache. Race-safe: a concurrent ingest for the same sender may have
    # just inserted; uq_im_identity_link catches it and we re-read.
    link = IMIdentityLink(
        org_id=account.org_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        im_user_id=im_user_id,
        user_id=user.id,
    )
    session.add(link)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
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
        raise
    return user.id
