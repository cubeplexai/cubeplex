---
sidebar_position: 3
title: MCP Connector Management
---

# MCP Connector Management

MCP (Model Context Protocol) connectors let the agent call external APIs — databases, SaaS products, internal services, and more. As an org admin, you manage the connector catalog: which templates are visible to workspaces, which have org-level credentials, and what happens when a connector needs to be suspended or cleaned up.

Connector management happens at **Admin > MCP Connectors** (`/admin/mcp`).

## Key concepts

| Concept | Description |
|---|---|
| **Template** | A connector definition that describes a service — its name, server URL, transport, and which authentication methods it supports. Templates exist at three scopes: global (built-in catalog), org (you create these), and workspace (workspace admins create these; you can see and disable them for governance). |
| **Connector row** | Behind-the-scenes infrastructure created automatically (lazily) the first time a workspace enables a template. Holds the tools cache, OAuth client identity, and shared config. You do not create or delete these directly. |
| **Credential** | An API token, bearer token, or OAuth grant — stored encrypted. Credentials can be scoped to the org, a specific workspace, or an individual user. |
| **Enable / Disable** | A reversible suspend at the org level. Disabled templates are hidden from workspace catalogs and excluded from the agent runtime. Existing state rows and credentials are preserved; re-enabling restores everything. |
| **Purge** | A hard cleanup: deletes the connector row, all its state rows across workspaces, and all its credentials. The template stays in the catalog and can be enabled again from zero. This is the only destructive lifecycle action. |
| **Distribute** | A dialog to push a template to workspaces: two checkboxes, both on by default. Never overwrites an existing per-workspace choice. |

## The admin catalog

The catalog at **Admin > MCP Connectors** shows all templates visible to your org — global (built-in), org-scope custom templates you have created, and workspace-scope templates your workspace admins have registered (for governance visibility).

:::info 📸 Screenshot placeholder
**Capture:** The admin catalog page showing the template list with filter chips (In use, Needs attention, Org credential, All) and at least two templates — one with a "Disabled" badge and one showing "3 workspaces enabled".
**Asset:** `/img/mcp/admin-catalog.png`
:::

### Filter chips

Use the filter chips at the top of the catalog to answer specific questions:

| Filter | What it shows | The question it answers |
|---|---|---|
| **In use** *(default)* | Templates that have a connector row (at least one workspace has engaged them) | "What am I actively managing?" |
| **Needs attention** | Templates with problems: expired org credential, OAuth pending, or discovery failure | "What is broken — my to-do queue" |
| **Org credential** | Templates where an org-level credential exists | "Which credential lifecycles are my responsibility?" |
| **All** | Every template visible to your org | Browsing the full catalog |

A **source** dropdown lets you narrow further to: global catalog, org-created custom templates, or workspace-created templates.

## Distribute a template to workspaces

Distributing pushes a template to workspaces that have not yet made a decision about it.

1. In the catalog, click the template row.
2. Click **Distribute**.
3. The dialog shows two checkboxes (both on by default):
   - **Enable for existing workspaces that have not yet decided** — inserts an enabled state into workspaces that have no state row for this template. Workspaces that have already explicitly enabled or disabled the template are not touched.
   - **Auto-enable for future workspaces** — new workspaces that join the org will automatically have this template enabled.
4. Confirm. CubePlex performs the fan-out immediately.

:::info 📸 Screenshot placeholder
**Capture:** The Distribute dialog for a template, showing both checkboxes checked (default state) and the count of workspaces that will be affected.
**Asset:** `/img/mcp/admin-distribute-dialog.png`
:::

## Disable a template

Disabling is a **reversible suspend**. It hides the template from all workspace catalogs and vetoes its tools at agent runtime. Existing workspace state rows and credentials are preserved — re-enabling restores everything as it was.

1. In the catalog, click the template row.
2. Click **Disable**. Confirm the dialog.
3. The template gains a **Disabled** badge in the catalog. Workspaces can no longer enable it and the agent can no longer call its tools.

To re-enable: click the template row, click **Re-enable**. The badge is removed and prior state rows and credentials become active again immediately.

## Purge a connector (danger zone)

