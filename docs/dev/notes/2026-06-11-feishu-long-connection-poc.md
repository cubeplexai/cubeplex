# Feishu long-connection PoC findings

Date: 2026-06-11
Plan: `docs/dev/plans/2026-06-11-im-connectors-feishu.md` Task 0
App used: `moltbot` (`cli_a9f0a4c078a11bd3`) — CN domain (`open.feishu.cn`).
PoC script: `backend/scripts/dev/feishu_long_connection_poc.py` (deleted after this note lands; lived only to inform schema decisions).

## What the PoC verified

1. **`uv add lark-oapi`** installs cleanly (1.6.8 + transitive deps:
   `pycryptodome`, `requests-toolbelt`, `websockets`). `python -c "import
   lark_oapi"` succeeds in the backend venv.
2. **Bot identity hydration via `/open-apis/bot/v3/info`** returns the bot's
   own `open_id` and `app_name` using only the tenant access token (no extra
   scopes required). The exact payload we got:

       {
         "bot": {
           "activate_status": 2,
           "app_name": "moltbot",
           "avatar_url": "...",
           "ip_white_list": [],
           "open_id": "ou_b6aef8a8a515e3e6d8561ce41dfd5ec9"
         },
         "code": 0,
         "msg": "ok"
       }

   This is the hydration step plan Task 15 (`connect_feishu`) and the
   webhook ingress (Task 12) read once and store in the credential JSON.

3. **WebSocket connect via `lark_oapi.ws.Client.start()`** opens cleanly to
   `wss://msg-frontier.feishu.cn/ws/v2`. The SDK handles its own
   reconnection. (No inbound traffic was sent during the PoC window, so the
   event handler path was not exercised end-to-end — see "What was not
   verified" below.)

## SDK event-shape, captured from the typed model files

Authoritative source: `lark_oapi.api.im.v1.model.*`. Every field below is
deterministic from the code-generated model; we do not need to send a real
message to verify the shape.

`P2ImMessageReceiveV1` extends `EventContext` (the P2 envelope):

    EventContext:
      schema: str | None             # always "2.0" for p2 events
      header: EventHeader:
        event_id: str | None         # stable UUID — IDEMPOTENCY KEY
        token: str | None            # verification token (matches dashboard)
        create_time: str | None      # string, NOT int (surprise)
        event_type: str | None       # "im.message.receive_v1"
        tenant_key: str | None
        app_id: str | None
      event: P2ImMessageReceiveV1Data:
        sender: EventSender:
          sender_id: UserId:
            open_id: str | None      # APP-SCOPED — present by default
            union_id: str | None     # DEVELOPER-SCOPED — present by default, scope-free
            user_id: str | None      # TENANT-SCOPED — needs contact:user.employee_id:readonly
          sender_type: str | None    # "user" | "app" (bot's own)
          tenant_key: str | None
        message: EventMessage:
          message_id: str | None     # om_xxx
          root_id: str | None        # populated on replies; root of the reply chain
          parent_id: str | None      # direct parent in a quote-reply chain
          create_time: int | None
          update_time: int | None
          chat_id: str | None        # oc_xxx
          thread_id: str | None      # populated ONLY when message is in a 话题/topic
          chat_type: str | None      # "p2p" (DM) | "group"
          message_type: str | None   # "text" | "image" | "post" | "interactive" | ...
          content: str | None        # JSON STRING: e.g. '{"text": "<at user_id=\"ou_x\">Bot</at> hello"}'
          mentions: list[MentionEvent]:
            key: str | None          # placeholder like "@_user_1"
            id: UserId               # nested open_id / union_id / user_id
            mentioned_type: str | None
            name: str | None
            tenant_key: str | None

## How the SDK delivers it on the long-connection path

`lark.JSON.marshal(data)` of `P2ImMessageReceiveV1` produces a dict shaped
like `{schema, header, event}` — i.e. the SAME envelope shape as the
webhook payload. **Surprise vs. the earlier review concern:** the
long-connection callback DOES carry the `header.event_type` and
`header.event_id` (we feared they were stripped). So the envelope
reconstruction in plan Task 7 is defensive but not strictly required —
`json.loads(lark.JSON.marshal(data))` alone could feed `parse_inbound`.

Even so, **keep the explicit envelope construction** in Task 7. The marshal
helper has changed shape across SDK versions (older versions returned just
the event body), and pinning the envelope inline makes the parser contract
independent of `lark_oapi` version drift. Cost is one dict literal.

## Confirmed Task 1 schema decisions

- **DM `scope_key = "dm"`**, `scope_kind = "dm"`. `chat_id` (which goes
  into the neutral `channel_id` column) is the distinguishing factor between
  different DMs; the scope_key just tags "no sub-cut".
- **Group `scope_key = f"u:{union_id}"`**, `scope_kind = "participant"`.
  `union_id` is the right anchor: developer-scoped, stable across DMs and
  groups for the same person, present by default. Fallback to `open_id`
  when `union_id` is absent (rare per docs but observed possible).
- **`reply_to_id = message_id`** for groups (Feishu's `im.v1.message.reply`
  API takes the inbound `message_id`), **`reply_to_id = None`** for DMs (we
  send plain via `im.v1.message.create` to the chat).
- **`inbound_message_id = message.message_id`** carries through to the
  outbound tailer so `on_processing_start(state)` can attach the
  `thinking-face` reaction to the user's message, not the bot's reply.
- **Bot echo filter**: `sender.sender_type == "app"` OR
  `sender.sender_id.open_id == bot_open_id` — both checks are correct;
  keep both per defense in depth.

## Confirmed Task 4 `parse_inbound` decisions

- `<at>`-tag stripping via the simple regex is correct — the SDK delivers
  the `content` JSON unchanged (the `<at user_id="ou_x">Name</at>` markup
  is exactly what Feishu sends and what the user sees in their client).
- Group mention-gating against `bot_open_id`: confirmed `mentions[].id.open_id`
  is the right field; `mentioned_type` is informational only.

## What was NOT verified end-to-end in this PoC window

- No live `@bot` mention was sent during the 30-second WS window (this is a
  remote / autonomous run with no human at the Feishu client). All
  event-shape claims above come from the SDK's typed model files (the
  authoritative source for "what we will receive"), not from observation
  of a live event.
- 话题/topic creation flow was not exercised (rare in practice; v1 plan
  treats it as future overlay only).
- Card actions, reactions, file/image uploads — out of v1 scope.

If any of these surface a difference at implementation time, update Task 4
and re-validate; the SDK source is the same authoritative reference the
implementer should reach for first.

## Reference: hermes-agent prior art

`~/hermes-agent/gateway/platforms/feishu.py` validates against the same SDK
in production:

- `_hydrate_bot_identity` (line 4166): the `/open-apis/bot/v3/info` probe
  pattern we copied.
- `_on_message_event` (line 2269) + `_submit_on_loop` (line 2547): the
  thread → asyncio bridge using `safe_schedule_threadsafe`
  (`asyncio.run_coroutine_threadsafe` under the hood). Plan Task 7 follows
  the same pattern.

## Outcome

Plan unblocks; proceed to Task 1 (data model). No schema or parser changes
required as a result of this PoC.
