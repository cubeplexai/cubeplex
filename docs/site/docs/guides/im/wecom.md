---
sidebar_position: 6
title: WeCom
---

# Connect a WeCom AI Bot

CubePlex connects to a WeCom (Enterprise WeChat) AI Bot over an outbound WebSocket. You do not
need a public callback URL, but the API service must be able to reach
`wss://openws.work.weixin.qq.com` on port 443.

## Create the bot

1. In the WeCom desktop client, open **Workbench → AI Bot → Create → Create manually**.
2. Choose **API mode**, then select **Use long connection**.
3. Copy the bot's displayed name and **Bot ID**, then reveal and copy the **Secret**. Store the
   Secret securely; WeCom may only show it once.
4. Save the bot.

![Placeholder for the WeCom AI Bot API-mode credential screen](/img/im/wecom-api-mode-placeholder.svg)

## Bind it to CubePlex

1. Open **Workspace settings → IM** and choose **Connect → WeCom**.
2. Complete the prerequisite checklist.
3. Enter the Bot ID, exact displayed bot name, and Secret, then select **Connect**. CubePlex uses
   the displayed name to remove the bot mention from group messages, including names with spaces.

CubePlex validates the credentials before storing them encrypted. A successful bind opens the
long connection immediately. WeCom permits one live consumer for a bot; connecting the same bot
from another product can disconnect CubePlex. If this happens, disable and re-enable the account
in CubePlex after stopping the competing client.

## Link your identity

WeCom callbacks do not provide an email address. Before the first agent request, send the bot:

```text
/link you@example.com
```

Open the returned link while signed in to CubePlex. The email must belong to a current member of
the bot's workspace. Membership is checked again for every message.

## Use the bot

- In a direct chat, send a message normally.
- In a group, mention the bot. The account's group routing setting controls whether each member
  receives an isolated conversation or the group shares one conversation.
- Send `/new`, `/reset`, or `新对话` to start a fresh conversation for the current scope.
- Send `/stop` to stop the currently active reply without clearing queued messages.
- Send `/steer <message>` to inject a follow-up instruction into the active reply (or start a new one if idle).

Replies stream into the original WeCom message when its reply window is available. Longer or
delayed runs fall back to a proactive final message.

Images, files, and videos you send in a **direct chat** are downloaded, decrypted, and handed to
the agent the same way a web upload is. Mixed image-and-text messages work in groups too. Voice
messages still arrive as WeCom's transcription only.

When the agent shows a file (`present_file`) or saves a deliverable (`save_artifact`), CubePlex
uploads it over the bot connection and sends a native WeCom image or file message. Files larger
than 20 MB, or a failed upload, fall back to a share link. Inbound download URLs expire after
five minutes; if the bot is already busy with another run past that window, the file is skipped
and the agent is told it was ignored.

## Rotate credentials

Delete the WeCom account in CubePlex, generate or copy the current Secret in WeCom, and connect
the bot again. Disabling closes the socket immediately; re-enabling opens it again without an API
restart.

## Current limits

This connector supports WeCom AI Bots only. It does not support personal WeChat, callback-mode
enterprise apps, QR-code setup, or interactive in-chat approval and question controls. Continue
those pending-input steps in the CubePlex web UI. WeCom itself only delivers standalone image,
file, and video callbacks in direct chats.
