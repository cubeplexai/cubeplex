# Skill name display — implementation plan

Related: #399 · Spec: `docs/dev/specs/2026-07-22-skill-name-display-design.md`

**Goal**: Show bare skill slug as the primary UI label for uploaded
(`org:slug`) skills while keeping canonical names for identity and tools.

**Architecture**: Pure frontend display helper (Phase 1). Optional later
backend resolve for bare `load_skill` (Phase 2) — not required for the first
shippable PR.

**Tech stack**: TypeScript helper + existing skill card/detail components;
Jest/Vitest unit tests for the helper.

---

## Unit 1: `formatSkillLabel` helper

**Files**:
- `frontend/packages/core/src/lib/formatSkillLabel.ts` (preferred if core is
  shared) **or** `frontend/packages/web/lib/formatSkillLabel.ts`
- Export from package index if placed in core

**API**:

```ts
export function formatSkillLabel(name: string): {
  primary: string
  canonical: string
  isNamespaced: boolean
  namespace: string | null  // segment before last ':'
}
```

**Rules**:
- If `name` contains `:`, `primary` = substring after **last** `:`;
  `namespace` = before last `:`; `isNamespaced = true`.
- Else `primary = canonical = name`; `isNamespaced = false`.
- Empty/edge: safe fallbacks (empty primary → canonical).

**Tests**:
- `acme-corp:quarterly-report` → primary `quarterly-report`
- `deep-research` → primary `deep-research`, not namespaced
- `a:b:c` → primary `c` (last colon) — document intentional behavior
- empty string

---

## Unit 2: Admin skill list + detail

**Files**:
- `frontend/packages/web/components/admin/skills/SkillCard.tsx`
- `frontend/packages/web/components/admin/skills/SkillDetailPanel.tsx`

**Changes**:
- Card title: `primary`; `title` attribute or Tooltip: `canonical`
- Detail header: `primary` as main heading; monospace secondary row with full
  canonical + copy button if not already present
- Keep `data-testid` stable: prefer still based on canonical `skill.name` so
  e2e selectors do not break

**Tests**: update any snapshot/e2e that assert visible full org prefix as sole
label; assert primary slug visible.

---

## Unit 3: Workspace skill list + detail

**Files**:
- `frontend/packages/web/components/workspace-settings/skills/WorkspaceSkillCard.tsx`
- `frontend/packages/web/components/workspace-settings/skills/WorkspaceSkillDetail.tsx`

Same display rules as Unit 2.

---

## Unit 4: Other UI surfaces (same helper)

**Files** (audit and apply if they show uploaded `skill.name` raw):
- Skill install lists, binding tables, any mono headers that truncate badly
- **Do not** change remote registry candidate cards that already show a
  short `candidate.name` separate from `canonical_name` unless they dump the
  long form as primary

**Chat/tool panels**:
- `SkillView` / load_skill panel may show the name the agent passed (often
  canonical) — optional: display via helper for friendliness; keep copyable
  canonical available

---

## Unit 5: Optional badge

If design time allows: small “Uploaded” / source badge on cards when
`isNamespaced` so users still see origin without the long prefix.

Skip if it requires new i18n/design cycles; Phase 1 is complete without it.

---

## Unit 6 (Phase 2, separate PR): bare `load_skill` + agent list

**Files**:
- `backend/cubeplex/skills/service.py` / catalog `find_enabled_by_name`
- `tools/builtin/load_skill.py` description update
- Skills list assembly for `SKILLS_PROMPT_TEMPLATE`
- Tests for precedence: preinstalled wins; unique uploaded bare resolves;
  ambiguous errors

**Not in Phase 1 PR.**

---

## Unit 7: Docs

- If only UI: brief note in skills management guide that uploaded skills show
  short names; tools still use `org:slug`.
- If Phase 2: update D18 follow-on note in marketplace design or skills guide.

---

## Delivery order

1. Unit 1 helper + tests  
2. Units 2–3 cards/details  
3. Unit 4 audit pass  
4. Unit 5 optional badge  
5. Docs with implementation  
6. Phase 2 later  

## Out of scope (Phase 1)

- Migrating `Skill.name` in DB  
- Changing sandbox path encoding  
- Bare `load_skill`  
- Frontmatter title field  

## Risks

| Risk | Mitigation |
| --- | --- |
| Users copy primary and paste into agent | Tooltip/detail show canonical; Phase 2 bare resolve later |
| E2E selectors break | Keep testids on canonical name |
| Multi-colon names | Last-colon rule documented |
| Confusion between two skills same bare slug different orgs | Not visible in single-org UI lists; federation later uses canonical |
