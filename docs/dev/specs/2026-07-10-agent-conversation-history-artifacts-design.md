# Agent conversation history and artifacts design

**Status:** approved
**Date:** 2026-07-10

## Goal

Expose read-only conversation history and workspace artifact discovery through
the agent capability mechanism. The agent can retrieve a bounded number of
recent user-initiated turns, expand one tool result only when needed, and find
artifacts from conversations it is allowed to access.

## Scope

Add two deferred capability groups:

- `conversation_history`
- `artifacts`

Do not add any destructive artifact operation. In particular, this change does
not expose artifact deletion to agents.

## Conversation history capability

### `conversation_history_search`

Input:

- `query`: non-empty search text.
- `n`: maximum search results, default `8`, range `1..20`.

The operation uses the existing hybrid conversation search service and returns
the existing result fields: conversation ID, title, snippet, matched sequence,
match timestamp, and score. It only searches conversations visible to the
current user in the current workspace.

### `conversation_history_read`

Input:

- `conversation_id`.
- `n`: number of user-initiated turns, default `5`.
- `max_tokens`: estimated output-token budget, default `4000`, range `256..12000`.
- `before_seq`: optional exclusive cursor for reading earlier turns.

A turn begins with a user message and includes all following non-user messages
until the next user message. With no cursor, the operation selects the most
recent turns. It returns selected turns in chronological order.

The response is a formatted agent contract, not the raw cubepi checkpoint
payload. Each turn contains user content, assistant text, and a compact list of
tool calls (`tool_call_id`, name, and redacted/compact arguments). It does not
embed tool-result bodies. It identifies whether each tool call completed or
errored when that can be resolved from the stored messages.

When one oversized turn contains more tool calls than fit, the turn retains a
usable prefix of calls and includes `tool_calls_omitted` with the number not
returned. Only IDs returned in that prefix can be passed to the targeted
tool-result operation. Re-read the same turn with a larger `max_tokens` budget
to expose more calls; normal history reads never include tool-result bodies.

The formatter estimates output tokens and includes complete turns while within
`max_tokens`. If one individual turn exceeds the budget, its textual content is
truncated and marked as such. Its budget includes the full response envelope,
not only the turn list. The response includes `has_more`, `next_before_seq`,
`estimated_tokens`, and `truncated` so the agent can decide whether to page or
fetch a more targeted result.

### `conversation_history_tool_result`

Input:

- `conversation_id`.
- `tool_call_id`.
- `max_tokens`: estimated output-token budget, default `2000`, range `256..12000`.

This operation obtains the result associated with exactly one historical tool
call. It applies the same conversation access check and returns a bounded,
formatted result body plus error and truncation metadata. It is the only
history operation that returns detailed tool output.

## Artifacts capability

### `artifacts_list`

Input:

- `n`: maximum artifacts, default `10`, range `1..50`.
- `q`: optional case-insensitive name search.
- `artifact_type`: optional exact type filter.
- `offset`: optional pagination offset, default `0`.

The operation reuses the workspace artifact repository query and the current
user's accessible-conversation subquery. It returns artifact metadata only:
ID, conversation ID, name, type, description, entry file, MIME type, version,
and timestamps. It does not read sandbox files or object-store content.

## Architecture and authorization

The capability handlers call services and repositories, never FastAPI route
functions. The conversation-history formatter lives in
`cubeplex.services.conversation_search.history`, alongside the existing
checkpointer-to-message sequence and searchable-text logic. It reuses that
package's message/sequence semantics but adds the distinct agent-facing work of
turn grouping, tool-call/result correlation, and bounded rendering.
`conversation_history` receives the run's embedding provider and lexical
backend as explicit runtime dependencies so its search behavior matches the
existing workspace API. Both capabilities open a per-call scoped session
through the action context.

Every operation is read-only and is registered for interactive, scheduled, and
IM runs. Before loading messages, tool results, or artifacts, handlers enforce
the same org, workspace, and current-user visibility rules as their existing
REST equivalents. No operation is marked `always_mutable`.

## Tests and documentation

Add focused backend e2e tests covering:

- inaccessible conversations and their artifacts never appear;
- history reads return recent complete turns in chronological order;
- tool-result bodies are absent from normal reads and available only through
  the targeted operation;
- `n`, cursors, and token budgets bound output as specified.

Add or update the matching site documentation to describe the new agent
capabilities and their read-only behavior.
