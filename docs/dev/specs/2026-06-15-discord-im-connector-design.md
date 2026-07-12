# Discord IM Connector

Adds Discord as the second IM platform in cubeplex, following the existing
Feishu connector. The existing connector-neutral pipeline (inbound
transaction, queue worker, identity resolution, resume) is fully reused;
Discord-specific logic lives in a new `im/discord/` module and a
platform-registry mechanism replaces the current hard-coded Feishu wiring.

Reference implementations studied: hermes-agent (discord.py Gateway,
plugin adapter, voice support) and openclaw (@buape/carbon Gateway,
three-stage pipeline, multi-account).

## Scope

**In scope (v1):**

- Gateway connection (discord.py, WebSocket long-connection only)
- Receive messages: DM + Guild channel @mention + Thread
- Outbound: streaming Markdown message edits + typing indicator
- AskUser / SandboxConfirm via Discord Button components
- Thread support (scope_key = `"t:{thread_id}"`)
- Slash commands: `/new`, `/reset`
- Artifact share links (appended to final message)
- Frontend connect wizard (platform descriptor)
- Platform registry (replaces hard-coded Feishu wiring in runtime.py)
- Distributed gateway ownership (Redis lease, multi-instance safe)

**Out of scope:**

- Voice channels
- Forum channels
- Embed-based tool-step rendering
- Full slash command suite
- Multi-account per workspace
- Per-user identity resolution (v1 uses acting_user fallback)
- User allowlists / channel allowlists

## Connection: Gateway Only

Discord requires a Gateway (WebSocket) connection to receive
`MESSAGE_CREATE` events. HTTP Interactions only handle slash commands and
component callbacks, not regular chat messages. The Feishu connector
supports two delivery modes (long_connection and webhook); Discord supports
only Gateway — the `delivery_mode` field is always `"gateway"`. The
existing schema validation pattern (`^(long_connection|webhook)$`) must be
extended to include `gateway`.

The discord.py library handles the WebSocket lifecycle: connection,
heartbeat, reconnect, resume, intent negotiation.

Required Gateway intents:

- `MESSAGE_CONTENT` (privileged — requires Portal approval) — read message text
- `GUILD_MESSAGES` — receive guild channel messages
- `DIRECT_MESSAGES` — receive DM messages
- `GUILD_MESSAGE_REACTIONS` — reaction support for processing indicators

## Platform Registry

A `PlatformConnector` protocol replaces the hard-coded Feishu references in
`im/runtime.py`. Each platform registers itself; the worker and runtime
look up the connector by `account.platform`.

```
im/registry.py         — PlatformConnector protocol + register/get
im/feishu/__init__.py   — registers FeishuPlatform
im/discord/__init__.py  — registers DiscordPlatform
```

The protocol surface:

- `parse_inbound(raw) → InboundEvent | None`
- `build_tailer(run_id, queue_item, account, redis, ...) → OutboundRunTailer`
- `on_account_enabled(account) → None`
- `on_account_disabled(account) → None`

The `_on_run_started` callback in `runtime.py` calls
`get_platform(account.platform).build_tailer(...)` instead of directly
constructing a FeishuConnector + CardKitClient.

## Distributed Gateway Ownership

Problem: multiple API instances each run `runtime.start()`. Without
coordination, every instance opens a Gateway connection for every Discord
account, receiving duplicate messages. (This problem also exists for Feishu
long-connections but is less visible because the Feishu SDK may distribute
events across connections.)

Solution: Redis-based per-account lease.

Each API instance generates a process-level `instance_id` (UUID). For each
enabled long-connection account (any platform), the instance attempts:

```
SET  im:gateway:{account_id}:owner  {instance_id}  NX  EX 30
```

- SETNX succeeds → this instance owns the connection, starts Gateway
- SETNX fails → another instance owns it, skip

A periodic sweep (every 15s) on each instance:

1. Accounts this instance owns → renew TTL
2. Unowned accounts (key expired / missing) → attempt SETNX
3. Accounts owned by others → skip
4. Accounts this instance owns but now disabled → release + disconnect

Failover: when an instance dies, its keys expire in 30s. The next sweep on
a surviving instance claims the orphaned accounts.

This mechanism lives in `im/runtime.py` and applies to all platforms
(Feishu long-connections included).

## Scope Key Mapping

The `scope_key` contract (opaque string in IMThreadLink unique index
`(account_id, channel_id, scope_key)`) maps Discord concepts as follows:

| Scenario | channel_id | scope_key | scope_kind | reply_to_id |
|----------|-----------|-----------|-----------|-------------|
| DM | DM channel snowflake | `"dm"` | `dm` | None |
| Guild @mention | channel snowflake | `"ch"` | `channel` | inbound message_id |
| Thread | thread channel snowflake | `"t:{thread_id}"` | `thread` | inbound message_id |

In Discord, a thread IS a channel (has its own snowflake). `channel_id`
stores the thread's own channel ID (what discord.py delivers as
`message.channel.id`), not the parent's. Replies post to this channel_id
so they land inside the thread. The parent channel ID is not stored — it's
only used transiently during `parse_inbound` to detect thread-vs-channel
context.

