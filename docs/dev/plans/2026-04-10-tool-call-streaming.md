# Tool Call Argument Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `write_file` and `edit_file` as dedicated sandbox tools and stream their arguments (file content) to the frontend in real-time via a new `tool_call_delta` SSE event, so users see file content being generated instead of a blank screen.

**Architecture:** LangChain's `messages` stream mode emits `AIMessageChunk` objects with `tool_call_chunks` containing incremental JSON argument fragments. We add `tool_call_delta` events to the SSE stream by extracting these chunks in `convert_messages_chunk()`. The existing `updates` mode `tool_call` event remains unchanged as the finalized signal. The frontend accumulates deltas per `index` and renders content progressively.

**Tech Stack:** Python 3.12, FastAPI, LangGraph, LangChain, Next.js, TypeScript, Zustand

---

## File Structure

### Backend (modified)
- `backend/cubeplex/middleware/sandbox.py` — Add `write_file` and `edit_file` tools alongside existing `execute`; update system prompt injection
- `backend/cubeplex/prompts/sandbox.py` — Update prompt to document new file tools
- `backend/cubeplex/agents/stream.py` — Add `tool_call_delta` extraction from `tool_call_chunks`

### Frontend (modified)
- `frontend/packages/core/src/types/events.ts` — Add `ToolCallDeltaEvent` type and `tool_call_delta` to `AgentEventType`
- `frontend/packages/core/src/stores/messageStore.ts` — Handle `tool_call_delta` events, accumulate args

### Tests (modified)
- `backend/tests/e2e/test_streaming.py` — Add test for `tool_call_delta` events in SSE stream

---

### Task 1: Add `write_file` and `edit_file` tools to SandboxMiddleware

**Files:**
- Modify: `backend/cubeplex/middleware/sandbox.py`

- [ ] **Step 1: Write the failing test**

Create a test that verifies the sandbox middleware exposes three tools: `execute`, `write_file`, and `edit_file`.

```python
# backend/tests/e2e/test_sandbox_tools.py
"""E2E test: sandbox middleware tool registration."""

import pytest

from cubeplex.middleware.sandbox import SandboxMiddleware
from cubeplex.sandbox.local import LocalSandbox


def test_sandbox_middleware_registers_file_tools() -> None:
    sandbox = LocalSandbox(workdir="/tmp")
    mw = SandboxMiddleware(sandbox=sandbox)
    names = [t.name for t in mw.tools]
    assert "execute" in names
    assert "write_file" in names
    assert "edit_file" in names


@pytest.mark.asyncio
async def test_write_file_creates_file(tmp_path) -> None:
    sandbox = LocalSandbox(workdir=str(tmp_path))
    mw = SandboxMiddleware(sandbox=sandbox)
    write_tool = next(t for t in mw.tools if t.name == "write_file")
    result = await write_tool.ainvoke(
        {"file_path": str(tmp_path / "hello.txt"), "content": "hello world"}
    )
    assert "hello.txt" in result
    assert (tmp_path / "hello.txt").read_text() == "hello world"


@pytest.mark.asyncio
async def test_edit_file_replaces_content(tmp_path) -> None:
    target = tmp_path / "greet.txt"
    target.write_text("hello world")
    sandbox = LocalSandbox(workdir=str(tmp_path))
    mw = SandboxMiddleware(sandbox=sandbox)
    edit_tool = next(t for t in mw.tools if t.name == "edit_file")
    result = await edit_tool.ainvoke(
        {
            "file_path": str(target),
            "old_string": "hello",
            "new_string": "goodbye",
        }
    )
    assert "greet.txt" in result
    assert target.read_text() == "goodbye world"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_sandbox_tools.py -v`
Expected: FAIL — `write_file` and `edit_file` not found in tool names.

- [ ] **Step 3: Implement `write_file` and `edit_file` tools**

