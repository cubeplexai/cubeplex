---
sidebar_position: 2
title: Feishu / Lark Setup
---

# Feishu / Lark setup

Feishu (and its international edition, Lark) is the most developed IM connector. This guide walks you through creating the bot in the Feishu/Lark developer console, binding it to your CubeBox workspace, and linking your account so the bot answers you.

CubeBox supports both editions from the same connector — choose **Feishu** (`feishu.cn`) or **Lark** (`larksuite.com`) when you bind. The setup steps are identical; only the console domain differs.

## Before you start

You need:

- A **workspace admin** or member account in CubeBox (a plain member can bind a bot that runs as themselves; impersonating another user requires workspace admin).
- Permission to create a **custom app** in your Feishu/Lark organization's developer console.

## Step 1 — Create a custom app

In the Feishu/Lark **Developer Console** (`open.feishu.cn` for Feishu, `open.larksuite.com` for Lark), create a new **custom app**. Note its **App ID** and **App Secret** — you'll need both when binding to CubeBox.

:::info 📸 Screenshot placeholder
**Capture:** The Feishu/Lark developer console "Create custom app" dialog, and the app's credentials page showing where App ID and App Secret appear.
**Asset:** `/img/im/feishu/console-app-credentials.png`
:::

## Step 2 — Enable the bot capability

Under the app's **Features**, add the **Bot** capability and publish the bot identity. CubeBox reads the bot's identity (its open ID) automatically from the App ID + App Secret when you bind — but the bot must be **published** first, or binding fails with a "could not hydrate bot" error.

:::info 📸 Screenshot placeholder
**Capture:** The app Features page with the Bot capability enabled.
**Asset:** `/img/im/feishu/console-bot-capability.png`
:::

## Step 3 — Grant message permissions

Under **Permissions & Scopes**, grant the scopes the bot needs to read mentions, send messages, and resolve group names. To let CubeBox auto-resolve a sender's email (so users don't have to run `/link` manually), also grant the contact/read-email scope.

| Scope | Required | Purpose |
|---|---|---|
| Message read / send (`im:message`, `im:message:send_as_bot`, …) | Yes | Receive @-mentions / DMs and reply as the bot. |
| `im:chat:readonly` (or `im:chat:read` / `im:chat`) | Yes | Look up the group display name via `GET /open-apis/im/v1/chats/:chat_id` so CubeBox Topic titles show the real group name instead of a generic label. |
| Contact email read (`contact:user.email:readonly` + related) | Recommended | Auto-match the sender's Feishu email to a CubeBox account (avoids manual `link`). |

After adding scopes, **publish a new app version** so the tenant grants take effect — Feishu does not apply new scopes until the version is published.

:::info 📸 Screenshot placeholder
**Capture:** The app Permissions & Scopes page with the message read/send, group-info read (`im:chat:readonly`), and contact email scopes selected.
**Asset:** `/img/im/feishu/console-permissions.png`
:::

## Step 4 — Choose how events reach CubeBox

Feishu can deliver events two ways. Pick one — it controls what you configure next and which `delivery_mode` you choose when binding.

### Option A — Long-connection (default, recommended)

CubeBox opens an outbound socket to Feishu and receives events over it. Nothing on your CubeBox host needs to be reachable from the internet, so this works behind a firewall. In the Feishu console, set the app's event delivery to **"Use long connection to receive events."** No public URL or signature is involved.

This is the default. When you bind in CubeBox, leave `delivery_mode` as `long_connection`.

### Option B — Webhook

Feishu POSTs each event to a public URL on your CubeBox host. Use this only if your host is internet-reachable and you prefer webhooks.

In the console's **Event Subscriptions**, set the **Request URL** to:

```
https://<your-cubebox-host>/api/v1/im/feishu/events
```

Feishu sends a one-time `url_verification` challenge to that URL; CubeBox echoes the challenge back automatically once the account is bound and the verification token matches, so bind the account in CubeBox (Step 5) **before** you ask Feishu to verify the URL.

When you bind in CubeBox, set `delivery_mode` to `webhook`.

:::info 📸 Screenshot placeholder
**Capture:** The Feishu console Event Subscriptions page showing the long-connection toggle vs. the Request URL field.
**Asset:** `/img/im/feishu/console-event-delivery.png`
:::

## Step 5 — Configure the verification token and encryption (optional but recommended)

In the Feishu console's **Event Subscriptions** section, Feishu shows two security values:

- **Verification Token** — a static token Feishu includes in every event. CubeBox compares it in constant time and rejects events whose token doesn't match. Supply it when you bind.
- **Encrypt Key** — enabling **Event Encryption** makes Feishu encrypt the whole event body. CubeBox decrypts it and also verifies the request signature. Strongly recommended for the webhook path.

