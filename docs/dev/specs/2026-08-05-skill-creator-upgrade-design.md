# skill-creator upgrade — authoring quality without a platform encyclopedia

## Goal

Make the preinstalled `skill-creator` skill produce **publishable, triggerable,
workflow-focused skills** — by fixing path/naming drift, tightening the authoring
loop (interview → draft → validate → publish → light verify), and teaching
authors to put **only workflow-local tool guidance** into the skill body — not
by embedding a second copy of CubePlex’s tool catalog.

## Context

### What ships today

`backend/skills/preinstalled/skill-creator/SKILL.md` (v0.3.0) is a single-file
runbook. It correctly covers:

- Draft under `/workspace/`, never `/tmp` or `/workspace/.skills/…`
- Bundle shape (`SKILL.md` + optional siblings)
- Frontmatter (`name`, `description`, `version`, `keywords`, `cubeplex.requires.env`)
- `save_artifact` → UI Publish or `platform_skills_publish_skill`
- Edit-existing: copy from `load_skill`’s `path`, leave version blank, republish

That matches the real publish path (`SkillPublishService.publish_from_artifact`)
and sandbox layout (`/workspace/.skills/<safe-name>/<version>/`).

### Why it still falls short

| Gap | Effect |
| --- | --- |
| Interview is one shallow paragraph | Skills lack clear WHAT/WHEN, success criteria, and boundaries |
| Strong on **register/publish**, weak on **body quality** | Agents write essay-like SKILL.md instead of ordered runbooks |
| Layout example uses `reference/` | Preinstalled catalog convention is `references/`; authors diverge |
| Gerund naming preference | Catalog is mostly short nouns/slugs (`git-commit`, `docx`); advice misleads |
| No pre-publish checks | Failures surface only as `InvalidSkillNameError` / `VersionCollisionError` / missing `SKILL.md` |
| No post-publish verify | Publish ≠ usable; no `load_skill` round-trip or sample prompt |
| Edit of preinstalled framed as in-place upgrade | Publish always creates/updates `org-slug:name` — a **fork**, not a system overwrite |
| No “search first / when not to create” | Duplicate low-quality workspace skills |
| No “capture from this conversation” path | Common user intent is underserved |
| Temptation to “document all platform tools” in creator | Token cost, drift vs live tool schemas, model confusion |

### Non-goals (this feature)

- Eval harness / benchmark loops for skills (future, optional).
- Org-wide marketplace zip-upload UX (admin path already exists).
- Changing publish APIs, frontmatter schema, or `SKILL_SLUG_RE`.
- A full “CubePlex tools encyclopedia” inside skill-creator.
- Cross-referencing other preinstalled skills by name as writing models
  (e.g. “look at deep-research”) — that confuses the authoring agent.

## Approaches considered

| Option | Summary | Verdict |
| --- | --- | --- |
| **A. Patch only factual bugs** (paths, `references/`, version notes) | Cheap; leaves authoring quality flat | Insufficient alone |
| **B. Platform conventions chapter** (subagent, widgets, sandbox catalog) | Richer authoring context; duplicates system/tool prompts; high drift | **Rejected** as a standalone layer |
| **C. Workflow-local tool guidance only** | When the *target* skill needs a capability, encode prefer/avoid and skill-specific rules in *that* skill’s body | **Adopt** |
| **D. Creator = full authoring product** (scripts, evals, multi-file always) | Overbuilt for v1; evals deferred | Partial later if needed |
| **E. A + C + tightened author loop + thin structure** | Fix correctness; teach C; optional progressive disclosure for creator’s own bulk | **Recommended** |

## Design

### Product behavior

`skill-creator` remains the agent-facing guide for **creating / editing /
publishing workspace skills**. After upgrade it should:

1. Route intent: **new** | **capture from this conversation** | **edit existing**.
2. Optionally check for an existing skill (`platform_skills_find`) before inventing one.
3. Interview only what changes the skill (triggers, I/O, success criteria, one-job scope) — not a fixed 10-question script.
4. Draft description (WHAT + WHEN, third person) before a long body.
5. Write a focused body; apply **C** for tools (below).
6. Pre-publish checklist aligned with server validation.
7. `save_artifact` + explicit publish consent (UI or `platform_skills_publish_skill`).
8. Light verify: `load_skill` on the canonical name; optionally try one user-like prompt.

