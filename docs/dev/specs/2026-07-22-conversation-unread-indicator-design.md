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
| **A. In-session client `unreadConversationIds` set** (recommended MVP) | Small; no API; enough for single-tab sessions; easy to clear on focus | Lost on full reload; other tabs may miss completion |
| **B. `localStorage` / `BroadcastChannel` persistence** | Survives reload / multi-tab in same browser | Still not multi-device; more edge cases (stale ids) |
| **C. Server-backed last_read / unread** | Multi-device truth | Schema + API + group-chat semantics; issue non-goal |

**Recommendation: A** for MVP, with documented limits. Prefer a Set (or
record map) on a client store. Optionally upgrade to B later if product
requires; do not block MVP on C.

## 4. Design

### 4.1 State model

Add client-only unread tracking, preferred location either:

- New fields on `useMessageStore` (next to stream lifecycle), **or**
- New small store / fields on `useConversationStore`

**Recommendation:** keep unread next to stream completion in
**`messageStore`** (or a dedicated thin `conversationUiStore` if message
store bloat is a concern). Concrete shape:

```ts
unreadConversationIds: ReadonlySet<string> | Record<string, true>
// helpers:
markUnread(conversationId: string): void
clearUnread(conversationId: string): void
```

MVP: **session memory only** (Zustand default, no persist middleware).

### 4.2 When to mark unread

Mark conversation `C` unread when **all** of the following hold at the
moment a run for `C` reaches a **terminal** client state:

1. The completing stream’s conversation is `C`
   (`streamingConversationId === C` just before clear, or completion
   handler is invoked with `C`).
2. The user is **away** from `C`’s chat surface, defined for MVP as:

   ```text
   useConversationStore.activeId !== C
   ```

   Optional strengthening (same PR if cheap): also treat
   `document.visibilityState === 'hidden'` as away **even if**
   `activeId === C` (user switched browser tab/window while still “on”
   that route). Product default in the issue: leave conversation UI
   focus **or** tab not focused — implement at least `activeId !== C`;
   add document visibility if it is a few lines and tested.

3. The terminal outcome produced **viewable new content** worth reviewing:

   - **Default (issue):** any terminal run that produced viewable new
     assistant/error content — success, error with content, cancel after
     partial assistant text if the store still committed something
     visible.
   - Practical MVP rule: mark on any client stream terminalization
     (done / error / cancel) **except** when the user never left `C`
     (criterion 2). Avoid over-filtering cancel vs success in v1 unless
     tests show noise.

**Do not mark** if the user stayed on `C` the whole time (`activeId === C`
and, if visibility is implemented, document visible).

### 4.3 When to clear unread

Clear unread for `C` when the user **opens / focuses** `C` again:

- `setActive(C)` from sidebar click / navigation into
  `/w/{ws}/conversations/{C}`
- Also clear if the user lands on `C` via search result or deep link
  (any path that sets `activeId` to `C` or mounts the conversation page
  for `C`).

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

### 4.6 Multi-tab / reload (MVP limits — document in code comment + plan)

| Scenario | MVP behavior |
| --- | --- |
| Single tab, in-session | Works |
| Full page reload | Unread set empty (lost) |
| Second tab without the SSE | May not learn completion |
| Multi-device | Not supported |

No BroadcastChannel required for MVP. If a one-liner shared storage is
trivial later, treat as follow-up.

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
6. MVP limits (session-only, single-tab SSE) documented in the plan /
   short code comment near the store fields.

## 7. Open questions (resolved for implementers)

| Question | Decision |
| --- | --- |
| Fail/cancel mark unread? | Yes if user was away (any terminal stream end). |
| Require scroll-to-bottom to clear? | No — open/focus is enough. |
| Persist across reload? | No for MVP. |
| document.visibilityState? | Nice-to-have same PR; `activeId` is required. |
