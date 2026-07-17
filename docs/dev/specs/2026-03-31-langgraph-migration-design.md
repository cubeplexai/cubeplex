# LangGraph Migration Design

> **SUPERSEDED (2026-05-14):** The LangGraph runtime designed here was fully replaced by cubepi. See `docs/superpowers/specs/2026-05-13-cubepi-main-agent-migration-design.md` for the current architecture.

Replace `deepagents` dependency with `langchain.agents.create_agent()` + custom middleware, simplify sandbox to execute-only, use thread state as single source of truth for messages, and simplify frontend state management.

## Key Insight

`langchain.agents.create_agent()` is a thin wrapper around LangGraph's `StateGraph`. It internally builds a state graph, compiles middleware as graph nodes, and returns a `CompiledStateGraph`. This means:

- We get LangGraph-native behavior (`.astream()`, checkpointer, etc.)
- We get a structured middleware system for composing agent capabilities
- We can vendor and adapt deepagents middleware without depending on the package

## Motivation

- `deepagents` package couples too many concerns and limits customization
- Sandbox exposes too many methods (read, write, grep, ls) when `execute` alone is sufficient — shell commands support pipes, parallelism, and composability
- Messages are stored in both LangGraph checkpointer AND a `messages` table — redundant
- Frontend manually manages optimistic updates, streaming event accumulation, and post-stream re-fetch — unnecessary complexity
- Need foundation for user management, authentication, and billing

## Scope

### In Scope

- Replace `deepagents` with `langchain.agents.create_agent()` + custom middleware
- Vendor and adapt middleware from deepagents: SandboxMiddleware (from FilesystemMiddleware), SubAgentMiddleware, SkillsMiddleware
- Simplify `Sandbox` to async-first base class with `execute` only
- Implement `OpenSandbox` adapter and `LocalSandbox` for dev
- Delete `messages` table; read messages from checkpointer thread state
- Create `cubeplex/prompts/` directory for prompt management
- Simplify frontend `messageStore` — flat state, store-level text accumulation
- Add backend E2E tests, frontend Playwright E2E tests, and unit tests
- Dependency injection in `create_app` for testability

### Out of Scope

- `useStream` SDK integration (custom API is better for auth/billing)
- Human-in-the-loop / interrupt support (future, but middleware system supports it)
- Memory middleware (future)
- User management / auth / billing (future)

## Architecture

### Backend Directory Changes

```
cubeplex/
├── agents/
│   ├── graph.py              # NEW: agent factory using create_agent() + middleware
│   ├── schemas.py            # KEEP: SSE event types (remove ChainStartEvent)
│   ├── convert.py            # NEW: LangChain Message → API format conversion
│   └── checkpointer.py       # KEEP
│   └── executor.py           # DELETE
├── middleware/               # NEW: vendored & adapted from deepagents
│   ├── sandbox.py            # Adapted from FilesystemMiddleware — execute tool + sandbox context
│   ├── subagents.py          # Adapted from SubAgentMiddleware — task delegation
│   └── skills.py             # Adapted from SkillsMiddleware — progressive skill disclosure
├── prompts/                  # NEW
│   ├── system.py             # Base system prompt
│   ├── sandbox.py            # Sandbox/execution tool documentation
│   ├── subagents.py          # Subagent usage guidance
│   └── skills.py             # Skills system guidance
├── sandbox/
│   ├── base.py               # NEW: async-first Sandbox ABC
│   ├── opensandbox.py        # MODIFY: inherit new base, execute-only
│   ├── local.py              # NEW: subprocess-based for dev/debug
│   └── manager.py            # MODIFY: use new Sandbox base class
├── tools/                    # KEEP: registry, builtin, MCP
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

#### Graph Factory (`agents/graph.py`)

Uses `langchain.agents.create_agent()` which internally builds a LangGraph `StateGraph` and returns `CompiledStateGraph`.

```python
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Checkpointer

from cubeplex.middleware.sandbox import SandboxMiddleware
from cubeplex.middleware.subagents import SubAgentMiddleware
from cubeplex.middleware.skills import SkillsMiddleware
from cubeplex.prompts.system import BASE_SYSTEM_PROMPT
from cubeplex.sandbox.base import Sandbox

