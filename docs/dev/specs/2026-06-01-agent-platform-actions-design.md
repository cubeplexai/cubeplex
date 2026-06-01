# Agent Platform Actions — Design

**Date:** 2026-06-01
**Status:** Approved (brainstorming)
**Branch:** `feat/agent-schedule-tools`

## Problem

cubebox exposes platform capabilities (skills, scheduled tasks, conversations,
MCP connectors, memory, …) as workspace-scoped REST routes. We want the
in-conversation agent to perform those same operations on the user's behalf.

Today this is done **per-capability, ad-hoc**: each capability hand-writes a set
of `create_X_tool(...)` factories in `run_manager.py` and manually threads
`org_id` / `workspace_id` / `user_id` through them (this is how the skill tools —
`find_skills`, `preview_skill`, `install_skill`, `load_skill` — are wired). More
agent-operable capabilities are coming continuously, so the ad-hoc approach
accumulates four problems:

1. **Logic duplicated.** Business logic (validation, schedule computation,
   authorization) lives in the route handler; a tool that needs the same
   behavior must either re-extract it or reimplement it.
2. **DI boilerplate.** Every tool re-threads scope (org/ws/user) + a DB session
   by hand; `run_manager.py` grows per capability.
3. **Permission drift.** REST routes enforce authorization via FastAPI deps
   (`require_member`, owner-or-admin). A tool that skips those checks lets the
   agent perform operations the user is not authorized for.
4. **Behavior drift.** A tool and its route should validate and fail
   identically; two code paths drift.

This branch establishes a **unified mechanism** for agent-operable platform
capabilities and lands **scheduled tasks** as the first capability built on it.

## Goals

- One reusable pattern: adding a new agent-operable capability = write its
  service (needed anyway) + declare its operations. No new wiring in
  `run_manager.py`.
- Single source of truth per capability (the service); REST route and agent
  tool are thin adapters over it.
- Authorization enforced in the service, so both front doors get it.
- Agent tool-list growth bounded: **one tool per capability**, not one per
  operation.

## Non-Goals

- Migrating the existing skill tools onto this mechanism. Skills stay as-is
  (already shipped); migration is a follow-up.
- A sandbox CLI surface. The agent shell only exists inside the sandbox and
  `sandbox.enabled` defaults to `false`, so a CLI surface would be unavailable
  by default. The registry is designed so a CLI (or a single global meta-tool)
  surface can be added later over the same services/actions without rework.
- New scheduled-task domain features. The capability exposes the existing
  scheduled-task behavior; it does not add scheduling semantics.

## Decisions (from brainstorming)

- **Operations exposed (8):** `list`, `get`, `list_runs`, `create`, `update`,
  `pause`, `resume`, `delete`.
- **Confirmation model:** soft — the tool description instructs the agent to act
  only on explicit user request (mirrors `install_skill`). No hard
  confirm-parameter gate.
- **Run target on create:** support both `new_each_run` (default) and `fixed`;
  the tool accepts pinning to the current conversation (the builder injects the
  current `conversation_id`).
- **Tool granularity:** one tool per capability with an `operation`
  discriminator (Pydantic discriminated union → `oneOf` in the JSON schema, so
  the model still sees per-operation fields).
- **Surface (v1):** native cubepi `AgentTool` (does not depend on sandbox;
  supports widgets; reliable schema).
- **Routes:** refactored into thin adapters that delegate to the service.

## Architecture

Three layers plus a registry. The **service + action registry is the invariant
foundation**; the surface (native tool now, CLI/meta-tool later) is a swappable
facade over it.

```
                 ┌─────────────────────────────┐
   REST route ──▶│   Scope-aware Service        │ ← single source of truth
  (thin adapter) │   (ScopeContext, session,    │   validation + business
                 │    input) -> result          │   authorization + operation
   Agent tool ──▶│                             │
 (registry-built)└─────────────────────────────┘
```

### New module layout

```
backend/cubebox/
├── agents/actions/
│   ├── context.py        ScopeContext + builder from RequestContext / RunContext
│   ├── capability.py     AgentOperation, AgentCapability, domain exceptions
│   ├── builder.py        build_capability_tool(cap, context_factory) -> AgentTool
│   ├── registry.py       AGENT_CAPABILITIES, tools_for_run(context_factory)
│   └── capabilities/
│       └── scheduled_tasks.py   declares the scheduled_tasks capability
├── services/
│   └── scheduled_task.py        ScheduledTaskService (source of truth)
├── api/routes/v1/
│   └── ws_scheduled_tasks.py    refactored to thin adapter (delegates to service)
└── streams/
    └── run_manager.py           appends tools_for_run(context_factory)
```

### Components

**`ScopeContext`** (`agents/actions/context.py`)
Carries everything an operation needs to be scoped and authorized:
`org_id`, `workspace_id`, `user_id`, `role`, `conversation_id | None`.
Two builders:
- `from_request(ctx: RequestContext)` — for routes (role already present).
- async build for runs — `RunContext` lacks `role`, so the run-side factory
  looks up the user's workspace membership role once and injects it; it also
  carries the current `conversation_id` so `create` can pin a fixed target.

