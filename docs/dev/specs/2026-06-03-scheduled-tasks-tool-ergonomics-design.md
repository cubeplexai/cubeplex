# Scheduled-tasks tool ergonomics

Date: 2026-06-03  
Status: design

## Why

Trace `7acc0ec0c3eeb7a78260fa52a801b363` (conversation
`conv-1fwZQ8u3ZDukx3`, kimi-k2.6, 2026-06-03): user asks the agent to create
several daily growth tasks. The agent calls `scheduled_tasks` five times. The
first three fail with cryptic pydantic validation errors; only the fourth
call succeeds. Each retry costs ~25 s of latency and ~196k input tokens (the
full conversation context is replayed on every chat call). The user types
"算了你先停一下.." (`forget it, stop`) and abandons the run.

The three failed calls:

| # | Argument the model sent | Tool error |
|---|---|---|
| 121 | `action: "create", task: { schedule: { type: "cron", expression: "0 9 * * *" } }` | `Unable to extract tag using discriminator 'operation'` |
| 122 | `operation: "create", schedule: "0 9 * * *"` | `create.schedule_kind Field required` |
| 123 | `operation: "create", schedule_kind: "cron", schedule: "0 9 * * *"` | `cron_expr required for cron schedule` |
| 124 | `operation: "create", schedule_kind: "cron", cron_expr: "0 9 * * *"` | ok |

Each error reveals exactly one piece of the schema. The model has no way to
learn the whole shape in one shot, so it pays for three round trips before
the first success. The same pattern will hit any sufficiently complex
capability tool we add — `scheduled_tasks` is just the first one big enough
to see it.

Secondary issue from the same run: the user said "让这些任务都在这个会话运行"
("run these tasks in this conversation"). The `CreateInput` schema has a
`target` field whose default is `"new_each_run"`. The model never set
`target`, so every task was created with `new_each_run` — silently the
opposite of what the user asked for. Nothing in the tool description tells
the model that `target='current_conversation'` exists, or that it can be
passed without knowing the conversation ID (the handler fills it in from
`ScopeContext`).

## What changes

Five changes, all inside the existing capability + builder framework. No new
abstractions, no MCP, no tool splitting.

### 1. Operation descriptions get a minimal example payload

Each `AgentOperation.description` in `backend/cubebox/agents/actions/capabilities/scheduled_tasks.py`
gets a one-line canonical input. The model can copy these without parsing
the discriminated-union JSON Schema.

For `create`, include three examples — one per `schedule_kind` — because the
required fields differ between them. Example shape:

```
create — schedule a new task. Examples:
  cron daily 09:00 UTC:
    {"operation":"create","name":"morning-reply","prompt":"...","schedule_kind":"cron","cron_expr":"0 9 * * *"}
  every 30 minutes:
    {"operation":"create","name":"poll","prompt":"...","schedule_kind":"interval","interval_seconds":1800}
  one-shot at a specific time:
    {"operation":"create","name":"remind","prompt":"...","schedule_kind":"once","run_at":"2026-06-10T15:00:00Z"}
To bind the task to the conversation this tool was called from, add
`"target":"current_conversation"`. You do not need to know the conversation
ID — the backend fills it in. To open a fresh conversation on each fire
(default), omit `target` or pass `"new_each_run"`.
```

`update`, `pause`, `resume`, `delete`, `get`, `list_runs` each get one
example. `list` takes no arguments and the description says so.

The capability-level description (currently `scheduled_tasks.py:208-213`)
gets a short pointer ("see each operation for example payloads"). We do not
duplicate the examples at the capability level — that would double the
prompt cost.

### 2. Pydantic ValidationError gets translated for the model — deferred

Confirmed during planning: pydantic validation runs upstream of our
capability builder, inside cubepi at `cubepi/cubepi/agent/tools.py:114-117`,
which wraps `str(exc)` and returns it as the tool result. The friendly
translation therefore belongs in cubepi (per the project rule
"cubepi upstream-first"), not in `backend/cubebox/agents/actions/builder.py`.

Because all three failure modes in the trace above are also fixed by changes
1, 3, 4 (clearer per-op descriptions + nested schedule union), the
translation is no longer load-bearing for this trace — it is defensive
hygiene for future failures. Moving it to a separate follow-up (cubepi PR +
pin bump) keeps this PR scoped to a single repo.

Tracked as follow-up in the section below.

### 3. UpdateInput uses the same `target` sentinel as CreateInput

Today `CreateInput.target` is the friendly enum
`"new_each_run" | "current_conversation"` — the model never touches a
conversation ID. `UpdateInput` skipped the sentinel and exposes
`target_mode` (`"new_each_run" | "fixed"`) + `target_conversation_id`
directly. The next agent that tries to update a task to point at the
current conversation will rediscover the same trap from a different angle.

Change `UpdateInput` to use `target: Literal["new_each_run", "current_conversation"] | None = None`,
mirroring `CreateInput`. The `_handle_update` handler translates it to
`target_mode` / `target_conversation_id` the same way `_handle_create`
already does, including the `ScopeContext.conversation_id` lookup.

