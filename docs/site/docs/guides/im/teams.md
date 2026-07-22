---
sidebar_position: 5
title: Microsoft Teams Setup
---

# Microsoft Teams setup

The Microsoft Teams connector lets your workspace's agent answer messages inside Teams. This guide walks you through registering a bot in Azure, pointing it at your CubePlex host, making it installable in Teams, binding it to your CubePlex workspace, and linking your account so the bot answers you.

Teams is the **one platform that requires a publicly reachable CubePlex host.** Unlike Feishu's long connection or the Slack / Discord / DingTalk gateway connectors — where CubePlex opens an outbound socket and nothing on your side needs to be exposed — Teams delivers messages by **webhook**: Microsoft's Bot Framework service POSTs each activity to a URL on your host. That URL must be reachable from Microsoft's servers over HTTPS. If your CubePlex host is behind a firewall with no inbound access, this connector will not work.

CubePlex validates the **Azure Bot Framework JWT** on every inbound activity, so only Microsoft's signed requests are accepted.

## Before you start

You need:

- A **workspace admin** or member account in CubePlex (a plain member can bind a bot that runs as themselves; impersonating another user requires workspace admin).
- An **Azure account** with permission to register an Azure Bot resource and a Microsoft Entra (Azure AD) application in your tenant.
- A **publicly reachable HTTPS URL** for your CubePlex host (see the note above).

:::caution The Azure / Teams console changes often
The screen names, blade labels, and manifest editor in Azure and the Teams Developer Portal change frequently and differ across tenants. This guide describes each step by **what you are configuring** (register a bot, get an app ID + secret + tenant ID, set the messaging endpoint, enable the Teams channel, build a manifest). Where an exact Azure UI label is given it may have moved or been renamed — follow the capability, not the literal string. The values CubePlex actually consumes — the app ID, app secret, tenant ID, and messaging endpoint path — are the only ones this guide can state with certainty, because they come from CubePlex's own code.
:::

## Step 1 — Register an Azure Bot

In the **Azure portal**, create an **Azure Bot** resource. During creation Azure provisions (or lets you supply) a **Microsoft App** — an Entra / Azure AD application identity for the bot. This is what gives you the credentials CubePlex needs.

Record three values as you go:

- **App ID** (the Microsoft App ID / client ID) — this is the bot's identity. CubePlex stores it as the account's external identifier, and it is the `recipient.id` Microsoft puts on every inbound activity, so it must match exactly.
- **App secret** (a client secret you generate for the app) — CubePlex uses it to obtain a Bot Framework token. Generate the secret and copy it immediately; Azure shows the secret value only once.
- **Tenant ID** — the Entra directory (tenant) ID the app lives in.

:::info 📸 Screenshot placeholder
**Capture:** The Azure portal "Create an Azure Bot" form, and the resulting resource's identity page showing where the Microsoft App ID and the option to create a client secret appear.
**Asset:** `/img/im/teams-azure-bot-create.png`
:::

:::info 📸 Screenshot placeholder
**Capture:** The app's "Certificates & secrets" view at the moment a new client secret is created, with the one-time secret value visible (redact before publishing).
**Asset:** `/img/im/teams-app-secret.png`
:::

## Step 2 — Set the messaging endpoint

In the Azure Bot resource's **configuration**, set the **messaging endpoint** to the inbound webhook on your CubePlex host:

```
https://<your-cubeplex-host>/api/v1/im/teams/messages
```

This is the exact path CubePlex listens on. Microsoft's Bot Framework service POSTs each Teams activity to this URL. The host must be internet-reachable over HTTPS (see the intro note) — Microsoft will not deliver to an unreachable or plain-HTTP endpoint.

You can set this endpoint before or after you bind in CubePlex, but the bot won't get any answers until both sides are in place: the endpoint must point here **and** the account must be bound and enabled in CubePlex (Step 5). CubePlex rejects activities for an unknown or disabled bot.

:::info 📸 Screenshot placeholder
**Capture:** The Azure Bot resource configuration page with the messaging endpoint field set to `https://<your-cubeplex-host>/api/v1/im/teams/messages`.
**Asset:** `/img/im/teams-messaging-endpoint.png`
:::

## Step 3 — Enable the Teams channel

A freshly registered Azure Bot isn't reachable from Teams until you add the **Microsoft Teams channel** to it. In the Azure Bot resource's **Channels** area, add and enable the Teams channel.

Without this, the bot exists but no Teams message ever reaches it.

:::info 📸 Screenshot placeholder
**Capture:** The Azure Bot "Channels" page with the Microsoft Teams channel added and showing as enabled / running.
**Asset:** `/img/im/teams-channel-enable.png`
:::

