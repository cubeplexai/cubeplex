# IM Feishu Connector — End-to-End Validation Report

**Date:** 2026-06-13
**Branch:** `feat/im-connectors`
**Worktree:** `.worktrees/feat/im-connectors` (slot 76, port 8076)
**Bot under test:** `@moltbot` (app_id `cli_a9f0a4c078a11bd3`, open_id `ou_b6aef8a8a515e3e6d8561ce41dfd5ec9`)
**Driver:** `lark-cli` (separate app `cli_aaa6509899badcdd`, acting as the
end-user `巩向锋 / ou_b4e36f58a1ea975cd01d5eacd3b415a2`)

## Goal

Run the IM Feishu connector against the real Feishu cloud, using
`lark-cli` to simulate a human user. Verify the long-connection
delivery mode, the durable outbox + queue, the run path, the bot reply,
the reactions lifecycle, and multi-turn conversation continuity.

## Setup

1. Reset worktree dev DB; `alembic upgrade head` (final migration
   `9ccd63170399 — im connectors tables`).
2. Start backend with `CUBEBOX_API__RELOAD=false uv run python main.py`
   on `127.0.0.1:8076`. Reload had to be off — `uvicorn --reload`
   restarts every time we save a Python file, which tears down the
   WebSocket mid-session.
3. Register a tester user via `POST /api/v1/auth/register`
   (`moltbot-tester+im@example.com`), then `POST /api/v1/auth/login`,
   capture cookies + bootstrap CSRF via `GET /api/v1/auth/me`.
4. Connect the bot via `POST /api/v1/ws/{ws}/im/accounts`:
   ```json
   {
     "platform": "feishu",
     "app_id": "cli_a9f0a4c078a11bd3",
     "app_secret": "<from ~/.feishurc>",
     "domain": "feishu",
     "delivery_mode": "long_connection",
     "acting_user_id": "self"
   }
   ```
   → 201, account `imac-1hK8Z55Guz1duS`.
5. Create a Feishu group via `lark-cli im +chat-create --bots
   cli_a9f0a4c078a11bd3`. Look up the bot's open_id under the
   user-app's namespace via `lark-cli im chat.members bots` (it's
   `ou_ccfff3521247621b6f42cdaaba1fea0f` from the user-app side, ≠ the
   bot-app's own open_id — Feishu open_ids are app-scoped). Use that
   id in the inline `<at user_id="…"></at>` markup when sending.

## Bugs Found + Fixed (this report's commit)

### 1. `connect failed, err: This event loop is already running`

`lark_oapi/ws/client.py` executes at import time:

```python
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
```

Under uvicorn this grabs the **main asyncio loop**, then
`Client.start()` (we run it via `loop.run_in_executor(None, …)`) calls
`loop.run_until_complete(self._connect())` against the captured global
— which is the uvicorn loop. RuntimeError, no WebSocket ever opens.
This affected every platform, not Python 3.13 specifically.

**Fix:** in `FeishuLongConnection.connect()`, install a fresh
`asyncio.new_event_loop()` on the executor thread and replace
`lark_oapi.ws.client.loop` before calling `start()`. The SDK's
`run_until_complete` now targets this worker thread.

Trade-off: `lark_oapi.ws.client.loop` is module-global, so this pattern
only supports **one** long-connection account per Python process today.
Multi-account support requires upstream changes to lark_oapi (or
running each account in a subprocess). v1 documents this elsewhere.

### 2. `POST /ws/{ws}/im/accounts` doesn't open the WebSocket

`_connect_one` only ran at app startup. Creating a new account via the
API left it dormant until the next process restart — the row was
visible, but `app.state.im_long_connections[account.id]` was empty
and no events flowed.

**Fix:** expose `_connect_one` as `app.state.im_connect_account` and
have `ws_im.connect_account` call it inline after `svc.connect_feishu`
returns. The route now returns 201 only after the WebSocket has
attempted to open.

### 3. `add_reaction failed: code=%s msg=%s` (literal "%s" in logs)

loguru only formats `{}` style — `%s` is a stdlib-logging-ism. Every
add_reaction failure logged the literal placeholder string and the
real Feishu error was lost.

**Fix:** `{}` placeholders. The actual `code=231001 msg=reaction type
is invalid` then surfaced, which led directly to bug #4.

### 4. Reaction emoji_type mismatched Feishu's set

`_REACTION_PROCESSING = "ThumbsUp"`. Feishu's emoji_type set is
**UPPERCASE** — `"THUMBSUP"`, `"OK"`, `"DONE"`, etc. Mixed case is
rejected with `code=231001 reaction type is invalid`. So the
processing-start reaction silently failed for every run. (The
processing-failure reaction was already `"OK"`, which happens to be
uppercase, so it was untouched.)

