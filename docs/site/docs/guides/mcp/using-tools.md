---
sidebar_position: 3
title: Using Tools
---

# Using Tools in Conversations

Once MCP connectors are installed in your workspace, the agent can call their tools automatically. You do not need to enable anything per conversation — all installed connectors are available by default.

## How the agent decides to use tools

When you send a message, the agent sees the list of available tools from your workspace's installed connectors. If your request would benefit from external data or an action, the agent calls the appropriate tool on its own. For example:

- "What are the open issues assigned to me on GitHub?" triggers the GitHub connector.
- "Search for recent articles about transformer architectures" triggers a web search connector.
- "Post a summary of this conversation to Slack" triggers the Slack connector.

You do not need to name the tool explicitly. The agent matches your intent to the right connector. That said, you can be specific if you want to force a particular tool:

> Use Exa to find academic papers on climate modeling.

## Reading tool results

When the agent calls a tool, the conversation shows:

1. **Tool call indicator** — A label showing which tool was called and a summary of the input.
2. **Tool result** — The data returned from the external service, incorporated into the agent's response.
3. **Citation** — A badge or annotation showing the connector and source that provided the information. Citations help you verify where the data came from.

The agent weaves tool results into its natural language response. You see the tool call details alongside the text so you can trace every claim back to its source.

## Multiple tool calls

The agent can call multiple tools in a single turn. For example, if you ask:

> Compare the latest GitHub issues with our Linear backlog and summarize the overlap.

The agent may call both the GitHub and Linear connectors, then synthesize the results into one response.

## Progressive disclosure

When your workspace has many connectors installed, CubeBox uses **progressive disclosure** to keep the agent efficient. When the combined tool definitions would take up too much of the model's context window, the system stops loading every tool upfront and instead:

1. Groups each connector's tools into a named, deferred group rather than loading them all into the agent's context.
2. Shows the agent a lightweight summary of what each group can do.
3. Loads a group's full tools on demand, only when the agent decides that connector is relevant to your request.

This kicks in automatically once enough connectors are installed (it needs at least two before it activates) and the tools grow large relative to the context window. You do not need to configure anything — it is an automatic optimization that keeps responses fast even when dozens of connectors are installed.

## What you can ask the agent to do

Here are common patterns for each connector category:

### Development tools

- "Show me the last 5 commits on the main branch" (GitHub)
- "Create a new Linear issue for the login bug" (Linear)
- "What Sentry errors happened in production today?" (Sentry)
- "Find the Confluence page about our deployment process" (Atlassian)

### Productivity tools

- "List my Notion tasks that are due this week" (Notion)
- "Check my Asana project for overdue items" (Asana)
- "Send a Slack message to #engineering with today's standup summary" (Slack)

### Web search

- "Search the web for the latest Next.js 15 features" (Tavily / Exa / WebTools)
- "Find recent research papers about multimodal LLMs" (Exa)
- "Read the content of this URL: ..." (Jina AI / WebTools)

### Infrastructure

- "Check the Cloudflare analytics for our domain" (Cloudflare)
- "Look up the DNS records for example.com" (Cloudflare)

## Troubleshooting

**The agent does not use a tool I expected:**

- Confirm the connector is installed: open the workspace **MCP** page.
- Make sure the connector is not disabled for your workspace.
- Try being more explicit in your request (e.g., "Use the GitHub connector to...").

**The agent gets an error from a tool:**

- **Authentication error** — The connector's credentials may have expired. Ask your admin to reconnect (OAuth) or update the API key.
- **Permission error** — The credential may not have the right scopes. The admin may need to re-authorize with broader permissions.
- **Service unavailable** — The external service may be temporarily down. Try again later.

**I do not see any tool calls:**

- Your workspace may not have any connectors installed. Open the workspace **MCP** page to check, or ask your workspace admin to set up connectors from the catalog.
- The model you selected may not support tool use. Switch to a model that does (most modern models support tools).

## Next steps

- [Installing Connectors](./installing-connectors.md) — Add more tools to your workspace.
- [MCP Tools Overview](./overview.md) — Review the full list of available connectors.
