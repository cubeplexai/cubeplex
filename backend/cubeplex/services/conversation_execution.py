"""Durable admission and generation closure; callers own the transaction."""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import uuid4

from cubeloop.providers.base import ReasoningControl
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col

from cubeplex.llm.resolver import parse_model_ref, resolve_model_preset
from cubeplex.llm.snapshot import LLMSnapshot, ModelPreset
from cubeplex.models.attachment import Attachment
from cubeplex.models.background_task import (
    INFLIGHT_TASK_STATES,
    BackgroundTask,
    BackgroundTaskEvent,
    TaskStopReason,
)
from cubeplex.models.conversation import Conversation
from cubeplex.models.conversation_execution import ConversationExecutionAdmission
from cubeplex.models.conversation_participant import ConversationParticipant
from cubeplex.models.membership import Membership
from cubeplex.models.steering_message import SteeringMessage, SteeringMessageState
from cubeplex.models.topic import Topic, TopicParticipant
from cubeplex.models.user import User
from cubeplex.models.workspace import Workspace
from cubeplex.repositories.background_task import ConversationExecutionAdmissionRepository
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.services.background_task_lifecycle import require_aware


class ExecutionConflictError(ValueError):
    """An immutable source identity was reused for different work."""


class ExecutionRevokedError(ValueError):
    """The original admission no longer authorizes execution."""


RunTerminalStatus = Literal["completed", "cancelled", "errored", "failed"]
RUN_TERMINAL_STATUSES = frozenset[RunTerminalStatus](
    ("completed", "cancelled", "errored", "failed")
)


class UserMessageIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    attachment_ids: tuple[str, ...] = ()
    model_key: str | None = None
    reasoning: ReasoningControl = Field(default_factory=ReasoningControl)


class ResolvedExecution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_key: str
    primary: str
    fallbacks: tuple[str, ...] = ()
    reasoning: ReasoningControl
    trigger: Literal["interactive", "im", "automated"] = "interactive"

    def model_preset(self) -> ModelPreset:
        return ModelPreset(
            key=self.model_key,
            primary=self.primary,
            fallbacks=self.fallbacks,
            kind="custom",
            is_default=False,
        )


class DirectExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["skill_install"]
    request_content: str
    content: str
    timestamp: datetime


@dataclass(frozen=True)
class RunExecutionBinding:
    admission_id: str
    attempt_id: str
    start_token: str
    execution_generation: int
    execution: ResolvedExecution


@dataclass(frozen=True)
class AdmittedExecution:
    admission: ConversationExecutionAdmission
    execution: ResolvedExecution
    created: bool


@dataclass(frozen=True)
class AdmittedDirectExecution:
    admission: ConversationExecutionAdmission
    result: DirectExecutionResult | None
    created: bool


@dataclass(frozen=True)
class ClosedExecution:
    execution_generation: int
    accepted: bool
    cleanup_pending: bool
    run_ids: tuple[str, ...]


@dataclass(frozen=True)
class StoppedRun:
    run_id: str
    accepted: bool
    cleanup_pending: bool
    admission: ConversationExecutionAdmission


