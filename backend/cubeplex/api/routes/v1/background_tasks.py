"""Workspace conversation background-task snapshots and controls."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from opensandbox.exceptions import SandboxApiException as ProviderApiError
from opensandbox.exceptions import SandboxException as ProviderSandboxError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask as ResponseCleanup

from cubeplex.api.schemas.background_tasks import (
    BackgroundTaskCapabilities,
    BackgroundTaskEventOut,
    BackgroundTaskEventPageResponse,
    BackgroundTaskListResponse,
    BackgroundTaskNotification,
    BackgroundTaskOut,
    CommandTaskDetails,
    StopBackgroundTaskResponse,
)
from cubeplex.auth.context import RequestContext
from cubeplex.auth.dependencies import require_member
from cubeplex.db import get_session
from cubeplex.models.background_task import TERMINAL_TASK_STATES, TaskStopReason
from cubeplex.models.sandbox_command import SandboxCommand
from cubeplex.repositories import ConversationRepository
from cubeplex.repositories.user_sandbox import UserSandboxRepository
from cubeplex.sandbox import SandboxError
from cubeplex.sandbox.manager import get_sandbox_manager
from cubeplex.sandbox.opensandbox import OpenSandbox
from cubeplex.services.background_task_query import (
    BackgroundTaskQueryService,
    InvalidBackgroundTaskCursorError,
    TaskEventProjection,
    TaskProjection,
)
from cubeplex.services.background_tasks import BackgroundTaskService
from cubeplex.utils.http import content_disposition
from cubeplex.utils.time import utc_isoformat

router = APIRouter(
    prefix="/ws/{workspace_id}/conversations/{conversation_id}",
    tags=["background-tasks"],
)


def _optional_time(value: datetime | None) -> str | None:
    return None if value is None else utc_isoformat(value)


def serialize_task(item: TaskProjection) -> BackgroundTaskOut:
    task = item.task
    command = item.command
    details = None
    if command is not None:
        details = CommandTaskDetails(
            command_id=command.id,
            command_kind=command.kind,
            command=command.command,
            status=command.status,
            exit_code=command.exit_code,
            log_path=command.log_path,
            log_state=command.log_state,
            monitor_outcome=command.monitor_outcome,
        )
    return BackgroundTaskOut(
        id=task.id,
        kind=task.kind,
        description=task.description,
        parent_task_id=task.parent_task_id,
        originating_run_id=task.originating_run_id,
        tool_call_id=task.tool_call_id,
        agent_id=task.agent_id,
        execution_generation=task.execution_generation,
        state=task.state,
        deadline_at=_optional_time(task.deadline_at),
        stop_requested_at=_optional_time(task.stop_requested_at),
        stop_reason=task.stop_reason,
        backgrounded_at=_optional_time(task.backgrounded_at),
        finished_at=_optional_time(task.finished_at),
        result_summary=task.result_summary,
        result_ref=task.result_ref,
        result_readiness=task.result_readiness,
        result_unavailable_reason=task.result_unavailable_reason,
        revision=task.revision,
        created_at=utc_isoformat(task.created_at),
        updated_at=utc_isoformat(task.updated_at),
        cleanup_pending=item.cleanup_pending,
        capabilities=BackgroundTaskCapabilities(
            can_stop=item.can_stop,
            remote_cancel_supported=item.remote_cancel_supported,
            reconnect_supported=item.reconnect_supported,
            logs_supported=command is not None and bool(command.log_path),
            input_supported=False,
        ),
        notification=BackgroundTaskNotification(
            enabled=task.notify_on_complete,
            has_pending=item.has_pending_event,
            cancelled_at=_optional_time(task.notifications_cancelled_at),
        ),
        details=details,
    )


def serialize_event(item: TaskEventProjection) -> BackgroundTaskEventOut:
    event = item.event
    return BackgroundTaskEventOut(
        id=event.id,
        task_id=event.task_id,
        task_kind=item.task_kind,
        execution_generation=event.execution_generation,
        reason=event.reason,
        summary=event.summary,
        result_ref=event.result_ref,
        state=event.state,
        discard_reason=event.discard_reason,
        revision=event.revision,
        created_at=utc_isoformat(event.created_at),
        updated_at=utc_isoformat(event.updated_at),
        delivered_at=_optional_time(event.delivered_at),
    )


async def _require_conversation(
    session: AsyncSession, ctx: RequestContext, conversation_id: str
) -> None:
    repository = ConversationRepository(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.workspace_id,
        user_id=ctx.user.id,
    )
    if await repository.get_by_id(conversation_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")


def _query_service(session: AsyncSession, ctx: RequestContext) -> BackgroundTaskQueryService:
    return BackgroundTaskQueryService(session, org_id=ctx.org_id, workspace_id=ctx.workspace_id)


@router.get("/background-tasks", response_model=BackgroundTaskListResponse)
async def list_background_tasks(
    workspace_id: str,
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    task_ids: Annotated[list[str] | None, Query(max_length=100)] = None,
    recent_limit: Annotated[int | None, Query(ge=1, le=100)] = None,
) -> BackgroundTaskListResponse:
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    if task_ids is not None and recent_limit is not None:
        raise HTTPException(
            status_code=422, detail="task_ids and recent_limit are mutually exclusive"
        )
    unique_ids = None if task_ids is None else tuple(dict.fromkeys(task_ids))
    items = await _query_service(session, ctx).list_tasks(
        conversation_id=conversation_id, task_ids=unique_ids, recent_limit=recent_limit
    )
    return BackgroundTaskListResponse(items=[serialize_task(item) for item in items])


@router.get("/background-tasks/{task_id}", response_model=BackgroundTaskOut)
async def get_background_task(
    workspace_id: str,
    conversation_id: str,
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> BackgroundTaskOut:
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    item = await _query_service(session, ctx).get_task(
        conversation_id=conversation_id, task_id=task_id
    )
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return serialize_task(item)


@router.post(
    "/background-tasks/{task_id}/stop",
    response_model=StopBackgroundTaskResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def stop_background_task(
    workspace_id: str,
    conversation_id: str,
    task_id: str,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
) -> StopBackgroundTaskResponse:
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    query = _query_service(session, ctx)
    original = await query.get_task(conversation_id=conversation_id, task_id=task_id)
    if original is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    was_terminal = original.task.state in TERMINAL_TASK_STATES
    remote_cancel_supported = original.remote_cancel_supported
    try:
        await BackgroundTaskService(
            session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
        ).request_task_stop(
            task_id=task_id,
            reason=TaskStopReason.user_stop,
            now=datetime.now(UTC),
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found") from exc
    await session.commit()
    updated = await query.get_task(conversation_id=conversation_id, task_id=task_id)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    response.status_code = status.HTTP_200_OK if was_terminal else status.HTTP_202_ACCEPTED
    return StopBackgroundTaskResponse(
        accepted=True,
        cleanup_pending=updated.cleanup_pending,
        remote_cancel_supported=remote_cancel_supported,
        task=serialize_task(updated),
    )


@router.get("/background-task-events", response_model=BackgroundTaskEventPageResponse)
async def list_background_task_events(
    workspace_id: str,
    conversation_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    delivery: Literal["pending", "all"] = "pending",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> BackgroundTaskEventPageResponse:
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    try:
        page = await _query_service(session, ctx).list_events(
            conversation_id=conversation_id,
            delivery=delivery,
            cursor=cursor,
            limit=limit,
        )
    except InvalidBackgroundTaskCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Invalid background task event cursor",
        ) from exc
    return BackgroundTaskEventPageResponse(
        items=[serialize_event(item) for item in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


def _require_task_output(item: TaskProjection | None) -> tuple[TaskProjection, SandboxCommand]:
    if item is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if item.task.result_readiness == "unavailable":
        raise HTTPException(
            status_code=410,
            detail=item.task.result_unavailable_reason or "Output unavailable",
        )
    command = item.command
    if command is None or not command.log_path:
        raise HTTPException(status_code=404, detail="Task output not found")
    return item, command


@router.get("/background-tasks/{task_id}/output", response_model=None)
async def get_background_task_output(
    workspace_id: str,
    conversation_id: str,
    task_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    ctx: Annotated[RequestContext, Depends(require_member)],
    download: bool = False,
) -> dict[str, str] | StreamingResponse:
    del workspace_id
    await _require_conversation(session, ctx, conversation_id)
    item = await _query_service(session, ctx).get_task(
        conversation_id=conversation_id, task_id=task_id
    )
    item, command = _require_task_output(item)
    path = PurePosixPath(command.log_path)

    stack = AsyncExitStack()
    streaming = False
    try:
        manager = get_sandbox_manager()
        if item.task.state in TERMINAL_TASK_STATES and item.task.result_readiness == "ready":
            record = await UserSandboxRepository(
                session, org_id=ctx.org_id, workspace_id=ctx.workspace_id
            ).get_by_id(command.user_sandbox_id)
            if record is None or record.deleted_at is not None:
                raise SandboxError("Original output storage unavailable")
            attachment = await manager.ensure_running(record.id)
            if attachment.user_sandbox_id != record.id:
                raise SandboxError("Original output storage changed")
            sandbox = attachment.sandbox
            if isinstance(sandbox, OpenSandbox):
                stack.push_async_callback(sandbox._sandbox.close)  # noqa: SLF001
        else:
            sandbox = await stack.enter_async_context(
                manager.connect_command_instance(
                    command_id=command.id, org_id=ctx.org_id, workspace_id=ctx.workspace_id
                )
            )
        if not isinstance(sandbox, OpenSandbox):
            raise SandboxError("Task output requires OpenSandbox")
        if not path.is_relative_to(sandbox.workdir) or ".." in path.parts:
            raise HTTPException(status_code=400, detail="Output path outside sandbox workdir")
        files = sandbox._sandbox.files  # noqa: SLF001
        try:
            if download:
                stream = await files.read_bytes_stream(command.log_path)

                async def output_chunks() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in stream:
                            yield chunk
                    finally:
                        await stack.aclose()

                response = StreamingResponse(
                    output_chunks(),
                    media_type="text/plain",
                    headers={
                        "Content-Disposition": content_disposition(path.name),
                        "Cache-Control": "no-store",
                    },
                    background=ResponseCleanup(stack.aclose),
                )
                # StreamingResponse closes the observer after consuming the provider stream.
                streaming = True
                return response
            info = (await files.get_file_info([command.log_path])).get(command.log_path)
            if info is not None and info.size > 1_048_576:
                raise HTTPException(status_code=413, detail="Log too large; download full output")
            return {"content": await files.read_file(command.log_path), "mime_type": "text/plain"}
        except ProviderApiError as exc:
            if exc.status_code == 404:
                raise HTTPException(status_code=404, detail="Task output not found") from exc
            raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Task output not found") from exc
    except (SandboxError, LookupError, ProviderSandboxError) as exc:
        raise HTTPException(status_code=503, detail="Original task sandbox unavailable") from exc
    finally:
        if not streaming:
            await stack.aclose()
