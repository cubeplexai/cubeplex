"""Durable routing state for one-shot background task result notices."""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from cubeloop.checkpointer.exceptions import (
    RunAlreadyClaimedError,
    RunAlreadyCompletedError,
    RunNotClaimedError,
)
from redis.asyncio import Redis
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import col
from uuid_utils import uuid7

from cubeplex.agents.schemas import BackgroundTaskNotice
from cubeplex.models.background_task import (
    BackgroundTask,
    BackgroundTaskEvent,
    BackgroundTaskEventState,
    TaskResultReadiness,
)
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.steering_message import SteeringMessage
from cubeplex.repositories.steering_message import (
    SteeringMessageQueueFullError,
    SteeringMessageRepository,
)
from cubeplex.services.background_task_lifecycle import require_aware
from cubeplex.services.conversation_execution import (
    ConversationExecutionService,
    ExecutionConflictError,
    ExecutionRevokedError,
)


@dataclass(frozen=True, slots=True)
class ClaimedBackgroundTaskNotice:
    notice: BackgroundTaskNotice
    input_id: str


class BackgroundNoticeRunManager(Protocol):
    async def drain_durable_steering(self, run_id: str) -> None: ...

    async def notify_durable_cancel(self, run_id: str, steer_id: str) -> None: ...

    async def start_background_notice(
        self,
        *,
        notice_id: str,
        owner_token: str,
        org_id: str,
        workspace_id: str,
    ) -> bool: ...


