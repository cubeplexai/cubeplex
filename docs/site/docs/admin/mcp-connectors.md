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
| **Connector identity** | An org-owned entry created when an admin adds a connector from the catalog. It groups the connector's credentials and per-workspace state under a single identity that can be shared across workspaces. |
| **Credential** | The secret attached to a connector — an API token, bearer token, or OAuth grant — stored encrypted in the credential vault. Credentials can be scoped to the organization, a specific workspace, or an individual user, all linked to the same connector identity. |
| **Workspace enablement** | Controls which workspaces can use a connector. Managed per-connector on the connector's **Workspaces** tab. |

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

CubeBox does not ask you to list the server's tools — it discovers them automatically by calling the server after the connector is created.

### Remove a connector

Removing a connector deletes the connector identity and its credentials. Built-in catalog templates cannot be deleted from the UI; if a template is dropped from the built-in catalog it is marked deprecated by the seeder so existing connectors keep working while new ones are blocked.

## Authentication

Connectors authenticate with external services in different ways. The auth method is fixed by the template; you supply the credential when you add the connector.

### OAuth connectors

Most OAuth connectors support **Dynamic Client Registration (DCR)** — Notion, Linear, Atlassian, Asana, Sentry, Intercom, and Cloudflare. For these, no setup is required: CubeBox registers its own OAuth client with the service automatically the first time the connector is added, then walks the admin through the vendor's consent screen.

A few OAuth connectors do **not** support DCR — **GitHub, Slack, and Google Workspace**. These require a pre-registered OAuth app:

1. An operator registers an OAuth app in the vendor's developer console, using redirect URI `${CUBEBOX_PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`.
2. The app's client ID and secret are placed in environment variables (`CUBEBOX_MCP_OAUTH__<SLUG>__CLIENT_ID` / `…__CLIENT_SECRET`) and loaded by the catalog seeder.

This is a deploy-time step, not something you enter in the admin UI. Until those credentials are loaded, the connector's OAuth flow cannot complete. See the operator runbook in `backend/docs/mcp_catalog_oauth.md` for details.

### API key / bearer token connectors

For connectors that authenticate with a static API token or bearer token (for example, the search connectors Tavily, Exa, and Jina AI), the token is requested when adding the connector. CubeBox encrypts it into the credential vault.

## Tool discovery & caching

Adding a connector (or saving its credential) runs **tool discovery**: CubeBox connects to the MCP server, lists its tools, and stores the tool list on the connector identity. Agent runs use this cached list to register the connector's tools — they do not re-contact the server on every message, which keeps chat start fast even with several connectors enabled. Actually *calling* a tool always goes to the live server.

The cache refreshes automatically in the background when it is older than 24 hours (config key `mcp.tools_cache_ttl_hours`; set `0` to disable background refresh). If a server changed its tools and you don't want to wait, use **Retry discovery** on the connector's detail page to refresh immediately.

## Add connectors

Connectors are added at the **organization level**. The connector identity is org-owned and can be enabled in any workspace.

### Add a connector to the organization

1. Go to **Admin > MCP Connectors** (`/admin/mcp`).
2. Select a template from the catalog.
3. Click **Add**. Choose the **Workspace rollout**:
   - **Each workspace enables manually** — creates the connector identity at the org level; every workspace sees the connector but it stays disabled until each workspace turns it on.
   - **Enable for all workspaces** — creates the connector and enables it for every existing workspace, and auto-enables it for new workspaces created later.
4. Complete the auth flow if required (OAuth consent or token entry).
5. The connector is now available org-wide; control per-workspace access on its **Workspaces** tab.

### Workspace-level setup

From the workspace **MCP** page (sidebar **MCP** item), workspace admins can enable an org-owned connector for their workspace and choose which credential to use: the org-level credential, a workspace-specific credential, or a per-user OAuth connection. This does not create a separate connector identity — the workspace is opting into the existing org-owned connector.

## Grant workspace access

After adding a connector to the organization, you control which workspaces can use it.

1. Go to **Admin > MCP Connectors** (`/admin/mcp`) and select the connector.
2. Open the **Workspaces** tab. It shows a per-workspace **Enabled / Disabled** toggle and a summary (e.g. "3 of 8 workspaces enabled").
3. Toggle each workspace on or off.

Only members of enabled workspaces will see the connector's tools in their conversations.

:::info 📸 Screenshot placeholder
**Capture:** An org-wide connector's detail panel with the **Workspaces** tab open, showing several workspaces with Enabled/Disabled toggles and the `<enabled> of <total> workspaces enabled` summary.
**Asset:** `/img/mcp/admin-workspaces-tab.png`
:::

## Revoke access

To remove a workspace's access to a connector, toggle it off on the **Workspaces** tab. Conversations that were using the connector will no longer be able to call its tools in subsequent turns.
