"""Public CubeLoop execution-session adapter for the CubePlex host runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol

from cubeloop.agent.types import AgentEvent
from cubeloop.session.events import (
    ExecutionEventEnvelope,
    ExecutionFinished,
    InputCommitted,
)
from cubeloop.session.types import ExecutionRequest, ExecutionResult
from loguru import logger

SESSION_EVENT_CAPACITY = 256
HOST_EVENT_QUEUE_CAPACITY = 64
MAX_EVENT_BYTES = 1024 * 1024
HOST_EVENT_ENQUEUE_TIMEOUT_SECONDS = 5.0
HOST_EVENT_PUBLISH_TIMEOUT_SECONDS = 5.0
SESSION_EVENT_DELIVERY_TIMEOUT_SECONDS = 6.0

EventConsumer = Callable[[AgentEvent], Awaitable[None] | None]
InputConsumer = Callable[[str], Awaitable[None] | None]
HostTerminalStatus = Literal["completed", "paused_hitl"]


class CubeloopAgentRunError(RuntimeError):
    """A settled CubeLoop attempt that CubePlex cannot classify as success."""


class EventProjectionError(RuntimeError):
    """A host event could not be forwarded within the projection contract."""


def _host_projection_error(result: ExecutionResult) -> EventProjectionError | None:
    for error in result.delivery_errors:
        if error.consumer == "cubeplex-runtime":
            return EventProjectionError(
                f"{error.consumer} delivery {error.reason} at sequence {error.seq}: {error.message}"
            )
    return None


def _json_size(value: Any) -> int:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode())


def ensure_event_fits(value: Any) -> None:
    """Reject one event before it can exceed the documented queue byte budget."""
    size = _json_size(value)
    if size > MAX_EVENT_BYTES:
        raise EventProjectionError(
            f"event is {size} bytes; maximum projected event size is {MAX_EVENT_BYTES}"
        )


async def enqueue_host_event(
    queue: asyncio.Queue[Any],
    item: tuple[str, Any, Any],
    *,
    timeout_seconds: float = HOST_EVENT_ENQUEUE_TIMEOUT_SECONDS,
) -> None:
    """Put an auxiliary host event into the bounded queue within a deadline."""
    ensure_event_fits(item[2])
    try:
        await asyncio.wait_for(queue.put(item), timeout=timeout_seconds)
    except TimeoutError as exc:
        raise EventProjectionError("timed out enqueueing a host event") from exc


class ExecutionSessionProtocol(Protocol):
    def subscribe(
        self,
        listener: Callable[[ExecutionEventEnvelope], object],
        *,
        required: bool = False,
        name: str | None = None,
        capacity: int = SESSION_EVENT_CAPACITY,
        delivery_timeout: float = 1.0,
    ) -> Callable[[], None]: ...

    async def execute(self, request: ExecutionRequest) -> ExecutionResult: ...


async def execute_session(
    *,
    session: ExecutionSessionProtocol,
    request: ExecutionRequest,
    on_agent_event: EventConsumer,
    on_checkpoint_input: InputConsumer | None = None,
) -> ExecutionResult:
    """Run one attempt while projecting public Session events to the host."""

    async def consume(envelope: ExecutionEventEnvelope) -> None:
        event = envelope.event
        if isinstance(event, ExecutionFinished):
            return
        if isinstance(event, InputCommitted):
            if event.durability != "checkpoint" or on_checkpoint_input is None:
                return
            value = on_checkpoint_input(event.input_id)
        else:
            value = on_agent_event(event)
        if inspect.isawaitable(value):
            try:
                await asyncio.wait_for(value, timeout=HOST_EVENT_PUBLISH_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise EventProjectionError("timed out publishing a host event") from exc

    unsubscribe = session.subscribe(
        consume,
        required=True,
        name="cubeplex-runtime",
        capacity=SESSION_EVENT_CAPACITY,
        delivery_timeout=SESSION_EVENT_DELIVERY_TIMEOUT_SECONDS,
    )
    try:
        return await session.execute(request)
    finally:
        unsubscribe()


def require_host_success(
    result: ExecutionResult,
    *,
    answered_question_id: str | None = None,
) -> HostTerminalStatus:
    """Map explicit Session facts to CubePlex's successful terminal states."""
    projection_error = _host_projection_error(result)
    if result.outcome == "completed":
        if projection_error is not None:
            raise projection_error
        if not result.checkpoint_committed:
            raise CubeloopAgentRunError("completed execution was not durably checkpointed")
        return "completed"
    if result.outcome == "suspended":
        if not result.checkpoint_committed or result.pending_request is None:
            raise CubeloopAgentRunError("suspended execution was not durably checkpointed")
        if (
            answered_question_id is not None
            and result.pending_request.question_id == answered_question_id
        ):
            if projection_error is not None:
                raise projection_error
            return "completed"
        if projection_error is not None:
            logger.warning(
                "preserving durable suspension after host projection failure: {}",
                projection_error,
            )
        return "paused_hitl"
    if projection_error is not None:
        raise projection_error
    if result.outcome == "cancelled":
        raise asyncio.CancelledError("execution cancelled")
    if result.error is not None and result.error.cause is not None:
        raise result.error.cause
    message = result.error.message if result.error is not None else result.outcome
    raise CubeloopAgentRunError(message)
