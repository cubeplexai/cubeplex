---
sidebar_position: 3
title: MCP Connector Management
---

# MCP Connector Management

MCP (Model Context Protocol) connectors let the agent call external APIs — databases, SaaS products, internal services, and more. As an admin, you manage the connector catalog, configure authentication, and control which workspaces have access.

Connector management happens at **Admin > MCP Connectors** (`/admin/mcp`).

## Key concepts

| Concept | Description |
|---|---|
| **Template** | A catalog connector definition that describes a service, its server URL, transport, and which authentication methods it supports. Think of it as the "app" in an app store. Templates are seeded from CubeBox's built-in catalog; they carry no tenant credentials and no tool list. |
| **Install** | A template activated for a specific org or workspace. Installing creates a concrete connector instance and triggers tool discovery against the server. |
| **Credential** | The secret attached to an install — an API token, bearer token, or OAuth grant — stored encrypted in the credential vault. |
| **Grant / workspace access** | Which workspaces (and their members) can use an org-wide install. Managed per-workspace on the install's **Workspaces** tab. |

## Manage the connector catalog

The catalog contains the connector templates available to your organization. The built-in templates (GitHub, Notion, Slack, Tavily, and the rest) ship with CubeBox and are loaded by the catalog seeder during deployment — you do not create or edit those by hand.

### Add a custom MCP server

For an MCP server that is not in the built-in catalog (for example, an internal service), register it directly:

1. Go to **Admin > MCP Connectors** (`/admin/mcp`).
2. Click **+ Add custom connector**.
3. Fill in the **Add custom MCP server** form:
   - **Name** and **Server URL**.
   - **Transport** (`streamable_http` or `sse`).
   - **Auth method** (OAuth, API token, or no auth) and **Credential scope** (organization-shared, per user, or none).
   - The **Credential** itself, when the auth method requires one.
4. Optionally click **Test connection** to confirm the server reachable and see the tool count.
5. Click **Create server**.

CubeBox does not ask you to list the server's tools — it discovers them automatically by calling the server after the install is created.

### Remove a connector

Uninstalling a connector removes that install and its credentials. Built-in catalog templates cannot be deleted from the UI; if a template is dropped from the built-in catalog it is marked deprecated by the seeder so existing installs keep working while new installs are blocked.

## Authentication

Connectors authenticate with external services in different ways. The auth method is fixed by the template; you supply the credential when you install.

### OAuth connectors

Most OAuth connectors support **Dynamic Client Registration (DCR)** — Notion, Linear, Atlassian, Asana, Sentry, Intercom, and Cloudflare. For these, no setup is required: CubeBox registers its own OAuth client with the service automatically the first time the connector is installed, then walks the installer through the vendor's consent screen.

A few OAuth connectors do **not** support DCR — **GitHub, Slack, and Google Workspace**. These require a pre-registered OAuth app:

1. An operator registers an OAuth app in the vendor's developer console, using redirect URI `${CUBEBOX_PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`.
2. The app's client ID and secret are placed in environment variables (`CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID` / `…__CLIENT_SECRET`) and loaded by the catalog seeder.

This is a deploy-time step, not something you enter in the admin UI. Until those credentials are loaded, the connector's OAuth flow cannot complete. See the operator runbook in `backend/docs/mcp_catalog_oauth.md` for details.

### API key / bearer token connectors

For connectors that authenticate with a static API token or bearer token (for example, the search connectors Tavily, Exa, and Jina AI), the token is requested in the install form. CubeBox encrypts it into the credential vault.

## Tool discovery & caching

Installing a connector (or saving its credential) runs **tool discovery**: CubeBox connects to the MCP server, lists its tools, and stores the tool list on the install. Agent runs use this cached list to register the connector's tools — they do not re-contact the server on every message, which keeps chat start fast even with several connectors enabled. Actually *calling* a tool always goes to the live server.

The cache refreshes automatically in the background when it is older than 24 hours (config key `mcp.tools_cache_ttl_hours`; set `0` to disable background refresh). If a server changed its tools and you don't want to wait, use **Retry discovery** on the install's detail page to refresh immediately.

## Install connectors

Connectors can be installed at the **organization level** (available to all workspaces) or at the **workspace level** (available only to that workspace).

### Install at the org level

1. Go to **Admin > MCP Connectors** (`/admin/mcp`).
2. Select a template from the catalog.
3. Click **Install**. Choose the **Workspace rollout**:
   - **Each workspace enables manually** — installs at the org level only; every workspace sees the connector but it stays disabled until each workspace turns it on.
   - **Enable for all workspaces** — installs and enables it for every existing workspace, and auto-enables it for new workspaces created later.
4. Complete the auth flow if required (OAuth consent or token entry).
5. The connector is now installed org-wide; control per-workspace access on its **Workspaces** tab.

### Install at the workspace level

Workspace-scoped installs are made by a workspace admin from the workspace **MCP** page (sidebar **MCP** item), not from the org admin area. Select a connector in the **Available** section, click **Connect**, and complete the auth flow. The install is private to that workspace.

## Grant workspace access

After installing a connector at the org level, you control which workspaces can use it.

1. Go to **Admin > MCP Connectors** (`/admin/mcp`) and select the installed connector.
2. Open the **Workspaces** tab. It shows a per-workspace **Enabled / Disabled** toggle and a summary (e.g. "3 of 8 workspaces enabled").
3. Toggle each workspace on or off.

Only members of enabled workspaces will see the connector's tools in their conversations.

:::info 📸 Screenshot placeholder
**Capture:** An org-wide connector's detail panel with the **Workspaces** tab open, showing several workspaces with Enabled/Disabled toggles and the `<enabled> of <total> workspaces enabled` summary.
**Asset:** `/img/mcp/admin-workspaces-tab.png`
:::

## Revoke access

To remove a workspace's access to a connector, toggle it off on the **Workspaces** tab. Conversations that were using the connector will no longer be able to call its tools in subsequent turns.
