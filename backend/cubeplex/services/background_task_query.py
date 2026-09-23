"""Read-only projections for conversation background tasks and events."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.config import config
from cubeplex.models.background_task import (
    INFLIGHT_TASK_STATES,
    BackgroundTask,
    BackgroundTaskEvent,
    BackgroundTaskEventState,
)
from cubeplex.models.sandbox_command import SandboxCommand

EventDelivery = Literal["pending", "all"]
PENDING_EVENT_STATES = (
    BackgroundTaskEventState.pending.value,
    BackgroundTaskEventState.claimed.value,
)


class InvalidBackgroundTaskCursorError(ValueError):
    """The event cursor was malformed or belongs to another query."""


@dataclass(frozen=True)
class TaskProjection:
    task: BackgroundTask
    command: SandboxCommand | None
    has_pending_event: bool
    has_claimed_event: bool

    @property
    def cleanup_pending(self) -> bool:
        command_cleanup = self.command is not None and self.command.log_state in (
            "pending",
            "retrying",
        )
        stop_cleanup = (
            self.task.stop_requested_at is not None and self.task.state in INFLIGHT_TASK_STATES
        )
        cancelled_delivery_cleanup = (
            self.task.notifications_cancelled_at is not None and self.has_claimed_event
        )
        return command_cleanup or stop_cleanup or cancelled_delivery_cleanup

    @property
    def can_stop(self) -> bool:
        if self.task.stop_requested_at is not None:
            return False
        if self.task.state in INFLIGHT_TASK_STATES:
            return True
        return self.has_pending_event and self.task.notifications_cancelled_at is None

    @property
    def remote_cancel_supported(self) -> bool:
        return (
            self.command is not None
            and self.command.provider_ref is not None
            and self.task.state in INFLIGHT_TASK_STATES
        )


@dataclass(frozen=True)
class TaskEventProjection:
    event: BackgroundTaskEvent
    task_kind: str


@dataclass(frozen=True)
class TaskEventPage:
    items: tuple[TaskEventProjection, ...]
    next_cursor: str | None
    has_more: bool


@dataclass(frozen=True)
class BackgroundTaskSummary:
    has_inflight: bool
    has_pending: bool
    has_cleanup: bool
    can_stop: bool


@dataclass(frozen=True)
class _EventCursor:
    created_at: datetime
    event_id: str


class BackgroundTaskQueryService:
    """Build UI projections exclusively from persisted database facts."""

    def __init__(self, session: AsyncSession, *, org_id: str, workspace_id: str) -> None:
        self.session = session
        self.org_id = org_id
        self.workspace_id = workspace_id

    def _task_query(self, conversation_id: str):  # type: ignore[no-untyped-def]
        pending_event = exists(
            select(col(BackgroundTaskEvent.id)).where(
                col(BackgroundTaskEvent.org_id) == self.org_id,
                col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                col(BackgroundTaskEvent.conversation_id) == conversation_id,
                col(BackgroundTaskEvent.task_id) == col(BackgroundTask.id),
                col(BackgroundTaskEvent.state).in_(PENDING_EVENT_STATES),
            )
        )
        claimed_event = exists(
            select(col(BackgroundTaskEvent.id)).where(
                col(BackgroundTaskEvent.org_id) == self.org_id,
                col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                col(BackgroundTaskEvent.conversation_id) == conversation_id,
                col(BackgroundTaskEvent.task_id) == col(BackgroundTask.id),
                col(BackgroundTaskEvent.state) == BackgroundTaskEventState.claimed.value,
            )
        )
        return (
            select(
                BackgroundTask,
                SandboxCommand,
                pending_event.label("has_pending_event"),
                claimed_event.label("has_claimed_event"),
            )
            .outerjoin(
                SandboxCommand,
                and_(
                    col(SandboxCommand.task_id) == col(BackgroundTask.id),
                    col(SandboxCommand.org_id) == self.org_id,
                    col(SandboxCommand.workspace_id) == self.workspace_id,
                ),
            )
            .where(
                col(BackgroundTask.org_id) == self.org_id,
                col(BackgroundTask.workspace_id) == self.workspace_id,
                col(BackgroundTask.conversation_id) == conversation_id,
            )
        )

    @staticmethod
    def _projection(
        row: Row[tuple[BackgroundTask, SandboxCommand | None, bool, bool]],
    ) -> TaskProjection:
        task, command, has_pending, has_claimed = row
        return TaskProjection(task, command, bool(has_pending), bool(has_claimed))

    async def list_tasks(
        self, *, conversation_id: str, task_ids: tuple[str, ...] | None = None
    ) -> list[TaskProjection]:
        query = self._task_query(conversation_id)
        if task_ids is None:
            actionable_event = exists(
                select(col(BackgroundTaskEvent.id)).where(
                    col(BackgroundTaskEvent.org_id) == self.org_id,
                    col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                    col(BackgroundTaskEvent.conversation_id) == conversation_id,
                    col(BackgroundTaskEvent.task_id) == col(BackgroundTask.id),
                    col(BackgroundTaskEvent.state).in_(PENDING_EVENT_STATES),
                )
            )
            query = query.where(
                or_(
                    col(BackgroundTask.state).in_(INFLIGHT_TASK_STATES),
                    col(SandboxCommand.log_state).in_(("pending", "retrying")),
                    actionable_event,
                )
            )
        else:
            query = query.where(col(BackgroundTask.id).in_(task_ids))
        rows = (
            await self.session.execute(
                query.order_by(col(BackgroundTask.created_at).desc(), col(BackgroundTask.id).desc())
            )
        ).all()
        return [self._projection(row) for row in rows]

    async def get_task(self, *, conversation_id: str, task_id: str) -> TaskProjection | None:
        row = (
            await self.session.execute(
                self._task_query(conversation_id).where(col(BackgroundTask.id) == task_id)
            )
        ).one_or_none()
        return None if row is None else self._projection(row)

    async def list_events(
        self,
        *,
        conversation_id: str,
        delivery: EventDelivery,
        cursor: str | None,
        limit: int,
    ) -> TaskEventPage:
        after = (
            self._decode_cursor(cursor, conversation_id=conversation_id, delivery=delivery)
            if cursor is not None
            else None
        )
        query = (
            select(BackgroundTaskEvent, col(BackgroundTask.kind))
            .join(
                BackgroundTask,
                and_(
                    col(BackgroundTask.id) == col(BackgroundTaskEvent.task_id),
                    col(BackgroundTask.org_id) == self.org_id,
                    col(BackgroundTask.workspace_id) == self.workspace_id,
                    col(BackgroundTask.conversation_id) == conversation_id,
                ),
            )
            .where(
                col(BackgroundTaskEvent.org_id) == self.org_id,
                col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                col(BackgroundTaskEvent.conversation_id) == conversation_id,
            )
        )
        if delivery == "pending":
            query = query.where(col(BackgroundTaskEvent.state).in_(PENDING_EVENT_STATES))
        if after is not None:
            query = query.where(
                or_(
                    col(BackgroundTaskEvent.created_at) < after.created_at,
                    and_(
                        col(BackgroundTaskEvent.created_at) == after.created_at,
                        col(BackgroundTaskEvent.id) < after.event_id,
                    ),
                )
            )
        rows = (
            await self.session.execute(
                query.order_by(
                    col(BackgroundTaskEvent.created_at).desc(),
                    col(BackgroundTaskEvent.id).desc(),
                ).limit(limit + 1)
            )
        ).all()
        has_more = len(rows) > limit
        selected = rows[:limit]
        items = tuple(TaskEventProjection(row[0], str(row[1])) for row in selected)
        next_cursor = None
        if has_more and items:
            last = items[-1].event
            next_cursor = self._encode_cursor(
                conversation_id=conversation_id,
                delivery=delivery,
                created_at=last.created_at,
                event_id=last.id,
            )
        return TaskEventPage(items, next_cursor, has_more)

    async def summary(self, *, conversation_id: str) -> BackgroundTaskSummary:
        task_scope = (
            col(BackgroundTask.org_id) == self.org_id,
            col(BackgroundTask.workspace_id) == self.workspace_id,
            col(BackgroundTask.conversation_id) == conversation_id,
        )
        event_scope = (
            col(BackgroundTaskEvent.org_id) == self.org_id,
            col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
            col(BackgroundTaskEvent.conversation_id) == conversation_id,
        )
        inflight = exists(
            select(col(BackgroundTask.id)).where(
                *task_scope, col(BackgroundTask.state).in_(INFLIGHT_TASK_STATES)
            )
        )
        pending = exists(
            select(col(BackgroundTaskEvent.id)).where(
                *event_scope, col(BackgroundTaskEvent.state).in_(PENDING_EVENT_STATES)
            )
        )
        log_cleanup = exists(
            select(col(SandboxCommand.id))
            .join(BackgroundTask, col(BackgroundTask.id) == col(SandboxCommand.task_id))
            .where(
                *task_scope,
                col(SandboxCommand.org_id) == self.org_id,
                col(SandboxCommand.workspace_id) == self.workspace_id,
                col(SandboxCommand.log_state).in_(("pending", "retrying")),
            )
        )
        stop_cleanup = exists(
            select(col(BackgroundTask.id)).where(
                *task_scope,
                col(BackgroundTask.stop_requested_at).is_not(None),
                col(BackgroundTask.state).in_(INFLIGHT_TASK_STATES),
            )
        )
        claimed_cancelled = exists(
            select(col(BackgroundTaskEvent.id))
            .join(BackgroundTask, col(BackgroundTask.id) == col(BackgroundTaskEvent.task_id))
            .where(
                *task_scope,
                *event_scope,
                col(BackgroundTask.notifications_cancelled_at).is_not(None),
                col(BackgroundTaskEvent.state) == BackgroundTaskEventState.claimed.value,
            )
        )
        stoppable = exists(
            select(col(BackgroundTask.id)).where(
                *task_scope,
                col(BackgroundTask.stop_requested_at).is_(None),
                or_(
                    col(BackgroundTask.state).in_(INFLIGHT_TASK_STATES),
                    exists(
                        select(col(BackgroundTaskEvent.id)).where(
                            *event_scope,
                            col(BackgroundTaskEvent.task_id) == col(BackgroundTask.id),
                            col(BackgroundTaskEvent.state).in_(PENDING_EVENT_STATES),
                        )
                    ),
                ),
            )
        )
        row = (
            await self.session.execute(
                select(
                    inflight.label("has_inflight"),
                    pending.label("has_pending"),
                    or_(log_cleanup, stop_cleanup, claimed_cancelled).label("has_cleanup"),
                    stoppable.label("can_stop"),
                )
            )
        ).one()
        return BackgroundTaskSummary(*(bool(value) for value in row))

    def _cursor_key(self) -> bytes:
        return str(config.get("auth.jwt_secret", "CHANGE_ME")).encode()

    def _encode_cursor(
        self,
        *,
        conversation_id: str,
        delivery: EventDelivery,
        created_at: datetime,
        event_id: str,
    ) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "org": self.org_id,
                "workspace": self.workspace_id,
                "conversation": conversation_id,
                "delivery": delivery,
                "created_at": created_at.isoformat(),
                "event_id": event_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(self._cursor_key(), payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).rstrip(b"=").decode("ascii")

    def _decode_cursor(
        self, token: str, *, conversation_id: str, delivery: EventDelivery
    ) -> _EventCursor:
        try:
            padding = "=" * (-len(token) % 4)
            decoded = base64.b64decode(token + padding, altchars=b"-_", validate=True)
            payload, signature = decoded[:-32], decoded[-32:]
            if not payload or not hmac.compare_digest(
                signature, hmac.new(self._cursor_key(), payload, hashlib.sha256).digest()
            ):
                raise ValueError
            data = json.loads(payload)
            if (
                not isinstance(data, dict)
                or data.get("v") != 1
                or data.get("org") != self.org_id
                or data.get("workspace") != self.workspace_id
                or data.get("conversation") != conversation_id
                or data.get("delivery") != delivery
                or not isinstance(data.get("created_at"), str)
                or not isinstance(data.get("event_id"), str)
            ):
                raise ValueError
            created_at = datetime.fromisoformat(data["created_at"])
            if created_at.tzinfo is None or created_at.utcoffset() is None:
                raise ValueError
            return _EventCursor(created_at, data["event_id"])
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise InvalidBackgroundTaskCursorError("invalid background task event cursor") from exc
