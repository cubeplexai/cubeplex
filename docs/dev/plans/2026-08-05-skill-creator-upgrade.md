# skill-creator upgrade — implementation plan

Related: Spec `docs/dev/specs/2026-08-05-skill-creator-upgrade-design.md`

**Goal**: Upgrade preinstalled `skill-creator` so agents author better skills
(interview → draft → checklist → publish → light verify) without a platform
tool encyclopedia or cross-skill template references; and make the preinstalled
version bump actually reach existing org installs.

**Architecture**:
1. Content under `backend/skills/preinstalled/skill-creator/` (SKILL.md +
   `references/`) with version `0.4.0`.
2. Seeder change so preinstalled install pins advance with catalog
   `current_version` (otherwise existing orgs stay on 0.3.0 forever).
3. Docs-site path correction (`/workspace/.skills`).

No Alembic migration. No `load_skill` precedence / marketplace UI work.

**Tech stack**: Markdown skill bundle; `skill_seeder.py`; existing publish /
frontmatter contracts (`SKILL_SLUG_RE`, auto version, `save_artifact` +
`platform_skills_publish_skill`).

---

## Unit 1: Advance preinstalled install pins on seed

**Why first:** Without this, Units 2–4 ship content that most orgs never load.

**Files**:
- `backend/cubeplex/seeders/skill_seeder.py` — extend
  `_reconcile_preinstalled_installs` (or a sibling helper called after it)
- `backend/tests/…` — unit or e2e covering seeder pin advance (placement per
  `docs/testing.md`; prefer unit if seeder is pure DB; e2e if session/fixtures
  already exist for seeder)

**Behavior**:
- For each org-wide (`workspace_id is None`) install of a **preinstalled**,
  non-deprecated skill: if `installed_version != skill.current_version` and
  no org tombstone for `(org_id, skill_id)`, set
  `installed_version = skill.current_version`.
- Do **not** create installs that were tombstoned.
- Do **not** change uploaded-skill installs.
- Do **not** invent new workspace-private installs; only update existing
  org-wide pin rows (and any workspace-private installs of preinstalled
  skills if they exist — same rule, same skill_id).
- Keep concurrent-safe patterns already used (nested transaction / IntegrityError
  handling if touching the same rows).

**Invariant**: After seed, an org that had `skill-creator@0.3.0` installed and
no tombstone has `installed_version == 0.4.0` when catalog head is `0.4.0`.

**Tests**:
- Seed catalog with skill at v1; create org install pinned at v1; update catalog
  head to v2 via seed path; assert install pin is v2.
- Tombstoned org: install not recreated; pin not force-updated if install was
  purged by tombstone heal.

---

## Unit 2: Progressive disclosure files

**Files**:
- `backend/skills/preinstalled/skill-creator/references/quality-checklist.md`
- `backend/skills/preinstalled/skill-creator/references/minimal-skill.example.md`

**quality-checklist.md**:
- Pre-publish items from the spec (frontmatter, paths, size, one-job, rule C)
- Capture-from-chat scrub + full-content confirm
- Publish error → fix mapping
- Post-publish: `load_skill` + optional sample prompt

**minimal-skill.example.md**:
- Fictional slug only (e.g. `weekly-status-summary`) — **no** real
  preinstalled skill names
- Full minimal SKILL.md example: third-person description, second-person steps,
  one tool under rule C (e.g. `save_artifact`), `references/` if needed

**Tests**: none beyond “files exist and are linked from SKILL.md”.

---

## Unit 3: Rewrite `SKILL.md` procedure

**Files**:
- `backend/skills/preinstalled/skill-creator/SKILL.md`

**What changes**:
- Frontmatter: bump `version` to `0.4.0`; refresh `description` (create /
  edit / publish / capture-from-chat; third person; optional Chinese phrases).
- Numbered workflow per design (intent → find → ground → description → bundle
  → body + rule C → checklist → save_artifact → publish → light verify).
- **Capture safeguards** in the ground step (scrub secrets/PII; full SKILL.md
  confirm before publish).
- **Edit / fork callout**: preinstalled → `org-slug:name` fork; never edit
  `/workspace/.skills/…`; fork discoverability rules (distinct name preferred,
  distinguishable description, no shadow promise).
- **Remove**: gerund preference; `reference/` example; cross-skill templates.
- **Keep**: `/workspace` vs `.skills`, version omit default,
  `cubeplex.requires.env`, workspace-scoped publish.

**Interfaces / contracts**:
- Name: `^[a-z0-9][a-z0-9-]{0,62}$`, no `:`
- Publish: `save_artifact` → `platform_skills_publish_skill(artifact_id)`
- Caps: 10 MB / file, 50 MB / bundle
- Runtime: use `load_skill` `path` only

**Tests**: manual dogfood preferred; no heavy prose suite.

---

## Unit 4: Docs site path correction

**Files**:
- `docs/site/docs/guides/skills/overview.md`
- zh-Hans twin if it still says `/.skills/`

**Change**: mount path → `/workspace/.skills/...`; note agents use
`load_skill`’s `path`.

---

## Unit 5: Ship verification

1. Diff: no sibling skill names as templates; no tool encyclopedia.
2. Frontmatter `version: 0.4.0`.
3. Grep skill-creator: no `reference/` directory advice.
4. Seeder test green (Unit 1).
5. Dogfood if stack up: create → publish → `load_skill`; and/or seed advances
   pin for existing install.

---

## Explicitly out of plan

- `scripts/validate_skill_*.py` (defer)
- Eval harness / description optimization
- Catalog precedence / fork shadows preinstalled
- Frontend marketplace changes

---

## Implementation order

1. Unit 1 (seeder pin advance + tests)
2. Unit 2 (references files)
3. Unit 3 (SKILL.md)
4. Unit 4 (site docs)
5. Unit 5 verify; push same PR (#470)

## PR shape

Single PR (already open): design + seeder + skill content + site docs.

- Title: keep `Upgrade skill-creator authoring loop` (or update if needed)
- Body: link design/plan; call out seeder pin advance as the rollout fix;
  version `0.4.0`.
