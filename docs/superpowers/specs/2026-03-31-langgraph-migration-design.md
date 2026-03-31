# LangGraph Migration Design

Replace `deepagents` with native LangGraph, simplify sandbox to execute-only, use thread state as single source of truth for messages, and simplify frontend state management.

## Motivation

- `deepagents` has too many abstractions (filesystem middleware, subagents, skills) that don't align with our needs
- Sandbox exposes too many methods (read, write, grep, ls) when `execute` alone is sufficient — shell commands support pipes, parallelism, and composability
- Messages are stored in both LangGraph checkpointer AND a `messages` table — redundant
- Frontend manually manages optimistic updates, streaming event accumulation, and post-stream re-fetch — unnecessary complexity
- Need foundation for user management, authentication, and billing

## Scope

### In Scope

- Replace `deepagents` with native LangGraph `StateGraph`
- Simplify `Sandbox` to async-first base class with `execute` only
- Implement `OpenSandbox` adapter and `LocalSandbox` for dev
- Delete `messages` table; read messages from checkpointer thread state
- Create `cubebox/prompts/` directory for prompt management
- Simplify frontend `messageStore` — flat state, store-level text accumulation
- Add backend E2E tests, frontend Playwright E2E tests, and unit tests
- Dependency injection in `create_app` for testability

### Out of Scope

- `useStream` SDK integration (custom API is better for auth/billing)
- Human-in-the-loop / interrupt support (future)
- Memory / skills middleware (future)
- Subagent spawning (future)
- User management / auth / billing (future)

## Architecture

### Backend Directory Changes

```
cubebox/
├── agents/
│   ├── graph.py              # NEW: native LangGraph agent builder
│   ├── state.py              # NEW: AgentState definition
│   ├── schemas.py            # KEEP: SSE event types (remove ChainStartEvent)
│   ├── checkpointer.py       # KEEP
│   └── executor.py           # DELETE
├── prompts/                  # NEW
│   ├── system.py             # Base system prompt
│   └── execution.py          # Execute tool documentation prompt
├── sandbox/
│   ├── base.py               # NEW: async-first Sandbox ABC
│   ├── opensandbox.py        # MODIFY: inherit new base, execute-only
│   ├── local.py              # NEW: subprocess-based for dev/debug
│   └── manager.py            # MODIFY: use new Sandbox base class
├── tools/
│   ├── sandbox_tool.py       # NEW: wrap Sandbox.execute as BaseTool
│   └── ...                   # KEEP: registry, builtin, MCP
├── api/routes/v1/
│   └── conversations.py      # MODIFY: read messages from checkpointer
├── models/
│   ├── conversation.py       # KEEP
│   └── message.py            # DELETE
├── repositories/
│   ├── conversation.py       # KEEP
│   └── message.py            # DELETE
└── ...
```

### Agent Core

#### State (`agents/state.py`)

```python
from langgraph.graph import MessagesState

class AgentState(MessagesState):
    """Inherits messages: Annotated[list, add_messages] from MessagesState."""
    pass
```

Simple inheritance. Extensible for future custom fields.

#### Graph (`agents/graph.py`)

```python
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

def create_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    checkpointer: Checkpointer | None = None,
    system_prompt: str | None = None,
) -> CompiledStateGraph:
    llm_with_tools = llm.bind_tools(tools)

    async def agent_node(state: AgentState) -> dict:
        messages = state["messages"]
        if system_prompt:
            messages = [SystemMessage(content=system_prompt)] + messages
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)
```

Standard ReAct loop: agent -> check tool_calls -> execute tools -> back to agent.
Agent node is async (`ainvoke`) for FastAPI compatibility.

### Sandbox

#### Base Class (`sandbox/base.py`)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ExecuteResult:
    output: str
    exit_code: int | None = None

class Sandbox(ABC):
    """Async-first sandbox with execute-only interface."""

    @property
    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult: ...

    @abstractmethod
    async def close(self) -> None: ...
