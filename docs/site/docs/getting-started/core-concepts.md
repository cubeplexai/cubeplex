---
sidebar_position: 2
title: Core Concepts
---

# Core Concepts

This page explains the building blocks of CubeBox. Understanding these will help you get the most out of the platform.

## Organizations and workspaces

CubeBox uses a hierarchical structure:

**Organization** is the top-level account. It owns billing, provider API keys, and org-wide policies. Every user belongs to exactly one organization.

**Workspaces** live inside an organization. Each workspace is an isolated collaboration space with its own conversations, skills, memory, and MCP tool grants. A user can be a member of multiple workspaces.

**Roles** control what you can do:

| Scope | Roles | Can do |
|---|---|---|
| Organization | Owner, Admin, Member | Owner/Admin: manage providers, models, members, org settings. Member: use assigned workspaces. |
| Workspace | Admin, Member | Admin: manage workspace settings, tools, skills. Member: chat, use tools. |

## Conversations

A conversation is a message thread between you and an AI agent. Each conversation is tied to a specific model (which you can change mid-conversation). The agent can:

- Respond with **text** and **thinking** (extended reasoning).
- Make **tool calls** to external services via MCP connectors.
- Execute **code** in a sandboxed environment.
- Generate **artifacts** — files, websites, images, or other deliverables.

You can attach documents, images, and code files to your messages. Conversations are saved automatically and appear in the sidebar history.

## Skills

Skills are packaged capabilities you install to extend what the agent can do. Think of them as plugins that give the agent new knowledge or behavior patterns.

**Three sources of skills:**

- **Built-in** — ship with CubeBox.
- **Org-uploaded** — your organization creates and shares custom skills.
- **Remote registries** — community skills hosted on registries like [skills.sh](https://skills.sh).

You discover and install skills from within a conversation or from the workspace settings page. Once installed, the agent can use a skill whenever it is relevant to the conversation.

See the [Skills guide](../guides/skills/overview.md) for details.

## Memory

Memory lets the agent remember information across conversations. CubeBox uses a three-tier system:

| Tier | Visibility | Example use |
|---|---|---|
| **Personal** | Only you, across all your workspaces | "I prefer Python over JavaScript" |
| **Workspace** | All members of the workspace | "Our API uses snake_case for JSON keys" |
| **Organization** | All members across all workspaces | "Company name is Acme Corp, founded 2020" |

**Memory types** describe what is being remembered:

- **preference** — how you like things done.
- **project_fact** — a fact about a project or codebase.
- **procedure** — a step-by-step process.
- **correction** — something the agent got wrong that you corrected.
- **decision** — an agreed-upon decision.
- **org_policy** — an organization-wide rule.

The agent reads relevant memories automatically at the start of each conversation. You can also view, edit, and delete memories manually.

See the [Memory guide](../guides/memory/overview.md) for details.

## MCP tools

Model Context Protocol (MCP) connectors let the agent call external APIs — databases, SaaS products, internal services, and more.

**How the tool lifecycle works:**

1. **Templates** — the catalog of available connectors (e.g., "GitHub", "Slack", "PostgreSQL").
2. **Installs** — an admin installs a connector template into the workspace, providing credentials.
3. **Grants** — the admin decides which workspace members can use the installed connector.
4. **Active** — granted tools are available to the agent during conversations.

**Authentication modes:**

- **API key** — you provide a static key.
- **OAuth** — the connector walks you through an OAuth flow.
- **Bearer token** — a pre-issued token.

See the [MCP Tools guide](../guides/mcp/overview.md) for details.

## Artifacts

Artifacts are deliverables the agent produces during a conversation. They go beyond plain text — an artifact might be a downloadable file, a live website preview, a code snippet, an image, or a data table.

Artifacts are:

- **Versioned** — the agent can iterate on an artifact across multiple turns.
- **Previewable** — rendered inline in the conversation.
- **Downloadable** — save artifacts to your local machine.

## Automation

Automation lets you run agent tasks without manual interaction.

- **Scheduled tasks** — run on a cron schedule, at a fixed interval, or as a one-shot at a specific time.
- **Event triggers** — a webhook fires and kicks off an agent run with the webhook payload as context.

See the [Automation guides](../guides/automation/scheduled-tasks.md) for details.

## Next steps

- [Workspace Setup](./workspace-setup.md) — Configure a workspace for your team.
- [Conversations guide](../guides/conversations/basics.md) — Learn the full set of chat features.