```python
# backend/cubeplex/middleware/sandbox.py
"""SandboxMiddleware — registers execute, write_file, edit_file tools and injects sandbox context."""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from cubeplex.middleware._utils import append_to_system_message
from cubeplex.prompts.sandbox import SANDBOX_PROMPT_TEMPLATE
from cubeplex.sandbox.base import Sandbox


class _ExecuteArgs(BaseModel):
    command: str


class _WriteFileArgs(BaseModel):
    file_path: str = Field(description="Absolute path where the file should be created.")
    content: str = Field(description="The text content to write to the file.")


class _EditFileArgs(BaseModel):
    file_path: str = Field(description="Absolute path to the file to edit.")
    old_string: str = Field(description="The exact text to find and replace. Must be unique.")
    new_string: str = Field(description="The replacement text. Must differ from old_string.")


def _create_execute_tool(sandbox: Sandbox) -> BaseTool:
    """Build the execute tool backed by a sandbox instance."""

    async def _execute(command: str) -> str:
        result = await sandbox.execute(command)
        output = result.output
        if result.exit_code is not None and result.exit_code != 0:
            output += f"\n[exit code: {result.exit_code}]"
        return output

    return StructuredTool.from_function(
        coroutine=_execute,
        name="execute",
        description="Execute a shell command in the sandbox environment.",
        args_schema=_ExecuteArgs,
    )


def _create_write_file_tool(sandbox: Sandbox) -> BaseTool:
    """Build the write_file tool backed by a sandbox instance."""

    async def _write_file(file_path: str, content: str) -> str:
        escaped = content.replace("\\", "\\\\").replace("'", "'\\''")
        cmd = f"mkdir -p \"$(dirname '{file_path}')\" && printf '%s' '{escaped}' > '{file_path}'"
        result = await sandbox.execute(cmd)
        if result.exit_code is not None and result.exit_code != 0:
            return f"Error writing {file_path}: {result.output}"
        return f"Successfully wrote {file_path}"

    return StructuredTool.from_function(
        coroutine=_write_file,
        name="write_file",
        description=(
            "Write content to a new file. Creates parent directories if needed. "
            "Prefer edit_file for modifying existing files."
        ),
        args_schema=_WriteFileArgs,
    )


def _create_edit_file_tool(sandbox: Sandbox) -> BaseTool:
    """Build the edit_file tool backed by a sandbox instance."""

    async def _edit_file(file_path: str, old_string: str, new_string: str) -> str:
        # Read current content
        read_result = await sandbox.execute(f"cat '{file_path}'")
        if read_result.exit_code is not None and read_result.exit_code != 0:
            return f"Error reading {file_path}: {read_result.output}"

        current = read_result.output
        count = current.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {file_path}"
        if count > 1:
            return (
                f"Error: old_string found {count} times in {file_path}. "
                "Provide more context to make it unique."
            )

        updated = current.replace(old_string, new_string, 1)
        escaped = updated.replace("\\", "\\\\").replace("'", "'\\''")
        write_result = await sandbox.execute(f"printf '%s' '{escaped}' > '{file_path}'")
        if write_result.exit_code is not None and write_result.exit_code != 0:
            return f"Error writing {file_path}: {write_result.output}"
        return f"Successfully edited {file_path}"

    return StructuredTool.from_function(
        coroutine=_edit_file,
        name="edit_file",
        description=(
            "Edit a file by replacing an exact string match. "
            "old_string must appear exactly once in the file."
        ),
        args_schema=_EditFileArgs,
    )


class SandboxMiddleware(AgentMiddleware[Any, Any, Any]):
    """Registers sandbox tools and injects sandbox context into system prompt."""

    def __init__(self, *, sandbox: Sandbox) -> None:
        self.sandbox = sandbox
        self.tools: Sequence[BaseTool] = [
            _create_execute_tool(sandbox),
            _create_write_file_tool(sandbox),
            _create_edit_file_tool(sandbox),
        ]

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any] | AIMessage]],
    ) -> ModelResponse[Any] | AIMessage:
        prompt = SANDBOX_PROMPT_TEMPLATE.format(workdir=self.sandbox.workdir)
        new_system = append_to_system_message(request.system_message, prompt)
        return await handler(request.override(system_message=new_system))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_sandbox_tools.py -v`
Expected: PASS — all three tests pass.

- [ ] **Step 5: Commit**

```bash
cd backend
git add cubeplex/middleware/sandbox.py tests/e2e/test_sandbox_tools.py
git commit -m "feat: add write_file and edit_file sandbox tools"
```

---

### Task 2: Update sandbox system prompt

**Files:**
- Modify: `backend/cubeplex/prompts/sandbox.py`

- [ ] **Step 1: Update the prompt to document file tools**

