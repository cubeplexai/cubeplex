# WeCom IM Connector

## Goal

Add WeCom (Enterprise WeChat) as a CubePlex IM platform so workspace members can talk to
their CubePlex agent from WeCom direct messages and group chats without exposing a public
webhook endpoint.

## Context

CubePlex already supports Feishu/Lark, Slack, Discord, DingTalk, and Microsoft Teams through
one platform-neutral inbound queue and outbound run tailer. WeCom AI Bots expose a persistent
WebSocket transport that fits the same runtime model: CubePlex opens one outbound connection
per configured bot, receives messages, starts the normal workspace-scoped agent run, and sends
the result back over that connection.

Two existing implementations establish the protocol behavior used by this design:

- Tencent's official `WecomTeam/wecom-openclaw-plugin`, backed by
  `@wecom/aibot-node-sdk`.
- The local `~/hermes-agent` WeCom adapter, which implements the same protocol in Python and
  documents the edge cases around reply acknowledgements, reconnects, and expired streams.

The issue asks for WeCom support but does not define a delivery mode or feature-parity target.
This design deliberately ships a useful text-first connector before adding the separate media
and callback-app concerns.

## Approaches considered

### 1. WeCom AI Bot over WebSocket — chosen

Store a Bot ID and Secret, subscribe to `wss://openws.work.weixin.qq.com`, and use
`aibot_msg_callback` plus `aibot_respond_msg` for inbound and streaming replies.

This matches CubePlex's existing gateway lifecycle, works behind a firewall, supports direct
and group chats, and needs only two credentials. The protocol has no official Python SDK, but
it is small enough to implement directly with the already-installed `aiohttp` package and can
be checked against Tencent's Node SDK and the Hermes Python implementation.

### 2. WeCom self-built app callbacks

Receive encrypted XML callbacks and send replies through the enterprise application API. This
makes the bot appear as a first-class WeCom application, but requires Corp ID, Agent ID, Corp
Secret, callback Token, EncodingAESKey, and a public HTTPS endpoint. It also has a different
message and security model and does not provide the same native reply stream.

This is a separate connector mode, not a small variation of the WebSocket bot, so it is out of
scope for this change.

### 3. Ship both modes and media parity together

This would provide the broadest coverage, but it combines three independent concerns:
WebSocket bot transport, encrypted callback transport, and bidirectional file transfer. It
would be difficult to review and would violate the repository's one-concern-per-PR rule.

## Design

### Account and API contract

The existing workspace-scoped endpoint accepts one additional discriminated payload:

```http
POST /api/v1/ws/{workspace_id}/im/accounts
Content-Type: application/json

{
  "platform": "wecom",
  "bot_id": "aib...",
  "secret": "...",
  "acting_user_id": "self"
}
```

`ConnectWecomAccountIn` requires non-empty `bot_id` and `secret`; `acting_user_id` keeps the
same `self` default and impersonation rules as every other platform. The account is stored as:

- `platform = "wecom"`
- `external_account_id = bot_id`
- `delivery_mode = "stream"`
- encrypted credential JSON containing `bot_id`, `secret`, and `bot_open_id = bot_id`

Before storing credentials, the service rejects an existing `(platform, external_account_id)`
pair and performs a short WebSocket subscribe handshake. An authentication error rejects the
request without creating either a credential or an account. After the transaction succeeds,
the existing `im_connect_account` hook starts the persistent gateway immediately.

The Bot ID is used as the displayed account identifier because the subscribe protocol does
not provide a supported bot-profile lookup. No database migration is needed: the platform and
delivery-mode columns are already strings, and `wecom` fits their current limits.

Account list, admin list, disable, enable, delete, bot settings, identity-link list, runtime
status, and channel routing routes remain shared with the other platforms. There is no new
org-admin route.

### WebSocket gateway

Each enabled WeCom account owns one `WecomGateway`, registered through the existing
`PlatformConnector` registry. The existing Redis account lease ensures that only one CubePlex
API instance opens the connection.

Connection startup does the following:

1. Open `wss://openws.work.weixin.qq.com` with a 20-second timeout.
2. Send `aibot_subscribe` with a fresh request ID and a body containing `bot_id`, `secret`, and
   a per-gateway `device_id`.
3. Wait for the matching acknowledgement and reject any non-zero `errcode`.
4. Start a read loop and send an application-level `ping` every 30 seconds.

