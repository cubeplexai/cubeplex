"""Shared durable steering state machine for Web and IM callers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from cubeplex.agents.checkpointer import shared_checkpointer
from cubeplex.models.conversation import Conversation
from cubeplex.models.user import User
from cubeplex.repositories.steering_message import (
    MAX_MESSAGE_BYTES,
    SteeringConversationUnavailableError,
    SteeringMessageConflictError,
    SteeringMessageContentTooLargeError,
    SteeringMessageQueueFullError,
    SteeringMessageRepository,
)
from cubeplex.services.conversation_execution import (
    ConversationExecutionService,
    ExecutionConflictError,
    ExecutionRevokedError,
)
from cubeplex.streams.run_events import get_active_run, get_run_meta

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from cubeplex.streams.run_manager import RunManager

SteerAttemptOutcome = Literal[
    "queued",
    "no_active_run",
    "actor_conflict",
    "queue_full",
    "too_large",
    "steer_id_conflict",
    "conversation_unavailable",
]


@dataclass(frozen=True, slots=True)
class SteerAttemptResult:
    """Outcome of attempting to enqueue a durable mid-run steer."""

    outcome: SteerAttemptOutcome
    http_status: str
    run_id: str | None
    steer_id: str
    error_detail: str | None = None


def durable_steer_status(state: object) -> str:
    """Map durable SteeringMessage.state to the public HTTP status string."""
    value = str(state)
    return "cancelled" if value == "cancel_requested" else value


async def _load_pending_hitl(conversation_id: str) -> tuple[Any, str | None]:
    """Read cubeloop-persisted pending_request + persisted run_id in one open."""
    async with shared_checkpointer() as cp:
        pending_req = await cp.load_pending_request(conversation_id)
        persisted_run_id = await cp.load_pending_run_id(conversation_id)
    return pending_req, persisted_run_id


async def _resolve_sender_display_name(
    session: AsyncSession,
    actor_user_id: str,
    sender_display_name: str | None,
) -> str | None:
    if sender_display_name is not None:
        return sender_display_name
    user = await session.get(User, actor_user_id)
    if user is None:
        return None
    return user.display_name or user.email


async def steer_conversation_run(
    *,
    session: AsyncSession,
    org_id: str,
    workspace_id: str,
    conversation: Conversation,
    actor_user_id: str,
    sender_display_name: str | None = None,
    client_steer_id: str,
    content: str,
    redis: Redis,
    redis_key_prefix: str,
    run_manager: RunManager | None,
) -> SteerAttemptResult:
    """Enqueue a durable steer into a running or HITL-paused conversation run.

    Preserves exact HTTP status strings in ``http_status`` while reporting a
    normalized ``outcome`` for both HTTP and IM callers. When a run finishes
    between initial inspection and commit, the queued row is marked failed and
    ``outcome`` is ``"no_active_run"`` (with ``http_status`` preserving the
    post-commit state).
    """
    if len(content.encode("utf-8")) > MAX_MESSAGE_BYTES:
        return SteerAttemptResult(
            outcome="too_large",
            http_status="too_large",
            run_id=None,
            steer_id=client_steer_id,
        )

    conversation_id = conversation.id
    steering_repo = SteeringMessageRepository(
        session,
        org_id=org_id,
        workspace_id=workspace_id,
    )
    existing = await steering_repo.get_by_client_id(
        conversation_id=conversation_id,
        client_steer_id=client_steer_id,
    )
    if existing is not None:
        if (
            existing.source_kind != "user_message"
            or existing.content != content
            or existing.sender_user_id != actor_user_id
        ):
            return SteerAttemptResult(
                outcome="steer_id_conflict",
                http_status="steer_id_conflict",
                run_id=existing.run_id,
                steer_id=existing.client_steer_id,
            )
        return SteerAttemptResult(
            outcome="queued",
            http_status=durable_steer_status(existing.state),
            run_id=existing.run_id,
            steer_id=existing.client_steer_id,
        )

    active_run = await get_active_run(
        redis, prefix=redis_key_prefix, conversation_id=conversation_id
    )
    pending_request, pending_run_id = await _load_pending_hitl(conversation_id)
    pending_run_meta = (
        await get_run_meta(redis, prefix=redis_key_prefix, run_id=pending_run_id)
        if pending_run_id is not None
        else None
    )
    pending_is_deliverable = pending_run_meta is None or pending_run_meta.status in (
        "paused_hitl",
        "running",
        "stale",
    )

    if pending_request is not None and pending_run_id is not None and pending_is_deliverable:
        target_run_id = pending_run_id
        hitl_question_id: str | None = pending_request.question_id
    elif active_run is not None and active_run.status == "running":
        target_run_id = active_run.run_id
        hitl_question_id = None
    else:
        return SteerAttemptResult(
            outcome="no_active_run",
            http_status="no_active_run",
            run_id=None,
            steer_id=client_steer_id,
        )

    resolved_name = await _resolve_sender_display_name(session, actor_user_id, sender_display_name)
    exec_service = ConversationExecutionService(session, org_id=org_id, workspace_id=workspace_id)
    try:
        input_admission = await exec_service.require_run_input_actor(
            conversation_id=conversation_id,
            run_id=target_run_id,
            actor_user_id=actor_user_id,
        )
        queued, _created = await steering_repo.enqueue(
            conversation_id=conversation_id,
            run_id=target_run_id,
            client_steer_id=client_steer_id,
            content=content,
            sender_user_id=actor_user_id,
            sender_display_name=resolved_name,
            hitl_question_id=hitl_question_id,
            execution_generation=(
                input_admission.execution_generation
                if input_admission is not None
                else conversation.execution_generation
            ),
        )
    except SteeringMessageContentTooLargeError:
        return SteerAttemptResult(
            outcome="too_large",
            http_status="too_large",
            run_id=None,
            steer_id=client_steer_id,
        )
    except SteeringMessageQueueFullError:
        return SteerAttemptResult(
            outcome="queue_full",
            http_status="queue_full",
            run_id=None,
            steer_id=client_steer_id,
        )
    except SteeringMessageConflictError:
        return SteerAttemptResult(
            outcome="steer_id_conflict",
            http_status="steer_id_conflict",
            run_id=target_run_id,
            steer_id=client_steer_id,
        )
    except SteeringConversationUnavailableError:
        return SteerAttemptResult(
            outcome="conversation_unavailable",
            http_status="conversation_unavailable",
            run_id=None,
            steer_id=client_steer_id,
        )
    except (ExecutionConflictError, ExecutionRevokedError) as exc:
        return SteerAttemptResult(
            outcome="actor_conflict",
            http_status="actor_conflict",
            run_id=target_run_id,
            steer_id=client_steer_id,
            error_detail=str(exc),
        )

    await session.commit()

    current_active_run = await get_active_run(
        redis, prefix=redis_key_prefix, conversation_id=conversation_id
    )
    if hitl_question_id is not None:
        current_pending, current_pending_run_id = await _load_pending_hitl(conversation_id)
        pending_still_owns = (
            current_pending is not None
            and current_pending_run_id == target_run_id
            and current_pending.question_id == queued.hitl_question_id
        )
        active_still_owns = (
            current_active_run is not None
            and current_active_run.run_id == target_run_id
            and current_active_run.status in ("running", "paused_hitl")
        )
        still_deliverable = pending_still_owns or active_still_owns
    else:
        still_deliverable = (
            current_active_run is not None
            and current_active_run.run_id == queued.run_id
            and current_active_run.status == "running"
        )

    if not still_deliverable:
        await steering_repo.fail_queued(row_id=queued.id)
        await session.commit()
        if hitl_question_id is not None:
            await session.refresh(queued)
            failed_status = durable_steer_status(queued.state)
        else:
            failed_status = "failed"
        return SteerAttemptResult(
            outcome="no_active_run",
            http_status=failed_status,
            run_id=queued.run_id,
            steer_id=queued.client_steer_id,
        )

    if run_manager is not None:
        try:
            await run_manager.notify_durable_steer(target_run_id, queued.id)
        except Exception:
            logger.exception(
                "Durable steer {} committed but run {} wake-up failed",
                queued.id,
                target_run_id,
            )
    await session.refresh(queued)
    return SteerAttemptResult(
        outcome="queued",
        http_status=durable_steer_status(queued.state),
        run_id=queued.run_id,
        steer_id=queued.client_steer_id,
    )
