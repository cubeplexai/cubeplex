---
name: skill-creator
description: Use when the user asks to create, build, write, or design a skill, or wants to package an agent behavior or workflow as a reusable skill. Also use when the user wants to edit, modify, update, improve, fix, rename, or bump the version of an existing skill (preinstalled or already published), or publish a new version of one. Also use when the user wants to publish, upload, or share a skill in the current workspace.
version: 0.3.0
keywords:
  - skill-authoring
  - marketplace
  - meta
---

# Skill Creator

This skill guides you through building a publishable skill bundle and installing it into the user's current workspace.

## Workflow

A cubebox skill is a **directory** containing `SKILL.md` at its root plus any sibling files the skill needs at runtime. You write that directory in the sandbox, then register it as a skill artifact.

1. **Ground the request** — Ask the user what problem the skill solves, who it is for, and what the agent should do when the skill is active. One paragraph of context is enough.

2. **Create the bundle directory** somewhere under `/workspace/` — `/workspace` is the user's persistent volume, so the bundle survives sandbox restarts and the user can come back and iterate. **Do not** draft under `/tmp/` (lost on restart) and **do not** write into `/workspace/.skills/...` (that's the read-only sync path for already-installed skills). A recommended location is `/workspace/skills/<name>/`, but anywhere under `/workspace/` works.

   For a skill called `weekly-report`, a typical layout:

   ```
   /workspace/skills/weekly-report/
     SKILL.md                # required, at the root
     scripts/                # optional — executable helpers
       fetch_metrics.py
     reference/              # optional — long docs the agent reads on demand
       schema.md
     templates/              # optional — fixtures the agent fills in
       report.md.tmpl
   ```

   The directory name doesn't have to match `frontmatter.name`, but matching makes things easier to follow.

3. **Write SKILL.md** at the root of the bundle directory. See **Frontmatter Reference** and **Body Guidelines** below.

4. **Add supporting files** (optional) — Drop scripts, reference docs, and templates into subdirectories of the bundle. Reference them from SKILL.md by their bundle-relative path (e.g. `python scripts/fetch_metrics.py`, `cat reference/schema.md`). When the skill is enabled in a workspace, cubebox syncs the whole directory into the sandbox under `/workspace/.skills/<safe-name>/<version>/` (colons in the canonical name are normalised to `__`), so the agent can read or execute them from there at runtime — use the `path` field returned by `load_skill` rather than constructing it yourself.

