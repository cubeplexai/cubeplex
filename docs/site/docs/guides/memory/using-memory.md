---
sidebar_position: 2
title: Using Memory
---

# Using Memory

You do not need to configure anything to start using memory. The agent automatically recalls relevant memories and stores new ones as you chat. This page covers how to actively shape what the agent remembers.

## Teaching the agent

The most direct way to create a memory is to tell the agent to remember something:

> "Remember that our API uses snake_case for all response fields."

The agent saves this as a **project_fact** in workspace memory, so every workspace member benefits from it in future conversations.

> "Remember that I like concise answers with code examples."

This becomes a **preference** in your personal memory, applying across all your workspaces.

You can also be explicit about scope:

> "Save this as an org-wide policy: all customer-facing text must be reviewed by the content team before launch."

The agent stores this as an **org_policy** in organization memory.

## Correcting the agent

When the agent gets something wrong, correct it directly:

> **Agent:** "I'll set up the project with npm..."
>
> **You:** "We use pnpm, not npm."

The agent saves a **correction** to workspace memory. In future conversations, it will use pnpm without being told again.

Corrections work at any scope. If the agent keeps misunderstanding your personal preference, a correction to personal memory fixes it across all workspaces:

> "That's wrong -- I prefer dark mode code blocks, not light."

## What the agent remembers automatically

Beyond explicit instructions, the agent may store memory items when it identifies reusable information during a conversation. For example:

- You describe a deployment process step by step -- the agent may save it as a **procedure**.
- You and the agent agree on an approach -- it may store the outcome as a **decision**.
- You share a fact the agent did not know -- it may record it as a **project_fact**.

Each automatically created memory includes a confidence score. Items from explicit instructions ("remember that...") typically get higher confidence than items the agent infers from context.

## Memory in action

Here is a practical example of how memory accumulates and helps over time:

1. **Day 1** -- You tell the agent: *"Our backend is FastAPI with PostgreSQL, deployed on AWS ECS."* The agent saves a workspace-scoped project_fact.

2. **Day 2** -- A teammate asks the agent to help write a database migration. The agent already knows the stack is PostgreSQL and uses the correct syntax without asking.

3. **Day 3** -- You correct the agent: *"We use Alembic for migrations, not raw SQL."* The agent saves a correction to workspace memory.

4. **Day 4** -- Another teammate asks for help with a new migration. The agent recalls both the PostgreSQL fact and the Alembic correction, and produces an Alembic migration file.

## Scope selection

The agent chooses the scope for new memories based on the content:

| Content pattern | Typical scope | Typical type |
|---|---|---|
| "I prefer..." / "I like..." | Personal | preference |
| "Our project uses..." / "The codebase..." | Workspace | project_fact |
| "Company policy is..." / "Org-wide, we..." | Organization | org_policy |
| Correcting a mistake | Same scope as the corrected topic | correction |
| Agreeing on an approach | Workspace | decision |
| Describing a step-by-step process | Workspace | procedure |

If the agent picks the wrong scope, you can correct it in conversation ("make that a personal preference, not workspace") or edit it later in the Memory Center.

## Tips

- **Be specific.** "Remember that we use 4-space indentation in Python files" is more useful than "remember our coding style."
- **Correct early.** The sooner you fix a mistake, the less likely the agent repeats it in other conversations.
- **Check the Memory Center** periodically. Over time, memory items may become outdated. Archiving stale items keeps the agent's context clean.

## Next steps

- [Managing Memory](./managing-memory.md) -- View, edit, and archive memory items from the Memory Center.
