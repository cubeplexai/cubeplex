---
name: skill-creator
description: >
  Builds, edits, and publishes CubePlex skills (SKILL.md bundles) into the
  current workspace. Use when the user asks to create, build, write, design,
  package, or capture a reusable agent workflow as a skill; to edit, improve,
  fix, rename, or bump a skill; or to publish, upload, or share a skill in this
  workspace. Also use for Chinese requests like 写个 skill、封装成 skill、
  发布 skill、把刚才的流程收成 skill.
version: 0.4.0
keywords:
  - skill-authoring
  - marketplace
  - meta
  - publish-skill
---

# Skill Creator

Guide for authoring a publishable skill bundle in the sandbox and installing it
into the **current workspace**.

A skill is a **directory** with `SKILL.md` at its root plus optional sibling
files. You draft under `/workspace/`, register with `save_artifact`, then
publish.

When you need the full checklist or a copy-paste minimal example, read:

- `references/quality-checklist.md` (use the `path` from `load_skill` for this skill)
- `references/minimal-skill.example.md`

## Workflow

### 1. Intent

Choose one:

| Intent | Meaning |
| --- | --- |
| **New** | Design a skill from requirements |
| **Capture-from-chat** | Turn this conversation’s workflow into a skill |
| **Edit existing** | Change an installed skill and republish |

### 2. Search before create

If the user might already have something usable, call `platform_skills_find`
once. Only create a new skill when nothing fits or they want a custom fork.

### 3. Ground the request

Collect only what changes the skill: problem, audience, success criteria, out
of scope. Prefer a short interview over a long questionnaire.

**Capture-from-chat (required safeguards):**

1. Extract durable **procedure** from the conversation (steps, tools, deliverables,
   corrections the user made).
2. **Scrub** secrets, tokens, API keys, passwords, and obvious PII. Drop one-off
   ticket IDs and temporary workarounds unless the user wants them kept.
3. If the conversation is mostly credentials or customer data, refuse to package
   it as-is; ask for a sanitized procedure instead.
4. Before publish, show the **full draft `SKILL.md`** (not only the description)
   and get explicit confirmation of that content.

### 4. Draft the description (before a long body)

Write frontmatter `description` in **third person**, 1–2 sentences, **WHAT + WHEN**,
with trigger phrases the user is likely to say. Show it to the user and adjust.

Examples of good shape (fictional):

- ✓ `Drafts a short weekly status from the user's notes. Use when the user asks for a weekly status or standup write-up.`
- ✗ `I can help you write status updates` (first person)
- ✗ `Helps with reports` (no WHEN)

### 5. Create the bundle directory

Somewhere under `/workspace/` (recommended: `/workspace/skills/<slug>/`).

- **Do not** draft under `/tmp/` (lost on restart).
- **Do not** write into `/workspace/.skills/...` (read-only sync of installed skills).

Typical layout:

```text
/workspace/skills/weekly-status-summary/
  SKILL.md                 # required at root
  scripts/                 # optional — executable helpers
  references/              # optional — long docs loaded on demand (plural)
  templates/               # optional — fixtures the agent fills in
```

Directory name need not match frontmatter `name`, but matching helps.

### 6. Write SKILL.md

See **Frontmatter** and **Body** below. Optional supporting files: reference them
with **bundle-relative** paths (e.g. `python scripts/fetch.py`,
`read references/schema.md`). At runtime the agent must use the `path` field
from `load_skill` for absolute paths — never hand-build
`/workspace/.skills/<name>/...` (colons become `__` on disk).

### 7. Pre-publish checklist

Run through `references/quality-checklist.md` (or the short list there). Do not
publish until frontmatter, layout, size caps, and rule C pass.

### 8. Register as skill artifact

Call `save_artifact` with:

- `path` = the bundle directory
- `artifact_type="skill"`
- `entry_file="SKILL.md"`
- `name` = human-readable name (usually frontmatter `name`)

The conversation artifact panel shows a **Publish** button.

### 9. Publish to the current workspace

A skill artifact is a **draft** until published. It does **not** appear in
`load_skill` or the available-skills list before that. Publish installs into the
**current workspace** (not the org-wide admin marketplace zip path).

Ask how the user wants to publish:

- They click **Publish** in the artifact panel, or
- You call `platform_skills_publish_skill(artifact_id="...")` **with their consent**

Report the returned **canonical name** (e.g. `acme:weekly-status-summary`) and version.

### 10. Light verify

1. `load_skill(<canonical_name>)` — confirm content and `path`.
2. Optional: one cheap dry-run of the skill’s main path if the user wants proof.

## Editing an existing skill

Installed skill trees under `/workspace/.skills/<safe-name>/<version>/` are
**read-only** and rewritten on every sync. **Never edit in place.**

1. `load_skill` the skill. Use the returned `path` as the copy source (do not
   reconstruct paths; `org:slug` becomes `org__slug` on disk).
2. Copy to a writable path, e.g.
   `cp -r <path-from-load_skill> /workspace/skills/<slug>`
3. Edit. Leave `version` **unset** so the server assigns the next patch.
4. `save_artifact` + publish (same as create), with consent.

### Fork semantics (critical)

| Source | What publish does |
| --- | --- |
| Uploaded `org:slug` | New version of that skill when `name` is unchanged |
| Preinstalled bare name | Creates/updates **`org-slug:<name>`** — a **fork**, not a system overwrite |

Also:

1. Tell the user clearly this is a fork if the source was preinstalled.
2. Prefer a **distinct** frontmatter `name` for customizations so the fork does
   not sit next to the same bare slug. If they reuse the slug, warn that both
   may stay installed.
3. Make the fork **description** distinguishable so auto-invoke can prefer it.
4. Verify with `load_skill` on the **fork’s** canonical name.
5. **Do not** claim that forking disables or shadows the preinstalled skill —
   both can remain enabled; `load_skill` requires the exact name.

## Frontmatter

```yaml
---
name: my-skill          # required — Name rules
description: …          # required — Description rules
version: 1.0.0          # optional — omit by default
keywords:               # optional
  - tag-one
---
```

### Name rules

- Format: `^[a-z0-9][a-z0-9-]{0,62}$` (max 63 chars)
- No colons — the server adds the org prefix at publish time
- Prefer **short stable slugs** users will say; do not force gerund forms

### Description rules

Loaded into the agent’s skill list and drives auto-load. Wrong description →
skill never fires.

- Third person only
- WHAT + WHEN + likely trigger phrases
- One or two sentences

### Version rules

- **Omit by default** — server assigns next patch (`1.0.0`, then `1.0.1`, …)
- Same version string cannot be published twice (`VersionCollisionError`)
- Set `version` only if the user explicitly wants a specific number

### Optional env requirements

```yaml
cubeplex:
  requires:
    env: [MY_API_KEY]
```

Only `requires.env` is consumed today (surfaced at preview/install). Aliases
`openclaw`, `clawdbot`, `clawdis` behave the same as `cubeplex`.

## Body guidelines

### Second person, step by step

The body is the agent’s runbook. Numbered steps; say which tool to call and
what to deliver.

### One job per skill

Prefer two skills over one giant create/edit branch.

### Keep it short

Loaded skill text stays in context for the rest of the conversation. Cut
general programming lectures and anything already in the system prompt.
**Target under 500 lines**; put bulk in `references/`, `scripts/`, or
`templates/`.

### Platform tools — rule C (not a catalog)

Assume the runtime agent has the same class of **platform tools** you see in
this run (sandbox, artifacts, skills APIs, delegation, etc.). For parameters,
use the **live tool list / schemas in the current conversation**.

**Do not** restate full tool schemas inside the skill. **Do** name a tool when
this workflow depends on it: when to prefer it, and any **workflow-local**
constraints the generic description does not already cover.

| Do | Don’t |
| --- | --- |
| Name exact tools this job needs | Dump every platform tool “for completeness” |
| Prefer/avoid rules for this deliverable | Copy system-prompt tool prose wholesale |
| Skill-specific contracts (checklists, prompt shape for sub-tasks) | Point authors at other installed skills as templates |

If the skill does not need a capability, do not mention it.

### External services

For MCP or HTTP APIs: exact tool name + one concrete input/output example.

### Terminology and time

One term per concept. Avoid “before date X, use Y”; use a clear Legacy section
or remove outdated guidance.

## Size limits

Server rejects bundles with a single file > 10 MB or total size > 50 MB.
