# Sandbox Confirm HITL — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the frontend to handle `sandbox_confirm_request` / `sandbox_confirm_resolved` SSE events, render an inline approve/deny card in the chat stream, and submit the user's decision to the backend.

**Architecture:** The store holds `pendingConfirmMap: Record<tool_call_id, PendingConfirm>` set on `sandbox_confirm_request` and cleared on `sandbox_confirm_resolved` or after the user submits. `ToolCallItem` for `execute` reads the map and renders `SandboxConfirmCard` (approve/deny + countdown) replacing the normal pending spinner. On submit, the API posts to `/api/v1/conversations/{id}/sandbox-confirm/{question_id}`. No E2E — backend requires a live HITL channel; unit tests cover store logic.

**Tech Stack:** Next.js 15, React 19, Zustand, Tailwind CSS, shadcn/ui, Vitest, TypeScript strict

---

## Files

| Action | Path | Responsibility |
|---|---|---|
| Modify | `frontend/packages/core/src/api/stream.ts` | Add `submitSandboxConfirm` |
| ~~Modify~~ | `frontend/packages/core/src/api/index.ts` | No change needed — already uses `export * from './stream'` |
| Modify | `frontend/packages/core/src/stores/messageStore.ts` | Add `pendingConfirmMap` state + event handlers |
| Modify | `frontend/packages/core/src/stores/index.ts` | Re-export `PendingConfirm` type |
| Create | `frontend/packages/core/__tests__/stores/messageStore.sandboxConfirm.test.ts` | Vitest unit tests |
| Create | `frontend/packages/web/components/chat/SandboxConfirmCard.tsx` | Approve/deny card + countdown |
| Modify | `frontend/packages/web/components/chat/ToolCallItem.tsx` | Accept `pendingConfirm` prop; suppress spinner when set |
| Modify | `frontend/packages/web/components/chat/ToolCallGroup.tsx` | Thread `pendingConfirmMap` |
| Modify | `frontend/packages/web/components/chat/AssistantMessage.tsx` | Thread `pendingConfirmMap` |
| Modify | `frontend/packages/web/components/chat/MessageList.tsx` | Read from store; inject submit callback |

**Note on subagent tool calls:** `SubAgentCard` renders `ToolCallItem` directly but is NOT wired — the backend never emits sandbox confirmations for subagent tools (known limitation, tracked separately).

---

## Task 1: API method — `submitSandboxConfirm`

**Files:**
- Modify: `frontend/packages/core/src/api/stream.ts`
- Modify: `frontend/packages/core/src/api/index.ts`

- [ ] **Step 1: Add the function after `cancelSteer` in `stream.ts`**

```typescript
export interface SandboxConfirmResponse {
  status: 'delivered' | 'published' | 'no_active_run'
  run_id: string | null
}

export async function submitSandboxConfirm(
  client: ApiClient,
  conversationId: string,
  questionId: string,
  decision: 'approve' | 'deny',
  reason?: string,
): Promise<SandboxConfirmResponse> {
  const res = await client.post(
    `/api/v1/conversations/${conversationId}/sandbox-confirm/${questionId}`,
    { decision, reason: reason ?? null },
  )
  if (!res.ok) {
    throw new Error(`Failed to submit sandbox confirm: HTTP ${res.status}`)
  }
  return (await res.json()) as SandboxConfirmResponse
}
```

- [ ] **Step 2: Verify re-export (no change needed)**

`api/index.ts` uses `export * from './stream'` — `submitSandboxConfirm` and `SandboxConfirmResponse` are already re-exported automatically. Skip this step.

- [ ] **Step 3: Verify TypeScript**

```bash
cd frontend && pnpm --filter @cubeplex/core run type-check
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/api/stream.ts \
        frontend/packages/core/src/api/index.ts
git commit -m "feat(sandbox-ui): submitSandboxConfirm API method"
```

---

## Task 2: Store state — `pendingConfirmMap`

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`
- Modify: `frontend/packages/core/src/stores/index.ts`
- Create: `frontend/packages/core/__tests__/stores/messageStore.sandboxConfirm.test.ts`

### Step 2a — Write failing tests first

- [ ] **Step 1: Create the test file**

Path: `frontend/packages/core/__tests__/stores/messageStore.sandboxConfirm.test.ts`
(matches existing test location: `__tests__/lib/toolName.test.ts`)

```typescript
import { describe, it, expect, beforeEach } from 'vitest'
import { useMessageStore } from '../../src/stores/messageStore'
import type { AgentEvent } from '../../src/types'

