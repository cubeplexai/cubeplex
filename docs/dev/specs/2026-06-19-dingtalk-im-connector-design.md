# DingTalk IM Connector

Adds DingTalk (钉钉) as the fourth IM platform in cubebox, following the
existing Feishu, Slack, and Discord connectors. The existing
connector-neutral pipeline (inbound transaction, queue worker, identity
resolution, resume) is fully reused; DingTalk-specific logic lives in a
new `im/dingtalk/` module that registers with the platform registry.

## Scope

**In scope (v1):**

- Stream mode gateway (WebSocket long-connection via `dingtalk-stream` SDK)
- Enterprise internal bot only (AppKey + AppSecret)
- Receive messages: single chat (DM) + group @mention
- Outbound: interactive card with streaming updates + markdown content
- AskUser / SandboxConfirm via interactive card action buttons
- Identity resolution: email auto-match + manual link fallback
- Artifact share links (appended to final card)
- Frontend connect wizard (platform descriptor)

**Out of scope:**

- ISV / third-party enterprise bots (SuiteKey + OAuth授权)
- Custom webhook-only bots (no interaction capability)
- DingTalk work notifications (非会话推送)
- Group topic/thread support (DingTalk groups are flat)
- Cool App (酷应用) / Mini Program (小程序) embedding
- Voice / video messages

## Connection: Stream Mode Only

DingTalk Stream is a WebSocket long-connection that pushes events to the
bot server — no public endpoint required. This matches the Slack
(Socket Mode) and Feishu (long-connection) pattern.

The `dingtalk-stream` Python SDK handles the WebSocket lifecycle:
connection, heartbeat, reconnect, and event routing. The bot registers
callback handlers for chat messages and interactive card actions.

`delivery_mode` is always `"stream"` for DingTalk accounts.

Credential: AppKey + AppSecret, stored in the credential vault at
connect time. Access token management is handled by the stream SDK
internally.

## Scope Key Mapping

DingTalk concepts mapped to `IMThreadLink(account_id, channel_id, scope_key)`:

| Scenario | channel_id | scope_key | reply_to_id |
|----------|-----------|-----------|-------------|
| Single chat (DM) | `conversationId` | `"dm"` | `msgId` |
| Group @mention | `conversationId` | `"u:{staffId}"` | `msgId` |

DingTalk group chats are flat (no threads). Group conversations use
per-user scope (`"u:{staffId}"`) for isolation — the same user talking
to the bot in the same group shares one cubebox conversation, but
different users get their own. This matches Feishu's group-chat model.

`sender_ref` is the DingTalk `staffId` (stable within the enterprise).
`sender_open_id` is also `staffId`.

## Identity Resolution

DingTalk connector implements both the `IdentityResolver` and
`RejectionNotifier` protocols.

**Auto-match (primary):** On first message from an unlinked user, call
`GET /topapi/v2/user/get?userid={staffId}` to retrieve the user's
enterprise email. If the email matches a cubebox user, create an
`IMIdentityLink` automatically.

**Manual link (fallback):** If email lookup fails or no match is found,
send a rejection card to the user with a link URL. The URL is a JWT
identity-link token (same mechanism as Slack/Feishu — `im/link.py`).
The user clicks the link, logs in to cubebox, and the identity binding
completes.

**Rejection:** Unlinked users receive a card with the link prompt. The
rejection card includes a brief explanation and a clickable URL button.

## Inbound Flow

```
DingTalk Stream on_message callback
  → filter: ignore bot self, ignore non-text
  → DingtalkConnector.parse_inbound(raw) → InboundEvent
  → ingest_inbound_event(event, account, session_maker,
      identity_resolver=DingtalkConnector,
      rejection_notifier=DingtalkConnector)
  → atomic transaction: Receipt + ThreadLink + Conversation + RunQueueItem
  → IMRunQueueWorker claims item (FOR UPDATE SKIP LOCKED)
  → RunManager.start_run()
  → on_run_started → registry.get_platform("dingtalk").build_tailer(...)
  → asyncio.create_task(tailer.run())
```

