# Feishu IM connector setup

cubebox can bind a Feishu (Lark) bot to a workspace so messages and
@-mentions become agent runs, with streamed replies + processing reactions
+ artifact share-links. Two delivery modes are supported:

- **Long connection** (recommended for self-host / dev): cubebox holds an
  outbound WebSocket via `lark_oapi.ws.Client`. No public ingress needed.
- **Webhook**: Feishu POSTs events to `/api/v1/im/feishu/events`. Required
  for cloud deploys behind a public LB.

## Create the Feishu app

1. Go to <https://open.feishu.cn> (or <https://www.larksuite.com> for the
   international tenant). Create an internal app.
2. Open the app's **Credentials & Basic Info** page. Note the `App ID`
   (starts with `cli_`) and `App Secret`.
3. Open **Permissions & Scopes** and grant the minimum scope set:
   - `im:message`
   - `im:message:send_as_bot`
   - `im:resource` (image upload)
   - `im:message.group_at_msg` (group @mentions delivered)
   - `im:message.p2p_msg` (DMs delivered)
   - `im:message.reaction:write` (processing reactions)
   - `im:chat:readonly` (or `im:chat:read` / `im:chat`) — **REQUIRED**
     for group Topic titles. CubeBox calls
     `GET /open-apis/im/v1/chats/:chat_id` on first group message to
     resolve the human-readable group name. Without this scope the
     Topic still works but its title falls back to the generic label
     `群聊` instead of the real group name.
   - `contact:user.base:readonly` (sender display name)
   - `contact:user.email:readonly` — **REQUIRED** for the sender
     identity gate. Without it, Feishu omits `user.email` from
     `contact/v3/users/{open_id}` responses and every sender is
     rejected as "not a workspace member". This is a separately
     governed scope from `contact:user.base:readonly`.
   - `contact:user.id:readonly` — used by the gate's reverse lookup
     (resolve email → user). Required for the same reason.
4. **Create the cubebox connector FIRST, then configure the Feishu
   event subscription.** The ingress route drops events for unknown
   `app_id` with a bare 200 and never echoes the `url_verification`
   challenge — Feishu's "verify request URL" step would fail until the
   app_id is known to cubebox. So:
   1. Copy the App ID + App Secret (and Encrypt Key / Verification
      Token, if you'll use webhook mode) from this page.
   2. Jump to "Connect the bot to a cubebox workspace" below and POST
      the credentials to cubebox. The endpoint hydrates `bot_open_id`
      and refuses to persist the account if hydration fails — so a
      successful 201 confirms the credentials are good.
   3. Then return here.
5. Open **Event Subscriptions**:
   - For **long connection**: switch the event-subscription mode to
     "Long Connection" / "Persistent Connection". No request URL.
   - For **webhook**: set the request URL to
     `https://<your-host>/api/v1/im/feishu/events`. The
     **Encrypt Key** and **Verification Token** you copied in step 4.1
     serve two roles: (a) signature verification on every request (the
     `x-lark-signature` header is HMAC'd with this key); (b) body
     encryption when you flip the "Event Encryption" toggle. cubebox
     supports **both modes** — if you enable encryption, the ingress
     route try-decrypts against each enabled account's `encrypt_key`
     and routes by the `app_id` inside the decrypted payload.
   - Subscribe to the event `im.message.receive_v1`. Future overlays
     (`im.message.reaction.created_v1`, `card.action.trigger`) are not used
     in v1.
6. Publish a version of the app and grant it to your tenant.

## Connect the bot to a cubebox workspace

```http
POST /api/v1/ws/{workspace_id}/im/accounts
Content-Type: application/json

{
  "platform": "feishu",
  "app_id": "cli_xxxx",
  "app_secret": "...",
  "encrypt_key": "...",                # required if you set one in step 4 for webhook mode; empty for long-conn-only
  "verification_token": "...",         # always required (Feishu sends it in event headers)
  "domain": "feishu",                  # 'lark' for the international tenant
  "delivery_mode": "long_connection",  # or 'webhook'
  "acting_user_id": "self"             # all runs are attributed to the calling user; or a specific user id
}
```

The route hydrates the bot's own `open_id` via Feishu's `/open-apis/bot/v3/info`
endpoint at connect time and stores it on the credential. The webhook
ingress and the long-connection client both read it from there — no further
hydration on every event.

Restart the cubebox API process (or wait for the next pod restart in cloud
deploys) so the long-connection client opens. For webhook mode no restart
is needed.

## Manual smoke checklist

Run this against a real bot before merging IM changes — Feishu has no
sandbox API, so this checklist IS the integration test for the Feishu
HTTP boundary.

- [ ] DM the bot `hello` → bot replies with a streamed response in the
      DM. ⏱️ reaction appears on the user's message during processing and
      is removed when the run completes.
- [ ] DM the bot `draw me a chart` (or any prompt that emits an `image`
      artifact) → image appears inline as a Feishu image message.
- [ ] DM the bot `build a tiny website` (or any non-image artifact) →
      "📎 view →" link appears in the thread; clicking it opens the share
      preview page; the preview disappears after the 7-day TTL.
- [ ] In a group, user A `@bot summarize` → bot quote-replies. A sends a
      fresh `@bot 改成精简版` (no re-quote) → bot is **still in A's
      conversation** (chat × user session, not thread-per-message).
- [ ] In the same group, user B `@bot ...` → bot answers in a **separate
      conversation** from A's; B's context never bleeds into A's.
- [ ] A pure non-@ message in the group → bot does NOT respond
      (subscription scope + parser mention gate both hold).
- [ ] Tamper a webhook signature → 401, no run started, no DB rows
      created.
- [ ] Force a Feishu retry on the webhook (slow ack → Feishu resends with
      the same `event_id`) → no duplicate reply, the receipt-table dedupe
      catches it.
- [ ] Disable the account via `POST /api/v1/admin/im/accounts/{id}/disable`
      → next inbound is silently dropped (200 ack, no run, no DB rows).
- [ ] Trigger an LLM error mid-run → ⏱️ reaction is removed, a failure
      marker is added, an error notice posts in the reply.

## Disabling / removing

```http
POST   /api/v1/admin/im/accounts/{id}/disable
POST   /api/v1/admin/im/accounts/{id}/enable
DELETE /api/v1/ws/{workspace_id}/im/accounts/{id}
```

Deleting an account removes its credential too (after `_guard_references`
confirms nothing else points at it).
