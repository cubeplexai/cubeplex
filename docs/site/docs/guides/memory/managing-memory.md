---
sidebar_position: 3
title: Managing Memory
---

# Managing Memory

The Memory Center is where you review what the agent remembers and retire items that are no longer accurate. You can browse memory for every scope you have access to, and archive any active item.

## Opening the Memory Center

Go to your workspace and open **Memory**. The Memory Center lives at `/w/{workspaceId}/memory`, where `{workspaceId}` is your current workspace. You can also reach it from a conversation: when the agent saves or updates a memory, the chip it shows links straight into the Memory Center, pre-filtered to that conversation.

:::info 📸 Screenshot placeholder
**Capture:** The Memory Center with the Personal / Workspace / Organization / Archived tabs visible, showing a few memory cards (type badge, confidence percentage, content, and the hover Archive button).
**Asset:** `/img/memory/memory-center.png`
:::

## Browsing memory items

The Memory Center is organized into four tabs:

- **Personal** — Active memories scoped to you.
- **Workspace** — Active memories shared across this workspace.
- **Organization** — Active memories shared across your whole organization.
- **Archived** — Items you've retired, across all scopes.

Each card displays:

- **Content** — The stored information.
- **Type** — A colored badge: Preference, Fact, Procedure, Correction, Decision, or Org Policy.
- **Confidence** — The agent's self-rated confidence, shown as a percentage (the underlying score ranges from 0.0 to 1.0).
- **Updated** — When the item last changed, shown as a relative time ("Today", "3d ago").
- **Source hint** — A "from conversation" marker on items the agent created during a chat.
- **Status** — Archived items carry an "Archived" badge; active items do not.

## Filtering to a single conversation

When you open the Memory Center from a conversation's memory chip, it filters to just the items tied to that conversation, with a banner at the top. Click **Clear** in the banner to return to the full list.

## Editing memory

The Memory Center is read-and-archive only. To change the *content* of a memory item, ask the agent in conversation — for example, "update the memory about our API base URL to `api.example.com`." The agent edits the existing item in place rather than creating a duplicate. The same applies to changing an item's type or confidence: those updates happen through the agent, not the Memory Center UI.

## Archiving

To archive an active memory, hover over its card and click the archive button in the top-right corner. Archiving sets the item's status to Archived: it is excluded from the agent's recall but kept in the system, and it moves to the **Archived** tab.

There is no permanent-delete button in the Memory Center — archiving is the way to take an item out of active use. If you no longer want an item recalled, archive it. (Behind the scenes, even the delete action in the API is a soft-delete that archives the item rather than erasing it.)

## Practical workflows

### Cleaning up after a project pivot

If your team changes tech stacks or renames a project, some workspace memories will be outdated. Open the **Workspace** tab, scan for project facts that no longer apply, and archive them. To replace one with corrected content, ask the agent to update it in conversation.

### Reviewing agent corrections

Open the **Workspace** tab and look at the Correction badges to see where the agent has been corrected. This is useful for spotting patterns — if the same correction keeps appearing, consider asking the agent to save a clearer fact or procedure to prevent the misunderstanding in the first place.

### Onboarding a new team member

New members automatically benefit from workspace and organization memory. They do not need to do anything. If you want to review what the agent will tell them, open the **Workspace** and **Organization** tabs to see all shared knowledge.

## Who can manage which memories

Access depends on the memory scope. Within a scope, anyone who can see an item can also archive or update it — there is no separate "creator only" or "admin only" restriction on memory:

| Memory scope | Who can view | Who can archive/update |
|---|---|---|
| Personal | Only you | Only you |
| Workspace | All members of that workspace | All members of that workspace |
| Organization | All members of that organization | All members of that organization |

## Tips

- **Review periodically.** Memory items created months ago may no longer be accurate. A quick scan of the Workspace tab once a month keeps the agent sharp.
- **Archive is the audit trail.** Archived items stay in the Archived tab as a record of what the agent used to know, even though they no longer affect recall.
- **Ask the agent to update, not duplicate.** If a fact changes, tell the agent to update the existing memory rather than saving a fresh one. The agent edits in place; duplicates can cause it to recall conflicting information.