def create_cubeplex_agent(
    llm: BaseChatModel,
    tools: list[BaseTool],
    *,
    sandbox: Sandbox | None = None,
    checkpointer: Checkpointer | None = None,
) -> CompiledStateGraph:
    """Build agent with middleware stack."""
    middleware = []

    if sandbox:
        middleware.append(SandboxMiddleware(sandbox=sandbox))

    # Skills and subagents always available
    middleware.append(SkillsMiddleware(...))
    middleware.append(SubAgentMiddleware(...))

    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=BASE_SYSTEM_PROMPT,
        middleware=middleware,
        checkpointer=checkpointer,
    )
```

`create_agent()` handles:
- Binding tools to the LLM (including tools from middleware)
- Building the ReAct loop (agent → tool_calls check → execute tools → back to agent)
- Compiling middleware hooks as graph nodes
- Merging middleware state schemas

The returned `CompiledStateGraph` supports `.astream()`, checkpointer persistence, etc.

### Middleware

Vendored from deepagents and adapted. Each middleware extends `langchain.agents.middleware.AgentMiddleware`.

#### SandboxMiddleware (`middleware/sandbox.py`)

Adapted from deepagents `FilesystemMiddleware`. Key changes:

- **Only registers `execute` tool** (no read, write, grep, ls, glob, edit tools)
- **Injects sandbox context into system prompt** via `before_model()` hook — sandbox ID, environment info, working directory
- **Async-first**: tool coroutine calls `sandbox.execute()` directly

```python
class SandboxMiddleware(AgentMiddleware):
    def __init__(self, sandbox: Sandbox):
        self.sandbox = sandbox
        self.tools = [create_execute_tool(sandbox)]

    async def abefore_model(self, request: ModelRequest, ...) -> ModelRequest:
        # Inject sandbox context (env info, cwd) into system message
        sandbox_prompt = build_sandbox_prompt(self.sandbox)
        return request.override(system_message=request.system_message + sandbox_prompt)
```

Prompt in `prompts/sandbox.py` — documents execute tool usage, sandbox environment details.

#### SubAgentMiddleware (`middleware/subagents.py`)

Adapted from deepagents. Registers a `task` tool that spawns ephemeral subagents.

```python
class SubAgentMiddleware(AgentMiddleware):
    def __init__(self, subagents: list[SubAgent], ...):
        self.tools = [create_task_tool(subagents)]
```

Each subagent is compiled as a separate `create_agent()` call with its own middleware stack. Prompt in `prompts/subagents.py`.

#### SkillsMiddleware (`middleware/skills.py`)

Adapted from deepagents. Progressively discloses available skills via system prompt updates in `before_model()` hook. Prompt in `prompts/skills.py`.

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
    """Async-first sandbox base class.

    Agent-facing: only `execute` is registered as a tool.
    Infrastructure-facing: `upload`/`download` for binary file transfer
    (used by API endpoints, SandboxManager, skills sync — NOT agent tools).
    """

    @property
    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    async def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResult: ...

    @abstractmethod
    async def upload(self, files: list[tuple[str, bytes]]) -> None: ...

    @abstractmethod
    async def download(self, paths: list[str]) -> list[tuple[str, bytes]]: ...

    @abstractmethod
    async def close(self) -> None: ...
```

- `execute` — the only method exposed to agent as a tool
- `upload`/`download` — binary file transfer, used by application layer (API endpoints, SandboxManager, skills sync), NOT registered as agent tools
- No `read`/`write`/`grep`/`ls` — all handled via shell commands through `execute`
- All sandbox implementations must implement all four methods

#### OpenSandbox Adapter (`sandbox/opensandbox.py`)

Inherits `Sandbox`. Wraps `opensandbox.Sandbox.commands.run()` into `execute()`.
Extracted from current `OpenSandbox.aexecute()` logic (combining stdout/stderr, getting exit code).
Delete all other methods (aread, awrite, agrep_raw, als_info, sync wrappers, sync_skills).

#### Local Sandbox (`sandbox/local.py`)

`asyncio.create_subprocess_shell` implementation. For dev/debug only.
Configurable working directory and timeout.

#### Tool Registration (in SandboxMiddleware)

