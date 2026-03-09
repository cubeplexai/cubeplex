"""Agent Execution API Routes

Provides REST API endpoints for executing DeepAgent tasks with streaming support.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import ValidationError

from cubebox.agents.executor import DeepAgentExecutor
from cubebox.agents.schemas import DoneEvent, ExecuteRequest
from cubebox.api.exceptions import ExecutionError, InternalError, InvalidInputError

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("/run", status_code=status.HTTP_200_OK)
async def run_agent(request: ExecuteRequest) -> StreamingResponse:
    """
    Execute an agent task with streaming response.

    Accepts a user input, executes it through DeepAgent, and returns
    a stream of Server-Sent Events (SSE) with execution events.

    Args:
        request: ExecuteRequest with user input

    Returns:
        StreamingResponse with SSE formatted event stream

    Raises:
        InvalidInputError: If request validation fails
        ExecutionError: If agent execution fails
        InternalError: For unexpected errors
    """
    logger.info("Agent execution request received: {}", request.input[:100])

    try:
        # Validate request
        if not request.input or not request.input.strip():
            raise InvalidInputError(
                message="Input field cannot be empty",
                details="Please provide a non-empty input string",
            )

        logger.debug("Request validated successfully")

    except ValidationError as e:
        logger.error("Request validation failed: {}", str(e))
        raise InvalidInputError(
            message="Invalid request format",
            details=str(e),
        ) from e

    async def event_generator() -> AsyncIterator[str]:
        """
        Generate SSE formatted events from agent execution.

        Yields:
            SSE formatted event strings
        """
        try:
            # Create executor with optional sandbox configuration
            logger.debug("Creating DeepAgentExecutor")
            executor = DeepAgentExecutor(
                sandbox_domain=request.sandbox_domain,
                sandbox_image=request.sandbox_image,
            )

            # Stream events from executor
            logger.debug("Starting agent stream")
            async for event in executor.stream(request.input):
                # Convert event to JSON and format as SSE
                event_json = event.model_dump_json()
                sse_event = f"data: {event_json}\n\n"
                logger.debug("Yielding event: {}", event.type)
                yield sse_event

        except InvalidInputError as e:
            logger.error("Invalid input error: {}", str(e))
            error_event = e.to_error_event()
            yield f"data: {error_event.model_dump_json()}\n\n"
            # Yield done event
            done_event = DoneEvent(
                timestamp=datetime.now(UTC).isoformat(),
            )
            yield f"data: {done_event.model_dump_json()}\n\n"

        except ExecutionError as e:
            logger.error("Execution error: {}", str(e))
            error_event = e.to_error_event()
            yield f"data: {error_event.model_dump_json()}\n\n"
            # Yield done event
            done_event = DoneEvent(
                timestamp=datetime.now(UTC).isoformat(),
            )
            yield f"data: {done_event.model_dump_json()}\n\n"

        except Exception as e:  # noqa: BLE001
            logger.error("Unexpected error during execution: {}", str(e), exc_info=True)
            error = InternalError(
                message="An unexpected error occurred during execution",
                details=str(e),
            )
            error_event = error.to_error_event()
            yield f"data: {error_event.model_dump_json()}\n\n"
            # Yield done event
            done_event = DoneEvent(
                timestamp=datetime.now(UTC).isoformat(),
            )
            yield f"data: {done_event.model_dump_json()}\n\n"

    logger.info("Returning streaming response")
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
