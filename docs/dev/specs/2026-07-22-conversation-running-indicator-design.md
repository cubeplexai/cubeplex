# Conversation Running Indicator — Design

**Status:** Draft  
**Date:** 2026-07-22  
**Related:** #388 · complements #389 (unread after completion)

## 1. Goal

Show a small spinner next to a conversation’s title in the left chat-history
sidebar while that conversation has an agent run in progress on the client.
When the run ends (success, error, or cancel), remove the spinner. Rows that
are not running show no indicator.

Users who switch threads or only glance at the list should still know which
conversation is actively streaming or using tools.

## 2. Context

### What exists today

- Sidebar conversation rows are rendered by `ConversationRow` in
  `frontend/packages/web/components/layout/Sidebar.tsx`. Layout already
  includes pin icon, truncated title, optional group avatars, and a hover
  overflow menu. There is no in-flight visual.
- Agent streaming is tracked in `@cubeplex/core`’s `useMessageStore`
  (`frontend/packages/core/src/stores/messageStore.ts`):
  - `isStreaming: boolean`
  - `streamingConversationId: string | null`
- The store models **one active stream at a time**. Starting a new send
  aborts the previous controller. Bootstrap can seed HITL / last-run error
  state, but there is no multi-conversation “in-flight runs” client map
  and no list API field for “running now.”
- `useMessages` already scopes streaming UI to
  `isStreaming && streamingConversationId === conversationId` for the open
  chat; the sidebar never reads that state.

### Why change

Without a list-level signal, users assume a backgrounded chat has finished
or lose track of which row is still working. The open conversation already
shows stream chrome; the history list does not.

## 3. Approaches considered

| Approach | Pros | Cons |
| --- | --- | --- |
| **A. Drive spinner from existing `messageStore` stream flags** (recommended) | No API/schema work; matches real SSE lifecycle; one source of truth with chat chrome | Only covers the single in-tab stream the client holds; no multi-device |
| **B. Server `last_run_status` / active-run list on conversation list** | Survives reload and multi-tab if backend tracks runs | Larger backend + list payload change; issue marks this non-goal unless client state is insufficient |
| **C. Per-row poll of conversation bootstrap** | Accurate when opened | Wasteful N+1 traffic; not list-friendly |

**Recommendation: A.** Acceptance criteria in #388 are client-tab stream
visibility (“without requiring the user to open that conversation” while
still on the same client session). Document the single-stream / single-tab
limit; server in-flight sync is a follow-up.

## 4. Design

### 4.1 Running predicate

A conversation row is **running** when:

```text
messageStore.isStreaming === true
AND messageStore.streamingConversationId === convo.id
```

Notes:

- Keep the predicate in the UI layer (or a tiny selector helper). Do **not**
  invent a second “running” flag that can drift from stream end handlers.
- When the user switches conversations mid-stream, `streamingConversationId`
  stays on the conversation that owns the SSE; that row keeps the spinner
  even when it is not `activeId`.
- On normal completion, cancel, or error paths that clear
  `isStreaming` / `streamingConversationId`, the spinner disappears with no
  extra sidebar logic.
- **HITL paused (resolved):** Today `finalizePausedStream` / bootstrap set
  `isStreaming: false` while often leaving `streamingConversationId` set so
  `MessageList` can still gate the AskUser card. MVP **does not** show the
  running spinner for paused HITL — the predicate requires `isStreaming ===
  true` (live stream / tool execution). Waiting-on-user is not “running”; a
  separate HITL icon is out of scope. Do **not** widen the predicate to
  “any non-null `streamingConversationId`.”
- **Stream ownership invariant (store, not a second flag):** Spinner is a
  pure read of the two flags. Correctness requires that only the **current
  stream owner** may clear `isStreaming` / `streamingConversationId`. If
  conversation A is aborted because B starts a send, a late terminal
  handler for A must not wipe B’s flags. That ownership check belongs in
  `messageStore` terminal paths if missing; this feature must not paper over
  it with a parallel “running ids” map.

### 4.2 UX

- **Placement:** Inside `ConversationRow`, immediately after the truncated
  title (`flex-1 min-w-0 truncate` title node), before group avatars and the
  overflow menu. Use a shrink-0 spinner so truncation stays on the title.
- **Visual:** ~12–14px spinner — prefer existing `Loader2` + `animate-spin`
  from `lucide-react` (already used in the composer). Color:
  `text-muted-foreground` by default; ensure contrast on the active row
  (`bg-accent`).
- **Surfaces:** Same `ConversationRow` is used for pinned, recent, and
  topic-nested lists — one change covers all three.
- **a11y:** Spinner (or its wrapper) gets
  `aria-label={tSidebar('conversationRunning')}` (or equivalent under
  `sidebar` / `shellLayout`). Decorative motion should not be the only cue;
  the label is required.
- **Non-interference:** Pin, rename inline edit, delete, group avatars, and
  hover menu remain unchanged. Spinner must not force horizontal overflow
  of the row.

### 4.3 i18n

Add keys under the existing `sidebar` namespace in
`frontend/packages/web/messages/en.json` and `zh.json`, e.g.:

- `sidebar.conversationRunning` → “Conversation running” / Chinese equivalent

### 4.4 Coordination with unread (#389)

- **While running:** show spinner only (this issue).
- **After complete while user away:** unread dot is #389; do not show both
  for the same terminal moment (spinner clears first).

### 4.5 Explicit non-goals

- Tool name / token / phase text next to the title.
- Backend conversation-list schema changes or multi-run server sync.
- Desktop notifications.
- Failure / HITL-specific icons beyond whatever falls out of the running
  predicate above.
- Multi-tab BroadcastChannel sync of stream state.

## 5. Out of scope

- Multi-device or multi-tab in-flight lists.
- Expanding `messageStore` to track multiple concurrent streams (only if a
  later product decision allows concurrent runs).
- Unread badges (#389).
- Docs site page (no user-facing route/header/enum change beyond a list
  affordance; optional one-line mention if a sidebar UX doc already exists —
  not required for this PR pair of design-only).

## 6. Success criteria

1. Start an agent reply → that conversation’s sidebar row shows a spinner
   next to the title without opening the conversation.
2. Stream completes normally or user cancels → spinner goes away.
3. Switch to another conversation while the stream continues → spinner
   remains on the still-streaming conversation row.
4. Rename, pin, delete, group avatars, and row menu still work.
5. Visible spinner + accessible name (i18n).

## 7. Open questions (resolved for implementers)

| Question | Decision for this design |
| --- | --- |
| Multi-conversation background runs? | Out of scope; current store is single-stream. Spinner tracks that one id. |
| Server `active_run` on list? | Not for MVP. Revisit only if product requires post-reload running state. |
| Show spinner during HITL wait? | **No.** Paused HITL has `isStreaming: false`; spinner is live-stream only. |
