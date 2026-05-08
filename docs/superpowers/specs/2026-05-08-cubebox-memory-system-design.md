# Cubebox Memory System Design

## Overview

Cubebox needs a memory system that is native to collaborative workspaces, not a
copy of file-backed personal notes or automatic chat summarization. The system
should help the agent retain durable user preferences, workspace knowledge,
organization-level conventions, corrections, and proven operating procedures
while keeping shared knowledge visible and user-approved.

This design introduces three memory scopes:

- Personal memory: private to the current user and usable across workspaces.
- Workspace memory: shared inside one workspace.
- Organization memory: shared across an organization.

The key product rule is asymmetric write friction:

- Personal memory may be saved directly by the agent or the user.
- Workspace and organization memory require inline confirmation from the user
  who triggered the proposal before they become active.

This keeps personal preference capture low-friction while preventing silent
pollution of shared team knowledge.

## Goals

- Separate personal, workspace, and organization memory from the start.
- Make shared memory writes synchronous and explicit in the active conversation.
- Keep every memory item source-linked and editable.
- Inject only relevant memory into agent context under a token budget.
- Support direct personal saves and shared-memory proposals through tools.
- Provide a Memory Center where users can inspect and manage active memory.
- Preserve room for later automatic extraction, admin approval, embeddings, and
  external memory providers without requiring them in the first release.

## Non-Goals

- Building an external memory-provider plugin system in v1.
- Requiring workspace or organization admin approval in v1.
- Adding vector search or semantic reranking in v1.
- Treating LangGraph checkpoints as the long-term memory store.
- Creating an automatic background summarizer that silently updates shared
  memory.
- Replacing skills with memory. Repeated procedures may later become skill
  candidates, but memory v1 only stores the knowledge item.

---

## Research Summary

Hermes uses a small curated memory model: personal notes and user profile files,
a `memory` tool, strict size limits, and external memory-provider hooks. Its
strength is low risk and strong prompt-cache stability. Its weakness for Cubebox
is that it is optimized for a local single-user agent profile, not a
multi-tenant workspace product.

DeerFlow uses structured memory with summaries and facts, automatic background
LLM extraction, and a Settings > Memory management UI. Its strength is product
visibility and automatic learning. Its weakness for Cubebox is that automatic
shared-memory writes would be risky without real-time confirmation and
collaboration governance.

Cubebox should combine the useful parts but take its own direction:

- Use explicit scopes instead of one global memory file.
- Use source-linked database rows instead of local JSON or Markdown files.
- Use inline confirmation for shared memory, not post-hoc cleanup.
- Use memory as an execution aid for workspaces, artifacts, tools, and skills,
  not only as a user-profile summary.

---

## Memory Scopes

### Personal Memory

Personal memory belongs to one user and is private by default. It follows the
user across workspaces.

Examples:

- The user prefers concise answers.
- The user wants Chinese responses unless they ask otherwise.
- The user prefers test-first implementation.
- The user corrected the agent's communication style.
- The user has a recurring personal workflow preference.

Personal memory can be saved directly because it affects only the current user.
Users can edit or delete it later in the Memory Center.

### Workspace Memory

Workspace memory belongs to one `(org_id, workspace_id)` pair and is shared with
workspace members.

Examples:

- This repo uses `pnpm test:e2e` for E2E verification.
- The backend stores message history in LangGraph checkpointer state.
- Files uploaded to this workspace follow a specific naming convention.
- A particular MCP server is the preferred integration for this workspace.
- A deployment or sandbox command is known to work.

Workspace memory must be confirmed inline by the triggering user before it is
saved. Confirmation does not require admin privileges in v1.

### Organization Memory

Organization memory belongs to one `org_id` and is shared across workspaces in
that organization.

Examples:

- Organization-wide coding or review conventions.
- Default provider or tool usage policies.
- Standard operating procedures that apply across projects.

Organization memory also requires inline confirmation from the triggering user.
Admin approval is a future advanced feature.

---

## Memory Types

Memory items use a controlled type set:

- `preference`: personal preference or behavior style.
- `project_fact`: stable fact about a workspace/project.
- `procedure`: repeatable command, workflow, or operating procedure.
- `correction`: user correction that should prevent a repeated mistake.
- `decision`: durable rationale or chosen direction.
- `tool_note`: stable observation about a tool, MCP server, sandbox, or provider.
- `artifact_note`: durable relationship or fact about a saved artifact.
- `org_policy`: organization-level convention or policy.

The type is used for ranking, UI filters, and prompt rendering. It should not be
used as an access-control boundary.

---

## Data Model

### MemoryItem

