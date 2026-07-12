# Microsoft Teams IM Connector Design

Status: approved
Date: 2026-06-19

## Overview

Add Microsoft Teams as the fourth IM connector platform in cubeplex,
alongside Slack, Discord, and Feishu. Follows the existing
`PlatformConnector` registry pattern. Messages arrive via webhook
(HTTP POST from Azure Bot Service), rendering uses Markdown for
streaming text and Adaptive Cards for interactive elements (AskUser /
SandboxConfirm).

## SDK & Delivery Mode

- Python SDK: `microsoft-teams-apps` (same as hermes-agent).
- Delivery mode: `"webhook"`. Azure Bot Service POSTs activities to
  cubeplex. No WebSocket gateway needed.
- One `microsoft_teams.apps.App` instance per enabled
  `IMConnectorAccount`, cached in memory. The App instance handles JWT
  validation and provides the send/update API.

## Webhook Ingress

New FastAPI route: `POST /api/v1/im/teams/messages`
(in `backend/cubeplex/api/routes/v1/im_ingress.py`, alongside the
existing Feishu ingress).

Flow:
1. Receive HTTP POST from Azure Bot Service.
2. Convert Starlette request to the SDK's `HttpRequest` format.
3. Route to the correct `App` instance by matching
   `activity.recipient.id` (Bot ID) against cached accounts.
4. SDK validates the Azure JWT Bearer token.
5. `on_message` handler: parse activity → build `InboundEvent` →
   call `ingest_inbound_event()`.
6. `on_card_action` handler: parse Adaptive Card submit data →
   call `resume_paused_run()`.

### App Instance Lifecycle

- `on_account_enabled()`: create `App` instance with credentials from
  the vault, register `on_message` + `on_card_action` handlers, cache
  by `external_account_id`.
- `on_account_disabled()`: remove from cache.
- Webhook-mode accounts are not "connected" at startup the way
  gateway-mode accounts are, but the App instance must be initialized
  so the ingress route can dispatch incoming activities.

## Scope Key Mapping

| Teams scenario | `conversationType` | `channel_id` | `scope_key` | `scope_kind` |
|---|---|---|---|---|
| Personal chat (1:1) | `personal` | conversation.id | `"dm"` | `dm` |
| Group chat @mention | `groupChat` | conversation.id | `"u:{aad_object_id}"` | `group` |
| Channel @mention | `channel` | conversation.id | `"u:{aad_object_id}"` | `channel` |
| Channel thread reply | `channel` + replyToId | conversation.id | `"u:{aad_object_id}\|t:{replyToId}"` | `thread` |

- Personal chats: process unconditionally.
- Group chats / channels: respond only when the bot is @mentioned.
- `sender_open_id` and `sender_ref`: AAD Object ID
  (`activity.from.aadObjectId`).
- `external_account_id`: Bot's App ID.
- Strip `<at>BotName</at>` mention tags from message text before
  passing to the agent.

## Identity Resolution

Same auto-resolution flow as Slack:

1. Cache hit in `im_identity_links` by `(account_id, aad_object_id)`.
   Re-verify workspace membership on every hit.
2. Microsoft Graph API `GET /users/{aad_object_id}` to get `mail` or
   `userPrincipalName`.
3. Case-insensitive email match against cubeplex `User.email`.
4. Insert `IMIdentityLink` cache row.
5. Fallback: send rejection notice telling the user to use `/link`.

### Graph API Authentication

OAuth2 client credentials flow with the Bot's App ID + App Secret.
Scope: `https://graph.microsoft.com/.default`.
Requires `User.Read.All` application permission with admin consent on
the Azure AD App Registration.

### /link Command

Teams has no native slash command registration. The bot recognizes
message text starting with `/link` or `link` as a command trigger.
Signs a JWT via the existing `im/link.py` module and returns a
confirmation URL. The rest of the flow (frontend `/im-link` page,
`POST /api/v1/im/link/confirm`) is fully shared.

## Message Rendering (TeamsOpDispatcher)

Hybrid mode: Markdown streaming + Adaptive Card interactions.

### Streaming Text

- `dispatch_create`: `App.send()` first Markdown message, record
  `bot_message_id` (activity ID). Send typing indicator first.
