"""Public conversation background-task API contracts."""

from typing import Literal

from pydantic import BaseModel


class BackgroundTaskCapabilities(BaseModel):
    can_stop: bool
    remote_cancel_supported: bool
    reconnect_supported: bool
    logs_supported: bool
    input_supported: bool


class BackgroundTaskNotification(BaseModel):
    enabled: bool
    has_pending: bool
    cancelled_at: str | None


class CommandTaskDetails(BaseModel):
    type: Literal["command"] = "command"
    command_id: str
    command_kind: str
    command: str
    status: str
    exit_code: int | None
    log_path: str
    log_state: str
    monitor_outcome: str | None


class BackgroundTaskOut(BaseModel):
    id: str
    kind: str
    description: str
    parent_task_id: str | None
    originating_run_id: str
    tool_call_id: str
    agent_id: str | None
    execution_generation: int
    state: str
    deadline_at: str | None
    stop_requested_at: str | None
    stop_reason: str | None
    backgrounded_at: str | None
    finished_at: str | None
    result_summary: str
    result_ref: str | None
    result_readiness: str
    result_unavailable_reason: str | None
    revision: int
    created_at: str
    updated_at: str
    cleanup_pending: bool
    capabilities: BackgroundTaskCapabilities
    notification: BackgroundTaskNotification
    details: CommandTaskDetails | None


class BackgroundTaskListResponse(BaseModel):
    items: list[BackgroundTaskOut]


class StopBackgroundTaskResponse(BaseModel):
    accepted: bool
    cleanup_pending: bool
    remote_cancel_supported: bool
    task: BackgroundTaskOut


class BackgroundTaskEventOut(BaseModel):
    id: str
    task_id: str
    task_kind: str
    execution_generation: int
    reason: str
    summary: str
    result_ref: str | None
    state: str
    discard_reason: str | None
    revision: int
    created_at: str
    updated_at: str
    delivered_at: str | None


class BackgroundTaskEventPageResponse(BaseModel):
    items: list[BackgroundTaskEventOut]
    next_cursor: str | None
    has_more: bool


class BackgroundTaskSummaryOut(BaseModel):
    has_inflight: bool
    has_pending: bool
    has_cleanup: bool
    can_stop: bool
