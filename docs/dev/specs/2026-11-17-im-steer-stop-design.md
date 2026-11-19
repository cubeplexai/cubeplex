# IM `/steer` and `/stop` Commands — Design

**Status**: Draft · 2026-11-17

---

## 1. Goal

Add explicit `/steer <content>` and `/stop` commands across all IM connectors so users can inject mid-run follow-up instructions or terminate the currently active reply without waiting for it to finish, while keeping ordinary IM messages queued sequentially as separate turns.

---

## 2. Context

Today, every non-command inbound IM message passes through `ingest_inbound_event` (`backend/cubeplex/im/inbound.py`) and inserts a pending `IMRunQueueItem` into `im_run_queue`. When `IMRunQueueWorker` (`backend/cubeplex/im/worker.py`) attempts `RunManager.start_run` on a conversation that already has a `running` or `paused_hitl` run, `start_run` raises `"already has an active run"` / `"pending HITL request"` and the worker rewinds the queue row (`rewind_queue_item_no_attempt_charge`) to poll again every 1 second until the active run completes.

As a result, IM users have two pain points compared with the web app:

1. **Cannot stop a wrong or runaway reply**: If the agent misunderstands the prompt or starts a long tool chain, the user cannot abort it from IM.
2. **Cannot add mid-run context**: Sending a clarification in IM waits until the entire first reply finishes and then triggers a second full run instead of steering the live turn.

Meanwhile, the backend runtime already has durable primitives for both operations on the web path (`backend/cubeplex/api/routes/v1/conversations.py`):

- **Durable run stop**: `ConversationExecutionService.stop_run` + `signal_stopped_runs` (`backend/cubeplex/services/execution_signals.py`), after which `RunManager` emits a terminal `done` event that `OutboundRunTailer` (`backend/cubeplex/im/outbound.py`) already folds into a clean card/message finalization.
- **Durable mid-run steering**: `SteeringMessageRepository.enqueue` + `RunManager.notify_durable_steer`, supporting both live `running` turns and `paused_hitl` pauses.
- **IM command interception precedent**: `/new` and `/reset` (`backend/cubeplex/im/reset_command.py`) already intercept control commands across all six IM platforms (`feishu`, `slack`, `discord`, `dingtalk`, `teams`, `wecom`), with native slash command registrations on Slack and Discord.

---

## 3. Approaches Considered

### Approach A (Recommended): Shared IM control-command module + shared steering service, wired at platform ingress and Slack/Discord slash commands

- Extract the core steering state machine out of `api/routes/v1/conversations.py:steer_active_run` into a reusable service helper (`backend/cubeplex/services/conversation_steering.py`) called by both the HTTP `/steer` route and IM.
- Add `backend/cubeplex/im/control_commands.py` providing command parsing (`parse_control_command`), read-only IM conversation lookup, `/stop` execution, `/steer` execution, `IMWebhookReceipt` deduplication, and an `InboundEvent` helper (`maybe_handle_control_command`) shared across event-based connectors.
- Wire text interception before `ingest_inbound_event` in Feishu (webhook + long connection), Slack (Socket Mode gateway), Discord (`on_message` — also adding `/new`/`/reset` text parity if typed with `@bot`), DingTalk (stream gateway), Teams (webhook), and WeCom (`ingest_command_response` / fallback to `ingest_inbound_event`), plus native `/stop` and `/steer` slash commands in Slack and Discord.

**Why chosen**:
- Sub-100ms control execution (bypasses the 1s `im_run_queue` poll loop and never blocks behind queued messages).
- Single source of truth for steering invariants (`require_run_input_actor`, HITL vs running ownership verification, `fail_queued` race recovery) between Web and IM.
- Cleanly supports `/steer <content>` idle fallback: when no active run exists, ingress rewrites `event.text = <content>` and delegates straight to `ingest_inbound_event` without burning or colliding on `uq_im_receipt_account_event`.
- Native compatibility with WeCom's `req_id` passive-reply protocol and Slack/Discord ephemeral slash-command interactions.

### Approach B: Enqueue `/stop` and `/steer` into `im_run_queue` and execute inside `IMRunQueueWorker`

