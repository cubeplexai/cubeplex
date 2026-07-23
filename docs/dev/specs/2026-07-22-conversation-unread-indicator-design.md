# Conversation Unread Indicator — Design

**Status:** Draft  
**Date:** 2026-07-22  
**Related:** #389 · pairs with #388 (running spinner while in-flight)

## 1. Goal

When an agent run finishes on a conversation the user is **not** focused on,
show a lightweight unread affordance on that conversation’s row in the left
chat history list. Opening / focusing that conversation again clears the
indicator.

This is the post-completion signal; #388 is the in-progress spinner.

## 2. Context

### What exists today

- History rows: `ConversationRow` in
  `frontend/packages/web/components/layout/Sidebar.tsx` (pinned, recent,
  topic-nested via the same component).
- Focused conversation: `useConversationStore.activeId` set by sidebar
  navigation (`setActive`) and conversation routing.
- Stream lifecycle: `useMessageStore` — `isStreaming`,
  `streamingConversationId`, clear on done / cancel / error. Completing a
  run does not currently mark anything “unread.”
- Single in-tab SSE ownership: one active stream controller; other tabs do
  not share completion events unless we add a channel later.

### User problem

User starts a run on A, switches to B (or another app page). A finishes.
Nothing in the list says “A has new content you have not looked at.” Users
guess which threads updated.

## 3. Approaches considered

| Approach | Pros | Cons |
| --- | --- | --- |
| **A. In-session client `unreadConversationIds` set** | Small; no API; enough for single-tab sessions; easy to clear on focus | Lost on full reload; other tabs may miss completion |
| **B. `localStorage` / `BroadcastChannel` persistence** (recommended) | Survives reload / multi-tab in same browser | Still not multi-device; more edge cases (stale ids) |
| **C. Server-backed last_read / unread** | Multi-device truth | Schema + API + group-chat semantics; issue non-goal |

**Recommendation: B** — client store plus `localStorage` + `BroadcastChannel`
so tabs that do not own the SSE still show/clear the dot, and reload keeps
the badge. Prefer a record map on `messageStore`. Do not block on C.

## 4. Design

### 4.1 State model

Add client-only unread tracking. **Resolved ownership (avoid circular
store imports):**

- `messageStore` **already** imports `useConversationStore` (e.g. title
  generation). Do **not** make `conversationStore` import `messageStore`
  for `clearUnread` — that creates a bidirectional ES module dependency.
- Keep the unread map + `markUnread` / `clearUnread` on **`messageStore`**
  (next to stream terminalization).
- Call **`clearUnread` from the focus boundary** (conversation page
  `setActive` effect, sidebar click path, deep-link mount) via
  `useMessageStore.getState().clearUnread(id)` — UI/page layer, not
  `conversationStore.setActive` body.

Concrete shape:

```ts
unreadConversationIds: Record<string, true>  // prefer over Set for Zustand immutability
// helpers:
markUnread(conversationId: string): void
clearUnread(conversationId: string): void
```

MVP: **browser-local** — Zustand state hydrated from `localStorage`, mutations
published via `localStorage` write + `BroadcastChannel` (see §4.6).

### 4.2 When to mark unread

Mark conversation `C` unread when **all** of the following hold at the
moment a run for `C` reaches a **terminal** client state:

1. The completing stream’s conversation is `C`
   (`streamingConversationId === C` just before clear, or completion
   handler is invoked with `C`).
2. The user is **away** from `C`’s chat surface. **MVP away predicate
   (resolved):**

   ```text
   isAwayFrom(C) =
     useConversationStore.activeId !== C
     OR the current app route is not C’s conversation page
        (e.g. pathname does not match /w/{ws}/conversations/{C})
   ```

   Rationale: today navigating to workspace home / settings / other
   panels often **leaves `activeId` set** to the last conversation (page
   unmount does not call `setActive(null)`). Treating `activeId === C`
   alone as “present” would miss the primary “user left the chat” case.

   Implementation options (pick one in the plan; both acceptable):

   - **A.** Clear `activeId` on conversation-page unmount / non-chat
     navigation, then `activeId !== C` is sufficient; or
   - **B.** At mark time, also require a route/focus helper
     `isViewingConversation(C)` injected or read from a small shell
     signal.

   Optional strengthening (same PR if cheap): also treat
   `document.visibilityState === 'hidden'` as away **even if** the user
   is still on C’s route (browser tab hidden).

