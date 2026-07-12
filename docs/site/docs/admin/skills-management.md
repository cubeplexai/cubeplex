---
sidebar_position: 4
title: Skills Management
---

# Skills Management

Skills extend what the agent can do by packaging knowledge, behavior patterns, or workflows into installable modules. As an admin, you manage skills across the organization — uploading custom skills, connecting external registries, and controlling availability.

Skill management happens at **Admin > Skills** (`/admin/skills`) and **Admin > Skill Registries** (`/admin/skill-registries`).

## Skill properties

Every skill has the following attributes:

| Property | Description |
|---|---|
| **Name** | Display name shown to users. |
| **Description** | What the skill does, shown during discovery and install. |
| **Keywords** | Tags used for search and categorization. |
| **Version** | Semantic version string. |
| **Source type** | Where the skill comes from: `preinstalled` or `uploaded`. Skills imported from a remote registry are stored as `uploaded` and additionally record which registry they came from. |

## Skill sources

A skill's stored source is one of two values, `preinstalled` or `uploaded`:

- **Preinstalled** — skills that ship with CubePlex. These are available by default. An admin can uninstall a preinstalled skill for the org; CubePlex records that decision so it is not restored on the next system update.
- **Uploaded** — custom skills your organization creates and uploads directly. Skills imported from a remote registry (e.g., [skills.sh](https://skills.sh)) are also stored as `uploaded`, with a reference back to the registry they were imported from.

## Upload a custom skill

1. Go to **Admin > Skills**.
2. Click **Upload Skill**.
3. Select a `.zip` bundle. It must contain a `SKILL.md` at the root whose frontmatter includes `name`, `version`, and `description`. (Each file may be at most 10 MB, and the whole bundle at most 50 MB.) The skill's name, description, and keywords are read from that frontmatter.
4. Upload it. CubePlex validates the bundle and publishes it.

The skill is added to your organization's catalog. To make it available in every workspace, install it org-wide (see "Install and control availability" below). Workspace owners and members can also install catalog skills themselves from the skill discovery interface.

## Connect a skill registry

Skill registries are external servers that host collections of skills. Connecting a registry makes its skills discoverable within your organization.

1. Go to **Admin > Skill Registries**.
2. Click **Add Registry**.
3. Choose the **Kind**: a built-in provider (skills.sh or ClawHub) or a generic **remote** registry. For a generic remote registry, also enter its **URL** (e.g., `https://registry.example.com`); built-in providers use their known base URL automatically.
4. Give the registry a **Name**.
5. Set the **Trust tier** — `official`, `community`, or `untrusted`. This is the default trust level applied to skills discovered through this registry. (For skills.sh, individual skills from recognized official upstreams are promoted to `official` regardless of this default.)
6. Click **Save**.

Once a registry is connected (and enabled), its skills surface in the discovery interface when users search. You can enable, disable, or change the trust tier of a registry later from its detail panel.

:::info 📸 Screenshot placeholder
**Capture:** The Add Registry form with the Kind selector (skills.sh / ClawHub / remote), the conditional URL field shown for the remote kind, and the Trust tier picker.
**Asset:** `/img/admin/add-skill-registry.png`
:::

## Install and control availability

From **Admin > Skills** you control which skills exist in your organization and how they reach workspaces:

- **Install** a skill org-wide so it becomes available to every workspace. Each org-wide install carries an **auto-bind** setting: when on, the skill is automatically enabled in workspaces; when off, a workspace owner must turn it on per workspace. Preinstalled skills default to auto-bind on; uploaded skills default to off.
- **Uninstall** a skill to remove it from the org. This also removes its per-workspace bindings. Uninstalling a *preinstalled* skill records a tombstone so CubePlex does not restore it on the next system update.

Whether a specific workspace can use an org-installed skill is then a per-workspace toggle (managed by the workspace owner from the workspace **Skills** page, or by an admin from the skill's workspace-bindings view). Disabling a skill for a workspace hides it from the agent there; historical conversation output is unaffected.

## Manage skill versions

Each upload or registry import appends a new immutable version; older versions are never modified. When a newer version exists than the one your org installed, the skill shows **update available**.

1. Go to **Admin > Skills** and select the skill.
2. Review the available versions in the detail panel.
3. Install the version you want your org to use. Installing the latest picks up the update; installing an earlier version pins the org to it.
