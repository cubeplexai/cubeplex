# Token Usage Info Panel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show per-turn and session-level token usage (input/output tokens, cache hit rate, context window %) in a collapsible panel below the last assistant message.

**Architecture:** Backend accumulates per-run usage in RunManager and attaches a summary to the `done` SSE event. Bootstrap endpoint also returns session-level totals. Frontend stores usage from `done` event and renders a collapsible `TokenUsageBar` component at the bottom of the last assistant message.

**Tech Stack:** Python/FastAPI (backend), TypeScript/React/Zustand/Tailwind (frontend), SQLAlchemy for session aggregation query.

**Worktree:** `/home/chris/cubeplex/.worktrees/feat/token-usage-panel` (ports 8031/3031)

---

### Task 1: Attach `_cubeplex_context_window` to LLM instances

Add context window size to the metadata already attached to LLM instances by `LLMFactory.create()`, so downstream code (RunManager) can read it without re-resolving model config.

**Files:**
- Modify: `backend/cubeplex/llm/factory.py:481-483` (openai-completions path) and `:528-530` (anthropic path)

- [ ] **Step 1: Add `_cubeplex_context_window` after existing metadata lines**

In `backend/cubeplex/llm/factory.py`, after line 483 (`llm._cubeplex_model_cost = ...`), add:

```python
llm._cubeplex_context_window = model_config.context_window  # type: ignore[attr-defined]
```

And after line 530 (`anthropic_llm._cubeplex_model_cost = ...`), add:

```python
anthropic_llm._cubeplex_context_window = model_config.context_window  # type: ignore[attr-defined]
```

- [ ] **Step 2: Verify type-check passes**

Run: `cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/backend && uv run mypy cubeplex/llm/factory.py`
Expected: `Success: no issues found`

- [ ] **Step 3: Commit**

```bash
git add cubeplex/llm/factory.py
git commit -m "feat(llm): attach _cubeplex_context_window to LLM instances"
```

---

### Task 2: Accumulate turn usage and enrich DoneEvent in RunManager

Intercept `UsageEvent`s during streaming to build a turn-level accumulator, then attach the summary (turn + session + context_window) to the `DoneEvent`.

**Files:**
- Modify: `backend/cubeplex/streams/run_manager.py:487-503` (`publish_stream_event`) and `:845-861` (done event emission)
- Test: `backend/tests/unit/streams/test_done_usage.py` (create)

- [ ] **Step 1: Write test for done event usage payload**

Create `backend/tests/unit/streams/__init__.py` (empty) and `backend/tests/unit/streams/test_done_usage.py`:

```python
"""DoneEvent carries accumulated turn usage."""

from cubeplex.agents.schemas import DoneEvent, UsageEvent


def test_done_event_accepts_usage_payload() -> None:
    """DoneEvent.data can carry a usage dict."""
    done = DoneEvent(
        timestamp="2026-05-11T00:00:00Z",
        data={
            "usage": {
                "turn": {
                    "input_tokens": 200,
                    "output_tokens": 50,
                    "cache_read_tokens": 150,
                    "cache_write_tokens": 30,
                },
                "session": {
                    "total_input_tokens": 1000,
                    "total_output_tokens": 400,
                },
                "context_window": 128000,
            }
        },
    )
    assert done.data["usage"]["turn"]["input_tokens"] == 200
    assert done.data["usage"]["context_window"] == 128000


def test_usage_event_accumulation() -> None:
    """Verify that multiple UsageEvent payloads can be summed."""
    events = [
        UsageEvent(
            timestamp="t1",
            data={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 80,
                "cache_write_tokens": 10,
            },
        ),
        UsageEvent(
            timestamp="t2",
            data={
                "input_tokens": 50,
                "output_tokens": 30,
                "cache_read_tokens": 40,
                "cache_write_tokens": 5,
            },
        ),
    ]
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    for e in events:
        for key in totals:
            totals[key] += e.data.get(key, 0)

    assert totals == {
        "input_tokens": 150,
        "output_tokens": 50,
        "cache_read_tokens": 120,
        "cache_write_tokens": 15,
    }
```

- [ ] **Step 2: Run test to verify it passes** (these are schema/logic tests, no impl change needed)