```python
# backend/cubeplex/prompts/sandbox.py
"""Sandbox execution prompt — injected when a sandbox is available."""

SANDBOX_PROMPT_TEMPLATE = """## Shell Execution

You have access to the `execute` tool to run shell commands in a sandbox environment.

**Working directory:** `{workdir}`
All commands execute in this directory by default. Always use this path (or relative paths \
from it) when reading, writing, or referencing files. Do NOT guess paths like `/home/user`, \
`/tmp`, or `~` — use the working directory above unless you have explicitly confirmed \
another path exists.

## File Tools

You have dedicated tools for file operations:

- `write_file(file_path, content)` — Create a new file with the given content. \
Creates parent directories automatically. Prefer this over `echo`/`cat` heredocs.
- `edit_file(file_path, old_string, new_string)` — Replace an exact string in an existing file. \
old_string must appear exactly once. Prefer this over `sed`/`awk`.

**When to use which:**
- Creating new files → `write_file`
- Modifying existing files → `edit_file`
- Running code, installing packages, listing files → `execute`

## Shell Commands (`execute` tool)

**Shell features available:**
- Pipes: `cat file.txt | grep pattern | wc -l`
- Redirection: `command > output.txt 2>&1`
- Command chaining: `cmd1 && cmd2` (stop on error), `cmd1 ; cmd2` (always continue)
- Background: `cmd &`

**Error handling:**
- Non-zero exit codes are appended to output as `[exit code: N]`
- Check exit codes for command success/failure
- Commands run in an isolated sandbox — safe to experiment"""
```

- [ ] **Step 2: Run existing tests to verify no regression**

Run: `cd backend && uv run pytest tests/e2e/test_streaming.py -v`
Expected: PASS — no change in behavior.

- [ ] **Step 3: Commit**

```bash
cd backend
git add cubeplex/prompts/sandbox.py
git commit -m "feat: update sandbox prompt with write_file and edit_file docs"
```

---

### Task 3: Add `tool_call_delta` event extraction to stream converter

**Files:**
- Modify: `backend/cubeplex/agents/stream.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/e2e/test_stream_converter.py
"""Tests for stream converter tool_call_delta extraction."""

from langchain_core.messages import AIMessageChunk

from cubeplex.agents.stream import convert_messages_chunk


def test_tool_call_chunk_emits_delta_event() -> None:
    """tool_call_chunks in AIMessageChunk should produce tool_call_delta events."""
    msg = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": "write_file", "args": '{"file_path": "/app/', "id": "tc_1", "index": 0}
        ],
    )
    events = convert_messages_chunk((msg, {}))
    deltas = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(deltas) == 1
    assert deltas[0]["data"]["name"] == "write_file"
    assert deltas[0]["data"]["args_delta"] == '{"file_path": "/app/'
    assert deltas[0]["data"]["tool_call_id"] == "tc_1"
    assert deltas[0]["data"]["index"] == 0


def test_tool_call_chunk_continuation_no_name() -> None:
    """Continuation chunks have name=None and id=None."""
    msg = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": None, "args": "hello world", "id": None, "index": 0}
        ],
    )
    events = convert_messages_chunk((msg, {}))
    deltas = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(deltas) == 1
    assert deltas[0]["data"]["name"] is None
    assert deltas[0]["data"]["args_delta"] == "hello world"


def test_text_and_tool_call_chunk_coexist() -> None:
    """Text content and tool_call_chunks can appear in the same chunk."""
    msg = AIMessageChunk(
        content="Let me write that file.",
        tool_call_chunks=[],
    )
    events = convert_messages_chunk((msg, {}))
    text_events = [e for e in events if e["type"] == "text_delta"]
    delta_events = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(text_events) == 1
    assert len(delta_events) == 0


def test_empty_args_delta_skipped() -> None:
    """Chunks with empty or None args should not produce events."""
    msg = AIMessageChunk(
        content="",
        tool_call_chunks=[
            {"name": "write_file", "args": "", "id": "tc_1", "index": 0},
            {"name": "write_file", "args": None, "id": "tc_2", "index": 1},
        ],
    )
    events = convert_messages_chunk((msg, {}))
    deltas = [e for e in events if e["type"] == "tool_call_delta"]
    # Name-only chunk (first) should still emit so frontend knows tool started
    assert len(deltas) == 1
    assert deltas[0]["data"]["name"] == "write_file"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/e2e/test_stream_converter.py -v`
Expected: FAIL — no `tool_call_delta` events emitted.

- [ ] **Step 3: Implement `tool_call_delta` extraction**

Add to the end of `convert_messages_chunk()` in `backend/cubeplex/agents/stream.py`, before the `return events` line:

