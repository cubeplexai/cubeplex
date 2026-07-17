# SSE Status Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Emit `status` events during sandbox initialization in the SSE stream so the frontend can show progress instead of a blank screen.

**Architecture:** Add a `StatusEvent` to the backend schema, yield it at sandbox init boundaries in the event generator, handle it in the frontend store, and display a status message in the chat UI.

**Tech Stack:** Python/FastAPI (backend), TypeScript/React/Zustand (frontend), Vitest (unit tests)

---

### Task 1: Backend — Add StatusEvent schema

**Files:**
- Modify: `backend/cubeplex/agents/schemas.py`

- [ ] **Step 1: Add StatusEvent class**

Add after the existing `DoneEvent` class at the end of `backend/cubeplex/agents/schemas.py`:

```python
class StatusEvent(AgentEvent):
    """Initialization phase status event.

    Emitted during setup (e.g., sandbox creation) before the LLM stream begins.
    """

    type: Literal["status"] = "status"
    data: dict[str, Any] = Field(description="Event data with phase identifier")
```

- [ ] **Step 2: Verify no type errors**

Run: `cd /home/chris/cubeplex/backend && uv run mypy cubeplex/agents/schemas.py`
Expected: Success, no errors

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/agents/schemas.py
git commit -m "feat: add StatusEvent schema for sandbox init phases"
```

---

### Task 2: Backend — Emit status events in event_generator

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py:16` (import)
- Modify: `backend/cubeplex/api/routes/v1/conversations.py:298-313` (sandbox init block)

- [ ] **Step 1: Add StatusEvent to imports**

In `backend/cubeplex/api/routes/v1/conversations.py`, change line 16 from:

```python
from cubeplex.agents.schemas import AgentEvent, DoneEvent
```

to:

```python
from cubeplex.agents.schemas import AgentEvent, DoneEvent, StatusEvent
```

- [ ] **Step 2: Add helper to create status SSE line**

Add this helper inside `event_generator()`, right after the `event_q` and `cv_token` lines (after line 286):

```python
        def _status(phase: str) -> str:
            evt = StatusEvent(
                timestamp=datetime.now(UTC).isoformat(),
                data={"phase": phase},
            )
            return f"data: {evt.model_dump_json()}\n\n"
```

- [ ] **Step 3: Yield status events around sandbox creation**

Replace the sandbox init block (lines 298-313) from:

```python
            # Get sandbox — DI or production
            sandbox_factory = getattr(raw_request.app.state, "sandbox_factory", None)
            if sandbox_factory:
                sandbox = sandbox_factory()
            else:
                from cubeplex.config import config

                sandbox_enabled = config.get("sandbox.enabled", False)
                if sandbox_enabled:
                    try:
                        from cubeplex.sandbox.manager import get_sandbox_manager

                        sandbox_manager = get_sandbox_manager()
                        sandbox = await sandbox_manager.get_or_create(user_id)
                    except Exception as e:
                        logger.warning("Sandbox unavailable, continuing without: {}", e)
```

with:

```python
            # Get sandbox — DI or production
            sandbox_factory = getattr(raw_request.app.state, "sandbox_factory", None)
            if sandbox_factory:
                sandbox = sandbox_factory()
            else:
                from cubeplex.config import config

                sandbox_enabled = config.get("sandbox.enabled", False)
                if sandbox_enabled:
                    try:
                        from cubeplex.sandbox.manager import get_sandbox_manager

                        yield _status("sandbox_creating")
                        sandbox_manager = get_sandbox_manager()
                        sandbox = await sandbox_manager.get_or_create(user_id)
                        yield _status("sandbox_ready")
                    except Exception as e:
                        logger.warning("Sandbox unavailable, continuing without: {}", e)
```

- [ ] **Step 4: Verify no type errors**

Run: `cd /home/chris/cubeplex/backend && uv run mypy cubeplex/api/routes/v1/conversations.py`
Expected: Success, no errors

- [ ] **Step 5: Commit**

```bash
git add backend/cubeplex/api/routes/v1/conversations.py
git commit -m "feat: emit sandbox status events in SSE stream"
```

---

