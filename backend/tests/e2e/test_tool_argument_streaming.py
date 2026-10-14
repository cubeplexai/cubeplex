"""Tool starts and partial arguments reach Redis before the model finishes."""

import asyncio
import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from cubeloop.providers.base import Model, StreamEvent
from cubeloop.providers.faux import (
    FauxProvider,
    faux_assistant_message,
    faux_text,
    faux_tool_call,
)

from cubeplex.streams.run_events import read_run_events_after
from tests.e2e.conftest import DEFAULT_WS_ID


async def test_tool_start_and_arguments_are_published_before_completion(
    memory_client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FauxProvider(token_size_min=1, token_size_max=1)
    provider.set_responses(
        [
            faux_assistant_message(
                faux_tool_call("calculator", {"expression": "1 + 1"}, id="streamed-call"),
                stop_reason="tool_use",
            ),
            faux_assistant_message(faux_text("2")),
        ]
    )
    release_start, release_args = asyncio.Event(), asyncio.Event()
    first_delta = True

    async def gate(event: StreamEvent, _model: Model) -> None:
        nonlocal first_delta
        if event.type == "toolcall_start":
            await release_start.wait()
        elif event.type == "toolcall_delta" and first_delta:
            first_delta = False
            await release_args.wait()

    provider.subscribe_chunk(gate)

    def build_provider(_snapshot: object, slug: str, **_kwargs: object) -> FauxProvider:
        provider.provider_id = slug
        return provider

    monkeypatch.setattr("cubeplex.llm.builder.build_provider", build_provider)
    created = await memory_client.post(
        f"/api/v1/ws/{DEFAULT_WS_ID}/conversations", params={"title": "tool streaming"}
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    path = f"/api/v1/ws/{DEFAULT_WS_ID}/conversations/{conversation_id}"
    app = memory_client._transport.app  # type: ignore[attr-defined]
    manager = app.state.run_manager
    cursor = "0-0"

    async def next_delta(run_id: str) -> dict[str, Any]:
        nonlocal cursor
        async with asyncio.timeout(20):
            while True:
                events = await read_run_events_after(
                    manager._redis,
                    prefix=manager._key_prefix,
                    run_id=run_id,
                    last_event_id=cursor,
                    block_ms=100,
                )
                for event in events:
                    cursor = event.event_id
                    assert event.payload["type"] not in {"error", "done", "tool_call"}
                    if event.payload["type"] == "tool_call_delta":
                        return event.payload["data"]

    try:
        started = await memory_client.post(
            f"{path}/messages",
            json={"client_message_id": str(uuid4()), "content": "Calculate 1 + 1."},
        )
        assert started.status_code == 200, started.text
        run_id = started.json()["run_id"]
        assert await next_delta(run_id) == {
            "tool_call_id": "streamed-call",
            "name": "calculator",
            "index": 0,
            "args_delta": "",
        }
        release_start.set()
        partial = await next_delta(run_id)
        assert partial["tool_call_id"] == "streamed-call"
        assert partial["args_delta"] == '{"ex'
        release_args.set()
        await manager.drain(timeout_seconds=20)

        replay = await memory_client.get(f"{path}/runs/{run_id}/stream")
        assert replay.status_code == 200, replay.text
        events = [
            json.loads(line.removeprefix("data: "))
            for line in replay.text.splitlines()
            if line.startswith("data: ")
        ]
        assert not [event for event in events if event["type"] == "error"]
        result = next(event["data"] for event in events if event["type"] == "tool_result")
        assert result["tool_call_id"] == "streamed-call"
        assert "Result: 2" in result["content"]
    finally:
        release_start.set()
        release_args.set()
        await manager.drain(timeout_seconds=20)
        await memory_client.delete(path)
