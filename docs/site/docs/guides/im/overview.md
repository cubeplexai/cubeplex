---
sidebar_position: 1
title: IM Connectors Overview
---

# IM Connectors

IM connectors let your workspace's agent answer messages inside a chat platform — Feishu/Lark, DingTalk, Slack, Microsoft Teams, or Discord. You bind a bot once, and from then on anyone in the chat (who is also a member of your workspace) can @-mention the bot or DM it and get the same agent that runs in the CubeBox web app, with the same skills, memory, and tools.

## The general model

Every platform follows the same four-step flow:

1. **Bind a bot.** A workspace member registers the bot's credentials (app ID, secrets, tokens) against the workspace. CubeBox stores them encrypted and creates an **IM connector account**.
2. **Inbound message arrives.** The platform delivers each message to CubeBox — either by pushing it to a webhook URL you configure in the platform's console, or over a persistent socket CubeBox opens to the platform (see [Delivery modes](#delivery-modes)).
3. **Identity gate + agent run.** CubeBox figures out *which CubeBox user* the sender is (see [Identity linking](#identity-linking)), confirms they belong to the workspace, then starts an agent run on their behalf.
4. **Reply.** The agent's response streams back into the chat. On Feishu it renders as a live-updating interactive card; on other platforms it posts as a message (and edits in place where the platform allows).

The bot runs each message as a real CubeBox user, so permissions, model access, and tool access are exactly what that user would have in the web app. If a sender can't be matched to a workspace member, the bot replies that it can't help and the run never starts.

## Supported platforms

CubeBox ships connector code for five platforms. They are **not** equally mature — Feishu/Lark is the reference implementation — but each has its own setup guide.

| Platform | Maturity | Delivery mode | Setup guide |
|---|---|---|---|
| **Feishu / Lark** | Most developed — interactive streaming cards, message encryption, signature verification, in-place card edits, human-in-the-loop button actions. | Long-connection (default) or webhook | [Feishu / Lark](./feishu.md) |
| **Slack** | Working connector — in-place message edits, automatic email-based identity resolution, native `/link` slash command. | Gateway (Socket Mode) | [Slack](./slack.md) |
| **DingTalk** | Working connector — automatic email-based identity resolution. | Stream | [DingTalk](./dingtalk.md) |
| **Microsoft Teams** | Working connector — validates the Azure Bot Framework JWT on each inbound activity; requires a publicly reachable host. | Webhook | [Microsoft Teams](./teams.md) |
| **Discord** | Working connector — native `/new`, `/reset`, `/link` slash commands. | Gateway | [Discord](./discord.md) |

Command support varies by platform — see [Conversation commands](#conversation-commands).

### Delivery modes

How a platform's messages reach CubeBox depends on the platform:

- **Long-connection / gateway / stream** — CubeBox opens a persistent outbound socket to the platform and receives events over it. Nothing needs to be reachable from the internet, so this works behind a firewall. Feishu (default), Slack, Discord, and DingTalk use this style.
- **Webhook** — the platform POSTs each event to a public URL on your CubeBox host. The host must be reachable from the platform's servers. Feishu (optional) and Teams use this style.

:::caution Re-enabling a long-connection account needs an API restart
Disabling or deleting an account tears down its live connection immediately. **Re-enabling** a long-connection account from the admin API rebinds it lazily — the current version requires restarting the API process to fully re-establish the socket. Webhook accounts pick up again immediately because the inbound route re-checks the enabled flag on every request.
:::

## Identity linking

A message in a chat app carries a platform user ID, not a CubeBox identity. Before running anything, CubeBox maps the sender to a CubeBox user and checks they're a member of the bot's workspace. Membership is **re-checked on every message**, even after the mapping is cached — a user removed from the workspace stops getting answers immediately.

Resolution happens in this order:

1. **Cached link.** If the sender was matched before, CubeBox reuses the stored mapping (after re-confirming workspace membership).
2. **Email resolution.** On platforms with a contact API — **Feishu, Slack, and DingTalk** — CubeBox looks up the sender's email and matches it to a CubeBox user with that email.
3. **`/link` command fallback.** On platforms without an email API (**Discord**, **Teams**), or whenever email resolution fails, the sender links manually.

### Linking with `/link`

The sender sends the bot:

```
/link you@example.com
```

(The Chinese alias `绑定 you@example.com` also works.) The bot replies with a confirmation URL of the form `https://<your-cubebox-host>/im-link?token=...`. The link carries a short-lived signed token (valid 10 minutes) encoding the claimed email and the target workspace.

The sender opens that link **while logged in to CubeBox**. CubeBox confirms that the logged-in user's email matches the claimed email and that they belong to the workspace, then permanently links the chat identity to the CubeBox account. After that, the sender's messages run as that user without re-linking.

:::tip
The email you `/link` must be the email of an existing CubeBox account that is already a member of the bot's workspace. Linking does not create accounts or grant membership — it only connects an existing one.
:::

## Conversation commands

Command support differs by platform — not every command exists everywhere.

| Command | Effect | Available on |
|---|---|---|
| `/link <email>` | Links your chat identity to your CubeBox account (see [Identity linking](#identity-linking)). | All platforms. Native slash command on Slack and Discord; a text message on Feishu, DingTalk, and Teams. The Chinese alias `绑定 <email>` works on Feishu only. |
| `/new` (alias `/reset`, `新对话`) | Starts a fresh conversation — drops the current conversation binding for the chat scope you're in, so the bot starts clean on your next message. | **Feishu** (text command) and **Discord** (native slash command) only. Not yet wired up on Slack, DingTalk, or Teams. |

See each platform's setup guide for the exact command form.

## Channel binding modes

In a group chat, you can choose whether everyone shares one conversation or each person gets their own:

- **Isolated** (default) — each sender in a group gets their own private conversation with the bot. This is the default for any channel without an explicit binding.
- **Shared** — everyone in the channel talks to one shared conversation. Shared mode requires choosing a sandbox mode for the channel.

Bindings are managed per account from the workspace IM settings.

## Managing connectors

IM connector accounts are created and managed from your workspace settings. Workspace members can connect a bot that runs **as themselves**; binding a bot that runs as *another* user (impersonation) requires the **workspace admin** role. Disabling, deleting, and channel-binding management are available from the same settings area.

:::info 📸 Screenshot placeholder
**Capture:** The workspace IM connectors settings page showing the list of bound accounts (platform icon, bot name, enabled toggle) and the "Connect" entry point.
**Asset:** `/img/im/connectors-list.png`
:::

## Per-platform setup guides

Every platform binds through the same workspace IM settings and follows the same inbound → identity gate → agent run → reply model described above. The credentials and console steps differ — follow the guide for your platform:

- **[Feishu / Lark](./feishu.md)** — app ID + app secret (+ optional encrypt key / verification token). Long-connection (default) or webhook.
- **[Slack](./slack.md)** — bot token + app-level token (Socket Mode). Gateway.
- **[DingTalk](./dingtalk.md)** — app key + app secret. Stream.
- **[Microsoft Teams](./teams.md)** — app (bot) ID + app secret + tenant ID. Webhook (needs a publicly reachable host).
- **[Discord](./discord.md)** — bot token + application ID. Gateway; native `/new`, `/reset`, `/link` slash commands.