```

No `read`/`write`/`grep`/`ls` — all handled via shell commands through `execute`.

#### OpenSandbox Adapter (`sandbox/opensandbox.py`)

Inherits `Sandbox`. Wraps `opensandbox.Sandbox.commands.run()` into `execute()`.
Extracted from current `OpenSandbox.aexecute()` logic (combining stdout/stderr, getting exit code).
Delete all other methods (aread, awrite, agrep_raw, als_info, sync wrappers, sync_skills).

#### Local Sandbox (`sandbox/local.py`)

`asyncio.create_subprocess_shell` implementation. For dev/debug only.
Configurable working directory and timeout.

#### Tool Registration (`tools/sandbox_tool.py`)

```python
from langchain_core.tools import tool

def create_execute_tool(sandbox: Sandbox) -> BaseTool:
    @tool
    async def execute(command: str) -> str:
        """Execute a shell command in the sandbox environment."""
        result = await sandbox.execute(command)
        output = result.output
        if result.exit_code and result.exit_code != 0:
            output += f"\n[exit code: {result.exit_code}]"
        return output
    return execute
```

### Prompts

#### `prompts/system.py`

Base system prompt adapted from deepagents `BASE_AGENT_PROMPT`:

```python
BASE_SYSTEM_PROMPT = """You are an AI assistant that helps users accomplish tasks using tools.

## Core Behavior

- Be concise and direct. No unnecessary preamble.
- Don't say "I'll now do X" — just do it.
- If the request is ambiguous, ask before acting.

## Doing Tasks

1. Understand first — read relevant context, check existing patterns.
2. Act — implement the solution.
3. Verify — check your work against what was asked.

Keep working until the task is fully complete. Only yield back when done or genuinely blocked.

If something fails repeatedly, stop and analyze why — don't retry the same approach."""
```

#### `prompts/execution.py`

```python
EXECUTION_PROMPT = """## Shell Execution

You have access to the `execute` tool to run shell commands in a sandbox environment.

- Use standard shell commands for file operations (cat, ls, grep, sed, etc.)
- Use pipes and command chaining for complex operations
- Commands run in an isolated sandbox — safe to experiment
- Check exit codes in output for error detection"""
```

#### Prompt Assembly

```python
def build_system_prompt(has_sandbox: bool = False) -> str:
    parts = [BASE_SYSTEM_PROMPT]
    if has_sandbox:
        parts.append(EXECUTION_PROMPT)
    return "\n\n".join(parts)
```

### Message State — Thread as Single Source of Truth

#### Delete `messages` table

- Remove `cubebox/models/message.py`
- Remove `cubebox/repositories/message.py`
- Alembic migration to drop `messages` table

#### `list_messages` endpoint reads from checkpointer

```python
@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: str):
    checkpointer = await create_checkpointer()
    try:
        config = {"configurable": {"thread_id": conversation_id}}
        checkpoint = await checkpointer.aget(config)
        if not checkpoint:
            return {"messages": []}
        lc_messages = checkpoint["channel_values"]["messages"]
        return {"messages": convert_to_api_messages(lc_messages)}
    finally:
        if hasattr(checkpointer, "conn"):
            checkpointer.conn.close()
```

#### Message Conversion (`agents/convert.py`)

Converts LangChain message types to API format:

| LangChain Type | API role | content |
|---|---|---|
| `HumanMessage` | `user` | `.content` |
| `AIMessage` | `assistant` | `.content` (text), `.tool_calls` (tools), `.additional_kwargs["reasoning_content"]` (reasoning) |
| `ToolMessage` | `tool` | `.content` with `.name` |

```python
def convert_to_api_messages(lc_messages: list) -> list[dict]:
    result = []
    for msg in lc_messages:
        if isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": msg.content, ...})
        elif isinstance(msg, AIMessage):
            result.append({
                "role": "assistant",
                "content": msg.content or None,
                "tool_calls": [{"name": tc["name"], "arguments": tc["args"]} for tc in msg.tool_calls] or None,
                "reasoning": msg.additional_kwargs.get("reasoning_content"),
                ...
            })
        elif isinstance(msg, ToolMessage):
            result.append({"role": "tool", "name": msg.name, "content": msg.content, ...})
    return result
```

#### `send_message` endpoint simplification

```python
@router.post("/{conversation_id}/messages")
async def send_message(conversation_id: str, body: SendMessageRequest, ...):
    # Validate conversation exists
    # NO user message save — checkpointer handles it
    # Build agent, stream response
    # NO assistant message save — checkpointer handles it
    return StreamingResponse(event_generator(), ...)
