---
sidebar_position: 3
title: Managing Skills
---

# Managing Skills

Once skills are installed, you can manage them from the workspace settings or the Skills marketplace page. This guide covers the day-to-day operations: enabling, disabling, updating, uploading custom skills, and understanding how org-wide and workspace-private installs interact.

## Viewing your workspace skills

Open the **Skills** page from the workspace sidebar to see every skill available in your workspace. Each skill card shows:

- **Name** and short description.
- **Source** — whether the skill is built-in (preinstalled) or uploaded.
- **Version** — the currently installed version.
- **State** — whether the skill is enabled, disabled, or available but not yet installed.

You can filter the list by source (preinstalled / uploaded), by state (enabled / disabled / available), or by a text search.

## Skill states

A skill in your workspace can be in one of four states:

| State | Meaning |
|---|---|
| **Org-enabled** | Installed org-wide by an admin and enabled for this workspace. The agent can use it. |
| **Org-disabled** | Installed org-wide but toggled off for this workspace. The agent cannot use it until an admin or workspace owner enables it. |
| **Workspace-private** | Installed directly into this workspace (not org-wide). Always enabled. |
| **Available** | Visible in the catalog but not installed in this workspace. You can install it from the detail panel. |

![Workspace skills page with source and state filters](/img/skills/workspace-skills-page.png)

## Enabling and disabling skills

For **org-wide skills**, a workspace owner or admin can toggle individual skills on or off for the workspace. Disabling a skill does not uninstall it — it just hides it from the agent in that workspace. Re-enable it at any time from the same panel.

**Workspace-private skills** are always enabled. To stop using one, uninstall it (see below).

## Uploading custom skills

You can upload a skill directly to your workspace:

1. On the workspace **Skills** page, click the **Add** button (or the upload action in the toolbar).
2. Select a `.zip` file containing your skill. The zip must include a `SKILL.md` at the root with valid frontmatter (name, version, description).
3. CubePlex validates the bundle and publishes it. The skill appears immediately in your workspace.

Custom skills uploaded at the workspace level are workspace-private. To make a skill available org-wide, ask your org admin to upload it from the admin panel (**Admin > Skills**).

### Skill file requirements

- The zip must contain a `SKILL.md` file at the root.
- `SKILL.md` frontmatter must include `name`, `version`, and `description`.
- Each file in the bundle may be at most 10 MB, and the whole bundle at most 50 MB.
- The skill name must be unique within your organization's catalog.

## Updating skills

When a new version of a skill is available (for example, a remote registry publishes an update), the skill's install state shows **update available**. To update:

1. Open the skill's detail panel.
2. Review the new version's description and content.
3. Click **Install** (or **Update**, depending on context) to pull in the latest version.

The agent will use the updated version in subsequent conversations.

## Uninstalling skills

To remove a skill from your workspace, open its detail panel and use the uninstall action. This removes the workspace binding but does not delete the skill from the org catalog — other workspaces that use the same skill are unaffected.

For org-wide skills, only an admin can fully uninstall them. See [Administration > Skills Management](../../admin/skills-management.md) for details.

**Built-in skills** have special handling: if an admin uninstalls a preinstalled skill for the org, CubePlex records that decision so the skill is not automatically restored on the next system update.

## How skills reach the agent

When a conversation starts, CubePlex assembles the list of skills the agent can see:

1. All **org-wide installed** skills that are **enabled** for the workspace.
2. All **workspace-private** installed skills.
3. Built-in skills that have not been uninstalled by the admin.

The agent receives this list as part of its system context. When a skill is relevant to your request, the agent calls `load_skill(name)` to read its full instructions and begins following them. You can also explicitly ask the agent to load a specific skill by name.

## Tips

- **Start with built-in skills.** They cover common tasks and require no setup.
- **Use workspace-private installs for experimentation.** Try a skill in one workspace before asking your admin to roll it out org-wide.
- **Check the trust tier** before installing skills from remote registries. Official skills are vetted; community and unvetted skills should be reviewed first.
- **Describe your goal, not the skill.** The agent is better at matching your intent to available skills than you are at guessing skill names.
