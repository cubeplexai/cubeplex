---
sidebar_position: 2
title: Installing Connectors
---

# Installing Connectors

This guide walks you through adding MCP connectors to your workspace. You need **admin** permissions (org admin or workspace admin) to install connectors.

## Browse the catalog

1. Open the **MCP** page from your workspace sidebar (for workspace-level installs). Org admins install org-wide connectors from **Admin > MCP Connectors** (`/admin/mcp`).
2. You will see the connector catalog. The page splits into an **Installed** section (connectors already set up for your workspace) and an **Available** section (connectors you can add), each showing names, descriptions, and authentication requirements.

Each catalog entry shows its current status for your workspace:

- **Not installed** — the template is available but has not been set up yet.
- **Installed (org-wide)** — an org admin installed this connector for all workspaces.
- **Installed (workspace)** — this connector is installed in your workspace specifically.
- **Disabled** — an org-wide connector has been turned off for this workspace.

## Install with an API key

For connectors that use static credentials (like Tavily, Exa, or Jina AI):

1. Click the connector in the catalog.
2. Select **API Key** as the authentication method.
3. Paste your API key or token in the form field.
4. Click **Install**.

CubeBox encrypts the credential and stores it securely. The connector's tools become available to workspace members immediately.

:::tip Where to get API keys
Each connector's install form includes a link to the service's developer console where you can generate an API key.
:::

## Install with OAuth

For connectors that use OAuth (like GitHub, Notion, Slack, or Linear):

1. Click the connector in the catalog.
2. Select **OAuth** as the authentication method.
3. Click **Connect with &lt;provider&gt;**. A new window opens with the service's consent screen.
4. Grant the requested permissions.
5. The window returns you to CubeBox. The connector is now connected and active.

**What happens behind the scenes:** CubeBox uses PKCE (Proof Key for Code Exchange) for all OAuth flows. For services that support Dynamic Client Registration — DCR (Notion, Linear, Atlassian, Asana, Sentry, Intercom, Cloudflare), no pre-configuration is needed: CubeBox registers its own OAuth client with the service automatically the first time someone installs the connector. For services that do not support DCR (GitHub, Slack, Google Workspace), your system administrator must register an OAuth app in the vendor's developer console and load its client credentials into CubeBox (via environment variables and the catalog seeder) before the connector can complete an OAuth flow.

:::info 📸 Screenshot placeholder
**Capture:** A connector card mid-OAuth, showing the **Connect with &lt;provider&gt;** button and the "Waiting for authorization in the new window…" state.
**Asset:** `/img/mcp/oauth-connect.png`
:::

### User-scoped OAuth

Some connectors may be configured so that each user authorizes their own account. In this case, the connector is installed once by an admin, but each workspace member completes their own OAuth flow the first time they use it. The agent will prompt you to authorize if needed.

### Reconnecting expired tokens

OAuth tokens can expire. If a connector loses its authorization, its card on the **MCP** page shows a **Needs your credential** state with a **Re-authenticate** action. Click it to re-run the OAuth flow and restore access.

## Org-wide vs. workspace installs

| Scope | Who can install | Visible in |
|---|---|---|
| **Org-wide** | Org admin (via **Admin > MCP Connectors**) | All workspaces in the organization |
| **Workspace** | Workspace admin (via the workspace **MCP** page) | That workspace only |

- Org-wide installs are useful for connectors the whole team needs (e.g., a shared GitHub integration).
- Workspace installs are useful for project-specific tools or when different workspaces need different credentials.

## Disable or remove a connector

- **Disable an org-wide connector** for your workspace: open the workspace **MCP** page, find the connector, and turn it off. The connector remains installed org-wide but is hidden from your workspace.
- **Remove a workspace connector**: open the workspace **MCP** page, find the connector, and choose **Uninstall**. This deletes the install and its credentials.

## Verifying the install

After installation, the connector's tools should appear in your conversation. Start a new conversation and ask the agent to use the connector:

> Search GitHub for open issues in our repo.

If the connector is working, the agent will call its tools and return results. If something is wrong, you will see an error message indicating the issue (e.g., expired credentials, missing permissions).

## Next steps

- [Using Tools](./using-tools.md) — See how tools appear in conversations and how to interpret results.
- [MCP Tools Overview](./overview.md) — Review the connector lifecycle and available integrations.