### Task 3: Frontend — Add status event type

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts`

- [ ] **Step 1: Add status to AgentEventType and add StatusEvent interface**

In `frontend/packages/core/src/types/events.ts`, change `AgentEventType` from:

```typescript
export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'error'
  | 'done'
```

to:

```typescript
export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'error'
  | 'done'
  | 'status'
```

Then add after the `DoneEvent` interface:

```typescript
export type StatusPhase = 'sandbox_creating' | 'sandbox_ready'

export interface StatusEvent extends AgentEvent {
  type: 'status'
  data: { phase: StatusPhase }
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/core/src/types/events.ts
git commit -m "feat: add StatusEvent type to frontend event types"
```

---

### Task 4: Frontend — Handle status events in messageStore

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Test: `frontend/packages/web/__tests__/hooks/useMessages.test.ts`

- [ ] **Step 1: Write the failing test**

Add this test at the end of the `describe('messageStore.send', ...)` block in `frontend/packages/web/__tests__/hooks/useMessages.test.ts`:

```typescript
  it('updates statusPhase on status events', async () => {
    let resolveStream: () => void
    const streamPromise = new Promise<void>((resolve) => { resolveStream = resolve })

    // Create a slow stream that yields events one at a time
    vi.stubGlobal('fetch', vi.fn(() => {
      const events = [
        { type: 'status', data: { phase: 'sandbox_creating' }, agent_id: null, agent_name: null, timestamp: '' },
        { type: 'status', data: { phase: 'sandbox_ready' }, agent_id: null, agent_name: null, timestamp: '' },
        { type: 'done', data: {}, agent_id: null, agent_name: null, timestamp: '' },
      ]
      const lines = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(lines))
          controller.close()
        },
      })
      return new Response(stream, {
        headers: { 'content-type': 'text/event-stream' },
      })
    }))

    await act(async () => {
      await useMessageStore.getState().send(mockClient as any, CONV_ID, 'hi')
    })

    // After done, statusPhase should be cleared
    expect(useMessageStore.getState().statusPhase).toBeNull()
  })
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/chris/cubeplex/frontend && pnpm --filter web test`
Expected: FAIL — `statusPhase` is not a property on the store

- [ ] **Step 3: Add statusPhase to store and handle status events**

In `frontend/packages/core/src/stores/messageStore.ts`:

Add `statusPhase` to the `MessageStore` interface:

```typescript
export interface MessageStore {
  messages: Record<string, Message[]>
  streamAgents: Record<string, AgentStream>
  isStreaming: boolean
  statusPhase: string | null
  error: string | null

