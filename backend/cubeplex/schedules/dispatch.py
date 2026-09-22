"""Turn one frozen schedule occurrence into a durable agent-run handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from cubeloop.providers.base import ReasoningControl
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.im.conversation_resolver import resolve_im_conversation
from cubeplex.im.run_handoff import enqueue_im_channel_run
from cubeplex.llm.resolver import resolve_model_preset
from cubeplex.llm.snapshot import LLMSnapshot
from cubeplex.models.conversation import Conversation
from cubeplex.models.im_connector import IMConnectorAccount, IMThreadLink
from cubeplex.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubeplex.repositories.conversation import ConversationRepository
from cubeplex.repositories.membership import MembershipRepository
from cubeplex.services.conversation_execution import (
    ConversationExecutionService,
    ResolvedExecution,
    UserMessageIntent,
)
from cubeplex.streams.run_manager import RunContext, RunManager


class TargetUnavailableError(Exception):
    """Fixed target missing/not owner-owned, or owner lost membership."""


class ConversationBusyError(Exception):
    """Fixed target conversation already has a running run.

    The poller (Task 8) catches this and applies the busy-retry policy
    (spec §"One-run-per-conversation interaction"): postpone by 5m up to 3
    times, then terminal ``skipped_busy_max_retries``.
    """


class ConversationPausedError(Exception):
    """Fixed target conversation is paused on a pending HITL request.

    The poller treats this as a terminal ``skipped_paused`` — the user
    has to answer or cancel the pending question before another scheduled
    occurrence can fire. Busy-retry would burn the retry budget on a
    state only the user can clear.
    """


class OccurrenceRevokedError(Exception):
    """The persisted occurrence was stopped before its run could start."""


def raise_scheduled_start_error(target_mode: str, exc: RuntimeError) -> None:
    """Map RunManager's fixed-target conflicts to schedule policy errors."""
    if target_mode == "fixed":
        message = str(exc)
        if "pending HITL request" in message:
            raise ConversationPausedError(message) from exc
        if "already" in message.lower():
            raise ConversationBusyError(message) from exc
    raise exc


@dataclass(slots=True)
class DispatchResult:
    run_id: str
    conversation_id: str


class ScheduledOccurrenceSnapshot(BaseModel):
    """Immutable inputs copied from a schedule when its occurrence is claimed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_user_id: str
    name: str
    content: str
    target_mode: str
    target_conversation_id: str | None = None
    topic_id: str | None = None
    im_account_id: str | None = None
    im_channel_id: str | None = None
    im_scope_key: str | None = None
    im_scope_kind: str | None = None
    bound_conversation_id: str | None = None
    bound_execution_generation: int | None = None
    bound_generation_closed: bool | None = None
    execution: ResolvedExecution


async def freeze_scheduled_occurrence(
    session: AsyncSession, task: ScheduledTask, llm_snapshot: LLMSnapshot
) -> ScheduledOccurrenceSnapshot:
    bound_conversation_id = task.target_conversation_id
    if (
        task.target_mode == "im_channel"
        and task.im_account_id is not None
        and task.im_channel_id is not None
        and task.im_scope_key is not None
    ):
        bound_conversation_id = await session.scalar(
            select(cast(Any, IMThreadLink.conversation_id)).where(
                cast(Any, IMThreadLink.account_id) == task.im_account_id,
                cast(Any, IMThreadLink.channel_id) == task.im_channel_id,
                cast(Any, IMThreadLink.scope_key) == task.im_scope_key,
            )
        )
    bound_conversation = (
        await session.get(Conversation, bound_conversation_id)
        if bound_conversation_id is not None
        else None
    )
    preset = resolve_model_preset(llm_snapshot, None)
    return ScheduledOccurrenceSnapshot(
        owner_user_id=task.owner_user_id,
        name=task.name,
        content=task.prompt,
        target_mode=task.target_mode,
        target_conversation_id=task.target_conversation_id,
        topic_id=task.topic_id,
        im_account_id=task.im_account_id,
        im_channel_id=task.im_channel_id,
        im_scope_key=task.im_scope_key,
        im_scope_kind=task.im_scope_kind,
        bound_conversation_id=(bound_conversation.id if bound_conversation is not None else None),
        bound_execution_generation=(
            bound_conversation.execution_generation if bound_conversation is not None else None
        ),
        bound_generation_closed=(
            bound_conversation.execution_closed_at is not None
            if bound_conversation is not None
            else None
        ),
        execution=ResolvedExecution(
            model_key=preset.key,
            primary=preset.primary,
            fallbacks=preset.fallbacks,
            reasoning=ReasoningControl(),
            trigger="automated",
        ),
    )


async def _owner_still_member(
    session: AsyncSession,
    task: ScheduledTask,
    occurrence: ScheduledOccurrenceSnapshot,
) -> bool:
    role = await MembershipRepository(session).get_role(
        user_id=occurrence.owner_user_id,
        workspace_id=task.workspace_id,
    )
    return role is not None


async def resolve_target(
    session: AsyncSession,
    task: ScheduledTask,
    run_row: ScheduledTaskRun,
    occurrence: ScheduledOccurrenceSnapshot,
) -> str:
    """Bind this occurrence to one target conversation in the caller's transaction."""
    repo = ConversationRepository(
        session,
        org_id=task.org_id,
        workspace_id=task.workspace_id,
        user_id=occurrence.owner_user_id,
    )
    if run_row.conversation_id is not None:
        conv = await repo.get_by_id(run_row.conversation_id)
        if conv is None:
            raise TargetUnavailableError("bound target not found or no longer accessible")
        return conv.id
    if occurrence.target_mode == "fixed":
        if occurrence.target_conversation_id is None:
            raise TargetUnavailableError("fixed target has no conversation id")
        conv = await repo.get_by_id(occurrence.target_conversation_id)
        if conv is None:
            raise TargetUnavailableError("fixed target not found or not owner-owned")
    else:
        conv = await repo.create(
            title=occurrence.name,
            topic_id=occurrence.topic_id,
            draft=True,
            commit=False,
        )
    run_row.conversation_id = conv.id
    await session.flush()
    return conv.id


