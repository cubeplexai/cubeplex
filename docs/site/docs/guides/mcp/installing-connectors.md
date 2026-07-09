---
sidebar_position: 2
title: Installing Connectors
---

# Installing Connectors

This guide walks you through enabling MCP connectors in your workspace. Org admins add connectors to the organization; workspace admins enable them and choose a credential source.

## Browse the catalog

1. Open the **MCP** page from your workspace sidebar to see connectors available to your workspace. Org admins add new connectors from **Admin > MCP Connectors** (`/admin/mcp`).
2. You will see the connector catalog. The page splits into an **Enabled** section (connectors active in your workspace) and an **Available** section (org connectors you can enable), each showing names, descriptions, and authentication requirements.
3. Workspace admins can also click **Add custom connector** from the workspace MCP page to register a custom MCP server only for that workspace.

Each catalog entry shows its current status for your workspace:

- **Available** — the connector exists in the org but has not been enabled in this workspace.
- **Enabled** — this connector is active in your workspace.
- **Disabled** — the connector has been turned off for this workspace.

## Choose a credential policy

The **Credential policy** section controls which saved credential the workspace uses for a connector:

- **Organization** — use the org credential managed by org admins.
- **Workspace** — store one credential for this workspace. Use this when the workspace needs its own API key or service account.
- **User** — each user connects their own account. Use this for personal OAuth access or per-user API credentials.
- **None** — no credential is used. This option is only valid for connectors whose authentication method is **No auth**; API key and OAuth connectors cannot use it.

If a saved credential already exists for a policy option, the workspace page marks that option as available.

When a workspace enables an org-owned connector that already has an org credential, CubeBox selects **Organization** by default. You can switch to **Workspace** or **User** later if the workspace needs a narrower credential.

## Enable with an API key

For connectors that use static credentials (like Tavily, Exa, or Jina AI):

1. Click the connector in the catalog.
2. Select **API Key** as the authentication method.
3. Choose the credential source: use the org-level credential (if one exists), provide a workspace-specific credential, or enter your own.
4. Click **Enable**.

CubeBox encrypts the credential and stores it securely. The connector's tools become available to workspace members immediately.

:::tip Where to get API keys
Each connector's setup form includes a link to the service's developer console where you can generate an API key.
:::

## Enable with OAuth

For connectors that use OAuth (like GitHub, Notion, Slack, or Linear):

1. Click the connector in the catalog.
2. Select **OAuth** as the authentication method.
3. Click **Connect with &lt;provider&gt;**. A new window opens with the service's consent screen.
4. Grant the requested permissions.
5. The window returns you to CubeBox. The connector is now enabled and active.

**What happens behind the scenes:** CubeBox uses PKCE (Proof Key for Code Exchange) for all OAuth flows. For services that support Dynamic Client Registration — DCR (Notion, Linear, Atlassian, Asana, Sentry, Intercom, Cloudflare), no pre-configuration is needed: CubeBox registers its own OAuth client with the service automatically the first time the connector is added. For services that do not support DCR (GitHub, Slack, Google Workspace), your system administrator must register an OAuth app in the vendor's developer console and load its client credentials into CubeBox (via environment variables and the catalog seeder) before the connector can complete an OAuth flow.

:::info 📸 Screenshot placeholder
**Capture:** A connector card mid-OAuth, showing the **Connect with &lt;provider&gt;** button and the "Waiting for authorization in the new window…" state.
**Asset:** `/img/mcp/oauth-connect.png`
:::

### User-scoped OAuth

Some connectors may be configured so that each user authorizes their own account. In this case, the connector identity is created once by an admin, but each workspace member completes their own OAuth flow the first time they use it. The agent will prompt you to authorize if needed.

### Reconnecting expired tokens

OAuth tokens can expire. If a connector loses its authorization, its card on the **MCP** page shows a **Needs your credential** state with a **Re-authenticate** action. Click it to re-run the OAuth flow and restore access.

## Connector identity vs. workspace enablement

| Layer | Who manages it | What it controls |
|---|---|---|
| **Connector identity** | Org admin (via **Admin > MCP Connectors**) | Creates the org-owned connector, sets up org-level credentials |
| **Workspace enablement** | Workspace admin (via the workspace **MCP** page) | Enables the connector for this workspace, selects the credential source |

- The connector identity is shared across workspaces — there is one per connector per org.
- Each workspace chooses its own credential source: use the org credential, provide a workspace-specific credential, or have each user connect their own account.
- Removing a workspace's credential does not affect other workspaces using the same connector.

## Disable or remove a connector

- **Disable a connector** for your workspace: open the workspace **MCP** page, find the connector, and turn it off. The connector identity remains in the org but is hidden from your workspace.
- **Remove a workspace credential**: open the workspace **MCP** page, find the connector, and remove its workspace-level credential. This does not affect other workspaces using the same connector or the org-level credential.

## Verifying the connector

After enabling a connector, its tools should appear in your conversation. Start a new conversation and ask the agent to use the connector:

> Search GitHub for open issues in our repo.

If the connector is working, the agent will call its tools and return results. If something is wrong, you will see an error message indicating the issue (e.g., expired credentials, missing permissions).

## Next steps

- [Using Tools](./using-tools.md) — See how tools appear in conversations and how to interpret results.
- [MCP Tools Overview](./overview.md) — Review the connector lifecycle and available integrations.