Run: `cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/backend && uv run pytest tests/unit/streams/test_done_usage.py -v`
Expected: PASS (DoneEvent already accepts arbitrary `data` dicts)

- [ ] **Step 3: Add turn usage accumulator and context_window extraction in `_execute_run`**

In `backend/cubeplex/streams/run_manager.py`, add a turn accumulator dict and context_window variable at the top of the `try` block inside `_execute_run` (right after `tool_delta_context` on line 447):

```python
        turn_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
```

Then modify `publish_stream_event` (around line 487) to intercept usage events and accumulate:

After the existing `publish_stream_event` function definition (which ends around line 503), wrap it so that `UsageEvent`s also accumulate. The cleanest approach: add accumulation logic inside `publish_stream_event` before the existing `publish_event` call. Since `publish_stream_event` currently only handles `text_delta` and falls through to `publish_event`, add a new branch:

In `publish_stream_event`, before the final `await publish_event(sse_event)` line (line 503), add:

```python
            if sse_event.type == "usage":
                for key in turn_usage:
                    turn_usage[key] += sse_event.data.get(key, 0)
```

Then extract context_window from the LLM after it's created. After line 578 (`llm = await LLMFactory(...).create_default()`), add:

```python
            context_window: int = getattr(llm, "_cubeplex_context_window", 0)
```

Note: `create_default()` may return a `RunnableWithFallbacks` wrapper. The `_cubeplex_context_window` attribute is on the inner model. Handle this:

```python
            _inner = getattr(llm, "runnable", llm)
            context_window: int = getattr(_inner, "_cubeplex_context_window", 0)
```

- [ ] **Step 4: Query session totals and build enriched DoneEvent**

Replace the DoneEvent emission block (lines 857-861) with:

```python
            # --- Aggregate session-level token totals ---
            session_usage = {"total_input_tokens": 0, "total_output_tokens": 0}
            try:
                from sqlalchemy import func as sa_func
                from sqlalchemy import select as sa_select

                from cubeplex.db.engine import async_session_maker
                from cubeplex.models.billing import BillingEvent, LlmBillingEvent

                async with async_session_maker() as billing_session:
                    row = (
                        await billing_session.execute(
                            sa_select(
                                sa_func.coalesce(
                                    sa_func.sum(LlmBillingEvent.input_tokens), 0
                                ),
                                sa_func.coalesce(
                                    sa_func.sum(LlmBillingEvent.output_tokens), 0
                                ),
                            ).join(
                                BillingEvent,
                                LlmBillingEvent.billing_event_id == BillingEvent.id,
                            ).where(
                                BillingEvent.conversation_id == conversation_id,
                            )
                        )
                    ).one()
                    session_usage["total_input_tokens"] = int(row[0])
                    session_usage["total_output_tokens"] = int(row[1])
            except Exception:
                logger.warning("Failed to query session usage for done event")

            await self._append_event(
                run_id,
                conversation_id,
                DoneEvent(
                    timestamp=datetime.now(UTC).isoformat(),
                    data={
                        "usage": {
                            "turn": dict(turn_usage),
                            "session": session_usage,
                            "context_window": context_window,
                        }
                    },
                ),
            )
```

- [ ] **Step 5: Run existing tests to verify nothing breaks**

Run: `cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/backend && uv run pytest tests/unit/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add cubeplex/streams/run_manager.py cubeplex/llm/factory.py tests/unit/streams/
git commit -m "feat(stream): accumulate turn usage and enrich DoneEvent with usage summary"
```

---

### Task 3: Add `usage_summary` to bootstrap endpoint

Return session-level token totals and context_window in the bootstrap response so the frontend can show stats after a page refresh.

**Files:**
- Modify: `backend/cubeplex/api/routes/v1/conversations.py:588-648`

- [ ] **Step 1: Add session usage query to bootstrap handler**

In `backend/cubeplex/api/routes/v1/conversations.py`, inside `get_conversation_bootstrap`, before the `return` statement (line 643), add:

```python
    # --- Session-level token usage for the usage panel ---
    usage_summary: dict[str, object] = {
        "session": {"total_input_tokens": 0, "total_output_tokens": 0},
        "context_window": 0,
    }
    try:
        from sqlalchemy import func as sa_func
        from sqlalchemy import select as sa_select

        from cubeplex.models.billing import BillingEvent, LlmBillingEvent

        row = (
            await session.execute(
                sa_select(
                    sa_func.coalesce(sa_func.sum(LlmBillingEvent.input_tokens), 0),
                    sa_func.coalesce(sa_func.sum(LlmBillingEvent.output_tokens), 0),
                )
                .join(
                    BillingEvent,
                    LlmBillingEvent.billing_event_id == BillingEvent.id,
                )
                .where(BillingEvent.conversation_id == conversation_id)
            )
        ).one()
        usage_summary["session"] = {
            "total_input_tokens": int(row[0]),
            "total_output_tokens": int(row[1]),
        }

        # Resolve context_window from the default model config
        from cubeplex.llm.config import LLMConfig
        from cubeplex.llm.factory import LLMFactory

        factory = LLMFactory(session=session, org_id=ctx.org_id)
        provider_name, model_id = await factory.get_default_model()
        model_cfg = factory.get_model_config(provider_name, model_id)
        usage_summary["context_window"] = model_cfg.context_window
    except Exception:
        pass  # Non-critical; frontend degrades gracefully
```

Then add `"usage_summary": usage_summary` to the return dict (line 643):

```python
    return {
        "messages": history["messages"],
        "total": history["total"],
        "active_run": active_run_payload,
        "last_run_status": last_run_status,
        "usage_summary": usage_summary,
    }
```

- [ ] **Step 2: Run type-check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/backend && uv run mypy cubeplex/api/routes/v1/conversations.py`
Expected: `Success`

- [ ] **Step 3: Commit**

```bash
git add cubeplex/api/routes/v1/conversations.py
git commit -m "feat(api): add usage_summary to conversation bootstrap response"
```

---

### Task 4: Frontend types — extend DoneEvent and add usage types

Add TypeScript types for usage data in the core package.

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts:48-58` (AgentEventType) and `:152-155` (DoneEvent)
- Modify: `frontend/packages/core/src/api/runStreams.ts:17-22` (ConversationBootstrap)

- [ ] **Step 1: Add `'usage'` to AgentEventType and add usage interfaces**

In `frontend/packages/core/src/types/events.ts`:

Add `| 'usage'` to `AgentEventType` (line 58):

```typescript
export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_call_delta'
  | 'tool_result'
  | 'artifact'
  | 'error'
  | 'done'
  | 'citation'
  | 'status'
  | 'usage'
```

Add usage type definitions after the `StatusEvent` interface (after line 162):

```typescript
export interface TurnUsage {
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
}

export interface SessionUsage {
  total_input_tokens: number
  total_output_tokens: number
}

export interface UsageSummary {
  turn: TurnUsage
  session: SessionUsage
  context_window: number
}
```

- [ ] **Step 2: Extend ConversationBootstrap in runStreams.ts**

In `frontend/packages/core/src/api/runStreams.ts`, add `usage_summary` to the `ConversationBootstrap` interface:

```typescript
export interface ConversationBootstrap {
  messages: Message[]
  total: number
  active_run: ActiveRunBootstrap | null
  last_run_status: 'stale' | null
  usage_summary?: {
    session: { total_input_tokens: number; total_output_tokens: number }
    context_window: number
  }
}
```

- [ ] **Step 3: Export new types from core index**

Check `frontend/packages/core/src/index.ts` and ensure `TurnUsage`, `SessionUsage`, `UsageSummary` are re-exported from `'./types'`. If types are barrel-exported via `export * from './types'`, no change needed.

- [ ] **Step 4: Build core and type-check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/frontend && pnpm --filter @cubeplex/core build && pnpm type-check`
Expected: No type errors

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/frontend
git add packages/core/src/types/events.ts packages/core/src/api/runStreams.ts
git commit -m "feat(core): add token usage types and extend DoneEvent/bootstrap"
```

---

### Task 5: Store usage state in messageStore