3. Terminal outcome — **MVP mark rule (resolved):** mark on any client
   stream **terminalization** that runs for `C` while away:

   - `finalizeCompletedStream` (normal done)
   - `finalizePausedStream` is **not** a “run finished while away” signal
     for unread (HITL still needs user input on C — do not mark unread
     solely because the stream paused)
   - SSE / generic stream **error** paths that clear streaming flags
   - `cancelStream` completion (including cancel with no partial text)

   Prefer **one shared terminal helper** invoked from every path that
   clears `isStreaming` for a completed/aborted run so mark cannot be
   skipped on a stray catch branch. Empty cancel vs partial content: still
   mark when away (issue default: any terminal worth a glance).

**Do not mark** if the user stayed on C’s chat surface the whole time
(`!isAwayFrom(C)` and, if visibility is implemented, document visible).

### 4.3 When to clear unread

Clear unread for `C` when the user **opens / focuses** `C` again:

- Conversation page mount / `setActive(C)` from sidebar click / navigation
  into `/w/{ws}/conversations/{C}` — call `clearUnread(C)` at that UI
  boundary (same place `setActive` is invoked today), **not** inside
  `conversationStore.setActive` if that would import `messageStore`.
- Deep link / search result that mounts C’s page likewise.

Clear is **idempotent**. Do not require scrolling to bottom for MVP
(“had a chance to see” = open the conversation).

### 4.4 Interaction with running spinner (#388)

| Phase | Indicator |
| --- | --- |
| Run in flight | Spinner only (#388) |
| Run ends, user away | Spinner off → unread on |
| Run ends, user present | Neither |
| Unread + user opens row | Unread off |

Never show spinner and unread together for the same conversation at the
same time after a clean transition (spinner clears on terminalization
before or as unread is set).

### 4.5 UX

- **Placement:** On `ConversationRow`, compact **primary/blue unread
  dot** near the title (after title / before menu). Prefer a ~6–8px
  rounded dot over a numeric badge.
- **Coexistence:** Leave space so #388 spinner and #389 dot do not
  stack clutter; mutually exclusive states above.
- **Surfaces:** pinned, recent, topic-nested (shared row).
- **a11y:** `aria-label` e.g. “Unread messages” via
  `sidebar.conversationUnread` (en + zh).
- **Non-interference:** pin, rename, delete, group avatars, menu
  unchanged.

### 4.6 Multi-tab / reload

| Scenario | Behavior |
| --- | --- |
| Single tab, in-session | Works |
| Full page reload | Hydrate unread ids from `localStorage` |
| Second tab without the SSE | Peer publishes full map on mark/clear via `BroadcastChannel` (+ `storage` event fallback) |
| Multi-device | Not supported (no server state) |

Tab that owns the SSE calls `markUnread` on terminalization; that publish
updates every other open tab's store. Opening the conversation on any tab
clears and re-publishes.

### 4.7 Explicit non-goals

- OS / browser push notifications.
- Server-persisted unread or multi-device sync.
- Human-only messages / @mentions unread (group chat receipts).
- Sound or toast on background completion.
- Full notification system redesign.

## 5. Out of scope

- Backend schema and conversation list API changes.
- Cross-user read receipts.
- Unifying with email-style notification centers.

## 6. Success criteria

1. Run on A, switch to B before A finishes → when A completes, A’s row
   shows unread.
2. Stay on A until finish → no unread on A.
3. Open A → unread clears.
4. Unread visible without opening A; pin/rename/avatars/menu intact.
5. Visible cue + accessible name.
6. Multi-tab: mark/clear on one tab is reflected on others via
   `localStorage` + `BroadcastChannel`; multi-device remains out of scope.

## 7. Open questions (resolved for implementers)

| Question | Decision |
| --- | --- |
| Fail/cancel mark unread? | Yes if user was away (any non-HITL-pause terminal). |
| HITL pause mark unread? | No — user still must answer on that conversation. |
| Require scroll-to-bottom to clear? | No — open/focus is enough. |
| Persist across reload? | Yes, via `localStorage` (same browser only). |
| document.visibilityState? | Nice-to-have same PR; route/`activeId` away predicate is required. |
| `activeId` stale after leaving chat? | Treat non-chat route as away; and/or clear `activeId` on unmount. |
