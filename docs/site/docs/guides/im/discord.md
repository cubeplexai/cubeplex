---
sidebar_position: 6
title: Discord Setup
---

# Discord setup

Discord runs as a gateway bot: CubeBox opens a persistent outbound socket to Discord and receives messages over it, so nothing on your CubeBox host needs to be reachable from the internet. Its distinguishing feature is **native slash commands** — CubeBox registers `/new`, `/reset`, and `/link` directly on your Discord application, so they show up in Discord's command picker.

This guide walks you through creating the Discord application, adding a bot and copying its credentials, enabling the privileged intent the bot needs, inviting the bot to your server, binding it to your CubeBox workspace, and linking your account.

:::note No email resolution on Discord
Discord exposes no email for a sender, so CubeBox can't auto-match you to your account the way it can on Feishu. On Discord, **`/link <email>` is the only way to connect your chat identity to your CubeBox account** — every user runs it once. See [Identity linking](./overview.md#identity-linking).
:::

## Before you start

You need:

- A **workspace admin** or member account in CubeBox (a plain member can bind a bot that runs as themselves; impersonating another user requires workspace admin).
- A Discord account with permission to create an application in the [Discord Developer Portal](https://discord.com/developers/applications), and **Manage Server** permission on the Discord server you want the bot to join.

## Step 1 — Create a Discord application

In the **Discord Developer Portal**, create a new application. This is the container for your bot and is what owns the slash commands CubeBox will register.

On the application's **General Information** page, copy the **Application ID** — you'll need it when binding to CubeBox (it's also the value CubeBox uses as this account's external identifier).

:::info 📸 Screenshot placeholder
**Capture:** The Discord Developer Portal "Create an application" dialog, and the application's General Information page with the Application ID field highlighted.
**Asset:** `/img/im/discord-create-application.png`
:::

## Step 2 — Add a bot and copy its token

Open the application's **Bot** section and add a bot. Then **Reset Token** (or **Copy**) to reveal the **bot token** — Discord shows the full token only once, so copy it now and keep it secret. You'll supply this token when binding to CubeBox.

:::info 📸 Screenshot placeholder
**Capture:** The application's Bot page showing the bot username, avatar, and the token reveal/reset control (token value itself redacted).
**Asset:** `/img/im/discord-bot-token.png`
:::

## Step 3 — Enable the Message Content intent

CubeBox's gateway connection requests the **Message Content** privileged intent so the bot can read the text of messages people send it. Message Content is a *privileged* intent on Discord and must be toggled on in the portal — without it, the bot connects but sees empty message bodies.

On the **Bot** page, enable the **Message Content Intent** under privileged gateway intents.

The connector also subscribes to guild messages, direct messages, and reactions, which are part of the default (non-privileged) intent set and need no separate toggle. Message Content is the only intent you have to enable by hand.

:::info 📸 Screenshot placeholder
**Capture:** The Bot page's "Privileged Gateway Intents" section with Message Content Intent turned on.
**Asset:** `/img/im/discord-message-content-intent.png`
:::

## Step 4 — Invite the bot to your server

Generate an OAuth2 invite URL for the bot and use it to add the bot to your Discord server. The invite needs two OAuth2 scopes:

- **`bot`** — adds the bot as a member of the server.
- **`applications.commands`** — lets CubeBox register the `/new`, `/reset`, and `/link` slash commands so they appear in the server's command picker.

Most servers grant the bot a baseline set of channel permissions (reading and sending messages in the channels you want it to operate in). Open the generated URL, choose the target server, and authorize.

:::info 📸 Screenshot placeholder
**Capture:** The OAuth2 URL Generator with the `bot` and `applications.commands` scopes checked, and the resulting "Add to Server" authorization screen.
**Asset:** `/img/im/discord-oauth-invite.png`
:::

## Step 5 — Bind the bot in CubeBox

In your CubeBox workspace, open the **IM connectors** settings and connect a new Discord account. Provide:

| Field | Required | Notes |
|---|---|---|
| **Bot token** | Yes | From Step 2. CubeBox uses it to open the gateway socket and to call Discord. |
| **Application ID** | Yes | From Step 1. Also serves as this account's external identifier. |
| **Run identity** | Yes | `self` (the bot runs as you) by default. Binding it to run as another user requires the **workspace admin** role. |

On binding, CubeBox validates the bot token by calling Discord's `GET /users/@me` (this also fetches the bot's username and avatar) and stores the credentials encrypted. If the token is wrong, binding fails — fix it in the portal and retry. The delivery mode is always **gateway** for Discord; there is no webhook option.

Once bound, CubeBox connects over the gateway and **syncs the `/new`, `/reset`, and `/link` slash commands** to the servers the bot is in, so they become available in Discord's command picker.

:::info 📸 Screenshot placeholder
**Capture:** The CubeBox "Connect Discord account" form with the Bot token, Application ID, and Run identity fields.
**Asset:** `/img/im/discord-cubebox-connect-form.png`
:::

:::caution Re-enabling needs an API restart
Disabling or deleting a Discord account tears down its gateway connection immediately. Re-enabling it rebinds lazily — the current version needs the API process restarted to fully re-establish the socket. See the [overview](./overview.md#delivery-modes).
:::

## Step 6 — Link your account and test

DM the bot or @-mention it in a channel it can see. Because Discord has no email API, the first time you'll need to link:

- Run the **`/link`** slash command with your CubeBox email (e.g. `/link you@example.com`). You can also type `/link you@example.com` as a plain message.
- The bot replies (privately) with a confirmation URL of the form `https://<your-cubebox-host>/im-link?token=...`. Open it **while logged in to CubeBox** and confirm.
- The email you link must belong to an existing CubeBox account that is already a member of this workspace. Linking connects an existing account — it doesn't create one or grant membership.

After linking, your messages run as your CubeBox user without re-linking, and the bot replies in the chat as the agent responds. See [Identity linking](./overview.md#identity-linking) for the full flow.

## Conversation commands

On Discord these are registered as **native slash commands** (they appear in the command picker) and also work as plain typed messages:

| Command | Effect |
|---|---|
| `/new` | Start a fresh conversation; your next message begins a new one. |
| `/reset` | Same as `/new` — drops the current conversation binding for this chat scope. |
| `/link <email>` | Link your Discord identity to your CubeBox account (see [Identity linking](./overview.md#identity-linking)). |

`/new` and `/reset` are equivalent. Slash command responses are sent privately (ephemeral) to the person who ran them.

## Rotating credentials

There is no in-place secret edit. To rotate a bot token, **delete** the account in CubeBox and bind it again with the new token.
