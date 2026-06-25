---
sidebar_position: 4
title: DingTalk Setup
---

# DingTalk setup

DingTalk binds an enterprise bot to your CubeBox workspace over a **Stream** connection: CubeBox opens an outbound socket to DingTalk and receives messages over it, so nothing on your CubeBox host needs to be reachable from the internet. This guide walks you through creating an internal app in the DingTalk Open Platform console, giving its bot a Stream-mode robot, granting the permissions the connector needs, binding it to CubeBox with your app key and app secret, and linking your account so the bot answers you.

## Before you start

You need:

- A **workspace admin** or member account in CubeBox (a plain member can bind a bot that runs as themselves; impersonating another user requires workspace admin).
- Permission to create an **internal app** in your organization's DingTalk Open Platform console (`open-dev.dingtalk.com`).

## Step 1 — Create an internal app

In the DingTalk **Open Platform** console (`open-dev.dingtalk.com`), create a new **internal enterprise app**. Once it exists, open its **Credentials & Basic Info** page and note the **AppKey** and **AppSecret** — you'll need both when binding to CubeBox.

:::info 📸 Screenshot placeholder
**Capture:** The DingTalk Open Platform "create internal app" dialog, and the app's Credentials & Basic Info page showing where AppKey and AppSecret appear.
**Asset:** `/img/im/dingtalk-app-credentials.png`
:::

## Step 2 — Add the bot (robot) capability

Under the app's capabilities, add the **Bot** (robot) capability so the app can receive and send chat messages. Give the bot a name and icon — this is the identity users see in DingTalk.

CubeBox identifies the bot by your **AppKey** (it doubles as the bot's robot code), so there is no separate bot ID to copy here — but the robot capability must be added, or the bot never receives messages.

:::info 📸 Screenshot placeholder
**Capture:** The app's capability/feature page with the Bot (robot) capability added and the bot name/icon filled in.
**Asset:** `/img/im/dingtalk-bot-capability.png`
:::

## Step 3 — Enable Stream mode

In the bot's message-receiving settings, choose **Stream mode** (the persistent-connection delivery option) rather than a webhook/HTTP callback URL. In Stream mode DingTalk pushes each inbound message down the socket CubeBox holds open, so you don't configure any public callback URL.

:::info 📸 Screenshot placeholder
**Capture:** The bot's message-receiving configuration with the Stream-mode (persistent connection) option selected instead of the HTTP-callback option.
**Asset:** `/img/im/dingtalk-stream-mode.png`
:::

## Step 4 — Grant the permissions the connector needs

In the app's **Permissions** section, grant the scopes the connector uses:

- **Send and receive bot messages** — required, so the bot can read messages addressed to it and reply with cards. Without this the bot sees nothing.
- **Read a user's profile (to resolve email)** — optional but recommended. CubeBox can look up a sender's email from their DingTalk staff ID and match it to a CubeBox account, so users don't have to run `link` manually (see Step 6). This needs the user-profile read permission that exposes the user's email.

:::note
The exact permission names in the DingTalk console change between console versions and editions, so the labels above are described by capability rather than quoted verbatim. Grant the permission that lets the app **send/receive bot messages** and, for email auto-resolution, the one that lets it **read a user's profile including email**. If your console wording differs, match by capability.
:::

:::info 📸 Screenshot placeholder
**Capture:** The app Permissions page with the bot-message send/receive permission and the user-profile (email) read permission granted.
**Asset:** `/img/im/dingtalk-permissions.png`
:::

## Step 5 — Bind the bot in CubeBox

In your CubeBox workspace, open the **IM connectors** settings and connect a new DingTalk account. Provide:

| Field | Required | Notes |
|---|---|---|
| **AppKey** | Yes | From Step 1. Also serves as the account's external identifier and the bot's robot code. |
| **AppSecret** | Yes | From Step 1. CubeBox uses it to obtain an access token and to call DingTalk. |
| **Run identity** | Yes | `self` (the bot runs as you) by default. Binding it to run as another user requires the **workspace admin** role. |

The delivery mode is fixed to **Stream** — there is nothing to choose. On binding, CubeBox validates the AppKey + AppSecret by exchanging them for a DingTalk access token; if the credentials are wrong the token exchange fails and binding is rejected. Fix them in the console and retry. Valid credentials are stored encrypted.

:::info 📸 Screenshot placeholder
**Capture:** The CubeBox "Connect DingTalk account" form with the AppKey, AppSecret, and Run identity fields.
**Asset:** `/img/im/dingtalk-cubebox-connect-form.png`
:::

Once bound, CubeBox opens the Stream connection automatically. (Note: re-enabling a disabled Stream account currently requires an API restart to re-establish the socket — see the [Overview](./overview.md#delivery-modes).)

## Step 6 — Test it

Add the bot to a chat (or DM it directly) and @-mention it in a group, or just message it in a DM. The first time, CubeBox needs to know who you are:

- If you granted the user-profile (email) permission in Step 4, CubeBox resolves your DingTalk email automatically and — if that email matches a CubeBox account in this workspace — runs your message immediately.
- Otherwise, the bot asks you to link. Send it `link your@email.com` (the `/link your@email.com` form also works), open the link the bot replies with **while logged in to CubeBox**, and confirm. See [Identity linking](./overview.md#identity-linking).

Once linked, the bot replies with a live-updating interactive card as the agent streams its response.

## Conversation commands

The DingTalk bot recognizes the link command in any chat it's in:

| Command | Aliases | Effect |
|---|---|---|
| `link <email>` | `/link <email>` | Link your DingTalk identity to your CubeBox account (see [Identity linking](./overview.md#identity-linking)). |

:::note
The `/new` and `/reset` "start a fresh conversation" commands are **not** wired up on the DingTalk connector today — they work on Feishu/Lark and Discord but are silently ignored on DingTalk. To start over, message the bot fresh; per-channel conversation behavior follows the [channel binding mode](./overview.md#channel-binding-modes).
:::

## Rotating credentials

There is no in-place secret edit. To rotate an AppSecret, **delete** the account in CubeBox and bind it again with the new AppKey + AppSecret.