function makeRequestEvent(overrides?: {
  question_id?: string; tool_call_id?: string; command?: string
  matched_pattern?: string | null; timeout_seconds?: number | null
}): AgentEvent {
  return {
    type: 'sandbox_confirm_request',
    event_id: '1000-1',
    timestamp: new Date().toISOString(),
    agent_id: null,
    agent_name: null,
    data: {
      question_id: overrides?.question_id ?? 'qid-1',
      tool_call_id: overrides?.tool_call_id ?? 'tc-1',
      command: overrides?.command ?? 'rm -rf /tmp/x',
      matched_pattern: overrides?.matched_pattern ?? 'rm *',
      timeout_seconds: overrides?.timeout_seconds ?? 180,
    },
  } as unknown as AgentEvent
}

function makeResolvedEvent(questionId = 'qid-1'): AgentEvent {
  return {
    type: 'sandbox_confirm_resolved',
    event_id: '1000-2',
    timestamp: new Date().toISOString(),
    agent_id: null,
    agent_name: null,
    data: { question_id: questionId, decision: 'approve', cancelled: false, timed_out: false, reason: null },
  } as unknown as AgentEvent
}

beforeEach(() => {
  useMessageStore.setState({ pendingConfirmMap: {}, lastAppliedEventId: null })
})

describe('sandbox_confirm_request', () => {
  it('adds entry to pendingConfirmMap keyed by tool_call_id', () => {
    useMessageStore.getState().__applyEvent(makeRequestEvent())
    const map = useMessageStore.getState().pendingConfirmMap
    expect(map['tc-1']).toEqual({
      question_id: 'qid-1',
      command: 'rm -rf /tmp/x',
      matched_pattern: 'rm *',
      timeout_seconds: 180,
      requestedAt: expect.any(Number),
    })
  })

  it('is idempotent — duplicate event_id is ignored', () => {
    const evt = makeRequestEvent()
    useMessageStore.getState().__applyEvent(evt)
    useMessageStore.getState().__applyEvent(evt)
    expect(Object.keys(useMessageStore.getState().pendingConfirmMap)).toHaveLength(1)
  })
})

describe('sandbox_confirm_resolved', () => {
  it('removes entry from pendingConfirmMap by question_id', () => {
    useMessageStore.getState().__applyEvent(makeRequestEvent())
    useMessageStore.getState().__applyEvent(makeResolvedEvent('qid-1'))
    expect(useMessageStore.getState().pendingConfirmMap['tc-1']).toBeUndefined()
  })

  it('is a no-op when question_id is not pending', () => {
    useMessageStore.getState().__applyEvent(makeResolvedEvent('qid-unknown'))
    expect(useMessageStore.getState().pendingConfirmMap).toEqual({})
  })
})
```

- [ ] **Step 2: Run tests — expect failure**

```bash
cd frontend && pnpm --filter @cubeplex/core exec vitest run --reporter=verbose \
  __tests__/stores/messageStore.sandboxConfirm.test.ts
```

Expected: FAIL — `pendingConfirmMap` not in store, `__applyEvent` undefined.

### Step 2b — Implement store changes

- [ ] **Step 3: Add `PendingConfirm` type and extend `MessageStore` interface**

In `messageStore.ts`, add after the `AgentStream` interface:

```typescript
export interface PendingConfirm {
  question_id: string
  command: string
  matched_pattern: string | null
  timeout_seconds: number | null
  requestedAt: number
}
```

Add to `MessageStore` interface (after `toolResultMap`):

```typescript
  pendingConfirmMap: Record<string, PendingConfirm>
  /** Test hook: apply a single AgentEvent synchronously */
  __applyEvent(event: AgentEvent): void
