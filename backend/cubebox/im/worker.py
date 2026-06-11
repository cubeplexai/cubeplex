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

from cubebox.models.im_connector import IMConnectorAccount, IMRunQueueItem
from cubebox.repositories.im_connector import (
    claim_pending_queue_item,
    mark_queue_item_completed,
    mark_queue_item_failed,
    mark_receipt_completed,
)
from cubebox.streams.run_manager import RunContext


class _RunStarter(Protocol):
    async def start_run(
        self,
        *,
        conversation_id: str,
        content: str,
        attachments: list[str] | None,
        ctx: RunContext,
    ) -> str: ...


RunStartedCallback = Callable[[str, IMRunQueueItem], Awaitable[None]]


async def process_one_queue_item(
    *,
    session_maker: async_sessionmaker[Any],
    run_manager: _RunStarter,
    on_run_started: RunStartedCallback | None,
    lease_seconds: int,
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
        await session.commit()
        captured = {
            "conversation_id": item.conversation_id,
            "content": item.content,
            "receipt_id": item.receipt_id,
            "org_id": account.org_id,
            "workspace_id": account.workspace_id,
            "acting_user_id": account.acting_user_id,
        }
        captured_item = item

    try:
        run_id = await run_manager.start_run(
            conversation_id=captured["conversation_id"],
            content=captured["content"],
            attachments=None,
            ctx=RunContext(
                user_id=captured["acting_user_id"],
                org_id=captured["org_id"],
                workspace_id=captured["workspace_id"],
                trigger="im",
            ),
        )
    except Exception:
        logger.warning(
            "[IM worker] start_run failed for queue item {}; leaving for re-claim",
            captured_item.id,
            exc_info=True,
        )
        async with session_maker() as session:
            await mark_queue_item_failed(session, item_id=captured_item.id)
            await session.commit()
        return True

    # Mark BOTH the receipt AND the queue row terminal. Without flipping the
    # queue row's status off 'started', claim_pending_queue_item would re-claim
    # it via the lease-expiry branch and re-fire start_run up to max_attempts
    # times — every accepted IM message would become 5 duplicate runs ~5 min
    # apart, billed N times.
    async with session_maker() as session:
        await mark_receipt_completed(session, receipt_id=captured["receipt_id"])
        await mark_queue_item_completed(session, item_id=captured_item.id)
        await session.commit()

    if on_run_started is not None:
        try:
            await on_run_started(run_id, captured_item)
        except Exception:
            logger.warning(
                "[IM worker] on_run_started callback raised for run {}",
                run_id,
                exc_info=True,
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
        poll_interval: float = 1.0,
        lease_seconds: int = 300,
    ) -> None:
        self._session_maker = session_maker
        self._run_manager = run_manager
        self._on_run_started = on_run_started
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
                )
            except Exception:
                logger.warning("[IM worker] poll error", exc_info=True)
                ran = False
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