## Step 4 — Build and upload the Teams app manifest

To make the bot installable for users, package it as a **Teams app**. A Teams app is described by a **manifest** (a small JSON document plus icons) that declares, among other things, the **bot ID** — which must be the **App ID from Step 1** — so Teams knows which bot the app installs.

You can author the manifest in the **Teams Developer Portal** (or hand-write the manifest JSON and zip it with the icons). Set the bot in the manifest to your App ID, fill in the app name and icons, then upload / install the resulting app into Teams — either by sideloading it for yourself for testing, or publishing it to your organization's app catalog so others can install it.

:::caution Manifest field names are not verifiable from CubePlex
The exact manifest schema keys and the Developer Portal's field labels live on Microsoft's side and change between schema versions, so this guide can't state them as fixed strings. The one value CubePlex depends on is that the manifest's bot ID equals the **App ID** you bind in CubePlex (Step 5). Get that wrong and inbound activities arrive under a `recipient.id` CubePlex has no account for, and they are silently dropped.
:::

:::info 📸 Screenshot placeholder
**Capture:** The Teams Developer Portal manifest editor (or the manifest JSON) showing the bot configured with the App ID, plus the upload / install action.
**Asset:** `/img/im/teams-manifest.png`
:::

## Step 5 — Bind the bot in CubePlex

In your CubePlex workspace, open the **IM connectors** settings and connect a new Teams account. Provide:

| Field | Required | Notes |
|---|---|---|
| **App ID** | Yes | The Microsoft App ID from Step 1. Also serves as the account's external identifier and must match the `recipient.id` on inbound activities. |
| **App secret** | Yes | The client secret from Step 1. CubePlex uses it to obtain a Bot Framework token. |
| **Tenant ID** | Yes | The Entra directory (tenant) ID from Step 1. |
| **Run identity** | Yes | `self` (the bot runs as you) by default. Binding it to run as another user requires the **workspace admin** role. |

The delivery mode for Teams is always **webhook** — there is no choice to make; CubePlex sets it for you.

On binding, CubePlex validates the credentials by requesting a client-credentials token from Microsoft (`https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token`). If the App ID, secret, or tenant ID is wrong, the token request fails and binding is rejected with a "could not validate Teams bot credentials" error — fix the values in Azure and retry. The credentials are stored encrypted.

![CubePlex Teams account connection form](/img/im/teams-cubeplex-connect-form.png)

## Step 6 — Test it

Install the bot in Teams (Step 4) and message it — DM it directly or @-mention it in a channel where it's installed. The first time, CubePlex needs to know who you are.

Teams has no email-resolution path, so you link manually. Send the bot:

```
/link your@email.com
```

The bot replies with a confirmation URL of the form `https://<your-cubeplex-host>/im-link?token=...`. Open that link **while logged in to CubePlex**, and confirm. CubePlex checks that your logged-in email matches the claimed email and that you belong to the bot's workspace, then permanently links your Teams identity to your CubePlex account. See [Identity linking](./overview.md#identity-linking).

The linked email must already belong to a CubePlex account that is a member of the bot's workspace — linking connects an existing account, it doesn't create one or grant membership.

Once linked, your messages run as that user, with the same skills, memory, and tools you have in the web app, and the agent's reply posts back into the chat.

## Conversation commands

| Command | Effect |
|---|---|
| `/link <email>` | Link your Teams identity to your CubePlex account. |
| `/new` | Start a fresh conversation; your next message begins a new one. |
| `/reset` | Same as `/new`. |
| `新对话` | Same as `/new` (text form). |

`/new`, `/reset`, and `新对话` are equivalent. (The Chinese `绑定` alias for `/link` is Feishu-only.)

## How inbound messages are authenticated

Every Teams activity arrives at `POST /api/v1/im/teams/messages`. Before CubePlex does any work it:

1. Reads the bot ID from the activity's `recipient.id` and finds the matching bound account. An unknown bot ID is dropped.
2. Validates the **Azure Bot Framework JWT** carried in the request's `Authorization: Bearer …` header against the bot's token validator, including the activity's `serviceUrl`. A missing, malformed, or invalid token is rejected with `401`. This is what guarantees the request genuinely came from Microsoft's Bot Framework and not from anyone who guessed your endpoint URL.
3. Confirms the account is enabled, then resolves identity and runs the agent.

Because the endpoint is public, this JWT check is the security boundary for the Teams connector — there is no separate signing secret you configure, unlike Feishu's encrypt key.

## Rotating credentials

There is no in-place secret edit. To rotate the App secret (or change the App ID or tenant ID), **delete** the account in CubePlex and bind it again with the new values.
