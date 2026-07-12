# cubepi Migration M1 — Agent Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the cubepi-runtime agent core skeleton (`agents/graph_pi.py` + `stream_pi.py` + `convert_pi.py` + `llm/cache_markers_pi.py`) and wire `streams/run_manager.py` to dispatch by `config.agents.runtime`. After M1, the cubepi-runtime path can serve a real LLM-backed conversation end-to-end through cubeplex's existing API, with messages persisted via cubepi.PostgresCheckpointer (already wired in M0.5) and prompt-cache markers placed correctly. **No cubeplex middleware ported in M1** (that's M3) — the cubepi agent runs without `MemoryMiddleware`/`SandboxMiddleware`/etc., so its behavior is narrower than the langgraph path. Subsequent milestones (M2 tools, M3 middleware) backfill.

**Architecture:** All new code lives in `*_pi.py` files alongside the existing langgraph implementations. `run_manager.py` chooses the path via `config.agents.runtime`; CI test config sets `cubepi` so the cubepi path is exercised. The langgraph path remains the dev default until M6.

**Tech Stack:** cubepi 0.3 (path dep), Pydantic 2, FastAPI/SSE, pytest + pytest-asyncio, existing cubeplex test fixtures.

**Spec:** `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` § M1.

**Baseline (must hold before starting):** All M0 commits landed; `uv run pytest tests/unit -q` shows 452 passing; `uv run alembic current` shows the cubepi-tables migration applied.

**Dependencies on cubepi:** All Spec A items (PR #65) must be in `~/cubepi` working tree. M1 consumes: `cubepi.Provider`, `cubepi.Agent`, `cubepi.Model`, `cubepi.providers.base.{Message, UserMessage, AssistantMessage, ToolResultMessage, TextContent, ThinkingContent, ToolCall}`, `cubepi.providers.base.StreamEvent`, `cubepi.providers.anthropic.{CacheMarkerPolicy, DefaultCacheMarkerPolicy}`, `cubepi.checkpointer.postgres.PostgresCheckpointer`, `cubeplex.agents.checkpointer_pi.init_cubepi_checkpointer` (M0.5).

---

## File Map

### Files to create

| File | Purpose |
|---|---|
| `backend/cubeplex/llm/cache_markers_pi.py` | `CubeplexCacheMarkerPolicy` implementing `cubepi.CacheMarkerPolicy` — walks back to last completed AIMessage |
| `backend/cubeplex/agents/convert_pi.py` | `cubepi.Message ↔ cubeplex wire format`; also `wire_input_to_cubepi_user_message` for inbound API messages |
| `backend/cubeplex/agents/stream_pi.py` | `convert_cubepi_event_to_sse(cubepi_event) -> list[sse_event_dict]` |
| `backend/cubeplex/agents/graph_pi.py` | `create_cubeplex_cubepi_agent(...)` — builds bare cubepi.Agent without cubeplex middleware (M3 backfills middleware) |
| `backend/tests/unit/test_cache_markers_pi.py` | Tests for cubeplex cache marker policy |
| `backend/tests/unit/test_convert_pi.py` | cubepi.Message ↔ wire format round-trip tests |
| `backend/tests/unit/test_stream_pi.py` | Event translation tests for each cubepi event type |
| `backend/tests/e2e/test_cubepi_path_conversation.py` | End-to-end: send a message through API with `agents.runtime=cubepi`, verify SSE event sequence |

### Files to modify

| File | What changes |
|---|---|
| `backend/cubeplex/streams/run_manager.py` | Dispatch by `config.agents.runtime`; cubepi path uses `create_cubeplex_cubepi_agent` + cubepi.PostgresCheckpointer + Provider via `LLMFactory.build_cubepi_provider` |

---

## Pre-flight

### Task M1.0: Verify M0 + cubepi PR baseline

- [ ] **Step 1: cubepi branch has PR #65 fixes**

```bash
cd /home/chris/cubepi && git log --oneline feat/cubeplex-readiness | head -8
```
Expected: includes `ec0653b` (loop state fix), `804cb93` (MCP fix), etc.

- [ ] **Step 2: cubeplex baseline**

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend && uv run pytest tests/unit -q --tb=no
```
Expected: 452 passing.

- [ ] **Step 3: alembic at head**

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend && uv run alembic current
```
Expected: `555c11215b57 (head)` — cubepi tables migration applied.

---

## Task M1.1: CubeplexCacheMarkerPolicy

cubeplex's prompt cache discipline marks system + last completed AIMessage. cubepi's `DefaultCacheMarkerPolicy` marks last message (regardless of role). Implement cubeplex's policy.

**Files:**
- Create: `backend/cubeplex/llm/cache_markers_pi.py`
- Test: `backend/tests/unit/test_cache_markers_pi.py`

### Step 1: Write failing tests

```python
"""CubeplexCacheMarkerPolicy tests (M1.1)."""

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from cubeplex.llm.cache_markers_pi import CubeplexCacheMarkerPolicy


def _user(text: str) -> UserMessage:
    return UserMessage(content=[TextContent(text=text)])


def _assistant(text: str = "ok", tool_calls: list[ToolCall] | None = None) -> AssistantMessage:
    content: list = [TextContent(text=text)]
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(content=content, usage=Usage())


def _tool_result(tool_call_id: str, text: str) -> ToolResultMessage:
    return ToolResultMessage(
        content=[TextContent(text=text)],
        tool_call_id=tool_call_id,
        tool_name="t",
    )


def test_policy_marks_system_and_tools() -> None:
    p = CubeplexCacheMarkerPolicy()
    assert p.mark_system() is True
    assert p.mark_last_tool() is True


def test_indices_empty_list() -> None:
    p = CubeplexCacheMarkerPolicy()
    assert p.message_breakpoint_indices([]) == []


def test_indices_only_user_message_no_assistant_yet() -> None:
    """First turn before model responds: no completed AIMessage → no breakpoint."""
    p = CubeplexCacheMarkerPolicy()
    msgs: list[Message] = [_user("hi")]
    assert p.message_breakpoint_indices(msgs) == []


def test_indices_picks_last_assistant() -> None:
    """[user, assistant, user] → mark index 1 (the assistant)."""
    p = CubeplexCacheMarkerPolicy()
    msgs: list[Message] = [_user("a"), _assistant("b"), _user("c")]
    assert p.message_breakpoint_indices(msgs) == [1]


def test_indices_picks_most_recent_assistant() -> None:
    """[user, assistant, user, assistant, user] → mark index 3."""
    p = CubeplexCacheMarkerPolicy()
    msgs: list[Message] = [
        _user("a"),
        _assistant("b"),
        _user("c"),
        _assistant("d"),
        _user("e"),
    ]
    assert p.message_breakpoint_indices(msgs) == [3]


def test_indices_skips_user_and_tool_result() -> None:
    """[user, assistant(tool_call), tool_result, assistant, user] → mark index 3."""
    p = CubeplexCacheMarkerPolicy()
    tc = ToolCall(id="tc1", name="t", arguments={})
    msgs: list[Message] = [
        _user("a"),
        _assistant("calling tool", tool_calls=[tc]),
        _tool_result("tc1", "result"),
        _assistant("done"),
        _user("next"),
    ]
    assert p.message_breakpoint_indices(msgs) == [3]
```

### Step 2: Run failing tests

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend
uv run pytest tests/unit/test_cache_markers_pi.py -v
```
Expected: 6 failures (module doesn't exist).

### Step 3: Implement

```python
# backend/cubeplex/llm/cache_markers_pi.py
"""cubeplex-side CacheMarkerPolicy implementation for cubepi.AnthropicProvider.

Walks back through the message list to find the most recent completed
AssistantMessage and marks it. The system prompt and last tool definition
also get markers (cubeplex's prompt cache discipline; see backend/CLAUDE.md).
"""
from __future__ import annotations

from cubepi.providers.base import AssistantMessage, Message


class CubeplexCacheMarkerPolicy:
    """Policy: mark system + last completed AssistantMessage + last tool.

    "Completed" here means: any AssistantMessage in the messages list.
    cubeplex builds the request after the assistant has finished streaming,
    so every AssistantMessage in the list is by definition completed.
    """

    def mark_system(self) -> bool:
        return True

    def mark_last_tool(self) -> bool:
        return True

    def message_breakpoint_indices(self, messages: list[Message]) -> list[int]:
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], AssistantMessage):
                return [i]
        return []
