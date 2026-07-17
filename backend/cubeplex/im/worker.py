"""Durable IM run-queue worker.

Polls the ``im_run_queue`` table, claims pending or stale-leased rows via
``FOR UPDATE SKIP LOCKED``, calls ``RunManager.start_run`` with a
``RunContext`` derived from the account, then flips the receipt to
``completed`` and fires the ``on_run_started`` hook so the app can spawn
an outbound tailer for the run.

Crash safety: if ``start_run`` raises, the row stays in ``status='started'``
but with a finite ``claim_lease_expires_at``. After the lease expires, the
next worker poll re-claims via the lease branch in
``claim_pending_queue_item``. ``max_attempts`` caps the spin so a
permanently-broken event eventually parks (a janitor pass beyond v1 will
flip such rows to ``status='failed'``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from cubeplex.models.im_connector import IMConnectorAccount, IMIdentityLink, IMRunQueueItem
from cubeplex.repositories.im_connector import (
    claim_pending_queue_item,
    mark_queue_item_completed,
    mark_queue_item_for_retry_or_fail,
    mark_receipt_completed,
    mark_receipt_failed,
    rewind_queue_item_no_attempt_charge,
)
from cubeplex.streams.run_manager import RunContext


class _RunStarter(Protocol):
    async def start_run(
        self,
        *,
        conversation_id: str,
        content: str,
        attachments: list[str] | None,
        ctx: RunContext,
        cancel_pending_hitl: bool = False,
    ) -> str: ...


RunStartedCallback = Callable[[str, IMRunQueueItem], Awaitable[None]]
# (queue item, uploader user id) -> (attachment ids, rejected-file notes)
ResolveInboundAttachments = Callable[[IMRunQueueItem, str], Awaitable[tuple[list[str], list[str]]]]


async def process_one_queue_item(
    *,
    session_maker: async_sessionmaker[Any],
    run_manager: _RunStarter,
    on_run_started: RunStartedCallback | None,
    lease_seconds: int,
    resolve_inbound_attachments: ResolveInboundAttachments | None = None,
) -> bool:
    """Claim and process at most one queue row. Returns True iff a row was processed."""
    async with session_maker() as session:
        item = await claim_pending_queue_item(session, lease_seconds=lease_seconds)
        if item is None:
            return False
        account = (
            await session.execute(
                select(IMConnectorAccount).where(
                    IMConnectorAccount.id == item.account_id  # type: ignore[arg-type]
                )
            )
        ).scalar_one()
        if not account.enabled:
            # Account was disabled between enqueue and now. Park the queue
            # row + receipt terminally so we don't drain start_run for a
            # disabled connector (would still bill LLM tokens and send
            # replies under credentials operators just turned off). The
            # corresponding receipt moves to ``failed`` for observability.
            logger.info(
                "[IM worker] dropping queue item {} — account {} is disabled",
                item.id,
                account.id,
            )
            await mark_queue_item_completed(session, item_id=item.id)
            await mark_receipt_failed(session, receipt_id=item.receipt_id)
            await session.commit()
            return True
        # Look up the sender → cubeplex user override. ``im_identity_links``
        # is populated by the inbound gate when sender resolves to a
        # workspace member; if missing we fall back to ``acting_user_id``.
        effective_user_id: str = account.acting_user_id
        if item.sender_im_user_id:
            link = (
                await session.execute(
                    select(IMIdentityLink).where(
                        IMIdentityLink.account_id == item.account_id,  # type: ignore[arg-type]
                        IMIdentityLink.im_user_id == item.sender_im_user_id,  # type: ignore[arg-type]
                    )
                )
            ).scalar_one_or_none()
            if link is not None:
                effective_user_id = link.user_id
        # Resolve the sender's display name from the run's effective user so the
        # group-chat SenderBadge + cubepi sender attribution fire for IM
        # messages, mirroring the in-app send path (display_name or email).
        from cubeplex.models.user import User

        sender_user = await session.get(User, effective_user_id)
        sender_display_name: str | None = (
            (sender_user.display_name or sender_user.email) if sender_user is not None else None
        )
        # Refuse to dispatch IM messages against topic or standalone group
        # chat conversations — the group-chat / topic-aware path is not
        # implemented for IM in v1, so silently running with
        # is_group_chat=False would leak personal memory and drop sender
        # attribution (the dispatched RunContext would attribute the run to
        # the IM sender with no conversation_creator_user_id linkage).
        from cubeplex.models.conversation import Conversation

        conv_row = (
            await session.execute(
                select(Conversation).where(
                    Conversation.id == item.conversation_id,  # type: ignore[arg-type]
                )
            )
        ).scalar_one_or_none()
        # An IM-origin Topic carries source metadata under attributes["im"];
        # its presence is what makes a topic / group-chat conversation
        # dispatchable. Non-IM topics are still out of scope for IM runs.
        topic_row: Any = None
        if conv_row is not None and conv_row.topic_id is not None:
            from cubeplex.models.topic import Topic

            topic_row = (
                await session.execute(select(Topic).where(Topic.id == conv_row.topic_id))
            ).scalar_one_or_none()

        if conv_row is not None and (conv_row.topic_id is not None or conv_row.is_group_chat):
            im_bound = topic_row is not None and "im" in (topic_row.attributes or {})
            if not im_bound:
                logger.warning(
                    "[IM worker] refusing to dispatch run for queue item {} — "
                    "conversation {} is a non-IM topic / group chat (v1 scope)",
                    item.id,
                    item.conversation_id,
                )
                await mark_queue_item_completed(session, item_id=item.id)
                await mark_receipt_failed(session, receipt_id=item.receipt_id)
                await session.commit()
                return True

        topic_creator_user_id: str | None = (
            topic_row.creator_user_id if topic_row is not None else None
        )
        sandbox_mode: str | None = topic_row.sandbox_mode if topic_row is not None else None
        await session.commit()
        captured = {
            "conversation_id": item.conversation_id,
            "content": item.content,
            "receipt_id": item.receipt_id,
            "org_id": account.org_id,
            "workspace_id": account.workspace_id,
            "acting_user_id": effective_user_id,
            "sender_display_name": sender_display_name,
            "topic_id": (conv_row.topic_id if conv_row is not None else None),
            "is_group_chat": (conv_row.is_group_chat if conv_row is not None else False),
            "sandbox_mode": sandbox_mode,
            "topic_creator_user_id": topic_creator_user_id,
        }
        captured_item = item

    # Resolve inbound file attachments (download → AttachmentService.upload)
    # BEFORE start_run, since the ids are an argument to it. Runs at most once
    # per inbound message: the resolved ids + the noted content are persisted
    # onto the queue row, so a re-claim (e.g. the 'already has an active run'
    # rewind below) reuses them instead of re-downloading / re-uploading.
    attachment_ids: list[str] | None = captured_item.attachment_ids
    # Resolve only when not yet resolved. Test ``is None`` (not falsiness): an
    # all-rejected message persists ``attachment_ids = []``, and ``not []`` is
    # True, which would otherwise re-download + re-prepend the rejection notes
    # on every re-claim.
    if (
        resolve_inbound_attachments is not None
        and captured_item.attachment_refs
        and captured_item.attachment_ids is None
    ):
        ids, notes = await resolve_inbound_attachments(captured_item, captured["acting_user_id"])
        attachment_ids = ids or None
        if notes:
            captured["content"] = "\n".join([*notes, captured["content"]])
        async with session_maker() as session:
            row = await session.get(IMRunQueueItem, captured_item.id)
            if row is not None:
                row.attachment_ids = ids
                row.content = captured["content"]
                await session.commit()

    try:
        run_id = await run_manager.start_run(
            conversation_id=captured["conversation_id"],
            content=captured["content"],
            attachments=attachment_ids,
            ctx=RunContext(
                user_id=captured["acting_user_id"],
                org_id=captured["org_id"],
                workspace_id=captured["workspace_id"],
                conversation_id=captured["conversation_id"],
                trigger="im",
                topic_id=captured["topic_id"],
                is_group_chat=captured["is_group_chat"],
                sandbox_mode=captured["sandbox_mode"],
                topic_creator_user_id=(captured["topic_creator_user_id"]),
                sender_display_name=captured["sender_display_name"],
            ),
            cancel_pending_hitl=True,
        )
    except Exception as exc:
        # ``RunManager.start_run`` raises a plain RuntimeError when the
        # conversation already has an active run. That's a normal UX
        # scenario (user sends a follow-up while the first reply is
        # still rendering, ~5–60s typical), NOT a failed inbound —
        # rewinding to pending without consuming an attempt lets the
        # next poll re-claim once the first run finishes. At default
        # poll=1s, max_attempts=5, charging the attempt would park the
        # follow-up as ``failed`` in ~5s even though the conversation
        # just needed a few seconds to finish.
        if isinstance(exc, RuntimeError) and "already has an active run" in str(exc):
            logger.info(
                "[IM worker] queue item {} waiting on active run (no attempt charge)",
                captured_item.id,
            )
            async with session_maker() as session:
                await rewind_queue_item_no_attempt_charge(session, item_id=captured_item.id)
                await session.commit()
            return False
        logger.opt(exception=True).warning(
            "[IM worker] start_run failed for queue item {}; leaving for re-claim",
            captured_item.id,
        )
        # Honor max_attempts: rewind to 'pending' for transient errors,
        # park as 'failed' only when the attempt cap is reached. When the
        # queue row is permanently parked, also flip the receipt to
        # 'failed' for symmetric observability — otherwise the receipt
        # stays 'pending' forever and operators can't tell in-flight from
        # parked rows.
        async with session_maker() as session:
            parked = await mark_queue_item_for_retry_or_fail(session, item_id=captured_item.id)
            if parked:
                await mark_receipt_failed(session, receipt_id=captured["receipt_id"])
            await session.commit()
        # Return False so the worker loop's idle-sleep branch fires —
        # otherwise the loop would immediately re-claim the rewound row
        # within milliseconds, hammering a downstream service that just
        # failed (thundering herd against a transient outage).
        return False

    # Mark the queue row + receipt terminal BEFORE invoking the
    # ``on_run_started`` hook. ``start_run`` has already executed (LLM
    # tokens billed, run row created), so requeuing on a tailer-setup
    # failure would re-fire ``start_run`` and produce duplicate runs
    # + duplicate billing for one inbound message. Keep the queue row
    # at-most-once for ``start_run`` and treat tailer failures as a
    # separate post-success failure mode (logged + best-effort error
    # bubble; no requeue).
    async with session_maker() as session:
        await mark_receipt_completed(session, receipt_id=captured["receipt_id"])
        await mark_queue_item_completed(session, item_id=captured_item.id)
        await session.commit()

    if on_run_started is not None:
        try:
            await on_run_started(run_id, captured_item)
        except Exception:
            # Tailer setup blew up after ``start_run`` already succeeded.
            # The user will not see the streaming reply — that's a UX
            # regression we cannot fix here (re-running would double-bill).
            # Log loudly so operators can investigate and (optionally)
            # tell the user via another channel.
            logger.exception(
                "[IM worker] on_run_started failed after start_run; "
                "user will not see a streaming reply for run {} (queue item {})",
                run_id,
                captured_item.id,
            )
    return True


class IMRunQueueWorker:
    """Polls the durable queue and processes items until stopped."""

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[Any],
        run_manager: _RunStarter,
        on_run_started: RunStartedCallback | None,
        resolve_inbound_attachments: ResolveInboundAttachments | None = None,
        poll_interval: float = 1.0,
        lease_seconds: int = 300,
    ) -> None:
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._on_run_started = on_run_started
        self._resolve_inbound_attachments = resolve_inbound_attachments
        self._poll_interval = poll_interval
        self._lease_seconds = lease_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                ran = await process_one_queue_item(
                    session_maker=self._session_maker,
                    run_manager=self._run_manager,
                    on_run_started=self._on_run_started,
                    lease_seconds=self._lease_seconds,
                    resolve_inbound_attachments=self._resolve_inbound_attachments,
                )
            except Exception:
                logger.opt(exception=True).warning("[IM worker] poll error")
                ran = False
            # ``ran=False`` covers both "queue was empty" and "start_run
            # raised" (process_one_queue_item returns False in both cases —
            # the latter intentionally so the failure path sleeps the
            # poll_interval instead of immediately re-claiming the same
            # row, which would hammer a downstream service that just
            # failed (thundering herd, defeating max_attempts).
            if not ran:
                await asyncio.sleep(self._poll_interval)

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="im-run-queue-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
