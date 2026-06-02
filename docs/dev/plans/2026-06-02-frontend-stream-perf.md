# Frontend Streaming Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the O(history × text_blocks × markdown_pipeline) work per stream delta that freezes the page after several conversation turns.

**Architecture:** Two memoization barriers, plus prop scoping:
1. Wrap `MarkdownWithCitations` in `React.memo` so unchanged markdown text doesn't re-run remark/rehype/highlight/katex on every parent re-render.
2. Split historical assistant rendering into a memoized `HistoryAssistantMessage`. Stabilize its hot-path props by passing the *historical* tool-result map (not the merged one that mutates on every tool_result event). Keep `pendingConfirmMap` and `onSandboxConfirm` for history too — see "Why historical messages still need confirm props" below — those props only change on rare `sandbox_confirm_request` / `sandbox_confirm_resolved` events, not on the text-delta storm, so memo still bails out during streaming.
3. Throttle `ResizeObserver` auto-scroll with `requestAnimationFrame` to coalesce layout writes across rapid deltas.

After these changes, the streaming-render work per delta drops to roughly: 1 × markdown pipeline (for the current text block), instead of N × all-historical-blocks.

**Tech Stack:** React 19, Next.js 15 (app router), Zustand, react-markdown + remark/rehype plugins, Vitest + React Testing Library.

**Worktree:** `/home/chris/cubebox/.worktrees/feat/frontend-stream-perf` (slot 87, frontend on `:3087`, backend on `:8087`). All paths below are relative to that worktree.

### Why historical messages still need confirm props

`messageStore.ts::__commitTurnAndInject` (lines ~1224–1268) commits the in-flight
assistant bubble into `messages[conversationId]` and resets `streamAgents`, but it
does **not** clear `pendingConfirmMap`. So when a steer / `injected_message` lands
while a sandbox confirm is open, the assistant bubble carrying that `tool_call`
moves to history while the matching `pendingConfirmMap[tool_call_id]` is still
populated. If we stripped `pendingConfirmMap` / `onSandboxConfirm` from the
historical render path, the user would lose the approve/deny card.

The good news for perf: `pendingConfirmMap` and `handleSandboxConfirm` are
reference-stable across the streaming hot path. The map only changes on
`sandbox_confirm_request` / `sandbox_confirm_resolved` events, and
`handleSandboxConfirm` is `useCallback`-memoized with deps
`[conversationId, streamingConversationId, pendingConfirmMap, workspaceId]` —
none of which mutate per text/reasoning delta. So memoization of history still
bails out during the delta storm.

**Pre-flight:** From the worktree root, run `cat .worktree.env` to confirm ports, then `cd frontend && pnpm install --frozen-lockfile` (already done by `new-worktree`, just verify). All `pnpm` commands run from `frontend/packages/web/` unless noted.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `frontend/packages/web/components/shared/MarkdownWithCitations.tsx` | Modify | Wrap export in `React.memo` |
| `frontend/packages/web/components/shared/__tests__/MarkdownWithCitations.memo.test.tsx` | Create | Render-count test proving memo skips re-renders on equal props |
| `frontend/packages/web/components/chat/AssistantMessage.tsx` | Modify | Add memoized `HistoryAssistantMessage` re-export |
| `frontend/packages/web/components/chat/MessageList.tsx` | Modify | Use `HistoryAssistantMessage` for history; pass `historicalToolResults` (not `mergedToolResultMap`); rAF-throttle ResizeObserver |
| `frontend/packages/web/__tests__/components/MessageListMemo.test.tsx` | Create | Assert `HistoryAssistantMessage` is a `React.memo` wrapper over `AssistantMessage` and still renders correctly |
| `frontend/packages/web/lib/scrollToBottom.ts` | Create | rAF-throttled scroll-to-bottom helper |
| `frontend/packages/web/lib/__tests__/scrollToBottom.test.ts` | Create | Coalescing behavior test for the helper |