Idempotency: DingTalk `msgId` serves as `platform_event_id`. Stream
reconnect replays are caught by the `IMWebhookReceipt` unique index.

The `parse_inbound` method strips bot @mention text
(`@BotName` prefix) from the message before passing to the agent.

## Outbound Rendering: Interactive Cards

DingTalk interactive cards support markdown content, action buttons, and
streaming updates — a close analogue to Feishu CardKit.

### Card Lifecycle

1. **Register card template** — on gateway start, register a reusable
   card template via `POST /v1.0/card/templates`. The template defines
   the card layout: a markdown body variable, an optional button group,
   and a status indicator.

2. **dispatch_create** — create a card instance
   (`POST /v1.0/card/instances/createAndDeliver`), binding the template
   to the target conversation. The card body starts with a "thinking..."
   placeholder. Store the `outTrackId` as the card identifier.

3. **dispatch_stream** — update the card body via streaming update
   (`PUT /v1.0/card/streaming`). The `key` field identifies which
   variable to update; `content` carries the incremental markdown. The
   card auto-appends content as it arrives. Stream interval: ~1.0s
   (DingTalk rate limits streaming updates to roughly 1/s per card).

4. **dispatch_patch** — when AskUser / SandboxConfirm fires, update
   the card to append action buttons. Each button carries a callback
   payload with `{run_id, question_id, answer_key, value}`.

5. **dispatch_finalize** — final card update with complete content.
   Replace the "thinking" status indicator with success/error. Append
   artifact share links if any. After finalize, mark the card as
   immutable (disable further button clicks on resolved questions).

6. **emergency_text** — fallback plain markdown message (not a card)
   when card creation fails.

### Card Template Design

One shared template per bot account, registered at gateway start:

- **body**: markdown variable `${content}` — receives streaming text
- **status**: text variable `${status}` — "thinking" / "done" / "error"
- **buttons**: dynamic button group `${actions}` — populated only during
  AskUser/SandboxConfirm

### Card Action Callbacks

Button clicks arrive via the Stream connection's card callback handler.
The callback payload includes the `outTrackId` and the button's
`action` data. Route to `resume_paused_run` via the standard
`im/resume.py` path.

Action ID format: `im:dingtalk:{run_id}:{short_qid}:{akey}:{value}`
(same convention as Slack/Feishu).

## Gateway Lifecycle

**`im/dingtalk/gateway.py`** manages one DingTalk Stream client per
account:

- `start(account)`: decrypt AppKey + AppSecret from credential vault,
  create `dingtalk_stream.DingTalkStreamClient`, register callback handlers
  (chat message + card action), connect. Spawned as background task.
- `stop()`: disconnect the stream client, cancel the background task.
- `is_open()`: check if the stream connection is alive.

Event handlers:

- Chat message callback → `DingtalkConnector.parse_inbound(raw)` →
  `ingest_inbound_event(...)`
- Card action callback → route by action payload → `resume_paused_run`

Connection status reported via `account.config["runtime_status"]`
(same pattern as other platforms), polled by frontend.

### Access Token

The `dingtalk-stream` SDK manages access tokens internally. For outbound
API calls that need a token (user info lookup, card operations), the
connector uses the `get_access_token()` method provided by the SDK's
client instance, or falls back to
`POST /v1.0/oauth2/accessToken` with AppKey + AppSecret.

## Link Command

DingTalk bots don't have a built-in slash-command system like Slack.
Instead, the bot recognizes the keyword `link` (case-insensitive) as a
trigger. When a user sends "link" in a chat with the bot:

1. Generate a JWT identity-link token (reusing `im/link.py`)
2. Reply with a card containing the link URL as a clickable button
3. The URL points to `{FRONTEND_BASE_URL}/im/link?token={jwt}`

This is the same mechanism as the Slack `/link` command, just triggered
by keyword instead of slash command.

## Frontend