Unexpected connection closure fails outstanding requests, closes the socket, and reconnects
with bounded backoff of 2, 5, 10, 30, then 60 seconds. A server
`aibot_event_callback/disconnected_event` means another process connected with the same bot;
the gateway stops reconnecting instead of creating a mutual-kick loop. Disable, delete, and
application shutdown cancel the read, heartbeat, and reconnect tasks and resolve pending
waiters before returning.

`is_open()` returns true only while an authenticated socket and live read task exist. The
existing runtime aggregation therefore reports `connected`, `disconnected`, or
`never_connected` without a WeCom-specific status model.

The WebSocket URL is a code constant in v1. It is not accepted from the API or stored in the
credential, which prevents a workspace member from turning the gateway into an arbitrary
outbound WebSocket client.

### Inbound messages and conversation scope

The gateway accepts `aibot_msg_callback` and the legacy `aibot_callback` command. It ignores
bot echoes, malformed callbacks, unsupported message types, and callbacks without a stable
sender or message ID.

V1 extracts user-visible text from:

- `msgtype = "text"`
- text entries inside `msgtype = "mixed"`
- WeCom-provided voice transcription in `voice.content`
- quoted text or quoted voice content when the new message contains no other text

Images, files, video, and other media are not downloaded in this change. A media-only callback
is ignored. Native media support will use the existing `InboundAttachmentRef` pipeline in a
follow-up.

The normalized event mapping is:

| WeCom context | `channel_id` | `scope_key` | `scope_kind` | `reply_to_id` |
|---|---|---|---|---|
| Direct message | `chatid`, falling back to sender `userid` | `dm` | `dm` | WebSocket `req_id` |
| Group, isolated routing | `chatid` | `u:{userid}` | `group` | WebSocket `req_id` |
| Group, shared routing | `chatid` | `ch` | `channel` | WebSocket `req_id` |

`msgid` becomes both `platform_event_id` and `inbound_message_id`; it continues to protect
against replay through the existing receipt unique constraint. `sender_ref` and
`sender_open_id` are both WeCom's `from.userid`. A leading group mention is removed from the
text before command parsing or agent dispatch.

The load-bearing decision is storing the callback's WebSocket `req_id` in the existing
`reply_to_id` column. The run queue is asynchronous, so an in-memory `msgid -> req_id` cache
could disappear before the tailer starts. Persisting it with the queue item preserves the
passive-reply capability across normal process boundaries without adding a WeCom-specific
column. The original WeCom `msgid` remains available separately as `inbound_message_id`.

The gateway reloads account-level routing settings for each message through
`lookup_binding_mode`, then delegates to the existing `ingest_inbound_event` transaction. No
changes are made to conversation resolution, queue claiming, or run startup.

### Identity and commands

The AI Bot callback exposes a WeCom user ID but no supported email lookup in this protocol.
The gateway therefore supplies `NullIdentityResolver` and a WeCom rejection notifier. An
unlinked sender receives the existing `/link <your-email>` instruction, and no agent run starts
until the identity is confirmed. Workspace membership is still rechecked on every message.

The gateway intercepts these text commands before the identity gate:

- `/link <email>`: sign the existing ten-minute identity token and passively reply with the
  confirmation URL.
- `/new` and `/reset` (plus the existing `新对话` alias): rotate the conversation binding and
  passively reply with the standard reset result.

Command replies use the current callback `req_id`; they do not depend on a later active-send
lookup.

### Outbound protocol and rendering

`WecomConnector` exposes the shared connector methods while `WecomOpDispatcher` turns the
platform-neutral `RenderState` into WeCom stream frames.

For a run started by an inbound WeCom message:

1. `dispatch_create` creates a random stream ID and sends a thinking placeholder through
   `aibot_respond_msg` with `finish = false`, which opens WeCom's typing bubble.
2. `dispatch_stream` sends the full cumulative text, not the latest delta, with the same
   stream ID. Intermediate frames are sent at the existing tailer's throttled cadence.
3. Only one frame may await acknowledgement for a given inbound `req_id`. If an intermediate
   acknowledgement is still pending, a newer intermediate frame is skipped because the next
   cumulative frame contains all prior text.
4. `dispatch_finalize` first waits for the pending intermediate acknowledgement, then sends
   the complete visible text with `finish = true` and waits up to 15 seconds for its own
   acknowledgement.

The gateway registers an acknowledgement waiter before writing the frame so a fast response
cannot arrive before the waiter exists. Incoming non-callback frames with the same `req_id`
resolve that waiter. Final acknowledgement timeout is treated as delivered after the socket
write succeeds: retrying with an active send could duplicate a message that WeCom rendered
but acknowledged late.