DB schema, frontend form, and `ScheduledTaskService` are unchanged —
`target_mode` + `target_conversation_id` is the right shape for a form with
a radio button and a conditional dropdown. Only the LLM-facing layer
collapses the two into one sentinel.

### 4. Conditional schedule fields move into a nested discriminated union

Today `CreateInput` is flat:

```python
schedule_kind: Literal["cron", "interval", "once"]
cron_expr: str | None = None
interval_seconds: int | None = Field(default=None, ge=60)
run_at: datetime | None = None
```

The JSON Schema cannot express "cron requires cron_expr" — all three
fields look optional. That constraint lives in
`ScheduledTaskService.create` as a runtime `ActionInvalidInput`, which is
why retry #3 above failed instead of #1.

Replace with a nested discriminated union on `schedule`:

```python
class CronSchedule(BaseModel):
    kind: Literal["cron"]
    cron_expr: str
    timezone: str = "UTC"

class IntervalSchedule(BaseModel):
    kind: Literal["interval"]
    interval_seconds: int = Field(ge=60)

class OnceSchedule(BaseModel):
    kind: Literal["once"]
    run_at: datetime

Schedule = Annotated[
    CronSchedule | IntervalSchedule | OnceSchedule,
    Field(discriminator="kind"),
]

class CreateInput(BaseModel):
    name: str
    prompt: str
    schedule: Schedule
    target: Literal["new_each_run", "current_conversation"] = "new_each_run"
    end_at: datetime | None = None
```

Now pydantic rejects `kind="cron"` without `cron_expr` at parse time, and
the JSON Schema makes the constraint visible. The handler flattens
`inp.schedule` back to the column shape (`schedule_kind`, `cron_expr`,
`interval_seconds`, `run_at`, `timezone`) when building the service-layer
dict — DB columns are unchanged.

`UpdateInput.schedule` becomes `Schedule | None = None`. Partial updates
that touch the schedule must replace it whole — there is no
"update cron_expr but keep the kind" shortcut, and the trace doesn't ask
for one.

### 5. Same example treatment for the other multi-operation capabilities

Whatever other `AgentCapability` definitions ship with cubebox today get a
one-line example added to each operation description as we touch them.
Scope of this PR: only `scheduled_tasks`. Other capabilities are a separate
follow-up so this PR stays reviewable.

## What we are not changing

- **Number of tools.** Splitting `scheduled_tasks` into eight separate
  tools (`scheduled_task_create`, `scheduled_task_list`, …) would duplicate
  the tool description and the `target` doc on every tool. The fix is
  better self-description inside one tool, not more tools. Progressive
  disclosure for big capabilities will be designed alongside MCP later.
- **Batch create.** A `create_many` op was on the table. Skipped — the
  model can already emit several `scheduled_tasks` tool calls in one
  assistant turn, and cubepi executes them in parallel.
- **Discriminator key name.** Renaming `operation` to `action` would be a
  one-time win against model priors but breaks every existing capability
  caller and every recorded trace. Examples in the description achieve the
  same outcome.
- **`ScopeContext`-driven `conversation_id`.** Already correct — the model
  never sees or sets it.

## How we verify

- **Unit:** new tests under `backend/tests/unit/` for
  - the nested `Schedule` union (each kind happy path + missing field
    rejection at parse time);
  - the `target` sentinel translating to `target_mode` /
    `target_conversation_id` in both `_handle_create` and `_handle_update`.
- **Integration:** existing `scheduled_tasks` integration tests must
  continue to pass; add one that drives the capability tool with the three
  malformed argument shapes from the trace above (`action: create`,
  `operation: create` flat, missing `cron_expr`) and asserts each response
  is an `is_error=True` `AgentToolResult` whose text names the right
  remediation.
- **E2E:** none. The fix is a tool-schema + error-message change. The
  conversation path doesn't need a new E2E; the existing scheduled-tasks
  E2E exercises the success path.
- **Live check:** rerun a similar prompt against kimi-k2.6 in a dev
  workspace, confirm the first `scheduled_tasks` call succeeds, attach the
  new trace ID to the PR description.

## Out of scope / follow-up

- **Cubepi-side ValidationError translation.** Format pydantic errors
  inside `cubepi/cubepi/agent/tools.py:114-117` (field paths,
  discriminator + allowed values) before returning to the LLM. Separate
  cubepi PR, then bump the cubepi pin in cubebox. See change #2 above.
- Progressive schema disclosure for capability tools (shared design with
  MCP). Tracked separately.
- Same description treatment for other capabilities
  (`browser`, `memory`, etc. — whichever ones use the discriminated-union
  builder). Tracked separately.
- Replacing the runtime-only checks left in `ScheduledTaskService`
  (e.g. timezone validation) with pydantic-layer checks. Out of scope here
  — those don't show up in the failing trace.