Handle `done` event usage payload and bootstrap `usage_summary` in the Zustand store.

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`

- [ ] **Step 1: Add usage state fields to the store interface**

In `frontend/packages/core/src/stores/messageStore.ts`, add to `MessageStore` interface (after `toolResultMap`):

```typescript
  turnUsage: Record<string, import('../types').TurnUsage | null>
  sessionUsage: Record<string, import('../types').SessionUsage | null>
  contextWindow: Record<string, number | null>
```

- [ ] **Step 2: Initialize these fields in the store creation**

In the `create<MessageStore>` call (line 638), add initial values after `toolResultMap: {}`:

```typescript
  turnUsage: {},
  sessionUsage: {},
  contextWindow: {},
```

- [ ] **Step 3: Handle usage in the `done` event branch of `send()`**

In the `send()` method, in the `else if (event.type === 'done')` block (line 769), replace:

```typescript
        } else if (event.type === 'done') {
          set({
            lastAppliedEventId: nextEventId(get().lastAppliedEventId, event.event_id),
          })
          sawDone = true
          break
        }
```

with:

```typescript
        } else if (event.type === 'done') {
          const usage = (event.data as Record<string, unknown>).usage as
            | import('../types').UsageSummary
            | undefined
          const usageUpdate: Partial<MessageStore> = {
            lastAppliedEventId: nextEventId(get().lastAppliedEventId, event.event_id),
          }
          if (usage) {
            usageUpdate.turnUsage = {
              ...get().turnUsage,
              [conversationId]: usage.turn,
            }
            usageUpdate.sessionUsage = {
              ...get().sessionUsage,
              [conversationId]: usage.session,
            }
            usageUpdate.contextWindow = {
              ...get().contextWindow,
              [conversationId]: usage.context_window,
            }
          }
          set(usageUpdate)
          sawDone = true
          break
        }
```

- [ ] **Step 4: Do the same for the `consumeRunStream` done branch**

In `consumeRunStream` (the `done` branch around line 618), apply the same pattern:

```typescript
      } else if (event.type === 'done') {
        const usage = (event.data as Record<string, unknown>).usage as
          | import('../types').UsageSummary
          | undefined
        const usageUpdate: Partial<MessageStore> = {
          lastAppliedEventId: nextEventId(get().lastAppliedEventId, event.event_id),
        }
        if (usage) {
          usageUpdate.turnUsage = {
            ...get().turnUsage,
            [conversationId]: usage.turn,
          }
          usageUpdate.sessionUsage = {
            ...get().sessionUsage,
            [conversationId]: usage.session,
          }
          usageUpdate.contextWindow = {
            ...get().contextWindow,
            [conversationId]: usage.context_window,
          }
        }
        set(usageUpdate)
        sawDone = true
        break
      }
```

- [ ] **Step 5: Populate from bootstrap in `loadMessages()`**

In `loadMessages()`, after loading the bootstrap (line 657), extract `usage_summary` and populate:

After `hydrateCitationsFromHistory(conversationId, messages)` (line 672), add:

```typescript
      const usageSummary = bootstrap.usage_summary
      const newTurnUsage = { ...get().turnUsage }
      const newSessionUsage = { ...get().sessionUsage, [conversationId]: null as import('../types').SessionUsage | null }
      const newContextWindow = { ...get().contextWindow, [conversationId]: null as number | null }
      if (usageSummary) {
        newSessionUsage[conversationId] = usageSummary.session
        newContextWindow[conversationId] = usageSummary.context_window
      }
```

Then in the `set()` call (line 677), add:

```typescript
        turnUsage: newTurnUsage,
        sessionUsage: newSessionUsage,
        contextWindow: newContextWindow,
```

- [ ] **Step 6: Clear turnUsage on new `send()`**

In `send()`, inside the initial `set()` call (line 724), add:

```typescript
      turnUsage: { ...get().turnUsage, [conversationId]: null },
```

- [ ] **Step 7: Build and type-check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/frontend && pnpm --filter @cubeplex/core build && pnpm type-check`
Expected: No type errors