- Let `ingest_inbound_event` store `/stop` and `/steer` as `IMRunQueueItem` rows and branch inside `process_one_queue_item`.
- **Rejected**: Adds up to 1s poll latency, requires special-casing queue claim ordering so `/stop` jumps ahead of older `pending` items on the same conversation, cannot send synchronous passive replies on WeCom (`req_id` window) or ephemeral responses on Slack/Discord slash commands, and unnecessarily creates conversations on `/stop` in empty chats.

### Approach C: Auto-steer all ordinary IM messages sent while a run is active, adding only `/stop`

- Mirror the web composer by turning any plain IM message sent during an active run into a steer instead of queuing it.
- **Rejected**: Explicitly declined during requirement alignment. In IM (especially group chats and mobile multi-message habits), users routinely send independent follow-up tasks back-to-back expecting separate queued replies; explicit `/steer <content>` avoids accidental mid-run injection while keeping ordinary messages queued.

---

## 4. Design

### 4.1 Command Grammar

Parsed in `backend/cubeplex/im/control_commands.py` against `InboundEvent.text` (after platform `parse_inbound` has already stripped any leading `@bot` mention in group chats):

| Input pattern | Parsed command | Notes |
|---|---|---|
| `^\s*/stop\s*$` (case-insensitive) | `StopCommand()` | Exact command token only; `/stopped` or `/stop foo` does not match. |
| `^\s*/steer(?:$\|\s+(?P<body>.*))$` (`IGNORECASE \| DOTALL`) | `SteerCommand(content=body.strip())` | Preserves multi-line body; `/steering` does not match. Empty `content` (`/steer` alone) returns a usage hint instead of falling through to the agent. |
| Anything else | `None` | Handled by existing `/link`, `/new`, or `ingest_inbound_event` queue path. |

**Native slash commands** (in addition to plain-text `@bot /stop` and `@bot /steer <content>`):
- **Slack** (`backend/cubeplex/im/slack/commands.py`): register `/stop` and `/steer` (reads `command["text"]`). Note: Slack's platform limitation still applies inside threads, where plain-text `@bot /stop` and `@bot /steer <content>` work via `SlackGateway`.
- **Discord** (`backend/cubeplex/im/discord/commands.py`): register `/stop` and `/steer` (with required `message: str` app-command option). Also wire text-command interception in `DiscordGateway.on_message` so `@bot /stop` and `@bot /steer <content>` work in channels/threads identically to other platforms.

### 4.2 Data Model & Migrations

**No database schema changes or Alembic migrations are required.**

Existing tables cover all persistence and idempotency invariants:
- `im_webhook_receipts` (`uq_im_receipt_account_event` on `(account_id, platform_event_id)`): deduplicates platform webhook/gateway redeliveries for handled `/stop` and `/steer` commands (`status = "completed"`). On WeCom, `response_payload` continues to store `{"kind": "text", "text": ...}` for `req_id` passive/proactive delivery.
- `steering_messages` (`uq_steering_messages_conversation_client_id` on `(conversation_id, client_steer_id)`): stores durable mid-run steers with deterministic `client_steer_id = f"im:{sha256(f'{account_id}:{platform_event_id}'.encode()).hexdigest()[:48]}"` (51 chars, within `VARCHAR(64)`).
- `conversation_execution_admissions`: tracks `run_stop_requested_at` via `ConversationExecutionService.stop_run`.

### 4.3 Identity Gate, Scope, & RBAC Invariants

1. **Workspace member gate (mandatory before acting)**:
   - Unlike `/link`, both `/stop` and `/steer` mutate workspace conversation execution state. Every invocation must resolve the sender to an active workspace member first:
     - Platforms with `IdentityResolver` (Feishu, Slack gateway, DingTalk, Teams): run `resolve_or_reject` (`backend/cubeplex/im/identity.py`). Unresolved/non-member senders get the standard rejection message and `IMWebhookReceipt(status="rejected")`.
     - Manual-link / slash paths (WeCom, Discord, Slack slash commands): run `resolve_current_linked_member`. Unlinked/non-member senders receive the rejection text without touching any conversation or run.