class ConversationExecutionService:
    def __init__(self, session: AsyncSession, *, org_id: str, workspace_id: str) -> None:
        self.session = session
        self.org_id = org_id
        self.workspace_id = workspace_id

    async def resolve_user_run(
        self,
        *,
        admission_id: str,
        conversation_id: str,
        actor_user_id: str,
        run_id: str,
        intent: UserMessageIntent,
        snapshot: LLMSnapshot,
    ) -> AdmittedExecution:
        await self._lock_authorized_conversation(conversation_id, actor_user_id)
        admission = await ConversationExecutionAdmissionRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        ).get(admission_id)
        if (
            admission is None
            or admission.conversation_id != conversation_id
            or admission.actor_user_id != actor_user_id
            or admission.run_id != run_id
            or admission.source_kind
            not in ("user_message", "schedule_occurrence", "trigger_occurrence")
            or admission.execution_kind != "run"
            or admission.request_fingerprint != self._fingerprint(intent)
            or admission.resolved_execution is None
        ):
            raise ExecutionConflictError("run does not match its immutable admission")
        execution = ResolvedExecution.model_validate(admission.resolved_execution)
        if self._needs_run_start(admission):
            self._validate_models(execution, snapshot)
        return AdmittedExecution(admission, execution, False)

    @staticmethod
    def _needs_run_start(admission: ConversationExecutionAdmission) -> bool:
        return (
            admission.run_start_token is None
            and admission.run_finished_at is None
            and admission.revoked_at is None
            and admission.run_stop_requested_at is None
        )

    async def resolve_run_continuation(
        self, *, conversation_id: str, run_id: str, responding_user_id: str
    ) -> AdmittedExecution | None:
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.run_id) == run_id,
            )
        )
        # Pre-cutover runs have no admission; the migration gate removes that case.
        if admission is None:
            return None
        if admission.conversation_id != conversation_id:
            raise ExecutionConflictError("resume does not match the admitted conversation")
        if admission.actor_user_id != responding_user_id:
            raise ExecutionConflictError("only the original execution actor may answer")
        admission = await self._lock_live_admission(admission.id)
        if (
            admission.run_start_token is None
            or admission.run_started_at is None
            or admission.run_finished_at is not None
            or admission.resolved_execution is None
        ):
            raise ExecutionRevokedError("run has no unfinished execution to continue")
        return AdmittedExecution(
            admission, ResolvedExecution.model_validate(admission.resolved_execution), False
        )

    async def require_run_input_actor(
        self, *, conversation_id: str, run_id: str, actor_user_id: str
    ) -> None:
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission).where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.run_id) == run_id,
            )
        )
        if admission is None:
            return
        if admission.conversation_id != conversation_id or admission.actor_user_id != actor_user_id:
            raise ExecutionConflictError("only the original execution actor may add input")
        await self._lock_live_admission(admission.id)

    async def stop_run(
        self, *, conversation_id: str, run_id: str, actor_user_id: str, now: datetime
    ) -> StoppedRun:
        """Stop only the named run and work that it has not handed to the background."""
        require_aware(now)
        await self._lock_authorized_conversation(conversation_id, actor_user_id)
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                col(ConversationExecutionAdmission.conversation_id) == conversation_id,
                col(ConversationExecutionAdmission.run_id) == run_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if admission is None:
            raise LookupError("execution admission not found")
        admission.run_stop_requested_at = admission.run_stop_requested_at or now
        tasks = list(
            await self.session.scalars(
                select(BackgroundTask)
                .where(
                    col(BackgroundTask.org_id) == self.org_id,
                    col(BackgroundTask.workspace_id) == self.workspace_id,
                    col(BackgroundTask.conversation_id) == conversation_id,
                    col(BackgroundTask.originating_run_id) == run_id,
                    col(BackgroundTask.backgrounded_at).is_(None),
                )
                .order_by(col(BackgroundTask.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        for task in tasks:
            task.stop_requested_at = task.stop_requested_at or now
            task.notifications_cancelled_at = task.notifications_cancelled_at or now
            task.stop_reason = task.stop_reason or TaskStopReason.run_stop.value
            task.revision += 1
        inputs_pending = await self._cancel_user_inputs(conversation_id, run_id=run_id)
        await self.session.flush()
        return StoppedRun(
            run_id,
            True,
            admission.run_finished_at is None
            or any(task.state in INFLIGHT_TASK_STATES for task in tasks)
            or inputs_pending,
            admission,
        )

    async def _cancel_user_inputs(
        self,
        conversation_id: str,
        *,
        run_id: str | None = None,
        generation: int | None = None,
    ) -> bool:
        query = select(SteeringMessage).where(
            col(SteeringMessage.org_id) == self.org_id,
            col(SteeringMessage.workspace_id) == self.workspace_id,
            col(SteeringMessage.conversation_id) == conversation_id,
            col(SteeringMessage.source_kind) == "user_message",
            col(SteeringMessage.state).in_(
                (
                    SteeringMessageState.queued,
                    SteeringMessageState.failed,
                    SteeringMessageState.dispatched,
                    SteeringMessageState.cancel_requested,
                )
            ),
        )
        if run_id is not None:
            query = query.where(col(SteeringMessage.run_id) == run_id)
        elif generation is not None:
            query = query.where(col(SteeringMessage.execution_generation) == generation)
        else:
            raise ValueError("input cancellation requires a run or generation")
        rows = list(
            await self.session.scalars(
                query.order_by(col(SteeringMessage.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        for row in rows:
            if row.state in (SteeringMessageState.queued, SteeringMessageState.failed):
                row.state = SteeringMessageState.cancelled
                row.delivery_owner = None
                row.delivery_lease_until = None
            else:
                row.state = SteeringMessageState.cancel_requested
        return any(row.state == SteeringMessageState.cancel_requested for row in rows)

    @staticmethod
    def _fingerprint(intent: UserMessageIntent) -> str:
        return sha256(
            json.dumps(
                intent.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()

    async def close_generation(
        self,
        *,
        conversation_id: str,
        actor_user_id: str,
        execution_generation: int,
        now: datetime,
    ) -> ClosedExecution:
        require_aware(now)
        if type(execution_generation) is not int or execution_generation < 0:
            raise ValueError("Stop requires a nonnegative execution_generation")
        conversation = await self._lock_authorized_conversation(conversation_id, actor_user_id)
        if execution_generation > conversation.execution_generation:
            raise ExecutionConflictError("Stop cannot close a future execution generation")
        if execution_generation == conversation.execution_generation:
            conversation.execution_closed_at = conversation.execution_closed_at or now
        admissions = list(
            (
                await self.session.scalars(
                    select(ConversationExecutionAdmission)
                    .where(
                        col(ConversationExecutionAdmission.org_id) == self.org_id,
                        col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
                        col(ConversationExecutionAdmission.conversation_id) == conversation_id,
                        col(ConversationExecutionAdmission.execution_generation)
                        == execution_generation,
                    )
                    .order_by(col(ConversationExecutionAdmission.id))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        for admission in admissions:
            admission.revoked_at = admission.revoked_at or now
        tasks = list(
            (
                await self.session.scalars(
                    select(BackgroundTask)
                    .where(
                        col(BackgroundTask.org_id) == self.org_id,
                        col(BackgroundTask.workspace_id) == self.workspace_id,
                        col(BackgroundTask.conversation_id) == conversation_id,
                        col(BackgroundTask.execution_generation) == execution_generation,
                    )
                    .order_by(col(BackgroundTask.id))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        for task in tasks:
            task.stop_requested_at = task.stop_requested_at or now
            task.notifications_cancelled_at = task.notifications_cancelled_at or now
            task.stop_reason = TaskStopReason.conversation_stop.value
            task.revision += 1
        notices = list(
            (
                await self.session.scalars(
                    select(BackgroundTaskEvent)
                    .where(
                        col(BackgroundTaskEvent.org_id) == self.org_id,
                        col(BackgroundTaskEvent.workspace_id) == self.workspace_id,
                        col(BackgroundTaskEvent.conversation_id) == conversation_id,
                        col(BackgroundTaskEvent.execution_generation) == execution_generation,
                        col(BackgroundTaskEvent.state).in_(("pending", "claimed")),
                    )
                    .order_by(col(BackgroundTaskEvent.id))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        for notice in notices:
            # Attempted inputs need checkpoint reconciliation, not a guessed discard.
            if notice.state == "pending" and notice.delivery_attempt_id is None:
                notice.state = "discarded"
                notice.discard_reason = TaskStopReason.conversation_stop.value
                notice.revision += 1
        inputs_pending = await self._cancel_user_inputs(
            conversation_id, generation=execution_generation
        )
        await self.session.flush()
        run_ids = tuple(
            sorted(
                {
                    admission.run_id
                    for admission in admissions
                    if admission.run_id is not None and admission.run_finished_at is None
                }
            )
        )
        direct_pending = any(
            admission.execution_kind != "run"
            and admission.direct_started_at is not None
            and admission.direct_result is None
            for admission in admissions
        )
        cleanup_pending = (
            inputs_pending
            or any(task.state in INFLIGHT_TASK_STATES for task in tasks)
            or any(notice.state in ("pending", "claimed") for notice in notices)
            or bool(run_ids)
            or direct_pending
        )
        return ClosedExecution(execution_generation, True, cleanup_pending, run_ids)

    async def claim_run_start(
        self,
        *,
        admission_id: str,
        attempt_id: str,
        now: datetime,
    ) -> bool:
        require_aware(now)
        if not 0 < len(attempt_id) <= 64:
            raise ValueError("run start requires an attempt identity")
        admission = await self._lock_live_admission(admission_id)
        if admission.run_id is None or admission.resolved_execution is None:
            raise ExecutionConflictError("admission has no proven run binding")
        if admission.run_start_token is not None or admission.run_finished_at is not None:
            return False
        # Persist before starting. A lost response is uncertain, never permission to replay.
        admission.run_start_token = attempt_id
        admission.run_start_requested_at = now
        await self.session.flush()
        return True

    async def record_run_started(
        self, *, admission_id: str, attempt_id: str, now: datetime
    ) -> bool:
        """Authorize the claimed worker once, immediately before its first work."""
        require_aware(now)
        admission = await self._lock_live_admission(admission_id)
        if (
            not attempt_id
            or admission.run_start_token != attempt_id
            or admission.run_started_at is not None
            or admission.run_finished_at is not None
        ):
            return False
        admission.run_started_at = now
        await self.session.flush()
        return True

    async def record_run_finished(
        self, *, admission_id: str, attempt_id: str, worker_started: bool, now: datetime
    ) -> bool:
        """Record owner teardown even after Stop; a paused HITL is not finished."""
        require_aware(now)
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.id) == admission_id,
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            admission is None
            or not attempt_id
            or admission.run_start_token != attempt_id
            or admission.run_finished_at is not None
            or (admission.run_started_at is not None) != worker_started
            or (worker_started and admission.run_terminal_status is None)
        ):
            return False
        if not worker_started:
            admission.run_terminal_status = "cancelled"
            admission.run_terminal_at = admission.run_terminal_at or now
        admission.run_finished_at = now
        await self.session.flush()
        return True

    async def record_run_terminal_outcome(
        self,
        *,
        admission_id: str,
        attempt_id: str,
        status: RunTerminalStatus,
        now: datetime,
    ) -> bool:
        """Persist a worker-owned result before Redis teardown can erase it."""
        require_aware(now)
        if status not in RUN_TERMINAL_STATUSES:
            raise ValueError("run terminal outcome is not durable")
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.id) == admission_id,
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            admission is None
            or not attempt_id
            or admission.run_start_token != attempt_id
            or admission.run_started_at is None
            or admission.run_finished_at is not None
        ):
            return False
        if admission.run_terminal_status is not None:
            return admission.run_terminal_status == status
        admission.run_terminal_status = status
        admission.run_terminal_at = now
        await self.session.flush()
        return True

    async def record_unclaimed_run_finished(self, *, admission_id: str, now: datetime) -> bool:
        """Finish stopped work that never acquired a durable start token."""
        require_aware(now)
        admission = await self.session.scalar(
            select(ConversationExecutionAdmission)
            .where(
                col(ConversationExecutionAdmission.id) == admission_id,
                col(ConversationExecutionAdmission.org_id) == self.org_id,
                col(ConversationExecutionAdmission.workspace_id) == self.workspace_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            admission is None
            or admission.run_id is None
            or admission.run_start_token is not None
            or admission.run_started_at is not None
            or admission.run_finished_at is not None
            or (admission.run_stop_requested_at is None and admission.revoked_at is None)
        ):
            return False
        admission.run_finished_at = now
        admission.run_terminal_status = "cancelled"
        admission.run_terminal_at = now
        await self.session.flush()
        return True

    async def require_run_authority(self, *, admission_id: str, attempt_id: str) -> None:
        admission = await self._lock_live_admission(admission_id)
        if (
            not attempt_id
            or admission.run_start_token != attempt_id
            or admission.run_started_at is None
            or admission.run_finished_at is not None
        ):
            raise ExecutionRevokedError("worker no longer owns this execution")

    async def require_reflection_authority(self, *, admission_id: str, attempt_id: str) -> None:
        """Allow post-run memory work only for a finished, still-authorized source."""
        admission = await self._lock_live_admission(admission_id)
        if (
            not attempt_id
            or admission.run_start_token != attempt_id
            or admission.run_started_at is None
            or admission.run_finished_at is None
        ):
            raise ExecutionRevokedError("reflection has no finished execution receipt")

    async def _lock_live_admission(self, admission_id: str) -> ConversationExecutionAdmission:
        repository = ConversationExecutionAdmissionRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        )
        admission = await repository.get(admission_id)
        if admission is None:
            raise LookupError("execution admission not found")
        conversation = await self._lock_authorized_conversation(
            admission.conversation_id,
            admission.actor_user_id,
        )
        await self.session.refresh(admission, with_for_update=True)
        if (
            admission.revoked_at is not None
            or admission.run_stop_requested_at is not None
            or conversation.execution_closed_at is not None
            or conversation.execution_generation != admission.execution_generation
        ):
            raise ExecutionRevokedError("original admission has been revoked")
        return admission

    async def admit_user_message(
        self,
        *,
        conversation_id: str,
        actor_user_id: str,
        namespace: Literal["web", "steer", "im"],
        source_id: str,
        intent: UserMessageIntent,
        snapshot: LLMSnapshot,
        now: datetime,
    ) -> AdmittedExecution:
        require_aware(now)
        if namespace not in ("web", "steer", "im") or not 0 < len(source_id) <= 200:
            raise ValueError("a user input requires a namespaced stable source ID")
        if not intent.content.strip() and not intent.attachment_ids:
            raise ValueError("a user input requires content or attachments")
        conversation = await self._lock_authorized_conversation(conversation_id, actor_user_id)
        fingerprint = self._fingerprint(intent)
        repository = ConversationExecutionAdmissionRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        )
        previous = await repository.get_source(
            source_kind="user_message", source_id=f"{namespace}:{source_id}"
        )
        if previous is not None:
            if (
                previous.conversation_id != conversation_id
                or previous.actor_user_id != actor_user_id
                or previous.execution_kind != "run"
                or previous.request_fingerprint != fingerprint
                or previous.resolved_execution is None
                or previous.run_id is None
            ):
                raise ExecutionConflictError(
                    "source is already bound to different or unproven work"
                )
            execution = ResolvedExecution.model_validate(previous.resolved_execution)
            if self._needs_run_start(previous):
                self._validate_models(execution, snapshot)
            return AdmittedExecution(previous, execution, False)

        preset = resolve_model_preset(snapshot, intent.model_key)
        execution = ResolvedExecution(
            model_key=preset.key,
            primary=preset.primary,
            fallbacks=preset.fallbacks,
            reasoning=intent.reasoning.model_copy(deep=True),
            trigger="im" if namespace == "im" else "interactive",
        )
        await self._attach_files(conversation_id, actor_user_id, intent.attachment_ids, now)
        if conversation.execution_closed_at is not None:
            conversation.execution_generation += 1
            conversation.execution_closed_at = None
        admission = ConversationExecutionAdmission(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=conversation_id,
            actor_user_id=actor_user_id,
            source_kind="user_message",
            source_id=f"{namespace}:{source_id}",
            execution_generation=conversation.execution_generation,
            execution_kind="run",
            request_fingerprint=fingerprint,
            resolved_execution=execution.model_dump(mode="json"),
            run_id=str(uuid4()),
            created_at=now,
            updated_at=now,
        )
        conversation.has_messages = True
        conversation.model_key = intent.model_key
        conversation.reasoning = execution.reasoning.model_dump(mode="json")
        conversation.updated_at = now
        self.session.add(admission)
        await self.session.flush()
        return AdmittedExecution(admission, execution, True)

    async def admit_automatic_run(
        self,
        *,
        conversation_id: str,
        actor_user_id: str,
        source_kind: Literal["schedule_occurrence", "trigger_occurrence"],
        source_id: str,
        intent: UserMessageIntent,
        execution: ResolvedExecution,
        snapshot: LLMSnapshot,
        now: datetime,
        run_id: str | None = None,
        expected_execution_generation: int | None = None,
        expected_generation_closed: bool | None = None,
    ) -> AdmittedExecution:
        """Bind one frozen automatic occurrence to one conversation and run."""
        require_aware(now)
        if source_kind not in ("schedule_occurrence", "trigger_occurrence"):
            raise ValueError("automatic execution requires a trusted source kind")
        if not 0 < len(source_id) <= 255:
            raise ValueError("automatic execution requires a stable source ID")
        if execution.trigger != "automated":
            raise ValueError("automatic execution requires an automated model snapshot")
        if not intent.content.strip() and not intent.attachment_ids:
            raise ValueError("automatic execution requires content or attachments")
        if (expected_execution_generation is None) != (expected_generation_closed is None):
            raise ValueError("automatic generation expectation must be complete")

        conversation = await self._lock_authorized_conversation(conversation_id, actor_user_id)
        fingerprint = self._fingerprint(intent)
        repository = ConversationExecutionAdmissionRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        )
        previous = await repository.get_source(source_kind=source_kind, source_id=source_id)
        if previous is not None:
            if (
                previous.conversation_id != conversation_id
                or previous.actor_user_id != actor_user_id
                or previous.execution_kind != "run"
                or previous.request_fingerprint != fingerprint
                or previous.resolved_execution != execution.model_dump(mode="json")
                or previous.run_id is None
                or (run_id is not None and previous.run_id != run_id)
            ):
                raise ExecutionConflictError(
                    "automatic source is already bound to different or unproven work"
                )
            if self._needs_run_start(previous):
                self._validate_models(execution, snapshot)
            return AdmittedExecution(previous, execution, False)

        if expected_execution_generation is not None and (
            conversation.execution_generation != expected_execution_generation
            or (conversation.execution_closed_at is not None) != expected_generation_closed
        ):
            raise ExecutionRevokedError(
                "automatic occurrence predates the current conversation generation"
            )
        self._validate_models(execution, snapshot)
        await self._attach_files(conversation_id, actor_user_id, intent.attachment_ids, now)
        if conversation.execution_closed_at is not None:
            conversation.execution_generation += 1
            conversation.execution_closed_at = None
        admission = ConversationExecutionAdmission(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=conversation_id,
            actor_user_id=actor_user_id,
            source_kind=source_kind,
            source_id=source_id,
            execution_generation=conversation.execution_generation,
            execution_kind="run",
            request_fingerprint=fingerprint,
            resolved_execution=execution.model_dump(mode="json"),
            run_id=run_id or str(uuid4()),
            created_at=now,
            updated_at=now,
        )
        conversation.has_messages = True
        conversation.updated_at = now
        self.session.add(admission)
        await self.session.flush()
        return AdmittedExecution(admission, execution, True)

    async def cancel_unstarted_automatic_run(
        self,
        *,
        source_kind: Literal["schedule_occurrence", "trigger_occurrence"],
        source_id: str,
        now: datetime,
    ) -> bool:
        """Cancel an automatic occurrence only while no worker can own it."""
        require_aware(now)
        admission = await ConversationExecutionAdmissionRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        ).get_source_locked(source_kind=source_kind, source_id=source_id)
        if admission is None:
            return True
        if admission.run_start_token is not None or admission.run_started_at is not None:
            return False
        admission.revoked_at = admission.revoked_at or now
        admission.run_stop_requested_at = admission.run_stop_requested_at or now
        if admission.run_finished_at is None:
            admission.run_finished_at = now
            admission.run_terminal_status = "cancelled"
            admission.run_terminal_at = now
        await self.session.flush()
        return True

    async def admit_direct_user_message(
        self,
        *,
        conversation_id: str,
        actor_user_id: str,
        namespace: Literal["web", "steer", "im"],
        source_id: str,
        intent: UserMessageIntent,
        operation: Literal["skill_install"],
        now: datetime,
    ) -> AdmittedDirectExecution:
        require_aware(now)
        if namespace not in ("web", "steer", "im") or not 0 < len(source_id) <= 200:
            raise ValueError("a user input requires a namespaced stable source ID")
        conversation = await self._lock_authorized_conversation(conversation_id, actor_user_id)
        fingerprint = self._fingerprint(intent)
        repository = ConversationExecutionAdmissionRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        )
        previous = await repository.get_source(
            source_kind="user_message", source_id=f"{namespace}:{source_id}"
        )
        if previous is not None:
            if (
                previous.conversation_id != conversation_id
                or previous.actor_user_id != actor_user_id
                or previous.execution_kind != operation
                or previous.request_fingerprint != fingerprint
                or previous.run_id is not None
            ):
                raise ExecutionConflictError(
                    "source is already bound to different or unproven work"
                )
            result = (
                DirectExecutionResult.model_validate(previous.direct_result)
                if previous.direct_result is not None
                else None
            )
            return AdmittedDirectExecution(previous, result, False)

        if conversation.execution_closed_at is not None:
            conversation.execution_generation += 1
            conversation.execution_closed_at = None
        admission = ConversationExecutionAdmission(
            org_id=self.org_id,
            workspace_id=self.workspace_id,
            conversation_id=conversation_id,
            actor_user_id=actor_user_id,
            source_kind="user_message",
            source_id=f"{namespace}:{source_id}",
            execution_generation=conversation.execution_generation,
            execution_kind=operation,
            request_fingerprint=fingerprint,
            created_at=now,
            updated_at=now,
        )
        conversation.has_messages = True
        conversation.updated_at = now
        self.session.add(admission)
        await self.session.flush()
        return AdmittedDirectExecution(admission, None, True)

    async def claim_direct_execution(
        self,
        *,
        admission_id: str,
        conversation_id: str,
        actor_user_id: str,
        now: datetime,
    ) -> None:
        require_aware(now)
        conversation = await self._lock_authorized_conversation(conversation_id, actor_user_id)
        admission = await self.session.get(
            ConversationExecutionAdmission,
            admission_id,
            with_for_update=True,
            populate_existing=True,
        )
        if (
            admission is None
            or admission.conversation_id != conversation_id
            or admission.actor_user_id != actor_user_id
            or admission.execution_kind != "skill_install"
            or admission.direct_result is not None
        ):
            raise ExecutionConflictError("direct execution does not match its admission")
        if admission.direct_started_at is not None:
            raise ExecutionConflictError("direct execution outcome is pending reconciliation")
        if (
            admission.revoked_at is not None
            or conversation.execution_closed_at is not None
            or conversation.execution_generation != admission.execution_generation
        ):
            raise ExecutionRevokedError("original admission has been revoked")
        admission.direct_started_at = now
        admission.updated_at = now
        await self.session.flush()

    async def finish_direct_execution(
        self,
        *,
        admission_id: str,
        result: DirectExecutionResult,
        now: datetime,
    ) -> None:
        require_aware(now)
        admission = await self.session.get(
            ConversationExecutionAdmission,
            admission_id,
            with_for_update=True,
            populate_existing=True,
        )
        if (
            admission is None
            or admission.execution_kind != result.kind
            or admission.direct_started_at is None
        ):
            raise ExecutionConflictError("direct execution has no matching start")
        if admission.direct_result is not None:
            if DirectExecutionResult.model_validate(admission.direct_result) != result:
                raise ExecutionConflictError("direct execution already has a different result")
            return
        admission.direct_result = result.model_dump(mode="json")
        admission.updated_at = now
        await self.session.flush()

    async def mark_direct_checkpoint_committed(self, *, admission_id: str, now: datetime) -> None:
        require_aware(now)
        admission = await self.session.get(
            ConversationExecutionAdmission,
            admission_id,
            with_for_update=True,
            populate_existing=True,
        )
        if admission is None or admission.direct_result is None:
            raise ExecutionConflictError("direct execution has no durable result")
        admission.checkpoint_committed_at = admission.checkpoint_committed_at or now
        admission.updated_at = now
        await self.session.flush()

    async def lock_direct_checkpoint_result(
        self, *, admission_id: str
    ) -> tuple[DirectExecutionResult, bool]:
        admission = await self.session.get(
            ConversationExecutionAdmission,
            admission_id,
            with_for_update=True,
            populate_existing=True,
        )
        if admission is None or admission.direct_result is None:
            raise ExecutionConflictError("direct execution has no durable result")
        return (
            DirectExecutionResult.model_validate(admission.direct_result),
            admission.checkpoint_committed_at is not None,
        )

    @staticmethod
    def _validate_models(execution: ResolvedExecution, snapshot: LLMSnapshot) -> None:
        for ref in (execution.primary, *execution.fallbacks):
            slug, model_id = parse_model_ref(ref)
            provider = snapshot.providers.get(slug)
            if provider is None or not any(model.id == model_id for model in provider.models):
                raise ExecutionRevokedError("original model is no longer available")

    async def _lock_authorized_conversation(
        self,
        conversation_id: str,
        actor_user_id: str,
    ) -> Conversation:
        # Serialize revocation without conflicting with billing/memory foreign-key checks.
        user = await self.session.scalar(
            select(User)
            .where(col(User.id) == actor_user_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        workspace = await self.session.scalar(
            select(Workspace)
            .where(
                col(Workspace.id) == self.workspace_id,
                col(Workspace.org_id) == self.org_id,
            )
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        member = await self.session.scalar(
            select(Membership)
            .where(
                col(Membership.user_id) == actor_user_id,
                col(Membership.workspace_id) == self.workspace_id,
            )
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if user is None or not user.is_active or workspace is None or member is None:
            raise LookupError("conversation not found")
        location = (
            await self.session.execute(
                select(col(Conversation.id), col(Conversation.topic_id)).where(
                    col(Conversation.id) == conversation_id,
                    col(Conversation.org_id) == self.org_id,
                    col(Conversation.workspace_id) == self.workspace_id,
                    col(Conversation.deleted_at).is_(None),
                )
            )
        ).one_or_none()
        if location is None:
            raise LookupError("conversation not found")
        topic_id = location.topic_id
        topic_member = None
        # Lock access-granting rows before the conversation, including B4's archive gate.
        if topic_id is not None:
            topic = await self.session.scalar(
                select(Topic)
                .where(
                    col(Topic.id) == topic_id,
                    col(Topic.org_id) == self.org_id,
                    col(Topic.workspace_id) == self.workspace_id,
                    col(Topic.is_archived).is_(False),
                )
                .with_for_update(key_share=True)
            )
            if topic is None:
                raise LookupError("conversation not found")
            topic_member = await self.session.scalar(
                select(TopicParticipant)
                .where(
                    col(TopicParticipant.topic_id) == topic_id,
                    col(TopicParticipant.user_id) == actor_user_id,
                )
                .with_for_update(key_share=True)
            )
        conversation_member = await self.session.scalar(
            select(ConversationParticipant)
            .where(
                col(ConversationParticipant.conversation_id) == conversation_id,
                col(ConversationParticipant.user_id) == actor_user_id,
            )
            .with_for_update(key_share=True)
        )
        accessible = ConversationRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id, user_id=actor_user_id
        ).accessible_id_subquery()
        conversation = await self.session.scalar(
            select(Conversation)
            .where(
                col(Conversation.id) == conversation_id,
                col(Conversation.org_id) == self.org_id,
                col(Conversation.workspace_id) == self.workspace_id,
                col(Conversation.deleted_at).is_(None),
                col(Conversation.topic_id).is_not_distinct_from(topic_id),
                col(Conversation.id).in_(accessible),
            )
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        if conversation is None:
            raise LookupError("conversation not found")
        if (
            topic_member is None
            and conversation_member is None
            and not (topic_id is None and conversation.creator_user_id == actor_user_id)
        ):
            # A just-inserted grant was not locked; retry admission instead of using it.
            raise LookupError("conversation not found")
        return conversation

    async def _attach_files(
        self,
        conversation_id: str,
        actor_user_id: str,
        attachment_ids: tuple[str, ...],
        now: datetime,
    ) -> None:
        for attachment_id in sorted(set(attachment_ids)):
            row = await self.session.scalar(
                select(Attachment)
                .where(
                    col(Attachment.id) == attachment_id,
                    col(Attachment.org_id) == self.org_id,
                    col(Attachment.workspace_id) == self.workspace_id,
                    col(Attachment.conversation_id) == conversation_id,
                    col(Attachment.uploader_user_id) == actor_user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None or row.status not in ("pending", "attached"):
                raise ValueError("attachment is not available for this input")
            if row.status == "pending":
                row.status = "attached"
                row.attached_at = now
                row.updated_at = now