```

- [ ] **Step 4: Add initial state and reset sites**

In the `create<MessageStore>(...)` call, add `pendingConfirmMap: {}` next to `toolResultMap: {}` (line ~789).

Grep for all `toolResultMap: {}` in the file — there are 3 object literal reset sites (bootstrap, stream start, stream clear). Add `pendingConfirmMap: {}` next to each.

Additionally, grep for `isStreaming: false` — there are 8 sites where the run terminates (done, error, cancel, abort). Add `pendingConfirmMap: {}` to each of these too, as a defensive cleanup so stale confirm cards never persist after a run ends.

- [ ] **Step 5: Add `applyStreamEvent` branches**

In `applyStreamEvent`, add before the final `return base`:

```typescript
  if (event.type === 'sandbox_confirm_request') {
    const d = event.data as {
      question_id: string; tool_call_id: string; command: string
      matched_pattern: string | null; timeout_seconds: number | null
    }
    if (!d.tool_call_id) return base
    return {
      ...base,
      pendingConfirmMap: {
        ...state.pendingConfirmMap,
        [d.tool_call_id]: {
          question_id: d.question_id,
          command: d.command,
          matched_pattern: d.matched_pattern ?? null,
          timeout_seconds: d.timeout_seconds ?? null,
          requestedAt: Date.now(),
        },
      },
    }
  }

  if (event.type === 'sandbox_confirm_resolved') {
    const d = event.data as { question_id: string }
    const tcId = Object.entries(state.pendingConfirmMap).find(
      ([, v]) => v.question_id === d.question_id,
    )?.[0]
    if (!tcId) return base
    const next = { ...state.pendingConfirmMap }
    delete next[tcId]
    return { ...base, pendingConfirmMap: next }
  }
```

- [ ] **Step 6: Add `__applyEvent` action**

In the `create<MessageStore>(...)` actions, add:

```typescript
  __applyEvent(event: AgentEvent) {
    set((s) => applyStreamEvent(s, event) as MessageStore)
  },
```

- [ ] **Step 7: Re-export `PendingConfirm` from `stores/index.ts`**

```typescript
// stores/index.ts — add to existing messageStore export:
export { useMessageStore, type MessageStore, type AgentStream, type PendingConfirm } from './messageStore'
```

- [ ] **Step 8: Run tests — expect pass**

```bash
cd frontend && pnpm --filter @cubeplex/core exec vitest run --reporter=verbose \
  __tests__/stores/messageStore.sandboxConfirm.test.ts
```

Expected: 4 tests PASS.

- [ ] **Step 9: Type-check**

```bash
cd frontend && pnpm --filter @cubeplex/core run type-check
```

Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts \
        frontend/packages/core/src/stores/index.ts \
        frontend/packages/core/__tests__/stores/messageStore.sandboxConfirm.test.ts
git commit -m "feat(sandbox-ui): pendingConfirmMap in messageStore + event handlers"
```

---

## Task 3: `SandboxConfirmCard` component

**Files:**
- Create: `frontend/packages/web/components/chat/SandboxConfirmCard.tsx`

- [ ] **Step 1: Create the component**