```python
    # Tool call argument deltas (streaming tool input)
    tool_call_chunks = getattr(msg, "tool_call_chunks", []) or []
    for tc_chunk in tool_call_chunks:
        chunk_name = tc_chunk.get("name") if isinstance(tc_chunk, dict) else None
        chunk_args = tc_chunk.get("args") if isinstance(tc_chunk, dict) else None
        chunk_id = tc_chunk.get("id") if isinstance(tc_chunk, dict) else None
        chunk_index = tc_chunk.get("index") if isinstance(tc_chunk, dict) else None

        # Skip chunks with no useful data
        if not chunk_name and not chunk_args:
            continue

        events.append(
            {
                "type": "tool_call_delta",
                "timestamp": timestamp,
                "data": {
                    "tool_call_id": chunk_id,
                    "name": chunk_name,
                    "args_delta": chunk_args or "",
                    "index": chunk_index,
                },
                "agent_id": agent_id,
            }
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/e2e/test_stream_converter.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend
git add cubeplex/agents/stream.py tests/e2e/test_stream_converter.py
git commit -m "feat: extract tool_call_delta events from messages stream"
```

---

### Task 4: Add `tool_call_delta` to frontend event types

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts`

- [ ] **Step 1: Add `ToolCallDeltaEvent` type**

Add `'tool_call_delta'` to the `AgentEventType` union and add the interface:

```typescript
// Add to AgentEventType union:
export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_call_delta'
  | 'tool_result'
  | 'artifact'
  | 'error'
  | 'done'
  | 'status'

// Add new interface after ToolCallEvent:
export interface ToolCallDeltaEvent extends AgentEvent {
  type: 'tool_call_delta'
  data: {
    tool_call_id: string | null
    name: string | null
    args_delta: string
    index: number | null
  }
}
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: PASS — new type is additive.

- [ ] **Step 3: Commit**

```bash
cd frontend
git add packages/core/src/types/events.ts
git commit -m "feat: add ToolCallDeltaEvent type for streaming tool args"
```

---

### Task 5: Handle `tool_call_delta` in message store

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Modify: `frontend/packages/core/src/types/events.ts` (add `ContentBlock` variant)

- [ ] **Step 1: Add streaming tool call content block type**

In `frontend/packages/core/src/types/events.ts`, add a new variant to `ContentBlock`:

```typescript
export type ContentBlock =
  | {
      type: 'reasoning'
      content: string
      started_at?: number
      duration_ms?: number
    }
  | { type: 'text'; content: string }
  | {
      type: 'tool_call'
      name: string
      arguments: Record<string, unknown>
      tool_call_id: string
    }
  | {
      type: 'tool_call_streaming'
      name: string
      args_text: string
      tool_call_id: string | null
      index: number
    }
```

- [ ] **Step 2: Add `tool_call_delta` handler in messageStore**

In `frontend/packages/core/src/stores/messageStore.ts`, add the import for `ToolCallDeltaEvent` and add the handler after the `tool_call` handler block (after line 276):

```typescript
        } else if (event.type === 'tool_call_delta') {
          const e = event as ToolCallDeltaEvent
          batchedSet((s) => {
            const prev = s.streamAgents[agentKey] ?? emptyStream(event.agent_name)
            const idx = e.data.index ?? 0
            const blocks = [...prev.blocks]

            // Find existing streaming block for this index
            const existingIdx = blocks.findIndex(
              (b) => b.type === 'tool_call_streaming' && b.index === idx,
            )

            if (existingIdx >= 0) {
              // Append to existing streaming block
              const existing = blocks[existingIdx] as Extract<
                ContentBlock,
                { type: 'tool_call_streaming' }
              >
              blocks[existingIdx] = {
                ...existing,
                args_text: existing.args_text + (e.data.args_delta || ''),
                tool_call_id: e.data.tool_call_id ?? existing.tool_call_id,
              }
            } else {
              // Create new streaming block
              const finalized = finalizeLastReasoning(blocks)
              finalized.push({
                type: 'tool_call_streaming',
                name: e.data.name ?? '',
                args_text: e.data.args_delta || '',
                tool_call_id: e.data.tool_call_id ?? null,
                index: idx,
              })
              return {
                streamAgents: {
                  ...s.streamAgents,
                  [agentKey]: { ...prev, blocks: finalized },
                },
              }
            }

            return {
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: { ...prev, blocks },
              },
            }
          })
```

