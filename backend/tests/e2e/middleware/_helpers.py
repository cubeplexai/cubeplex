"""Shared scaffolding for the agent-middleware-coverage E2E suite.

Constants and tiny helpers consumed by test_journey.py, test_subagent.py,
and test_compaction.py. See
docs/dev/specs/2026-05-15-agent-middleware-e2e-design.md.
"""

from __future__ import annotations

from typing import Any

import httpx

from tests.e2e.conftest import collect_sse_events

# --- Tool names (verified against middleware source in Task 2) ---------------
# cubeplex/tools/registry.py — built-in tool
TOOL_CALCULATOR = "calculator"
# cubeplex/middleware/todo.py:418
TOOL_TODO = "write_todos"
# MemoryMiddleware itself is prompt-injection only (transform_system_prompt /
# transform_context). The memory *tools* live separately in
# cubeplex/tools/builtin/memory.py and are registered into every cubepi run's
# tool list via run_manager.py (lines 594-600). Three tools are exposed:
TOOL_MEMORY_SAVE = "memory_save"
TOOL_MEMORY_SEARCH = "memory_search"
TOOL_MEMORY_UPDATE = "memory_update"
# cubeplex/middleware/sandbox.py:154 — primary code-execution tool
TOOL_SANDBOX = "execute"
# cubeplex/middleware/subagents.py:256
TOOL_SUBAGENT_SPAWN = "subagent"

# --- SSE event types (verified against cubeplex/streams/run_manager.py) -------
EVT_TEXT_DELTA = "text_delta"
EVT_TOOL_CALL = "tool_call"
EVT_TOOL_RESULT = "tool_result"
EVT_USAGE = "usage"
EVT_ERROR = "error"
EVT_DONE = "done"

# --- Compaction note (informational; no test asserts on this) ----------------
# Trigger: approx_tokens(compressed_history) >= context_window * threshold_ratio
# Defaults: fallback_context_window=64000, threshold_ratio=0.7 → ~44800 tokens.
# Both values are overridable via config keys "compaction.fallback_context_window"
# and "compaction.threshold_ratio" (cubeplex/streams/run_manager.py lines 770-771).
COMPACTION_NOTE = (
    "Compact when approx_tokens(compressed_history) >= context_window * 0.7 "
    "(default fallback_context_window=64000, giving ~44800 token threshold)"
)


# ---------------------------------------------------------------------------
# Event filtering helpers
# ---------------------------------------------------------------------------


def events_of_type(events: list[dict[str, Any]], type_name: str) -> list[dict[str, Any]]:
    """Return all events whose 'type' field equals type_name."""
    return [e for e in events if e.get("type") == type_name]


def tool_call_names(events: list[dict[str, Any]]) -> list[str]:
    """Return the tool name from every tool_call event in the stream.

    The cubeplex SSE envelope nests tool identity under a 'data' sub-dict:
      {'type': 'tool_call', 'data': {'name': 'calculator', ...}, ...}
    Falls back to top-level 'name' for forward-compatibility.
    """
    names: list[str] = []
    for e in events_of_type(events, EVT_TOOL_CALL):
        data = e.get("data") or {}
        n = data.get("name") or e.get("name")
        if isinstance(n, str):
            names.append(n)
    return names


def _flatten_content(evt: dict[str, Any]) -> str:
    """Extract plain text from a tool_result/text_delta event.

    The SSE envelope nests content under 'data.content' (or 'data.result'
    for tool_result). Handles structured list-of-blocks shapes returned by
    some providers. Mirrors the helper in test_cubepi_path_tools.py.
    """

    def _from_value(c: object) -> str | None:
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts: list[str] = []
            for block in c:
                if isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
                elif isinstance(block, str):
                    parts.append(block)
            return "".join(parts)
        return None

    data = evt.get("data")
    if isinstance(data, dict):
        v = _from_value(data.get("content"))
        if v is not None:
            return v
        v = _from_value(data.get("result"))
        if v is not None:
            return v
    v = _from_value(evt.get("content"))
    if v is not None:
        return v
    v = _from_value(evt.get("result"))
    if v is not None:
        return v
    return ""


def tool_result_contents(events: list[dict[str, Any]]) -> list[str]:
    """Return flattened content strings for every tool_result event."""
    return [_flatten_content(e) for e in events_of_type(events, EVT_TOOL_RESULT)]


def assistant_text(events: list[dict[str, Any]]) -> str:
    """Concatenate the content of every text_delta event into the full reply."""
    return "".join(_flatten_content(e) for e in events_of_type(events, EVT_TEXT_DELTA))


# ---------------------------------------------------------------------------
# HTTP helpers — mirror test_cubepi_path_tools.py exactly
# ---------------------------------------------------------------------------


async def create_conversation(client: httpx.AsyncClient, ws_id: str, title: str) -> str:
    """Create a conversation and return its id.

    Body shape mirrors test_cubepi_path_tools.py: title is a query param,
    not a JSON body field (matches POST /api/v1/ws/{ws_id}/conversations).
    """
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations",
        params={"title": title},
    )
    assert resp.status_code == 201, f"conversation creation failed: {resp.text}"
    return str(resp.json()["id"])


async def post_turn(
    client: httpx.AsyncClient,
    ws_id: str,
    conv_id: str,
    content: str,
) -> list[dict[str, Any]]:
    """POST a user message and collect all SSE events.

    Mirrors the streaming pattern in test_cubepi_path_tools.py:
      collect_sse_events(client, url, json_data={"content": ...})
    which opens client.stream("POST", url, json=...) and parses
    each "data: ..." line as JSON.
    """
    return await collect_sse_events(
        client,
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json_data={"content": content},
    )