Bump preinstalled frontmatter version (e.g. `0.3.0` → `0.4.0`) so sandbox skill
sync pushes the new bundle (same dogfood mechanism as the 0.2 → 0.3 path update).

### Default assumption about platform tools (one short paragraph — not a catalog)

Authoring guidance, in skill-creator’s own voice:

> When writing the skill body, assume the runtime agent has access to the same
> class of **platform tools** you see in the current run (sandbox file/shell,
> artifacts, skills APIs, delegation, etc.). For exact parameters, use the
> **live tool list / schemas in this conversation**. Do **not** restate full
> tool schemas inside the skill. Only document a tool when this workflow
> depends on it — name it, say when to prefer it, and add any
> **workflow-local** constraints that the generic tool description does not
> already cover.

That is the entire “system context” policy. No separate
`platform-conventions.md` encyclopedia.

### Rule C — what goes into the *authored* skill

When designing a skill that uses platform capabilities:

| Do | Don’t |
| --- | --- |
| Name the exact tool (`save_artifact`, `subagent`, `execute`, …) if the workflow requires it | Dump every platform tool “for completeness” |
| Prefer/avoid rules **for this job** (“save the report as an artifact, don’t paste the full report in chat”) | Copy system-prompt prose about tools verbatim |
| Skill-specific contracts (prompt structure for sub-tasks, checklist before deliver) | Cross-link “see skill X for how to use subagents” |
| Concrete I/O examples for MCP or external APIs this skill wraps | Abstract “use the appropriate tool” |

If the skill does not need parallel delegation, do not mention `subagent` at all.
If it does, write the **this skill’s** fan-out rules in its body — sourced from
what the authoring agent already knows about that tool, not from a second
manual inside skill-creator.

### Creator structure (progressive disclosure for *itself*)

Keep authoring load small:

```text
backend/skills/preinstalled/skill-creator/
  SKILL.md                         # procedure + hard rules (short)
  references/
    quality-checklist.md           # pre-publish + light post-publish checks
    minimal-skill.example.md       # fictional minimal SKILL.md (no real skill names)
```

Optional later (not required for first ship): `scripts/validate_skill_frontmatter.py`
in the sandbox for local checks. Prefer a markdown checklist first — fewer
moving parts, same agent-followable contract.

**Do not** add a `references/platform-*.md` tool encyclopedia.
**Do not** name other preinstalled skills as templates to imitate.

### Authoring workflow (SKILL.md body outline)

1. **Intent** — new / capture-from-chat / edit existing.
2. **Search before create** — if the user might already have a skill, `platform_skills_find` once; only create when missing or user wants a custom fork.
3. **Ground** — problem, audience, success criteria, out of scope; for capture-from-chat, extract steps and corrections from the conversation first, then confirm with the user.
4. **Description draft** — third person, WHAT + WHEN, trigger phrases (include language variants the user actually uses); show user and adjust.
5. **Bundle layout** under `/workspace/skills/<slug>/`:
   - `SKILL.md` required
   - optional `scripts/`, `references/`, `templates/` (plural `references/`)
6. **Body** — second person, numbered steps, one job; apply rule C for tools; split bulk into bundle files and point to them by **bundle-relative** path; at runtime the agent uses `load_skill`’s `path` for absolute paths.
7. **Pre-publish checklist** (see below).
8. **Register** — `save_artifact` with `artifact_type="skill"`, `entry_file="SKILL.md"`.
9. **Publish** — user clicks Publish **or** agent calls `platform_skills_publish_skill` with consent; report `canonical_name` + version.
10. **Light verify** — `load_skill(canonical_name)`; optional one dry-run of the workflow if cheap.

### Edit-existing semantics (must be explicit)

| Source | What republish does |
| --- | --- |
| Uploaded skill `org:slug` | New **version** of the same skill if `name` unchanged and version free/auto |
| Preinstalled bare slug (e.g. system skill) | Creates/updates **`org-slug:<name>`** — a workspace/org **fork**, does **not** replace the preinstalled skill |

Instructions must say: copy from `load_skill` path → edit under `/workspace/…` →
leave `version` blank → publish. Never edit `/workspace/.skills/…` in place.

