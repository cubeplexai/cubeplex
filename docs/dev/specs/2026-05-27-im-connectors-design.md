# IM Connectors (Slack / Feishu) — Design

Status: draft · Issue: #149 · Synergizes with: triggers (#152)

## Problem & motivation

Today a workspace's agent is only reachable through the cubebox web UI. People live in
Slack and Feishu. They want to @-mention an agent in a channel or DM it, get a threaded
reply, and watch it work — without opening cubebox. This is the same pattern OpenClaw
ships: an IM message becomes an agent run, and the run's output streams back into the
originating IM thread.

We want this to be bidirectional and faithful to how the web UI already works:

- Inbound: an IM message (mention, DM, or thread reply) starts an agent run in the
  right workspace, against the right conversation, attributed to the right user.
- Outbound: the run's streamed text and tool activity flow back as a threaded IM reply
  that updates live as the agent works.

This must respect cubebox's multi-tenant isolation: an IM account maps to exactly one
org/workspace, never leaks across tenants, and bot credentials live in the credential
vault like every other secret.

## Goals

- Let a workspace bind one or more IM bot accounts (Slack workspace, Feishu app).
- Inbound IM message → agent run on a cubebox conversation, scoped to that workspace.
- Outbound run events → a live-updating threaded reply in the source IM thread.
- Map each IM thread to one cubebox conversation, deterministically and durably.
- Map each IM sender to a cubebox user (or a per-account service identity).
- Store bot/signing credentials in the existing credential vault; never inline.
- Scope-isolated config: separate workspace-scope and org-admin routes/pages.
- Multi-tenant isolation enforced structurally, not by ACL bolted on.
- Reuse the existing run/event/Redis machinery; do not fork the run path.

## Non-goals

- Not building a new agent runtime or event format — IM is a transport, nothing more.
- Not supporting every IM platform now. Scope is Slack + Feishu; WhatsApp/Teams later.
- Not replacing the web UI; the web conversation list remains the source of truth.
- No per-message billing model changes — runs bill exactly as web runs do.
- No rich Slack/Feishu app distribution (Marketplace listing) in v1.
- No interactive components (buttons, slash-command menus) in v1 beyond mentions/DMs.

## Current state — what an IM bridge must reuse

The bridge sits on top of the existing run path; it does not invent a parallel one.

- **Conversation model** — `backend/cubebox/models/conversation.py`. A `Conversation`
  is `OrgScopedMixin` (carries `org_id` + `workspace_id`) plus `creator_user_id` and a
  `title`. Public ID prefix `conv`. An IM thread maps to one of these rows.
- **Start a run** — `RunManager.start_run(conversation_id, content, attachments, ctx)`
  in `backend/cubebox/streams/run_manager.py` (line ~482). It claims an active-run slot
  in Redis (one active run per conversation), spawns `_execute_run` as a background
  task, and returns a `run_id`. `ctx` is a `RunContext(user_id, org_id, workspace_id)`.
- **Run events** — `_execute_run` appends typed events (`text_delta`, `tool_call`,
  `tool_result`, `reasoning`, `artifact`, `done`, `error`) to a Redis stream via
  `append_run_event` (`backend/cubebox/streams/run_events.py`). This is the same stream
  the web SSE endpoint tails.
- **SSE consumption** — `_build_run_streaming_response` in
  `backend/cubebox/api/routes/v1/conversations.py` (line ~301) replays the Redis backlog
  then live-tails. The IM outbound side is a *second consumer* of this same stream — it
  reads the same events and renders them into an IM message instead of an SSE frame.
- **History** — message history lives in the cubepi `PostgresCheckpointer`, read via
  `init_checkpointer().load(conversation_id)` (`conversations.py` ~623). Same store
  whether a turn came from web or IM.
- **Auth / scoping** — `Organization → Workspace → Membership → User`. Web routes are
  `/api/v1/ws/{workspace_id}/...` guarded by `require_member` (`RequestContext`). Repos
  enforce `(org_id, workspace_id)` via `OrgScopedMixin` + `ScopedRepository`. Two
  deployment modes (`single_tenant`, `multi_tenant`) — see `backend/docs/auth.md`.