```

### Streaming (SSE)

Retain custom SSE format. Use `stream_mode=["messages", "updates"]` with v2 format.

#### Event Types (keep)

- `text_delta` — incremental LLM text
- `reasoning` — model reasoning/thinking
- `tool_call` — tool invocation
- `tool_result` — tool execution result
- `error` — error
- `done` — stream complete

#### Remove

- `chain_start` — not needed by frontend

#### Stream Processing

```python
async def event_generator():
    agent = create_agent(llm, tools, checkpointer, system_prompt)
    config = {"configurable": {"thread_id": conversation_id}}

    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=content)]},
        stream_mode="messages",
        config=config,
    ):
        # chunk is (message_chunk, metadata) tuple
        events = convert_stream_chunk(chunk)
        for event in events:
            yield f"data: {event.model_dump_json()}\n\n"

    yield f"data: {DoneEvent(...).model_dump_json()}\n\n"
```

`convert_stream_chunk()` extracts from the chunk the same way as current `_handle_stream_chunk()`, but as a standalone function (not a method on an executor class).

### App Factory — Dependency Injection

```python
def create_app(
    checkpointer_factory: Callable | None = None,
    sandbox_factory: Callable | None = None,
) -> FastAPI:
    """Factory with injectable dependencies for testing."""
    ...
```

- Production: factories read from config (MySQL checkpointer, OpenSandbox)
- Testing: pass `MemorySaver` and `LocalSandbox` factories

### Frontend Changes

#### `packages/core/src/stores/messageStore.ts` — Rewrite

```typescript
interface MessageStore {
  messages: Message[]           // from thread state
  streamText: string            // real-time accumulated text
  streamToolCalls: ToolCallEvent[]
  streamReasoning: string
  isStreaming: boolean
  error: string | null

