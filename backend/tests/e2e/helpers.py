import json
from collections.abc import AsyncIterator

from cubebox.agents.schemas import AgentEvent


def _parse_sse_event(event_str: str) -> AgentEvent | None:
    event_str = event_str.strip()
    if not event_str or not event_str.startswith("data: "):
        return None
    try:
        return AgentEvent(**json.loads(event_str[6:]))
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