- **Credential vault** — `backend/cubebox/models/credential.py` +
  `backend/cubebox/services/credential.py`. One row per secret, `kind` discriminator,
  `value_encrypted`. System creds use `org_id=NULL` + partial unique index
  `uq_credential_system_kind_name`; org-scoped creds set `org_id` +
  `uq_credential_org_kind_name`. We add a new `kind` for IM bot secrets and reuse the
  service as-is.
- **Scope-tiered config precedent** — `MCPCredentialGrant`
  (`backend/cubebox/models/mcp.py` ~219) is the model to mirror: one table, nullable
  `workspace_id`/`user_id`, a CHECK constraint pinning legal scope combinations, and
  partial unique indexes per scope. The sandbox-env vault
  (`docs/dev/plans/2026-05-25-sandbox-env-vault.md`) follows the same shape.

The decisive observation: **a run is fully decoupled from its HTTP connection.** The web
client merely tails a Redis stream. So an IM connector needs only to (a) call
`start_run` with the right `RunContext`, and (b) tail the resulting run's Redis stream
and push rendered chunks back to IM. No runtime changes required.

## Platform research

### Slack

- **Two connection modes.** *HTTP Events API* (Slack POSTs events to a public request
  URL) vs *Socket Mode* (the app holds an outbound WebSocket, no public URL). Slack
  recommends HTTP for production / Marketplace apps; Socket Mode for dev or
  firewall-bound deployments.
  ([comparing HTTP & Socket Mode](https://docs.slack.dev/apis/events-api/comparing-http-socket-mode/),
  [event delivery](https://api.slack.com/apis/event-delivery))
- **Inbound events.** `app_mention` (mentioned in a channel), `message.im` (DM), and
  thread replies. Slack assistant threads add `assistant_thread_started` and an
  `assistant.threads.setStatus` "is typing…" affordance (scope `assistant:write`).
  ([OpenClaw Slack](https://docs.openclaw.ai/channels/slack))
- **Threading.** Replies carry `thread_ts` (the parent message ts). Streamed replies
  should always be thread replies to the triggering message.
  ([chat.update](https://docs.slack.dev/reference/methods/chat.update/))
- **Streaming presentation.** Two options: (1) the classic *debounced edit* loop — post a
  placeholder, accumulate tokens, `chat.update` every ~500ms / N tokens; `chat.update`
  is Tier-3 (~50/min/channel), so debouncing is mandatory. (2) Slack's newer native
  streaming `chat.startStream` / `chat.appendStream` / `chat.stopStream` (~300ms min
  between calls), which is purpose-built for LLM token streams.
  ([rate limits](https://docs.slack.dev/apis/web-api/rate-limits/),
  [chat_stream](https://docs.slack.dev/tools/python-slack-sdk/reference/web/chat_stream.html))
- **Auth.** OAuth install yields a bot token (`xoxb-`); we also store the signing secret
  (HTTP mode) or app-level token (`xapp-`, Socket Mode). Bot scopes for v1:
  `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `channels:history`,
  `assistant:write` (optional, for typing status). Defined via an app manifest.
  ([OpenClaw Slack](https://docs.openclaw.ai/channels/slack))

### Feishu / Lark

- **Two event modes**, mirroring Slack: *Webhook/callback* (Feishu POSTs events to a
  configured callback URL) vs *long connection* (the official Lark SDK holds an outbound
  WebSocket; no public URL).
  ([Feishu callback config](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/event-subscription-guide/callback-subscription/configure-callback-request-address))
- **Messaging & cards.** The bot sends messages and, importantly, *interactive cards*
  that can be updated in place — Feishu's native vehicle for streaming: the bot updates
  one card as text generates. In webhook mode a separate Card Request URL is configured;
  in long-connection mode the SDK handles it.
  ([OpenClaw Feishu](https://docs.openclaw.ai/channels/feishu),
  [LangBot Lark](https://docs.langbot.app/en/deploy/platforms/lark))
- **Auth.** App credentials (`app_id` / `app_secret`) plus an `Encrypt Key` and
  `Verification Token` for callback validation. The SDK exchanges these for short-lived
  tenant access tokens. Note an observed failure mode: WebSocket mode can cause excessive
  token refresh / quota exhaustion if not managed carefully.
  ([token refresh issue](https://github.com/openclaw/openclaw/issues/15293))

### How agent products bridge IM ↔ session

OpenClaw's model is the reference: a **binding** maps an IM account (a Slack workspace, a
Feishu app) to one agent, and a **session key** is derived deterministically from the IM
location — e.g. `agent:<id>:slack:channel:<channelId>:thread:<rootTs>`. The thread root
and its replies share one session; DMs can collapse into a "main" session or split per
DM. This is exactly the thread↔conversation mapping we need.
([OpenClaw Slack](https://docs.openclaw.ai/channels/slack),
[multi-agent routing](https://docs.openclaw.ai/concepts/multi-agent))

## Proposed design

### Connector abstraction

A connector is a per-platform adapter with two halves around a shared core:

```
inbound:  IM event  ─► normalize ─► resolve binding+identity+thread ─► start_run
outbound: run stream ─► render chunks (platform-specific) ─► edit/append IM message
```

- **`IMConnector` protocol** (platform-agnostic): `parse_inbound(raw) -> InboundEvent`,
  `render_outbound(run_event, state) -> OutboundOp`, `send/edit(OutboundOp)`. Concrete
  `SlackConnector`, `FeishuConnector` implement the platform calls; everything between is
  shared.
- **Inbound core** is platform-agnostic: given an `InboundEvent`
  (`account_ref`, `channel_id`, `thread_id`, `sender_ref`, `text`), it resolves the
  binding → workspace, the sender → user, the thread → conversation, then calls
  `RunManager.start_run(conversation_id, content=text, ctx=RunContext(...))`. **No new
  run path** — same entry point as the web `send_message` route.
- **Outbound core** subscribes to the run's Redis event stream (the same
  `read_run_events_after` tail used by SSE) and feeds events to the connector's
  `render_outbound`, which decides edit-vs-append and emits platform API calls.

Inbound delivery mode (HTTP webhook vs WebSocket/Socket Mode): v1 supports **HTTP
webhooks** as the canonical production path (one public ingress, stateless, scales with
the API), with Socket/long-connection left as a documented dev convenience. This keeps
multi-tenant routing simple — every event arrives at one signed endpoint and is routed by
account.

### Inbound: webhook routing & verification

- One ingress per platform: `POST /api/v1/im/slack/events`, `POST /api/v1/im/feishu/events`.
  These are **unauthenticated by cubebox session** (no `require_member`) — they are
  verified by the *platform's* signature (Slack signing secret; Feishu encrypt key +
  verification token). Signature check is mandatory before any work.
- The payload identifies the IM account (Slack `team_id` / app id; Feishu `app_id`). We
  look up the `IMConnectorAccount` row by that external id to find `org_id` +
  `workspace_id`. An unknown account → 200 ack + drop (never error-leak).
- Slack URL-verification challenge and Feishu `url_verification` are handled inline.
- These IM ingress routes are deliberately **not** under `/ws/{workspace_id}/...` because
  the caller is the platform, not a cubebox member; the workspace is *derived* from the
  account, not asserted by the URL. Management routes (below) stay scope-isolated.

### Inbound idempotency (dedupe before any run)

Both platforms redeliver: Slack retries failed/slow deliveries with an `x-slack-retry-num`
header (the event itself carries a stable `event_id`), and Feishu callbacks can also be
redelivered (each event carries a stable `event_id` / message id). Without dedupe, a retry
arriving *after* the first run already finished would find the `IMThreadLink` conversation
already present, reuse it, and call `start_run` again — duplicating the reply, re-running
tool side effects, and double-billing one IM message. The thread-link uniqueness check is
not dedupe: it prevents two conversations for one thread, not two runs for one event.

So the ingress persists an **idempotency receipt keyed by the platform event id** in the
**same database transaction** that durably commits the run to be processed — a
transactional outbox. Receipt and "this event will be run" are one atomic fact: either both
commit (a worker will pick the run up, even after a crash) or neither does (the platform
retry re-delivers and we start over). This is the durable resolution; it does *not* lean on
in-process timing or the lease to avoid dropping an unstarted event.

- A new `IMWebhookReceipt` table records each processed event by
  `(account_id, platform_event_id)` with a unique constraint. `platform_event_id` is
  Slack's `event_id` (preferred over `event_ts`, which retries preserve) / Feishu's event
  `event_id`. The row carries a `status` (`pending` | `completed`) and a `lease_expires_at`
  timestamp used only as a *secondary* guard against two workers grabbing the same `pending`
  row — not as the mechanism that keeps an event from being dropped.
- On every inbound event, after signature verification and account lookup but **before**
  any thread/conversation/run work, open one transaction that does both:
  1. Insert the receipt row (`status=pending`).
  2. Create/reuse the `Conversation` + `IMThreadLink`, then **enqueue a durable run record**
     (a row in the run queue / outbox table) referencing that conversation and event.
  Commit them together. The webhook handler then acks 200; it does **not** itself execute
  the run. A separate worker drains the queue, calls `RunManager.start_run`, and on success
  flips the receipt to `completed`.
  - **Insert succeeds + commit** → the event is now durably owned by the queue. Even if the
    web process dies the instant after commit, the queued run survives and a worker runs it.
  - **Unique violation, existing row is `completed`** → genuine duplicate of a finished
    event → **ack 200 and stop**, no side effects. This is the ack-without-side-effects
    behavior platforms expect for retries.
  - **Unique violation, existing row is `pending`** → the event is already durably enqueued
    (or in flight) → **ack 200 and stop**; the queue, not this retry, will run it. The
    lease only decides whether a *worker* may re-claim a stalled `pending` run, never whether
    the platform's retry is allowed to drop the event.
- Because the receipt and the durable run enqueue commit atomically, the crash window codex
  flagged is closed: there is no interval where the receipt says "seen" but no run is
  guaranteed to happen. A crash before the commit rolls back both (platform retry recovers);
  a crash after the commit leaves a queued run for any worker to drain. The receipt is the
  source of truth for "have we acted on this event"; the thread link only maps thread →
  conversation; the run queue is the source of truth for "this will be executed".
- **Dependency:** this requires a durable run queue / outbox that a worker drains
  independently of the web process. cubebox today starts runs in-process
  (`RunManager.start_run` → `asyncio.create_task` over Redis run state), so this durable
  queue does not exist yet — see Open Questions. Until it lands, the lease-based receipt is
  the fallback, but it is explicitly a *narrowed*, not closed, window.
- Receipts are short-lived bookkeeping: a periodic prune drops rows older than the longest
  platform retry window (Slack retries over ~minutes/hours; we keep a conservative window —
  exact retention in Open Questions). They are `OrgScopedMixin` like the other tables.

This is the same dedup seam the triggers work (#152) needs for its own webhook sources, so
if #152's "event → run" entry lands first, the receipt check belongs *in that shared seam*
rather than duplicated per connector (see "Relationship to triggers").

### Session boundary: connector-owned `scope_key`

A new `IMThreadLink` table is the durable map. The uniqueness key is
`(account_id, channel_id, scope_key)`. **`scope_key` is a non-null opaque
string the connector owns** — cubebox guarantees uniqueness on it but does
not interpret it. Each platform encodes its natural session boundary into
the string. A separate `scope_kind` column records what the connector chose
(`'dm' | 'participant' | 'thread' | 'thread_participant' | ...`); it is for
observability + admin filtering only, **not part of the unique index**, so
new kinds add freely.

This avoids re-migrating the schema each time a new platform with a
different session model is wired in. Per-platform mapping:

| Platform / scenario              | `scope_key`                       | `scope_kind`         | `reply_to_id`          |
|----------------------------------|-----------------------------------|----------------------|------------------------|
| Feishu DM                        | `"dm"`                            | `dm`                 | None                   |
| Feishu group @mention            | `"u:<sender_union_id>"`           | `participant`        | inbound `message_id`   |
| Feishu group + 话题 (future)     | `"u:<union_id>|t:<thread_id>"`    | `thread_participant` | inbound `message_id`   |
| Slack DM                         | `"dm"`                            | `dm`                 | None                   |
| Slack channel @ → starts thread  | `"t:<thread_ts>"`                 | `thread`             | `thread_ts`            |
| Slack thread reply               | `"t:<thread_ts>"`                 | `thread`             | `thread_ts`            |

Rules:

- **First inbound for a `(account, channel, scope_key)`** triple → create
  a `Conversation` (title seeded from the first line) and insert an
  `IMThreadLink` binding the scope to `conversation_id`.
- **Subsequent inbounds with the same scope** → reuse the existing
  conversation; the agent has full context. Mirrors the web "same
  conversation = same checkpointer state".
- **DMs use a literal sentinel `"dm"`** (channel_id distinguishes which
  DM), never NULL — Postgres treats NULL as distinct in unique indexes,
  so two DM rows would collide-by-accident if NULL were allowed.
- **One active run per conversation** is already enforced by `start_run`;
  a second IM message while a run is live is queued as a follow-up turn
  (steering is out of v1 scope).

**Feishu chat × user is the chosen group default.** Treating every group
@-mention as a new "thread root" misroutes badly in Feishu's real usage —
话题/topic is rare, so each fresh @ would otherwise spawn a new
conversation with no memory of the previous one in the same group. v1
keys group conversations on `(group, sender_union_id)` instead, matching
hermes-agent's validated UX.

### Identity mapping (IM user/channel → workspace/user)

- An `IMIdentityLink` table maps `(account_id, im_user_id)` → cubebox `user_id`.
- **Provisioning v1:** binding-level default. Each `IMConnectorAccount` names a single
  cubebox user as the "acting user" for runs from that account (a service identity is
  acceptable). All runs from that account are attributed to that user. This is the
  simplest correct thing and is enough for a workspace-scoped bot.
- **Optional verified linking (later):** a `/link` flow where an IM user proves they own
  a cubebox account (e.g. enters a short code from the web UI), creating an
  `IMIdentityLink`. Until then, falls back to the binding's acting user.
- Attribution always lands inside the bound workspace's `(org_id, workspace_id)`, so
  multi-tenant isolation holds regardless of identity resolution: a Slack user with no
  link still cannot reach another tenant's data, because the *account* is what selects the
  workspace.
- **`sender_ref` for `scope_key` is a separate facet from RunContext attribution.**
  The cubebox `RunContext.user_id` is the binding's acting user (above); the inbound
  message's `sender_ref` (Feishu: union_id) is independently used to compose the
  group scope_key. These two must not be conflated — a per-user IMIdentityLink would
  upgrade attribution without changing scope, and vice versa.

#### Feishu three-tier identity model

Feishu (per <https://open.feishu.cn/document/home/user-identity-introduction/introduction>)
exposes three ids per user:

- `open_id` (`ou_xxx`) — app-scoped; available without extra scope.
- `union_id` (`on_xxx`) — developer-scoped; available without extra scope, stable
  across DMs and groups for the same person.
- `user_id` (`u_xxx`) — tenant-scoped; requires `contact:user.employee_id:readonly`.

cubebox prefers `union_id` for `IMIdentityLink.im_user_id` AND as the `sender_ref`
that composes `scope_key` (groups), with `open_id` as a fallback. `open_id` is used
exclusively for the group mention gate (mentioned_open_id == bot_open_id).

### Credential storage

- New credential vault `kind = "im_bot"`. One vault row per account holding the bot
  secrets as encrypted JSON (Slack: bot token, signing secret, optional app token;
  Feishu: app_id, app_secret, encrypt key, verification token).
- The `IMConnectorAccount` row references the credential id (FK), mirroring how MCP
  installs reference `MCPCredentialGrant`. Reuse `CredentialService.create / get_decrypted
  / upsert_by_kind_name` unchanged.
- Account rows are **org-scoped** (`org_id` set). System/global IM accounts are not a v1
  concept, but the partial-unique-index pattern leaves the door open (`org_id=NULL`).

### Data model (new tables)

Public ID prefixes follow the per-model `_PREFIX` convention (no edit to
`backend/cubebox/models/public_id.py` needed): `imac` (account), `imtl` (thread
link), `imil` (identity link), `imwr` (webhook receipt), `imrq` (run queue item).

- **`IMConnectorAccount`** (`OrgScopedMixin`): `platform` (`slack`|`feishu`),
  `external_account_id` (Slack team/app id, Feishu app id), `workspace_id` (the bound
  workspace), `acting_user_id` (default attribution), `credential_id` (FK to vault),
  `delivery_mode` (`webhook` v1), `enabled`, config JSON. Partial unique index on
  `(platform, external_account_id)` so an external IM account binds to at most one cubebox
  account row.
- **`IMThreadLink`** (`OrgScopedMixin`): `account_id` (FK), `channel_id`,
  **`scope_key`** (non-null, opaque connector-owned string), **`scope_kind`** (label
  for observability — NOT part of the uniqueness key), `conversation_id` (FK). Unique
  on `(account_id, channel_id, scope_key)`. NULL is forbidden because Postgres would
  otherwise allow duplicate `(…, NULL)` rows. The plan adds `imrq` (run queue) with
  the same `scope_key`/`scope_kind` columns + a distinct `reply_to_id` for the real
  platform reply target.
- **`IMIdentityLink`** (`OrgScopedMixin`): `account_id` (FK), `im_user_id`, `user_id`
  (FK). Unique on `(account_id, im_user_id)`.
- **`IMWebhookReceipt`** (`OrgScopedMixin`): `account_id` (FK), `platform_event_id`,
  `status` (`pending` | `completed`), `lease_expires_at`, `created_at`. Unique on
  `(account_id, platform_event_id)`. Inserted **in the same transaction** that durably
  enqueues the run (transactional outbox), so receipt and queued run commit or roll back
  together — a redelivered event hits the index and is acked without re-running, while a
  crash after commit still leaves a queued run for a worker to drain. `lease_expires_at` is
  only a secondary guard so two workers don't claim the same `pending` queued run; it is not
  what prevents an event from being dropped. Pruned past the retry window.

All `OrgScopedMixin` so `(org_id, workspace_id)` filtering is structural. Migrations via
`alembic revision --autogenerate`.

### Streaming presentation in IM (edit vs append)

The web UI gets fine-grained SSE; IM needs coarser, debounced updates to respect rate
limits. The outbound core maintains per-run render state and emits:

- **First text** → post a placeholder reply in-thread; record the message id/ts.
- **Streaming text** → debounced edit of that message. Slack: prefer native
  `chat.startStream`/`appendStream`/`stopStream` where available, else throttled
  `chat.update` (≥500ms). Feishu: update one interactive card in place.
- **Tool activity** → render compactly (e.g. an italic "running `web_search`…" line or a
  collapsed section) rather than streaming every `tool_call_delta`; coalesce.
- **`done`** → final edit to the complete answer (Slack `stopStream`; Feishu finalize the
  card). **`error`** → replace with an error notice.
- **Long answers** → if the message exceeds platform limits, append a follow-up reply in
  the same thread rather than truncating.

Edit (not append) is the default for the assistant's answer so the thread stays clean;
append is reserved for overflow and for distinct turns.

### Scope-isolated config / routes

Following the hard rule (workspace vs admin = separate handlers):

- **Workspace scope** — `POST/GET/DELETE /api/v1/ws/{workspace_id}/im/accounts` and
  `.../im/accounts/{id}`: a workspace member connects/lists/disconnects the workspace's
  own IM bots, sets the acting user, manages identity links. Guarded by `require_member`.
- **Org-admin scope** — `GET /api/v1/admin/im/accounts` (+ enable/disable): an org admin
  sees every IM account across the org's workspaces for governance. Separate handler file,
  separate route; reuse goes through a shared `IMConnectorService`, never a `?scope=` flag.
- **Platform ingress** — `/api/v1/im/{platform}/events` (unauthenticated by session,
  platform-signed) is its own concern, neither workspace nor admin.
- **Frontend** — separate Next pages: a workspace "Integrations → IM" page and an
  admin "IM accounts" page, each its own route + page file; shared `<IMAccountList>` /
  `<ConnectAccountWizard>` modules are the reuse boundary.

## Per-platform specifics

### Slack

- Install via app manifest (declares scopes + event subscriptions + request URL). v1
  documents a manifest template; OAuth install stores `xoxb` bot token + signing secret
  in the vault.
- Ingress verifies the `X-Slack-Signature` HMAC (signing secret) and the timestamp.
- Inbound events: `app_mention`, `message.im`, thread replies. Strip the bot mention from
  the text before passing to `start_run`.
- Outbound: thread reply on the triggering `thread_ts`; debounced `chat.update` or native
  streaming APIs; optional `assistant.threads.setStatus` "thinking…" while the run is live.

### Feishu

- App configured with `app_id` / `app_secret` / encrypt key / verification token, stored
  in the vault. Ingress decrypts (encrypt key) and verifies (verification token); handles
  the `url_verification` challenge.
- Inbound events: `im.message.receive_v1` (mentions + DMs). Resolve the bound workspace by
  `app_id`.
- Outbound: send an interactive card and update it in place as text streams; finalize on
  `done`. SDK manages tenant-access-token refresh; cap refresh to avoid the quota
  exhaustion failure mode noted above.

## Relationship to triggers (#152)

Triggers (#152) is the general "an external event starts an agent run" mechanism (cron,
webhooks, etc.). IM connectors are a *specialized, bidirectional* trigger:

- Inbound IM is a trigger source — both ultimately call `RunManager.start_run` with a
  `RunContext`. If #152 lands first with a clean "event → run" entry, the IM inbound core
  should call *that* seam rather than `start_run` directly, so triggers and IM share
  routing/attribution/rate-limit policy. The inbound idempotency receipt (dedupe by
  platform event id before run creation) is part of that shared seam — any webhook trigger
  source faces the same retry/redelivery problem, so the receipt check should live with the
  "event → run" entry, not be reimplemented per connector.
- What IM adds beyond a generic trigger is the **outbound** half: tailing the run stream
  and rendering it back into the *same* IM thread. That is IM-specific and stays in the
  connector. Recommendation: design the inbound core against a small "start a run from an
  external source" interface so it can sit on #152 once available, but ship IM without
  blocking on #152.

## v1 scope

- **Feishu first.** lark_oapi's long-connection mode needs no public ingress,
  so the loop "@-mention → run → reply" can be validated inside a worktree
  without setting up a tunnel. Slack ships as a follow-up plan
  (`docs/dev/plans/2026-05-27-im-connectors.md`, frozen) that reuses the same
  neutral data model + connector protocol — only the platform-specific
  adapter differs.
- **Two delivery modes**: long connection (recommended for self-host) +
  HTTP webhook (cloud deploys behind a public LB). Both feed the same
  `ingest_inbound_event` core; only the inbound transport differs.
- **Session boundary in groups is `(chat × sender)`**, not `thread × thread`.
  Feishu's 话题/thread feature is rare in practice; treating every @-mention
  as a new thread misroutes the common "A keeps talking to bot in the same
  group" case. v1 keys group conversations on `(group, sender_union_id)`;
  话题 overlay is reserved for future work.
- **Binding-level acting user** for attribution; verified per-user linking
  deferred.
- **Edit-based streaming** (debounced 0.8s with adaptive backoff) for the
  assistant answer; coalesced tool activity.
- **Processing reactions are required v1 UX**: ⏱️ on start, removed on
  success, ❌ on failure. Wired through connector-level `on_processing_start`
  / `_complete` / `_failed` hooks so the tailer stays platform-agnostic
  (Slack will implement these via `assistant.threads.setStatus`).
- **Artifact share-links are required v1**: image artifacts upload inline
  as native Feishu image messages; other types post a `📎 view →` link
  that opens a public preview page nonce-scoped to the artifact (7d TTL).

## Testing strategy

E2E-first per project discipline, tempered by the "no fake E2E for unsimulatable systems"
rule — Slack/Feishu have no usable end-to-end test mode, so we do **not** stand up a fake
Slack server and call it E2E.

- **Real internal E2E (the bulk).** Test the half we own end-to-end: feed a *captured
  real* inbound payload (a recorded Slack/Feishu event, signature included) into the
  ingress route against a real Postgres + Redis + run path, and assert that (a) the right
  conversation is created/reused, (b) a run starts with the correct `RunContext`, (c) the
  outbound core consumes the run's real Redis event stream. The agent run itself is the
  existing E2E run path (already covered), so this exercises the full inbound→run→stream
  chain without mocking cubebox internals.
- **Signature verification** as focused unit tests with real platform fixtures (valid +
  tampered Slack HMAC; valid + bad Feishu encrypt/token) — security-critical, cheap.
- **Outbound rendering** as unit tests: given a sequence of run events, assert the
  edit-vs-append decisions and debounce/coalesce behavior (no network).
- **Platform API boundary** (the genuinely unsimulatable part — Slack/Feishu HTTP calls)
  is isolated behind the connector's `send/edit` methods and tested with a thin recorded
  contract (request shape assertions) plus a documented **manual smoke checklist** against
  a real dev Slack workspace / Feishu tenant before release. We do not fake their servers.
- **Multi-tenant isolation** E2E: two accounts bound to two workspaces; assert an event
  for account A never touches workspace B's conversations.

## Open Questions

- **DM conversation lifetime.** One rolling conversation per DM forever, per-day, or a
  `/new` command to reset? Long-lived DMs grow checkpointer context unbounded.
- **Concurrent IM messages on a live run.** Queue as a follow-up turn, or inject as a
  steer (`RunManager.steer_run`)? Steering is live but may surprise the IM user.
- **Identity for unlinked senders.** Is a single binding-level acting user acceptable for
  v1, or do we need at least a "who am I replying as" indicator in the thread?
- **Inbound delivery mode.** Is HTTP-only acceptable for v1, given some self-host
  deployments are firewall-bound and would prefer Socket Mode / long connection?
- **Slack native streaming availability.** `chat.startStream` may be gated/rolling out;
  do we ship debounced `chat.update` first and adopt native streaming when broadly
  available?
- **Rate-limit backpressure.** When a run emits faster than the platform allows edits,
  do we drop intermediate frames (latest-wins) or risk lag? Latest-wins is assumed.
- **Tool activity verbosity.** How much tool detail belongs in IM vs a "view full run in
  cubebox" deep link? Default: compact summary + deep link.
- **Attachments / files.** Inbound IM file uploads and outbound artifacts — in scope for
  v1 or deferred? (Web already has an attachment path to reuse.)
- **Durable run queue / outbox (dependency).** The idempotency design commits the receipt
  and the run enqueue in one transaction, then a worker drains the queue and calls
  `start_run`. cubebox today starts runs in-process (`asyncio.create_task` over Redis run
  state) with no durable queue, so this table + drainer must be built (or adopted from
  whatever #152 triggers introduce) before the crash window is truly closed. Open: does the
  outbox row live in the same DB so it joins the receipt transaction, with a poller flipping
  Redis-backed runs, or do we make run creation itself transactional? Until it lands, the
  lease-based receipt narrows but does not close the window.
- **Worker re-claim lease duration.** Once a durable queue exists, how long may a `pending`
  queued run sit before another worker re-claims it (the secondary lease)? Too short
  double-runs a slow-but-healthy worker; too long delays recovery of a crashed worker. A
  value bounded by the run timeout (with margin) is the starting assumption. This lease only
  governs worker-vs-worker hand-off, not whether platform retries drop the event.
- **Webhook receipt retention.** How long do we keep `IMWebhookReceipt` rows before
  pruning? Must exceed the longest platform retry window (Slack retries can span hours);
  a conservative fixed window (e.g. 24–72h) vs a platform-specific one is undecided.
- **Finite platform retry windows.** Slack/Feishu stop retrying after a bounded window, so
  the transactional outbox (not retries) is what guarantees an accepted-and-committed event
  still runs after a crash. Any event whose receipt+enqueue transaction never commits before
  the platform gives up is genuinely lost; the outbox is what shrinks that to "we never
  durably accepted it" rather than "we accepted then dropped it".
- **Single-process run affinity.** `steer_run`/`cancel_run` only work in the process
  hosting the run; does the outbound consumer need to live in the same process as the run,
  or can it tail Redis from any worker? (Tailing is cross-process; control is not.)

## References

- Slack — [HTTP vs Socket Mode](https://docs.slack.dev/apis/events-api/comparing-http-socket-mode/),
  [event delivery](https://api.slack.com/apis/event-delivery),
  [chat.update](https://docs.slack.dev/reference/methods/chat.update/),
  [chat_stream (python-slack-sdk)](https://docs.slack.dev/tools/python-slack-sdk/reference/web/chat_stream.html),
  [rate limits](https://docs.slack.dev/apis/web-api/rate-limits/)
- Feishu / Lark —
  [callback / persistent connection config](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/event-subscription-guide/callback-subscription/configure-callback-request-address),
  [LangBot Lark setup](https://docs.langbot.app/en/deploy/platforms/lark),
  [token-refresh failure mode](https://github.com/openclaw/openclaw/issues/15293)
- Bridging patterns — [OpenClaw Slack](https://docs.openclaw.ai/channels/slack),
  [OpenClaw Feishu](https://docs.openclaw.ai/channels/feishu),
  [OpenClaw multi-agent routing](https://docs.openclaw.ai/concepts/multi-agent)
- Internal — `backend/cubebox/streams/run_manager.py` (`start_run`),
  `backend/cubebox/streams/run_events.py` (`append_run_event`, run stream tail),
  `backend/cubebox/api/routes/v1/conversations.py` (SSE consumption pattern),
  `backend/cubebox/models/conversation.py`, `backend/cubebox/models/credential.py`,
  `backend/cubebox/models/mcp.py` (`MCPCredentialGrant` scope pattern),
  `docs/dev/plans/2026-05-25-sandbox-env-vault.md`, `backend/docs/auth.md`.
