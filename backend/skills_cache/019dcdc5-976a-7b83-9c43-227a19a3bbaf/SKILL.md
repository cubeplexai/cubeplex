---
name: skill-creator
description: Use when the user wants to author a new skill bundle for the org marketplace. Walks them through frontmatter, body, and supporting files, then captures the result as a skill artifact ready to publish.
version: 0.1.0
keywords:
  - skill-authoring
  - marketplace
  - meta
---

# Skill Creator

This skill guides you through building a publishable skill bundle for the cubebox marketplace.

## Workflow

1. **Ground the request** — Ask the user what problem the skill solves, who it is for, and what the agent should do when the skill is active. Keep this short; one paragraph of context is enough.

2. **Draft SKILL.md** — Write the skill file with proper frontmatter and a clear body. The frontmatter must include:
   - `name`: lowercase, hyphen-separated, unique within the org (e.g. `code-reviewer`)
   - `description`: one sentence the user will read in the marketplace
   - `version`: start at `0.1.0`
   - `keywords`: 1–5 tags that help with search and filtering

   The body is the agent's instruction set. Write it in second person ("When the user asks…"). Be concrete — describe the steps, the tools to use, and any output format expected.

3. **Add supporting files** (optional) — If the skill needs templates, example data, or reference documents, write them to the sandbox under `/.skills/<name>/`. The agent can load these at runtime via `load_file`.

4. **Write to sandbox** — Save the finished SKILL.md to `/.skills/<name>/SKILL.md` so the user can inspect it in the file panel.

5. **Register as skill artifact** — Call `save_artifact` with `artifact_type="skill"` and `name` matching the skill name. This makes the bundle available in the **Publish** flow in the Skills tab.

6. **Hand off** — Tell the user the skill is ready. Remind them to open the Skills tab → Upload, or use the artifact's Publish button, to submit it to the org marketplace.

## Frontmatter Reference

```yaml
---
name: my-skill          # required, unique slug
description: …          # required, shown in marketplace card
version: 0.1.0          # required, semver
keywords:               # optional, improves discoverability
  - tag-one
  - tag-two
---
```

## Tips

- Keep the body focused. A skill that does one thing well is more reliable than one that tries to do everything.
- Use numbered steps in the body so the agent works through them in order.
- If the skill wraps an external service, document the expected input/output shape so the agent knows how to call it.
