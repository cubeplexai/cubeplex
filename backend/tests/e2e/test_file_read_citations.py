"""Citation pipeline integration test for file_read tool results.

Drives ``CitationMiddleware.awrap_tool_call`` directly with a synthesised
file_read ToolMessage instead of relying on an LLM to call the tool. The
SUT here is the citation pipeline (config loading from tool metadata,
discriminator filtering, chunking, event emission) — not the LLM's
tool-use behavior, so the test is deterministic and runs cleanly in CI.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from cubebox.middleware.citations.config import load_builtin_citation_configs
from cubebox.middleware.citations.counter import (
    CitationCounter,
    citation_counter_var,
    citation_event_queue,
)
from cubebox.middleware.citations.middleware import CitationMiddleware
from cubebox.middleware.sandbox import _create_file_read_tool

pytestmark = pytest.mark.asyncio


def _build_tool_call_request(
    *, tool_call_id: str, tool_name: str, args: dict[str, Any]
) -> ToolCallRequest:
    """Construct a minimal ToolCallRequest for middleware testing."""
    return ToolCallRequest(
        tool_call={"name": tool_name, "args": args, "id": tool_call_id},
        tool=None,
        state=None,
        runtime=MagicMock(),
    )


async def _emit(
    middleware: CitationMiddleware,
    *,
    tool_name: str,
    args: dict[str, Any],
    raw_result: dict[str, Any],
) -> tuple[ToolMessage, list[Any]]:
    """Run the middleware against a synthesised tool result; return (msg, events)."""
    request = _build_tool_call_request(
        tool_call_id="call-test-1",
        tool_name=tool_name,
        args=args,
    )

    async def handler(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(raw_result),
            tool_call_id="call-test-1",
            name=tool_name,
        )

    out = await middleware.awrap_tool_call(request, handler)
    assert isinstance(out, ToolMessage)

    queue = middleware._event_queue  # noqa: SLF001 — direct access for drain
    assert queue is not None
    events: list[Any] = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return out, events


@pytest.fixture
def file_read_middleware() -> tuple[CitationMiddleware, asyncio.Queue[Any]]:
    """Build a CitationMiddleware wired to the real file_read tool config."""
    # The sandbox / conversation_id arguments are unused by the citation
    # pipeline — only the tool's metadata['citation'] block matters here.
    file_read_tool = _create_file_read_tool(sandbox=MagicMock(), conversation_id=None)
    configs = load_builtin_citation_configs([file_read_tool])
    assert "file_read" in configs, (
        "file_read tool metadata is missing the citation config; "
        "check Task 3 in cubebox/middleware/sandbox.py"
    )
    queue: asyncio.Queue[Any] = asyncio.Queue()
    middleware = CitationMiddleware(citation_configs=configs, event_queue=queue)
    return middleware, queue


async def test_file_read_text_emits_file_source_citation(
    file_read_middleware: tuple[CitationMiddleware, asyncio.Queue[Any]],
) -> None:
    """A kind='text' file_read result emits a citation with source_type='file'.

    The metadata fields (path, mime, size_bytes, truncated) and any range
    args (page_range/line_range) are mapped according to the tool's
    citation config; chunks contain the file content split into segments.
    """
    middleware, _queue = file_read_middleware
    citation_counter_var.set(CitationCounter(start=1))

    raw_result = {
        "kind": "text",
        "path": "/workspace/uploads/conv-x/file-y/fact.md",
        "mime": "text/markdown",
        "content": "The capital of France is Paris. " * 20,
        "size_bytes": 600,
        "truncated": False,
    }

    _msg, events = await _emit(
        middleware,
        tool_name="file_read",
        args={"path": "/workspace/uploads/conv-x/file-y/fact.md"},
        raw_result=raw_result,
    )

    citation_events = [e for e in events if e[0] == "citation"]
    assert citation_events, f"no citation events emitted, got: {events}"
    payload = citation_events[0][2]

    assert payload["metadata"]["source_type"] == "file"
    assert payload["metadata"]["path"].endswith("fact.md")
    assert payload["metadata"]["mime"] == "text/markdown"
    assert payload["metadata"]["truncated"] is False
    assert payload["chunks"], "citation has no chunks"
    assert any("Paris" in c["content"] for c in payload["chunks"])


async def test_file_read_unsupported_emits_no_citation(
    file_read_middleware: tuple[CitationMiddleware, asyncio.Queue[Any]],
) -> None:
    """A kind='unsupported' result is filtered by the discriminator and
    produces no citation event."""
    middleware, _queue = file_read_middleware
    citation_counter_var.set(CitationCounter(start=1))

    raw_result = {
        "kind": "unsupported",
        "path": "/workspace/uploads/conv-x/file-y/opaque.bin",
        "mime": "application/octet-stream",
        "size_bytes": 4,
        "reason": "no parser plugin registered for this MIME",
    }

    _msg, events = await _emit(
        middleware,
        tool_name="file_read",
        args={"path": "/workspace/uploads/conv-x/file-y/opaque.bin"},
        raw_result=raw_result,
    )

    citation_events = [e for e in events if e[0] == "citation"]
    assert citation_events == [], (
        f"unexpected citation events for unsupported result: {citation_events}"
    )


async def test_file_read_carries_range_args_into_metadata(
    file_read_middleware: tuple[CitationMiddleware, asyncio.Queue[Any]],
) -> None:
    """page_range / line_range from the tool call args land in citation metadata
    via args_mapping (so the popover can show 'Pages 1-5' badges)."""
    middleware, _queue = file_read_middleware
    citation_counter_var.set(CitationCounter(start=1))

    raw_result = {
        "kind": "text",
        "path": "/workspace/uploads/conv-x/file-y/notes.md",
        "mime": "text/markdown",
        "content": "Lines from the requested range.",
        "size_bytes": 30,
        "truncated": False,
    }

    _msg, events = await _emit(
        middleware,
        tool_name="file_read",
        args={
            "path": "/workspace/uploads/conv-x/file-y/notes.md",
            "line_range": "100-150",
        },
        raw_result=raw_result,
    )

    citation_events = [e for e in events if e[0] == "citation"]
    assert citation_events
    metadata = citation_events[0][2]["metadata"]
    assert metadata.get("line_range") == "100-150"


async def test_publishes_to_contextvar_queue_when_no_explicit_queue(
    file_read_middleware: tuple[CitationMiddleware, asyncio.Queue[Any]],
) -> None:
    """When the middleware is constructed without an explicit queue, it should
    publish via the citation_event_queue ContextVar instead — this is the path
    used during real agent runs (run_manager sets the ContextVar)."""
    middleware, _queue = file_read_middleware
    middleware._event_queue = None  # noqa: SLF001 — exercise ContextVar fallback
    citation_counter_var.set(CitationCounter(start=1))
    fallback_queue: asyncio.Queue[Any] = asyncio.Queue()
    citation_event_queue.set(fallback_queue)

    raw_result = {
        "kind": "text",
        "path": "/workspace/uploads/conv-x/file-y/note.md",
        "mime": "text/markdown",
        "content": "Hello world.",
        "size_bytes": 12,
        "truncated": False,
    }

    request = _build_tool_call_request(
        tool_call_id="call-cv-1",
        tool_name="file_read",
        args={"path": raw_result["path"]},
    )

    async def handler(_req: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=json.dumps(raw_result),
            tool_call_id="call-cv-1",
            name="file_read",
        )

    await middleware.awrap_tool_call(request, handler)

    events: list[Any] = []
    while not fallback_queue.empty():
        events.append(fallback_queue.get_nowait())
    citation_events = [e for e in events if e[0] == "citation"]
    assert citation_events, f"ContextVar queue did not receive citation: {events}"
