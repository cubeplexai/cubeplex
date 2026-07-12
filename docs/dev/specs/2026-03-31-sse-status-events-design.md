# SSE Status Events for Sandbox Initialization

## Problem

The `POST /api/v1/conversations/{id}/messages` endpoint performs sandbox creation, LLM initialization, and agent assembly **before** yielding the first SSE event. If sandbox creation is slow or hangs, the client receives `200 OK` with headers but 0 bytes of data — no user feedback, and E2E tests time out.

## Solution

Add a `status` event type to the SSE stream. The backend emits status events at key initialization phases so the frontend can display progress.

## Design

### New Event Type: `status`

```json
{
  "type": "status",
  "timestamp": "2026-03-31T12:00:00Z",
  "data": { "phase": "sandbox_creating" },
  "agent_id": null,
  "agent_name": null
}
```

### Phases

| Phase | When emitted |
|-------|-------------|
| `sandbox_creating` | Before `sandbox_manager.get_or_create()` |
| `sandbox_ready` | After sandbox is available |

When sandbox is **not enabled**, no status events are emitted — streaming starts immediately.

### Backend Changes

**`cubeplex/agents/schemas.py`** — Add `StatusEvent` class with `type: Literal["status"]`.

**`cubeplex/api/routes/v1/conversations.py`** — In `event_generator()`, yield `StatusEvent` before and after sandbox initialization:

```python
# Before sandbox creation
yield f"data: {StatusEvent(phase='sandbox_creating', ...).model_dump_json()}\n\n"
sandbox = await sandbox_manager.get_or_create(user_id)
yield f"data: {StatusEvent(phase='sandbox_ready', ...).model_dump_json()}\n\n"
```

### Frontend Changes

**`packages/core/src/types/events.ts`** — Add `'status'` to `AgentEventType`, add `StatusEvent` interface.

**`packages/core/src/stores/messageStore.ts`** — Add `statusPhase: string | null` to store state. Handle `status` events by updating `statusPhase`. Clear on `done`/`error`.

**`packages/web/components/chat/AssistantMessage.tsx`** — When `statusPhase` is set during streaming, display a localized message instead of loading dots:

| Phase | Display |
|-------|---------|
| `sandbox_creating` | "正在准备沙箱环境..." |
| `sandbox_ready` | (cleared, show normal loading dots) |

**`packages/web/__tests__/hooks/useMessages.test.ts`** — Add test case for status event handling.

### Event Stream Example

```
data: {"type":"status","data":{"phase":"sandbox_creating"},...}
data: {"type":"status","data":{"phase":"sandbox_ready"},...}
data: {"type":"text_delta","data":{"content":"Hello"},...}
data: {"type":"text_delta","data":{"content":" world"},...}
data: {"type":"done","data":{},...}
```

### Non-Goals

- No progress bar or step counting
- No status events when sandbox is disabled
- No `agent_ready` phase (agent creation is fast, not worth a separate event)
