"""Lease-based worker for durable trigger events."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from loguru import logger
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from uuid_utils import uuid7

from cubeplex.models.trigger import TriggerEvent
from cubeplex.triggers.pipeline import TriggerPipeline


async def claim_trigger_events(
    session: AsyncSession,
    *,
    now: datetime,
    lease_seconds: int,
    limit: int,
) -> list[tuple[str, str]]:
    rows = list(
        await session.scalars(
            select(TriggerEvent)
            .where(
                or_(
                    and_(
                        cast(Any, TriggerEvent.status) == "pending",
                        or_(
                            cast(Any, TriggerEvent.next_attempt_at).is_(None),
                            cast(Any, TriggerEvent.next_attempt_at) <= now,
                        ),
                    ),
                    and_(
                        cast(Any, TriggerEvent.status) == "claimed",
                        cast(Any, TriggerEvent.claim_lease_expires_at) <= now,
                    ),
                )
            )
            .order_by(cast(Any, TriggerEvent.received_at))
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    claimed: list[tuple[str, str]] = []
    for event in rows:
        owner = str(uuid7())
        event.status = "claimed"
        event.claim_owner = owner
        event.claim_lease_expires_at = now + timedelta(seconds=lease_seconds)
        event.next_attempt_at = None
        event.attempts += 1
        claimed.append((event.id, owner))
    return claimed


class TriggerEventWorker:
    def __init__(
        self,
        *,
        session_maker: async_sessionmaker[Any],
        pipeline: TriggerPipeline,
        poll_interval_seconds: float = 1.0,
        lease_seconds: int = 120,
        batch_limit: int = 20,
    ) -> None:
        self._session_maker = session_maker
        self._pipeline = pipeline
        self._poll_interval = poll_interval_seconds
        self._lease_seconds = lease_seconds
        self._batch_limit = batch_limit
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def start(self) -> None:
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="trigger-event-worker")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                processed = await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("trigger event worker poll failed")
                processed = 0
            if processed == 0:
                await asyncio.sleep(self._poll_interval)

    async def poll_once(self) -> int:
        async with self._session_maker() as session:
            claimed = await claim_trigger_events(
                session,
                now=datetime.now(UTC),
                lease_seconds=self._lease_seconds,
                limit=self._batch_limit,
            )
            await session.commit()
        for event_id, owner in claimed:
            try:
                await self._pipeline.process_claimed(event_id, claim_owner=owner)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning(
                    "trigger event processing interrupted; lease will recover event {}",
                    event_id,
                )
        return len(claimed)
