# Cubebox Memory System Design

## Overview

Cubebox needs a memory system that is native to collaborative workspaces, not a
copy of file-backed personal notes or automatic chat summarization. The system
should help the agent retain durable user preferences, workspace knowledge,
organization-level conventions, corrections, and proven operating procedures
while keeping shared knowledge visible, source-linked, and easy to correct.

This design introduces three memory scopes:

- Personal memory: private to the current user and usable across workspaces.
- Workspace memory: shared inside one workspace.
- Organization memory: shared across an organization.

The key product rule for v1 is scoped ownership with simple write permissions:

- Personal memory may be saved directly by the agent or the user.
- Workspace and organization memory may also be saved directly when the current
  user has access to that scope.

This keeps the first implementation small. User confirmation, proposal review,
and admin approval are explicit future governance layers, not v1 requirements.

## Goals

- Separate personal, workspace, and organization memory from the start.
- Keep every memory item source-linked and editable.
- Inject only relevant memory into agent context under a token budget.
- Support direct scoped memory saves through tools and UI.
- Provide a Memory Center where users can inspect and manage active memory.
- Preserve room for later automatic extraction, user confirmation, admin
  approval, embeddings, and external memory providers without requiring them in
  the first release.

## Non-Goals

- Building an external memory-provider plugin system in v1.
- Requiring workspace or organization admin approval in v1.
- Requiring inline user confirmation before shared memory is saved in v1.
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
shared-memory extraction would be risky before we have collaboration governance.
V1 therefore avoids background shared-memory writes and keeps memory creation in
explicit tool/UI paths.

Cubebox should combine the useful parts but take its own direction:

- Use explicit scopes instead of one global memory file.
- Use source-linked database rows instead of local JSON or Markdown files.
- Keep write paths direct in v1, then add confirmation/proposal governance once
  the core memory object model is stable.
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

Workspace memory can be saved directly by workspace members in v1. Confirmation
and stricter approval rules are future governance features.

### Organization Memory

Organization memory belongs to one `org_id` and is shared across workspaces in
that organization.

Examples:

- Organization-wide coding or review conventions.
- Default provider or tool usage policies.
- Standard operating procedures that apply across projects.

Organization memory can be saved directly by organization members in v1. Inline
confirmation and admin approval are future advanced features.

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
- `created_by_user_id`: user who created the item.
- `updated_by_user_id`: nullable.
- `created_at`, `updated_at`.
- `last_used_at`: nullable.
- `metadata`: JSON for future extension.

Invariants:

- `scope=personal` requires `owner_user_id` and requires `workspace_id IS NULL`.
- `scope=workspace` requires `workspace_id` and requires `owner_user_id IS NULL`.
- `scope=org` requires both `workspace_id IS NULL` and `owner_user_id IS NULL`.
- Every item must have at least one source field or `source_type=manual`.

### Future MemoryProposal and MemoryCandidate

`MemoryProposal` is reserved for inline confirmation and governance workflows. It
will store a proposed shared-memory write before it becomes active.

`MemoryCandidate` is reserved for background extraction, batch review, and skill
candidate workflows. Neither model is required in v1.

---

## Write Policy

### Direct Scoped Save

The agent or user may directly save memory when:

- The target scope is allowed for the current user.
- The user explicitly says to remember it, or the preference/correction/procedure
  is clear.
- The content is not sensitive data that should never be persisted.
- The source conversation, artifact, or tool result can be recorded.

Example:

```text
User: 以后回答我尽量简洁点。
Agent tool: memory_save(scope="personal", type="preference", ...)
Agent: 明白，之后我会尽量简洁。
```

```text
User: 这个 workspace 以后跑 E2E 都用 pnpm test:e2e。
Agent tool: memory_save(scope="workspace", type="procedure", ...)
Agent: 记下了，这个 workspace 的 E2E 验证使用 `pnpm test:e2e`。
```

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

- Creating `personal`, `workspace`, or `org` memory saves directly after scope
  permission checks.

### Suggested Create Request Shape