```

### Step 4: Run tests + full suite

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend
uv run pytest tests/unit/test_cache_markers_pi.py -v   # 6 pass
uv run pytest tests/unit -q --tb=no                    # 458 pass (452 + 6)
```

### Step 5: Commit

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi
git add backend/cubeplex/llm/cache_markers_pi.py backend/tests/unit/test_cache_markers_pi.py
git commit -m "feat(llm): add CubeplexCacheMarkerPolicy for cubepi.AnthropicProvider (M1.1)

Walks back to the last completed AssistantMessage; marks system + last
tool too. Mirrors the existing langgraph cubeplex/llm/cache_markers.py
discipline (see backend/CLAUDE.md 'Prompt Cache Discipline')."
```

---

## Task M1.2: convert_pi.py

cubepi messages need conversion to/from cubeplex's API wire format. Inbound: HTTP request body's user message text → `cubepi.UserMessage`. Outbound: `cubepi.AssistantMessage` (and other types) → response dict for `agent.state.messages` API surface.

**Files:**
- Create: `backend/cubeplex/agents/convert_pi.py`
- Test: `backend/tests/unit/test_convert_pi.py`

### Discovery

Read the existing `backend/cubeplex/agents/convert.py` (the LangChain version) to understand what wire format cubeplex uses. Key functions you'll need to mirror:

```bash
grep -n "^def \|render_attachments_hint\|format_message_for_api" /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend/cubeplex/agents/convert.py | head
```

The wire format (returned to API consumers) is typically:
```python
{
    "id": "msg-...",        # cubeplex public ID
    "role": "user" | "assistant" | "tool",
    "content": "...",        # rendered text OR list of content blocks
    "metadata": {...},
    "created_at": "iso-string",
}
```

Verify by reading `convert.py` directly.

### Step 1: Write tests

```python
"""convert_pi tests — cubepi.Message ↔ cubeplex wire format (M1.2)."""

