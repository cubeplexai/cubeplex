---
sidebar_position: 3
title: Managing Memory
---

# Managing Memory

The Memory Center gives you full control over what the agent remembers. You can view, filter, edit, and archive memory items for any scope you have access to.

## Opening the Memory Center

Navigate to your workspace, then click **Memory** in the sidebar. This opens the Memory Center at the path `/(app)/w/{workspaceId}/memory`.

## Browsing memory items

The Memory Center shows a list of all memory items you can access. Each item displays:

- **Content** -- The stored information.
- **Scope** -- A badge showing Personal, Workspace, or Organization.
- **Type** -- The memory type (preference, project_fact, procedure, correction, decision, or org_policy).
- **Source** -- A link to the conversation that created the memory item.
- **Confidence** -- The agent's self-rated confidence score (0.0 to 1.0).
- **Status** -- Active or Archived.

## Filtering

Use the filter controls at the top of the Memory Center to narrow the list:

- **By scope** -- Show only Personal, Workspace, or Organization items.
- **By type** -- Show only a specific memory type (e.g., show only corrections, or only procedures).

Filters combine: selecting "Workspace" scope and "project_fact" type shows only workspace-level project facts.

## Editing a memory item

Click on any memory item to open it for editing. You can change:

- **Content** -- Update the stored information. For example, if your API base URL changed, edit the project_fact directly rather than waiting for the agent to learn the new one.
- **Status** -- Switch between Active and Archived. Archived items are excluded from the agent's recall but remain in the system for reference.

Changes take effect immediately. The next time the agent recalls memory, it will use the updated content.

## Archiving vs. deleting

CubeBox supports two ways to remove a memory item from active recall:

- **Archive** -- Sets the status to Archived. The item stays in the Memory Center and can be restored to Active later. Use this when information is outdated but might be useful for historical reference.
- **Delete** -- Permanently removes the memory item. Use this for items that were created by mistake or contain incorrect information you do not want to keep.

When in doubt, archive rather than delete. You can always delete an archived item later.

## Practical workflows

### Cleaning up after a project pivot

If your team changes tech stacks or renames a project, some workspace memories will be outdated. Filter by scope "Workspace" and type "project_fact", then archive or update items that no longer apply.

### Reviewing agent corrections

Filter by type "correction" to see every time someone corrected the agent. This is useful for spotting patterns -- if the same correction keeps appearing, consider adding a clearer project_fact or procedure to prevent the misunderstanding in the first place.

### Onboarding a new team member

New members automatically benefit from workspace and organization memory. They do not need to do anything. If you want to review what the agent will tell them, filter by "Workspace" scope to see all shared knowledge.

## Who can manage which memories

Access depends on the memory scope and your role:

| Memory scope | Who can view | Who can edit/archive/delete |
|---|---|---|
| Personal | Only you | Only you |
| Workspace | All workspace members | Workspace admins and the item creator |
| Organization | All org members | Org admins and the item creator |

## Tips

- **Review periodically.** Memory items created months ago may no longer be accurate. A quick scan of workspace memories once a month keeps the agent sharp.
- **Use archive over delete.** Archived items serve as an audit trail of what the agent used to know.
- **Edit rather than duplicate.** If a fact changes, update the existing memory item instead of creating a new one. Duplicates can cause the agent to recall conflicting information.
