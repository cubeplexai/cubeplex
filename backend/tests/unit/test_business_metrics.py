"""Agent metrics keep labels bounded and leave tool results untouched."""

import asyncio
from unittest.mock import MagicMock

import pytest
from cubeloop.agent.types import AfterToolCallContext, AgentContext, AgentToolResult
from cubeloop.providers.base import AssistantMessage, TextContent, ToolCall
from prometheus_client import REGISTRY, generate_latest
from prometheus_fastapi_instrumentator.metrics import Info
from starlette.requests import Request
from starlette.responses import Response

from cubeplex.metrics import _record_http_latency, record_llm_call
from cubeplex.middleware.metrics import ToolMetricsMiddleware
from cubeplex.streams.run_manager import RunManager


@pytest.mark.asyncio
async def test_tool_metric_uses_fixed_category_without_changing_result() -> None:
    raw_name = "mcp__tenant_private_connector__search"
    result = AgentToolResult(content=[TextContent(text="found")], is_error=True)
    ctx = AfterToolCallContext(
        assistant_message=AssistantMessage(content=[]),
        tool_call=ToolCall(id="call-1", name=raw_name, arguments={}),
        args=MagicMock(),
        result=result,
        is_error=True,
        context=AgentContext(system_prompt="", messages=[]),
    )
    labels = {"category": "mcp", "outcome": "error"}
    before = REGISTRY.get_sample_value("cubeplex_tool_calls_total", labels) or 0

    contribution = await ToolMetricsMiddleware().after_tool_call(ctx)

    assert contribution is None
    assert ctx.result is result
    assert REGISTRY.get_sample_value("cubeplex_tool_calls_total", labels) == before + 1
    assert raw_name not in generate_latest(REGISTRY).decode()


def test_llm_token_metric_uses_only_fixed_directions() -> None:
    calls_before = (
        REGISTRY.get_sample_value("cubeplex_llm_calls_total", {"outcome": "success"}) or 0
    )
    reads_before = (
        REGISTRY.get_sample_value("cubeplex_llm_tokens_total", {"direction": "cache_read"}) or 0
    )

    record_llm_call("success", {"input_tokens": 5, "cache_read_tokens": 3})

    assert (
        REGISTRY.get_sample_value("cubeplex_llm_calls_total", {"outcome": "success"})
        == calls_before + 1
    )
    assert (
        REGISTRY.get_sample_value("cubeplex_llm_tokens_total", {"direction": "cache_read"})
        == reads_before + 3
    )


@pytest.mark.asyncio
async def test_run_attempt_gauge_returns_to_zero_after_task_finishes() -> None:
    manager = object.__new__(RunManager)
    manager._tasks = {}
    manager._cleanup_tasks = set()
    manager._tasks_empty = asyncio.Event()
    manager._metric_task_started_at = {}
    active_before = REGISTRY.get_sample_value("cubeplex_agent_run_attempts_active") or 0
    labels = {"trigger": "other", "phase": "start"}
    started_before = (
        REGISTRY.get_sample_value("cubeplex_agent_run_attempts_started_total", labels) or 0
    )

    async def completed() -> None:
        return None

    task = asyncio.create_task(completed())
    manager._tasks["run-1"] = task
    manager._observe_task(task, trigger="tenant-private", phase="start")
    assert REGISTRY.get_sample_value("cubeplex_agent_run_attempts_active") == active_before + 1

    await task
    manager._on_task_done("run-1", task)

    assert REGISTRY.get_sample_value("cubeplex_agent_run_attempts_active") == active_before
    assert (
        REGISTRY.get_sample_value("cubeplex_agent_run_attempts_started_total", labels)
        == started_before + 1
    )
    assert "tenant-private" not in generate_latest(REGISTRY).decode()


def test_http_latency_excludes_sse_stream_lifetime() -> None:
    labels = {"handler": "/stream", "method": "GET"}
    count_name = "cubeplex_http_request_duration_seconds_count"
    before = REGISTRY.get_sample_value(count_name, labels) or 0
    request = Request({"type": "http", "method": "GET", "path": "/stream", "headers": []})
    common = {
        "request": request,
        "method": "GET",
        "modified_handler": "/stream",
        "modified_status": "200",
        "modified_duration": 30.0,
    }

    _record_http_latency(
        Info(response=Response(headers={"content-type": "text/event-stream"}), **common)
    )
    assert (REGISTRY.get_sample_value(count_name, labels) or 0) == before

    _record_http_latency(
        Info(response=Response(headers={"content-type": "application/json"}), **common)
    )
    assert REGISTRY.get_sample_value(count_name, labels) == before + 1
