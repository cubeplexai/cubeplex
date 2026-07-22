---
sidebar_position: 2
title: Enabling Connectors
---

# Enabling Connectors

This guide walks you through enabling MCP connectors in your workspace and connecting credentials. The flow is: browse the catalog → toggle a template on → connect the credential.

Org admins manage the global template catalog from **Admin > MCP Connectors**. Workspace admins enable templates and manage workspace credentials from the workspace **MCP** page.

## Browse the catalog

Open the **MCP** page from your workspace sidebar. It shows all templates visible to your workspace — global (built-in), org-scope templates your org admins have registered, and workspace-scope templates you have created.

Each row shows the template's name, description, and current state for your workspace:

| State | Meaning |
|---|---|
| Not enabled | The template is available but your workspace has not turned it on yet. |
| Enabled | This template is active in your workspace. |
| Disabled | An org admin has suspended this template org-wide. You cannot enable it while it is suspended. |

![Workspace MCP catalog with connected, disabled, and available templates](/img/mcp/workspace-catalog.png)

## Enable a template

Click the toggle next to any template to enable it for your workspace. The connector row is created automatically behind the scenes if this is the first workspace to use that template — you do not need to do anything extra.

Once enabled, the template's tools are available to workspace members as soon as a valid credential is connected.

## Connect credentials

Most templates require a credential before the agent can call their tools. The credential scope determines who provides it:

| Scope | Who sets it up | When to use it |
|---|---|---|
| **Org** | Org admins (via **Admin > MCP Connectors**) | Shared API key or service account used by all workspaces |
| **Workspace** | Workspace admins | This workspace needs its own API key or service account |
| **User** | Each individual user | Personal OAuth — each user connects their own account |

If an org-level credential already exists for a template, your workspace automatically inherits it when you enable the template and no further action is needed.

### Connect with an API key or bearer token

For connectors that use static credentials (like Tavily, Exa, or Jina AI):

1. Click the template in the catalog.
2. In the credential section, choose **Workspace** (or **User** if per-user keys are needed).
3. Paste the API key or token.
4. Click **Save credential**.

CubePlex encrypts the credential and stores it securely. The connector's tools become available to workspace members immediately.

:::tip Where to get API keys
Each template's setup form includes a link to the service's developer console where you can generate an API key.
:::

### Connect with OAuth

For connectors that use OAuth (like GitHub, Notion, Slack, or Linear):

1. Click the template in the catalog.
2. Choose the credential scope: **User** if you want each person to authorize their own account, or **Workspace** to use a shared service account.
3. Click **Sign in with &lt;provider&gt;**. A new window opens with the service's consent screen.
4. Grant the requested permissions.
5. The window closes and returns you to CubePlex. The credential is now connected.

**What happens behind the scenes:** CubePlex uses PKCE (Proof Key for Code Exchange) for all OAuth flows. For services that support Dynamic Client Registration (DCR) — Notion, Linear, Atlassian, Asana, Sentry, Intercom, Cloudflare — no pre-configuration is needed. For services that do not support DCR — GitHub, Slack, Google Workspace — your system administrator must register an OAuth app in the vendor's developer console before the OAuth flow can complete.

![MCP OAuth credential panel with the provider sign-in action](/img/mcp/oauth-connect.png)

### User-scoped OAuth

When a template is set to user-scope credentials, each workspace member completes their own OAuth flow the first time they use it. The agent will prompt you to authorize if your personal credential is missing or expired.

### Reconnecting expired tokens

OAuth tokens can expire. If a credential loses its authorization, the template card shows a **Needs your credential** state with a **Re-authenticate** action. Click it to re-run the OAuth flow and restore access.

## Register a custom workspace template

Workspace admins can register a custom MCP server that is only visible to their workspace. This is useful for internal tools or services not in the global catalog.

1. On the workspace **MCP** page, click **+ Add custom template**.
2. Fill in the form:
   - **Name** and **Server URL**.
   - **Transport** (`streamable_http` or `sse`).
   - **Supported auth methods**.
3. Click **Create template**.

The template appears in your workspace catalog immediately. Enable it and connect credentials as you would any other template.

### Promote a workspace template to org scope

If your custom template would be useful to other workspaces in your org, you can promote it to org scope. Promotion widens the template's visibility so all workspaces in the org can see and enable it.

1. Click the template in your catalog.
2. Click **Promote to org**. Confirm the dialog.

The template is now an org-scope template. Its source label changes from "Workspace" to "Org" in the catalog. Existing enablement and credentials are unaffected.

:::note
Promotion is immediate in v1 (no approval flow). Org admins can see all workspace-created templates in the admin catalog for governance.
:::

## Disable a connector for your workspace

To stop using a connector in your workspace without affecting other workspaces, toggle it off on the workspace **MCP** page. The connector identity remains in the org and other workspaces are unaffected. Toggling it back on restores your workspace's previous state.

To remove a workspace-level credential without disabling the template, open the template's credential panel and click **Remove credential**. The org credential (if any) continues to be used unless you explicitly switch away from it.

## Verifying the connector

After enabling a connector and connecting credentials, its tools should appear in your conversations. Start a new conversation and ask the agent to use the connector:

> Search GitHub for open issues in our repo.

If the connector is working, the agent will call its tools and return results. If something is wrong, you will see an error message indicating the issue (e.g., expired credentials, missing permissions).

## Next steps

- [Using Tools](./using-tools.md) — See how tools appear in conversations and how to interpret results.
- [MCP Tools Overview](./overview.md) — Review the connector states and available integrations.
