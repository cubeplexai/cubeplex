---
sidebar_position: 1
title: MCP Tools Overview
---

# MCP Tools Overview

MCP (Model Context Protocol) connectors let the CubeBox agent call external services during a conversation. Instead of being limited to its own knowledge, the agent can search the web, read GitHub issues, query databases, post Slack messages, and more — all in real time.

## How it works

CubeBox organizes MCP tools into a five-stage lifecycle:

1. **Templates (Catalog)** — A system-wide catalog of available connector definitions. Each template describes a service (for example, "GitHub" or "Slack") and how to authenticate with it. Admins manage the catalog.
2. **Connector identity** — When an admin adds a connector from the catalog, they create an org-owned connector identity. This identity is shared across workspaces and groups credentials and workspace state under a single entry.
3. **Workspace enablement** — Each workspace opts into the connectors it needs. Enabling a connector in a workspace does not create a new identity — the workspace references the existing org-owned connector.
4. **Credential source** — Credentials can exist at org, workspace, or user scope for the same connector. Each workspace chooses which credential to use: the org-level credential, a workspace-specific credential, or per-user OAuth. There is no implicit fallback between scopes — each workspace explicitly selects its credential source.
5. **Active tools** — Once a connector is enabled and has a valid credential, its tools become available to workspace members. The agent can call them whenever they are relevant to the conversation.

You do not need to understand the MCP protocol itself. From your perspective, connectors are just tools the agent can use.

## Available connectors

CubeBox ships with templates for a growing list of services:

| Category | Connectors |
|---|---|
| **Development** | GitHub, Linear, Sentry, Atlassian Rovo (Jira, Confluence, Bitbucket, Compass) |
| **Productivity** | Notion, Asana, Google Workspace, Slack, Intercom |
| **Web search** | Tavily, Exa, Jina AI, WebTools |
| **Infrastructure** | Cloudflare (API, Workers, Observability, Logs, Radar) |
| **Knowledge** | Microsoft Learn |

Your admin may have additional connectors available. Open the **MCP** page in your workspace sidebar to see what is enabled and what is available.

:::info 📸 Screenshot placeholder
**Capture:** The workspace **MCP** page showing the **Enabled** and **Available** connector sections, with at least one connector in each (one ready, one showing a "Connect" action).
**Asset:** `/img/mcp/workspace-mcp-page.png`
:::

## Authentication modes

Different connectors use different authentication methods:

- **OAuth** — You authorize CubeBox to access the service on your behalf. The connector walks you through the vendor's consent screen. Most connectors (Notion, Linear, Atlassian, Asana, Sentry, Intercom, Cloudflare) handle this automatically; a few (GitHub, Slack, Google Workspace) require your administrator to pre-register an OAuth app first.
- **API key** — You or your admin provides a static API key. Common for search connectors like Tavily and Exa.
- **Bearer token** — A pre-issued token, similar to an API key. Used by connectors that issue long-lived access tokens.

As a workspace member, you typically do not need to worry about authentication — your admin handles it when adding the connector. For connectors with user-scoped credentials (like personal OAuth), you will be prompted to authorize when you first use the connector.

## Tool citations

When the agent uses an MCP tool in its response, CubeBox shows a **citation** indicating which connector provided the information and what source it came from. This helps you verify the agent's claims and trace data back to the original service.

## Scoping

Connector identities are org-owned. An org admin adds a connector once, and it becomes available for any workspace to enable. Workspace admins can enable or disable the connector for their workspace and choose which credential source to use (org credential, workspace credential, or per-user OAuth).

## Next steps

- [Installing Connectors](./installing-connectors.md) — Enable a connector for your workspace.
- [Using Tools](./using-tools.md) — Learn how the agent uses tools during conversations.