### Platform Descriptor

New file `platforms/dingtalk.ts` following the `PlatformDescriptor`
shape:

**Prerequisites checklist:**
1. Create an enterprise internal bot in the DingTalk Developer Console
   (open.dingtalk.com)
2. Enable the "Stream Mode" option in the bot settings
3. Copy the AppKey and AppSecret
4. Grant the bot the necessary permissions: message receiving, user
   info read, interactive card

**Credential fields:**
- `app_key` (text) — required
- `app_secret` (password) — required

**Steps:** prereqs → credentials → verify (same step components as
other platforms)

### Type Changes

- `PlatformDescriptor.id`: add `'dingtalk'` to the union
- `@cubebox/core`: add `ConnectDingtalkAccountIn` schema

### Backend Connect Route

`POST /ws/{ws}/im/accounts` dispatches `platform="dingtalk"` to
`connect_dingtalk()` in `IMConnectorService`:

1. Validate credentials by calling `POST /v1.0/oauth2/accessToken`
   with AppKey + AppSecret
2. Call `POST /v1.0/oauth2/userAccessToken` or bot info API to get
   the bot's name/avatar for display
3. Store credential (app_key, app_secret) in vault
4. Create `IMConnectorAccount(platform="dingtalk",
   delivery_mode="stream", external_account_id=app_key, ...)`
5. Trigger gateway startup via `on_account_enabled`

### No Changes Needed

- `ImAccountListItem`, `ImAccountDetailPanel`, `ImAccountStatusPill` —
  already platform-agnostic
- `PlatformLogo` — add DingTalk logo asset
- Enable/disable/delete routes — already platform-agnostic

## Dependencies

**Backend:** `dingtalk-stream` (via `uv add dingtalk-stream`) — the
official DingTalk Stream SDK. Also `alibabacloud-dingtalk-oauth2` and
`alibabacloud-dingtalk-im` for REST API calls (card operations, user
info).

Evaluate at implementation time whether the `dingtalk-stream` SDK's
built-in API client covers all needed calls. If so, skip the separate
`alibabacloud-dingtalk-*` packages and use raw httpx against the
DingTalk OpenAPI v2 endpoints directly — fewer deps, simpler.

**Frontend:** no new dependencies.

**Configuration:** no new environment variables. AppKey and AppSecret
are stored in the credential vault at connect time. The existing
`CUBEBOX_FRONTEND_BASE_URL` env var is used for identity-link URL
generation (already configured for other IM connectors).

## File Change Summary

### New Files

| File | Purpose |
|------|---------|
| `im/dingtalk/__init__.py` | Register DingtalkPlatform |
| `im/dingtalk/_platform.py` | DingtalkPlatform: PlatformConnector implementation |
| `im/dingtalk/connector.py` | parse_inbound, outbound card/message API, identity resolution |
| `im/dingtalk/gateway.py` | DingTalk Stream lifecycle |
| `im/dingtalk/renderer.py` | DingtalkOpDispatcher: interactive card rendering |
| `im/dingtalk/interactions.py` | Card action callback handling |
| `frontend/.../platforms/dingtalk.ts` | Platform descriptor |
| `@cubebox/core` schema | ConnectDingtalkAccountIn |

### Modified Files

| File | Change |
|------|--------|
| `im/runtime.py` | Add `import cubebox.im.dingtalk` |
| `api/schemas/im_connector.py` | Add ConnectDingtalkAccountIn, extend discriminated union |
| `services/im_connector.py` | Add `connect_dingtalk()` |
| `api/routes/v1/ws_im.py` | Dispatch connect by platform for dingtalk |
| `frontend/.../platforms/types.ts` | Extend `PlatformDescriptor.id` union |

### Unchanged

- `im/inbound.py`, `im/worker.py`, `im/identity.py`, `im/resume.py`,
  `im/outbound.py`, `im/card_model.py`
- DB models (`IMConnectorAccount`, `IMThreadLink`, etc.)
- Frontend generic IM components
