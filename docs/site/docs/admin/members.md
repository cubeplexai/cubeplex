---
sidebar_position: 2
title: Members & Roles
---

# Members & Roles

CubePlex uses a two-level role system: **organization roles** control access to admin settings, and **workspace roles** control what a member can do inside a specific workspace.

Org-level member management happens at **Admin > Members** (`/admin/members`). Workspace-level roles are managed within each workspace's settings.

![Admin members table with owner and member role controls](/img/admin/members-table.png)

## Organization roles

| Role | Capabilities |
|---|---|
| **Owner** | Full control. Manage providers, models, members, cost tracking, and org settings. Only one owner per org. The owner's role cannot be changed and the owner cannot be removed. |
| **Admin** | Manage providers, models, members, and other admin settings. Cannot change the owner's role or remove the owner. |
| **Member** | Use assigned workspaces. No access to admin settings. |

The role hierarchy is: **Owner > Admin > Member**. Higher roles inherit all permissions of lower roles.

## Add members

1. Go to **Admin > Members**.
2. Click **Add Member**.
3. Enter the person's email address.
4. Select an organization role (Admin or Member).
5. Click **Add**.

The person must already have a CubePlex account — the email is matched against existing accounts, and adding fails if no account with that email exists. Ask new teammates to sign up first, then add them to your organization. Once added, they immediately gain the access their org role grants.

## Change a member's org role

1. Go to **Admin > Members**.
2. Find the member in the list.
3. Click the role dropdown next to their name.
4. Select the new role.

:::note
The owner's role cannot be changed from the members list — the owner appears with an **Owner** badge instead of a role dropdown, and has no **Remove** action. Each organization has exactly one owner.
:::

## Remove a member

1. Go to **Admin > Members**.
2. Find the member and click **Remove**.
3. Confirm the removal.

Removing a member revokes their access to all workspaces in the organization. Their past conversation history is preserved.

## Workspace roles

Within each workspace, members can have one of two roles:

| Role | Capabilities |
|---|---|
| **Workspace Admin** | Manage workspace settings, installed tools, skills, and member access. |
| **Workspace Member** | Chat, use tools, and interact with the agent. |

Workspace roles are managed from the workspace's own settings page, not the org-level admin panel. An org admin can access any workspace's settings.

## Common scenarios

### Grant someone admin access without making them org owner

Assign the **Admin** org role. They gain access to all admin settings (models, members, connectors, etc.) but cannot change or remove the owner.

### Limit a member to specific workspaces

Assign the **Member** org role, then add them only to the workspaces they need. They will not see workspaces they are not a member of.