import pytest
from cubepi.providers.base import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
)

from cubeplex.agents.convert_pi import (
    cubepi_message_to_wire,
    wire_input_to_cubepi_user_message,
)


def test_user_message_to_wire_text_only() -> None:
    msg = UserMessage(content=[TextContent(text="hello")])
    out = cubepi_message_to_wire(msg)
    assert out["role"] == "user"
    assert out["content"] == "hello"


def test_assistant_message_to_wire_text_only() -> None:
    msg = AssistantMessage(content=[TextContent(text="hi back")], usage=Usage())
    out = cubepi_message_to_wire(msg)
    assert out["role"] == "assistant"
    assert out["content"] == "hi back"


def test_assistant_message_to_wire_with_tool_call() -> None:
    """tool_calls land in metadata.tool_calls as cubeplex-shaped dicts."""
    tc = ToolCall(id="tc1", name="search", arguments={"q": "x"})
    msg = AssistantMessage(
        content=[TextContent(text="calling tool"), tc],
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    out = cubepi_message_to_wire(msg)
    assert out["role"] == "assistant"
    assert out["content"] == "calling tool"
    assert out["metadata"]["tool_calls"] == [
        {"id": "tc1", "name": "search", "arguments": {"q": "x"}}
    ]
    assert out["metadata"]["usage"]["input_tokens"] == 10
    assert out["metadata"]["usage"]["output_tokens"] == 5


def test_tool_result_message_to_wire() -> None:
    msg = ToolResultMessage(
        content=[TextContent(text="result text")],
        tool_call_id="tc1",
        tool_name="search",
    )
    out = cubepi_message_to_wire(msg)
    assert out["role"] == "tool"
    assert out["content"] == "result text"
    assert out["metadata"]["tool_call_id"] == "tc1"
    assert out["metadata"]["tool_name"] == "search"


def test_wire_input_to_user_message_simple_text() -> None:
    """API request body's user-input text → cubepi.UserMessage."""
    msg = wire_input_to_cubepi_user_message("hello world")
    assert isinstance(msg, UserMessage)
    assert msg.content[0].text == "hello world"


def test_wire_input_carries_attachments_in_metadata() -> None:
    """Attachment blocks (file_attachment dicts) land in metadata for M3 to render."""
    attachments = [
        {"kind": "image", "filename": "a.png", "size_bytes": 100, "sandbox_path": "/x/a.png"}
    ]
    msg = wire_input_to_cubepi_user_message("look at this", attachments=attachments)
    assert msg.content[0].text == "look at this"
    assert msg.metadata["attachments"] == attachments
```

### Step 2: Run failing tests

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend
uv run pytest tests/unit/test_convert_pi.py -v
```
Expected: 6 failures.

### Step 3: Implement

```python
# backend/cubeplex/agents/convert_pi.py
"""cubepi.Message ↔ cubeplex API wire format conversion (M1.2).

Mirrors cubeplex/agents/convert.py (LangChain version). Used by the
cubepi-runtime path; M3 will extend with attachment rendering, citations,
etc. once those middlewares are ported.
"""
from __future__ import annotations

from typing import Any

from cubepi.providers.base import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)


def _join_text(content: list) -> str:
    """Concatenate all TextContent text values; ignore non-text blocks."""
    parts = [c.text for c in content if isinstance(c, TextContent)]
    return "".join(parts)


def cubepi_message_to_wire(msg: Message) -> dict[str, Any]:
    """Convert a cubepi.Message into cubeplex's API response dict shape."""
    if isinstance(msg, UserMessage):
        return {
            "role": "user",
            "content": _join_text(msg.content),
            "metadata": dict(msg.metadata),
        }

    if isinstance(msg, AssistantMessage):
        tool_calls = [
            {"id": c.id, "name": c.name, "arguments": c.arguments}
            for c in msg.content
            if isinstance(c, ToolCall)
        ]
        meta: dict[str, Any] = dict(msg.metadata)
        if tool_calls:
            meta["tool_calls"] = tool_calls
        meta["usage"] = {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        }
        return {
            "role": "assistant",
            "content": _join_text(msg.content),
            "metadata": meta,
        }

    if isinstance(msg, ToolResultMessage):
        meta = dict(msg.metadata)
        meta["tool_call_id"] = msg.tool_call_id
        meta["tool_name"] = msg.tool_name
        return {
            "role": "tool",
            "content": _join_text(msg.content),
            "metadata": meta,
        }

    raise TypeError(f"unknown cubepi Message type: {type(msg).__name__}")


def wire_input_to_cubepi_user_message(
    text: str,
    *,
    attachments: list[dict[str, Any]] | None = None,
) -> UserMessage:
    """Build a cubepi.UserMessage from an API-shaped user input.

    Attachments are stored in metadata for M3's AttachmentMiddleware port
    to render later. M1 doesn't render them — the bare cubepi path sends
    only text.
    """
    metadata: dict[str, Any] = {}
    if attachments:
        metadata["attachments"] = list(attachments)
    return UserMessage(
        content=[TextContent(text=text)],
        metadata=metadata,
    )
```

### Step 4: Run tests + suite

```bash
uv run pytest tests/unit/test_convert_pi.py -v   # 6 pass
uv run pytest tests/unit -q --tb=no             # 464 pass (458 + 6)
```

### Step 5: Commit

```bash
git add backend/cubeplex/agents/convert_pi.py backend/tests/unit/test_convert_pi.py
git commit -m "feat(agents): add convert_pi for cubepi.Message ↔ wire format (M1.2)

cubepi_message_to_wire: cubepi.{User,Assistant,ToolResult}Message →
cubeplex API response dict shape.

wire_input_to_cubepi_user_message: inbound API user text (+optional
attachments) → cubepi.UserMessage. Attachments stored in metadata for
M3's AttachmentMiddleware port to render."
```

---

## Task M1.3: stream_pi.py

Translate cubepi `StreamEvent`s into cubeplex SSE event dicts. cubepi events: `start`, `text_delta`, `text_start/end`, `thinking_start/delta/end`, `toolcall_start/delta/end`, `done`, `error`. cubeplex SSE: `text_delta`, `reasoning`, `tool_call`, `tool_call_delta`, `tool_result`, `usage`, `error`, `done`.

**Files:**
- Create: `backend/cubeplex/agents/stream_pi.py`
- Test: `backend/tests/unit/test_stream_pi.py`

### Step 1: Write tests

```python
"""stream_pi tests — cubepi StreamEvent → cubeplex SSE (M1.3)."""

from cubepi.providers.base import (
    AssistantMessage,
    StreamEvent,
    TextContent,
    ToolCall,
    Usage,
)

from cubeplex.agents.stream_pi import convert_cubepi_event_to_sse


def _mk_assistant(text: str = "", tool_calls: list[ToolCall] | None = None) -> AssistantMessage:
    content: list = []
    if text:
        content.append(TextContent(text=text))
    if tool_calls:
        content.extend(tool_calls)
    return AssistantMessage(content=content, usage=Usage())


def test_text_delta_translates_to_text_delta() -> None:
    evt = StreamEvent(type="text_delta", delta="hello", partial=_mk_assistant("hello"))
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "text_delta", "delta": "hello"}]


def test_thinking_delta_translates_to_reasoning() -> None:
    evt = StreamEvent(type="thinking_delta", delta="thinking...", partial=_mk_assistant())
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "reasoning", "delta": "thinking..."}]


def test_toolcall_end_emits_tool_call() -> None:
    """toolcall_end → tool_call (fully-formed)."""
    tc = ToolCall(id="tc1", name="search", arguments={"q": "x"})
    partial = _mk_assistant(tool_calls=[tc])
    evt = StreamEvent(type="toolcall_end", content_index=0, partial=partial)
    out = convert_cubepi_event_to_sse(evt)
    assert len(out) == 1
    assert out[0]["type"] == "tool_call"
    assert out[0]["id"] == "tc1"
    assert out[0]["name"] == "search"
    assert out[0]["arguments"] == {"q": "x"}


def test_toolcall_delta_emits_tool_call_delta() -> None:
    evt = StreamEvent(
        type="toolcall_delta",
        delta='{"q": "x"',
        partial=_mk_assistant(tool_calls=[ToolCall(id="tc1", name="search", arguments={})]),
        content_index=0,
    )
    out = convert_cubepi_event_to_sse(evt)
    assert out[0]["type"] == "tool_call_delta"
    assert out[0]["delta"] == '{"q": "x"'


def test_done_translates_to_done() -> None:
    evt = StreamEvent(type="done")
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "done"}]


def test_error_translates_to_error() -> None:
    evt = StreamEvent(type="error", error_message="boom")
    out = convert_cubepi_event_to_sse(evt)
    assert out == [{"type": "error", "error": "boom"}]


def test_silent_events_are_dropped() -> None:
    """text_start/end, thinking_start/end, toolcall_start, start — drop (no cubeplex SSE equiv)."""
    for t in ["text_start", "text_end", "thinking_start", "thinking_end", "toolcall_start", "start"]:
        evt = StreamEvent(type=t)
        out = convert_cubepi_event_to_sse(evt)
        assert out == [], f"event type {t!r} should be silently dropped, got {out!r}"
```

### Step 2: Run failing tests

```bash
uv run pytest tests/unit/test_stream_pi.py -v
```
Expected: 7 failures.

### Step 3: Implement

```python
# backend/cubeplex/agents/stream_pi.py
"""cubepi StreamEvent → cubeplex SSE event dict translation (M1.3).

cubeplex's SSE event types (consumed by frontend): text_delta, reasoning,
tool_call, tool_call_delta, tool_result, usage, error, done.

cubepi's provider events are richer: text_start/delta/end, thinking_*,
toolcall_*, start, done, error. This module maps the subset cubeplex cares
about and silently drops the rest.

`tool_result` events come from the agent loop's after_tool_call path,
not the provider stream — emitted separately when the loop captures a
tool execution result. M3 will wire that path; M1 only handles provider
stream events.
"""
from __future__ import annotations

from typing import Any

from cubepi.providers.base import StreamEvent, ToolCall


def convert_cubepi_event_to_sse(evt: StreamEvent) -> list[dict[str, Any]]:
    """Translate a single cubepi StreamEvent into 0..1 cubeplex SSE event dicts."""
    t = evt.type

    if t == "text_delta":
        return [{"type": "text_delta", "delta": evt.delta or ""}]

    if t == "thinking_delta":
        return [{"type": "reasoning", "delta": evt.delta or ""}]

    if t == "toolcall_delta":
        return [{"type": "tool_call_delta", "delta": evt.delta or ""}]

    if t == "toolcall_end":
        # Fully-formed tool call lives at partial.content[content_index]
        if evt.partial is None or evt.content_index is None:
            return []
        block = evt.partial.content[evt.content_index]
        if not isinstance(block, ToolCall):
            return []
        return [{
            "type": "tool_call",
            "id": block.id,
            "name": block.name,
            "arguments": block.arguments,
        }]

    if t == "done":
        return [{"type": "done"}]

    if t == "error":
        return [{"type": "error", "error": evt.error_message or "unknown error"}]

    # Silent: start, text_start/end, thinking_start/end, toolcall_start
    return []
```

### Step 4: Run tests + suite

```bash
uv run pytest tests/unit/test_stream_pi.py -v   # 7 pass
uv run pytest tests/unit -q --tb=no             # 471 pass (464 + 7)
```

### Step 5: Commit

```bash
git add backend/cubeplex/agents/stream_pi.py backend/tests/unit/test_stream_pi.py
git commit -m "feat(agents): translate cubepi StreamEvent to cubeplex SSE (M1.3)

Maps text_delta, thinking_delta (→reasoning), toolcall_delta,
toolcall_end (→tool_call), done, error. Silently drops cubepi-only
events (text_start/end, thinking_start/end, toolcall_start, start)
that have no cubeplex SSE equivalent.

tool_result events emit from the agent loop's after_tool_call path,
not the provider stream — M3 wires that."
```

---

## Task M1.4: graph_pi.py — create_cubeplex_cubepi_agent

The factory function that builds a cubepi.Agent for cubeplex. M1 builds a **bare** agent: no cubeplex middleware, no sandbox, no skills, no memory injection. Just LLM + system prompt + Python function tools (if any).

**Files:**
- Create: `backend/cubeplex/agents/graph_pi.py`
- Test: `backend/tests/unit/test_graph_pi.py`

### Step 1: Write tests

```python
"""graph_pi tests — create_cubeplex_cubepi_agent (M1.4)."""

import asyncio

import pytest
from cubepi import Agent
from cubepi.providers.faux import FauxProvider, faux_assistant_message
from cubepi.providers.base import Model

from cubeplex.agents.graph_pi import create_cubeplex_cubepi_agent


def test_returns_cubepi_agent_instance() -> None:
    provider = FauxProvider()
    agent = create_cubeplex_cubepi_agent(
        provider=provider,
        model_id="test-model",
        provider_name="faux",
        system_prompt="You are helpful.",
    )
    assert isinstance(agent, Agent)


def test_agent_carries_system_prompt() -> None:
    agent = create_cubeplex_cubepi_agent(
        provider=FauxProvider(),
        model_id="test-model",
        provider_name="faux",
        system_prompt="You are helpful.",
    )
    # cubepi.Agent stores system_prompt on its internal state
    assert agent._state.system_prompt == "You are helpful."


def test_agent_accepts_checkpointer_and_thread_id() -> None:
    from cubepi.checkpointer import MemoryCheckpointer
    cp = MemoryCheckpointer()
    agent = create_cubeplex_cubepi_agent(
        provider=FauxProvider(),
        model_id="test-model",
        provider_name="faux",
        system_prompt="",
        checkpointer=cp,
        thread_id="conv-123",
    )
    assert agent._checkpointer is cp
    assert agent._thread_id == "conv-123"


@pytest.mark.asyncio
async def test_bare_agent_runs_a_turn() -> None:
    """Smoke: a bare cubepi agent runs an LLM call against FauxProvider."""
    provider = FauxProvider()
    provider.set_responses([faux_assistant_message("hello back")])
    agent = create_cubeplex_cubepi_agent(
        provider=provider,
        model_id="test-model",
        provider_name="faux",
        system_prompt="You are helpful.",
    )
    await agent.prompt("hi")
    assert len(agent.state.messages) == 2
    assert agent.state.messages[-1].content[0].text == "hello back"
```

### Step 2: Run failing tests

```bash
uv run pytest tests/unit/test_graph_pi.py -v
```
Expected: 4 failures.

### Step 3: Implement

```python
# backend/cubeplex/agents/graph_pi.py
"""cubepi agent factory for cubeplex runtime (M1.4).

Builds a bare cubepi.Agent without cubeplex middleware. M3 will add the
11 middleware ports as opt-in *_pi modules and extend this factory to
compose them.
"""
from __future__ import annotations

from typing import Any

from cubepi import Agent, Model
from cubepi.agent.types import AgentTool
from cubepi.providers.base import Provider


def create_cubeplex_cubepi_agent(
    *,
    provider: Provider,
    model_id: str,
    provider_name: str,
    system_prompt: str = "",
    tools: list[AgentTool] | None = None,
    checkpointer: Any = None,
    thread_id: str | None = None,
) -> Agent:
    """Build a cubepi.Agent for cubeplex's cubepi runtime path.

    M1: bare agent, no cubeplex middleware. M2 will wire tools through
    cubeplex.tools.registry_pi; M3 will compose the 11 cubeplex middlewares
    via the `middleware=[...]` kwarg on Agent.
    """
    return Agent(
        provider=provider,
        model=Model(id=model_id, provider=provider_name),
        system_prompt=system_prompt,
        tools=tools or [],
        checkpointer=checkpointer,
        thread_id=thread_id,
    )
```

### Step 4: Run tests + suite

```bash
uv run pytest tests/unit/test_graph_pi.py -v   # 4 pass
uv run pytest tests/unit -q --tb=no            # 475 pass (471 + 4)
```

### Step 5: Commit

```bash
git add backend/cubeplex/agents/graph_pi.py backend/tests/unit/test_graph_pi.py
git commit -m "feat(agents): add create_cubeplex_cubepi_agent factory (M1.4)

Bare cubepi.Agent wrapper: provider + system_prompt + tools + checkpointer.
No cubeplex middleware (M3 backfills). Sufficient for M1's smoke E2E."
```

---

## Task M1.5: run_manager dispatch by runtime flag

`streams/run_manager.py` currently calls `create_cubeplex_agent` (langgraph). Add a branch that selects the cubepi path when `config.agents.runtime == "cubepi"`.

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py`

### Discovery

Read the full call site:
```bash
grep -B5 -A50 "create_cubeplex_agent" backend/cubeplex/streams/run_manager.py
```

Note the langgraph path:
1. Loads LLMFactory, gets a LangChain `llm` via `create_default()`
2. Calls `create_cubeplex_agent(llm=, tools=, system_prompt=, sandbox=, ...)`
3. Iterates `agent.astream(stream_mode=...)` and converts chunks via `convert_messages_chunk`/`convert_updates_chunk`
4. Pushes events to `event_q`

The cubepi path needs to:
1. Use `LLMFactory.build_cubepi_provider(provider_config, cache_policy=CubeplexCacheMarkerPolicy())` to get a cubepi.Provider. This requires loading the active ProviderConfig (look at how `create_default()` does it; mirror that data-loading step).
2. Open `init_cubepi_checkpointer()` async context for the cubepi.PostgresCheckpointer
3. Build via `create_cubeplex_cubepi_agent(provider=, model_id=, provider_name=, system_prompt=, checkpointer=cp, thread_id=conversation_id)`
4. Run `await agent.prompt(user_text)` — cubepi.Agent emits events on its event queue; consume them
5. For each cubepi event, translate via `convert_cubepi_event_to_sse(evt)` and push translated events to `event_q`

### Step 1: Add unit-level seam test (integration test comes in M1.6)

Skip — this task is the integration glue; unit testing requires heavy mocking. Verify via M1.6 E2E.

### Step 2: Modify run_manager

In `streams/run_manager.py`, find the existing langgraph path. Wrap it:

```python
from cubeplex.config import config as _config

# ... in the run logic, where create_cubeplex_agent currently lives:

runtime = _config.agents.runtime if hasattr(_config, "agents") else _config.get("agents.runtime", "langgraph")

if runtime == "cubepi":
    await self._run_cubepi_path(
        ctx=ctx,
        conversation_id=conversation_id,
        user_text=user_text,
        attachments=attachments,
        effective_system_prompt=effective_system_prompt,
        event_q=event_q,
    )
    return

# Existing langgraph path follows unchanged:
from cubeplex.agents.graph import create_cubeplex_agent
# ... existing code ...
```

Add the new method:

```python
async def _run_cubepi_path(
    self,
    *,
    ctx,
    conversation_id: str,
    user_text: str,
    attachments: list[dict],
    effective_system_prompt: str,
    event_q,
) -> None:
    """Execute a conversation turn via the cubepi runtime (M1)."""
    from cubeplex.agents.checkpointer_pi import init_cubepi_checkpointer
    from cubeplex.agents.convert_pi import wire_input_to_cubepi_user_message
    from cubeplex.agents.graph_pi import create_cubeplex_cubepi_agent
    from cubeplex.agents.stream_pi import convert_cubepi_event_to_sse
    from cubeplex.db.engine import async_session_maker
    from cubeplex.llm.cache_markers_pi import CubeplexCacheMarkerPolicy
    from cubeplex.llm.factory import LLMFactory

    # 1. Pick provider config — mirror what LLMFactory.create_default() does
    async with async_session_maker() as llm_session:
        factory = LLMFactory(
            session=llm_session,
            org_id=ctx.org_id,
            encryption_backend=self._app.state.encryption_backend,
        )
        provider_config, model_id = await factory.resolve_default_provider_and_model()
        await llm_session.commit()

    cache_policy = CubeplexCacheMarkerPolicy()
    provider = factory.build_cubepi_provider(
        provider_config, cache_policy=cache_policy
    )

    # 2. Open cubepi checkpointer
    async with init_cubepi_checkpointer() as cp:
        agent = create_cubeplex_cubepi_agent(
            provider=provider,
            model_id=model_id,
            provider_name=provider_config.name,
            system_prompt=effective_system_prompt,
            checkpointer=cp,
            thread_id=conversation_id,
        )

        # 3. Push the user message; cubepi.Agent.prompt accepts str directly
        # but we lose attachments. M1 strips attachments and just sends text;
        # M3's AttachmentMiddleware port will render them.
        # NOTE: append the user message ourselves so we can carry metadata
        # (attachments etc.) — Agent.prompt(str) does this internally but
        # without metadata.
        user_msg = wire_input_to_cubepi_user_message(user_text, attachments=attachments)
        # cubepi.Agent doesn't expose a direct "prompt with Message" API in v0.3;
        # if there's a public way to push a UserMessage with metadata, use it;
        # otherwise call prompt(user_text) and accept losing metadata for M1.
        # M3 will fix this when MemoryMiddleware needs to read attachments.

        # 4. Stream events
        # cubepi.Agent emits its own AgentEvent stream (start/text_delta/...).
        # Subscribe and translate.
        async for cubepi_event in agent.stream_events_during(agent.prompt(user_text)):
            # Adapt to the actual cubepi event-streaming API. Likely:
            #   stream = await agent.prompt(user_text)
            #   async for evt in stream: ...
            # Or use the event queue / on_event callback Agent supports.
            for sse_event in convert_cubepi_event_to_sse(cubepi_event):
                await event_q.put(sse_event)
```

**Important**: the exact API to stream events from a running `cubepi.Agent` needs verification against cubepi v0.3. Read `~/cubepi/cubepi/agent/agent.py` to find the streaming entry point. Common patterns:
- `agent.prompt(text)` returns an awaitable; events fire via `Agent.on_event` callback OR an internal event_emitter
- Or: there's a `stream_events()` async generator

Read the cubepi `Agent` class carefully and pick the right shape. If the shape doesn't match cleanly, this task may require small cubepi-side additions (added to a follow-up PR), in which case BLOCK and report.

`factory.resolve_default_provider_and_model()` may not exist as-is; mirror whatever `create_default()` does internally (it likely picks an `(provider_config, model_config)` tuple from `llm_config.default_model`). Refactor by extracting a shared helper if needed.

### Step 3: Verify imports + syntax

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend
uv run python -c "from cubeplex.streams.run_manager import RunManager; print('ok')"
```

### Step 4: Commit

```bash
git add backend/cubeplex/streams/run_manager.py
git commit -m "feat(streams): dispatch run_manager to cubepi runtime by config flag (M1.5)

When config.agents.runtime == 'cubepi', use create_cubeplex_cubepi_agent
with cubepi.PostgresCheckpointer + build_cubepi_provider + CubeplexCacheMarkerPolicy.
M1 path has no cubeplex middleware (M3 backfills); attachments are noted
in metadata but not yet rendered.

Existing langgraph path remains unchanged and is the default."
```

---

## Task M1.6: E2E test through the cubepi runtime path

Send a real conversation through the API with `CUBEPLEX_AGENTS__RUNTIME=cubepi`. Verify the SSE event sequence matches cubeplex's wire shape.

**Files:**
- Create: `backend/tests/e2e/test_cubepi_path_conversation.py`

### Step 1: Test

```python
"""End-to-end smoke test: conversation goes through the cubepi runtime path (M1.6).

Uses the existing E2E client fixture + sets CUBEPLEX_AGENTS__RUNTIME=cubepi
via monkeypatch on config. Issues one POST /conversations/{id}/messages
and asserts the SSE stream contains expected event types.
"""

import json

import pytest


pytestmark = pytest.mark.real_llm


@pytest.mark.asyncio
async def test_cubepi_path_round_trip_one_turn(
    member_client,  # provides authenticated client + workspace_id
) -> None:
    client, ws_id = member_client

    # Force cubepi runtime via env (config.agents.runtime).
    # NOTE: requires CUBEPLEX_AGENTS__RUNTIME to be honored before client lifetime begins;
    # if member_client fixture doesn't pick this up, set it in conftest or run pytest with
    # CUBEPLEX_AGENTS__RUNTIME=cubepi explicitly.

    # 1. Create a conversation
    resp = await client.post(
        f"/api/v1/ws/{ws_id}/conversations", params={"title": "cubepi-m1-smoke"}
    )
    resp.raise_for_status()
    conv_id = resp.json()["id"]

    # 2. POST a message; consume SSE
    seen_types: list[str] = []
    async with client.stream(
        "POST",
        f"/api/v1/ws/{ws_id}/conversations/{conv_id}/messages",
        json={"content": "Say hello in one word."},
    ) as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data = line[len("data: ") :].strip()
            if not data:
                continue
            evt = json.loads(data)
            t = evt.get("type")
            if t:
                seen_types.append(t)

    # 3. Expect the canonical event sequence: at least one text_delta, then done
    assert "done" in seen_types, f"no 'done' event in stream: {seen_types!r}"
    text_deltas = [t for t in seen_types if t == "text_delta"]
    assert len(text_deltas) > 0, f"no text_delta events: {seen_types!r}"
```

NOTE: this test needs `CUBEPLEX_AGENTS__RUNTIME=cubepi` set when the test runs. The simplest way: ensure `config.test.yaml` already has `agents.runtime: cubepi` (M0.4 did this) AND verify the test process picks up that config (the `member_client` fixture should use test config). If env doesn't take effect, monkey-patch `config.agents.runtime` directly inside the test.

### Step 2: Run

```bash
# Note: real_llm marker; needs CUBEPLEX_E2E_LLM_* env or skip
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend
uv run pytest tests/e2e/test_cubepi_path_conversation.py -v -m real_llm --tb=short
```

Expected: PASS if `CUBEPLEX_E2E_LLM_*` is set; skip otherwise.

### Step 3: Commit

```bash
git add backend/tests/e2e/test_cubepi_path_conversation.py
git commit -m "test(e2e): smoke a real-LLM conversation through the cubepi runtime path (M1.6)

Goes through the public API (POST /conversations/{id}/messages) with
agents.runtime=cubepi. Asserts the SSE stream emits at least one
text_delta and a final 'done', confirming end-to-end wiring."
```

---

## Task M1.7: Verify M1 surface + push

### Step 1: Full check

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend && make check
```
Expected: format clean, lint clean, type-check clean, all unit tests pass.

### Step 2: Test counts

```bash
uv run pytest tests/unit -q --tb=no
```
Expected: ~475 (452 baseline M0 + 6+6+7+4 = 23 new M1 unit tests).

### Step 3: Manual smoke

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi/backend
CUBEPLEX_AGENTS__RUNTIME=cubepi uv run python -c "
from cubeplex.config import config
from cubeplex.streams.run_manager import RunManager
from cubeplex.agents.graph_pi import create_cubeplex_cubepi_agent
from cubeplex.agents.stream_pi import convert_cubepi_event_to_sse
from cubeplex.agents.convert_pi import cubepi_message_to_wire
from cubeplex.llm.cache_markers_pi import CubeplexCacheMarkerPolicy
print('runtime:', config.agents.runtime)
print('all M1 modules import')
"
```
Expected: `runtime: cubepi` + 'all M1 modules import'.

### Step 4: Push

```bash
cd /home/chris/cubeplex/.worktrees/feat/integrate-cubepi
git push
```

Expected: pre-push hooks pass; remote update.

---

## Self-review checklist

After all M1 tasks complete:

- [ ] All 4 new `*_pi.py` modules import cleanly
- [ ] `CubeplexCacheMarkerPolicy` walks back to last `AssistantMessage` (test covers user-only, tool-result-between, multiple assistants)
- [ ] `cubepi_message_to_wire` produces stable dicts for all 3 Message types
- [ ] `convert_cubepi_event_to_sse` covers text_delta, thinking (→reasoning), toolcall (→tool_call/tool_call_delta), done, error; drops silent events
- [ ] `create_cubeplex_cubepi_agent` produces a working bare cubepi.Agent
- [ ] `run_manager.py` dispatches by `config.agents.runtime`
- [ ] One real-LLM E2E confirms the cubepi path returns SSE matching cubeplex's wire shape
- [ ] No existing cubeplex langgraph test regressed
- [ ] M1 PR-update pushed; CI green on cubepi path

## Spec coverage map (Spec B § M1)

| Spec requirement | Implementing task |
|---|---|
| `agents/graph_pi.py` | M1.4 |
| `agents/stream_pi.py` | M1.3 |
| `agents/convert_pi.py` | M1.2 |
| `llm/cache_markers_pi.py` | M1.1 |
| API route / run_manager dispatches by flag | M1.5 |
| Real-LLM smoke through cubepi path | M1.6 |

M2 begins after M1 PR-update is reviewed + cubepi PR #65 is merged (so cubepi v0.3 is releasable and any further pre-release fixes have settled). M2 covers tools layer (registry, 6 builtin tool ports, MCP runtime).