**Fix:** `_REACTION_PROCESSING = "THUMBSUP"`. The unit test that
asserted on the old literal was updated in lock-step.

## Test Matrix

All runs used the real Feishu cloud, the real moltbot account, and the
real `deepseek-v4-flash` LLM (the worktree's default).

| # | Stimulus (lark-cli, user identity) | Expected | Observed | Pass |
|---|---|---|---|---|
| 1 | Create group with `@moltbot`, send `@moltbot 你好，请用一句话简短介绍你自己。` | bot replies; receipt + queue row both `completed`; thread_link created | bot replied `"你好！我是你的 AI 助手…"`, `reply_to=om_…(user msg)`, receipt `imwr-…` `completed`, queue `imrq-…` `completed`, thread_link `imtl-…` created (`scope_key=u:on_27c2810f…`) | ✓ |
| 2 | Follow-up: `@moltbot 我刚才问了你什么？请把原话复述一遍。` | same conversation_id reused, bot recalls turn 1 | only 1 thread_link row exists; bot replied `"你刚才问的是：「你好，请用一句话简短介绍你自己。」"` | ✓ |
| 3 | `@moltbot 在吗？请说在。` | quick reply with reaction lifecycle | bot replied `"在。"`; reaction event chain visible in long-conn (`reaction.created_v1` then `reaction.deleted_v1`) | ✓ |
| 4 | `@moltbot 请用尽量详细的方式，分5个段落…` (forces a slow LLM run) | `THUMBSUP` reaction added during processing, persists, removed on complete | polled `/messages/{id}/reactions` every 500ms — `THUMBSUP` present from t+1s through t+20s, gone at t+25s; final reply 1060 chars; `reply_to` set | ✓ |

## What the System Actually Did (data-layer)

```
im_webhook_receipts          1 row per inbound, status = completed
im_run_queue                 1 row per inbound, status = completed, attempts = 1
im_thread_links              1 row total — same scope_key across multiple
                             inbound user mentions in the same group chat;
                             conversation_id reused; the chat × user session
                             boundary works as the spec described.
im_connector_accounts        1 row, enabled=true, delivery_mode=long_connection
```

The long-connection log stream confirms:

- `connected to wss://msg-frontier.feishu.cn/ws/…` once at startup, then
  reconnect-free across all 4 stimuli.
- For each inbound: `[Feishu LC] inbound <event_id>: enqueued`.
- For the reaction lifecycle: Feishu echoes the bot's own
  `im.message.reaction.created_v1` / `.deleted_v1` events back over
  the long-conn. lark_oapi logs `processor not found` for those — we
  don't subscribe to reaction events, and we don't need to. This is
  log noise, not a bug.

## What This Validation Does NOT Cover

- **Webhook delivery mode.** Only long-connection was exercised. The
  webhook path (`POST /api/v1/im/feishu/events` with signed bodies and
  the encrypted-payload try-decrypt fan-out) has unit tests in
  `tests/e2e/test_im_feishu_ingress.py` but no live cloud verification
  this session.
- **Multi-account fan-out.** See bug #1's trade-off — the SDK's
  module-global loop means we can prove 1 account works at a time.
- **Artifact share-link.** The bot's replies in these stimuli were
  text-only, no file artifacts surfaced.
- **HITL (interactive cards with buttons).** Not in v1 scope; deferred.
- **Reactions on failure.** All 4 runs completed cleanly; the
  on_processing_failed → `OK` reaction path has unit coverage but no
  live trigger this session.
- **Rate limit / `_FloodSignal`.** No traffic at that scale in this
  session.

## Operational Notes for Anyone Reproducing This

- `lark-cli` (`@larksuite/cli`) is the driver. It auths separately
  from moltbot's app — get user scopes via
  `lark-cli auth login --scope "im:message.send_as_user,im:chat:create_by_user,im:chat,im:message"`
  before sending.
- Cross-app open_ids do NOT match. Use `lark-cli im chat.members bots
  --params '{"chat_id":"oc_…"}'` to look up the bot's open_id under
  the driver's app namespace; that's the value you put inside the
  `<at user_id="…">` markup. The bot's webhook/long-conn will receive
  the event with the mention id translated to its own namespace, where
  it matches `bot_open_id` and the parser's group-mention gate passes.
- Use a group chat, not p2p. The cross-app DM path requires the user's
  CLI app to know the moltbot's open_id under ITS namespace, which it
  doesn't until they share a chat.
- moltbot's `bot_open_id` is hydrated at connect-time via
  `/open-apis/bot/v3/info` against the **bot's own** tenant_access_token,
  using the credentials stashed in `~/.feishurc`. The hydrated value
  goes into the encrypted credential blob, not directly into
  `im_connector_accounts`.

## Status

End-to-end happy path: ✅ working against real Feishu cloud.
4 bugs found, 4 fixes committed (`bf8df415`), 45 IM unit tests pass.
