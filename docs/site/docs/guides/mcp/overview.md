---
sidebar_position: 1
title: MCP Tools Overview
---

# MCP Tools Overview

MCP (Model Context Protocol) connectors let the CubeBox agent call external services during a conversation. Instead of being limited to its own knowledge, the agent can search the web, read GitHub issues, query databases, post Slack messages, and more — all in real time.

## How it works

CubeBox organizes MCP tools into a simple lifecycle:

1. **Templates (Catalog)** — A system-wide catalog of available connector definitions. Each template describes a service (for example, "GitHub" or "Slack") and how to authenticate with it. Admins manage the catalog.
2. **Connector identity** — When an org admin adds a template to the organization, CubeBox creates one connector identity for that service inside the org. This identity defines the MCP server URL, transport, tool namespace, and discovered tools.
3. **Credential source** — Org admins, workspace admins, or users can provide credentials for the same connector identity. A workspace chooses whether calls use the organization credential, a workspace credential, or each user's own credential.
4. **Active tools** — Once a workspace enables the connector and has the selected credential source, the agent can call the connector's tools whenever they are relevant to the conversation.

You do not need to understand the MCP protocol itself. From your perspective, connectors are just tools the agent can use.

## Available connectors

CubeBox ships with templates for a growing list of services:

| Category           | Connectors                                                                    |
| ------------------ | ----------------------------------------------------------------------------- |
| **Development**    | GitHub, Linear, Sentry, Atlassian Rovo (Jira, Confluence, Bitbucket, Compass) |
| **Productivity**   | Notion, Asana, Google Workspace, Slack, Intercom                              |
| **Web search**     | Tavily, Exa, Jina AI, WebTools                                                |
| **Infrastructure** | Cloudflare (API, Workers, Observability, Logs, Radar)                         |
| **Knowledge**      | Microsoft Learn                                                               |

Your admin may have additional connectors available. Open the **MCP** page in your workspace sidebar to see what is installed and what is available to add.

:::info 📸 Screenshot placeholder
**Capture:** The workspace **MCP** page showing the **Installed** and **Available** connector sections, with at least one connector in each (one ready, one showing a "Connect" action).
**Asset:** `/img/mcp/workspace-mcp-page.png`
:::

## Authentication modes

Different connectors use different authentication methods:

- **OAuth** — You authorize CubeBox to access the service on your behalf. The connector walks you through the vendor's consent screen. Most connectors (Notion, Linear, Atlassian, Asana, Sentry, Intercom, Cloudflare) handle this automatically; a few (GitHub, Slack, Google Workspace) require your administrator to pre-register an OAuth app first.
- **API key** — You or your admin provides a static API key. Common for search connectors like Tavily and Exa.
- **Bearer token** — A pre-issued token, similar to an API key. Used by connectors that issue long-lived access tokens.

As a workspace member, you typically do not need to worry about authentication — your admin can provide an organization or workspace credential. For connectors with user-scoped credentials (like personal OAuth), you will be prompted to authorize when you first use the connector.

## Tool citations

When the agent uses an MCP tool in its response, CubeBox shows a **citation** indicating which connector provided the information and what source it came from. This helps you verify the agent's claims and trace data back to the original service.

## Scoping: org-wide vs. workspace-private

Connectors have one organization-level identity, but credentials can be supplied at multiple levels:

- **Organization credential** — Provided by an org admin and usable by workspaces that choose organization-managed access.
- **Workspace credential** — Provided by a workspace admin and used only in that workspace.
- **User credential** — Provided by each user, often through OAuth, and used for that user's calls.

Workspace admins can disable an organization connector for their workspace or choose a workspace/user credential instead of the organization credential when policy allows it.

## Next steps

- [Installing Connectors](./installing-connectors.md) — Add, enable, and connect MCP connectors.
- [Using Tools](./using-tools.md) — Learn how the agent uses tools during conversations.