```json
{
  "scope": "workspace",
  "type": "procedure",
  "content": "Run E2E verification with `pnpm test:e2e` from `frontend/`."
}
```

---

## SSE Events

The run stream can add memory-specific informational events.

### `memory_saved`

Emitted after a tool-driven save creates memory.

```json
{
  "type": "memory_saved",
  "timestamp": "2026-05-08T12:00:00Z",
  "data": {
    "memory_id": "mem_xxx",
    "scope": "workspace",
    "memory_type": "procedure",
    "content": "Run E2E verification with `pnpm test:e2e`.",
    "source_conversation_id": "conv_xxx"
  }
}
```

This event is optional for UI polish in v1. The authoritative state lives in the
memory API and database.

---

## Agent Tool Design

### `memory_save`

For direct memory writes.

Inputs:

- `scope`: `personal | workspace | org`.
- `type`.
- `content`.
- `confidence`.
- `reason`.

Output:

```json
{
  "status": "saved",
  "memory_id": "mem_xxx"
}
```

The backend validates that the current user can write the requested scope.

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
- Optionally emit memory-saved events into the run event queue.

### Run Manager

`RunManager` already owns background execution and event persistence. It should
wire memory service/tool dependencies with:

- `ctx.user_id`
- `ctx.org_id`
- `ctx.workspace_id`
- `conversation_id`
- `run_id`
- `event_queue`

---

## Frontend Design

### Memory Center

Memory Center lives under settings or a workspace-level memory page.

Suggested tabs:

- Personal
- Workspace
- Organization
- Archived

V1 can implement the three active memory tabs and an archived view.

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

The web app should use React Query cache updates after mutations.

---

## Permissions

V1 keeps permissions simple:

- Personal memory: only owner can read/write.
- Workspace memory: workspace members can read. Any workspace member can create
  workspace memory.
- Organization memory: org members can read. Any org member can create org memory
  in v1.
- Delete/archive can follow the same rule in v1, with audit fields preserving
  who changed what.

Future:

- Inline confirmation for agent-initiated shared memory.
- Admin-only org memory confirmation.
- Workspace admin approval for shared memory.
- Proposal notifications for admins.
- Memory audit log surface.

---

## Error Handling

- Tool failure must not crash the run. Return a tool error and continue.
- Create/update/delete endpoints must return 404 if the memory item does not
  exist or is outside the current workspace/org scope.
- Create/update/delete endpoints must return 403 if the current user cannot
  access the requested scope.
- Memory injection failure should be logged and skipped; it must not block the
  conversation.

---

## Testing Strategy

### Backend

Focused tests:

- Personal memory direct save creates a user-owned item.
- `memory_save(scope=workspace)` creates a workspace memory item.
- `memory_save(scope=org)` creates an organization memory item.
- Cross-user personal memory is not visible.
- Workspace memory is visible to another workspace member.
- Workspace memory is not visible outside the workspace.
- Org memory is visible across workspaces in the same org.
- Memory injection includes personal + workspace + org items.
- Injected memory does not appear as persisted user message content in the
  checkpointer.

### Frontend

Focused E2E tests:

- Memory Center lists personal/workspace/org items with source links.
- Creating and editing personal memory updates the list.
- Creating and editing workspace memory updates the list for another workspace
  member.

---

## Rollout Plan

### Phase 1: Manual and Tool-Driven Memory

- Add database models and migrations.
- Add repositories and service.
- Add memory item APIs.
- Add memory tools.
- Add `MemoryMiddleware` retrieval/injection.
- Add basic Memory Center.

### Phase 2: Background Suggestions

- Add background candidate extraction for low-priority insights.
- Add candidate review surfaces if needed.

### Phase 3: Smarter Retrieval and Governance

- Add embeddings or hybrid search.
- Add inline confirmation for agent-initiated shared memory.
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

These defaults intentionally avoid admin workflows in v1. Later releases can add
inline confirmation, admin approval, stricter delete permissions, proposal
notifications, and an audit-oriented proposal history view without changing the
core memory item model.
