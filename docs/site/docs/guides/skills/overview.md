---
sidebar_position: 1
title: Skills Overview
---

# Skills Overview

Skills are packaged capabilities that extend what your agent can do. A skill bundles instructions (as a `SKILL.md` file) with optional supporting files — scripts, templates, configuration — into a single installable unit. When a skill is loaded during a conversation, the agent follows its instructions to perform a specific task: generating slide decks, drafting emails, analyzing datasets, writing code in a particular framework, and so on.

## How skills work

1. **A skill is installed** into your workspace (or org-wide by an admin).
2. **The agent sees it** listed among its available skills at the start of every conversation in that workspace.
3. **When relevant, the agent loads it** — either because you asked for something the skill covers, or because you explicitly requested it. The complete instructions are returned in the `load_skill` tool result and remain in the conversation context.
4. **The agent follows the instructions** to complete the task, using any supporting files the skill provides (templates, scripts, etc.) inside the sandbox.

You do not need to memorize skill names. The agent matches your intent to installed skills automatically. For example, if you say "create a presentation about Q3 results," the agent will load a presentation skill if one is available.

## Skill sources

Skills come from three places:

- **Built-in (preinstalled)** — Ship with CubePlex and are available by default. Your org admin can disable specific built-in skills if they are not needed.
- **Uploaded** — Published by your org admin (or by you at the workspace level). These are custom skills tailored to your team's workflows.
- **Remote registries** — External skill sources (such as [skills.sh](https://skills.sh)) that your admin has connected. You can search these registries and install skills from them directly.

## Trust tiers

Skills from remote registries carry a trust indicator so you know what you are installing:

| Tier | Meaning |
|---|---|
| **Official** | Vetted by the registry maintainer or a known publisher. |
| **Community** | Published by community contributors; not formally vetted. |
| **Untrusted** | No review has been performed. Inspect the skill content before installing. |

Built-in and uploaded skills are inherently trusted because they come from CubePlex or your own organization.

## Built-in research skills

CubePlex includes two research workflows with different goals:

- **Deep Research** investigates one topic through multiple sources and follow-up
  questions, then produces an evidence-backed explanation or report.
- **Wide Research** builds a comprehensive list or dataset by researching many
  independent entities or discovery lanes in parallel, validating every row,
  deduplicating the results, and reporting coverage gaps.

Use Wide Research for requests such as “find all matching vendors,” “map this
market,” or “enrich every company in this list.” Use Deep Research when the hard
part is understanding one subject in depth rather than covering a large set.

## Installation scopes

A skill can be installed at two levels:

- **Org-wide** — An admin installs it for the entire organization. It becomes available in every workspace (admins can toggle it per workspace).
- **Workspace-private** — You install it into a single workspace. Only members of that workspace can use it.

## What is inside a skill

Every skill contains at least a `SKILL.md` file — a markdown document with frontmatter (name, version, description, keywords) and the instructions the agent follows. Many skills also include:

- **Scripts** — Shell or Python scripts the agent can run in the sandbox.
- **Templates** — Starter files, boilerplate, or reference material.
- **Configuration** — Settings that control the skill's behavior.

When a skill is loaded, its files are mounted at `/workspace/.skills/<name>/<version>/` inside the sandbox so the agent can reference them. The agent should use the `path` field returned by `load_skill` (do not construct that path by hand). The agent reads a skill's instructions by calling the built-in `load_skill` tool with the skill's exact name.

## Next steps

- [Discover and Install Skills](./discover-and-install.md) — Find new skills through chat or the marketplace, and install them.
- [Managing Skills](./managing-skills.md) — Enable, disable, update, and remove skills in your workspace.
