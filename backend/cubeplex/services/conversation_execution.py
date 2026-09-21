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
from cubeplex.models.membership import Membership
from cubeplex.models.user import User
from cubeplex.models.workspace import Workspace
from cubeplex.repositories.background_task import ConversationExecutionAdmissionRepository
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.services.background_task_lifecycle import require_aware


class ExecutionConflictError(ValueError):
    """An immutable source identity was reused for different work."""


class ExecutionRevokedError(ValueError):
    """The original admission no longer authorizes execution."""


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
class ClosedExecution:
    execution_generation: int
    accepted: bool
    cleanup_pending: bool
    run_ids: tuple[str, ...]


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
            or admission.source_kind != "user_message"
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
        admission = await self._lock_live_admission(
            admission.id, additional_actor_user_id=responding_user_id
        )
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
        cleanup_pending = (
            any(task.state in INFLIGHT_TASK_STATES for task in tasks)
            or any(notice.state in ("pending", "claimed") for notice in notices)
            or any(
                admission.run_start_token is not None and admission.run_finished_at is None
                for admission in admissions
            )
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
        ):
            return False
        admission.run_finished_at = now
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

    async def _lock_live_admission(
        self, admission_id: str, *, additional_actor_user_id: str | None = None
    ) -> ConversationExecutionAdmission:
        repository = ConversationExecutionAdmissionRepository(
            self.session, org_id=self.org_id, workspace_id=self.workspace_id
        )
        admission = await repository.get(admission_id)
        if admission is None:
            raise LookupError("execution admission not found")
        conversation = await self._lock_authorized_conversation(
            admission.conversation_id,
            admission.actor_user_id,
            additional_actor_user_id=additional_actor_user_id,
        )
        await self.session.refresh(admission, with_for_update=True)
        if (
            admission.revoked_at is not None
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
            request_fingerprint=fingerprint,
            resolved_execution=execution.model_dump(mode="json"),
            run_id=str(uuid4()),
            created_at=now,
            updated_at=now,
        )
        conversation.model_key = intent.model_key
        conversation.reasoning = execution.reasoning.model_dump(mode="json")
        conversation.updated_at = now
        self.session.add(admission)
        await self.session.flush()
        return AdmittedExecution(admission, execution, True)

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
        *,
        additional_actor_user_id: str | None = None,
    ) -> Conversation:
        # Authority rows precede the conversation lock; deletion must use this order too.
        actors = sorted({actor_user_id, additional_actor_user_id or actor_user_id})
        users = list(
            (
                await self.session.scalars(
                    select(User)
                    .where(col(User.id).in_(actors))
                    .order_by(col(User.id))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        workspace = await self.session.scalar(
            select(Workspace)
            .where(
                col(Workspace.id) == self.workspace_id,
                col(Workspace.org_id) == self.org_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        members = list(
            (
                await self.session.scalars(
                    select(Membership)
                    .where(
                        col(Membership.user_id).in_(actors),
                        col(Membership.workspace_id) == self.workspace_id,
                    )
                    .order_by(col(Membership.user_id))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        if (
            len(users) != len(actors)
            or any(not user.is_active for user in users)
            or workspace is None
            or len(members) != len(actors)
        ):
            raise LookupError("conversation not found")
        query = (
            select(Conversation)
            .where(
                col(Conversation.id) == conversation_id,
                col(Conversation.org_id) == self.org_id,
                col(Conversation.workspace_id) == self.workspace_id,
                col(Conversation.deleted_at).is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        for actor in actors:
            accessible = ConversationRepository(
                self.session, org_id=self.org_id, workspace_id=self.workspace_id, user_id=actor
            ).accessible_id_subquery()
            query = query.where(col(Conversation.id).in_(accessible))
        conversation = await self.session.scalar(query)
        if conversation is None:
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
