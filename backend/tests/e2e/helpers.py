import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable

from cubebox.agents.schemas import AgentEvent


async def await_until[T](
    predicate: Callable[[], Awaitable[T] | T],
    *,
    timeout: float = 5.0,
    interval: float = 0.05,
    message: str = "predicate did not become truthy",
) -> T:
    """Poll ``predicate`` until it returns a truthy value or ``timeout`` elapses.

    Use this instead of ``await asyncio.sleep(X)`` whenever a test is waiting
    on a fire-and-forget side effect (background DB write, SSE subscriber
    registration, debounced flush) — the wall-clock cost stays low when the
    side effect is fast and the test fails loudly with ``message`` instead of
    silently moving on past a still-pending state.

    ``predicate`` may be sync or async. The first truthy return value is
    returned to the caller so it can be used directly (e.g. a row count or a
    populated dict).
    """
    deadline = asyncio.get_event_loop().time() + timeout
    last: T | None = None
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result  # type: ignore[return-value]
        last = result  # type: ignore[assignment]
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(f"{message} (last value: {last!r}, waited {timeout:.1f}s)")
        await asyncio.sleep(interval)


def _parse_sse_event(event_str: str) -> AgentEvent | None:
    event_str = event_str.strip()
    if not event_str:
        return None
    data_lines = [line[6:] for line in event_str.splitlines() if line.startswith("data: ")]
    if not data_lines:
        return None
    try:
        return AgentEvent(**json.loads("\n".join(data_lines)))
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Failed to parse SSE event: {event_str}") from e


def parse_sse_events(response_text: str) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    for event_str in response_text.split("\n\n"):
        event = _parse_sse_event(event_str)
        if event is not None:
            events.append(event)
    return events


async def parse_sse_stream(stream: AsyncIterator[bytes]) -> list[AgentEvent]:
    events: list[AgentEvent] = []
    buffer = ""
    async for chunk in stream:
        buffer += chunk.decode("utf-8")
        while "\n\n" in buffer:
            event_str, buffer = buffer.split("\n\n", 1)
            event = _parse_sse_event(event_str)
            if event is not None:
                events.append(event)
    return events


def assert_event_contains(event: AgentEvent, expected_keys: list[str]) -> None:
    for key in expected_keys:
        assert key in event.data, (
            f"Expected key '{key}' in event data, got keys: {list(event.data.keys())}"
        )


def csrf_cookie_name() -> str:
    """Return the CSRF cookie name from config.

    Handles per-worktree env overrides, so works across both CI (no suffix)
    and worktree environments (slot-specific suffix).
    """
    from cubebox.config import config as _cubebox_config

    return str(_cubebox_config.get("auth.csrf_cookie_name", "cubebox_csrf"))
