# skill-creator upgrade — implementation plan

Related: Spec `docs/dev/specs/2026-08-05-skill-creator-upgrade-design.md`

**Goal**: Upgrade preinstalled `skill-creator` so agents author better skills
(interview → draft → checklist → publish → light verify) without a platform
tool encyclopedia or cross-skill template references.

**Architecture**: Content-only change under
`backend/skills/preinstalled/skill-creator/` (SKILL.md rewrite + optional
`references/`), version bump for seeder/sync, one docs-site path correction.
No API, migration, or frontend code.

**Tech stack**: Markdown skill bundle; existing publish/frontmatter contracts
unchanged (`SKILL_SLUG_RE`, auto version, `save_artifact` +
`platform_skills_publish_skill`).

---

## Unit 1: Rewrite `SKILL.md` procedure

**Files**:
- `backend/skills/preinstalled/skill-creator/SKILL.md`

**What changes**:
- Frontmatter: bump `version` to `0.4.0`; refresh `description` (create /
  edit / publish / capture-from-chat triggers; third person; optional
  Chinese phrases users actually say). Keep or tighten `keywords`.
- Body structure (numbered workflow, not essay):
  1. Intent routing (new / capture-from-chat / edit)
  2. Search-before-create (`platform_skills_find` when useful)
  3. Ground / interview (minimal; conversation extraction for capture)
  4. Description draft + user confirm
  5. Bundle under `/workspace/skills/<slug>/` with **`references/`** (plural)
  6. Body guidelines + **rule C** (workflow-local tools only; one short
     “use live tool schemas” paragraph — no tool catalog)
  7. Pre-publish checklist (inline short list **or** “read
     `references/quality-checklist.md`”)
  8. `save_artifact` → publish consent path
  9. Light verify (`load_skill`; optional one dry-run)
- **Edit semantics callout**: preinstalled → fork as `org-slug:name`; never
  edit `/workspace/.skills/…` in place; always use `load_skill` path as copy
  source.
- **Remove**: gerund naming preference; `reference/` example; any instruction
  to load or imitate other preinstalled skills by name.
- **Keep**: absolute correctness on `/workspace` vs `.skills`, version omit
  default, `cubeplex.requires.env` note, publish targets current workspace.

**Interfaces / contracts** (must stay aligned with code):
- Name: `^[a-z0-9][a-z0-9-]{0,62}$`, no `:`
- Publish: `save_artifact` → `platform_skills_publish_skill(artifact_id)`
- Caps: 10 MB / file, 50 MB / bundle (document in checklist)
- Runtime helpers: `load_skill` returns `path`; do not hand-build
  `/workspace/.skills/...`

**Tests**:
- No automated runtime test required for markdown-only ship.
- Manual (or one-shot agent dogfood in a worktree sandbox): follow the
  rewritten skill to create a throwaway skill → publish → `load_skill`
  succeeds. Document command/outcome in PR body.
- Optional unit-style assertion only if the repo already parses preinstalled
  SKILL.md in tests — do **not** invent a heavy suite for prose.

---

## Unit 2: Progressive disclosure files

**Files**:
- `backend/skills/preinstalled/skill-creator/references/quality-checklist.md`
  (create)
- `backend/skills/preinstalled/skill-creator/references/minimal-skill.example.md`
  (create)

**quality-checklist.md**:
- Pre-publish items from the spec (frontmatter, paths, size, one-job, rule C)
- Publish error → fix mapping
- Post-publish: `load_skill` + optional sample prompt

**minimal-skill.example.md**:
- Fictional slug only (e.g. `weekly-status-summary`) — **no** real
  preinstalled skill names
- Full minimal SKILL.md example demonstrating third-person description,
  second-person steps, one tool mention under rule C (e.g. `save_artifact`
  for a deliverable), and `references/` layout if needed

**Core logic**:
- SKILL.md points to these files with “read when you need the full checklist /
  example” so the always-injected creator body stays short.

**Tests**: none beyond “files exist and are linked from SKILL.md”.

---

## Unit 3: Docs site path correction

**Files**:
- `docs/site/docs/guides/skills/overview.md`
- Matching zh-Hans copy if present under
  `docs/site/i18n/zh-Hans/docusaurus-plugin-content-docs/current/guides/skills/overview.md`
  (update if it still says `/.skills/`)

**Change**:
- Skill mount path: `/.skills/...` → `/workspace/.skills/...` (and note
  agents should use `load_skill`’s `path`, not construct paths).

**Tests**: none; docs-only.

---

## Unit 4: Ship verification (manual)

**Not a code unit** — PR verification checklist:

1. Diff review: no sibling skill names used as templates; no platform tool
   encyclopedia section.
2. Frontmatter `version: 0.4.0` present.
3. Grep skill-creator tree: no bare `reference/` directory advice (except
   historical notes outside this tree).
4. Worktree dogfood (optional but preferred): seed/run path that loads
   preinstalled skills, exercise create → publish → load once if local
   stack is up (`CUBEPLEX_API__PORT` from `.worktree.env`).

---

## Explicitly out of plan

- `scripts/validate_skill_*.py` (defer; checklist is enough for v0.4)
- Eval harness, description optimization loops
- API / seeder Python changes beyond natural pickup of new preinstalled files
- Frontend marketplace changes

---

## Implementation order

1. Unit 2 files first (checklist + example) so SKILL.md can link to real paths.
2. Unit 1 SKILL.md rewrite + version bump.
3. Unit 3 docs path fix.
4. Unit 4 manual verify; open PR with dogfood notes.

## PR shape

**One concern, one PR** (docs/dev spec+plan may already be a prior commit on
this branch; implementation commit(s) ship skill + site docs together):

- Title idea: `Improve skill-creator authoring loop and path accuracy`
- Body: link this plan + design; list success criteria; note version `0.4.0`
  for sandbox sync.

If spec/plan were committed alone first, implementation is a follow-up commit
on the same branch (still one PR is fine for this size).