```python
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

All prompts in `cubeplex/prompts/`, one file per concern:

| File | Content |
|---|---|
| `system.py` | `BASE_SYSTEM_PROMPT` — core agent behavior (adapted from deepagents) |
| `sandbox.py` | `SANDBOX_PROMPT` — execute tool docs, sandbox environment context |
| `subagents.py` | `SUBAGENT_PROMPT` — when/how to delegate to subagents |
| `skills.py` | `SKILLS_PROMPT` — skill discovery and invocation |

Prompts are composed by middleware — each middleware appends its prompt section to the system message via `before_model()` hook. No central assembly function needed.

### Message State — Thread as Single Source of Truth

#### Delete `messages` table

- Remove `cubeplex/models/message.py`
- Remove `cubeplex/repositories/message.py`
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

Retain custom SSE format. Enable `stream_subgraphs=True` for subagent event streaming.

#### Event Schema

All events now carry optional `agent_id` and `agent_name` for subagent identification:

```python
class AgentEvent(BaseModel):
    type: str
    timestamp: str
    data: dict[str, Any]
    agent_id: str | None = None    # None = main agent, "task:xxx" = subagent
    agent_name: str | None = None  # Human-readable subagent description
```

#### Event Types (keep)

- `text_delta` — incremental LLM text
- `reasoning` — model reasoning/thinking
- `tool_call` — tool invocation
- `tool_result` — tool execution result
- `error` — error
- `done` — stream complete

#### Remove

- `chain_start` — not needed by frontend

#### Subagent Streaming

Use `stream_subgraphs=True` to receive events from subagent execution. LangGraph's v2 format includes a `ns` (namespace) tuple that identifies which graph/subgraph emitted the event:

- `ns=()` → main agent
- `ns=("task:abc123",)` → subagent with ID "task:abc123"

The stream processor maps `ns` to `agent_id`/`agent_name` on each SSE event.

#### Stream Processing

```python
async def event_generator():
    agent = create_cubeplex_agent(llm, tools, sandbox=sandbox, checkpointer=checkpointer)
    config = {"configurable": {"thread_id": conversation_id}}

    async for chunk in agent.astream(
        {"messages": [HumanMessage(content=content)]},
        stream_mode="messages",
        stream_subgraphs=True,   # Enable subagent events
        config=config,
    ):
        # chunk includes ns field for subgraph identification
        events = convert_stream_chunk(chunk)
        for event in events:
            yield f"data: {event.model_dump_json()}\n\n"

    yield f"data: {DoneEvent(...).model_dump_json()}\n\n"
```

`convert_stream_chunk()` extracts events the same way as current `_handle_stream_chunk()`, plus maps `ns` tuple to `agent_id`/`agent_name` fields. Standalone function (not a method on an executor class).

#### Example SSE Stream with Subagent

```
data: {"type":"tool_call","agent_id":null,"data":{"name":"task","arguments":{"description":"Search for files"}}}
data: {"type":"text_delta","agent_id":"task:abc123","agent_name":"Search for files","data":{"content":"Looking"}}
data: {"type":"tool_call","agent_id":"task:abc123","agent_name":"Search for files","data":{"name":"execute","arguments":{"command":"find . -name '*.py'"}}}
data: {"type":"tool_result","agent_id":"task:abc123","agent_name":"Search for files","data":{"content":"./main.py\n./app.py"}}
data: {"type":"text_delta","agent_id":"task:abc123","agent_name":"Search for files","data":{"content":"Found 2 files"}}
data: {"type":"tool_result","agent_id":null,"data":{"content":"Found 2 Python files: main.py, app.py"}}
data: {"type":"text_delta","agent_id":null,"data":{"content":"I found the files..."}}
data: {"type":"done","data":{}}
```

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
// Per-agent streaming state (main agent or subagent)
interface AgentStream {
  text: string
  toolCalls: ToolCallEvent[]
  reasoning: string
  name: string | null           // subagent description, null for main
}

interface MessageStore {
  messages: Message[]            // from thread state
  streamAgents: Record<string, AgentStream>  // key = agent_id ("main" or "task:xxx")
  isStreaming: boolean
  error: string | null

  loadMessages(conversationId: string): Promise<void>
  send(conversationId: string, content: string): Promise<void>
  clearStream(): void
}
```

Changes from current:
- Flat `messages: Message[]` instead of `Record<string, Message[]>`
- `streamAgents` groups streaming state by agent_id — supports main agent + multiple concurrent subagents
- Store-level text accumulation instead of component-level extraction
- No post-stream re-fetch — construct assistant message locally from accumulated stream data
- `loadMessages` calls backend which reads from checkpointer

When an SSE event arrives, the store routes it to the correct `AgentStream` by `agent_id` (defaulting to `"main"` if null).

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

