---
sidebar_position: 2
title: Installing Connectors
---

# Installing Connectors

This guide walks you through adding MCP connectors to your workspace. You need **admin** permissions (org admin or workspace admin) to install connectors.

## Browse the catalog

1. Go to **Settings > MCP Connectors** (for workspace-level installs) or **Admin > MCP** (for org-wide installs).
2. You will see the connector catalog — a list of available templates with their names, descriptions, and authentication requirements.

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
3. Click **Authorize**. You will be redirected to the service's consent screen.
4. Grant the requested permissions.
5. You are redirected back to CubeBox. The connector is now installed and active.

**What happens behind the scenes:** CubeBox uses PKCE (Proof Key for Code Exchange) for all OAuth flows. For services that support Dynamic Client Registration (Notion, Linear, Asana, Sentry, Intercom, Cloudflare), no pre-configuration is needed. For services that require a pre-registered OAuth app (GitHub, Slack, Google Workspace), your system administrator must configure the OAuth client credentials before the connector appears in the catalog.

### User-scoped OAuth

Some connectors may be configured so that each user authorizes their own account. In this case, the connector is installed once by an admin, but each workspace member completes their own OAuth flow the first time they use it. The agent will prompt you to authorize if needed.

### Reconnecting expired tokens

OAuth tokens can expire. If a connector loses its authorization, you will see a **Reconnect** prompt the next time the agent tries to use that connector. Click it to re-authorize.

## Org-wide vs. workspace installs

| Scope | Who can install | Visible in |
|---|---|---|
| **Org-wide** | Org admin (via **Admin > MCP**) | All workspaces in the organization |
| **Workspace** | Workspace admin (via workspace **Settings > MCP Connectors**) | That workspace only |

- Org-wide installs are useful for connectors the whole team needs (e.g., a shared GitHub integration).
- Workspace installs are useful for project-specific tools or when different workspaces need different credentials.

## Disable or remove a connector

- **Disable an org-wide connector** for your workspace: go to workspace **Settings > MCP Connectors**, find the connector, and toggle it off. The connector remains installed org-wide but is hidden from your workspace.
- **Remove a workspace connector**: go to workspace **Settings > MCP Connectors**, find the connector, and click **Remove**. This deletes the install and its credentials.

## Verifying the install

After installation, the connector's tools should appear in your conversation. Start a new conversation and ask the agent to use the connector:

> Search GitHub for open issues in our repo.

If the connector is working, the agent will call its tools and return results. If something is wrong, you will see an error message indicating the issue (e.g., expired credentials, missing permissions).

## Next steps

- [Using Tools](./using-tools.md) — See how tools appear in conversations and how to interpret results.
- [MCP Tools Overview](./overview.md) — Review the connector lifecycle and available integrations.