Design rationale: both hermes-agent and openclaw treat the same channel as
a shared conversation (all users see the same context). This matches
Discord's public-channel UX — unlike Feishu group chats where per-user
isolation (`"u:{union_id}"`) is appropriate because @bot messages are
contextually private.

Guild channel scope_key uses the helper `make_channel_scope()` → `"ch"`
(new in `im/types.py`, alongside `make_thread_scope` and
`make_participant_scope`). The `channel_id` column already distinguishes
channels, so the scope_key only differentiates session types within the
same channel (regular vs thread).

## Identity Resolution

v1 skips the identity gate. All Discord messages execute agent runs as
`account.acting_user_id` (the cubeplex user who connected the bot). This is
the existing fallback behavior in `identity.py` when no `IMIdentityLink`
exists and no `identity_resolver` is provided.

The ingest call passes `identity_resolver=None, rejection_notifier=None`.

`sender_ref` is set to the Discord user ID (snowflake) — globally unique
and stable. `sender_open_id` is also the user ID (Discord doesn't have a
separate app-scoped ID like Feishu's open_id).

## Inbound Flow

```
Discord Gateway on_message
  → filter: ignore self, ignore bots, ignore non-text, require @mention in guilds
  → DiscordConnector.parse_inbound(message) → InboundEvent
  → ingest_inbound_event(event, account, session_maker,
      identity_resolver=None, rejection_notifier=None)
  → atomic transaction: Receipt + ThreadLink + Conversation + RunQueueItem
  → IMRunQueueWorker claims item (FOR UPDATE SKIP LOCKED)
  → RunManager.start_run()
  → on_run_started → registry.get_platform("discord").build_tailer(...)
  → asyncio.create_task(tailer.run())
```

Idempotency: Discord message snowflake ID serves as `platform_event_id`.
Gateway RESUME replays are caught by the `IMWebhookReceipt` unique index.

## Outbound Rendering

Discord doesn't have CardKit. The outbound path uses plain message
editing — send one message, edit it as streaming content arrives.

### OpDispatcher Protocol

The current `OutboundRunTailer._dispatch_op` hard-codes CardKit calls.
Extract an `OpDispatcher` protocol:

```python
class OpDispatcher(Protocol):
    async def dispatch_create(self, state) -> bool: ...
    async def dispatch_stream(self, state, text) -> bool: ...
    async def dispatch_patch(self, state) -> bool: ...
    async def dispatch_finalize(self, state) -> bool: ...
    async def emergency_text(self, text) -> None: ...
```

`FeishuOpDispatcher` wraps the existing CardKit logic (extracted from
`_dispatch_op`). `DiscordOpDispatcher` implements Discord message
editing.

The `OutboundRunTailer` keeps its `run()` loop unchanged; only the
dispatch exit point is injected.

Note: `fold_event()` currently imports Feishu-specific types (`CardState`,
`ToolStep`, `ArtifactItem`, `PendingInput`) directly. As part of this
work, these accumulation models need to become platform-neutral (moved out
of `im/feishu/` into `im/`) or the fold logic needs to be parameterized
per platform. The simplest path: lift `CardState` and its nested types
into `im/card_model.py` as a shared accumulation model — both Feishu and
Discord read from it, but only the dispatcher decides how to render it.
This is a critical prerequisite for the Discord outbound path, not an
optional cleanup.

### Discord Rendering Details

**dispatch_create**: Send the first message with current accumulated text.
Record `bot_message_id`. Add ⏳ reaction on the user's inbound message.

**dispatch_stream**: Edit `bot_message_id` with the latest text for the
current message segment. Discord's 2000-char limit requires splitting
across multiple messages. The Discord render state tracks a
`sent_char_offset` — the number of characters already finalized in
previous messages. Each `dispatch_stream` call edits the current message
with `streaming_content[sent_char_offset:]` (the unsent portion only).
When the unsent portion approaches 1900 chars, the current message is
finalized, `sent_char_offset` advances, and a new message starts.
Split at line boundaries when possible.

Discord's edit rate limit (5 edits per 5 seconds per channel) requires a
longer stream interval than Feishu. `DiscordOpDispatcher` uses
`stream_interval=1.2s` (well under the 5/5s ceiling). The existing
`note_flood_strike` mechanism handles 429 responses — after 3 consecutive
rate-limit hits, progressive edits are disabled and the full answer lands
on `dispatch_finalize`.

**dispatch_patch**: During tool calls, maintain typing indicator
(`channel.typing()`). Tool steps are not rendered (unlike Feishu's
collapsible panel). The typing indicator signals "still working".

**dispatch_finalize**: Final edit with complete content. Append artifact
share links if any. Remove ⏳ reaction. On error, append error text.

**AskUser / SandboxConfirm**: Send a new message with `discord.ui.View`
containing Button components. Button clicks arrive via Gateway's
`on_interaction` event → route to `resume.py` via the platform registry.
Multi-select and free-form questions fall back to web client (same as
Feishu).

### RenderState

Discord uses a lighter render state than Feishu — no card_id,
card_unavailable, streaming_mode sequence, or CardState. Key fields:

- `bot_message_id`: the message currently being edited
- `sent_char_offset`: characters already finalized in previous messages
- `reaction_id`: processing indicator on the user's message
- `button_message_id`: the AskUser/SandboxConfirm button message

This can be a Discord-specific dataclass alongside the existing
`RenderState`, or a subclass — decided at implementation time.

## Gateway Lifecycle

**`im/discord/gateway.py`** manages one `discord.py Bot` per account:

- `start(account)`: decrypt bot_token from credential vault, create
  `commands.Bot(intents=...)`, register event handlers, start in
  background task
- `stop(account_id)`: `await bot.close()`, cleanup references
- Event handlers:
  - `on_message` → parse_inbound → ingest (gated on `bot.user` being set;
    messages received before `on_ready` are dropped)
  - `on_interaction` → route by interaction type: `application_command` →
    slash command handler; `component` (button) → resume path
  - `on_ready` → sync slash commands, record bot user ID

Connection status reported via `account.config["runtime_status"]`
(same pattern as Feishu), polled by frontend.

## Slash Commands

Two commands registered via `discord.py`'s `app_commands`:

- `/new` — create a new conversation in the current channel (deletes the
  existing IMThreadLink for this scope, so the next message creates a
  fresh one)
- `/reset` — alias for `/new`

Registered per-guild via `bot.tree.sync()` on `on_ready`.

## Frontend

### Platform Descriptor

New file `platforms/discord.ts` following the same `PlatformDescriptor`
shape as `feishu.ts`:

**Prerequisites checklist:**
1. Create application at discord.com/developers/applications
2. Add Bot, copy token
3. Enable MESSAGE_CONTENT privileged intent + standard gateway intents
4. Invite bot to server with appropriate permissions

**Credential fields:**
- `bot_token` (password) — required
- `application_id` (text) — required

**Steps:** prereqs → credentials → verify (same step components as Feishu)

### Type Changes

- `PlatformDescriptor.id`: add `'discord'` to the union
- `buildPayload` return type: generalize from `ConnectFeishuAccountIn` to
  a union type or generic `ConnectIMAccountIn`
- `@cubeplex/core`: add `ConnectDiscordAccountIn` schema

### Backend Connect Route

`POST /ws/{ws}/im/accounts` dispatches by `platform` field to the
appropriate connect method:

- `connect_discord()` in `IMConnectorService`:
  1. Validate bot_token by calling Discord API `GET /users/@me`
  2. Record bot username and avatar from response
  3. Store credential (bot_token, application_id) in vault
  4. Create `IMConnectorAccount(platform="discord", delivery_mode="gateway", ...)`
  5. Trigger gateway startup via `on_account_enabled`

### No Changes Needed

- `ImAccountListItem`, `ImAccountDetailPanel`, `ImAccountStatusPill` —
  already platform-agnostic
- `PlatformLogo` — add Discord logo asset
- Enable/disable/delete routes — already platform-agnostic

## Dependencies

**Backend:** `discord.py` (via `uv add discord.py`)

**Frontend:** no new dependencies

**Configuration:** no new environment variables. Bot token and application
ID are stored in the credential vault at connect time.

## File Change Summary

### New Files

| File | Purpose |
|------|---------|
| `im/registry.py` | PlatformConnector protocol + registry |
| `im/card_model.py` | Shared accumulation models (CardState, ToolStep, etc.) lifted from feishu |
| `im/discord/__init__.py` | Register DiscordPlatform |
| `im/discord/connector.py` | parse_inbound, send/edit message, reactions |
| `im/discord/gateway.py` | discord.py Bot lifecycle |
| `im/discord/renderer.py` | DiscordOpDispatcher + render state |
| `im/discord/interactions.py` | Button interaction handling |
| `im/discord/commands.py` | /new /reset slash commands |
| `frontend/.../platforms/discord.ts` | Platform descriptor |
| `@cubeplex/core` schema | ConnectDiscordAccountIn |

### Modified Files

| File | Change |
|------|--------|
| `im/runtime.py` | Registry-based startup + distributed lease |
| `im/outbound.py` | Extract OpDispatcher protocol |
| `im/types.py` | Add `make_channel_scope()` helper |
| `im/feishu/card_model.py` | Re-export from shared `im/card_model.py` |
| `api/routes/v1/ws_im.py` | Dispatch connect by platform |
| `api/schemas/im_connector.py` | Add ConnectDiscordAccountIn, extend delivery_mode pattern |
| `services/im_connector.py` | Add connect_discord() |
| `frontend/.../platforms/types.ts` | Extend PlatformDescriptor.id, generalize buildPayload type |

### Unchanged

- `im/inbound.py`, `im/worker.py`, `im/identity.py`, `im/resume.py`
- DB models (`IMConnectorAccount`, `IMThreadLink`, etc.)
- Frontend generic components (`ImAccountListItem`, etc.)
