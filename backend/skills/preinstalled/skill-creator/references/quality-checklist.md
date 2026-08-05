# Pre-publish and post-publish checklist

Read this before calling `save_artifact` / publish, and after publish succeeds.

## Capture-from-chat (if applicable)

- [ ] Secrets, tokens, API keys, passwords, and obvious PII are scrubbed
- [ ] Procedure is durable (not one-off ticket IDs or temporary workarounds)
- [ ] User confirmed the **full** draft `SKILL.md` content, not only the description

## Frontmatter

- [ ] Root file is exactly `SKILL.md`
- [ ] YAML frontmatter parses (`---` … `---`)
- [ ] `name` matches `^[a-z0-9][a-z0-9-]{0,62}$` and contains no `:`
- [ ] `description` is third person and includes WHAT + WHEN (trigger phrases)
- [ ] `version` omitted unless the user demanded a specific string
- [ ] `keywords` (if any) are short discovery synonyms

## Bundle layout

- [ ] Draft lives under `/workspace/…` (recommended `/workspace/skills/<slug>/`)
- [ ] Not under `/tmp/` or `/workspace/.skills/…`
- [ ] Supporting dirs use `scripts/`, `references/` (plural), `templates/` as needed
- [ ] Body references bundle-relative paths; runtime absolute paths come from `load_skill`’s `path`
- [ ] No hard-coded `/workspace/.skills/<name>/…` paths for helpers

## Body quality

- [ ] One job only (split create vs edit into separate skills if needed)
- [ ] Second person, numbered steps
- [ ] Under ~500 lines, or bulk moved into `references/` / scripts
- [ ] Rule C: only name tools this workflow needs; do not restate full tool schemas
- [ ] External APIs / MCP: exact tool names + one concrete I/O example

## Size (server caps)

- [ ] No single file > 10 MB
- [ ] Total bundle ≤ 50 MB

## Publish error map

| Error | Fix |
| --- | --- |
| missing root `SKILL.md` | Move/rename so `SKILL.md` is at the bundle root |
| `InvalidSkillNameError` / bad slug | Fix `name` (lowercase, digits, hyphens; no `:`) |
| `VersionCollisionError` | Clear `version` and republish (server assigns next patch) |
| artifact type not `skill` | Re-`save_artifact` with `artifact_type="skill"`, `entry_file="SKILL.md"` |
| file / bundle too large | Trim assets or split bulk out of the skill |

## After publish

- [ ] Note the returned `canonical_name` and version (e.g. `acme:weekly-status-summary`)
- [ ] `load_skill(canonical_name)` succeeds and returns a `path`
- [ ] Optional: one cheap dry-run of the skill’s main path if the user wants proof
- [ ] If this was a **fork** of a preinstalled skill: confirm the user understands both the bare system skill and the `org:slug` fork may remain installed; auto-match may still pick the system skill unless name/description are distinct