---

## Task 1: Memoize MarkdownWithCitations

**Files:**
- Modify: `frontend/packages/web/components/shared/MarkdownWithCitations.tsx`
- Create: `frontend/packages/web/components/shared/__tests__/MarkdownWithCitations.memo.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/packages/web/components/shared/__tests__/MarkdownWithCitations.memo.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MarkdownWithCitations } from '../MarkdownWithCitations'

describe('MarkdownWithCitations memoization', () => {
  it('is exported as a React.memo component', () => {
    // React.memo returns an object with $$typeof === Symbol.for('react.memo').
    // Checking the wrapper marker is the most reliable way to assert the
    // memoization barrier is in place — DOM-identity checks would pass even
    // without memo because React reconciliation reuses same-type nodes.
    const marker = (MarkdownWithCitations as unknown as { $$typeof?: symbol })
      .$$typeof
    expect(marker).toBe(Symbol.for('react.memo'))
  })

  it('still renders markdown correctly', () => {
    render(
      <MarkdownWithCitations conversationId="conv-test">
        hello **world**
      </MarkdownWithCitations>,
    )
    expect(screen.getByText('world')).toBeInTheDocument()
    expect(screen.getByText('world').tagName).toBe('STRONG')
  })

  it('updates output when children text changes', () => {
    const { rerender } = render(
      <MarkdownWithCitations conversationId="conv-test">alpha</MarkdownWithCitations>,
    )
    expect(screen.getByText('alpha')).toBeInTheDocument()
    rerender(
      <MarkdownWithCitations conversationId="conv-test">beta</MarkdownWithCitations>,
    )
    expect(screen.getByText('beta')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run from `frontend/packages/web/`:
```bash
pnpm vitest run components/shared/__tests__/MarkdownWithCitations.memo.test.tsx
```
Expected: the first test FAILS — `firstStrong` and `secondStrong` are different DOM nodes because the component re-rendered.

- [ ] **Step 3: Wrap export in React.memo**

Modify `frontend/packages/web/components/shared/MarkdownWithCitations.tsx`:

- Add `memo` to the `react` import:
  ```tsx
  import { memo } from 'react'
  import type { ComponentProps } from 'react'
  ```
- Rename the existing `export function MarkdownWithCitations(...)` to a local `function MarkdownWithCitationsImpl(...)` (keep the body unchanged).
- At the bottom of the file, replace it with the memo'd export:
  ```tsx
  export const MarkdownWithCitations = memo(MarkdownWithCitationsImpl)
  ```

Rationale: `children` (string), `className` (string), `conversationId` (string|undefined) are all primitives → default shallow `Object.is` comparison is correct. The internal `useConversationStore` call still subscribes to `activeId`; if it changes the memoized component re-renders, which is correct behavior.

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pnpm vitest run components/shared/__tests__/MarkdownWithCitations.memo.test.tsx
```
Expected: both tests PASS.

- [ ] **Step 5: Run the existing shared-component tests to confirm no regression**

Run:
```bash
pnpm vitest run components/shared
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/shared/MarkdownWithCitations.tsx \
        frontend/packages/web/components/shared/__tests__/MarkdownWithCitations.memo.test.tsx
git commit -m "perf(frontend): memoize MarkdownWithCitations to skip remark/rehype on equal props"
```

---

## Task 2: Memoize historical AssistantMessage and scope its props

**Files:**
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
- Create: `frontend/packages/web/__tests__/components/MessageListMemo.test.tsx`

- [ ] **Step 1: Add `HistoryAssistantMessage` memoized re-export**

Open `frontend/packages/web/components/chat/AssistantMessage.tsx`. At the very bottom of the file, append:

```tsx
import { memo } from 'react'

/**
 * Memoized wrapper for historical (non-streaming) assistant messages. The
 * perf win comes from stabilizing `toolResultMap`: MessageList passes the
 * message-stable `historicalToolResults` (not the live `mergedToolResultMap`,
 * which mutates on every streaming `tool_result` event).
 *
 * `pendingConfirmMap` and `onSandboxConfirm` are still passed in — a sandbox
 * confirm can outlive a `__commitTurnAndInject` (steer / injected_message),
 * so historical bubbles must keep rendering the approve/deny card. Those
 * props are reference-stable across text/reasoning deltas (the map only
 * mutates on `sandbox_confirm_request` / `_resolved`, and the handler is
 * useCallback'd with deps that exclude per-delta state), so memo still bails
 * out on the hot path.
 */
export const HistoryAssistantMessage = memo(AssistantMessage)
```

Also add `memo` to the existing top-of-file React import block (replace `import { useState, useEffect, useRef } from 'react'` with `import { useState, useEffect, useRef, memo } from 'react'`) and **remove** the duplicate `import { memo } from 'react'` you just added at the bottom. Final ordering: single React import at the top, `HistoryAssistantMessage` defined at the bottom.

- [ ] **Step 2: Write the failing test**

Create `frontend/packages/web/__tests__/components/MessageListMemo.test.tsx`:

```tsx
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  AssistantMessage,
  HistoryAssistantMessage,
} from '@/components/chat/AssistantMessage'
import type { AssistantMessage as AssistantMessageType } from '@cubebox/core'

const baseMessage = {
  id: 'msg-1',
  role: 'assistant',
  content: [{ type: 'text', text: 'hello world' }],
  timestamp: 1_700_000_000,
} as unknown as AssistantMessageType

describe('HistoryAssistantMessage', () => {
  it('is a memoized re-export of AssistantMessage', () => {
    const marker = (HistoryAssistantMessage as unknown as {
      $$typeof?: symbol
      type?: unknown
    })
    expect(marker.$$typeof).toBe(Symbol.for('react.memo'))
    expect(marker.type).toBe(AssistantMessage)
  })

  it('still renders the message text', () => {
    render(
      <HistoryAssistantMessage
        message={baseMessage}
        subagentDataMap={{}}
        toolResultMap={{}}
        conversationId="conv-1"
      />,
    )
    expect(screen.getByText('hello world')).toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run test to verify both pass**

Run from `frontend/packages/web/`:
```bash
pnpm vitest run __tests__/components/MessageListMemo.test.tsx
```
Expected: both tests PASS (memo already added in Step 1).

- [ ] **Step 4: Switch MessageList to use HistoryAssistantMessage with stable props**

Edit `frontend/packages/web/components/chat/MessageList.tsx`:

1. Update the import on line 17:
   ```tsx
   import { AssistantMessage, HistoryAssistantMessage } from './AssistantMessage'
   ```

2. Replace the historical-message render block (currently lines 253–262) with:
   ```tsx
   {msg.role === 'assistant' && msg.id !== lastAssistantId && (
     <HistoryAssistantMessage
       message={msg}
       subagentDataMap={subagentDataMap}
       toolResultMap={historicalToolResults}
       conversationId={conversationId}
       pendingConfirmMap={pendingConfirmMap}
       onSandboxConfirm={handleSandboxConfirm}
     />
   )}
   ```

   Rationale:
   - **Drop the live `mergedToolResultMap`** for history. Historical messages already have their tool results captured in `historicalToolResults` (built from `messages`); the merged map mutates on every `tool_result` event during streaming and would force every historical bubble to re-render even with memo.
   - **Keep `pendingConfirmMap` and `onSandboxConfirm`.** `__commitTurnAndInject` can move a bubble carrying an unresolved sandbox confirm into history; stripping the props would hide the approve/deny card. These props are reference-stable across text/reasoning delta storms (only `sandbox_confirm_request` / `_resolved` mutate the map; `handleSandboxConfirm`'s useCallback deps exclude delta-driven state), so memo still bails out on the hot path.

3. Leave the streaming render block (currently lines 266–278) unchanged — it still uses `AssistantMessage` with `mergedToolResultMap`, `pendingConfirmMap`, and `onSandboxConfirm`.

- [ ] **Step 5: Run MessageList and related tests to verify no regressions**

Run:
```bash
pnpm vitest run __tests__/components/MessageList __tests__/hooks/useMessages __tests__/stores/messageStore
```
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/chat/AssistantMessage.tsx \
        frontend/packages/web/components/chat/MessageList.tsx \
        frontend/packages/web/__tests__/components/MessageListMemo.test.tsx
git commit -m "perf(frontend): memoize history AssistantMessage; pass historical-only tool results"
```