#### `packages/core/src/types/events.ts` — Update

```typescript
interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, any>
  agent_id: string | null    // null = main agent, "task:xxx" = subagent
  agent_name: string | null  // subagent description
}
```

Add `agent_id` and `agent_name` fields for subagent event routing.

#### `packages/core/src/api/stream.ts` — Simplify

Keep `readLines()` and `streamMessages()` async generators. Simplify error handling.

#### `packages/web/components/chat/AssistantMessage.tsx` — Simplify

- For streaming: read from `streamAgents` store — main agent renders inline, subagents render as separate cards
- For history: read `content`, `tool_calls`, `reasoning` directly from `Message` object
- Remove `extractText()`, `extractReasoning()`, `hasToolActivity()` functions

#### `packages/web/components/chat/SubAgentCard.tsx` — New

Renders a subagent's execution as a collapsible card:
- Header: agent name/description + status indicator (running/done)
- Body: streaming text, tool calls, reasoning (same layout as AssistantMessage but nested)
- Auto-collapses when subagent finishes

```tsx
function SubAgentCard({ agentId, stream }: { agentId: string; stream: AgentStream }) {
  return (
    <div className="border rounded-lg p-3 my-2 bg-muted/30">
      <div className="text-sm text-muted-foreground">{stream.name}</div>
      {stream.toolCalls.map(tc => <ToolCallBadge key={tc.id} call={tc} />)}
      {stream.text && <MarkdownContent content={stream.text} />}
    </div>
  )
}
```

#### `packages/web/hooks/useMessages.ts` — Simplify

```typescript
export function useMessages() {
  const messages = useMessageStore((s) => s.messages)
  const isStreaming = useMessageStore((s) => s.isStreaming)
  const streamAgents = useMessageStore((s) => s.streamAgents)
  return { messages, isStreaming, streamAgents }
}
```

No `conversationId` parameter — store tracks one conversation at a time.
Components access `streamAgents["main"]` for main agent, iterate other keys for subagent cards.

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
| `test_graph.py` | Agent builds correctly with middleware, tools bind, ReAct loop works with mock LLM + MemorySaver |
| `test_sandbox_local.py` | LocalSandbox execute, timeout, exit codes |
| `test_sandbox_base.py` | Sandbox ABC contract |
| `test_middleware_sandbox.py` | SandboxMiddleware registers execute tool, injects sandbox context into prompt |
| `test_middleware_subagents.py` | SubAgentMiddleware registers task tool, subagent compilation |
| `test_middleware_skills.py` | SkillsMiddleware progressive disclosure |
| `test_convert_messages.py` | LangChain Message -> API format conversion |
| `test_prompts.py` | All prompts load correctly, no syntax issues |

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

### Phase 1: Backend Foundation

1. Create `sandbox/base.py`, `sandbox/local.py`, update `sandbox/opensandbox.py`
2. Create `prompts/` with all prompt files (system, sandbox, subagents, skills)
3. Create `middleware/` — vendor and adapt SandboxMiddleware, SubAgentMiddleware, SkillsMiddleware from deepagents
4. Create `agents/graph.py` (create_cubeplex_agent using create_agent + middleware)
5. Create `agents/convert.py` (LangChain Message → API format)
6. Update `create_app` with dependency injection (checkpointer_factory, sandbox_factory)

### Phase 2: Backend API Migration

7. Update `conversations.py` — new `send_message` (stream via create_cubeplex_agent) and `list_messages` (read from checkpointer)
8. Delete `executor.py`, `models/message.py`, `repositories/message.py`
9. Remove `deepagents` from dependencies, remove `nest-asyncio`, `asyncer`
10. Alembic migration to drop `messages` table

### Phase 3: Backend Testing

11. Write unit tests (graph, sandbox, convert, prompts)
12. Write E2E tests (conversation flow, streaming, thread state)

### Phase 4: Frontend

13. Update types (`Message` with tool role, tool_calls, reasoning fields)
14. Rewrite `messageStore.ts` (flat state, store-level accumulation)
15. Simplify `AssistantMessage.tsx`, `useMessages.ts`
16. Update `stream.ts` and API client

### Phase 5: Frontend Testing

17. Add vitest + playwright setup
18. Write hook unit tests (mock SSE)
19. Write Playwright E2E tests (chat flow, streaming)
