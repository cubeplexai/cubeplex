# Conversation Unread Indicator — Implementation Plan

**Goal:** Mark a conversation unread in the sidebar when its agent run
finishes while the user is focused elsewhere; clear when they open it again.
In-session client state only.

**Architecture:**

1. Extend client store with `unreadConversationIds` + mark/clear helpers.
2. On stream terminalization paths in `messageStore`, if
   `conversationStore.activeId !== completedId`, call `markUnread`.
3. Clear on focus: conversation `setActive` / conversation page mount.
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
| `frontend/packages/core/src/stores/messageStore.ts` | Modify | Unread set + mark on terminal stream; export helpers |
| `frontend/packages/core/src/stores/conversationStore.ts` | Modify | Clear unread when `setActive(id)` (or call messageStore from here) |
| `frontend/packages/core/src/index.ts` (if needed) | Modify | Export any new types/helpers |
| `frontend/packages/web/components/layout/Sidebar.tsx` | Modify | Unread dot in `ConversationRow` |
| `frontend/packages/web/messages/en.json` / `zh.json` | Modify | `sidebar.conversationUnread` |
| `frontend/packages/core/__tests__/stores/messageStore.unread.test.ts` | Create | Mark/clear rules |
| `frontend/packages/web/__tests__/components/…` | Create | Dot renders from store state |

Exact store ownership: **prefer messageStore for the Set** and call
`clearUnread` from `conversationStore.setActive` via
`useMessageStore.getState().clearUnread(id)` to avoid circular React
deps (both are already plain Zustand modules).

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

**Hook points for mark** (same completion paths that clear
`isStreaming` / `streamingConversationId`):

- Normal `done` finalization
- Error terminalization
- User `cancelStream` completion
- Any shared helper used by those paths (prefer **one** call site if a
  `finalize…` already exists)

At mark time:

```ts
const activeId = useConversationStore.getState().activeId
if (activeId !== conversationId) {
  get().markUnread(conversationId)
}
```

Optional: if `typeof document !== 'undefined' && document.visibilityState === 'hidden'`,
mark even when `activeId === conversationId`.

**Comment near field:** MVP session-only; no multi-tab/server sync.

**Tests (intent):**

- Stream completes while `activeId` is other → id is unread.
- Stream completes while `activeId` is same → not unread.
- `clearUnread` removes id.
- Cancel while away → unread (per spec default).

---

## Unit of work 2 — Clear on focus

**Files:** `conversationStore.ts` (`setActive`)

**Core logic:**

```ts
setActive(id: string | null) {
  set({ activeId: id })
  if (id) {
    useMessageStore.getState().clearUnread(id)
  }
}
```

Also clear when conversation page sets active on mount if that path does
not always go through `setActive` — audit
`Sidebar` `onClick` and conversation page `useEffect` so deep links clear
too.

**Tests:** `setActive('c1')` clears unread for `c1`; leaves other ids.

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