WeCom errors `846604` and `846608` mean the passive reply or stream window expired. In that
case the dispatcher sends the complete final answer once with `aibot_send_msg`. A run older
than 330 seconds takes the same proactive-final path without attempting a likely-expired final
stream frame. Error `6000` on finalization is treated as delivered because a newer frame has
already replaced the same stream.

Runs created by a scheduled task or trigger have no inbound `req_id`. The dispatcher does not
attempt progressive streaming for them and sends the final result once with
`aibot_send_msg`. This preserves the existing IM-channel destination behavior.

Stream content is capped by UTF-8 byte length at WeCom's 20,480-byte limit. If a final answer
exceeds the limit, the visible stream ends with a truncation notice; the complete answer stays
in the durable CubePlex conversation. Proactive messages are split at readable boundaries to
fit WeCom's 4,000-character message limit.

V1 renders text, citations, errors, and artifact share links in the message body. It does not
upload native files. AskUser and sandbox-confirm requests show the existing text prompt and
tell the user to continue in the CubePlex web UI; this change does not add WeCom template-card
buttons.

### Frontend

The existing workspace IM connection wizard gains a live WeCom descriptor with:

- prerequisites explaining how to create a WeCom AI Bot and copy its Bot ID and Secret;
- a Bot ID text field and Secret password field;
- the existing prerequisites, credentials, and verify steps;
- a WeCom logo using the existing inline `PlatformLogo` pattern;
- English and Simplified Chinese strings.

The core client adds `ConnectWecomAccountIn` to `ConnectImAccountIn`. The wizard submits
`acting_user_id = "self"`, matching the other platforms. Invalid credentials are attached to
the Secret field; duplicate Bot IDs use the existing duplicate-account banner.

No new page, layout, component library, color token, or visual pattern is introduced.

### Documentation

The shipped documentation adds an English and Simplified Chinese WeCom setup page and updates
the IM overview platform table and per-platform links. The guide covers AI Bot creation,
credentials, WebSocket delivery, identity linking, group mentions, commands, and the v1 media
and interactive-input limits. Missing console screenshots use explicit placeholders.

## Security and failure handling

- Bot secrets are encrypted through the existing `im_bot` credential path and are never
  returned by list or detail APIs.
- Logs include account IDs, command names, request IDs, and error codes, but never the Bot
  Secret or full callback payload.
- Malformed JSON and unknown commands are logged and dropped without terminating the read
  loop.
- Duplicate inbound messages are handled by the existing database receipt constraint rather
  than an in-memory deduplication cache.
- Redis gateway leases prevent duplicate connections across CubePlex instances.
- The fixed WeCom endpoint and TLS defaults are used; the connector does not accept arbitrary
  transport URLs.
- A failed credential probe creates no durable rows. A later gateway disconnect leaves the
  account enabled but visibly disconnected so the lease owner can retry.

## Out of scope

- Personal WeChat / Weixin support.
- WeCom self-built application callback mode and encrypted XML callbacks.
- QR-code bot creation or credential acquisition.
- Inbound image, file, and video downloads.
- Native outbound image or file upload.
- WeCom template cards and in-chat AskUser or sandbox-confirm buttons.
- Per-user or per-group allowlists outside CubePlex's existing membership and routing controls.
- A configurable WebSocket endpoint.

## Success criteria

- A workspace member can bind a valid Bot ID and Secret; invalid credentials create no account
  or credential, and secrets never appear in API responses.
- Exactly one authenticated WeCom WebSocket connection runs per enabled account across API
  instances, and disable/delete/shutdown stops it promptly.
- A linked member's direct message creates or reuses the expected DM conversation and receives
  one streamed final answer.
- A linked member's group mention uses participant scope in isolated mode and channel scope in
  shared mode.
- An unlinked sender receives `/link` instructions and cannot start a run; a removed workspace
  member is rejected on their next message.
- Duplicate `msgid` delivery creates only one queue item and one run.
- Intermediate frames never overtake one another; finalization waits behind an outstanding
  frame and late final ACKs do not cause duplicate answers.
- Expired passive streams and automated runs without an inbound `req_id` deliver one proactive
  final message.
- `/link`, `/new`, and `/reset` work in direct messages and group mentions.
- The workspace wizard can create a WeCom account in English and Simplified Chinese, and the
  setup guide describes the shipped behavior and limits.