- [ ] **Step 8: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/frontend
git add packages/core/src/stores/messageStore.ts
git commit -m "feat(store): handle token usage from done event and bootstrap"
```

---

### Task 6: Create TokenUsageBar component

Build the collapsible token usage panel and wire it into the message list.

**Files:**
- Create: `frontend/packages/web/components/chat/TokenUsageBar.tsx`
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
- Modify: `frontend/packages/web/hooks/useMessages.ts`
- Modify: `frontend/packages/web/messages/en.json` and `zh.json`

- [ ] **Step 1: Add i18n keys**

In `frontend/packages/web/messages/en.json`, add to the `"chat"` section:

```json
    "tokenUsage": "Token Usage",
    "turnLabel": "This Turn",
    "sessionLabel": "Session Total",
    "inputTokens": "Input",
    "outputTokens": "Output",
    "cacheHitRate": "Cache Hit",
    "totalTokens": "Total Tokens",
    "contextWindow": "Context"
```

In `frontend/packages/web/messages/zh.json`, add to the `"chat"` section:

```json
    "tokenUsage": "Token 用量",
    "turnLabel": "本轮",
    "sessionLabel": "会话累计",
    "inputTokens": "输入",
    "outputTokens": "输出",
    "cacheHitRate": "缓存命中",
    "totalTokens": "总 Token",
    "contextWindow": "Context"
```

- [ ] **Step 2: Expose usage from useMessages hook**

In `frontend/packages/web/hooks/useMessages.ts`, add selectors for usage state:

```typescript
  const turnUsage = useMessageStore((s) => s.turnUsage[conversationId] ?? null)
  const sessionUsage = useMessageStore((s) => s.sessionUsage[conversationId] ?? null)
  const contextWindow = useMessageStore((s) => s.contextWindow[conversationId] ?? null)
```

Add them to the return object:

```typescript
  return {
    messages,
    isStreaming: isStreamingThis,
    statusPhase,
    mainStream,
    subAgentStreams,
    todos,
    error,
    toolResultMap,
    turnUsage,
    sessionUsage,
    contextWindow,
  }
```

- [ ] **Step 3: Create TokenUsageBar component**

Create `frontend/packages/web/components/chat/TokenUsageBar.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { ChevronDown, ChevronRight, BarChart3 } from 'lucide-react'
import type { TurnUsage, SessionUsage } from '@cubeplex/core'

function formatTokenCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function progressColor(pct: number): string {
  if (pct >= 80) return 'bg-red-500'
  if (pct >= 50) return 'bg-amber-500'
  return 'bg-emerald-500'
}

interface TokenUsageBarProps {
  turnUsage: TurnUsage | null
  sessionUsage: SessionUsage | null
  contextWindow: number | null
}

