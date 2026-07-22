---
sidebar_position: 2
title: Discover & Install Skills
---

# Discover & Install Skills

CubePlex gives you two ways to find and install skills: through natural conversation with the agent, and through the Skills marketplace page.

## Discovering skills in chat

The fastest way to find a skill is to describe what you need. The agent searches both your local catalog (built-in + uploaded skills) and any connected remote registries, then presents matching candidates.

**Example conversation:**

> **You:** I need to create a slide deck for our quarterly review.
>
> **Agent:** I found these skills that can help:
>
> 1. **slide-deck** (Built-in) — Generate structured presentations from a topic or outline.
> 2. **executive-slides** (skills.sh, Community) — Opinionated executive-style slide layouts with data visualization.
>
> Would you like me to install one of these?
>
> **You:** Install the first one.
>
> **Agent:** Done — "slide-deck" is now installed in this workspace. Let me load it and get started on your quarterly review deck.

The agent installs the skill to your current workspace and loads it immediately, so you can start using it in the same conversation.

### How discovery ranking works

When the agent searches for skills, results are ranked by:

1. **Name match** — Exact or partial matches on the skill name rank highest.
2. **Keyword and description match** — Skills whose keywords or descriptions overlap with your query.
3. **Trust tier** — Official skills rank above community skills, which rank above unvetted ones.
4. **Popularity** — Among otherwise equal candidates, skills with more installs rank higher.

Local catalog skills (built-in and uploaded) take priority over remote duplicates of the same skill.

## Using the Skills marketplace

For a more visual browsing experience, open the **Skills** page from the workspace sidebar. It lives under your workspace at `/w/<workspace-id>/skills`, where `<workspace-id>` is your current workspace.

![Skills marketplace with the catalog list and skill detail panel](/img/skills/marketplace.png)

The marketplace shows two sections:

- **System Catalog** — All built-in and uploaded skills visible to your organization. These are already available to install with one click.
- **External Sources** — Results from connected remote registries. These appear when you type a search query in the toolbar.

### Browsing and searching

- Use the **search bar** in the toolbar to filter by name or keyword. When remote registries are connected, the search also queries them.
- Use the **Source** dropdown in the toolbar to narrow the list. It offers **All**, **Preinstalled**, **Uploaded**, and **External** — choosing **External** shows only results from remote registries and hides the local catalog.
- Click any skill card to open its detail panel on the right, which shows the full description, keywords, version, download count (for remote skills), trust tier, and a rendered preview of the skill's `SKILL.md` content.

### Installing from the marketplace

1. Find the skill you want and click it to open the detail panel.
2. Review the skill description and content preview.
3. Click **Install**.

The skill is installed into your current workspace. It becomes available to the agent in new conversations immediately.

## What happens during installation

When you install a skill:

1. **Local skills** (built-in or uploaded) are bound to your workspace. No files are downloaded — they are already in the catalog.
2. **Remote skills** are fetched from the registry, validated (the bundle must contain a valid `SKILL.md`), and published into your organization's catalog. The skill then appears as an uploaded skill owned by your org.
3. An install record is created scoping the skill to your workspace (workspace-private install) or org-wide, depending on how the install was initiated.

If the same remote skill was previously imported at the same version, CubePlex reuses the existing catalog entry instead of creating a duplicate.

## Next steps

- [Managing Skills](./managing-skills.md) — Enable, disable, update, or remove installed skills.
