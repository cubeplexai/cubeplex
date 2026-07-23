# Conversation Running Indicator — Implementation Plan

**Goal:** Add a list-level “this conversation is still streaming” spinner next
to the title in the chat history sidebar, driven by existing
`useMessageStore` stream flags.

**Architecture:** Pure frontend affordance. `ConversationRow` subscribes to
`isStreaming` + `streamingConversationId` and renders a small `Loader2` when
the row’s `convo.id` matches. No backend or conversation-list API changes.

**Tech stack:** React 19, Next.js app router, Zustand (`@cubeplex/core`
message store), lucide-react, next-intl, Vitest + React Testing Library.

**Spec:** [docs/dev/specs/2026-07-22-conversation-running-indicator-design.md](../specs/2026-07-22-conversation-running-indicator-design.md)  
**Issue:** #388

---

## File structure

| File | Action | Responsibility |
| --- | --- | --- |
| `frontend/packages/web/components/layout/Sidebar.tsx` | Modify | `ConversationRow`: running predicate + spinner after title |
| `frontend/packages/web/messages/en.json` | Modify | `sidebar.conversationRunning` |
| `frontend/packages/web/messages/zh.json` | Modify | Chinese copy for the same key |
| `frontend/packages/web/__tests__/components/ConversationRow.running.test.tsx` (or Sidebar-focused test) | Create | Business-facing: spinner present/absent from store state |

No changes to `messageStore` unless a tiny exported selector is preferred
for testability; default is inline store read in the row.

---

## Unit of work 1 — i18n keys

**Files:** `messages/en.json`, `messages/zh.json`

**Interfaces:** `useTranslations('sidebar')` → `t('conversationRunning')`

**Core logic:** Add one string key under existing `sidebar` object. No
namespace restructure.

**Tests:** Covered indirectly by component test that asserts accessible name
matches the English string under the test intl provider (same pattern as
other Sidebar tests if present).

---

## Unit of work 2 — ConversationRow spinner

**Files:** `Sidebar.tsx` (`ConversationRow`)

**Interfaces:**

```ts
// Read-only subscription inside ConversationRow
const isRunning = useMessageStore(
  (s) => s.isStreaming && s.streamingConversationId === convo.id,
)
```

**Core logic:**

1. Import `Loader2` and `useMessageStore` if not already available in the
   file (message store is already used elsewhere in the app; wire import
   from `@cubeplex/core`).
2. After the title `div`, when `isRunning`, render:

   ```tsx
   <Loader2
     className="size-3.5 shrink-0 animate-spin text-muted-foreground"
     aria-label={tSidebar('conversationRunning')}
   />
   ```

   Adjust size/class only to match pin icon scale (~12–14px) and avoid
   colliding with avatars/menu.
3. Do not render the spinner in the rename (`isEditing`) branch unless
   product wants it during rename; default: spinner only on the link row.
4. Keep pin / group / menu order:  
   `[pin?] [groupIcon?] [title …] [spinner?] [avatars?] [menu]`

**Tests (intent):**

- Given store `{ isStreaming: true, streamingConversationId: 'c1' }` and a
  row for `c1` → spinner is in the document with the running accessible
  name.
- Given same store and row for `c2` → no spinner.
- Given `isStreaming: false` → no spinner (covers idle **and** paused HITL
  where `streamingConversationId` may still be set).
- Given `{ isStreaming: false, streamingConversationId: 'c1' }` (HITL-paused
  shape) and row `c1` → **no** spinner.
- Row still exposes rename/pin controls (smoke: menu trigger or pin icon
  still present) — protect “layout not broken,” not DOM counts for their
  own sake.

Prefer a focused unit/component test with store state injected (existing
patterns in `InputBar.test.tsx` / messageStore mocks) over a full Playwright
suite for this pure client indicator.

**Store ownership (implementation note, not a spinner feature):** If during
QA a late terminal event from aborted conversation A clears B’s streaming
flags, fix the owner check inside `messageStore` terminalization (only clear
when `streamingConversationId === completedId` / current run still owns the
controller). Do not add a second running set in the sidebar.

---

## Unit of work 3 — Manual / E2E smoke (optional if time)

**Intent:** One Playwright or manual checklist item:

1. Send a message in conversation A.
2. Navigate to conversation B while A streams.
3. Assert A’s sidebar row shows the spinner; B does not.
4. Wait for completion; spinner gone.

Only automate if a cheap existing sidebar E2E harness already mounts the
shell; do not build a large new E2E surface for a 10-line UI change.

---

## Out of scope in implementation

- Multi-stream `Set` of running conversation ids
- Server list fields
- Unread indicator (#389)
- Docs site page (no route/API surface)

---

## Verification

```bash
# from worktree frontend package
cd frontend/packages/web && pnpm exec vitest run __tests__/components/ConversationRow.running.test.tsx
# or the chosen test path
```

Paste green vitest output before claiming done.

---

## Suggested commit sequence (implementation phase)

1. `feat(sidebar): show running spinner on streaming conversation row`
2. (optional) test-only follow-up if split

Implementation waits for design approval of this plan + spec.