Purge is a **hard cleanup** and cannot be undone. Use it when you want to remove all traces of a connector's usage from the org — not just suspend it.

What purge deletes:
- The connector row (the shared infrastructure object)
- All state rows across every workspace
- All credentials at every scope (org, workspace, and user)

What purge keeps:
- The template itself — it remains in the catalog and can be enabled again from zero.

To purge:
1. In the catalog, click the template row.
2. Scroll to the **Danger zone** section.
3. Click **Purge connector**. The confirmation dialog lists exactly what will be deleted (connector row, N state rows, M credentials).
4. Confirm. The deletion is immediate and irreversible.

:::caution
Purge is the right action when you are decommissioning a connector permanently. If you only want to stop workspaces from using it temporarily, use **Disable** instead.
:::

## Create an org-scope custom template

For a service not in the built-in catalog (for example, an internal tool), register it as an org-scope template. Once created, the template appears in the catalog and any workspace in your org can enable it.

1. Go to **Admin > MCP Connectors** and click **+ Add custom template**.
2. Fill in the form:
   - **Name** and **Server URL**.
   - **Transport** (`streamable_http` or `sse`).
   - **Supported auth methods** — one or more of: OAuth, API token / bearer token, No auth.
3. Click **Create template**.

The template now appears in the catalog with source **Org**. Workspaces can enable it immediately; you do not need to distribute it first, though you can.

:::info
CubePlex discovers the template's tools automatically the first time a workspace enables it — you do not list tools manually.
:::

## Authentication

### OAuth connectors

Most OAuth connectors support **Dynamic Client Registration (DCR)** — Notion, Linear, Atlassian, Asana, Sentry, Intercom, and Cloudflare. For these, no setup is required: CubePlex registers its own OAuth client with the service automatically when the first credential is created.

A few OAuth connectors do **not** support DCR — **GitHub, Slack, and Google Workspace**. These require a pre-registered OAuth app:

1. An operator registers an OAuth app in the vendor's developer console, using redirect URI `${CUBEPLEX_PUBLIC_BASE_URL}/api/v1/oauth/mcp/callback`.
2. The app's client ID and secret are placed in environment variables (`CUBEPLEX_MCP_OAUTH__<SLUG>__CLIENT_ID` / `…__CLIENT_SECRET`) and loaded by the catalog seeder.

This is a deploy-time step, not something you enter in the admin UI. Until those credentials are loaded, the connector's OAuth flow cannot complete. See the operator runbook in `backend/docs/mcp_catalog_oauth.md` for details.

### API key / bearer token connectors

For connectors that authenticate with a static API token or bearer token (for example, the search connectors Tavily, Exa, and Jina AI), the credential is provisioned when creating an org-level credential for the template. CubePlex encrypts it into the credential vault.

### Mixed auth on one template

A template can carry both OAuth and static credentials at the same time — for example, workspace A's users each complete their own OAuth flow while workspace B uses a shared service-account token. The auth method is chosen when each credential is provisioned, not fixed at the template level.

## Tool discovery and caching

When a workspace first enables a template (creating the connector row), CubePlex connects to the MCP server, lists its tools, and stores the tool list on the connector row. Agent runs use this cached list — they do not re-contact the server on every message, which keeps chat startup fast even with many connectors active. Actually calling a tool always goes to the live server.

The cache refreshes automatically in the background when it is older than 24 hours (config key `mcp.tools_cache_ttl_hours`; set `0` to disable background refresh). If a server changed its tools and you do not want to wait, use **Retry discovery** on the template's detail page to refresh immediately.

## Workspace state visibility

Each template's detail panel shows a **Workspaces** tab: a per-workspace list of whether the template is enabled or not, plus the credential source in use. You can see this for any template that has a connector row (i.e., has been engaged by at least one workspace).

![WebTools template detail with the Workspaces tab showing enabled state and credential source labels](/img/mcp/admin-workspaces-tab.png)

Individual workspace enable/disable toggles are managed by workspace admins from the workspace **MCP** page. Org admins can see the state but manage it only via the **Distribute** dialog or by using **Disable** to suspend the template org-wide.
