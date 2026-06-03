# Memory Reflection v2: Detached Run + User Event Channel

**Status:** Draft, supersedes v1 (`2026-06-02-memory-reflection-hook-design.md`)
**Author:** xfgong
**Date:** 2026-06-02

## Background

v1 (PR #187) implemented end-of-run memory reflection via cubepi's new
`on_run_end` hook: when a run completes, the middleware injects a `UserMessage`
into the same conversation and the agent loop runs one extra turn to review
and save memories.

Testing surfaced two problems, both confirmed by the PR codex review (P1
finding) and direct user feedback:

1. **Output pollution.** The reflection turn's text (`"done"` or
   memory tool call narrations) leaks into the visible conversation
   transcript. The backend event bridge filters only the seed `UserMessage`,
   not subsequent `text_delta` events, and the frontend `messageStore`
   appends every delta into the visible assistant message.
2. **Perceived latency.** Reflection runs *before* `AgentEndEvent`, so the
   conversation doesn't visually "complete" until the reflection LLM call
   finishes. Several seconds of dead air after the real answer is done.

Both problems are structural — they exist because reflection lives inside
the main conversation's lifecycle. Filtering events post-hoc would be
invasive and fragile. The right fix is to move reflection out of the
conversation entirely.

## Goals

- Reflection runs **after** `AgentEndEvent` fires, so users see the
  conversation complete immediately.
- Reflection output (intermediate text, tool call narrations) never enters
  the conversation transcript, checkpoint, or replay history.
- When reflection saves memories, the user sees it as a non-blocking signal
  (toast / inline marker), not as a blocking part of the response.
- The notification channel is reusable for future async events
  (background jobs, cross-device notifications, etc.).

## Non-goals

- Real-time guarantees. Reflection happens eventually-consistently; the user
  may have already moved on by the time it finishes.
- Multi-conversation correlation. Each reflection is scoped to the single
  run that triggered it.