def render_background_task_notice(notice: BackgroundTaskNotice) -> str:
    return "Background task result:\n" + json.dumps(
        notice.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


class BackgroundTaskDeliveryService:
    """Own notice claim, attempt binding, and checkpoint acknowledgement."""

    def __init__(self, session: AsyncSession, *, org_id: str, workspace_id: str) -> None:
        self.session = session
        self.org_id = org_id
        self.workspace_id = workspace_id

    async def claim_ready(
        self,
        *,
        owner_token: str,
        now: datetime,
        owner_until: datetime,
        limit: int = 20,
    ) -> list[ClaimedBackgroundTaskNotice]:
        require_aware(now)
        require_aware(owner_until)
        candidate_ids = list(
            await self.session.scalars(
                select(col(BackgroundTaskEvent.id))
                .join(
                    BackgroundTask,
                    col(BackgroundTask.id) == col(BackgroundTaskEvent.task_id),
                )
                .where(
                    col(BackgroundTaskEvent.org_id) == self.org_id,
                    col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                    col(BackgroundTask.result_readiness) != TaskResultReadiness.pending.value,
                    or_(
                        col(BackgroundTaskEvent.state) == BackgroundTaskEventState.pending.value,
                        (
                            (
                                col(BackgroundTaskEvent.state)
                                == BackgroundTaskEventState.claimed.value
                            )
                            & col(BackgroundTaskEvent.delivery_attempt_id).is_(None)
                            & (
                                col(BackgroundTaskEvent.owner_until).is_(None)
                                | (col(BackgroundTaskEvent.owner_until) <= now)
                            )
                        ),
                    ),
                )
                .order_by(
                    col(BackgroundTaskEvent.created_at),
                    col(BackgroundTaskEvent.id),
                )
                .limit(limit)
            )
        )
        claimed: list[ClaimedBackgroundTaskNotice] = []
        for notice_id in candidate_ids:
            claim = await self._claim_one(
                notice_id,
                owner_token=owner_token,
                now=now,
                owner_until=owner_until,
            )
            if claim is not None:
                claimed.append(claim)
        await self.session.flush()
        return claimed

    async def _claim_one(
        self,
        notice_id: str,
        *,
        owner_token: str,
        now: datetime,
        owner_until: datetime,
    ) -> ClaimedBackgroundTaskNotice | None:
        header = (
            await self.session.execute(
                select(
                    col(BackgroundTaskEvent.conversation_id),
                    col(BackgroundTaskEvent.execution_generation),
                    col(BackgroundTask.started_by_user_id),
                    col(BackgroundTaskEvent.task_id),
                )
                .join(
                    BackgroundTask,
                    col(BackgroundTask.id) == col(BackgroundTaskEvent.task_id),
                )
                .where(
                    col(BackgroundTaskEvent.id) == notice_id,
                    col(BackgroundTaskEvent.org_id) == self.org_id,
                    col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                )
            )
        ).one_or_none()
        if header is None:
            return None
        discard_reason: str | None = None
        try:
            await ConversationExecutionService(
                self.session, org_id=self.org_id, workspace_id=self.workspace_id
            ).require_background_notice_authority(
                conversation_id=header.conversation_id,
                actor_user_id=header.started_by_user_id,
                execution_generation=header.execution_generation,
            )
        except ExecutionRevokedError:
            discard_reason = "generation_closed"
        except LookupError:
            discard_reason = "access_revoked"

        task = await self.session.scalar(
            select(BackgroundTask)
            .where(
                col(BackgroundTask.id) == header.task_id,
                col(BackgroundTask.org_id) == self.org_id,
                col(BackgroundTask.workspace_id) == self.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        event = await self.session.scalar(
            select(BackgroundTaskEvent)
            .where(
                col(BackgroundTaskEvent.id) == notice_id,
                col(BackgroundTaskEvent.org_id) == self.org_id,
                col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None or event is None:
            return None
        claimable = event.state == BackgroundTaskEventState.pending.value or (
            event.state == BackgroundTaskEventState.claimed.value
            and event.delivery_attempt_id is None
            and (event.owner_until is None or event.owner_until <= now)
        )
        if not claimable:
            return None
        if task.notifications_cancelled_at is not None:
            discard_reason = "notifications_cancelled"
        elif not task.notify_on_complete:
            discard_reason = "notifications_disabled"
        elif task.execution_generation != event.execution_generation:
            discard_reason = "source_mismatch"
        if discard_reason is not None:
            event.state = BackgroundTaskEventState.discarded.value
            event.discard_reason = discard_reason
            event.owner_token = None
            event.owner_until = None
            event.revision += 1
            return None
        if task.result_readiness == TaskResultReadiness.pending.value:
            return None
        event.state = BackgroundTaskEventState.claimed.value
        event.owner_token = owner_token
        event.owner_until = owner_until
        event.delivery_input_id = event.delivery_input_id or event.id
        event.summary = task.result_summary
        event.result_ref = task.result_ref
        event.revision += 1
        return ClaimedBackgroundTaskNotice(
            notice=BackgroundTaskNotice(
                notice_id=event.id,
                task_id=task.id,
                task_kind=task.kind,
                originating_run_id=task.originating_run_id,
                execution_generation=event.execution_generation,
                reason=event.reason,
                summary=event.summary,
                result_ref=event.result_ref,
            ),
            input_id=event.delivery_input_id,
        )

    async def bind_attempt(
        self,
        *,
        notice_id: str,
        owner_token: str,
        run_id: str,
        attempt_id: str,
    ) -> bool:
        event = await self.session.get(
            BackgroundTaskEvent, notice_id, with_for_update=True, populate_existing=True
        )
        if (
            event is None
            or event.org_id != self.org_id
            or event.workspace_id != self.workspace_id
            or event.state != BackgroundTaskEventState.claimed.value
            or event.owner_token != owner_token
        ):
            return False
        if event.delivery_run_id is not None or event.delivery_attempt_id is not None:
            return event.delivery_run_id == run_id and event.delivery_attempt_id == attempt_id
        event.delivery_run_id = run_id
        event.delivery_attempt_id = attempt_id
        event.revision += 1
        await self.session.flush()
        return True

    async def bind_initial_attempt(
        self,
        *,
        notice_id: str,
        owner_token: str,
        run_id: str,
        attempt_id: str,
    ) -> bool:
        task = await self.session.scalar(
            select(BackgroundTask)
            .join(
                BackgroundTaskEvent,
                col(BackgroundTaskEvent.task_id) == col(BackgroundTask.id),
            )
            .where(col(BackgroundTaskEvent.id) == notice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        event = await self.session.get(
            BackgroundTaskEvent, notice_id, with_for_update=True, populate_existing=True
        )
        if (
            task is None
            or event is None
            or event.org_id != self.org_id
            or event.workspace_id != self.workspace_id
            or event.state != BackgroundTaskEventState.claimed.value
            or event.owner_token != owner_token
            or event.delivery_attempt_id is not None
            or task.notifications_cancelled_at is not None
            or not task.notify_on_complete
            or task.result_readiness == TaskResultReadiness.pending.value
        ):
            return False
        event.delivery_run_id = run_id
        event.delivery_attempt_id = attempt_id
        event.delivery_input_id = event.delivery_input_id or event.id
        event.revision += 1
        await self.session.flush()
        return True

    async def release_unbound(self, *, notice_id: str, owner_token: str) -> bool:
        event = await self.session.get(
            BackgroundTaskEvent, notice_id, with_for_update=True, populate_existing=True
        )
        if (
            event is None
            or event.org_id != self.org_id
            or event.workspace_id != self.workspace_id
            or event.state != BackgroundTaskEventState.claimed.value
            or event.owner_token != owner_token
            or event.delivery_attempt_id is not None
        ):
            return False
        event.state = BackgroundTaskEventState.pending.value
        event.owner_token = None
        event.owner_until = None
        event.revision += 1
        await self.session.flush()
        return True

    async def discard_stopped_unbound_initial(
        self,
        *,
        notice_id: str,
        owner_token: str,
        now: datetime,
    ) -> bool:
        """Discard an unbound notice whose exact idle-run admission was stopped."""
        require_aware(now)
        task_id = await self.session.scalar(
            select(col(BackgroundTaskEvent.task_id)).where(
                col(BackgroundTaskEvent.id) == notice_id,
                col(BackgroundTaskEvent.org_id) == self.org_id,
                col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
            )
        )
        if task_id is None:
            return False
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.source_kind) == "background_task",
                col(ConversationExecutionAdmission.source_id) == notice_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if admission is None or (
            admission.run_stop_requested_at is None and admission.revoked_at is None
        ):
            return False
        task = await self.session.get(
            BackgroundTask, task_id, with_for_update=True, populate_existing=True
        )
        event = await self.session.get(
            BackgroundTaskEvent, notice_id, with_for_update=True, populate_existing=True
        )
        if (
            task is None
            or event is None
            or event.task_id != task.id
            or event.org_id != self.org_id
            or event.workspace_id != self.workspace_id
            or event.state != BackgroundTaskEventState.claimed.value
            or event.owner_token != owner_token
            or event.delivery_attempt_id is not None
        ):
            return False
        event.state = BackgroundTaskEventState.discarded.value
        event.discard_reason = task.stop_reason or "run_stop"
        event.owner_token = None
        event.owner_until = None
        event.revision += 1
        self._finish_fenced_admission(admission, now=now)
        await self.session.flush()
        return True

    async def enqueue_for_active_run(
        self,
        *,
        notice_id: str,
        owner_token: str,
        run_id: str,
    ) -> str | None:
        """Bind a claimed notice to a live run owned by the same actor."""
        header = (
            await self.session.execute(
                select(
                    col(BackgroundTaskEvent.conversation_id),
                    col(BackgroundTaskEvent.execution_generation),
                    col(BackgroundTask.started_by_user_id),
                )
                .join(
                    BackgroundTask,
                    col(BackgroundTask.id) == col(BackgroundTaskEvent.task_id),
                )
                .where(
                    col(BackgroundTaskEvent.id) == notice_id,
                    col(BackgroundTaskEvent.org_id) == self.org_id,
                    col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                )
            )
        ).one_or_none()
        if header is None:
            return None
        admission = await ConversationExecutionService(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        ).require_run_input_actor(
            conversation_id=header.conversation_id,
            run_id=run_id,
            actor_user_id=header.started_by_user_id,
        )
        if admission is None or admission.run_start_token is None:
            return None
        previous_initial = await self.session.scalar(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.source_kind) == "background_task",
                col(ConversationExecutionAdmission.source_id) == notice_id,
            )
            .with_for_update()
        )
        if previous_initial is not None:
            if previous_initial.run_finished_at is None and (
                previous_initial.run_start_token is not None
                or previous_initial.run_started_at is not None
            ):
                return None
            if previous_initial.run_finished_at is None:
                await self.session.delete(previous_initial)
                await self.session.flush()
        task = await self.session.scalar(
            select(BackgroundTask)
            .join(
                BackgroundTaskEvent,
                col(BackgroundTaskEvent.task_id) == col(BackgroundTask.id),
            )
            .where(col(BackgroundTaskEvent.id) == notice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        event = await self.session.get(
            BackgroundTaskEvent, notice_id, with_for_update=True, populate_existing=True
        )
        if (
            task is None
            or event is None
            or event.state != BackgroundTaskEventState.claimed.value
            or event.owner_token != owner_token
            or task.notifications_cancelled_at is not None
            or task.result_readiness == TaskResultReadiness.pending.value
        ):
            return None
        notice = BackgroundTaskNotice(
            notice_id=event.id,
            task_id=task.id,
            task_kind=task.kind,
            originating_run_id=task.originating_run_id,
            execution_generation=event.execution_generation,
            reason=event.reason,
            summary=event.summary,
            result_ref=event.result_ref,
        )
        content = render_background_task_notice(notice)
        input_id = event.delivery_input_id or event.id
        row, _ = await SteeringMessageRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        ).enqueue(
            conversation_id=event.conversation_id,
            run_id=run_id,
            client_steer_id=input_id,
            content=content,
            sender_user_id=task.started_by_user_id,
            sender_display_name=None,
            hitl_question_id=None,
            source_kind="background_task",
            execution_generation=event.execution_generation,
            notice_id=event.id,
            lock_conversation=False,
        )
        event.delivery_run_id = run_id
        event.delivery_attempt_id = admission.run_start_token
        event.delivery_input_id = input_id
        event.revision += 1
        await self.session.flush()
        return row.id

    async def acknowledge_checkpoint(
        self,
        *,
        notice_id: str,
        run_id: str,
        attempt_id: str,
        input_id: str,
        now: datetime,
    ) -> bool:
        require_aware(now)
        event = await self.session.get(
            BackgroundTaskEvent, notice_id, with_for_update=True, populate_existing=True
        )
        if (
            event is None
            or event.org_id != self.org_id
            or event.workspace_id != self.workspace_id
            or event.state != BackgroundTaskEventState.claimed.value
            or event.delivery_run_id != run_id
            or event.delivery_attempt_id != attempt_id
            or event.delivery_input_id != input_id
        ):
            return False
        event.state = BackgroundTaskEventState.delivered.value
        event.checkpoint_run_id = run_id
        event.checkpoint_input_id = input_id
        event.delivered_at = now
        event.owner_token = None
        event.owner_until = None
        event.revision += 1
        await self.session.flush()
        return True

    async def acknowledge_history(
        self,
        *,
        notice_id: str,
        run_id: str,
        input_id: str,
        now: datetime,
        retire_fenced_run_at: datetime | None = None,
    ) -> bool:
        """Accept a persisted message as proof after a worker restart."""
        require_aware(now)
        if retire_fenced_run_at is not None:
            require_aware(retire_fenced_run_at)
        admission = None
        if retire_fenced_run_at is not None:
            admission = await self.session.scalar(
                select(ConversationExecutionAdmission)
                .where(
                    col(ConversationExecutionAdmission.org_id) == self.org_id,
                    col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                    col(ConversationExecutionAdmission.run_id) == run_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        event = await self.session.get(
            BackgroundTaskEvent, notice_id, with_for_update=True, populate_existing=True
        )
        if (
            event is None
            or event.org_id != self.org_id
            or event.workspace_id != self.workspace_id
            or event.delivery_run_id != run_id
            or event.delivery_attempt_id is None
            or event.delivery_input_id != input_id
            or (
                retire_fenced_run_at is not None
                and (admission is None or admission.run_start_token != event.delivery_attempt_id)
            )
        ):
            return False
        if event.state == BackgroundTaskEventState.delivered.value:
            return event.checkpoint_run_id == run_id and event.checkpoint_input_id == input_id
        if event.state != BackgroundTaskEventState.claimed.value:
            return False
        event.state = BackgroundTaskEventState.delivered.value
        event.checkpoint_run_id = run_id
        event.checkpoint_input_id = input_id
        event.delivered_at = now
        event.owner_token = None
        event.owner_until = None
        event.revision += 1
        if retire_fenced_run_at is not None and admission is not None:
            self._finish_fenced_admission(admission, now=retire_fenced_run_at)
        await self.session.flush()
        return True

    async def settle_uncommitted_attempt(
        self,
        *,
        notice_id: str,
        run_id: str,
        input_id: str,
        discard_cancelled_initial: bool,
        retire_fenced_run_at: datetime | None = None,
    ) -> bool:
        """Release a lost append, or discard a cancelled initial notice."""
        if retire_fenced_run_at is not None:
            require_aware(retire_fenced_run_at)
        task_id = await self.session.scalar(
            select(col(BackgroundTaskEvent.task_id)).where(
                col(BackgroundTaskEvent.id) == notice_id,
                col(BackgroundTaskEvent.org_id) == self.org_id,
                col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
            )
        )
        if task_id is None:
            return False
        # Stop locks admissions before tasks and events. Reconciliation can
        # retire an orphaned initial admission, so it must use the same order.
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.run_id) == run_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        task = await self.session.get(
            BackgroundTask, task_id, with_for_update=True, populate_existing=True
        )
        event = await self.session.get(
            BackgroundTaskEvent, notice_id, with_for_update=True, populate_existing=True
        )
        if (
            event is None
            or event.org_id != self.org_id
            or event.workspace_id != self.workspace_id
            or event.state != BackgroundTaskEventState.claimed.value
            or event.delivery_run_id != run_id
            or event.delivery_input_id != input_id
            or event.checkpoint_input_id is not None
        ):
            return False
        if task is None or event.task_id != task.id:
            return False
        if retire_fenced_run_at is not None and admission is None:
            return False
        is_initial = bool(
            admission is not None
            and admission.source_kind == "background_task"
            and admission.source_id == notice_id
        )
        event.owner_token = None
        event.owner_until = None
        initial_was_stopped = bool(
            is_initial and admission is not None and admission.run_stop_requested_at is not None
        )
        notifications_were_cancelled = task.notifications_cancelled_at is not None
        if notifications_were_cancelled or (
            is_initial and (discard_cancelled_initial or initial_was_stopped)
        ):
            event.state = BackgroundTaskEventState.discarded.value
            event.discard_reason = task.stop_reason or (
                "notifications_cancelled" if notifications_were_cancelled else "run_stop"
            )
        else:
            event.state = BackgroundTaskEventState.pending.value
            event.delivery_run_id = None
            event.delivery_attempt_id = None
            event.discard_reason = None
        if (
            retire_fenced_run_at is not None
            and admission is not None
            and admission.run_finished_at is None
        ):
            self._finish_fenced_admission(admission, now=retire_fenced_run_at)
        event.revision += 1
        await self.session.flush()
        return True

    @staticmethod
    def _finish_fenced_admission(
        admission: ConversationExecutionAdmission,
        *,
        now: datetime,
    ) -> None:
        if admission.run_finished_at is not None:
            return
        cancelled = admission.run_stop_requested_at is not None or admission.revoked_at is not None
        admission.run_terminal_status = admission.run_terminal_status or (
            "cancelled" if cancelled else "failed"
        )
        admission.run_terminal_at = admission.run_terminal_at or now
        admission.run_finished_at = now
        admission.updated_at = now


class BackgroundTaskDeliveryCoordinator:
    """Route ready notices into compatible live runs; idle routing is separate."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        run_manager: BackgroundNoticeRunManager,
        redis: Redis,
        redis_key_prefix: str,
    ) -> None:
        self.session_factory = session_factory
        self.run_manager = run_manager
        self.redis = redis
        self.redis_key_prefix = redis_key_prefix
        self.owner_token = f"background-delivery-{uuid7()}"

    async def deliver_once(self, *, now: datetime, lease_until: datetime) -> list[str]:
        from cubeplex.streams.run_events import get_active_run

        await self.cancel_revoked_appends_once()
        await self.reconcile_bound_once(now=now)
        async with self.session_factory() as session:
            scope_rows = await session.execute(
                select(
                    col(BackgroundTaskEvent.org_id),
                    col(BackgroundTaskEvent.workspace_id),
                )
                .join(
                    BackgroundTask,
                    col(BackgroundTask.id) == col(BackgroundTaskEvent.task_id),
                )
                .where(
                    col(BackgroundTask.result_readiness) != TaskResultReadiness.pending.value,
                    or_(
                        col(BackgroundTaskEvent.state) == BackgroundTaskEventState.pending.value,
                        (
                            (
                                col(BackgroundTaskEvent.state)
                                == BackgroundTaskEventState.claimed.value
                            )
                            & col(BackgroundTaskEvent.delivery_attempt_id).is_(None)
                            & (
                                col(BackgroundTaskEvent.owner_until).is_(None)
                                | (col(BackgroundTaskEvent.owner_until) <= now)
                            )
                        ),
                    ),
                )
                .distinct()
            )
            scopes = [(row[0], row[1]) for row in scope_rows.all()]
        routed: list[str] = []
        for org_id, workspace_id in scopes:
            async with self.session_factory() as session:
                claims = await BackgroundTaskDeliveryService(
                    session, org_id=org_id, workspace_id=workspace_id
                ).claim_ready(
                    owner_token=self.owner_token,
                    now=now,
                    owner_until=lease_until,
                )
                await session.commit()
            for claim in claims:
                active = await get_active_run(
                    self.redis,
                    prefix=self.redis_key_prefix,
                    conversation_id=await self._conversation_id(
                        claim.notice.notice_id,
                        org_id=org_id,
                        workspace_id=workspace_id,
                    ),
                )
                if active is None:
                    revoked = False
                    try:
                        started = await self.run_manager.start_background_notice(
                            notice_id=claim.notice.notice_id,
                            owner_token=self.owner_token,
                            org_id=org_id,
                            workspace_id=workspace_id,
                        )
                    except ExecutionRevokedError:
                        revoked = True
                        started = False
                    except (LookupError, RuntimeError):
                        started = False
                    if started:
                        routed.append(claim.notice.notice_id)
                    else:
                        settled = False
                        if revoked:
                            async with self.session_factory() as session:
                                settled = await BackgroundTaskDeliveryService(
                                    session,
                                    org_id=org_id,
                                    workspace_id=workspace_id,
                                ).discard_stopped_unbound_initial(
                                    notice_id=claim.notice.notice_id,
                                    owner_token=self.owner_token,
                                    now=now,
                                )
                                await session.commit()
                        if not settled:
                            await self._release(
                                claim.notice.notice_id,
                                org_id=org_id,
                                workspace_id=workspace_id,
                            )
                    continue
                if active.status != "running":
                    await self._release(
                        claim.notice.notice_id,
                        org_id=org_id,
                        workspace_id=workspace_id,
                    )
                    continue
                async with self.session_factory() as session:
                    service = BackgroundTaskDeliveryService(
                        session, org_id=org_id, workspace_id=workspace_id
                    )
                    try:
                        steering_id = await service.enqueue_for_active_run(
                            notice_id=claim.notice.notice_id,
                            owner_token=self.owner_token,
                            run_id=active.run_id,
                        )
                    except (
                        ExecutionConflictError,
                        ExecutionRevokedError,
                        LookupError,
                        SteeringMessageQueueFullError,
                    ):
                        steering_id = None
                    if steering_id is None:
                        await service.release_unbound(
                            notice_id=claim.notice.notice_id,
                            owner_token=self.owner_token,
                        )
                    await session.commit()
                if steering_id is None:
                    continue
                routed.append(claim.notice.notice_id)
                await self.run_manager.drain_durable_steering(active.run_id)
        return routed

    async def cancel_revoked_appends_once(self) -> list[str]:
        """Withdraw task results whose source notification right was revoked."""
        from cubeplex.models import SteeringMessage, SteeringMessageState

        async with self.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(BackgroundTaskEvent, SteeringMessage)
                        .join(
                            BackgroundTask,
                            col(BackgroundTask.id) == col(BackgroundTaskEvent.task_id),
                        )
                        .join(
                            SteeringMessage,
                            col(SteeringMessage.notice_id) == col(BackgroundTaskEvent.id),
                        )
                        .where(
                            col(BackgroundTaskEvent.state)
                            == BackgroundTaskEventState.claimed.value,
                            col(BackgroundTask.notifications_cancelled_at).is_not(None),
                            col(SteeringMessage.source_kind) == "background_task",
                            col(SteeringMessage.state).in_(
                                (
                                    SteeringMessageState.queued,
                                    SteeringMessageState.dispatched,
                                    SteeringMessageState.cancel_requested,
                                    SteeringMessageState.cancelled,
                                )
                            ),
                        )
                        .order_by(col(BackgroundTaskEvent.id))
                        .limit(100)
                    )
                ).all()
            )
        cancelled: list[str] = []
        for event, steering in rows:
            current_state: SteeringMessageState | None = None
            current_run_id: str | None = None
            current_input_id: str | None = None
            async with self.session_factory() as session:
                from cubeplex.repositories.steering_message import (
                    SteeringMessageRepository,
                )

                repo = SteeringMessageRepository(
                    session,
                    org_id=event.org_id,
                    workspace_id=event.workspace_id,
                )
                current = await repo.request_cancel(
                    conversation_id=event.conversation_id,
                    client_steer_id=steering.client_steer_id,
                )
                if current is None:
                    continue
                current_state = current.state
                current_run_id = current.run_id
                current_input_id = current.client_steer_id
                # Release the steering-row lock before settlement takes the
                # canonical admission -> task/event locks.
                await session.commit()
            if (
                current_state == SteeringMessageState.cancelled
                and current_run_id is not None
                and current_input_id is not None
            ):
                async with self.session_factory() as session:
                    released = await BackgroundTaskDeliveryService(
                        session,
                        org_id=event.org_id,
                        workspace_id=event.workspace_id,
                    ).settle_uncommitted_attempt(
                        notice_id=event.id,
                        run_id=current_run_id,
                        input_id=current_input_id,
                        discard_cancelled_initial=False,
                    )
                    if released:
                        repo = SteeringMessageRepository(
                            session,
                            org_id=event.org_id,
                            workspace_id=event.workspace_id,
                        )
                        cancelled_row = await repo.get_by_client_id(
                            conversation_id=event.conversation_id,
                            client_steer_id=current_input_id,
                            for_update=True,
                        )
                        if (
                            cancelled_row is not None
                            and cancelled_row.state == SteeringMessageState.cancelled
                            and cancelled_row.notice_id == event.id
                        ):
                            await session.delete(cancelled_row)
                        cancelled.append(event.id)
                    await session.commit()
            if (
                current_state == SteeringMessageState.cancel_requested
                and current_run_id is not None
                and current_input_id is not None
            ):
                await self.run_manager.notify_durable_cancel(
                    current_run_id,
                    current_input_id,
                )
        return cancelled

    async def reconcile_bound_once(self, *, now: datetime) -> list[str]:
        """Repair initial notices after a worker exits before acknowledging."""
        from cubeplex.agents.checkpointer import shared_checkpointer
        from cubeplex.streams.run_events import get_run_meta

        async with self.session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(BackgroundTaskEvent)
                        .join(
                            ConversationExecutionAdmission,
                            and_(
                                col(ConversationExecutionAdmission.run_id)
                                == col(BackgroundTaskEvent.delivery_run_id),
                                col(ConversationExecutionAdmission.org_id)
                                == col(BackgroundTaskEvent.org_id),
                                col(ConversationExecutionAdmission.workspace_id)
                                == col(BackgroundTaskEvent.workspace_id),
                            ),
                        )
                        .where(
                            col(BackgroundTaskEvent.state)
                            == BackgroundTaskEventState.claimed.value,
                            col(BackgroundTaskEvent.delivery_attempt_id).is_not(None),
                        )
                        .distinct()
                        .order_by(col(BackgroundTaskEvent.id))
                        .limit(100)
                    )
                )
                .scalars()
                .all()
            )
        reconciled: list[str] = []
        async with shared_checkpointer() as checkpointer:
            for event in rows:
                if event.delivery_run_id is None or event.delivery_input_id is None:
                    continue
                checkpoint = await checkpointer.load(event.conversation_id)
                in_history = bool(
                    checkpoint is not None
                    and any(
                        getattr(message, "metadata", {}).get("notice_id") == event.id
                        for message in checkpoint.messages
                    )
                )
                meta = await get_run_meta(
                    self.redis,
                    prefix=self.redis_key_prefix,
                    run_id=event.delivery_run_id,
                )
                pending_run_id = await checkpointer.load_pending_run_id(event.conversation_id)
                run_is_live = bool(
                    (meta is not None and meta.status in ("running", "paused_hitl"))
                    or pending_run_id == event.delivery_run_id
                )
                if not in_history and run_is_live:
                    continue
                fenced = False
                if not run_is_live:
                    # Completion takes CubeLoop's per-conversation advisory
                    # lock. It waits for an in-flight append, then permanently
                    # rejects any later append from this fenced old run.
                    try:
                        await checkpointer.mark_run_complete(
                            event.conversation_id,
                            event.delivery_run_id,
                        )
                    except RunNotClaimedError:
                        try:
                            await checkpointer.claim_run(
                                event.conversation_id,
                                event.delivery_run_id,
                            )
                        except (RunAlreadyClaimedError, RunAlreadyCompletedError):
                            pass
                        await checkpointer.mark_run_complete(
                            event.conversation_id,
                            event.delivery_run_id,
                        )
                    fenced = True
                    checkpoint = await checkpointer.load(event.conversation_id)
                    in_history = bool(
                        checkpoint is not None
                        and any(
                            getattr(message, "metadata", {}).get("notice_id") == event.id
                            for message in checkpoint.messages
                        )
                    )
                async with self.session_factory() as session:
                    service = BackgroundTaskDeliveryService(
                        session,
                        org_id=event.org_id,
                        workspace_id=event.workspace_id,
                    )
                    if in_history:
                        changed = await service.acknowledge_history(
                            notice_id=event.id,
                            run_id=event.delivery_run_id,
                            input_id=event.delivery_input_id,
                            now=now,
                            retire_fenced_run_at=now if fenced else None,
                        )
                    else:
                        changed = await service.settle_uncommitted_attempt(
                            notice_id=event.id,
                            run_id=event.delivery_run_id,
                            input_id=event.delivery_input_id,
                            discard_cancelled_initial=(
                                meta is not None and meta.status == "cancelled"
                            ),
                            retire_fenced_run_at=now,
                        )
                    if changed:
                        await session.execute(
                            delete(SteeringMessage).where(
                                col(SteeringMessage.org_id) == event.org_id,
                                col(SteeringMessage.workspace_id) == event.workspace_id,
                                col(SteeringMessage.notice_id) == event.id,
                                col(SteeringMessage.source_kind) == "background_task",
                            )
                        )
                    await session.commit()
                if changed:
                    reconciled.append(event.id)
        return reconciled

    async def _conversation_id(self, notice_id: str, *, org_id: str, workspace_id: str) -> str:
        async with self.session_factory() as session:
            conversation_id = await session.scalar(
                select(col(BackgroundTaskEvent.conversation_id)).where(
                    col(BackgroundTaskEvent.id) == notice_id,
                    col(BackgroundTaskEvent.org_id) == org_id,
                    col(BackgroundTaskEvent.workspace_id) == workspace_id,
                )
            )
        if conversation_id is None:
            raise LookupError("background notice not found")
        return conversation_id

    async def _release(self, notice_id: str, *, org_id: str, workspace_id: str) -> None:
        async with self.session_factory() as session:
            await BackgroundTaskDeliveryService(
                session, org_id=org_id, workspace_id=workspace_id
            ).release_unbound(notice_id=notice_id, owner_token=self.owner_token)
            await session.commit()
