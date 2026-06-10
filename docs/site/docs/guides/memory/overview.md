---
sidebar_position: 1
title: Memory Overview
---

# Memory Overview

Memory lets the CubeBox agent remember information across conversations. Instead of repeating yourself every time you start a new chat, the agent recalls your preferences, project facts, and team conventions automatically.

## Three tiers of memory

CubeBox organizes memory into three scopes. Each scope controls who can see and benefit from a memory item.

| Tier | Who sees it | Best for |
|---|---|---|
| **Personal** | Only you, in any workspace within your org | Individual preferences, corrections, personal notes |
| **Workspace** | All members of the workspace | Project facts, team procedures, codebase conventions |
| **Organization** | Everyone across all workspaces | Company-wide policies, brand guidelines, shared decisions |

When the agent processes your conversation, it pulls in relevant items from all three tiers. Personal memory takes precedence when it conflicts with broader scopes -- for example, if workspace memory says "use tabs" but your personal memory says "I prefer spaces," the agent follows your personal preference when working with you.

## Memory types

Each memory item is classified by type, which helps the agent understand how to apply it:

- **preference** -- How you or your team likes things done. *"I prefer TypeScript over JavaScript."*
- **project_fact** -- A concrete fact about a project, codebase, or domain. *"Our API uses snake_case for all JSON keys."*
- **procedure** -- A step-by-step process to follow. *"To deploy, run `make build` then `make deploy-staging` before pushing to production."*
- **correction** -- Something the agent got wrong that you corrected. *"PostgreSQL, not MySQL -- we migrated last year."*
- **decision** -- An agreed-upon decision that should be respected going forward. *"We chose Tailwind over CSS modules for the new dashboard."*
- **org_policy** -- An organization-wide rule or standard. *"All public APIs must include rate limiting."*

## How memory works

The agent handles memory automatically in two directions:

**Recall** -- At the start of each conversation turn, the agent retrieves memory items relevant to the current context. You do not need to ask it to "check memory" -- it does this on its own.

**Storage** -- When you share important information during a conversation, the agent may save it as a memory item. This happens when:

- You explicitly ask: *"Remember that our staging URL is staging.example.com."*
- You correct the agent: *"No, we use Poetry, not pip."*
- The agent identifies a reusable fact during conversation.

Each stored memory item includes:

- **Content** -- The actual information.
- **Scope** -- Personal, workspace, or organization.
- **Type** -- One of the six types listed above.
- **Source** -- A link back to the conversation or artifact that created it.
- **Confidence** -- A score from 0.0 to 1.0, self-rated by the agent, indicating how confident it is in the memory's accuracy.
- **Status** -- Active (used in recall) or archived (hidden from recall but not deleted).

## Next steps

- [Using Memory](./using-memory.md) -- Learn how to teach the agent and shape what it remembers.
- [Managing Memory](./managing-memory.md) -- Edit, archive, and organize memory items from the Memory Center.