- Conversation-level memory facts (e.g. "earlier in this conversation we
  decided X"). v2 only addresses cross-conversation memories
  (`MemoryItem`).

## Architecture overview

Three independent pieces:

```
┌─────────────────────────────────────────────────────────────────┐
│  Main agent run (existing)                                       │
│  conversation SSE → ... → AgentEndEvent → SSE closes             │
└──────────────────────────────┬──────────────────────────────────┘
                               │ (after AgentEndEvent emitted)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  ReflectionRunner (new)                                          │
│  - asyncio background task                                       │
│  - spawns fresh Agent (cheap model, memory tools only)           │
│  - feeds last-turn context + memory snapshot                     │
│  - captures memory_save / memory_update tool calls               │
│  - on completion → publishes UserEvent                           │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│  UserEventBus + /api/v1/user/events SSE (new)                    │
│  - user-scoped persistent channel                                │
│  - in-memory pubsub + user_event table (durable)                 │
│  - frontend subscribes once at app shell                         │
└─────────────────────────────────────────────────────────────────┘
```

The reflection run is just a regular cubepi `Agent`, constructed and called
inside a background `asyncio.Task` — no cubepi changes needed. The
`on_run_end` hook from v1 stays in cubepi (still a useful general-purpose
hook) but cubebox no longer uses it.

## Component 1: ReflectionRunner

**Location:** `backend/cubebox/services/reflection_runner.py`

A service owned by `RunManager`. Triggered after each main-conversation
run completes successfully.

### Trigger point

In `run_manager._run_loop` (or wherever `AgentEndEvent` is consumed),
after the main run's stop_reason is observed as "natural" / "stop" /
"should_stop" (i.e. not "error", "aborted", or HITL suspend), schedule
a reflection task:

```python
asyncio.create_task(
    reflection_runner.reflect(
        conversation_id=...,
        run_id=...,
        user_id=...,
        workspace_id=...,
        last_user_message=...,
        final_assistant_message=...,
        tool_result_summaries=[...],  # this run's tool calls, name + brief outcome
    )
)
```

The task is fire-and-forget. Failures are logged, not retried, not
surfaced to the user.

### Context window

The reflection agent receives **only the last turn** of the conversation,
not the full history:

- The user message that initiated the last turn.
- The agent's final assistant message.
- A condensed list of tool calls made in this run (`{name, args_summary,
  outcome}`), no full tool outputs.

Rationale: reflection's job is "did *this* turn produce something worth
remembering?" — past turns either already triggered their own reflection
or are irrelevant. Token cost stays bounded regardless of conversation
length.

Plus the standard memory snapshot in the system prompt (same
`memory_inject` middleware as main runs) so the agent can dedupe against
existing items.

### Reflection agent configuration

- **Model:** cheap/fast model (configurable; default Haiku or similar).
  No prompt cache sharing with the main run is fine — it's a small,
  bounded call.
- **Tools:** only `memory_search`, `memory_save`, `memory_update`. No
  conversation tools, no sandbox, no MCP.
- **System prompt:** memory-snapshot + a focused reflection instruction
  (move `REFLECTION_PROMPT` from `cubebox/prompts/reflection.py` to
  `cubebox/prompts/reflection_system.py`, expand to set context).
- **No checkpointer.** Reflection has no resume semantics.
- **No subscriber.** No streaming UI; we only care about the tool calls
  it makes.
- **Timeout:** 30 seconds. If reflection hangs, drop it.

### Output capture

The runner subscribes to the reflection agent's events long enough to
collect `tool_execution_end` events for `memory_save` and `memory_update`.
For each, capture: `{op: "save"|"update", memory_id, type, scope,
content_preview}`.

When the reflection agent ends, build a `UserEvent` payload:

```json
{
  "type": "memory_updated",
  "conversation_id": "conv_...",
  "run_id": "run_...",
  "items": [
    {"op": "save", "memory_id": "mem_...", "type": "preference",
     "scope": "personal", "content_preview": "user prefers Chinese in chat"}
  ]
}
```

If `items` is empty, no event is published — silence means nothing was
worth saving.

### Existing `MemoryItem` source tracking

Add `MemorySourceType.REFLECTION = "reflection"`. The reflection agent
calls `memory_save` / `memory_update` via the same service; those
services need to learn the call is reflection-sourced.

Two options for plumbing the source signal:
- **A.** New optional `source_type` argument on the `memory_save`/
  `memory_update` tool schemas, filled in by the reflection agent's
  system prompt.
- **B.** A `ContextVar` set by `ReflectionRunner` around the agent
  invocation; the memory service reads it when persisting.

Prefer **B**: keeps the public tool schema clean and prevents abuse from
the main agent ever claiming to be reflection-sourced.

### Idempotency / concurrency

- One reflection per main run. If the same run completes twice (e.g. due
  to checkpointer replay), the second trigger is a no-op (gated by a
  `reflected_run_ids` set keyed by `run_id`, evicted after 1 hour).
- Reflection tasks from different runs proceed independently. No global
  serialization.

### Failure modes

| Failure | Behavior |
|---|---|
| LLM timeout / error | Log + drop. No retry. No user-visible signal. |
| Memory service write error | Log + drop. The events captured up to the failure are not published. |
| Main run was aborted / errored | `ReflectionRunner.reflect` not called at all. |
| HITL suspend | Not called. Reflection only runs on natural completion. |
| User logged out mid-reflection | Task continues to completion; memory still saved; event published to bus but no SSE subscriber to deliver it. Durable record stays in `user_event` table for next session. |

## Component 2: UserEvent persistence + bus

### Table: `user_events`

```python
class UserEvent(CubeboxBase, table=True):
    _PREFIX: ClassVar[str] = PREFIX_USER_EVENT  # "uev_"
    __tablename__ = "user_events"
    __table_args__ = (
        Index("ix_user_events_user_id_created", "user_id", "created_at"),
        Index("ix_user_events_unread", "user_id", "read_at"),
    )

    user_id: str = Field(foreign_key="users.id", max_length=20)
    workspace_id: str | None = Field(default=None, foreign_key="workspaces.id", max_length=20)
    type: UserEventType  # StrEnum: MEMORY_UPDATED, ...
    payload: dict[str, Any] = Field(sa_column=Column(JSONB))
    read_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
```

`workspace_id` is nullable because some events (memory at personal scope,
user-level notifications) aren't workspace-bound.

`read_at` enables "mark as read" semantics for future event types that
need it. For memory markers, the frontend can mark read on render.

### In-memory bus

`UserEventBus` (singleton, in-process):
- `publish(event: UserEventDTO) -> None` — writes to DB *and* fans out
  to live subscribers.
- `subscribe(user_id) -> async iterator` — yields events as they're
  published. On subscribe, optionally yields events created since
  `last_seen_id` (passed from the SSE request) so reconnects don't
  drop events.

Pubsub is in-process: cubebox is single-instance for now. When we move
to multi-instance, swap the bus for Redis pub/sub behind the same
interface — no caller changes.

### SSE endpoint: `GET /api/v1/user/events`

- Authenticates the current user via the existing cookie middleware.
- Query param: `?since=<user_event_id>` — replay events created after
  that id; client sends the highest id it has seen.
- Emits `event: <type>` lines with `data: <json>` payload.
- Heartbeat `: ping` every 30s to keep the connection alive through
  proxies.
- Closes on user logout / cookie invalidation.

Scope: per-user. Workspace filtering happens client-side on payload
inspection (events typically include `workspace_id` when applicable).

## Component 3: Frontend integration

### Global subscriber

A single `useUserEvents()` hook mounted at the app shell (above the
conversation route) opens the SSE connection once per session. Last
seen `id` is persisted in `localStorage` for reconnect replay.

Incoming events dispatched by `type` to handlers:

- `memory_updated` → push to a Zustand `memoryEventStore` keyed by
  `conversation_id`.

### Memory marker UI

When `memoryEventStore[conversation_id]` has entries, render an inline
chip in the conversation timeline after the matched `run_id`:

```
[user]: 我比较喜欢简洁的回答
[agent]: 收到。...
        💭 已记住：用户偏好简洁回答    ← inline chip, dim, clickable
```

Clicking the chip:
- Opens the memory panel filtered to the new item.
- Marks the event as read (POST `/api/v1/user/events/{id}/read`).

If the conversation isn't currently visible (user navigated away), surface
a non-intrusive toast: "Memory updated. View →".

Detailed visual design out of scope for this spec — design pass happens in
the implementation plan with the frontend-design skill.

## Migration from v1

The v1 PR (#187) already merged the `on_run_end` hook into cubepi. v2
**removes the cubebox side** of v1:

- Delete `backend/cubebox/middleware/reflection.py` (the
  `ReflectionMiddleware` class).
- Delete the `ReflectionMiddleware()` instance from the run_manager
  middleware list.
- Move `REFLECTION_PROMPT` from `cubebox/prompts/reflection.py` and
  expand into a system-prompt-style instruction for the new reflection
  agent.
- Delete `backend/tests/unit/test_reflection_middleware.py`.

The cubepi `on_run_end` hook stays — it's a general-purpose mechanism
that may be useful for other features (e.g. cost accumulation, run-level
audit logging). v2 just doesn't use it.

## Open questions

1. **Reflection model selection.** v2 reuses the main run's resolved
   provider/model (no separate config). Tracked as a known v1 limitation
   per codex P2 review on 68fca5c0 — billing now correctly attributes
   reflection token cost to the same scope as the main run, so the
   structural piece is in place. Follow-up: add `memory.reflection.
   model_id` config + a `LLMFactory.resolve_provider_for_model(model_id)`
   path so reflection can default to a cheap model (Haiku-tier). Until
   that lands, deployments using a heavy reasoning model as the chat
   default should be aware that every successful turn adds one short
   reflection LLM call billed at the same rate.
2. **`user_events` retention.** Do we trim old rows? Memory markers stay
   useful as historical conversation context, so probably indefinite
   retention initially. Reassess after dogfooding.
3. **Reflection budget.** No hard quota in v2. If reflection cost becomes
   a problem, gate behind `MaxTurns(1)` middleware or skip reflection on
   short conversations (heuristic: skip if user message < 5 tokens).

## Out of scope (deferred)

- **Cross-conversation memory recall UI.** v2 only handles "save"; the
  read-side improvements (search across conversation history, "have we
  talked about X?") remain pain-point #1 in the original triage and need
  their own design.
- **Workspace-scoped reflection.** All v2 reflections produce
  personal-scope memories unless the user explicitly opted in for sharing
  (same as v1 behavior).
- **Multi-instance event bus.** In-process pub/sub is enough for now.
  When cubebox scales horizontally, swap `UserEventBus` impl.
- **Unified user-event notification system.** v2's chip is a permanent
  per-conversation count (refreshed by SSE events), not an unread inbox.
  The `user_events` table + SSE pipeline still produces durable events
  with `read_at IS NULL` semantics, but the UI no longer treats them as
  read/unread — `read_at` is unused after this iteration. A follow-up
  design will define the unified notification surface (cross-feature
  inbox, badge counts, dismissal flows) that consumes `user_events`
  alongside future event sources (background jobs, mentions, etc.).
  Until then, frontend treats events purely as "refresh now" signals
  for whichever count/list happens to be on screen.

## Summary of changes

| Layer | Change |
|---|---|
| `cubepi` | None (v1's `on_run_end` hook stays, just unused by cubebox) |
| Backend models | New `UserEvent` model + migration; `MemoryItem.MemorySourceType.REFLECTION` enum value |
| Backend services | New `ReflectionRunner`, `UserEventBus`, `MemoryItem.source_type` plumbing via ContextVar |
| Backend API | New `/api/v1/user/events` SSE endpoint + `POST /api/v1/user/events/{id}/read` |
| Backend run_manager | Schedule `ReflectionRunner.reflect` after `AgentEndEvent`; remove `ReflectionMiddleware` from middleware stack |
| Frontend | `useUserEvents` hook at app shell; `memoryEventStore`; inline memory chip in conversation timeline; toast fallback |
| Tests | Unit: ReflectionRunner happy path + skip on aborted run; Integration: full reflection flow with FauxProvider; E2E: send message expressing preference → see memory chip |
