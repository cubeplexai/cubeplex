---
sidebar_position: 1
title: MCP Tools Overview
---

# MCP Tools Overview

MCP (Model Context Protocol) connectors let the CubePlex agent call external services during a conversation. Instead of being limited to its own knowledge, the agent can search the web, read GitHub issues, query databases, post Slack messages, and more — all in real time.

## The mental model in one sentence

**Admins pick from the catalog → workspaces decide whether to use it → users connect their own credentials.**

That's the whole flow. Templates are what you can connect; enablement and credentials control who uses it and how.

## Templates: the thing you connect

A **template** is a connector definition that describes a service — its name, server URL, transport, and which authentication methods it supports. Templates are the single concept users face as "a connector you can add." There are three template scopes:

| Scope | Who creates it | Who can see it |
|---|---|---|
| **Global (catalog)** | CubePlex (built-in) | Everyone |
| **Org** | Org admins | All workspaces in the org |
| **Workspace** | Workspace admins | Only that workspace (until promoted) |

Workspace admins can **promote** a workspace-scope template to org scope, making it visible across all workspaces in the organization.

## Connector states

A template can be in one of four states for a given workspace:

| State | Meaning |
|---|---|
| **Visible** | The template is in the catalog; the workspace has not yet decided to use it. |
| **Enabled** | The workspace has turned on this template. The agent can use its tools once credentials are connected. |
| **Credentialed** | Enabled and has a valid credential. The agent's tools are active. |
| **Disabled** | An org admin has suspended this template org-wide. It is hidden from workspace catalogs and excluded from the agent runtime. Existing state rows and credentials are preserved; re-enabling restores everything. |

## Connector rows and lazy creation

Behind the scenes, each template that gets used has a single **connector row** per org — a shared infrastructure object that holds the tools cache, OAuth client identity, and other shared config. This row is created automatically (lazily) the first time any workspace enables a template. You never see or manage connector rows directly; they are internal plumbing.

## Credentials

Credentials are scoped to organization, workspace, or user:

- **Org-level** — one credential shared across all workspaces. Org admins manage this.
- **Workspace-level** — a separate credential for one workspace. Workspace admins manage this.
- **User-level** — each user connects their own account (typically via OAuth). Individual users manage this.

The auth method (OAuth or static token) is chosen when a credential is created, not when the template is registered. A single template can have both OAuth and static credentials coexisting — for example, one workspace's users OAuth while another workspace uses a service-account token.

## Available connectors

CubePlex ships with global templates for a growing list of services:

| Category | Connectors |
|---|---|
| **Development** | GitHub, Linear, Sentry, Atlassian Rovo (Jira, Confluence, Bitbucket, Compass) |
| **Productivity** | Notion, Asana, Google Workspace, Slack, Intercom |
| **Web search** | Tavily, Exa, Jina AI, WebTools |
| **Infrastructure** | Cloudflare (API, Workers, Observability, Logs, Radar) |
| **Knowledge** | Microsoft Learn |

Your admin may have additional org-scope or workspace-scope templates available. Open the **MCP** page in your workspace sidebar to see what is enabled and what is available.

:::info 📸 Screenshot placeholder
**Capture:** The workspace **MCP** page showing the full template list with enable toggles — at least one enabled (with a credential connected) and one not yet enabled.
**Asset:** `/img/mcp/workspace-mcp-page.png`
:::

## Authentication modes

Different connectors use different authentication methods:

- **OAuth** — You authorize CubePlex to access the service on your behalf. The connector walks you through the vendor's consent screen. Most connectors (Notion, Linear, Atlassian, Asana, Sentry, Intercom, Cloudflare) handle this automatically; a few (GitHub, Slack, Google Workspace) require your administrator to pre-register an OAuth app first.
- **API key** — You or your admin provides a static API key. Common for search connectors like Tavily and Exa.
- **Bearer token** — A pre-issued token, similar to an API key. Used by connectors that issue long-lived access tokens.

As a workspace member, you typically do not need to worry about authentication — your admin handles it when setting up the connector. For connectors with user-scoped credentials (like personal OAuth), you will be prompted to authorize when you first use the connector.

## Tool citations

When the agent uses an MCP tool in its response, CubePlex shows a **citation** indicating which connector provided the information and what source it came from. This helps you verify the agent's claims and trace data back to the original service.

## Next steps

- [Enabling Connectors](./enabling-connectors.md) — Enable a connector in your workspace and connect credentials.
- [Using Tools](./using-tools.md) — Learn how the agent uses tools during conversations.