```tsx
'use client'

import { useState, useEffect } from 'react'
import { Check, X, Clock } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { PendingConfirm } from '@cubeplex/core'

interface SandboxConfirmCardProps {
  pending: PendingConfirm
  onApprove: () => Promise<void>
  onDeny: () => Promise<void>
}

export function SandboxConfirmCard({ pending, onApprove, onDeny }: SandboxConfirmCardProps) {
  const [submitting, setSubmitting] = useState<'approve' | 'deny' | null>(null)
  const [secondsLeft, setSecondsLeft] = useState<number | null>(() => {
    if (pending.timeout_seconds === null) return null
    const elapsed = Math.floor((Date.now() - pending.requestedAt) / 1000)
    return Math.max(0, pending.timeout_seconds - elapsed)
  })

  useEffect(() => {
    if (secondsLeft === null || secondsLeft <= 0) return
    const id = setInterval(() => setSecondsLeft((s) => (s !== null && s > 0 ? s - 1 : 0)), 1000)
    return () => clearInterval(id)
  }, [secondsLeft])

  const handle = async (decision: 'approve' | 'deny') => {
    if (submitting) return
    setSubmitting(decision)
    try {
      if (decision === 'approve') await onApprove()
      else await onDeny()
    } catch {
      setSubmitting(null)
    }
  }

  return (
    <div className="my-2 rounded-lg border border-amber-200 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950/30">
      <div className="mb-2 flex items-center gap-2 text-sm font-medium text-amber-800 dark:text-amber-200">
        <Clock className="h-4 w-4 shrink-0" />
        <span>Command requires approval</span>
        {secondsLeft !== null && secondsLeft > 0 && (
          <span className="ml-auto tabular-nums text-amber-600 dark:text-amber-400">
            {secondsLeft}s
          </span>
        )}
      </div>
      <code className="mb-3 block rounded bg-amber-100 px-2 py-1 text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-100">
        {pending.command}
      </code>
      {pending.matched_pattern && (
        <p className="mb-3 text-xs text-amber-700 dark:text-amber-300">
          Matched rule: <code className="font-mono">{pending.matched_pattern}</code>
        </p>
      )}
      <div className="flex gap-2">
        <Button
          size="sm"
          className="gap-1 bg-green-600 hover:bg-green-700 dark:bg-green-700 dark:hover:bg-green-600"
          disabled={!!submitting}
          onClick={() => handle('approve')}
        >
          <Check className="h-3.5 w-3.5" />
          {submitting === 'approve' ? 'Approving…' : 'Approve'}
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="gap-1 border-red-300 text-red-600 hover:bg-red-50 dark:border-red-700 dark:text-red-400 dark:hover:bg-red-950/30"
          disabled={!!submitting}
          onClick={() => handle('deny')}
        >
          <X className="h-3.5 w-3.5" />
          {submitting === 'deny' ? 'Denying…' : 'Deny'}
        </Button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Type-check**

```bash
cd frontend && pnpm --filter web run type-check
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/SandboxConfirmCard.tsx
git commit -m "feat(sandbox-ui): SandboxConfirmCard component"
```

---

## Task 4: Wire `ToolCallItem` — replace spinner with card

**Files:**
- Modify: `frontend/packages/web/components/chat/ToolCallItem.tsx`

- [ ] **Step 1: Add imports and props**

```typescript
import type { PendingConfirm } from '@cubeplex/core'
import { SandboxConfirmCard } from './SandboxConfirmCard'
```

Add to `ToolCallItemProps`:
```typescript
  pendingConfirm?: PendingConfirm | null
  onSandboxConfirm?: (decision: 'approve' | 'deny') => Promise<void>
```

- [ ] **Step 2: Replace pending spinner with confirm card when `pendingConfirm` is set**

The pending spinner lives in the header row at the `{isPending ? (<> <Circle .../> ... </>) : ...}` branch.

Replace the entire ternary so that when `pendingConfirm` is set the spinner is suppressed:

```tsx
{pendingConfirm ? null : isPending ? (
  <>
    <Circle className="size-2.5 text-blue-500 animate-pulse" />
    <span className="text-xs text-muted-foreground">{formatDuration(elapsed)}</span>
  </>
) : toolResult ? (
  // ... existing toolResult branch unchanged ...
) : null}
```

Then render the card below the header row (before the closing wrapper tag):

```tsx
{pendingConfirm && onSandboxConfirm && (
  <SandboxConfirmCard
    pending={pendingConfirm}
    onApprove={() => onSandboxConfirm('approve')}
    onDeny={() => onSandboxConfirm('deny')}
  />
)}
```

- [ ] **Step 3: Type-check**

```bash
cd frontend && pnpm --filter web run type-check
```

Expected: no errors (new props are optional — no existing callsites break).

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/chat/ToolCallItem.tsx
git commit -m "feat(sandbox-ui): ToolCallItem renders SandboxConfirmCard, hides spinner"
```

---

## Task 5: Thread `pendingConfirmMap` down the render tree

**Files:**
- Modify: `frontend/packages/web/components/chat/ToolCallGroup.tsx`
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`

- [ ] **Step 1: `ToolCallGroup` — add props and pass through**

Add to `ToolCallGroupProps` (and destructure in the function signature):
```typescript
import type { PendingConfirm } from '@cubeplex/core'
  pendingConfirmMap?: Record<string, PendingConfirm>
  onSandboxConfirm?: (toolCallId: string, decision: 'approve' | 'deny') => Promise<void>
```

On the `<ToolCallItem ... />` inside the map, add:
```tsx
  pendingConfirm={pendingConfirmMap?.[block.id] ?? null}
  onSandboxConfirm={
    onSandboxConfirm ? (d) => onSandboxConfirm(block.id, d) : undefined
  }
