"""The shared 'something decided to run an agent' seam.

``dispatch_scheduled_run`` resolves/creates the target conversation, builds a
``RunContext`` for the OWNER, and calls the same ``RunManager.start_run`` an
interactive message uses. #152 (triggers) and #153 (managed agents) reuse this.

When ``task.target_mode == "im_channel"`` the dispatcher does NOT start a run
directly — it synthesizes the same outbox rows an inbound IM message would
write and lets the existing ``IMRunQueueWorker`` start the run. The poller
hands its open session + claimed row to ``dispatch_scheduled_run`` for this
branch so the receipt, queue item, and ``ScheduledTaskRun`` terminal-state
update commit together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from cubebox.db.engine import async_session_maker
from cubebox.im.conversation_resolver import resolve_im_conversation
from cubebox.im.run_handoff import enqueue_im_channel_run
from cubebox.models.im_connector import IMConnectorAccount
from cubebox.models.scheduled_task import ScheduledTask, ScheduledTaskRun
from cubebox.repositories.conversation import ConversationRepository
from cubebox.repositories.membership import MembershipRepository
from cubebox.streams.run_manager import RunContext, RunManager


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


@dataclass(slots=True)
class DispatchResult:
    run_id: str
    conversation_id: str


async def _owner_still_member(task: ScheduledTask) -> bool:
    async with async_session_maker() as session:
        role = await MembershipRepository(session).get_role(
            user_id=task.owner_user_id, workspace_id=task.workspace_id
        )
    return role is not None


async def resolve_target(task: ScheduledTask) -> str:
    """Return a conversation_id owned by the task owner, creating one if needed."""
    async with async_session_maker() as session:
        repo = ConversationRepository(
            session,
            org_id=task.org_id,
            workspace_id=task.workspace_id,
            user_id=task.owner_user_id,
        )
        if task.target_mode == "fixed":
            if task.target_conversation_id is None:
                raise TargetUnavailableError("fixed target has no conversation id")
            conv = await repo.get_by_id(task.target_conversation_id)
            if conv is None:
                raise TargetUnavailableError("fixed target not found or not owner-owned")
            return conv.id
        conv = await repo.create(title=task.name, topic_id=task.topic_id)
        return conv.id


async def dispatch_scheduled_run(
    *,
    task: ScheduledTask,
    run_manager: RunManager,
    run_id: str | None = None,
    session: AsyncSession | None = None,
    run_row: ScheduledTaskRun | None = None,
) -> DispatchResult | None:
    """Start one run for one occurrence.

    ``run_id`` may be supplied by the caller so the run id is known BEFORE
    ``run_manager.start_run`` actually launches the background task. The
    poller uses this to pre-stamp the occurrence row with ``run_id``,
    closing the race where a very short run could complete (and call the
    completion hook) before the poller had a chance to commit the
    ``started`` / ``run_id`` update.

    For ``task.target_mode == "im_channel"`` the caller MUST pass ``session``
    (its open dispatch session) and ``run_row`` (the claimed
    ``ScheduledTaskRun``). The dispatcher synthesizes the IM outbox rows and
    commits the row's terminal state itself; no ``DispatchResult`` is
    returned in that branch.

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
    if not await _owner_still_member(task):
        raise TargetUnavailableError("owner is no longer a workspace member")

    if task.target_mode == "im_channel":
        assert session is not None and run_row is not None, (
            "im_channel dispatch requires session + run_row from poller"
        )
        assert task.im_account_id is not None, "im_channel task missing im_account_id"
        assert task.im_channel_id is not None, "im_channel task missing im_channel_id"
        assert task.im_scope_key is not None, "im_channel task missing im_scope_key"
        assert task.im_scope_kind is not None, "im_channel task missing im_scope_kind"
        account = await session.get(IMConnectorAccount, task.im_account_id)
        if account is None:
            run_row.state = "failed"
            run_row.detail = "im_account_unlinked"
            await session.commit()
            return None

        resolved = await resolve_im_conversation(
            session,
            account,
            channel_id=task.im_channel_id,
            scope_key=task.im_scope_key,
            scope_kind=task.im_scope_kind,
            effective_user_id=task.owner_user_id,
            title_hint=f"Scheduled: {task.prompt[:80]}",
            origin="schedule",
        )

        await enqueue_im_channel_run(
            session,
            account=account,
            conversation_id=resolved.conversation_id,
            content=task.prompt,
            channel_id=task.im_channel_id,
            scope_key=task.im_scope_key,
            scope_kind=task.im_scope_kind,
            owner_user_id=task.owner_user_id,
            platform_event_id=f"schedule:{run_row.id}",
        )

        # Fire-and-forget terminal state — the worker owns the real run.
        # ``started_at`` mirrors the non-IM path's post-dispatch write so
        # downstream UI ("View conversation") sees a populated timestamp.
        run_row.conversation_id = resolved.conversation_id
        run_row.state = "succeeded"
        run_row.detail = "im_channel_enqueued"
        run_row.started_at = datetime.now(UTC)
        await session.commit()
        return None

    conversation_id = await resolve_target(task)
    ctx = RunContext(
        user_id=task.owner_user_id,
        org_id=task.org_id,
        workspace_id=task.workspace_id,
        conversation_id=conversation_id,
        trigger="automated",
    )
    try:
        actual_run_id = await run_manager.start_run(
            conversation_id=conversation_id,
            content=task.prompt,
            attachments=[],
            ctx=ctx,
            run_id=run_id,
        )
    except RuntimeError as exc:
        # RunManager.start_run can reject a second run on the same conversation
        # in two distinct ways:
        # - "already has an active run" — the busy case; postpone + retry.
        # - "has a pending HITL request" — paused on user input; do NOT retry,
        #   the user has to answer or cancel before another occurrence can fire.
        if task.target_mode == "fixed":
            msg = str(exc)
            if "pending HITL request" in msg:
                raise ConversationPausedError(msg) from exc
            if "already" in msg.lower():
                raise ConversationBusyError(msg) from exc
        raise
    return DispatchResult(run_id=actual_run_id, conversation_id=conversation_id)