async def dispatch_scheduled_run(
    *,
    task: ScheduledTask,
    run_manager: RunManager,
    occurrence: ScheduledOccurrenceSnapshot,
    llm_snapshot: LLMSnapshot,
    session: AsyncSession,
    run_row: ScheduledTaskRun,
) -> DispatchResult | None:
    """Start or enqueue the run bound to an immutable occurrence.

    Direct targets commit their target and admission before calling
    ``RunManager``. IM targets commit the receipt, queue item, admission, and
    occurrence state together; the IM worker then starts that admitted run.

    Raises:
      TargetUnavailableError -- owner is gone OR fixed target is missing /
        no longer owner-owned. The poller marks the occurrence ``failed``.
      ConversationBusyError -- ``fixed`` target already has a running run.
        The poller applies the busy-retry policy (postpone 5m, retry up to 3,
        then ``skipped_busy_max_retries``).
      ConversationPausedError -- ``fixed`` target is paused on a pending
        HITL request. The poller marks the occurrence ``skipped_paused``
        without retry (only the user can clear pending).
    """
    if not await _owner_still_member(session, task, occurrence):
        raise TargetUnavailableError("owner is no longer a workspace member")

    if occurrence.target_mode == "im_channel":
        # ``im_account_id`` may be NULL here: the FK uses ON DELETE SET NULL,
        # so an operator who deleted the bound IMConnectorAccount leaves the
        # schedule row alive with the destination field cleared. Treat the
        # missing-account case symmetrically — record a terminal ``failed``
        # state with ``im_account_unlinked`` so the run history explains why
        # the schedule stopped firing without taking the poller down.
        if occurrence.im_account_id is None:
            run_row.state = "failed"
            run_row.detail = "im_account_unlinked"
            await session.commit()
            return None
        assert occurrence.im_channel_id is not None, "im_channel task missing im_channel_id"
        assert occurrence.im_scope_key is not None, "im_channel task missing im_scope_key"
        assert occurrence.im_scope_kind is not None, "im_channel task missing im_scope_kind"
        account = await session.get(IMConnectorAccount, occurrence.im_account_id)
        if account is None:
            run_row.state = "failed"
            run_row.detail = "im_account_unlinked"
            await session.commit()
            return None

        resolved = await resolve_im_conversation(
            session,
            account,
            channel_id=occurrence.im_channel_id,
            scope_key=occurrence.im_scope_key,
            scope_kind=occurrence.im_scope_kind,
            effective_user_id=occurrence.owner_user_id,
            title_hint=f"Scheduled: {occurrence.content[:80]}",
            origin="schedule",
        )

        admitted = await ConversationExecutionService(
            session,
            org_id=task.org_id,
            workspace_id=task.workspace_id,
        ).admit_automatic_run(
            conversation_id=resolved.conversation_id,
            actor_user_id=occurrence.owner_user_id,
            source_kind="schedule_occurrence",
            source_id=run_row.id,
            intent=UserMessageIntent(
                content=occurrence.content,
                model_key=occurrence.execution.model_key,
                reasoning=occurrence.execution.reasoning,
            ),
            execution=occurrence.execution,
            snapshot=llm_snapshot,
            now=datetime.now(UTC),
            run_id=run_row.run_id,
            expected_execution_generation=(
                occurrence.bound_execution_generation
                if resolved.conversation_id == occurrence.bound_conversation_id
                else None
            ),
            expected_generation_closed=(
                occurrence.bound_generation_closed
                if resolved.conversation_id == occurrence.bound_conversation_id
                else None
            ),
        )
        run_row.run_id = admitted.admission.run_id

        await enqueue_im_channel_run(
            session,
            account=account,
            conversation_id=resolved.conversation_id,
            content=occurrence.content,
            channel_id=occurrence.im_channel_id,
            scope_key=occurrence.im_scope_key,
            scope_kind=occurrence.im_scope_kind,
            owner_user_id=occurrence.owner_user_id,
            platform_event_id=f"schedule:{run_row.id}",
            execution_admission_id=admitted.admission.id,
        )

        # Queue handoff is durable but is not evidence that the run started.
        # The completion hook moves this row to its terminal run outcome.
        run_row.conversation_id = resolved.conversation_id
        run_row.state = "queued"
        run_row.detail = "im_channel_queued"
        await session.commit()
        return None

    conversation_id = await resolve_target(session, task, run_row, occurrence)
    admitted = await ConversationExecutionService(
        session,
        org_id=task.org_id,
        workspace_id=task.workspace_id,
    ).admit_automatic_run(
        conversation_id=conversation_id,
        actor_user_id=occurrence.owner_user_id,
        source_kind="schedule_occurrence",
        source_id=run_row.id,
        intent=UserMessageIntent(
            content=occurrence.content,
            model_key=occurrence.execution.model_key,
            reasoning=occurrence.execution.reasoning,
        ),
        execution=occurrence.execution,
        snapshot=llm_snapshot,
        now=datetime.now(UTC),
        run_id=run_row.run_id,
        expected_execution_generation=occurrence.bound_execution_generation,
        expected_generation_closed=occurrence.bound_generation_closed,
    )
    run_row.run_id = admitted.admission.run_id
    admission = admitted.admission
    if admission.run_start_token is None and (
        admission.revoked_at is not None
        or admission.run_stop_requested_at is not None
        or admission.run_finished_at is not None
    ):
        raise OccurrenceRevokedError("scheduled occurrence was revoked before start")
    await session.commit()
    ctx = RunContext(
        user_id=occurrence.owner_user_id,
        org_id=task.org_id,
        workspace_id=task.workspace_id,
        conversation_id=conversation_id,
        trigger="automated",
    )
    try:
        actual_run_id = await run_manager.start_run(
            conversation_id=conversation_id,
            content=occurrence.content,
            attachments=[],
            ctx=ctx,
            run_id=admitted.admission.run_id,
            model_key=occurrence.execution.model_key,
            reasoning=occurrence.execution.reasoning,
            llm_snapshot=llm_snapshot,
            admission_id=admitted.admission.id,
        )
    except RuntimeError as exc:
        # RunManager.start_run can reject a second run on the same conversation
        # in two distinct ways:
        # - "already has an active run" — the busy case; postpone + retry.
        # - "has a pending HITL request" — paused on user input; do NOT retry,
        #   the user has to answer or cancel before another occurrence can fire.
        raise_scheduled_start_error(occurrence.target_mode, exc)
    return DispatchResult(run_id=actual_run_id, conversation_id=conversation_id)