2. **Non-creating conversation lookup**:
   - Helper `find_live_im_conversation(session, account, *, channel_id, scope_key, scope_kind, effective_user_id)`:
     - Queries `IMThreadLink` for `(account.id, channel_id, scope_key)`.
     - Returns `None` if no link exists, if the linked `Conversation` is missing or `deleted_at is not None`, or if `wants_topic(settings)` is true and the anchored `Topic` is archived (`is_archived is True`).
     - In shared mode (`settings.routing_mode == "shared" and scope_kind != "dm"`), ensures `ConversationParticipant` (and `TopicParticipant` when `conversation.topic_id` is present) for `effective_user_id` before returning, matching `resolve_im_conversation` so any workspace member in the shared IM channel passes `ConversationExecutionService._lock_authorized_conversation` when stopping a shared run.
3. **Actor isolation on `/steer` vs shared authority on `/stop`**:
   - **`/stop`**: Any authorized participant of the conversation (in 1:1, the owner; in a shared IM channel, any workspace member in that channel) may stop the active run via `ConversationExecutionService.stop_run`.
   - **`/steer`**: `ConversationExecutionService.require_run_input_actor` enforces `admission.actor_user_id == actor_user_id` so one user in a shared group chat cannot inject instructions into a run executing with another user's personal MCP credentials or sandbox authority. When `require_run_input_actor` raises `ExecutionConflictError`, IM `/steer` replies:
     `"⚠️ 只有发起当前回复的成员可以中途补充；你可以直接发送普通消息排队，或发送 /stop 终止当前回复。"`
     It must **not** bypass `require_run_input_actor` and must **not** silently enqueue a separate run in this conflict case.

### 4.4 Shared Steering Service (`backend/cubeplex/services/conversation_steering.py`)

Extract the core logic of `POST /api/v1/ws/{workspace_id}/conversations/{conversation_id}/steer` (`conversations.py#L2474-L2680`) into `steer_conversation_run(...)`:

```python
SteerAttemptOutcome = Literal[
    "queued",          # durable SteeringMessage committed + wake-up attempted (or idempotent replay)
    "no_active_run",   # neither deliverable paused_hitl nor running active_run (includes post-commit fail_queued race)
    "actor_conflict",  # require_run_input_actor rejected non-originating user in shared chat
    "queue_full",      # SteeringMessageQueueFullError
    "too_large",       # SteeringMessageContentTooLargeError / > MAX_MESSAGE_BYTES
]
```

- Preserves exact HTTP route response codes and payload shapes (`{"status": ..., "run_id": ..., "steer_id": ...}`) for `/api/v1/ws/{workspace_id}/conversations/{conversation_id}/steer` (with zero behavioral change on Web).
- Resolves `sender_display_name` (`user.display_name or user.email`) so group-chat sender attribution in CubeLoop matches Web steering and `IMRunQueueWorker`.
- Handles both deliverable `paused_hitl` (`hitl_question_id` bound) and live `running` runs, including the post-commit re-verification (`fail_queued` when the run finished between initial Redis read and Postgres commit). For IM callers, a post-commit `fail_queued` maps to `"no_active_run"` so `/steer <content>` cleanly falls back to starting a normal new run instead of dropping the user's message.

### 4.5 End-to-End Command Flows & Edge Cases

#### A. `/stop` Flow

1. Deduplicate `(account.id, event.platform_event_id)` via `IMWebhookReceipt` (if already present, no-op on webhook/gateway platforms; re-deliver cached `response_payload` on WeCom if undelivered).
2. Verify workspace member identity (`effective_user_id`).
3. Look up live `conversation_id` via `find_live_im_conversation` (never creates a conversation or topic).
   - If `conversation_id is None`: commit receipt (`status="completed"`) and reply `"ℹ️ 当前没有正在进行的回复。"`.
4. Locate active target `run_id`:
   - Check `get_active_run(redis, prefix=key_prefix, conversation_id=conversation_id)` (covers `running` and `paused_hitl`).
   - Fallback if Redis pointer is absent: check `shared_checkpointer().load_pending_run_id(conversation_id)` and latest unfinished started `ConversationExecutionAdmission` (`execution_kind == "run"`, `run_started_at is not None`, `run_finished_at is None`).
   - If no target `run_id` exists (or `stop_run` raises `LookupError` / `admission.run_finished_at is not None` before stop): commit receipt and reply `"ℹ️ 当前没有正在进行的回复。"`.