  loadMessages(client: ApiClient, conversationId: string): Promise<void>
  send(client: ApiClient, conversationId: string, content: string): Promise<void>
  clearStream(): void
}
```

Add initial value in the `create` call:

```typescript
export const useMessageStore = create<MessageStore>((set, get) => ({
  messages: {},
  streamAgents: {},
  isStreaming: false,
  statusPhase: null,
  error: null,
```

In the `send` method, add `statusPhase: null` to the initial `set()` call (the one that adds the user message):

```typescript
    set((s) => ({
      messages: {
        ...s.messages,
        [conversationId]: [...(s.messages[conversationId] ?? []), userMessage],
      },
      streamAgents: { [MAIN_AGENT_KEY]: emptyStream() },
      isStreaming: true,
      statusPhase: null,
      error: null,
    }))
```

Add a handler for `status` events in the `for await` loop, after the `tool_result` handler and before the `done` handler:

```typescript
        } else if (event.type === 'status') {
          set({ statusPhase: (event.data as { phase: string }).phase })
```

In the `finally` block, add `statusPhase: null` to both `set()` calls — the one that adds the assistant message:

```typescript
        set((s) => ({
          messages: {
            ...s.messages,
            [conversationId]: [
              ...(s.messages[conversationId] ?? []),
              assistantMessage,
            ],
          },
          isStreaming: false,
          statusPhase: null,
          streamAgents: {},
        }))
```

And the fallback:

```typescript
        set({ isStreaming: false, statusPhase: null, streamAgents: {} })
```

Also in `clearStream()`:

```typescript
  clearStream() {
    set({ streamAgents: {}, isStreaming: false, statusPhase: null })
  },
```

- [ ] **Step 4: Export statusPhase in useMessages hook**

In `frontend/packages/web/hooks/useMessages.ts`, add `statusPhase` to the returned object:

```typescript
export function useMessages(conversationId: string) {
  const messagesMap = useMessageStore((s) => s.messages) ?? {}
  const messages = messagesMap[conversationId] ?? []
  const isStreaming = useMessageStore((s) => s.isStreaming) ?? false
  const statusPhase = useMessageStore((s) => s.statusPhase)
  const streamAgents = useMessageStore((s) => s.streamAgents)

  const agents = streamAgents ?? {}
  const mainStream = agents['main'] ?? null
  const subAgentStreams = Object.entries(agents).filter(([key]) => key !== 'main')

  return { messages, isStreaming, statusPhase, mainStream, subAgentStreams }
}
```

- [ ] **Step 5: Update the test beforeEach to include statusPhase**

In the `beforeEach` of `useMessages.test.ts`, add `statusPhase: null`:

```typescript
beforeEach(() => {
  useMessageStore.setState({
    messages: {},
    streamAgents: {},
    isStreaming: false,
    statusPhase: null,
    error: null,
  })
})
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/chris/cubeplex/frontend && pnpm --filter web test`
Expected: All 5 tests PASS

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts frontend/packages/web/hooks/useMessages.ts frontend/packages/web/__tests__/hooks/useMessages.test.ts
git commit -m "feat: handle status events in messageStore"
```

---

### Task 5: Frontend — Display status phase in chat UI

**Files:**
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`

- [ ] **Step 1: Pass statusPhase from MessageList to AssistantMessage**

In `frontend/packages/web/components/chat/MessageList.tsx`, destructure `statusPhase` and pass it to the streaming `AssistantMessage`:

```typescript
export function MessageList({ conversationId }: MessageListProps) {
  const { messages, isStreaming, statusPhase, mainStream, subAgentStreams } =
    useMessages(conversationId)
```

And change the streaming AssistantMessage render:

```typescript
        {isStreaming && mainStream && (
          <>
            {subAgentStreams.map(([agentId, stream]) => (
              <SubAgentCard
                key={agentId}
                agentId={agentId}
                stream={stream}
                isRunning={isStreaming}
              />
            ))}
            <AssistantMessage stream={mainStream} isStreaming statusPhase={statusPhase} />
          </>
        )}
```

- [ ] **Step 2: Update AssistantMessage to accept and display statusPhase**

In `frontend/packages/web/components/chat/AssistantMessage.tsx`, update `StreamingProps`:

```typescript
interface StreamingProps {
  message?: never
  stream: AgentStream
  isStreaming: true
  statusPhase?: string | null
}
```

Update the component signature:

```typescript
export function AssistantMessage({ message, stream, isStreaming, statusPhase }: AssistantMessageProps) {
```

Replace the loading indicator block (the `else isStreaming ?` branch) from:

```tsx
        ) : isStreaming ? (
          <div data-testid="loading-indicator" className="flex items-center gap-1 pl-1">
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:300ms]" />
          </div>
```

with:

```tsx
        ) : isStreaming ? (
          <div data-testid="loading-indicator" className="flex items-center gap-1 pl-1">
            {statusPhase === 'sandbox_creating' ? (
              <span className="text-xs text-muted-foreground animate-pulse">
                正在准备沙箱环境...
              </span>
            ) : (
              <>
                <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:300ms]" />
              </>
            )}
          </div>
```

- [ ] **Step 3: Run type check**

Run: `cd /home/chris/cubeplex/frontend && pnpm type-check`
Expected: No type errors

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/chat/MessageList.tsx frontend/packages/web/components/chat/AssistantMessage.tsx
git commit -m "feat: display sandbox status phase in chat UI"
```

---

### Task 6: Verify unit tests pass

**Files:** (no changes — verification only)

- [ ] **Step 1: Run all frontend unit tests**

Run: `cd /home/chris/cubeplex/frontend && pnpm --filter web test`
Expected: All 5 tests PASS

- [ ] **Step 2: Run backend type check**

Run: `cd /home/chris/cubeplex/backend && uv run mypy cubeplex/agents/schemas.py cubeplex/api/routes/v1/conversations.py`
Expected: Success
