"""The shared 'something decided to run an agent' seam.

``dispatch_scheduled_run`` resolves/creates the target conversation, builds a
``RunContext`` for the OWNER, and calls the same ``RunManager.start_run`` an
interactive message uses. #152 (triggers) and #153 (managed agents) reuse this.
"""

from __future__ import annotations

from dataclasses import dataclass

from cubebox.db.engine import async_session_maker
from cubebox.models.scheduled_task import ScheduledTask
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
            if conv.topic_id is not None:
                # Topic-aware schedule dispatch is not implemented in v1; refuse
                # rather than silently running with is_group_chat=False.
                raise TargetUnavailableError(
                    "fixed target is a topic conversation; topic-aware schedule "
                    "dispatch is not implemented in v1"
                )
            return conv.id
        conv = await repo.create(title=task.name)
        return conv.id


async def dispatch_scheduled_run(
    *,
    task: ScheduledTask,
    run_manager: RunManager,
    run_id: str | None = None,
) -> DispatchResult:
    """Start one run for one occurrence.

    ``run_id`` may be supplied by the caller so the run id is known BEFORE
    ``run_manager.start_run`` actually launches the background task. The
    poller uses this to pre-stamp the occurrence row with ``run_id``,
    closing the race where a very short run could complete (and call the
    completion hook) before the poller had a chance to commit the
    ``started`` / ``run_id`` update.

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
    conversation_id = await resolve_target(task)
    ctx = RunContext(
        user_id=task.owner_user_id,
        org_id=task.org_id,
        workspace_id=task.workspace_id,
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