```

- [ ] **Step 2: `AssistantMessage` — add props and pass through**

There are two props interfaces in the file: `HistoryProps` and `StreamingProps` (unified as `AssistantMessageProps = HistoryProps | StreamingProps`). Add to both:
```typescript
  pendingConfirmMap?: Record<string, PendingConfirm>
  onSandboxConfirm?: (toolCallId: string, decision: 'approve' | 'deny') => Promise<void>
```

Pass `pendingConfirmMap` and `onSandboxConfirm` to all 3 `<ToolCallGroup ... />` calls in the file.

- [ ] **Step 3: `MessageList` — read from store and build callback**

```typescript
import { useMessageStore, submitSandboxConfirm } from '@cubeplex/core'

// Inside MessageList({ conversationId }):
const pendingConfirmMap = useMessageStore((s) => s.pendingConfirmMap)
const streamingConversationId = useMessageStore((s) => s.streamingConversationId)

const handleSandboxConfirm = useCallback(
  async (toolCallId: string, decision: 'approve' | 'deny') => {
    const convId = streamingConversationId ?? conversationId
    const pending = pendingConfirmMap[toolCallId]
    if (!pending) return
    const client = createApiClient('')
    if (workspaceId) client.setWorkspaceId(workspaceId)
    await submitSandboxConfirm(client, convId, pending.question_id, decision)
    // Optimistic removal — resolved SSE will also clean up
    useMessageStore.setState((s) => {
      const next = { ...s.pendingConfirmMap }
      delete next[toolCallId]
      return { pendingConfirmMap: next }
    })
  },
  [conversationId, streamingConversationId, pendingConfirmMap, workspaceId],
)
```

Pass to both `<AssistantMessage ... />` calls:
```tsx
  pendingConfirmMap={pendingConfirmMap}
  onSandboxConfirm={handleSandboxConfirm}
```

Note: `workspaceId` is already in scope from the existing `useWorkspaceContext()` call; `createApiClient` is already imported.

- [ ] **Step 4: Type-check the whole frontend**

```bash
cd frontend && pnpm run type-check
```

Expected: no errors.

- [ ] **Step 5: Run vitest (both packages)**

```bash
cd frontend && pnpm --filter @cubeplex/core exec vitest run && pnpm --filter web run test
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/chat/ToolCallGroup.tsx \
        frontend/packages/web/components/chat/AssistantMessage.tsx \
        frontend/packages/web/components/chat/MessageList.tsx
git commit -m "feat(sandbox-ui): thread pendingConfirmMap to ToolCallItem"
```

---

## Task 6: Full build verification

- [ ] **Step 1: Build `@cubeplex/core`**

```bash
cd frontend && pnpm --filter @cubeplex/core run build
```

Expected: exit 0.

- [ ] **Step 2: Build `web`**

```bash
cd frontend && pnpm --filter web run build
```

Expected: exit 0.

- [ ] **Step 3: Run full vitest suite**

```bash
cd frontend && pnpm --filter @cubeplex/core exec vitest run && pnpm --filter web run test
```

Expected: all tests pass.

- [ ] **Step 4: Final push**

```bash
git push origin feat/sandbox-confirm-frontend
```

---

## Self-Review Checklist

- [x] `submitSandboxConfirm` URL matches backend: `/api/v1/conversations/{id}/sandbox-confirm/{questionId}`
- [x] `pendingConfirmMap` keyed by `tool_call_id` (what `ToolCallItem` has), not `question_id`
- [x] Resolved event removes by `question_id → tool_call_id` reverse lookup
- [x] Optimistic removal on submit (don't wait for resolved SSE)
- [x] Countdown initialized from `requestedAt + timeout_seconds` — correct even if SSE is delayed
- [x] All 3 `toolResultMap: {}` sites + all 8 `isStreaming: false` terminal sites reset `pendingConfirmMap`
- [x] Spinner suppressed (not just card added alongside) when `pendingConfirm` is present
- [x] `PendingConfirm` exported from `stores/index.ts` and therefore from `@cubeplex/core`
- [x] Test file at `__tests__/stores/` (not `src/__tests__/`) — matches existing core test layout
- [x] `MessageList` uses existing `createApiClient('')` + `client.setWorkspaceId(workspaceId)` pattern
- [x] Build/test commands use `pnpm run type-check` and filtered `pnpm --filter ... exec vitest run`
- [x] No E2E added — backend requires live HITL channel, unit tests cover store logic