---

## Task 3: rAF-throttle ResizeObserver auto-scroll

**Files:**
- Create: `frontend/packages/web/lib/scrollToBottom.ts`
- Create: `frontend/packages/web/lib/__tests__/scrollToBottom.test.ts`
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/packages/web/lib/__tests__/scrollToBottom.test.ts`:

```ts
import { describe, it, expect } from 'vitest'
import { rafThrottleScrollToBottom } from '../scrollToBottom'

function nextFrame(): Promise<void> {
  return new Promise((r) => requestAnimationFrame(() => r()))
}

describe('rafThrottleScrollToBottom', () => {
  it('coalesces many calls into a single scrollTop write per frame', async () => {
    const el = { scrollTop: 0, scrollHeight: 500 } as unknown as HTMLElement
    const scheduler = rafThrottleScrollToBottom(() => el)

    for (let i = 0; i < 50; i++) scheduler()
    // No write yet — still inside the same task.
    expect(el.scrollTop).toBe(0)

    await nextFrame()
    expect(el.scrollTop).toBe(500)

    // A subsequent burst schedules again and writes the latest scrollHeight.
    ;(el as { scrollHeight: number }).scrollHeight = 800
    for (let i = 0; i < 30; i++) scheduler()
    await nextFrame()
    expect(el.scrollTop).toBe(800)
  })

  it('is a no-op when the element getter returns null', async () => {
    const scheduler = rafThrottleScrollToBottom(() => null)
    expect(() => scheduler()).not.toThrow()
    await nextFrame()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run from `frontend/packages/web/`:
```bash
pnpm vitest run lib/__tests__/scrollToBottom.test.ts
```
Expected: FAIL — `../scrollToBottom` does not exist yet.

- [ ] **Step 3: Implement the throttled helper**

Create `frontend/packages/web/lib/scrollToBottom.ts`:

```ts
/**
 * Returns a scheduler that coalesces multiple "scroll to bottom" requests into
 * a single write per animation frame. The supplied getter is called inside the
 * rAF callback so the element's latest scrollHeight is read just before the
 * write — avoiding stale heights when many deltas land in one task.
 */
export function rafThrottleScrollToBottom(getElement: () => HTMLElement | null): () => void {
  let pending = false
  return () => {
    if (pending) return
    pending = true
    requestAnimationFrame(() => {
      pending = false
      const el = getElement()
      if (!el) return
      el.scrollTop = el.scrollHeight
    })
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pnpm vitest run lib/__tests__/scrollToBottom.test.ts
```
Expected: both tests PASS.

- [ ] **Step 5: Wire the helper into MessageList**

Edit `frontend/packages/web/components/chat/MessageList.tsx`:

1. Add import near the top:
   ```tsx
   import { rafThrottleScrollToBottom } from '@/lib/scrollToBottom'
   ```

2. Replace the existing ResizeObserver effect (lines 213–225):
   ```tsx
   useEffect(() => {
     const content = contentRef.current
     const scroller = scrollRef.current
     if (!content || !scroller) return

     const scheduleScroll = rafThrottleScrollToBottom(() => scrollRef.current)
     const ro = new ResizeObserver(() => {
       if (stickToBottom.current) scheduleScroll()
     })
     ro.observe(content)
     return () => ro.disconnect()
   }, [])
   ```

   Rationale: bursts of `text_delta` events grow content height many times per animation frame. Writing `scrollTop = scrollHeight` synchronously on every ResizeObserver fire forces a layout each time. Coalescing to one write per frame eliminates the layout thrash without changing the user-visible scroll behavior.

- [ ] **Step 6: Run all MessageList-related tests**

```bash
pnpm vitest run __tests__/components/MessageList __tests__/components/MessageListMemo lib/__tests__/scrollToBottom
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/lib/scrollToBottom.ts \
        frontend/packages/web/lib/__tests__/scrollToBottom.test.ts \
        frontend/packages/web/components/chat/MessageList.tsx
git commit -m "perf(frontend): coalesce streaming auto-scroll into one write per frame"
```

---

## Task 4: Full type-check, lint, and manual browser verification

**Files:** (none modified in this task)

- [ ] **Step 1: Type-check the web package**

From the worktree root:
```bash
cd frontend && pnpm -C packages/web typecheck
```
Expected: no errors.

- [ ] **Step 2: Lint the web package**

```bash
pnpm -C packages/web lint
```
Expected: no errors. Fix any new warnings introduced by the changes.

- [ ] **Step 3: Run the full web vitest suite**

```bash
pnpm -C packages/web test
```
Expected: all suites PASS.

- [ ] **Step 4: Start backend and frontend in the worktree**

From the worktree root, two terminals (or `tmux`):

Terminal A (backend):
```bash
source .worktree.env
cd backend && python main.py
```

Terminal B (frontend):
```bash
cd frontend && pnpm dev
```

Verify the dev server prints `http://localhost:3087`. The wrapper `scripts/with-worktree-env.mjs` injects `PORT=3087` from `.worktree.env`; if you see `:3000` instead, you bypassed the wrapper — restart with plain `pnpm dev` (not `pnpm next dev`).

- [ ] **Step 5: Reproduce the original symptom on a control build, then on the patched build**

Open `http://192.168.1.150:3087/w/<wsId>/conversations/<convId>` (use a long existing conversation — at least 5 assistant turns, ideally with code blocks or KaTeX). The user reported `conv-1fwZQ8u3ZDukx3` in workspace `ws-1cmDVQzDJpWuVG` as a reproducer; if that workspace is not provisioned in the worktree's per-slot DB, fall back to seeding a long conversation in the worktree DB or restoring from a dump.

Open Chrome DevTools → Performance, start recording, send a new message that triggers a long streaming response (many text deltas + at least one tool call). Stop after ~10 seconds.

Expected outcome (after patches): main-thread "Scripting" time during streaming drops from the previous multi-hundred-ms long tasks to short (<50 ms) tasks. No "page unresponsive" dialog. Long historical messages no longer show up under "Component rendering" for `MarkdownWithCitations` in the React DevTools Profiler.

If the symptom persists, do NOT add more fixes — return to systematic-debugging Phase 1 and gather a fresh profiler trace; the bottleneck is elsewhere (likely `WidgetView` if widgets dominate the conversation, or a Zustand selector that is over-subscribing).

- [ ] **Step 6: Commit the verification log (optional)**

If the manual verification produced a saved Chrome Performance trace worth keeping, drop it under `docs/dev/notes/2026-06-02-frontend-stream-perf-verification.md` with a one-paragraph summary of before/after numbers and commit. Skip if not informative.

---

## Out of Scope (deliberate)

- `WidgetView` memoization. The current render does not remount the iframe, and Step 5 verification will tell us whether widgets are still a hot path. Address in a follow-up plan only if profiler evidence implicates it.
- `rehype-highlight` lazy / viewport-only highlighting. Large workaround for a problem likely solved by Task 1+2.
- Refactoring `AssistantMessage` into separate `Streaming` / `History` components. The memoized re-export gets the same perf win with a much smaller diff; a real split can wait until the file grows further.
- Backend changes. The symptom is purely a frontend render-cost issue.