export function TokenUsageBar({
  turnUsage,
  sessionUsage,
  contextWindow,
}: TokenUsageBarProps) {
  const t = useTranslations('chat')
  const [isExpanded, setIsExpanded] = useState(false)

  if (!turnUsage && !sessionUsage) return null

  const cacheHitRate =
    turnUsage && turnUsage.input_tokens > 0
      ? (turnUsage.cache_read_tokens / turnUsage.input_tokens) * 100
      : null

  const sessionTotal = sessionUsage
    ? sessionUsage.total_input_tokens + sessionUsage.total_output_tokens
    : null

  const ctxPct =
    sessionUsage && contextWindow && contextWindow > 0
      ? ((sessionUsage.total_input_tokens + sessionUsage.total_output_tokens) /
          contextWindow) *
        100
      : null

  return (
    <div className="mt-1">
      <button
        type="button"
        onClick={() => setIsExpanded((prev) => !prev)}
        className="flex items-center gap-1.5 text-xs text-muted-foreground/60
          hover:text-muted-foreground transition-colors cursor-pointer"
      >
        <span className="text-muted-foreground/40">
          {isExpanded ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
        </span>
        <BarChart3 className="size-3" />
        <span>{t('tokenUsage')}</span>
      </button>

      {isExpanded && (
        <div
          className="mt-2 text-xs text-muted-foreground bg-muted/30
            border border-border/50 rounded-lg px-3 py-2.5 space-y-3
            max-w-xs"
        >
          {turnUsage && (
            <div>
              <div className="font-medium text-foreground/70 mb-1">
                {t('turnLabel')}
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                <span>{t('inputTokens')}</span>
                <span className="text-right font-mono">
                  {formatTokenCount(turnUsage.input_tokens)}
                </span>
                <span>{t('outputTokens')}</span>
                <span className="text-right font-mono">
                  {formatTokenCount(turnUsage.output_tokens)}
                </span>
                <span>{t('cacheHitRate')}</span>
                <span className="text-right font-mono">
                  {cacheHitRate !== null ? `${cacheHitRate.toFixed(1)}%` : '—'}
                </span>
              </div>
            </div>
          )}

          {sessionUsage && (
            <div>
              <div className="font-medium text-foreground/70 mb-1">
                {t('sessionLabel')}
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                <span>{t('totalTokens')}</span>
                <span className="text-right font-mono">
                  {sessionTotal !== null ? formatTokenCount(sessionTotal) : '—'}
                </span>
              </div>
              {ctxPct !== null && (
                <div className="mt-1.5">
                  <div className="flex items-center justify-between mb-0.5">
                    <span>{t('contextWindow')}</span>
                    <span className="font-mono">{ctxPct.toFixed(1)}%</span>
                  </div>
                  <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${progressColor(ctxPct)}`}
                      style={{ width: `${Math.min(ctxPct, 100)}%` }}
                    />
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Wire TokenUsageBar into MessageList**

In `frontend/packages/web/components/chat/MessageList.tsx`:

Add import:

```typescript
import { TokenUsageBar } from './TokenUsageBar'
```

Destructure new fields from `useMessages`:

```typescript
  const {
    messages,
    isStreaming,
    statusPhase,
    mainStream,
    subAgentStreams,
    todos,
    error,
    toolResultMap,
    turnUsage,
    sessionUsage,
    contextWindow,
  } = useMessages(conversationId)
```

Add the `TokenUsageBar` after the streaming `AssistantMessage` block (after line 225, after `</AssistantMessage>`), inside the same conditional:

```tsx
        {!isStreaming &&
          (turnUsage || sessionUsage) &&
          (messages ?? []).some((m) => m.role === 'assistant') && (
          <div className="flex justify-start gap-2.5">
            <div className="shrink-0 w-6 h-6" />
            <div className="flex-1 max-w-[75%]">
              <TokenUsageBar
                turnUsage={turnUsage}
                sessionUsage={sessionUsage}
                contextWindow={contextWindow}
              />
            </div>
          </div>
        )}
```

This renders the bar only when streaming is done, there is usage data, **and** at least one assistant message exists in history (prevents phantom zero panel on empty conversations after bootstrap). Aligned with the assistant message's content column (the `w-6` spacer matches the bot icon width).

- [ ] **Step 5: Build and type-check**

Run: `cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/frontend && pnpm --filter @cubeplex/core build && pnpm type-check`
Expected: No type errors

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/frontend
git add packages/web/components/chat/TokenUsageBar.tsx \
       packages/web/components/chat/MessageList.tsx \
       packages/web/hooks/useMessages.ts \
       packages/web/messages/en.json \
       packages/web/messages/zh.json
git commit -m "feat(ui): add TokenUsageBar component with collapsible token usage panel"
```

---

### Task 7: Visual verification and polish

Start both servers in the worktree, send a message, and verify the token usage panel renders correctly.

**Files:** None (manual testing)

- [ ] **Step 1: Start backend**

```bash
cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/backend
python main.py
```

Confirm it starts on port 8031 (from `.worktree.env`).

- [ ] **Step 2: Start frontend**

```bash
cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/frontend
pnpm dev
```

Confirm it starts on port 3031.

- [ ] **Step 3: Test in browser**

Open `http://localhost:3031`. Send a message in a conversation. After the AI responds:

1. Verify the `▸ Token 用量` button appears below the last assistant message (bottom-left).
2. Click it — verify the panel expands showing:
   - This Turn: input tokens, output tokens, cache hit rate
   - Session Total: total tokens, context window progress bar
3. Click again — verify it collapses.
4. Send another message — verify the panel updates with new values.
5. Refresh the page — verify session totals persist (from bootstrap).

- [ ] **Step 4: Run full type-check and lint**

```bash
cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/backend && make check
cd /home/chris/cubeplex/.worktrees/feat/token-usage-panel/frontend && pnpm type-check
```

- [ ] **Step 5: Final commit if any polish needed**

```bash
git add -A && git commit -m "fix(ui): polish token usage panel"
```
