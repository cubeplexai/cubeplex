# Uploaded skill names: shorter display without losing isolation

Related: #399

## Goal

Stop long `org-slug:skill-slug` strings from dominating skill UI (and, later,
agent skill lists), while keeping **canonical** names for identity, uniqueness,
and `load_skill`.

## Context

### Why the prefix exists (D18)

Canonical name for uploaded skills:

```text
<org-slug>:<skill-slug>
```

Intentional for namespace isolation across orgs, visual distinction from
**preinstalled** bare slugs (`deep-research`), and future federation without
rename breakage. Object-store paths use `org_id` + bare skill slug and are
independent of the display string.

### Current behavior

| Layer | Behavior |
| --- | --- |
| Publish | `SkillPublishService`: `canonical_name = f"{org_slug}:{fm.name}"` |
| DB | `Skill.name` stores full canonical; max 128 |
| UI | Admin/workspace cards and detail headers render `skill.name` raw |
| Agent | Available-skills list + `load_skill(name)` use canonical name |
| Sandbox FS | `:` → `__` via `safe_skill_name` |

There is **no** separate `display_name` field. Org slugs up to 32 chars make
cards truncate on the **prefix**, hiding the useful skill slug.

## Problem

1. Visual noise — prefix repeats the current org on every uploaded skill in an
   already org-scoped list.
2. Truncation cuts the skill slug first.
3. Agent friction — long tokens in skill lists and `load_skill` args.
4. Sandbox path length (secondary).

Must not casually break: uniqueness vs preinstalled bare slugs, stable identity
for installs/versions/`load_skill`, org-rename policy (old prefixes may stick).

## Approaches considered

| Option | Summary | Verdict |
| --- | --- | --- |
| **A. Display ≠ canonical** | UI shows bare slug; tooltip/detail show full | **Primary UX fix** |
| **B. Bare `load_skill` resolve** | Unambiguous bare name → `org:slug` | **Optional later** |
| **C. Shorter namespace token** | Short org key in canonical | Migration cost; later |
| **D. Single-tenant bare names** | Divergent modes | Avoid for now |
| **E. UI layout only** | Two-line / ellipsis prefix | Complements A |
| **F. Frontmatter title** | Human title field | Later polish |

## Recommended direction

### Phase 1 — UI display (A + E) — **this feature’s default scope**

Shared helper:

```ts
formatSkillLabel(name: string): { primary: string; canonical: string; isNamespaced: boolean }
// primary = segment after last ':' if colon present, else whole name
// canonical = original name
```

| Surface | Show |
| --- | --- |
| List cards, most headers | `primary` (bare slug) |
| Tooltip on hover | full `canonical` |
| Detail technical row / copy | full `canonical` |
| Preinstalled (no colon) | unchanged bare name |
| Optional | “Uploaded” badge instead of textual prefix |

Truncation: prefer ellipsis on the **prefix** if any secondary line shows
canonical; primary line should show the full bare slug when possible.

**No DB migration.** `Skill.name` stays canonical. `load_skill` still requires
the full name unless Phase 2 lands.

### Phase 2 — Agent list / bare resolve (optional follow-up)

- Skills list in prompt: show bare slug with note `id: org:slug`, **or**
- `load_skill("my-skill")` resolves when exactly one enabled match after
  applying precedence:

  1. Preinstalled bare name wins if both exist.
  2. Else unique uploaded bare slug in this org/workspace visibility.
  3. Else require full canonical / error with candidates.

Keep full canonical always valid.

### Phase 3 — Optional (C/F)

Short org namespace or separate display `title` from frontmatter — only if
Phase 1–2 still feel noisy.

## Non-goals (Phase 1)

- Rewriting historical `Skill.name` rows.
- Changing object-store or sandbox path layout.
- Dropping uniqueness of uploaded vs preinstalled names.
- Making remote registry candidate cards use a different rule without review
  (default: same primary/canonical helper when a colon form appears).

## Acceptance criteria

1. In org-scoped skill lists, the **dominant visible label** is the short skill
   slug (or later title), not a long `org-slug:` prefix.
2. Full canonical name remains available (tooltip, detail, copy).
3. `load_skill` continues to work for existing uploaded skills with full name
   (and bare name only if Phase 2 is implemented).
4. No undefined collision between preinstalled and uploaded names.
5. Preinstalled skills unchanged (still bare).
6. Tests for display helper + (if Phase 2) resolution precedence.
7. Docs/spec note updated if agent-visible names change.

## Open questions (v1 decisions)

| Question | Decision |
| --- | --- |
| UI-only first vs also agent names | **UI-only (Phase 1) in first implementation**; Phase 2 separate |
| Bare load when preinstalled collides | Require prefix; preinstalled wins |
| Frontmatter title | Not required for Phase 1 |
| Org slug rename | Tooltip shows historical canonical forever |
| Search / remote install cards | Reuse same helper when name is namespaced |

## Related code

- Design D18: `docs/dev/specs/2026-04-26-skills-marketplace-design.md`
- Publish: `backend/cubeplex/skills/service.py`
- Model: `backend/cubeplex/models/skill.py`
- Sandbox path: `backend/cubeplex/skills/sandbox_paths.py`
- UI: `SkillCard.tsx`, `WorkspaceSkillCard.tsx`, skill detail panels
- Agent list: `prompts/skills.py` + catalog assembly in run path
- `load_skill`: `tools/builtin/load_skill.py`