**`ScheduledTaskService`** (`services/scheduled_task.py`)
The source of truth. Methods take `(ctx: ScopeContext, session, input)` and
return domain objects / dicts:
- `list_tasks`, `get_task`, `list_runs` — reads (membership is enough).
- `create`, `update`, `pause`, `resume`, `delete` — mutations; enforce
  owner-or-admin internally.

All logic currently in the route module moves here: cron/timezone validation
(reuse the Pydantic validators / `croniter`), `_initial_next_fire`,
`_resume_next_fire`, `_to_utc_naive`, target-conversation ownership check, and
`owner_user_id` assignment (`= ctx.user_id`). Failures raise domain exceptions:
`NotFound`, `PermissionDenied`, `InvalidInput`.

**Action registry + generic builder** (`agents/actions/capability.py`,
`builder.py`, `registry.py`)
- `AgentOperation`: `name` (e.g. `"create"`), `description` (for the LLM),
  `input_model` (operation-specific args), `handler`
  (`(ScopeContext, session, input) -> Awaitable[result]`, typically a bound
  service method).
- `AgentCapability`: `name` (tool name, e.g. `"scheduled_tasks"`),
  `description`, `operations: list[AgentOperation]`.
- `build_capability_tool(cap, context_factory)` produces one cubepi
  `AgentTool`. The tool input is a discriminated union over operations keyed by
  an `operation: Literal[...]` field. `_execute`: parse → dispatch to the
  matching operation → build `ScopeContext` + open a session via
  `context_factory` → call the handler → serialize the result as
  `AgentToolResult`; domain exceptions map to `is_error=True` text.
- `registry.py`: `AGENT_CAPABILITIES = [SCHEDULED_TASKS_CAPABILITY]` and
  `tools_for_run(context_factory) -> list[AgentTool]`.

**`scheduled_tasks` capability** (`agents/actions/capabilities/scheduled_tasks.py`)
Declares the 8 operations, each pointing at a `ScheduledTaskService` method,
with LLM-facing descriptions (including the soft "only on explicit user
request" instruction for mutations and the `new_each_run` default for
`create`).

**Front doors**
- Route (`ws_scheduled_tasks.py`): FastAPI auth dep → `ScopeContext.from_request`
  → service call → map domain exceptions to HTTP (`NotFound`→404,
  `PermissionDenied`→403, `InvalidInput`→422) → serialize via existing
  `_to_out` / `ScheduledTaskOut`.
- Agent tool: `run_manager` builds a `context_factory` from `RunContext` (+ role
  lookup + current `conversation_id` + session maker) and appends
  `tools_for_run(context_factory)` to the builtin tool list.

## Data Flow — `create` example

1. Agent calls `scheduled_tasks(operation="create", name=…, schedule_kind=…, …,
   target="new_each_run")`.
2. Builder parses the discriminated union, opens a session, builds
   `ScopeContext` (org/ws/user/role from the run; `conversation_id` if
   `target="current_conversation"`).
3. `ScheduledTaskService.create(ctx, input)` validates, sets
   `owner_user_id = ctx.user_id`, computes `next_fire_at`, persists.
4. Result serialized to `AgentToolResult` text (task id, name, next_fire_at).
   The same `create` called from the route returns `ScheduledTaskOut`.

## Error Handling

The service raises domain exceptions; each front door maps them:

| Domain exception   | Route   | Tool                          |
|--------------------|---------|-------------------------------|
| `NotFound`         | 404     | `is_error=True`, message      |
| `PermissionDenied` | 403     | `is_error=True`, message      |
| `InvalidInput`     | 422     | `is_error=True`, message      |

One validation path, two consistent surfaces.

## Testing

- **Service unit tests** — `create` / `update` / `pause` / `resume` / `delete`
  + `next_fire_at` computation + authorization (owner allowed, admin allowed,
  other denied). This is where the bulk of behavior is verified.
- **Route tests** — existing scheduled-task route tests must stay green after
  the thin-adapter refactor (behavior-preserving guard).
- **Builder unit tests** — discriminated-union dispatch and
  domain-exception → `AgentToolResult` mapping, using a fake capability.
- **E2E** — an agent run that calls the `scheduled_tasks` tool to create a task
  and asserts it lands in the DB (following the existing builtin-tool test
  pattern). Full LLM-driven end-to-end is optional given nondeterminism.

## Risks / Open Items

- **Route refactor regression.** Mitigated by keeping it behavior-preserving and
  relying on existing route tests as the guard.
- **Role lookup cost on every run.** One membership query per run when building
  the context factory; acceptable, and only paid when the capability tools are
  constructed.
- **Discriminated-union schema size.** Bounded by per-capability tools; if a
  capability has many operations the schema grows, but the tool count stays at
  one per capability.

## Follow-ups (out of scope here)

- Migrate the skill tools onto this mechanism.
- Add a CLI / global meta-tool surface once sandbox networking stabilizes.
- Register further capabilities (conversations, MCP connectors, …) as needed.