### Frontmatter guidance changes

Keep existing server rules; adjust author-facing advice:

- **Name**: keep `^[a-z0-9][a-z0-9-]{0,62}$`, no `:`. Drop “prefer gerund”; prefer **short stable slugs** that match how users will refer to the skill.
- **Description**: keep third person, WHAT+WHEN, trigger phrases, 1–2 sentences. Add: avoid overlapping pure “find/install another skill” intents — that is discovery, not this skill’s job when writing *other* skills; for skill-creator’s own description, mention Chinese/English create/edit/publish triggers if useful.
- **Version**: omit by default (unchanged).
- **keywords**: short synonym list for discovery; optional language variants.
- **cubeplex.requires.env**: only when the skill truly needs secrets; list names the install UI should surface.

### Pre-publish checklist (normative)

Agent should not call publish until:

- [ ] Root `SKILL.md` exists; YAML frontmatter parses
- [ ] `name` matches slug regex and has no `:`
- [ ] `description` is third person and includes WHEN
- [ ] `version` omitted unless user demanded a specific string
- [ ] Bundle paths in body are consistent (`references/` not `reference/` if used)
- [ ] No absolute `/workspace/.skills/…` paths hard-coded for runtime helpers — instruct use of `load_skill`’s `path`
- [ ] Body is one job; under ~500 lines or bulk split out
- [ ] Size: no single file > 10 MB; total bundle ≤ 50 MB (server caps)
- [ ] Rule C: only tools this workflow needs are named; no tool encyclopedia

On publish errors: map common failures to fixes (`InvalidSkillNameError` → rename;
`VersionCollisionError` → clear version / bump; missing `SKILL.md` → fix layout;
type not `skill` → re-save artifact).

### skill-creator’s own description

Refresh triggers so create/edit/publish/capture-from-chat fire reliably; keep
third person; optionally add common Chinese phrases users type. Do **not**
claim skill-creator installs arbitrary marketplace skills (that is
`platform_skills_find` / install).

### Docs site (same PR if we touch paths)

`docs/site/docs/guides/skills/overview.md` still says mount path `/.skills/…`.
That contradicts runtime `/workspace/.skills/…`. Fix in the **same** PR as the
skill bump so authors and humans share one truth. No new doc page required
beyond that correction (and only if we ship the path fix).

### Seeding / rollout

- Change files under `backend/skills/preinstalled/skill-creator/`
- Bump `version` in frontmatter so seeder creates a new `SkillVersion` and
  sandbox sync replaces the old tree
- No Alembic migration; no API change

## Out of scope

- Automated multi-case eval of authored skills
- Blind A/B description optimization
- Packaging `.skill` zip for external harnesses
- Teaching admin org-wide zip upload as the primary agent path
- Changing how `load_skill` injects content or how available-skills lists are built

## Success criteria

1. An agent following skill-creator can produce a minimal skill that **publishes**
   and is **loadable** via `load_skill` with the returned canonical name.
2. Bundle layout docs use **`references/`**; no `reference/` example remains.
3. Edit-preinstalled path is described as **fork to `org:name`**, not overwrite.
4. Creator does **not** maintain a platform tool encyclopedia; authored skills
   only mention tools they need (rule C).
5. Creator does **not** tell authors to open other preinstalled skills as templates.
6. Pre-publish checklist is present and matches server validation outcomes for
   name, version collision, and missing `SKILL.md`.
7. Preinstalled version bumps; existing workspaces pick up content on next skill sync.
8. Site overview path for skill files matches `/workspace/.skills/…` if that page is updated in the same PR.

## Risks

| Risk | Mitigation |
| --- | --- |
| Longer SKILL.md hurts skill-creator’s own token cost | Keep procedure in SKILL.md; put checklist/example in `references/`; load on demand |
| Authors still write bloated tool sections | Explicit “don’t restate schemas”; checklist item |
| Fork-vs-overwrite still confused | Bold callout + one example of `org:slug` after editing a preinstalled skill |
| Over-interviewing | “Only ask what changes the skill”; capture-from-chat fills defaults |
| Checklist ignored | Put checklist step in the numbered workflow, not only an appendix |
