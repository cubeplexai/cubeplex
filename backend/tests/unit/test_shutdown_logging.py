"""Expected transport shutdown cancellation must not hide other ASGI failures."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from loguru import logger
from starlette.types import Message, Receive, Scope, Send

from cubeplex.api.middleware.cancellation import CancellationMiddleware
from cubeplex.utils.log import InterceptHandler

if TYPE_CHECKING:
    from loguru import Message as LogMessage
    from loguru import Record

_SHUTDOWN_REASON = "Task cancelled, timeout graceful shutdown exceeded"


@pytest.fixture
def log_records() -> Iterator[list[Record]]:
    records: list[Record] = []

    def capture(message: LogMessage) -> None:
        records.append(message.record)

    handler_id = logger.add(capture, level="DEBUG")
    try:
        yield records
    finally:
        logger.remove(handler_id)


def _emit_error(
    exception: BaseException | None,
    *,
    name: str = "uvicorn.error",
    message: str = "Exception in ASGI application\n",
) -> None:
    source = logging.Logger(name, level=logging.DEBUG)
    source.addHandler(InterceptHandler())
    if exception is None:
        source.error(message)
    else:
        try:
            raise exception
        except BaseException:
            source.error(message, exc_info=True)


def test_transport_shutdown_cancellation_is_info_without_traceback(
    log_records: list[Record],
) -> None:
    _emit_error(asyncio.CancelledError(_SHUTDOWN_REASON))

    record = log_records[-1]
    assert record["level"].name == "INFO"
    assert record["message"] == "ASGI request cancelled after graceful shutdown timeout"
    assert record["exception"] is None


@pytest.mark.parametrize(
    ("exception", "name", "message"),
    [
        (asyncio.CancelledError(), "uvicorn.error", "Exception in ASGI application\n"),
        (
            asyncio.CancelledError("client disconnected"),
            "uvicorn.error",
            "Exception in ASGI application\n",
        ),
        (RuntimeError(_SHUTDOWN_REASON), "uvicorn.error", "Exception in ASGI application\n"),
        (ValueError("cleanup failed"), "uvicorn.error", "Exception in ASGI application\n"),
        (
            BaseExceptionGroup(
                "mixed shutdown failure",
                [asyncio.CancelledError(_SHUTDOWN_REASON), RuntimeError("cleanup failed")],
            ),
            "uvicorn.error",
            "Exception in ASGI application\n",
        ),
        (
            asyncio.CancelledError(_SHUTDOWN_REASON),
            "cubeplex.shutdown",
            "Exception in ASGI application\n",
        ),
        (asyncio.CancelledError(_SHUTDOWN_REASON), "uvicorn.error", "Application shutdown failed"),
        (None, "uvicorn.error", _SHUTDOWN_REASON),
        (None, "uvicorn.error", "Cancel 1 running task(s), timeout graceful shutdown exceeded"),
    ],
)
def test_other_errors_keep_their_level_and_exception(
    log_records: list[Record],
    exception: BaseException | None,
    name: str,
    message: str,
) -> None:
    _emit_error(exception, name=name, message=message)

    record = log_records[-1]
    assert record["level"].name == "ERROR"
    assert record["message"] == message
    if exception is None:
        assert record["exception"] is None
    else:
        assert record["exception"] is not None
        assert record["exception"].value is exception
        assert record["exception"].traceback is not None


@pytest.mark.parametrize("reason", [None, "client disconnected", _SHUTDOWN_REASON])
async def test_middleware_reraises_cancellation_without_assuming_client_disconnect(
    log_records: list[Record], reason: str | None
) -> None:
    cancellation = asyncio.CancelledError() if reason is None else asyncio.CancelledError(reason)

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise cancellation

    async def receive() -> Message:
        raise AssertionError("Cancellation must not consume request messages")

    async def send(message: Message) -> None:
        raise AssertionError("Cancellation must not fabricate a response")

    middleware = CancellationMiddleware(downstream)
    with pytest.raises(asyncio.CancelledError) as caught:
        await middleware({"type": "http", "method": "GET", "path": "/events"}, receive, send)

    assert caught.value is cancellation
    assert log_records[-1]["message"] == "Request cancelled: GET /events"