Both are optional fields when binding. If you set an Encrypt Key, Feishu signs each webhook request and CubeBox verifies the signature (see [Signature scheme](#signature-scheme)).

:::info 📸 Screenshot placeholder
**Capture:** The Event Subscriptions security panel showing the Verification Token and Encrypt Key / Event Encryption toggle.
**Asset:** `/img/im/feishu/console-token-encrypt.png`
:::

### Subscribe to the message event

Still under Event Subscriptions, add the bot-message-received event so Feishu forwards messages to CubeBox. Without this subscription the bot never sees any messages.

:::info 📸 Screenshot placeholder
**Capture:** The "Add events" dialog with the receive-message event subscribed.
**Asset:** `/img/im/feishu/console-subscribe-message.png`
:::

## Step 6 — Bind the bot in CubeBox

In your CubeBox workspace, open the **IM connectors** settings and connect a new Feishu account. Provide:

| Field | Required | Notes |
|---|---|---|
| **App ID** | Yes | From Step 1. Also serves as the account's external identifier. |
| **App Secret** | Yes | From Step 1. CubeBox uses it to read the bot identity and to call Feishu. |
| **Encrypt Key** | No | Only if you enabled Event Encryption (Step 5). |
| **Verification Token** | No | The token from Step 5. |
| **Domain** | Yes | `feishu` or `lark` — pick the edition your app lives in. Defaults to `feishu`. |
| **Delivery mode** | Yes | `long_connection` (default) or `webhook`, matching Step 4. |
| **Run identity** | Yes | `self` (the bot runs as you) by default. Binding it to run as another user requires the **workspace admin** role. |

On binding, CubeBox reads the bot's identity from Feishu using your App ID + App Secret and stores the credentials encrypted. If the App Secret is wrong or the bot isn't published, binding fails — fix the console side and retry.

:::info 📸 Screenshot placeholder
**Capture:** The CubeBox "Connect Feishu account" form with the App ID, App Secret, Encrypt Key, Verification Token, Domain (Feishu/Lark), and Delivery mode fields.
**Asset:** `/img/im/feishu/cubebox-connect-form.png`
:::

If you chose the **webhook** path, go back to the Feishu console now and trigger the Request URL verification — CubeBox will answer the challenge.

## Step 7 — Test it

Add the bot to a chat (or DM it directly) and @-mention it. The first time, CubeBox needs to know who you are:

- If you granted the contact/email scope (Step 3), CubeBox resolves your Feishu email automatically and — if that email matches a CubeBox account in this workspace — runs your message immediately.
- Otherwise, the bot asks you to link. Send it `/link your@email.com` (or `绑定 your@email.com`), open the link the bot replies with **while logged in to CubeBox**, and confirm. See [Identity linking](./overview.md#identity-linking).

Once linked, the bot replies with a live-updating interactive card as the agent streams its response.

## Signature scheme

When you enable **Event Encryption** (Encrypt Key set), Feishu signs each webhook request and CubeBox verifies it. This applies to the webhook delivery path; the long-connection path is authenticated by the socket itself.

CubeBox computes the signature exactly as Feishu specifies:

```
signature = SHA256( timestamp + nonce + encrypt_key + raw_request_body )
```

The three inputs other than the body and key arrive as request headers:

| Header | Meaning |
|---|---|
| `x-lark-request-timestamp` | The timestamp included in the signed string. |
| `x-lark-request-nonce` | The per-request nonce included in the signed string. |
| `x-lark-signature` | The hex SHA-256 digest CubeBox compares against (constant-time). |

The signature is computed over the **outer** request body. When Event Encryption is on, that body is `{"encrypt": "<base64 ciphertext>"}`; CubeBox decrypts it (AES-256-CBC, key = `SHA256(encrypt_key)`, IV = first 16 bytes of the ciphertext, PKCS#7 padding) before processing the event.

In addition to (or instead of) the signature, CubeBox checks the **verification token** carried in the event payload's `header.token` (or top-level `token` on legacy events). The verification-token check runs **before** the `url_verification` challenge is echoed, so an attacker can't prove control of your endpoint by getting a challenge bounced back.

:::tip Plain mode
If you don't set an Encrypt Key, Feishu doesn't send the `x-lark-signature` header and CubeBox skips signature verification — the **verification token** is then the only safeguard. For internet-facing webhook deployments, enabling Event Encryption is strongly recommended.
:::

## Conversation commands

The bot understands these in any chat it's in:

| Command | Aliases | Effect |
|---|---|---|
| `/link <email>` | `绑定 <email>` | Link your Feishu identity to your CubeBox account. |
| `/new` | `/reset`, `新对话` | Start a fresh conversation; your next message begins a new one. |

## Rotating credentials

There is no in-place secret edit. To rotate an App Secret, Encrypt Key, or Verification Token, **delete** the account in CubeBox and bind it again with the new values.
