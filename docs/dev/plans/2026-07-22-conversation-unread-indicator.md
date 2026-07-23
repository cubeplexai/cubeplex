# Conversation Unread Indicator — Implementation Plan

**Goal:** Mark a conversation unread in the sidebar when its agent run
finishes while the user is focused elsewhere; clear when they open it again.
In-session client state only.

**Architecture:**

1. Extend `messageStore` with `unreadConversationIds` + mark/clear helpers.
2. On **every** non-HITL-pause stream terminalization path, if
   `isAwayFrom(completedId)`, call `markUnread`.
3. Clear on focus at the **UI boundary** (conversation page effect /
   sidebar navigation) — **not** by importing `messageStore` from
   `conversationStore`.
4. `ConversationRow` renders a compact unread dot when the id is in the set.

Coordinate with #388: spinner while streaming; unread only after complete
when away (do not show both).

**Tech stack:** React 19, Zustand (`@cubeplex/core`), next-intl, lucide or
plain CSS dot, Vitest + RTL.

**Spec:** [docs/dev/specs/2026-07-22-conversation-unread-indicator-design.md](../specs/2026-07-22-conversation-unread-indicator-design.md)  
**Issue:** #389

---

## File structure

| File | Action | Responsibility |
| --- | --- | --- |
| `frontend/packages/core/src/stores/messageStore.ts` | Modify | Unread map + mark on shared terminal helper; export helpers |
| `frontend/packages/web/app/(app)/w/[wsId]/conversations/[id]/page.tsx` | Modify | Clear unread on mount/focus; optionally clear `activeId` on unmount |
| `frontend/packages/web/components/layout/Sidebar.tsx` | Modify | Unread dot in `ConversationRow`; clear on row activate if needed |
| `frontend/packages/web/messages/en.json` / `zh.json` | Modify | `sidebar.conversationUnread` |
| `frontend/packages/core/__tests__/stores/messageStore.unread.test.ts` | Create | Mark/clear + away rules |
| `frontend/packages/web/__tests__/components/…` | Create | Dot renders from store state |

**Do not** import `useMessageStore` from `conversationStore.ts` — that
creates a circular dependency (`messageStore` already imports
`conversationStore`). Clear unread from page/sidebar call sites.

---

## Unit of work 1 — Store: unread set + mark/clear

**Files:** `messageStore.ts`, unit tests under `core/__tests__/stores/`

**Interfaces:**

```ts
// MessageStore fields / methods
unreadConversationIds: Record<string, true>  // or Set serialized carefully
markUnread(conversationId: string): void
clearUnread(conversationId: string): void
isUnread(conversationId: string): boolean   // optional helper
```

Prefer `Record<string, true>` for simple Zustand immutability
(`{ ...prev, [id]: true }` / omit key on clear) unless the codebase
already uses Sets elsewhere.

**Core logic:**

1. Initialize empty map in store defaults and any full-reset paths.
2. `markUnread(id)`: set key; no-op if already set.
3. `clearUnread(id)`: remove key; no-op if absent.
4. **Do not** persist.

**Hook points for mark** — introduce or extend **one** shared terminal
helper (conceptually `onStreamTerminal({ conversationId, kind })`)
invoked from:

- `finalizeCompletedStream` (normal done / abort stop reasons)
- SSE error / generic stream failure branches that clear streaming flags
- `cancelStream` completion (both early-return and full cancel paths)

**Do not** call `markUnread` from `finalizePausedStream` (HITL pause).

At mark time:

```ts
function isAwayFrom(conversationId: string): boolean {
  const activeId = useConversationStore.getState().activeId
  if (activeId !== conversationId) return true
  // Required if activeId is left stale on non-chat routes:
  // return !isViewingConversationRoute(conversationId)
  // Implement via pathname helper or by clearing activeId on page unmount.
  return false
}

// optional: document.visibilityState === 'hidden' → away even if on route
if (isAwayFrom(conversationId)) {
  get().markUnread(conversationId)
}
```

**Away + stale `activeId` (pick one before coding):**

1. **Preferred:** conversation page effect cleanup calls
   `setActive(null)` when unmounting / switching away so `activeId`
   tracks chat presence; or
2. Pass/read a `isViewingConversation(id)` from the shell at mark time.

Add an acceptance-style unit/component case: complete while “on A” but
route is workspace home / settings → still marks unread.

**Comment near field:** MVP session-only; no multi-tab/server sync.

**Tests (intent):**

- Stream completes while `activeId` is other → id is unread.
- Stream completes while viewing same conversation → not unread.
- Stream completes after navigating to non-chat surface (stale or cleared
  `activeId` per chosen approach) → unread.
- HITL pause while away → **not** unread.
- `clearUnread` removes id.
- Cancel while away → unread.

---

## Unit of work 2 — Clear on focus

**Files:** conversation page (`page.tsx`), Sidebar activate path

**Core logic:**

```ts
// conversation page mount effect (already calls setActive(conversationId))
useMessageStore.getState().clearUnread(conversationId)

// optional cleanup for stale activeId:
// return () => { if (stillThisPage) setActive(null) }
```

Audit sidebar click and deep links so every path that opens C clears
unread. **Do not** wire clear inside `conversationStore.setActive`.

**Tests:** focus/open helper clears unread for `c1`; leaves other ids.

---

## Unit of work 3 — Sidebar dot + i18n

**Files:** `Sidebar.tsx`, message JSON files

**Core logic:**

```ts
const isUnread = useMessageStore((s) => !!s.unreadConversationIds[convo.id])
// When #388 lands, isRunning takes visual precedence; if both ever true,
// prefer spinner only (should not happen after clean transitions).
```

Render a compact span/dot after the title (or after spinner slot):

```tsx
{isUnread && !isRunning && (
  <span
    className="size-1.5 shrink-0 rounded-full bg-primary"
    aria-label={tSidebar('conversationUnread')}
  />
)}
```

If #388 is not merged yet, omit `!isRunning` or treat `isRunning` as false.

**Tests (intent):** unread map contains id → accessible unread cue present;
cleared → absent; does not break pin icon presence smoke.

---

## Unit of work 4 — Docs of MVP limits

No new docs/site page required for design-only. Implementation PR should
keep the store comment. If a user-facing notifications doc exists later,
one sentence is enough — not blocking.

---

## Ordering vs #388

| If #388 already merged | Use shared title trailing slot: spinner XOR unread |
| If not | Ship unread alone; leave room after title for spinner |

Do not block #389 on #388 merge; designs are complementary.

---

## Verification

```bash
cd frontend/packages/core && pnpm exec vitest run __tests__/stores/messageStore.unread.test.ts
cd frontend/packages/web && pnpm exec vitest run __tests__/components/<unread-row-test>.tsx
```

---

## Implementation phase commits (suggested)

1. `feat(core): track session unread conversation ids on stream end`
2. `feat(sidebar): show unread dot on conversation rows`

Implementation waits for design approval.
