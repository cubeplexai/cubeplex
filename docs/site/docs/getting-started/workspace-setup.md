---
sidebar_position: 3
title: Workspace Setup
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Workspace Setup

A workspace is where your team collaborates with AI. This guide walks you through creating a workspace, inviting members, and configuring the essentials.

## Prerequisites

- You have a CubeBox account (see [Quick Start](./quick-start.md)).
- You have **org admin** or **owner** permissions to create workspaces. Workspace members can skip to [Join an existing workspace](#join-an-existing-workspace).

## Create a workspace

1. Open the **workspace switcher** at the top of the sidebar and click **New workspace** (this opens the **Workspaces** page).
2. Fill in the **Create workspace** form with a name and confirm.

You are automatically assigned the **workspace admin** role in the new workspace.

## Invite members

1. Navigate to your workspace, open **Settings** in the sidebar, then select the **Members** tab.
2. Click **Invite member**.
3. Enter the user's email address and choose a role:
   - **Admin** — can manage workspace settings, tools, skills, and members.
   - **Member** — can chat, use tools, and install personal skills.
4. The invited user receives an email with a link to accept.

## Join an existing workspace

<Tabs groupId="deploy-mode">
<TabItem value="cloud" label="Cloud">

Click the invite link you received by email. If you do not have a CubeBox account yet, you will be prompted to create one first.

</TabItem>
<TabItem value="self-hosted" label="Self-hosted">

Click the invite link, or ask your admin to add you directly from the workspace members page. Depending on your instance's registration policy, you may need admin approval.

</TabItem>
</Tabs>

## Configure models

Before your team can chat, at least one AI model must be enabled at the organization level.

1. Open the **Admin** area from the avatar menu (**Admin panel**), then go to **Models > Model Providers**.
2. Add a provider (Anthropic, OpenAI, or a custom endpoint) and enter your API key.
3. Enable the specific models you want available.

Models enabled at the org level are available across all workspaces.

:::info 📸 Screenshot placeholder
**Capture:** The Admin **Models > Model Providers** page with a provider being added — show the provider type selector, the API-key field, and the per-model enable toggles.
**Asset:** `/img/admin/model-providers.png`
:::

## Install MCP tools

MCP connectors let the agent interact with external services.

1. Open the **MCP** page from the workspace sidebar.
2. Browse the connector catalog and click **Install** on the one you want.
3. Provide authentication credentials (a static credential, OAuth, or none, depending on the connector).
4. Choose which workspace members should have access via **Grants**.

Once installed and granted, the tools are available to the agent in conversations. See the [MCP Tools guide](../guides/mcp/overview.md) for a full walkthrough.

## Install skills

Skills extend what the agent can do. To add skills to your workspace:

1. Open the **Skills** page from the workspace sidebar, or use the `/` command inside a conversation to discover skills.
2. Browse built-in skills, org-uploaded skills, or remote registries.
3. Click **Install** to make a skill available in the workspace.

See the [Skills guide](../guides/skills/overview.md) for details.

## Set up automation (optional)

If you want the agent to run tasks on a schedule or in response to events:

1. Open the **Scheduled Tasks** page (for cron, interval, or one-shot runs) or the **Triggers** page (for webhook URLs that start an agent run) from the workspace sidebar.
2. Create a scheduled task or an event trigger.
3. Configure the prompt, model, and any tools the automated run should use.

See the [Automation guides](../guides/automation/scheduled-tasks.md) for details.

## Next steps

Your workspace is ready. Here is where to go from here:

- [Conversations](../guides/conversations/basics.md) — Start chatting with full context on features.
- [Memory](../guides/memory/overview.md) — Set up shared memory so the agent remembers team knowledge.
- [Members & roles](../admin/members.md) — Manage organization-wide membership.
- [Cost tracking](../admin/cost-tracking.md) — Monitor API usage across your organization.