`MemoryItem` stores active or archived memory.

Fields:

- `id`: short public id, prefix `mem`.
- `org_id`: owning organization.
- `workspace_id`: nullable. Present for workspace memory, null for personal and
  organization memory.
- `owner_user_id`: nullable. Present for personal memory.
- `scope`: `personal | workspace | org`.
- `type`: memory type enum.
- `content`: canonical memory text.
- `confidence`: float from `0.0` to `1.0`.
- `status`: `active | archived`.
- `source_type`: `conversation | tool_result | artifact | manual | import`.
- `source_conversation_id`: nullable.
- `source_run_id`: nullable.
- `source_artifact_id`: nullable.
- `source_message_refs`: JSON list of source message identifiers or offsets.
- `created_by_user_id`: user who created or confirmed the item.
- `updated_by_user_id`: nullable.
- `created_at`, `updated_at`.
- `last_used_at`: nullable.
- `metadata`: JSON for future extension.

Invariants:

- `scope=personal` requires `owner_user_id` and requires `workspace_id IS NULL`.
- `scope=workspace` requires `workspace_id` and requires `owner_user_id IS NULL`.
- `scope=org` requires both `workspace_id IS NULL` and `owner_user_id IS NULL`.
- Every item must have at least one source field or `source_type=manual`.

### MemoryProposal

`MemoryProposal` stores a shared-memory write that is waiting for inline user
confirmation.

Fields:

- `id`: short public id, prefix `mprop`.
- `org_id`.
- `workspace_id`: nullable for organization proposals.
- `proposed_scope`: `workspace | org`.
- `type`.
- `content`.
- `reason`: why the agent believes this should be shared memory.
- `confidence`.
- `source_type`.
- `source_conversation_id`.
- `source_run_id`.
- `source_artifact_id`: nullable.
- `source_message_refs`: JSON list.
- `proposed_by_user_id`.
- `status`: `pending | accepted | rejected | saved_personal | expired`.
- `decision_by_user_id`: nullable.
- `decision_at`: nullable.
- `created_at`, `updated_at`.
- `expires_at`: nullable, default 7 days.
- `metadata`.

`MemoryProposal` is not the same as a background candidate. It is an active
conversation proposal that should be resolved in-line whenever possible.

### Future MemoryCandidate

`MemoryCandidate` is reserved for background extraction, batch review, and skill
candidate workflows. It is not required in v1.

---

## Write Policy

### Direct Personal Save

The agent may directly save personal memory when:

- The memory affects only the current user.
- The user explicitly says to remember it, or the preference/correction is clear.
- The content is not sensitive workspace knowledge masquerading as preference.

Example:

```text
User: 以后回答我尽量简洁点。
Agent tool: memory_save(scope="personal", type="preference", ...)
Agent: 明白，之后我会尽量简洁。
```

### Shared Memory Proposal

The agent must propose, not save, workspace or organization memory.

Example:

```text
User: 这个 workspace 以后跑 E2E 都用 pnpm test:e2e。
Agent tool: memory_propose(scope="workspace", type="procedure", ...)
System event: memory_confirmation
```

The UI renders the confirmation card in the current conversation:

```text
Save as workspace memory?

This workspace runs E2E verification with `pnpm test:e2e`.

[Save to workspace] [Save only for me] [Edit] [Don't save]
```

If the model calls `memory_save(scope="workspace" | "org")`, the backend must not
save directly. It should create a proposal and return
`confirmation_required`.

### Decision Options

The proposal decision API supports:

- `save_shared`: create a workspace/org `MemoryItem`.
- `save_personal`: create a personal `MemoryItem` instead.
- `edit_and_save_shared`: save edited content in the proposed shared scope.
- `edit_and_save_personal`: save edited content as personal memory.
- `reject`: reject the proposal.

---

## Read and Injection Policy

Every agent run receives a memory context assembled from:

- Personal memory for `ctx.user.id`.
- Workspace memory for `ctx.workspace_id`.
- Organization memory for `ctx.org_id`.

The retriever ranks by:

1. Scope/type priority.
2. Text relevance to the current user message.
3. Confidence.
4. Recency and prior usage.

V1 retrieval can use Postgres text matching and deterministic ranking. Embedding
retrieval is a later improvement.

Prompt rendering should use explicit fenced sections:

```text
<memory>
<personal_memory>
- [preference] User prefers concise Chinese responses.
</personal_memory>

<workspace_memory>
- [procedure] Run backend checks with `make check` from `backend/`.
</workspace_memory>

<organization_memory>
- [org_policy] Use PR descriptions that include test evidence.
</organization_memory>
</memory>
```

