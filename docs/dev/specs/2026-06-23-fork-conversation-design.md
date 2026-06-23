# Fork conversation from a message

Status: design
Date: 2026-06-23
Owner: xfgong

## What this is

Let a user pick any message in a conversation and create a new conversation
that starts as an exact copy of the history up to (and including) that
message's turn. The new conversation is independent — sending new messages
in either side does not affect the other.

## Why

Two real workflows that today require copy-paste:

1. **Explore an alternate continuation.** "I want to try a different
   follow-up question from this point without losing the current thread."
2. **Re-derive from a known-good state.** A long thread drifted off-topic;
   the user wants a clean continuation from message N and the noise after
   it gone.

## Where the action lives

Per-message hover menu on user and assistant messages in
`components/chat/MessageList.tsx`. There is no per-message hover menu in
the codebase today — this feature introduces it. Single action for now:
**Fork conversation**.

A new conversation opens at `/w/{wsId}/conversations/{newId}`. The user
lands on the forked conversation, ready to type the next message.

## Fork point — what "from this message" actually means

cubepi's checkpointer (the source of truth for messages) only supports
forking *after a completed run*. A "run" starts when a user sends a
message and ends when the assistant finishes responding (or the run is
cancelled and marked complete). All messages inside a run share the same
`run_id`.

Concretely: every `UserMessage`, `AssistantMessage`, and `ToolResultMessage`
exposes `run_id: str | None` in the API payload (see
`cubepi/providers/base.py:128`). Forking is implemented as
`cp.fork(src_thread_id=src_conv_id, new_thread_id=new_conv_id, after_run_id=<run_id>, metadata=...)`.

UX mapping:

- **Click on an assistant message** → fork after this assistant's run.
  Result: the new conversation contains every message through the end of
  this turn.
- **Click on a user message** → also forks after this user message's run
  (same run as the assistant reply it produced). Result: same as above —
  the new conversation contains the user message *and* its assistant
  reply. We use the message's `run_id` directly; the user does not need
  to think about "which side of the turn."
- **Click on a tool-result message** → uses the same run_id; same
  semantics.

Edges where Fork is disabled (button greyed, tooltip explains):

- Message has no `run_id` (e.g., synthetic system nudges, very old rows
  pre-`060310ecfd8a` migration). Tooltip: "Cannot fork from this message."
- The run is not yet completed (the assistant is still streaming, or
  hit a HITL request and is paused). Tooltip: "Wait for the response to
  finish."
- The conversation is a group chat (`is_group_chat = True`). Forking a
  multi-participant conversation into a personal copy raises participant
  questions we do not want to decide in this PR. Tooltip: "Fork is not
  available in group chats."

## What carries over to the new conversation

From the source `conversations` row:

| Field | Behavior |
|---|---|
| `id` | fresh (`generate_public_id("conv")`) |
| `org_id`, `workspace_id` | same as source |
| `creator_user_id` | the caller (not the source's creator) |
| `topic_id` | always `NULL` — fork is a personal conversation owned by the caller (see "Fork is always personal" below) |
| `title` | `"{source_title}"` with an em-dash suffix `" — fork"`. Auto-title may overwrite later via the existing CAS path (`update_title_if_current`); manual rename always wins. |
| `model_key` | same as source |
| `thinking` | same as source |
| `has_messages` | `True` (we just copied messages) |
| `is_pinned` | `False` (fresh start; user can pin) |
| `is_group_chat` | `False` (forks are personal by construction; group-source is rejected above) |
| `deleted_at` | `NULL` |

From cubepi (handled by `cp.fork()`):

- All messages with `run_id IS NULL` or belonging to a completed run with
  `completion_seq <= cutoff`, with `seq` preserved.
- All completed runs satisfying the cutoff.
- A new `cubepi_threads` row with `parent_thread_id = src_conv_id`,
  `forked_at_seq = cutoff`, and `extra.fork = {source_conversation_id,
  forked_by_user_id, forked_at}` so the lineage survives in storage.

The `parent_thread_id` link is informational for now; we do not surface
"this is a fork of X" in the UI in this PR. A follow-up can add a small
"forked from …" header link.

## API contract

```
POST /api/v1/ws/{workspace_id}/conversations/{conversation_id}/fork
Body: { "after_run_id": "<run id>" }
Auth: require_member
```

Response (200): the standard conversation serializer payload (same shape
as `GET /{id}`).

Errors:

- 404 — source conversation not visible to caller (any of: doesn't exist,
  cross-workspace, cross-org, soft-deleted, caller is not a member of the
  topic/conv).
- 400 `{detail: {code: "run_not_completed"}}` — `after_run_id` is either
  unknown on this thread or not yet completed. cubepi raises the same
  `RunNotCompletedError` for both cases; we surface a single code.
- 400 `{detail: {code: "group_chat_not_supported"}}` — source
  `is_group_chat = True`.
- 400 `{detail: {code: "invalid_after_run_id"}}` — empty / whitespace body.
- 400 `{detail: {code: "source_has_no_messages"}}` — source conversation
  exists in cubebox but has no cubepi thread (drafted but never sent).
- 409 `{detail: {code: "new_thread_exists"}}` — the freshly-minted
  `new_id` already exists in cubepi (vanishingly rare given
  `generate_public_id`; we surface it rather than silently retrying so
  the operator sees the collision).

The `detail.code` shape matches the frontend's `toApiError()` mapper so
`ApiError.code` lights up the right toast on the client.

The destination conversation row is inserted in a single SQLAlchemy
transaction *after* `cp.fork()` succeeds. If the row insert fails, the
cubepi thread is orphaned — a follow-up cleanup job can reap orphan
threads whose `extra.fork.source_conversation_id` exists but whose
`conversations.id = thread_id` row is missing. We accept this leak
because (a) it's bounded by request failure rate, (b) cubepi storage is
cheap, and (c) the simpler alternative (insert row first, then fork)
leaves a conversation pointing at no messages on the inverse failure,
which is much worse UX.

## Visibility & RBAC — fork is always personal

- Caller must pass `ConversationRepository.get_by_id(src_id)` (B1–B4
  visibility rules) to see the source. Standard 404 on miss.
- The fork is created as a **personal conversation** (`topic_id = NULL`)
  owned by the caller. It is invisible to everyone else until the caller
  explicitly invites them via the existing upgrade-to-topic / invite
  flow.
- We do not inherit `src.topic_id` because the source may be visible to
  the caller only through B4 (a conv-level invite into a topic the
  caller is *not* a member of). Copying `topic_id` in that case would
  (a) publish the fork's content to every topic participant — a
  visibility leak from a conversation that was scoped to the conv-level
  invitee — and (b) leave the caller themselves without a B-rule that
  covers the new conv, so the post-fork redirect would 404.
- Going personal-only is also the right default for the primary use
  case ("explore an alternate continuation"). Promoting a fork into a
  topic is one extra click; recovering from a leaked fork is not.

## Out of scope (named so we don't argue later)

- "Fork into a different workspace/agent" — separate feature, separate PR.
- A "Forked from …" header in the conversation view — easy follow-up.
- Showing a tree of forks in the sidebar — explicit non-goal; the
  `parent_thread_id` is recorded but not visualized.
- Forking *before* a message ("redo this turn"). The cubepi API doesn't
  expose that; we'd need to introduce a new fork-before primitive.
