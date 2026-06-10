---
sidebar_position: 2
title: Members & Roles
---

# Members & Roles

CubeBox uses a two-level role system: **organization roles** control access to admin settings, and **workspace roles** control what a member can do inside a specific workspace.

Org-level member management happens at **Admin > Members** (`/admin/members`). Workspace-level roles are managed within each workspace's settings.

## Organization roles

| Role | Capabilities |
|---|---|
| **Owner** | Full control. Manage providers, models, members, billing, org settings. Transfer ownership. Only one owner per org. |
| **Admin** | Same as owner except cannot transfer ownership or remove the owner. |
| **Member** | Use assigned workspaces. No access to admin settings. |

The role hierarchy is: **Owner > Admin > Member**. Higher roles inherit all permissions of lower roles.

## Invite members

1. Go to **Admin > Members**.
2. Click **Invite Member**.
3. Enter the person's email address.
4. Select an organization role (Admin or Member).
5. Click **Send Invite**.

The invitee receives an email with a link to join your organization. If they do not already have a CubeBox account, they will create one during the sign-up flow.

## Change a member's org role

1. Go to **Admin > Members**.
2. Find the member in the list.
3. Click the role dropdown next to their name.
4. Select the new role.

:::note
You cannot change the owner's role directly. To transfer ownership, the current owner must use the **Transfer Ownership** action.
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

Assign the **Admin** org role. They gain access to all admin settings (models, members, connectors, etc.) but cannot transfer ownership.

### Limit a member to specific workspaces

Assign the **Member** org role, then add them only to the workspaces they need. They will not see workspaces they are not a member of.