5. Call `ConversationExecutionService.stop_run(conversation_id=conversation_id, run_id=run_id, actor_user_id=effective_user_id, now=datetime.now(UTC))` and commit.
   - Stops only `run_id` (plus foreground-bound `BackgroundTask`s with `backgrounded_at is None` and uninjected `SteeringMessage`s for `run_id`).
   - **Does not** close `execution_generation` and **does not** cancel `pending` rows in `im_run_queue`: once `run_id` finishes stopping, `IMRunQueueWorker` automatically claims and runs the next queued ordinary IM message.
6. Call `await signal_stopped_runs(run_manager, conversation_id=conversation_id, run_ids=(stopped.run_id,), user_id=effective_user_id, org_id=account.org_id, workspace_id=account.workspace_id)`.
   - `OutboundRunTailer` receives `RunManager`'s terminal `done` event, finalizes the streaming card/message with any partial output generated so far, and clears the processing indicator.
7. Reply `"⏹️ 已停止当前回复。"` (if `already_stopping` — user sent `/stop` twice while cleanup was still draining — still reply `"⏹️ 已停止当前回复。"` idempotently).

#### B. `/steer <content>` Flow

1. **Empty body** (`/steer` with no text):
   - Deduplicate receipt, reply `"ℹ️ 用法：/steer <补充内容>"`.
2. **Duplicate receipt check**:
   - Before acting, check `IMWebhookReceipt` on `(account.id, event.platform_event_id)`. If a receipt already exists (whether from an earlier steer or an earlier idle-fallback `ingest_inbound_event`), treat as duplicate and return without double-sending.
3. **Identity check**:
   - Resolve `effective_user_id` (rejecting non-members with `status="rejected"` receipt + rejection reply).
4. **Check live conversation & attempt steer**:
   - Call `find_live_im_conversation(...)`.
   - If a live `conversation_id` exists, call `steer_conversation_run(...)` with deterministic `client_steer_id`.
   - **Outcome `"queued"`**:
     - Commit `IMWebhookReceipt(status="completed")`.
     - Reply `"↪️ 已补充到当前回复。"` (if `event.attachments` is non-empty, append `"（注：中途补充暂不支持附带文件，仅已注入文本内容。）"`).
   - **Outcome `"actor_conflict"`**:
     - Commit `IMWebhookReceipt(status="completed")`.
     - Reply `"⚠️ 只有发起当前回复的成员可以中途补充；你可以直接发送普通消息排队，或发送 /stop 终止当前回复。"`.
   - **Outcome `"queue_full"`**:
     - Commit `IMWebhookReceipt(status="completed")`.
     - Reply `"⚠️ 当前补充消息队列已满，请稍后再试或发送 /stop 终止当前回复。"`.
   - **Outcome `"too_large"`**:
     - Commit `IMWebhookReceipt(status="completed")`.
     - Reply `"⚠️ 补充内容过长，请精简后再试。"`.
   - **Outcome `"no_active_run"` (or no live conversation exists yet — idle fallback)**:
     - Do **not** commit a standalone command receipt (so `ingest_inbound_event` owns the receipt insert atomically with `IMRunQueueItem`).
     - On `InboundEvent` paths (Feishu, Slack gateway, Discord `on_message`, DingTalk, Teams, WeCom): clone `event` with `text = content` and pass directly to `ingest_inbound_event(...)` (enriching `channel_name` on Feishu just like normal inbound). Any attachments sent alongside `/steer <content>` are preserved on the fallback new message.
     - On Slack / Discord native `/steer` slash commands: construct an `InboundEvent` (`platform_event_id = f"slash:{command_or_interaction_id}"`, `text = content`, resolved `channel_id`, `scope_key`, `scope_kind`, `sender_ref`, `sender_open_id`), call `ingest_inbound_event(...)`, and send an ephemeral acknowledgment `"ℹ️ 当前没有正在进行的回复，已作为新消息发送。"` while `IMRunQueueWorker` streams the normal reply into the channel.

### 4.6 Runtime Dependency Plumbing