Also update the import line at top of `messageStore.ts`:

```typescript
import type {
  ContentBlock, TodoItem,
  Message, TextDeltaEvent, ToolCallEvent, ToolCallDeltaEvent,
  ToolResultEvent, ReasoningEvent, ArtifactEventData,
} from '../types'
```

- [ ] **Step 3: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd frontend
git add packages/core/src/types/events.ts packages/core/src/stores/messageStore.ts
git commit -m "feat: handle tool_call_delta events in message store"
```

---

### Task 6: E2E test for `tool_call_delta` SSE events

**Files:**
- Modify: `backend/tests/e2e/test_streaming.py`

- [ ] **Step 1: Add E2E test using monkeypatched agent**

Add this test to `backend/tests/e2e/test_streaming.py`:

```python
@pytest.mark.asyncio
async def test_tool_call_delta_events_in_sse_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tool_call_delta events should appear in SSE stream for tool_call_chunks."""
    from langchain_core.messages import AIMessageChunk

    class _DummySessionContext:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

    class _FakeConn:
        def close(self) -> None:
            pass

    class _FakeCheckpointer:
        def __init__(self) -> None:
            self.conn = _FakeConn()

    class _FakeToolCallAgent:
        async def astream(self, *_args, **_kwargs):
            # First chunk: tool name + start of args
            chunk1 = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {
                        "name": "write_file",
                        "args": '{"file_path": "/app/main.py", "content": "import ',
                        "id": "tc_1",
                        "index": 0,
                    }
                ],
            )
            yield ("messages", (chunk1, {}))

            # Second chunk: continuation
            chunk2 = AIMessageChunk(
                content="",
                tool_call_chunks=[
                    {"name": None, "args": 'os\\nimport sys"}', "id": None, "index": 0}
                ],
            )
            yield ("messages", (chunk2, {}))

    def _fake_session_maker() -> _DummySessionContext:
        return _DummySessionContext()

    async def _fake_get_by_id(self, conversation_id: str):
        return SimpleNamespace(id=conversation_id, title=conversation_id)

    async def _noop_update_timestamp(_conversation_id: str) -> None:
        return None

    monkeypatch.setattr(conversations_route, "async_session_maker", _fake_session_maker)
    monkeypatch.setattr(
        conversations_route, "_update_conversation_timestamp", _noop_update_timestamp
    )
    monkeypatch.setattr(
        conversations_route.ConversationRepository, "get_by_id", _fake_get_by_id
    )
    monkeypatch.setattr(
        "cubeplex.agents.graph.create_cubeplex_agent",
        lambda **_kwargs: _FakeToolCallAgent(),
    )

    raw_request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer_factory=_FakeCheckpointer,
                sandbox_factory=lambda: None,
                skills=[],
            )
        ),
        state=SimpleNamespace(user_id="test-user"),
    )

    response = await conversations_route.send_message(
        "test-conv",
        conversations_route.SendMessageRequest(content="write a file"),
        raw_request,
    )

    events = []
    async for chunk in response.body_iterator:
        payload = chunk.removeprefix("data: ").strip()
        if payload:
            events.append(json.loads(payload))

    delta_events = [e for e in events if e["type"] == "tool_call_delta"]
    assert len(delta_events) == 2, f"Expected 2 tool_call_delta events, got {len(delta_events)}"
    assert delta_events[0]["data"]["name"] == "write_file"
    assert delta_events[0]["data"]["tool_call_id"] == "tc_1"
    assert delta_events[1]["data"]["args_delta"] == 'os\\nimport sys"}'
```

- [ ] **Step 2: Run the E2E test**

Run: `cd backend && uv run pytest tests/e2e/test_streaming.py::test_tool_call_delta_events_in_sse_stream -v`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `cd backend && uv run pytest tests/ -v`
Expected: PASS — no regressions.

- [ ] **Step 4: Commit**

```bash
cd backend
git add tests/e2e/test_streaming.py
git commit -m "test: add E2E test for tool_call_delta SSE events"
```

---

### Task 7: Lint and type check

**Files:** None (validation only)

- [ ] **Step 1: Backend checks**

Run: `cd backend && make check`
Expected: PASS — format, lint, type-check, test all green.

- [ ] **Step 2: Frontend checks**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 3: Fix any issues found, then commit**

If there are any lint/type issues, fix them and commit:
```bash
git add -A
git commit -m "chore: fix lint and type issues"
```
