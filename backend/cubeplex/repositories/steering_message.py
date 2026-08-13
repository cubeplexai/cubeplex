"""Scoped state machine for durable HITL steering messages."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.models import Conversation, SteeringMessage, SteeringMessageState
from cubeplex.repositories.base import ScopedRepository

MAX_MESSAGE_BYTES = 32 * 1024
MAX_ACTIVE_ROWS_PER_RUN = 32
MAX_ACTIVE_BYTES_PER_RUN = 256 * 1024
MAX_VISIBLE_ROWS_PER_CONVERSATION = 100
MAX_VISIBLE_BYTES_PER_CONVERSATION = 1024 * 1024
DELIVERY_LEASE_SECONDS = 30
TOMBSTONE_RETENTION = timedelta(hours=24)

ACTIVE_STATES = (
    SteeringMessageState.queued,
    SteeringMessageState.dispatched,
    SteeringMessageState.cancel_requested,
)
VISIBLE_STATES = (*ACTIVE_STATES, SteeringMessageState.failed)
BOOTSTRAP_STATES = (
    SteeringMessageState.queued,
    SteeringMessageState.dispatched,
    SteeringMessageState.failed,
)


class SteeringMessageConflictError(Exception):
    """A client steer ID was reused with different immutable data."""


class SteeringMessageContentTooLargeError(Exception):
    """A single steering message exceeds the UTF-8 byte limit."""


class SteeringMessageQueueFullError(Exception):
    """The run or conversation queue reached a configured bound."""


class SteeringConversationUnavailableError(Exception):
    """The scoped conversation is absent or was soft-deleted."""


@dataclass(frozen=True)
class SteeringQueueUsage:
    rows: int
    content_bytes: int


class SteeringMessageRepository(ScopedRepository[SteeringMessage]):
    """Queue operations that participate in the caller's transaction."""

    model = SteeringMessage

    async def get_by_client_id(
        self,
        *,
        conversation_id: str,
        client_steer_id: str,
        for_update: bool = False,
    ) -> SteeringMessage | None:
        stmt = self._scoped_select().where(
            cast(Any, SteeringMessage.conversation_id) == conversation_id,
            cast(Any, SteeringMessage.client_steer_id) == client_steer_id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    @staticmethod
    def _assert_retry_matches(
        row: SteeringMessage,
        *,
        content: str,
        sender_user_id: str,
    ) -> None:
        if row.content != content or row.sender_user_id != sender_user_id:
            raise SteeringMessageConflictError(row.client_steer_id)

    async def _usage(self, *predicates: Any) -> SteeringQueueUsage:
        stmt = select(
            func.count(cast(Any, SteeringMessage.id)),
            func.coalesce(func.sum(func.octet_length(SteeringMessage.content)), 0),
        ).where(
            cast(Any, SteeringMessage.org_id) == self.org_id,
            cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
            *predicates,
        )
        rows, content_bytes = (await self.session.execute(stmt)).one()
        return SteeringQueueUsage(rows=int(rows), content_bytes=int(content_bytes))

    async def enqueue(
        self,
        *,
        conversation_id: str,
        run_id: str,
        client_steer_id: str,
        content: str,
        sender_user_id: str,
        sender_display_name: str | None,
        hitl_question_id: str,
    ) -> tuple[SteeringMessage, bool]:
        """Lock a conversation, enforce bounds, and enqueue idempotently."""
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_MESSAGE_BYTES:
            raise SteeringMessageContentTooLargeError

        conversation_stmt = (
            select(cast(Any, Conversation.id))
            .where(
                cast(Any, Conversation.id) == conversation_id,
                cast(Any, Conversation.org_id) == self.org_id,
                cast(Any, Conversation.workspace_id) == self.workspace_id,
                cast(Any, Conversation.deleted_at).is_(None),
            )
            .with_for_update()
        )
        locked_id = (await self.session.execute(conversation_stmt)).scalar_one_or_none()
        if locked_id is None:
            raise SteeringConversationUnavailableError(conversation_id)

        existing = await self.get_by_client_id(
            conversation_id=conversation_id,
            client_steer_id=client_steer_id,
            for_update=True,
        )
        if existing is not None:
            self._assert_retry_matches(
                existing,
                content=content,
                sender_user_id=sender_user_id,
            )
            return existing, False

        run_usage = await self._usage(
            cast(Any, SteeringMessage.run_id) == run_id,
            cast(Any, SteeringMessage.state).in_(ACTIVE_STATES),
        )
        conversation_usage = await self._usage(
            cast(Any, SteeringMessage.conversation_id) == conversation_id,
            cast(Any, SteeringMessage.state).in_(VISIBLE_STATES),
        )
        if (
            run_usage.rows >= MAX_ACTIVE_ROWS_PER_RUN
            or run_usage.content_bytes + content_bytes > MAX_ACTIVE_BYTES_PER_RUN
            or conversation_usage.rows >= MAX_VISIBLE_ROWS_PER_CONVERSATION
            or conversation_usage.content_bytes + content_bytes > MAX_VISIBLE_BYTES_PER_CONVERSATION
        ):
            raise SteeringMessageQueueFullError

        row = SteeringMessage(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            client_steer_id=client_steer_id,
            content=content,
            sender_user_id=sender_user_id,
            sender_display_name=sender_display_name,
            hitl_question_id=hitl_question_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row, True

    async def list_for_bootstrap(self, conversation_id: str) -> list[SteeringMessage]:
        stmt = (
            self._scoped_select()
            .where(
                cast(Any, SteeringMessage.conversation_id) == conversation_id,
                cast(Any, SteeringMessage.state).in_(BOOTSTRAP_STATES),
            )
            .order_by(
                cast(Any, SteeringMessage.created_at),
                cast(Any, SteeringMessage.id),
            )
            .limit(MAX_VISIBLE_ROWS_PER_CONVERSATION)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def claim_queued(
        self,
        *,
        run_id: str,
        owner: str,
        limit: int = MAX_ACTIVE_ROWS_PER_RUN,
        now: datetime | None = None,
    ) -> list[SteeringMessage]:
        claimed_at = now or datetime.now(UTC)
        stmt = (
            self._scoped_select()
            .where(
                cast(Any, SteeringMessage.run_id) == run_id,
                cast(Any, SteeringMessage.state) == SteeringMessageState.queued,
                cast(Any, SteeringMessage.created_at) <= claimed_at,
            )
            .order_by(
                cast(Any, SteeringMessage.created_at),
                cast(Any, SteeringMessage.id),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        for row in rows:
            row.state = SteeringMessageState.dispatched
            row.delivery_owner = owner
            row.delivery_lease_until = claimed_at + timedelta(seconds=DELIVERY_LEASE_SECONDS)
            row.updated_at = claimed_at
            self.session.add(row)
        await self.session.flush()
        return rows

    async def list_expired_claims(
        self,
        *,
        run_id: str,
        now: datetime | None = None,
    ) -> list[SteeringMessage]:
        expired_at = now or datetime.now(UTC)
        stmt = (
            self._scoped_select()
            .where(
                cast(Any, SteeringMessage.run_id) == run_id,
                cast(Any, SteeringMessage.state).in_(
                    (
                        SteeringMessageState.dispatched,
                        SteeringMessageState.cancel_requested,
                    )
                ),
                cast(Any, SteeringMessage.delivery_lease_until).is_not(None),
                cast(Any, SteeringMessage.delivery_lease_until) < expired_at,
            )
            .order_by(
                cast(Any, SteeringMessage.created_at),
                cast(Any, SteeringMessage.id),
            )
            .limit(MAX_ACTIVE_ROWS_PER_RUN)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_active_for_run(self, run_id: str) -> list[SteeringMessage]:
        stmt = (
            self._scoped_select()
            .where(
                cast(Any, SteeringMessage.run_id) == run_id,
                cast(Any, SteeringMessage.state).in_(ACTIVE_STATES),
            )
            .order_by(
                cast(Any, SteeringMessage.created_at),
                cast(Any, SteeringMessage.id),
            )
            .limit(MAX_ACTIVE_ROWS_PER_RUN)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def requeue_expired_claim(
        self,
        *,
        row_id: str,
        now: datetime | None = None,
    ) -> bool:
        expired_at = now or datetime.now(UTC)
        stmt = (
            update(SteeringMessage)
            .where(
                cast(Any, SteeringMessage.id) == row_id,
                cast(Any, SteeringMessage.org_id) == self.org_id,
                cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
                cast(Any, SteeringMessage.state) == SteeringMessageState.dispatched,
                cast(Any, SteeringMessage.delivery_lease_until).is_not(None),
                cast(Any, SteeringMessage.delivery_lease_until) < expired_at,
            )
            .values(
                state=SteeringMessageState.queued,
                delivery_owner=None,
                delivery_lease_until=None,
                updated_at=expired_at,
            )
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def return_claim_to_queue(self, *, row_id: str, owner: str) -> bool:
        stmt = (
            update(SteeringMessage)
            .where(
                cast(Any, SteeringMessage.id) == row_id,
                cast(Any, SteeringMessage.org_id) == self.org_id,
                cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
                cast(Any, SteeringMessage.state) == SteeringMessageState.dispatched,
                cast(Any, SteeringMessage.delivery_owner) == owner,
            )
            .values(
                state=SteeringMessageState.queued,
                delivery_owner=None,
                delivery_lease_until=None,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def request_cancel(
        self,
        *,
        conversation_id: str,
        client_steer_id: str,
    ) -> SteeringMessage | None:
        row = await self.get_by_client_id(
            conversation_id=conversation_id,
            client_steer_id=client_steer_id,
            for_update=True,
        )
        if row is None:
            return None
        now = datetime.now(UTC)
        if row.state in (SteeringMessageState.queued, SteeringMessageState.failed):
            row.state = SteeringMessageState.cancelled
            row.delivery_owner = None
            row.delivery_lease_until = None
            row.updated_at = now
        elif row.state == SteeringMessageState.dispatched:
            row.state = SteeringMessageState.cancel_requested
            row.updated_at = now
        self.session.add(row)
        await self.session.flush()
        return row

    async def _transition_owned(
        self,
        *,
        row_id: str,
        owner: str,
        from_states: Sequence[SteeringMessageState],
        to_state: SteeringMessageState,
    ) -> bool:
        stmt = (
            update(SteeringMessage)
            .where(
                cast(Any, SteeringMessage.id) == row_id,
                cast(Any, SteeringMessage.org_id) == self.org_id,
                cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
                cast(Any, SteeringMessage.state).in_(from_states),
                cast(Any, SteeringMessage.delivery_owner) == owner,
            )
            .values(
                state=to_state,
                delivery_owner=None,
                delivery_lease_until=None,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def mark_owned_injected(self, *, row_id: str, owner: str) -> bool:
        return await self._transition_owned(
            row_id=row_id,
            owner=owner,
            from_states=(
                SteeringMessageState.dispatched,
                SteeringMessageState.cancel_requested,
            ),
            to_state=SteeringMessageState.injected,
        )

    async def mark_owned_cancelled(self, *, row_id: str, owner: str) -> bool:
        return await self._transition_owned(
            row_id=row_id,
            owner=owner,
            from_states=(SteeringMessageState.cancel_requested,),
            to_state=SteeringMessageState.cancelled,
        )

    async def reconcile_terminal(
        self,
        *,
        row_id: str,
        state: SteeringMessageState,
    ) -> bool:
        if state not in (SteeringMessageState.injected, SteeringMessageState.cancelled):
            raise ValueError(f"invalid reconciliation state: {state}")
        stmt = (
            update(SteeringMessage)
            .where(
                cast(Any, SteeringMessage.id) == row_id,
                cast(Any, SteeringMessage.org_id) == self.org_id,
                cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
                cast(Any, SteeringMessage.state).in_(ACTIVE_STATES),
            )
            .values(
                state=state,
                delivery_owner=None,
                delivery_lease_until=None,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def fail_active_for_run(self, run_id: str) -> int:
        stmt = (
            update(SteeringMessage)
            .where(
                cast(Any, SteeringMessage.org_id) == self.org_id,
                cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
                cast(Any, SteeringMessage.run_id) == run_id,
                cast(Any, SteeringMessage.state).in_(ACTIVE_STATES),
            )
            .values(
                state=SteeringMessageState.failed,
                delivery_owner=None,
                delivery_lease_until=None,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def fail_queued(self, *, row_id: str) -> bool:
        """Fail one orphan only while no delivery owner has claimed it."""
        stmt = (
            update(SteeringMessage)
            .where(
                cast(Any, SteeringMessage.id) == row_id,
                cast(Any, SteeringMessage.org_id) == self.org_id,
                cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
                cast(Any, SteeringMessage.state) == SteeringMessageState.queued,
            )
            .values(
                state=SteeringMessageState.failed,
                delivery_owner=None,
                delivery_lease_until=None,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(stmt)
        return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def delete_for_conversation(self, conversation_id: str) -> int:
        stmt = delete(SteeringMessage).where(
            cast(Any, SteeringMessage.org_id) == self.org_id,
            cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
            cast(Any, SteeringMessage.conversation_id) == conversation_id,
        )
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def purge_terminal_tombstones(
        self,
        *,
        limit: int = 100,
        now: datetime | None = None,
    ) -> int:
        cutoff = (now or datetime.now(UTC)) - TOMBSTONE_RETENTION
        ids = (
            select(cast(Any, SteeringMessage.id))
            .where(
                cast(Any, SteeringMessage.org_id) == self.org_id,
                cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
                cast(Any, SteeringMessage.state).in_(
                    (SteeringMessageState.injected, SteeringMessageState.cancelled)
                ),
                cast(Any, SteeringMessage.updated_at) < cutoff,
            )
            .order_by(cast(Any, SteeringMessage.updated_at))
            .limit(limit)
        )
        stmt = delete(SteeringMessage).where(cast(Any, SteeringMessage.id).in_(ids))
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def count_for_conversation(self, conversation_id: str) -> int:
        stmt = select(func.count(cast(Any, SteeringMessage.id))).where(
            cast(Any, SteeringMessage.org_id) == self.org_id,
            cast(Any, SteeringMessage.workspace_id) == self.workspace_id,
            cast(Any, SteeringMessage.conversation_id) == conversation_id,
        )
        return int((await self.session.execute(stmt)).scalar_one())


async def list_active_steering_for_reconciliation(
    session: AsyncSession,
    *,
    limit: int = 100,
    after: tuple[datetime, str] | None = None,
) -> list[SteeringMessage]:
    """Bounded internal maintenance scan across workspace scopes."""
    stmt = select(SteeringMessage).where(cast(Any, SteeringMessage.state).in_(ACTIVE_STATES))
    if after is not None:
        updated_at, row_id = after
        stmt = stmt.where(
            or_(
                cast(Any, SteeringMessage.updated_at) > updated_at,
                (
                    (cast(Any, SteeringMessage.updated_at) == updated_at)
                    & (cast(Any, SteeringMessage.id) > row_id)
                ),
            )
        )
    stmt = stmt.order_by(
        cast(Any, SteeringMessage.updated_at),
        cast(Any, SteeringMessage.id),
    ).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def purge_terminal_steering_tombstones(
    session: AsyncSession,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> int:
    """Delete a bounded global batch after the idempotency window."""
    cutoff = (now or datetime.now(UTC)) - TOMBSTONE_RETENTION
    ids = (
        select(cast(Any, SteeringMessage.id))
        .where(
            cast(Any, SteeringMessage.state).in_(
                (SteeringMessageState.injected, SteeringMessageState.cancelled)
            ),
            cast(Any, SteeringMessage.updated_at) < cutoff,
        )
        .order_by(cast(Any, SteeringMessage.updated_at))
        .limit(limit)
    )
    result = await session.execute(
        delete(SteeringMessage).where(cast(Any, SteeringMessage.id).in_(ids))
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