- Expose read-only `redis` and `key_prefix` properties on `RunManager` (`backend/cubeplex/streams/run_manager.py`) so any caller holding `run_manager` has typed access to the exact Redis client and prefix without global env reconstruction.
- Thread `run_manager` to the three IM command entrypoints that do not currently store it:
  1. `SlackGateway` (`im/slack/gateway.py`) → pass `run_manager=self._run_manager` into `slack/commands.py:register_commands`.
  2. `DiscordGateway` (`im/discord/gateway.py`) → attach `bot._cubeplex_run_manager = self._run_manager` alongside `_cubeplex_session_maker` / `_cubeplex_account_id`, and align `discord/commands.py` scope calculation with `lookup_binding_mode` so shared Discord channels resolve `make_channel_scope()` consistently with `on_message`.
  3. `WecomPlatform.on_account_enabled` (`im/wecom/_platform.py`) → pass `run_manager=kwargs.get("run_manager")` into `WecomGateway.configure_inbound` → `wecom/ingress.py:handle_inbound_callback`.
- Feishu webhook and Teams webhook (`api/routes/v1/im_ingress.py`) read `request.app.state.run_manager`. Feishu long connection (`im/feishu/long_connection.py`) and DingTalk gateway (`im/dingtalk/gateway.py`) already hold `self._run_manager`.

### 4.7 Product Documentation (English + 简体中文)

Update both language trees in the implementation PR (AGENTS.md Rule 13):
- `docs/site/docs/guides/im/overview.md` & `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/guides/im/overview.md` — add `/stop` and `/steer <message>` to the Conversation Commands table and platform summary rows.
- Per-platform guides (`feishu.md`, `slack.md`, `discord.md`, `dingtalk.md`, `teams.md`, `wecom.md` in both `docs/site/docs/guides/im/` and `zh-Hans/.../guides/im/`) — document `/stop` and `/steer <message>` alongside `/new` and `/link` (including Slack/Discord slash-command registration list where `/new` is listed).

---

## 5. Out of Scope

- Changing ordinary IM message behavior while a run is active (ordinary messages continue to queue in `im_run_queue` and execute after the current run finishes).
- Cancelling queued `im_run_queue` items or backgrounded daemon tasks (`backgrounded_at is not None`) from `/stop` (`/stop` stops only the active foreground run; `stop-all` / queue clearing is intentionally excluded).
- File/image attachments on mid-run steers (`SteeringMessage` is text-only across CubePlex; attachments are preserved only when `/steer` falls back to a new run on an idle conversation).
- Interactive "Stop" button on streaming IM cards (can be evaluated separately per platform rate-limit/card-action budget; text/slash `/stop` works uniformly across all six platforms).
- Cancelling an already-queued individual steer from IM (`/steer/cancel`).

---

## 6. Success Criteria

1. Sending `/stop` in an IM conversation with a `running` or `paused_hitl` run durably stops that run, signals `RunManager`, finalizes the in-flight IM reply, replies `"⏹️ 已停止当前回复。"`, and allows any subsequent queued `IMRunQueueItem` on that conversation to start normally.
2. Sending `/stop` when no run is active replies `"ℹ️ 当前没有正在进行的回复。"` without creating a new `Conversation` or `Topic`.
3. Sending `/steer <content>` while the sender's run is `running` or `paused_hitl` enqueues a durable `SteeringMessage`, notifies `RunManager` so the agent drains `<content>` mid-run, replies `"↪️ 已补充到当前回复。"`, and does **not** enqueue a second `IMRunQueueItem`.
4. Sending `/steer <content>` when no run is active (including when no conversation exists yet, or when the active run completed just before commit) automatically falls back to `ingest_inbound_event` with `<content>` and starts a normal new run without duplicate-receipt errors.
5. Sending bare `/steer` with empty content replies with usage instructions and never invokes the agent.
6. Unlinked or non-member IM users sending `/stop` or `/steer` are rejected by the workspace identity gate; in shared group chats, a second member sending `/steer` during another member's active run receives the actor-conflict message while `/stop` succeeds for any authorized channel participant.
7. Webhook/gateway redeliveries with the same `platform_event_id` are idempotent across `/stop`, active `/steer`, and idle-fallback `/steer`.
8. All six connectors (Feishu webhook + LC, Slack Socket Mode + slash, Discord gateway + slash, DingTalk, Teams, WeCom) and both English and Simplified Chinese product docs are updated in lockstep.