  loadMessages(conversationId: string): Promise<void>
  send(conversationId: string, content: string): Promise<void>
  clearStream(): void
}
```

Changes from current:
- Flat `messages: Message[]` instead of `Record<string, Message[]>`
- Store-level text accumulation (`streamText`) instead of component-level extraction
- No post-stream re-fetch — construct assistant message locally from accumulated stream data
- `loadMessages` calls backend which reads from checkpointer

#### `packages/core/src/types/message.ts` — Update

```typescript
interface Message {
  id: string
  role: 'user' | 'assistant' | 'tool'
  content: string | null
  tool_calls?: { name: string; arguments: Record<string, any> }[] | null
  reasoning?: string | null
  name?: string | null  // for tool messages
  created_at?: string
}
```

Add `tool` role, `tool_calls`, `reasoning`, `name` fields to match new backend format.

#### `packages/core/src/api/stream.ts` — Simplify

Keep `readLines()` and `streamMessages()` async generators. Simplify error handling.

#### `packages/web/components/chat/AssistantMessage.tsx` — Simplify

- For streaming: read `streamText`, `streamToolCalls`, `streamReasoning` from store
- For history: read `content`, `tool_calls`, `reasoning` directly from `Message` object
- Remove `extractText()`, `extractReasoning()`, `hasToolActivity()` functions

#### `packages/web/hooks/useMessages.ts` — Simplify

```typescript
export function useMessages() {
  const messages = useMessageStore((s) => s.messages)
  const isStreaming = useMessageStore((s) => s.isStreaming)
  const streamText = useMessageStore((s) => s.streamText)
  const streamToolCalls = useMessageStore((s) => s.streamToolCalls)
  const streamReasoning = useMessageStore((s) => s.streamReasoning)
  return { messages, isStreaming, streamText, streamToolCalls, streamReasoning }
}
```

No `conversationId` parameter — store tracks one conversation at a time.

## Dependency Changes

### Backend — Remove

- `deepagents>=0.4.5`
- `nest-asyncio>=1.6.0`
- `asyncer>=0.0.17`

### Backend — Keep

- `langgraph>=1.0.10`
- `langgraph-checkpoint-mysql[aiomysql]>=3.0.0`
- `langchain-openai>=1.1.10`
- `opensandbox>=0.1.5` (make optional)

### Frontend — Add (dev)

- `vitest` — unit tests
- `@playwright/test` — E2E tests

## Database Migration

- Alembic migration to drop `messages` table
- LangGraph checkpointer tables remain (initialized in app lifespan)
- `conversations` table remains unchanged

## Testing Strategy

### Backend Unit Tests (`tests/unit/`)

No external dependencies (no LLM API key, no MySQL).

| Test File | What It Tests |
|---|---|
| `test_graph.py` | Agent builds correctly, tools bind, ReAct loop works with mock LLM + MemorySaver |
| `test_sandbox_local.py` | LocalSandbox execute, timeout, exit codes |
| `test_sandbox_base.py` | Sandbox ABC contract |
| `test_convert_messages.py` | LangChain Message -> API format conversion |
| `test_prompts.py` | Prompt assembly logic |

### Backend E2E Tests (`tests/e2e/`)

Use real LLM API + MemorySaver (no MySQL dependency). Dependency-injected via `create_app()`.

| Test File | What It Tests |
|---|---|
| `test_conversation_flow.py` | Create conversation -> send message -> stream response -> read history from thread state |
| `test_streaming.py` | SSE format validation, event type presence, done event |
| `test_thread_state.py` | Multi-turn conversation context retention, message count consistency |

#### E2E Test Infrastructure

```python
@pytest.fixture
async def client():
    app = create_app(
        checkpointer_factory=lambda: MemorySaver(),
        sandbox_factory=lambda: LocalSandbox(),
    )
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c
```

#### Key E2E Assertions

1. **Send + read consistency**: messages sent via SSE stream match what `GET /messages` returns
2. **Multi-turn context**: agent remembers prior turns (thread state accumulates correctly)
3. **SSE format**: every line is `data: {valid JSON}\n\n`, every stream ends with `done` event
4. **Error handling**: invalid conversation ID returns 404, empty content returns 400

### Frontend Unit Tests (`packages/web/__tests__/`)

Vitest with mock SSE responses.

| Test File | What It Tests |
|---|---|
| `hooks/useMessages.test.ts` | Store accumulates stream text correctly, constructs final message, handles errors |

### Frontend E2E Tests (`packages/web/__tests__/e2e/`)

Playwright against running frontend + backend.

| Test File | What It Tests |
|---|---|
| `chat-flow.spec.ts` | Send message -> see streaming -> see final response -> reload preserves history |
| `streaming.spec.ts` | Loading animation appears during stream, disappears on completion, text renders incrementally |

### Test Commands

```bash
# Backend
cd backend
uv run pytest tests/unit/ -v              # Unit (no API key needed)
uv run pytest tests/e2e/ -v               # E2E (needs LLM API key)
make test                                  # All

# Frontend
cd frontend
pnpm test                                 # Vitest unit tests
pnpm test:e2e                             # Playwright E2E (needs running backend)
```

## What Does NOT Change

- `conversations` table and `ConversationRepository`
- `LLMFactory` and LLM config system
- `ToolRegistry` and MCP tool loading
- `UserIdentityMiddleware` / `CancellationMiddleware`
- `SandboxManager` (updated to use new `Sandbox` base class)
- Frontend `conversationStore.ts`
- Frontend UI component structure (AppShell, Sidebar, InputBar)
- Frontend styling and theme system

## Migration Steps (High Level)

1. Backend: Create `sandbox/base.py`, `sandbox/local.py`, update `sandbox/opensandbox.py`
2. Backend: Create `prompts/` with system and execution prompts
3. Backend: Create `agents/state.py`, `agents/graph.py`, `agents/convert.py`
4. Backend: Create `tools/sandbox_tool.py`
5. Backend: Update `create_app` with dependency injection
6. Backend: Update `conversations.py` — new `send_message` and `list_messages`
7. Backend: Delete `executor.py`, `models/message.py`, `repositories/message.py`
8. Backend: Remove `deepagents` from dependencies
9. Backend: Alembic migration to drop `messages` table
10. Backend: Write unit tests and E2E tests
11. Frontend: Update types (`Message`, remove events-based types)
12. Frontend: Rewrite `messageStore.ts`
13. Frontend: Simplify `AssistantMessage.tsx`, `useMessages.ts`
14. Frontend: Add vitest + playwright, write tests