Corrections should be rendered before ordinary memories inside each scope.

The middleware must keep memory injection separate from persisted user messages.
Injected memory should affect the LLM call but should not be written into
LangGraph checkpoint message history as if the user typed it.

---

## Conflict Rules

Memory conflicts are resolved by semantic domain:

- Interaction style: personal memory wins.
- Workspace/project procedure: workspace memory wins.
- Organization policy: organization memory wins unless a workspace memory is a
  more specific operational override.
- Explicit correction: correction memory outranks ordinary memory in the same
  semantic domain.

When conflict is detected, the agent should not silently choose if the action is
high-impact. It should mention the conflict and ask for clarification.

General priority:

```text
explicit correction
> scoped procedure/policy for the current task
> personal preference
> workspace project fact
> organization background
> conversation-local context
```

---

## API Design

Routes are workspace-scoped where the operation needs workspace context.

### Memory Items

```text
GET    /api/v1/ws/{workspace_id}/memory
POST   /api/v1/ws/{workspace_id}/memory
PATCH  /api/v1/ws/{workspace_id}/memory/{memory_id}
DELETE /api/v1/ws/{workspace_id}/memory/{memory_id}
```

Query parameters:

- `scope=personal|workspace|org|all`
- `type=...`
- `status=active|archived`
- `q=...`

Create behavior:

- Creating `personal` memory saves directly.
- Creating `workspace` or `org` from the normal API can save directly because it
  is a user-initiated UI action, not an agent-initiated hidden write.

### Proposals

```text
GET  /api/v1/ws/{workspace_id}/memory/proposals
POST /api/v1/ws/{workspace_id}/memory/proposals
POST /api/v1/ws/{workspace_id}/memory/proposals/{proposal_id}/decision
```

The proposal create endpoint is used by tools and by future background
extractors. The decision endpoint creates the final `MemoryItem` or marks the
proposal rejected.

### Suggested Request Shape

```json
{
  "decision": "edit_and_save_shared",
  "content": "Run E2E verification with `pnpm test:e2e` from `frontend/`."
}
```

---

## SSE Events

The run stream adds memory-specific events.

### `memory_confirmation`

Emitted when a shared-memory proposal needs user confirmation.

```json
{
  "type": "memory_confirmation",
  "timestamp": "2026-05-08T12:00:00Z",
  "data": {
    "proposal_id": "mprop_xxx",
    "scope": "workspace",
    "memory_type": "procedure",
    "content": "Run E2E verification with `pnpm test:e2e`.",
    "reason": "The user corrected the verification command for this workspace.",
    "source_conversation_id": "conv_xxx",
    "choices": [
      "save_shared",
      "save_personal",
      "edit",
      "reject"
    ]
  }
}
```

### `memory_saved`

Emitted after a proposal decision saves memory.

### `memory_rejected`

Emitted after a proposal is rejected.

These events are part of the run event log so reconnects can replay pending
confirmation cards.

---

## Agent Tool Design

### `memory_save`

For personal memory direct writes.

Inputs:

- `scope`: must be `personal` in v1 for direct save.
- `type`.
- `content`.
- `confidence`.
- `reason`.

If `scope` is `workspace` or `org`, the tool returns
`confirmation_required` and creates a proposal instead of saving.

### `memory_propose`

For shared memory.

Inputs:

- `scope`: `workspace | org`.
- `type`.
- `content`.
- `confidence`.
- `reason`.

Output:

```json
{
  "status": "confirmation_required",
  "proposal_id": "mprop_xxx"
}
```

The tool should emit enough metadata for the SSE layer to render an inline
confirmation card.

### `memory_search`

Optional in v1. Lets the agent inspect memory without relying only on automatic
injection.

Inputs:

- `query`.
- `scope`: optional.
- `type`: optional.

---

## Backend Integration

### Files

Expected new modules:

- `backend/cubebox/models/memory.py`
- `backend/cubebox/repositories/memory.py`
- `backend/cubebox/services/memory.py`
- `backend/cubebox/middleware/memory.py`
- `backend/cubebox/tools/builtin/memory.py`
- `backend/cubebox/api/routes/v1/memory.py`

### Agent Factory

`create_cubebox_agent()` should accept memory dependencies and add
`MemoryMiddleware` near the system-prompt and attachment-hint layers.

Responsibilities:

- Retrieve relevant personal/workspace/org memory for the current run.
- Inject it into the model call without mutating persisted user messages.
- Provide memory tools with request-scoped org/workspace/user context.
- Emit proposal events into the run event queue.

### Run Manager

`RunManager` already owns background execution and event persistence. It should
wire memory service/tool dependencies with:

- `ctx.user_id`
- `ctx.org_id`
- `ctx.workspace_id`
- `conversation_id`
- `run_id`
- `event_queue`

Memory proposal events should be written to the Redis run stream so they replay
on reconnect.

---

## Frontend Design

### Inline Confirmation Card

Chat UI should render `memory_confirmation` events as inline cards in the
conversation stream.

Card contents:

- Scope badge: Personal fallback, Workspace, or Org.
- Type badge.
- Proposed memory content.
- Short reason.
- Source link to current conversation if useful.
- Actions:
  - Save to workspace/org.
  - Save only for me.
  - Edit.
  - Do not save.

The edit flow can be a small modal or inline expandable textarea.

The card should show final state after decision:

- Saved to workspace.
- Saved only for me.
- Not saved.

### Memory Center

Memory Center lives under settings or a workspace-level memory page.

Suggested tabs:

- Personal
- Workspace
- Organization
- Proposals
- Archived

V1 can implement the first three active memory tabs and show pending proposals
that are still unresolved.

Memory item row/card:

- Content.
- Scope.
- Type.
- Confidence.
- Source conversation/artifact link.
- Created by.
- Last updated.
- Edit/archive/delete actions.

Filters:

- Text search.
- Type.
- Scope.
- Source.

### API Client

Frontend core should expose:

- `listMemory`
- `createMemory`
- `updateMemory`
- `archiveMemory`
- `listMemoryProposals`
- `decideMemoryProposal`

The web app should use React Query cache updates after mutations.

---

## Permissions

V1 keeps permissions simple:

- Personal memory: only owner can read/write.
- Workspace memory: workspace members can read. Any workspace member can confirm
  and create workspace memory.
- Organization memory: org members can read. Any org member can confirm and
  create org memory in v1.
- Delete/archive can follow the same rule in v1, with audit fields preserving
  who changed what.

Future:

- Admin-only org memory confirmation.
- Workspace admin approval for shared memory.
- Proposal notifications for admins.
- Memory audit log surface.

---

## Error Handling

- Tool failure must not crash the run. Return a tool error and continue.
- Proposal creation failure should be visible in the stream as a normal error
  event if it prevents rendering the confirmation card.
- Decision endpoint must return 404 if the proposal does not exist or is outside
  the current workspace/org scope.
- Decision endpoint must return 409 if the proposal is already decided.
- Memory injection failure should be logged and skipped; it must not block the
  conversation.

---

## Testing Strategy

### Backend

Focused tests:

- Personal memory direct save creates a user-owned item.
- `memory_save(scope=workspace)` creates a proposal, not an active item.
- Proposal decision `save_shared` creates workspace/org memory.
- Proposal decision `save_personal` creates personal memory.
- Cross-user personal memory is not visible.
- Workspace memory is visible to another workspace member.
- Workspace memory is not visible outside the workspace.
- Org memory is visible across workspaces in the same org.
- Memory injection includes personal + workspace + org items.
- Injected memory does not appear as persisted user message content in the
  checkpointer.

### Frontend

Focused E2E tests:

- Inline memory confirmation appears from a streamed event.
- Save to workspace updates the card state.
- Save only for me updates the card state.
- Edit and save sends edited content.
- Reject updates the card state.
- Memory Center lists personal/workspace/org items with source links.

---

## Rollout Plan

### Phase 1: Manual and Tool-Driven Memory

- Add database models and migrations.
- Add repositories and service.
- Add memory item and proposal APIs.
- Add memory tools.
- Add `MemoryMiddleware` retrieval/injection.
- Add inline confirmation card.
- Add basic Memory Center.

### Phase 2: Background Suggestions

- Add background candidate extraction for low-priority insights.
- Keep shared-memory extraction proposal-based.
- Add candidate review surfaces if needed.

### Phase 3: Smarter Retrieval and Governance

- Add embeddings or hybrid search.
- Add admin approval for org/workspace shared memory.
- Add audit log UI.
- Add skill-candidate conversion.

---

## V1 Defaults

The first implementation uses these defaults:

- Organization memory is retrieved only while the user is operating inside a
  workspace that belongs to that organization.
- Workspace members can create, edit, and archive workspace memory.
- Organization members can create, edit, and archive organization memory.
- Rejected proposals remain in the database but are hidden from the default
  Memory Center view.
- Memory confirmation cards are non-blocking. The agent can finish its response
  while the card waits for a decision.

These defaults intentionally avoid admin workflows in v1. Later releases can add
admin approval, stricter delete permissions, proposal notifications, and an
audit-oriented proposal history view without changing the core memory/proposal
model.