- `dispatch_stream`: `App.update_activity()` to edit the message.
  Split threshold: 25000 chars (Teams limit ~28KB; practically no
  splitting needed). `stream_interval = 1.5s` (Teams rate limits are
  stricter than Slack/Discord).
- `dispatch_finalize`: final edit with complete content + artifact
  share links.

### Markdown Format

Teams supports a standard Markdown subset (bold, italic, code, links,
lists, tables). Closer to standard than Slack mrkdwn, so minimal
conversion is needed. `format.py` handles:

- Strip unsupported syntax (`~~strikethrough~~`).
- Standard `[text](url)` links work as-is (no conversion needed
  unlike Slack's `<url|text>`).

### Interactive Elements

- `dispatch_patch`: AskUser / SandboxConfirm rendered as Adaptive
  Card v1.4 with `Action.Submit` buttons. Submit data payload carries
  `{"action": "im:{kind}:{run_id}:{short_qid}:{akey}:{value}"}`,
  consistent with Slack/Discord action_id convention.
- Button callback: `on_card_action` handler parses the action string,
  resolves `question_id` via `resolve_full_question_id()`, calls
  `resume_paused_run()`. Returns an updated Adaptive Card showing
  the result.

### Processing Indicators

- Typing indicator via `TypingActivity` on message receipt.
- No emoji reactions (Teams reaction API is less ergonomic than
  Slack/Discord; typing indicator is sufficient).

## Configuration & Credentials

### Credential Fields

| Field | Description |
|---|---|
| `app_id` | Azure Bot Registration App ID |
| `app_secret` | Azure AD Client Secret |
| `tenant_id` | Azure AD Tenant ID |

Stored in credential vault: `kind="im_bot"`, `name="teams:{app_id}"`.
Non-sensitive metadata (bot name, tenant_id) stored in
`IMConnectorAccount.config` JSON.

### Connect Flow (IMConnectorService.connect_teams)

1. Validate credentials: OAuth2 client credentials token request
   against `https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token`.
2. Verify bot registration is functional.
3. Write credential to vault.
4. Create `IMConnectorAccount(platform="teams",
   external_account_id=app_id, delivery_mode="webhook")`.
5. Initialize App instance in cache.

### API Schema

New: `ConnectTeamsAccountIn(platform="teams", app_id, app_secret,
tenant_id)`. Added to the `ConnectIMAccountIn` discriminated union.

## Frontend Connect Wizard

Activate `teams.stub.ts` → full `teams.ts` `PlatformDescriptor`:

- **Prerequisites**:
  1. Create Bot Registration in Azure Portal (or via Teams CLI).
  2. Add `User.Read.All` application permission + admin consent.
  3. Create Client Secret.
  4. Set Messaging Endpoint to
     `https://<your-domain>/api/v1/im/teams/messages`.
- **Credential fields**: `app_id`, `app_secret`, `tenant_id`.
- **Steps**: 3-step wizard (Prerequisites → Credentials → Verify),
  matching Slack/Discord pattern.

## Runtime Integration

### im/runtime.py Changes

- Import `cubeplex.im.teams` at startup to trigger
  `register_platform("teams", TeamsPlatform())`.
- Webhook-mode accounts do not need startup connection (same as Feishu
  webhook), but App instances must be initialized so the ingress route
  can dispatch.

### File Structure

```
backend/cubeplex/im/teams/
├── __init__.py          # register_platform("teams", TeamsPlatform())
├── _platform.py         # TeamsPlatform(PlatformConnector)
├── connector.py         # TeamsConnector: parse_inbound, send_message, resolve_email
├── gateway.py           # TeamsAppManager: App instance lifecycle & cache
├── renderer.py          # TeamsOpDispatcher(OpDispatcher)
├── interactions.py      # handle_card_action → resume_paused_run
├── commands.py          # /link text command recognition
└── format.py            # Markdown cleanup for Teams
```

## Out of Scope

- Teams meeting integration (transcripts, recordings, summaries).
- Message Extensions / Compose Extensions.
- Multi-tenant bot (v1 is single-tenant per account).
- Group chat shared conversations (deferred to v2, same as other
  platforms).
- File upload / download from Teams.
- Tab or Task Module integrations.
