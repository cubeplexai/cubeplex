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
| **Source type** | Where the skill comes from: preinstalled, uploaded, or remote registry. |

## Skill sources

CubeBox supports three sources of skills:

- **Preinstalled** — skills that ship with CubeBox. These are always available and cannot be removed.
- **Uploaded** — custom skills your organization creates and uploads directly.
- **Remote registry** — skills fetched from an external registry URL (e.g., [skills.sh](https://skills.sh)).

## Upload a custom skill

1. Go to **Admin > Skills**.
2. Click **Upload Skill**.
3. Provide the skill package (name, description, keywords, and skill content).
4. Click **Save**.

The skill becomes available to all workspaces in your organization. Workspace admins and members can then install it from the skill discovery interface.

## Connect a skill registry

Skill registries are external servers that host collections of skills. Connecting a registry makes its skills discoverable within your organization.

1. Go to **Admin > Skill Registries**.
2. Click **Add Registry**.
3. Enter the registry URL (e.g., `https://skills.sh`).
4. Click **Save**.

CubeBox periodically syncs the registry to keep the skill catalog up to date.

## Enable and disable skills

You can enable or disable any non-preinstalled skill at the org level:

1. Go to **Admin > Skills**.
2. Find the skill in the list.
3. Toggle it **on** or **off**.

Disabling a skill removes it from the discovery interface across all workspaces. Conversations that previously used the skill will still show its historical output, but the agent will no longer invoke it.

## Manage skill versions

When a new version of a skill is available (from an upload or a registry sync), you can update or roll back:

1. Go to **Admin > Skills** and select the skill.
2. View available versions.
3. Select the version you want active.

### Deprecate a skill

If a skill is no longer recommended, you can mark it as deprecated. Deprecated skills remain functional for existing installs but are hidden from discovery. This gives workspace users time to migrate before you fully disable it.
