---
sidebar_position: 2
title: Using Memory
---

# Using Memory

You do not need to configure anything to start using memory. The agent automatically recalls relevant memories and stores new ones as you chat. This page covers how to actively shape what the agent remembers.

## Teaching the agent

The most direct way to create a memory is to tell the agent to remember something:

> "Remember that I like concise answers with code examples."

This becomes a **preference** in your personal memory, applying across all your workspaces.

By default, the agent saves to **personal** scope. This keeps your memory private to you unless you decide otherwise. So even a project fact saved without further instruction lands in your personal memory:

> "Remember that our API uses snake_case for all response fields."

The agent saves this as a **project_fact** in your personal memory.

To share a memory with your whole team or organization, ask explicitly. The agent only writes workspace or organization scope when you say so:

> "Save this for the whole workspace: our API uses snake_case for all response fields."

> "Save this as an org-wide policy: all customer-facing text must be reviewed by the content team before launch."

The first becomes a workspace **project_fact**; the second an **org_policy** in organization memory.

## Correcting the agent

When the agent gets something wrong, correct it directly:

> **Agent:** "I'll set up the project with npm..."
>
> **You:** "We use pnpm, not npm."

The agent saves a **correction** to your personal memory. In future conversations, it will use pnpm without being told again. If you want the whole team to inherit the fix, tell the agent to share it ("save that for the workspace").

Corrections work at any scope, and a personal correction follows you across all your workspaces:

> "That's wrong — I prefer dark mode code blocks, not light."

## What the agent remembers automatically

Beyond explicit instructions, the agent may store memory items when it identifies reusable information during a conversation. For example:

- You describe a deployment process step by step — the agent may save it as a **procedure**.
- You and the agent agree on an approach — it may store the outcome as a **decision**.
- You share a fact the agent did not know — it may record it as a **project_fact**.

Each automatically created memory includes a confidence score. Items from explicit instructions ("remember that...") typically get higher confidence than items the agent infers from context.

## Memory in action

Here is a practical example of how memory accumulates and helps over time:

1. **Day 1** — You tell the agent: *"Save this for the workspace: our backend is FastAPI with PostgreSQL, deployed on AWS ECS."* The agent saves a workspace-scoped project_fact so the whole team inherits it.

2. **Day 2** — A teammate asks the agent to help write a database migration. The agent already knows the stack is PostgreSQL and uses the correct syntax without asking.

3. **Day 3** — You correct the agent: *"We use Alembic for migrations, not raw SQL — save that for the workspace too."* The agent saves a correction to workspace memory.

4. **Day 4** — Another teammate asks for help with a new migration. The agent recalls both the PostgreSQL fact and the Alembic correction, and produces an Alembic migration file.

## Scope selection

The agent always defaults new memories to **personal** scope, and infers the *type* from the content. It only writes workspace or organization scope when you explicitly ask it to share:

| Content pattern | Default scope | Typical type |
|---|---|---|
| "I prefer..." / "I like..." | Personal | preference |
| "Our project uses..." / "The codebase..." | Personal | project_fact |
| Correcting a mistake | Personal | correction |
| Agreeing on an approach | Personal | decision |
| Describing a step-by-step process | Personal | procedure |
| "Save this for the workspace: ..." | Workspace | project_fact / procedure / decision |
| "Save this as an org-wide policy: ..." | Organization | org_policy |

To promote a memory beyond yourself, say so when you ask the agent to remember it ("save that for the whole workspace"). You cannot change the scope of an existing item from the Memory Center, so be explicit up front.

## Tips

- **Be specific.** "Remember that we use 4-space indentation in Python files" is more useful than "remember our coding style."
- **Correct early.** The sooner you fix a mistake, the less likely the agent repeats it in other conversations.
- **Check the Memory Center** periodically. Over time, memory items may become outdated. Archiving stale items keeps the agent's context clean.

## Next steps

- [Managing Memory](./managing-memory.md) — Review and archive memory items from the Memory Center.
