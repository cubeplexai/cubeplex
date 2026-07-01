---
sidebar_position: 1
title: Quick Start
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Quick Start

Get from zero to your first AI conversation in a few minutes.

## 1. Create your account

<Tabs groupId="deploy-mode">
<TabItem value="cloud" label="Cloud">

1. Go to [cubebox.ai](https://cubebox.ai) and click **Sign up**.
2. Enter your email and password (or use a social login).
3. If your instance has email verification enabled, enter the one-time code sent to your inbox to confirm your address.
4. Finish the **onboarding** wizard: name your organization, choose a slug, and create your first workspace.

</TabItem>
<TabItem value="self-hosted" label="Self-hosted">

1. Open the CubeBox URL your administrator provided.
2. Register with your email and password. If email verification is enabled, enter the one-time code sent to your inbox.
3. If you are the **first user**, the onboarding wizard has you name the organization, choose a slug, and create your first workspace. You become the organization's owner.
4. Otherwise you join the existing organization as a member and create your own personal workspace through the same wizard.

</TabItem>
</Tabs>

## 2. Select a model

Before chatting, make sure at least one AI model is available.

- **Org owners/admins**: Open the **Admin** area and go to **Models > Model Providers**. Add a provider (e.g., Anthropic, OpenAI) with your API key, then enable the models you want your team to use.
- **Members**: You will see whichever models your admin has enabled. No setup needed.

## 3. Start a conversation

1. Click **New conversation** (or press the keyboard shortcut shown in the sidebar).
2. Pick a model from the model selector at the top of the chat input.
3. Type a message and press **Send**.

The agent responds with text, and may also produce tool calls, code execution results, or artifacts depending on the model and available tools.

:::info 📸 Screenshot placeholder
**Capture:** The chat view mid-conversation — show the model selector on the chat input, the attachment icon, and a streaming agent response.
**Asset:** `/img/getting-started/first-conversation.png`
:::

### Try attaching a file

Click the attachment icon in the chat input to upload a document, image, or code file. The agent will incorporate the file content into its response.

### Try an artifact

Ask the agent to create something concrete — for example:

> Write a Python script that converts CSV to JSON.

If the agent generates a deliverable, it appears as an **artifact** you can preview, copy, or download.

## 4. Explore further

You now have a working CubeBox setup. Here is where to go next:

- [Core Concepts](./core-concepts.md) — Understand conversations, skills, memory, MCP tools, and automation.
- [Workspace Setup](./workspace-setup.md) — Invite team members and configure workspace settings.
- [Conversations guide](../guides/conversations/basics.md) — Deep dive into chat features.
- [Skills guide](../guides/skills/overview.md) — Install skills to extend your agent.
