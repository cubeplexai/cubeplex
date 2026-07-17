# Event Triggers — Design Spec (#152)

Status: Draft
Date: 2026-05-27
Branch: feat/event-triggers

## Problem & Motivation

Today an agent run only starts when a human sends a message in the chat UI. Everything
flows through one path: `POST /api/v1/ws/{ws}/conversations/{id}/messages` →
`RunManager.start_run(...)`. There is no way for the platform to *react* to the outside
world. A workspace cannot say "when a GitHub issue is opened, run the triage agent" or
"every morning at 9am, run the report agent" or "when someone @-mentions the bot in
Slack, continue the conversation."

Three sibling features all want the same thing — *something happens, an agent run
starts* — but from different sources:

- **#150 Scheduled tasks** — the "something" is a clock tick (cron / interval / one-shot).
- **#149 IM connectors** — the "something" is an inbound Slack / Feishu message.
- **#152 (this spec)** — the "something" is an inbound webhook, a file change, or an
  MCP/connector event.

If each of those builds its own bespoke "start a run when X happens" plumbing, we get
three subtly different idempotency stories, three retry policies, three sets of
observability, and three places to audit for tenant isolation. That is expensive and
error-prone.

This spec defines **one shared trigger abstraction** that all three sit on top of: an
event arrives, it is matched against subscriptions, and a bound target is started as an
agent run — with consistent dedup, rate limiting, retry, and observability. Schedules
and IM messages become *event sources* that feed the same pipeline; managed agents
(#153) become the *target* a trigger binds to.

## Goals

- A single **Trigger** abstraction: `event source` + `filter condition` + `bound target`
  + `run identity`.
- A single **event → run pipeline** with dedup/idempotency, rate limiting, retry with
  backoff, dead-letter, and per-trigger observability (event log + run history).
- **Inbound webhook ingestion** as the v1 source: a public, unauthenticated-by-network
  endpoint that is authenticated by **signature**, scoped to exactly one workspace, and
  cheap to reject when abused.
- Source abstraction that **schedule (#150)** and **IM (#149)** can plug into without
  contradicting their own designs — they emit normalized events into the same pipeline.
- Target abstraction that maps cleanly onto the existing `RunManager.start_run` path and
  onto **managed agents (#153)** once those exist.
- **Scope-isolated** workspace routes for managing triggers; structural `(org_id,
  workspace_id)` enforcement via the existing repository mixins.

## Non-goals (v1)

- No visual workflow builder / multi-step DAG (we are not building n8n). A trigger fires
  exactly one bound target; chaining is out of scope.
- No IM source implementation here — #149 owns the Slack/Feishu adapters; this spec only
  defines the seam they emit into.
- No schedule executor here — #150 owns the timer substrate; this spec defines how a
  fired schedule becomes an event.
- No outbound webhooks / event *emission* from cubeplex. Inbound only.
- No managed-agent definition object — #153 owns that; we only define how a target
  *reference* resolves to a run.
- No general-purpose filter scripting language. v1 filters are declarative field
  matchers, not arbitrary code.
- **No provider connectors** (GitHub App, Stripe app, Linear, Slack-as-connector,
  etc.). See "Layered model: ingest vs connectors" below — v1 is layer 1 only.

## Layered model: ingest vs connectors

This spec deliberately ships **only the ingest layer**. There are two layers of
work; both belong to event triggers as a product, but they ship separately.

- **Layer 1 — generic webhook ingest (this spec, v1).** A workspace creates a
  trigger, cubeplex returns a per-trigger ingest URL plus an HMAC secret, and the
  user pastes both into the source provider's webhook settings by hand. cubeplex
  treats every inbound request as an opaque JSON event: verify signature,
  dedup, filter, dispatch to a run, audit. The pipeline doesn't know "this came
  from GitHub" — it only knows "this came over the generic webhook source for
  trigger `trig-...`."
- **Layer 2 — connectors (deferred, v1.x).** A connector wraps a specific
  provider so the user gets a one-click "Connect GitHub" / "Connect Stripe"
  flow: cubeplex installs as e.g. a GitHub App per repo (or per org), subscribes
  to the right events automatically, and routes inbound payloads to the right
  trigger without the user copying URLs or secrets. Each connector is its own
  spec and its own PR; the anticipated first one is a GitHub App. Connectors
  reuse the layer-1 pipeline internally — same dedup, same filter, same
  dispatch — they only replace the "paste a webhook URL by hand" UX with a
  provider-aware OAuth/install flow and provider-aware event routing.

#149 (IM connectors — Slack/Feishu) and a future GitHub App connector are
**both layer-2** efforts but for **different sources**. They do not depend on
each other; they each have their own connection model and identity story.
Conceptually they sit on the same seam, but neither is in scope here.

Until the layer-2 connectors ship, the v1 UX for "issues/PRs/comments trigger
an agent" is:

1. Create a generic-webhook trigger in cubeplex; copy the ingest URL + HMAC
   secret from the trigger detail page.
2. Open the source repo's GitHub settings → webhooks → add webhook; paste the
   URL, paste the secret, pick the events to deliver.
3. Pick `application/json` content type so the body cubeplex sees matches what
   was signed.

This is acceptable for early users and removes the need to ship any
provider-specific code in v1. The connector layer is the planned UX
improvement, scheduled after #152 v1 lands.

## Current State

### How a run starts (the only path today)

`backend/cubeplex/api/routes/v1/conversations.py` → `send_message` (line ~509):

1. Loads the conversation, validates content/attachments, marks it active.
2. Builds `RunContext(user_id, org_id, workspace_id)`
   (`backend/cubeplex/streams/run_manager.py`, `RunContext` at line ~30).
3. Calls `run_manager.start_run(conversation_id=..., content=..., attachments=...,
   ctx=...)` (`run_manager.py` line ~482), which claims an active-run key in Redis,
   spawns a background `asyncio.Task` running `_execute_run`, and returns a `run_id`.
4. If the client sent `Accept: text/event-stream`, replays + tails the SSE stream;
   otherwise returns `{run_id}`.

Key facts the trigger pipeline must respect:

- A run **requires a `conversation_id`** and a **`RunContext` carrying a real
  `user_id`** plus the org/workspace. Triggers must therefore decide which conversation
  to use (existing vs new) and which identity to run as.
- `start_run` enforces **one active run per conversation** — claiming the Redis key
  fails with `RuntimeError` if a run is already active. Triggers firing into a busy
  conversation must handle this (queue, skip, or new conversation).
- `run_manager` is `raw_request.app.state.run_manager` — a process-local singleton, so a
  trigger that fires in one process starts the run in that process.

### Existing inbound endpoints (precedent)

- **MCP OAuth callback** — `backend/cubeplex/api/routes/v1/mcp_oauth.py`. A public
  `GET /api/v1/oauth/mcp/callback` that takes an opaque `state` query param, **decodes an
  HMAC-signed state token** to recover identity/tenant, then acts. This is the closest
  precedent for "unauthenticated network path, authenticated by a signed token, mapped
  back to a tenant." The webhook design reuses this shape.
- **System setup** — `backend/cubeplex/api/routes/v1/system.py` `POST /api/v1/system/setup`.
- HMAC signing infra already exists in the app (`mcp_user_token_signer` built in
  `backend/cubeplex/api/app.py` `_build_mcp_user_token_signer`); we follow the same
  "signer object on `app.state`" pattern for webhook secrets.

### Scope & data conventions

- All business tables use `CubeplexBase` + `OrgScopedMixin`
  (`backend/cubeplex/models/mixins.py`): public-id PK via `generate_public_id(_PREFIX)`,
  `(org_id, workspace_id)` FKs, composite index `ix_<table>_org_ws`.
- Public-id prefixes are declared per-model as `_PREFIX` (e.g. `conv`, `cred`, `agt`).
- Workspace routes are mounted under `/api/v1/ws/{workspace_id}/...` and read identity
  from `RequestContext` via `Depends(require_member)`.

## Research — Trigger / automation patterns

How established platforms model triggers, and what we borrow:

**n8n / Zapier (node/trigger model).** A workflow has *trigger nodes* (the thing that
starts it — webhook, schedule, app event) and *filter nodes* (declarative AND/OR field
conditions that pass or drop an item). Only one trigger fires per execution. We borrow
the clean split between **source** (what produces the event) and **filter** (a
declarative condition over the event payload), and the principle that a trigger maps to
exactly one downstream action in v1.
([n8n trigger nodes](https://docs.n8n.io/integrations/builtin/trigger-nodes/),
[n8n filter node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.filter/),
[n8n node types](https://docs.n8n.io/integrations/creating-nodes/plan/node-types/))

**GitHub Actions (events trigger workflows).** Events (push, issue, schedule, custom)
are *signals*; the same workflow engine runs regardless of which event fired. This is
the model where "schedule" is just another event type alongside repo events — exactly
the unification we want for #150/#149/#152.
([GitHub: events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows))

**Webhook ingestion & signature verification.** Industry consensus for inbound
webhooks: verify an **HMAC-SHA256 signature over the raw request body** (not parsed
JSON — body parsers reformat and break the check); use a **constant-time compare**
(`hmac.compare_digest`); include and **validate a timestamp within a max-age window
(~5 min)** to blunt replay; and treat a valid signature as authentication, *not* as
proof the event is safe to process twice — dedup separately.
([Hookdeck: SHA256 signature verification](https://hookdeck.com/webhooks/guides/how-to-implement-sha256-webhook-signature-verification),
[Hooque: webhook security best practices](https://hooque.io/guides/webhook-security/),
[Hooklistener: webhooks fundamentals 2026](https://www.hooklistener.com/learn/webhooks-fundamentals))

**Idempotency & dedup.** Make side effects idempotent via a stored **event ID /
idempotency key**, separate from the signature check. On arrival, look up the event's
unique ID; if already seen, ack and drop. Retries are *expected*, so the receiver — not
the sender — owns "exactly once" semantics.
([Hookdeck guide](https://hookdeck.com/webhooks/guides/how-to-implement-sha256-webhook-signature-verification),
[Hooque guide](https://hooque.io/guides/webhook-security/))

**Retry / backoff / dead-letter.** The widely-used pattern is **exponential backoff**
(e.g. Temporal's default: 1s initial, 2.0 coefficient, capped ~100s) with a bounded
retry count, after which the work lands in a **dead-letter queue** for manual
inspection. Temporal notably avoids DLQs by tracking full execution state — a useful
contrast: because our run substrate is durable-ish (Redis active-run + Postgres
history), the *delivery* layer still needs a DLQ even though the *run* layer does not.
([Temporal retry policies](https://docs.temporal.io/encyclopedia/retry-policies),
[Queue-based exponential backoff](https://dev.to/andreparis/queue-based-exponential-backoff-a-resilient-retry-pattern-for-distributed-systems-37f3),
[Temporal: reliable data processing](https://temporal.io/blog/reliable-data-processing-queues-workflows))

**CloudEvents.** The emerging standard for a normalized event envelope (id, source,
type, time, subject, data). We adopt its *shape* for our internal `NormalizedEvent` so
all sources (webhook, schedule, IM) speak one vocabulary downstream.
([Hooklistener 2026 trends](https://www.hooklistener.com/learn/webhooks-fundamentals))

## Proposed Design

### The trigger abstraction

A **Trigger** is a workspace-owned row binding four things:

```
Trigger
├── source        : what produces events   (webhook | schedule | im | mcp_event)
├── filter        : declarative condition over the normalized event payload
├── target        : what to run             (managed_agent ref | inline prompt)
└── run_identity  : whose RunContext the run executes under
```

Conceptually: *when an event from `source` matches `filter`, start `target` as a run
under `run_identity`.* Each source contributes source-specific config (webhook secret,
cron expression, IM channel mapping) stored in a JSON `source_config` column, but the
*pipeline* downstream of "an event arrived" is identical for all sources.

#### Normalized event envelope

Every source adapter converts its raw input into one shape (CloudEvents-flavored):

```
NormalizedEvent
├── event_id       : stable unique id from the source (used for dedup)
├── source_type    : webhook | schedule | im | mcp_event
├── trigger_id     : which Trigger this event was routed to
├── event_type     : source-specific type string (e.g. "github.issues.opened")
├── occurred_at    : source timestamp (utc_isoformat)
├── subject        : optional resource id (issue #, channel id, file path)
├── payload        : the source-specific body (validated JSON)
└── dedup_key      : derived idempotency key (default: event_id; source may override)
```

#### Filter

v1 filter is a **declarative matcher**, not code: a small AND/OR tree of
`{ path, op, value }` clauses evaluated against `payload`. `path` is a JSONPath
expression (dot-walked into the payload — e.g. `event.action`, `repository.name`);
`op ∈ {eq, neq, contains, exists, in}`. This covers "only when
`event.action == opened`" / "only `repository.name in [...]`" without opening a
code-execution surface. A trigger with no filter matches every event from its
source. Richer predicate languages (regex, JMESPath, CEL, etc.) are deferred until
real users push the simple matcher to its limit.

#### Bound target → run

The target is a **reference**, resolved to a run at fire time:

- **`inline`** (v1): the trigger stores a `prompt_template`. At fire time, the
  template is rendered against the event payload (safe substitution of fields listed
  in the trigger's `payload_fields` whitelist, wrapped in an `<external_input>`
  delimiter block) to produce the run's `content`. Model selection is resolved by
  `start_run` from the org default in v1; per-trigger model override is a fast-follow
  once `start_run` grows that seam.
- **`managed_agent`** (when #153 lands): the trigger stores a managed-agent id +
  version. Resolution loads that agent's config and instantiates it into the run. The
  target column is designed so this is an additive change — a `target_type` discriminator
  plus a `target_ref` — not a schema rewrite.

Both forms ultimately produce the inputs `RunManager.start_run` already takes:
`conversation_id`, `content`, `ctx`. The trigger's **conversation policy** decides the
conversation:

- `new_each_time` (the **only** mode in v1): create a fresh `Conversation`
  (draft-then-active) per fired event, so concurrent events never collide on the
  one-active-run-per-conversation rule. Yes, this can produce many short-lived
  conversations under a noisy source; we accept that for v1 and rely on rate
  limiting plus operator GC to keep it bounded.
- `pinned` (**deferred**): always target a configured conversation id, intended for
  IM threads (#149) where a thread maps to a stable conversation. The enum value
  is reserved in the schema but v1 ingest never writes it; the `one_active_run`
  / queue-and-merge busy behaviour required to make `pinned` safe lands with
  whichever feature first needs it.

#### Run identity

A trigger has no live HTTP session, so it cannot borrow a request's `RequestContext`.
Each trigger stores an explicit **`run_as_user_id`** (a workspace member, chosen at
trigger-creation time and re-validated on fire). The fired run's `RunContext` is built
as `RunContext(user_id=run_as_user_id, org_id=trigger.org_id,
workspace_id=trigger.workspace_id)`. This keeps RBAC, cost attribution, and sandbox
ownership coherent — the run looks exactly like that user started it. If the chosen user
loses membership, the trigger is auto-disabled and logged.

### Data model

New tables, all `CubeplexBase + OrgScopedMixin` (public-id PK, org/workspace FKs,
`ix_<t>_org_ws`). New prefixes added to model `_PREFIX` declarations:

**`triggers`** — `_PREFIX = "trig"`
- `name`, `enabled` (bool), `source_type` (enum str), `source_config` (JSON: per-source —
  webhook secret ref, cron, IM mapping), `filter` (JSON matcher tree), `target_type`
  (`inline` | `managed_agent`), `target_ref` (JSON: prompt_template — model selection is
  resolved by `start_run` from the org default in v1; or managed-agent id+version),
  `payload_fields` (JSON list of JSONPath expressions whitelisted for prompt-template
  injection — see Security below), `conversation_policy` (always `new_each_time` in v1;
  the `pinned` value is reserved in the enum for #149 IM but not written by v1 ingest),
  `run_as_user_id` (FK members), rate-limit fields (`max_runs_per_minute`,
  `rate_limit_burst`), `rate_limit_response` (`429` | `202_drop`, default `429`).
- **Secret rotation columns**: `current_secret_cred_id` (the live secret), plus
  `previous_secret_cred_id` and `previous_secret_expires_at`. During the overlap
  window (default 24h, configurable per-trigger) both secrets verify; after the
  expiry only `current` does. See Security below.
- **Summary counters**: `events_total`, `events_success`, `events_failed`,
  `events_dedup_dropped` (BIGINT, default 0). The dispatch path increments these
  cheaply so the trigger detail UI doesn't have to scan `trigger_events`.
- Webhook secrets (both current and previous) are **not** stored inline; they live in the
  credential vault and `source_config` references them by id (reuses the existing
  credential model and its system/org scope pattern).

**`trigger_events`** — `_PREFIX = "trev"` — the inbound event log (observability + dedup).
- `trigger_id` (FK), `source_type`, `event_type`, `dedup_key`, `occurred_at`,
  `received_at`, `status` (`accepted` | `duplicate` | `filtered_out` | `rate_limited` |
  `failed` | `dead_lettered`), `attempts`, `last_error`, `payload` (JSON, possibly
  truncated), `resulting_run_id` (nullable), `resulting_conversation_id` (nullable).
- **Unique constraint `(trigger_id, dedup_key)`** — the database-level idempotency
  guarantee. A duplicate insert is caught and the event is acked as `duplicate`.

Retention is **permanent in v1** — no TTL job, no rollup. Operators should monitor
table growth; if a real high-volume source materializes, an archival/rollup PR can be
added later (recorded as an ops follow-up, not implementation work in this round).
The four summary counters on `triggers` give cheap aggregates without scanning the
event log.

### Inbound webhook ingestion

A single public route per workspace, addressed by trigger id (which embeds no secret):

```
POST /api/v1/ws/{workspace_id}/triggers/{trigger_id}/ingest
```

Pipeline at ingest (cheap-reject ordering — reject before doing expensive work):

1. **Read raw body bytes** (before any JSON parsing) — needed for signature.
2. **Look up the trigger** by `(workspace_id, trigger_id)`. Every pre-auth lookup failure —
   trigger missing OR `enabled == false` — returns the **same status and body** (a flat
   `404 {"error": "not_found"}`). Because this happens before the caller proves knowledge of
   the HMAC secret, a different code for "disabled" vs "missing" would leak whether a trigger
   id is real, turning the unauthenticated path into a trigger-existence oracle. Disabled
   triggers are only distinguishable *after* a valid signature, and even then we just stop
   silently — we never confirm existence to an unsigned caller.
3. **Signature verification.** Compute `HMAC-SHA256(secret, timestamp + "." + raw_body)`
   and `hmac.compare_digest` against the header signature. Reject on mismatch.
   **Dual-secret accept during rotation.** Resolve `current_secret_cred_id` from the
   vault and try it first; if mismatch, and `previous_secret_cred_id` is set with
   `previous_secret_expires_at > now`, try the previous secret. Accept on either match;
   outside the overlap window only `current` matches. Default overlap window is **24h**
   from rotation, configurable per trigger.
4. **Timestamp window.** Reject if the signed timestamp is outside ±5 min (replay
   guard).
5. **Tenant isolation by construction.** The secret is per-trigger and per-workspace; a
   valid signature *is* the proof this caller is allowed to fire *this* trigger in *this*
   workspace. There is no cross-workspace ambiguity because the route path and the secret
   both pin the workspace.
6. **Dedup.** Derive `dedup_key` from a *stable* identity of the event: the provider
   event-id header when present, else a SHA-256 content hash of the raw body bytes alone.
   The fallback **must not** include the signed freshness timestamp — that timestamp changes
   on every re-sign, so a provider replaying the identical event with a fresh signature would
   otherwise produce a new `dedup_key` and spawn a duplicate run. Hashing the body keeps the
   key constant across retries of the same payload. Attempt `trigger_events` insert; on
   unique-constraint hit, return `200` with `duplicate` (idempotent ack).
7. **Rate limit.** Token-bucket per trigger in Redis (reuse the `RedisHandle` infra). On
   exhaustion, log `rate_limited` and return the trigger's configured
   `rate_limit_response`: **`429`** by default (let a well-behaved sender back off), or
   **`202_drop`** (return `202` and silently drop) for sources that retry hard on any
   non-2xx and would otherwise pile retries on top of an already-overloaded trigger.
   `429` is the right default for hand-pasted webhook setups; `202_drop` exists as an
   opt-out per trigger.
8. **Filter.** Evaluate the declarative matcher; non-match → log `filtered_out`, return
   `200`.
9. **Enqueue for run.** Hand the `NormalizedEvent` to the event→run pipeline (below) and
   return `202 Accepted` immediately. Webhook senders expect a fast ack; the run happens
   asynchronously.

Verification follows the MCP-OAuth precedent of a signed token mapping back to a tenant,
but here the signature is over the *body* and the tenant is pinned by the path + secret.

### The event → run pipeline

Shared by all sources. Once a `NormalizedEvent` is accepted (deduped, within rate limit,
passes filter):

1. **Resolve identity & conversation.** Build `RunContext` from `run_as_user_id` +
   org/workspace. Pick the conversation per `conversation_policy` (create draft, or use
   pinned + apply `busy_policy`).
2. **Resolve target → content.** Render the inline prompt template against the payload,
   or (future) load the managed-agent config.
3. **Start the run.** Call `run_manager.start_run(conversation_id, content, attachments=
   [], ctx=ctx)`. Capture `run_id`; write it back to the `trigger_events` row
   (`resulting_run_id`, `resulting_conversation_id`).
4. **Handle start failure / busy.** If `start_run` raises (conversation already running),
   apply `busy_policy`: `skip` → log and stop; `queue` → push to a Redis per-conversation
   queue drained when the active run ends.

**Dedup/idempotency** — owned by the `(trigger_id, dedup_key)` unique constraint, decided
*before* the run starts. Because run-start is the side effect, a duplicate event never
spawns a second run.

**Rate limiting** — per-trigger token bucket (steady-state runs/min + burst), enforced
at ingest (fast reject). An org-level ceiling is deferred until we have a real workload
to size it against; until then, run-capacity / cost containment is the job of the
planned cross-cutting CostMiddleware, not the trigger layer.

**Retry / backoff / dead-letter** — applies to the *enqueue→start_run* step, not the LLM
run itself (a failed agent run is the run subsystem's concern, surfaced as run status).
If starting the run fails for transient reasons (Redis hiccup, no capacity), retry with
exponential backoff (1s, 2x, capped) up to N attempts; on exhaustion mark the
`trigger_events` row `dead_lettered`. Dead-lettered events are visible in the trigger's
event log and can be manually replayed from the UI/API.

**Observability** — every inbound event produces exactly one `trigger_events` row with a
terminal `status`, linked to its `resulting_run_id`. This is the audit trail: "what fired
this run, when, from what payload, and did it dedup/filter/rate-limit." Trace spans
(per the existing tracing in #141) are stamped with `trigger_id` so a run is traceable
back to its source.

### How schedule (#150) and IM (#149) fit as sources

The pipeline is source-agnostic. Each sibling owns its *adapter* that produces a
`NormalizedEvent` and calls the same enqueue entrypoint:

- **#150 Schedule.** The schedule executor (its own timer substrate) fires on its
  cadence and emits a `NormalizedEvent(source_type="schedule", event_type="schedule.tick",
  event_id="<schedule_id>:<fire_time>")`. The `event_id` makes missed-run/double-fire
  dedup automatic via the same `(trigger_id, dedup_key)` constraint. A scheduled task is
  simply a `Trigger` with `source_type="schedule"` and cron in `source_config`. #150's
  spec keeps its scheduling policy; it just lands runs through this pipeline instead of
  its own start-run plumbing.

- **#149 IM.** The Slack/Feishu adapter (its own connection model — Events API / Socket
  Mode / long-connection) receives a message, maps the IM thread → a `pinned`
  conversation, and emits `NormalizedEvent(source_type="im",
  event_type="im.message", subject="<channel/thread>", event_id="<im_msg_ts>")`. IM uses
  `conversation_policy="pinned"` + `busy_policy="queue"` so multi-turn threads preserve
  context. Credential storage and IM↔workspace mapping stay #149's concern; the run-start
  seam is this pipeline.

Neither sibling needs the webhook route. They share the **`NormalizedEvent` shape**, the
**dedup/rate-limit/retry/observability** machinery, and the **identity→conversation→run**
resolution. The `source_type` enum is the extension point — adding `mcp_event` later is
additive.

### How a bound target maps to a run (and to #153)

v1 ships `target_type="inline"` (prompt template + model). The schema reserves
`target_type="managed_agent"` with a `target_ref` carrying the managed-agent id +
version. When #153 lands, target resolution gains one branch: load the managed agent,
instantiate its config (system prompt, tools/MCP, skills, model, sandbox/permission
scope) into the run. No table change — the discriminator and ref column already exist.
This keeps #153 as the *definition* of an agent and triggers as one of several *callers*
of it (alongside conversations, schedules, IM).

### Scope-isolated workspace routes

Management routes are workspace-scoped and member-guarded (`Depends(require_member)`),
mounted like the existing conversation routes:

```
GET    /api/v1/ws/{ws}/triggers                  list triggers
POST   /api/v1/ws/{ws}/triggers                  create
GET    /api/v1/ws/{ws}/triggers/{id}             detail
PATCH  /api/v1/ws/{ws}/triggers/{id}             update / enable / disable
DELETE /api/v1/ws/{ws}/triggers/{id}             delete
GET    /api/v1/ws/{ws}/triggers/{id}/events      event log (observability)
POST   /api/v1/ws/{ws}/triggers/{id}/events/{eid}/replay   re-run a dead-lettered event
POST   /api/v1/ws/{ws}/triggers/{id}/ingest      public webhook ingest (signature-auth)
```

The `ingest` route is the only one not behind `require_member` — it is authenticated by
HMAC signature instead. All management routes enforce `(org_id, workspace_id)`
structurally via the repository mixins. Per the scope-isolation rule, if an org-admin
view of triggers is ever needed, it gets its own `/api/v1/admin/...` handlers — never a
`?scope=` param on these.

### Workspace UI (scope-isolated page)

Workspace members manage their triggers from a dedicated page at
`/w/{workspaceId}/triggers`. Per the scope-isolated-pages rule, this is its own Next
route with its own page file; modules (`<List>`, `<DetailPanel>`, `<Form>`, …) are the
reuse boundary, not a `mode?: 'admin' | 'workspace'` prop. If an org-admin cross-workspace
view of triggers is ever introduced it gets a separate `/admin/triggers` page with its
own page file, never a flag on this one.

A "Triggers" entry sits under the workspace navigation alongside the other workspace
sections (conversations, MCP, members, …) so the feature is discoverable.

**List view.** A table of all triggers in the workspace with: name, source kind
(generic webhook in v1), enabled/disabled toggle, last-event timestamp, and the four
summary counters maintained on the trigger row (`events_total`, `events_success`,
`events_failed`, `events_dedup_dropped`). Each row links to the detail view. A
"Create trigger" action opens the create form.

**Create / edit form.** Fields: name, prompt template, enable toggle, and the
rate-limit-exceeded response choice (return `429` to tell the sender to back off, or
return `202` and silently drop). The prompt-template editor has a side-panel listing
the trigger's `payload_fields` whitelist as autocomplete tokens — users insert them
into the template rather than free-typing JSON paths, which keeps templates within the
whitelist enforced by the backend.

Create-only: the form collects the **HMAC secret**. On submit, the response shows the
secret **once** with a copy button and a "save it now, we won't show it again"
warning. After save, the page shows the public **ingest URL** (the
`POST /api/v1/ws/{ws}/triggers/{id}/ingest` endpoint) with a copy button — this is
what the user pastes into the source provider's webhook settings.

**Detail view.** Shows the trigger config (name, filter summary, prompt template
preview, payload whitelist, run identity, busy policy, rate-limit settings) and the
four summary counters from the list view. Below the config is an **audit feed** of
recent `trigger_events` rows: timestamp, sender headers (User-Agent, signature header
name), signature-verified Y/N, filter pass / fail, status (`accepted` /
`signature_failed` / `duplicate` / `filtered_out` / `rate_limited` / `dead_lettered`),
and — when present — a link to the resulting run.

The detail view also hosts the mutating controls: **pause** / **resume** (toggle
`enabled`), **delete**, and **rotate secret**. Rotate prompts for a new secret, then
shows it once with a copy button just like create; the previous secret remains valid
for the 24h overlap window (configurable via the spec's overlap default) so the user
has time to swap the secret on the source provider side. All mutating controls are
**owner-or-admin gated**; viewers see them disabled with a tooltip explaining why.

**What v1 does NOT include in the UI.** Per-provider presets (GitHub / Stripe header
shapes), schedule / IM source kinds (own specs), `managed_agent` target picker (#153),
and the cross-workspace admin view. The list view shows only `source_kind=webhook`
because that's the only kind the backend writes in v1.

### Security / abuse prevention

- **Signature over raw body + constant-time compare + timestamp window** — the core
  webhook auth, as in research.
- **No secret in the URL.** The trigger id is a public id; the secret is vault-stored.
  Knowing the URL is not enough to fire a trigger.
- **Constant-shape rejections** so the endpoint isn't an oracle for which trigger ids /
  workspaces exist.
- **Rate limit at the edge** caps the blast radius of a leaked/compromised secret
  (bounded runs) until rotated. v1 enforces only a per-trigger bucket; an org-level
  ceiling is a fast-follow once we have multi-trigger workloads to size it against.
- **Secret rotation with overlap.** Each trigger has a `current_secret_cred_id` and an
  optional `previous_secret_cred_id` + `previous_secret_expires_at`. During the overlap
  window (default 24h, per-trigger configurable) ingest accepts either secret; outside
  the window only `current`. Rotation is therefore a two-step UX: rotate sets the old
  secret as `previous` with an expiry, then the user updates the provider; when the
  expiry passes only the new secret works.
- **Payload size cap** — reject oversized bodies before HMAC to avoid CPU/memory abuse.
- **Prompt-injection containment.** The webhook body is untrusted external input.
  The trigger's `prompt_template` may only reference fields listed in the trigger's
  `payload_fields` whitelist (JSONPath expressions). The renderer ignores any
  placeholder that isn't on the whitelist — the literal token is left in the
  template, never interpolated. Every injected value is wrapped at render time in
  a clearly-labeled "untrusted external input" delimiter block (an explicit
  `<external_input source="...">…</external_input>` framing in the rendered
  prompt) so the model can see where the boundary is. The full request body is
  **never** dumped verbatim into the prompt. This is a #152-only rule in v1;
  generalizing the "external-input framing" abstraction across #149 (IM messages)
  and #153 (managed-agent inputs) is **deferred as a follow-up** and tracked
  separately.
- **Disabled triggers reject fast** — no run, logged as such.
- **Run identity is a real member** — a trigger can't escalate beyond what its
  `run_as_user_id` may do; loss of membership disables the trigger.
- **Cost ceiling is project-wide, not per-trigger.** Enforcing a per-run / per-org
  cost cap is the responsibility of the planned `CostMiddleware` (see #150 OQ-8 for
  the matching decision). The trigger layer does **not** enforce cost caps; it relies
  on rate-limit + run-capacity to bound damage until cost middleware ships.

### v1 Scope (recommendation)

Ship the **abstraction + webhook source + inline target**:

1. `triggers` + `trigger_events` tables, prefixes, repositories (scoped). `triggers`
   carries `payload_fields`, `rate_limit_response`, the dual-secret rotation columns,
   and the four summary counters.
2. Webhook ingest route: signature verify (with dual-secret accept during the rotation
   window), timestamp window, dedup, rate limit, filter.
3. Event→run pipeline: identity/conversation resolution (`new_each_time` only),
   `start_run` call, retry/DLQ, `trigger_events` audit rows, summary-counter bumps.
4. Workspace CRUD + event-log + replay + rotate-secret routes.
5. Declarative filter matcher: AND/OR tree of `{path, op, value}` leaves with
   JSONPath-style field references and ops `eq | neq | contains | exists | in`.
6. Inline prompt-template target with whitelisted `payload_fields` + `<external_input>`
   wrapping.
7. `source_type` enum + `NormalizedEvent` seam ready for schedule/IM adapters.
8. `target_type="managed_agent"` reserved in schema, not implemented.
9. **Connectors (layer 2) are out of scope** — see "Layered model" above.

Webhook first because it is self-contained (no external connection model like IM, no
timer substrate like schedule), exercises the full pipeline (signature, dedup, rate
limit, retry, observability), and is the highest-leverage integration surface.

## Testing Strategy (E2E-first)

Per repo discipline, E2E over mocks. The webhook source is fully simulatable locally —
no third-party SaaS needed — so it gets real E2E.

- **E2E happy path**: create a trigger via the workspace API → POST a correctly-signed
  payload to `/ingest` → assert a run starts (poll run status / SSE), a conversation is
  created, and a `trigger_events` row is `accepted` with a `resulting_run_id`.
- **E2E signature failure**: bad signature → rejected, no run, no event row (or a
  `failed` row), correct status code.
- **E2E replay/dedup**: send the same signed payload twice → exactly one run; second is
  `duplicate`.
- **E2E timestamp window**: stale timestamp → rejected.
- **E2E filter**: payload that fails the filter → `filtered_out`, no run.
- **E2E rate limit**: burst beyond the bucket → excess `rate_limited`, runs capped.
- **E2E busy/pinned**: pinned conversation already running + `busy_policy=queue` →
  second event queued, runs after the first ends.
- **E2E tenant isolation**: a trigger's secret cannot fire a trigger in another
  workspace; cross-workspace listing is impossible (scoped repo).
- **E2E dead-letter + replay**: force start failure → `dead_lettered`; replay endpoint
  re-runs it.
- **Unit** (only where E2E is awkward): the filter matcher evaluator and the
  dedup-key/HMAC derivation as pure functions.

The schedule/IM source adapters are tested by their own specs (#150/#149); this spec's
E2E covers the shared pipeline by way of the webhook source.

## Open Questions

All ten OQs were resolved in the 2026-05-28 design session. Decisions are recorded
below and folded into the spec body above; each OQ's resolution is also reflected as
a concrete task in `docs/dev/plans/2026-05-27-event-triggers.md`.

1. **`trigger_events` retention.**
   **Resolved 2026-05-28: permanent in v1 — no TTL, no rollup.** Operators should
   monitor table growth; a real high-volume source can drive an archival/rollup PR
   later. To keep observability cheap without scanning the event log, the trigger
   row carries four summary counters: `events_total`, `events_success`,
   `events_failed`, `events_dedup_dropped` (BIGINT, default 0), bumped on every
   dispatch outcome.
2. **Cross-process run start.**
   **Resolved 2026-05-28: v1 starts the run in the receiving process.** Same pattern
   as #150 / #149 — process-local `RunManager.start_run`. A shared queue any worker
   drains is deferred until horizontal scale (more than one backend pod fronting one
   tenant) becomes a real need.
3. **Filter expressiveness.**
   **Resolved 2026-05-28: declarative AND/OR matcher with JSONPath field references.**
   Operators are `eq | neq | contains | exists | in`. No code-exec surface. Richer
   predicate languages (regex, JMESPath, CEL) only on real demand.
4. **Payload → prompt templating safety.**
   **Resolved 2026-05-28: per-trigger `payload_fields` whitelist** (JSONPath
   expressions). Only whitelisted fields may be referenced from `prompt_template`;
   placeholders for non-whitelisted paths render as the literal token, never
   interpolated. Every injected value is wrapped at render time in an
   `<external_input source="...">…</external_input>` delimiter block so the model
   can see where untrusted external data ends. The full request body is never
   dumped verbatim. This is a #152-only rule in v1; **generalizing the
   "external-input framing" across #149 IM and #153 managed agents is flagged as a
   follow-up** in those specs, not done here.
5. **Rate-limit response semantics.**
   **Resolved 2026-05-28: default `429`, per-trigger configurable.** `triggers`
   carries a `rate_limit_response` column (`429` | `202_drop`). Default `429`
   (well-behaved senders back off); `202_drop` is the opt-out for sources that
   retry hard on any non-2xx.
6. **Secret rotation overlap window.**
   **Resolved 2026-05-28: per-trigger `previous_secret_cred_id` +
   `previous_secret_expires_at`, default overlap 24h.** Rotation sets the old
   secret as `previous` with an expiry; during the window both secrets verify;
   after the expiry only `current` does. Default is configurable per trigger when
   users need a shorter / longer cutover.
7. **Generic webhook vs provider presets.**
   **Resolved 2026-05-28: v1 ships only generic HMAC.** Per-provider presets
   (GitHub `X-Hub-Signature-256`, Stripe `Stripe-Signature`, Linear, etc.) are a
   **fast-follow PR** — they convert into the same generic ingest internally and
   slot in as a thin "preset → header config" layer on top of the existing route.
8. **One-active-run vs `new_each_time`.**
   **Resolved 2026-05-28: v1 only `new_each_time`.** A fresh conversation per
   event. We accept the proliferation of short-lived conversations under noisy
   sources; rate limiting + operator GC keep it bounded. `pinned` + queue-and-merge
   is deferred until IM (#149) or another feature concretely needs it.
9. **Org-level run capacity / cost ceiling.**
   **Resolved 2026-05-28: deferred to project-wide CostMiddleware** (same call as
   #150 OQ-8). The trigger layer does not enforce cost caps; it relies on per-trigger
   rate limit + run capacity until CostMiddleware lands as the authoritative budget.
10. **Body-hash dedup false-merge.**
    **Resolved 2026-05-28: v1 accepts the false-merge risk.** Two distinct logical
    events with byte-identical bodies and no provider event-id will collide on
    `dedup_key` and the second will be acked as duplicate. This is rare in
    practice. A fast-follow can add a per-trigger `allow_identical_bodies` opt-out
    that mixes a coarse time bucket into the hash for sources that legitimately
    repeat payloads.

## References

- [n8n trigger nodes](https://docs.n8n.io/integrations/builtin/trigger-nodes/)
- [n8n filter node](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.filter/)
- [n8n node types: trigger vs action](https://docs.n8n.io/integrations/creating-nodes/plan/node-types/)
- [GitHub: events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [Hookdeck: implement SHA256 webhook signature verification](https://hookdeck.com/webhooks/guides/how-to-implement-sha256-webhook-signature-verification)
- [Hooque: webhook security best practices](https://hooque.io/guides/webhook-security/)
- [Hooklistener: webhooks fundamentals 2026](https://www.hooklistener.com/learn/webhooks-fundamentals)
- [Temporal: retry policies](https://docs.temporal.io/encyclopedia/retry-policies)
- [Temporal: reliable data processing — queues and workflows](https://temporal.io/blog/reliable-data-processing-queues-workflows)
- [Queue-based exponential backoff retry pattern](https://dev.to/andreparis/queue-based-exponential-backoff-a-resilient-retry-pattern-for-distributed-systems-37f3)
- Internal: `backend/cubeplex/api/routes/v1/conversations.py` (`send_message`),
  `backend/cubeplex/streams/run_manager.py` (`RunManager.start_run`, `RunContext`),
  `backend/cubeplex/api/routes/v1/mcp_oauth.py` (signed-token tenant mapping),
  `backend/cubeplex/models/mixins.py` (`CubeplexBase`, `OrgScopedMixin`).
- Sibling issues: #150 (scheduled tasks), #149 (IM connectors), #153 (managed agents).