5. **Register as skill artifact** — Call `save_artifact` with:

   - `path` = the bundle directory (e.g. `/workspace/skills/weekly-report`)
   - `artifact_type="skill"`
   - `entry_file="SKILL.md"`
   - `name` = a human-readable name (typically the skill's `frontmatter.name`)

   The artifact now shows up in the conversation's artifact panel with a **Publish** button.

6. **Publish to the current workspace** — A skill artifact is only registered as a draft; it does **not** appear in `load_skill` or in the available-skills list until it is published. Publishing installs it into the **current workspace** (not the org-wide marketplace; that path is admin-only via zip upload in Org Settings).

   Ask the user how they want to publish:

   - **The user clicks Publish themselves** — point them at the Publish button in the artifact preview panel.
   - **You publish on their behalf** — call `platform_skills_publish_skill(artifact_id="<id from save_artifact>")`. The skill becomes loadable in this workspace immediately.

   After publishing, tell the user the canonical name (e.g. `acme:weekly-report`) and that they can now invoke it by asking — `load_skill` will pick it up.

## Editing an Existing Skill

When the user wants to modify a skill that is already installed (preinstalled or published), the source under `/workspace/.skills/<safe-name>/<version>/` is read-only and gets rewritten on every sync — never edit in place.

Instead:

1. **Copy the bundle out** to a writable workspace path. Use the `path` returned by `load_skill` verbatim as the source — don't reconstruct it from the canonical name, because a name like `acme:my-skill` is normalised to `acme__my-skill` on disk. For example:

   ```bash
   cp -r /workspace/.skills/acme__my-skill/1.0.3 /workspace/skills/my-skill
   ```

2. **Edit under the copy** — change SKILL.md or any sibling file. Leave the `version` field in SKILL.md unset (or remove it); the server will auto-assign the next patch on publish. Only set a specific version if the user explicitly wants one.

3. **Register the edited bundle** with `save_artifact` (same arguments as step 5 above) and then publish it the same way as step 6 — either ask the user to click Publish, or call `platform_skills_publish_skill(artifact_id=...)` yourself with their consent. Republishing the same version string is rejected, so leaving version blank is the safest default.

## Frontmatter Reference

```yaml
---
name: my-skill       # required — see Name Rules
description: …       # required — see Description Rules
version: 1.0.0       # optional — see Version Rules
keywords:            # optional, improves discoverability
  - tag-one
  - tag-two
---
```

### Name Rules

- **Format**: lowercase letters, digits, and hyphens only — `^[a-z0-9][a-z0-9-]{0,62}$` (max 63 chars)
- **No colons**: the org prefix (`org-slug:name`) is added by the server at publish time
- **Prefer gerund form** for readability: `processing-pdfs`, `analyzing-data`, `writing-reports`. Noun phrases (`pdf-processing`) and action forms (`process-pdfs`) also work.

### Description Rules

The description is loaded into the agent's system prompt at startup and is what the agent reads to decide whether to load this skill for the current task. Get this wrong and the skill never fires.

- **Third person only** — the description goes straight into the system prompt:
  - ✓ `"Extracts text from PDF files. Use when the user mentions PDFs or document extraction."`
  - ✗ `"I can help you extract text"` / `"You can use this to extract text"`
- **Include WHAT + WHEN** — what the skill does plus the trigger conditions:
  - ✓ `"Generates git commit messages by analyzing diffs. Use when the user asks for help writing commit messages or reviewing staged changes."`
  - ✗ `"Helps with git commits"`
- **List the trigger phrases the user is likely to say** — if the skill should fire on "create a report", "write a report", "generate a report", put those alternatives into the description so the agent matches all of them.
- **Keep it one or two sentences** — long descriptions add noise to the system prompt and dilute the trigger signal.

### Version Rules

- **Recommended: omit the field.** The server auto-assigns the next patch version (first publish → `1.0.0`; each subsequent publish bumps the patch). This is the safest default and avoids `VersionCollisionError`.
- **Format** (if you do set it): semver with no whitespace (e.g. `1.0.0`, `0.3.1`).
- **Immutable**: the same version string cannot be published twice. Only set the field manually when the user wants a specific version number; otherwise leave it blank.

### Optional Extensions

Use the `cubebox` block to declare runtime dependencies:

```yaml
cubebox:
  requires:
    env: [MY_API_KEY, ANOTHER_VAR]   # env vars the skill needs at runtime
```

Only `requires.env` is currently consumed — preview and install surface these names so the user knows what credentials to provide. Other keys (`bins`, `primaryEnv`, …) are reserved for future use and have no effect today; don't add them unless the user asks.

Aliases `openclaw`, `clawdbot`, and `clawdis` at the top level are also accepted and behave identically to `cubebox`.

## Body Guidelines

### Write in second person, step by step

The body is the agent's runbook for the task. Use numbered steps so the agent works through them in order, and say plainly which tool to call, which artifact to save, which workspace endpoint to hit.

### Keep the body focused on one job

A skill that does one thing well is far more reliable than one that branches into several. If the workflow has a natural fork ("create vs. edit"), prefer two skills over one with a giant if/else.

### Keep it short — every token rides along

When the skill is loaded, SKILL.md is injected into the system prompt for the rest of the conversation. Every token competes with the user's chat. Cut anything the agent doesn't strictly need:

- Don't lecture on general programming or popular libraries — the underlying LLM already knows.
- Don't restate workspace conventions already in the system prompt.
- **Target under 500 lines.** If you need more, split out the bulk into bundle files (see below).

### Split heavy material into bundle files

A skill bundle can contain more than SKILL.md. When the user enables the skill in a workspace, **all bundle files are synced into the sandbox filesystem** under the skill's directory. The agent can `cat`, `grep`, or execute them from there.

Use this for:

- **Reference docs** (API schemas, long tables, large prompts) — keep them out of the system prompt, let the agent read them on demand via sandbox shell tools.
- **Scripts** (`*.py`, `*.sh`) — let the agent execute them rather than regenerating the same code each run.
- **Templates / fixtures** — large JSON / markdown samples the agent fills in.

In SKILL.md, reference these files by their path inside the bundle so the agent knows where to look, e.g. `"Run python scripts/extract.py <input>"` or `"Read reference/api.md for the full field list"`.

### Document external services concretely

If the skill wraps an MCP server, an HTTP API, or a sandbox command:

- Show the **exact tool name** (`GitHub:create_issue`, not "the GitHub tool").
- Show one **concrete input / output example** rather than abstract field descriptions — it disambiguates format faster than prose.

### Avoid time-sensitive wording

Don't write "before December 2025, use X". Put deprecated guidance in a clearly-marked "Legacy" section, or remove it.

### Use consistent terminology

Pick one term per concept and stick with it — don't switch between "field" / "element" / "control" or "extract" / "pull" / "retrieve" in the same skill.
