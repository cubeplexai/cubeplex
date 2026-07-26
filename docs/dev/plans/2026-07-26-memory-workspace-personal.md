# Plan: Memory workspace-bound personal scope

**Goal:** Ship workspace-bound personal memory and aligned write paths so
personal no longer follows users across workspaces.

**Architecture:** Personal rows mount on `(owner_user_id, workspace_id)`. All
reads/injection filter by current workspace. Writes stamp the run’s workspace.
Reflection and consolidation use the same `MemoryService` path (no global
personal extract). Server dedup is L0 normalize+exact only.

**Tech stack:** Postgres + Alembic, `MemoryService` / `MemoryRepository`,
run_manager reflection hook, consolidation Redis gate, site docs under
`docs/site/docs/guides/memory/`.

**Spec:** [2026-07-26-memory-workspace-personal-design.md](../specs/2026-07-26-memory-workspace-personal-design.md)  
**Issue:** [#419](https://github.com/cubeplexai/cubeplex/issues/419)

---

## Unit 1 — Schema and migration

**Files**

- `backend/cubeplex/models/memory.py` — document/assert personal may carry
  `workspace_id` (and org when set).
- `backend/alembic/versions/<rev>_personal_memory_workspace.py` — autogenerate
  index changes; data backfill in migration or a short follow-up script under
  `backend/scripts/dev/` if migration would be too heavy.

**Interfaces**

- Invariant after migration for **new** writes: personal always has
  `workspace_id`.
- Index supporting list by `(scope, owner_user_id, workspace_id)`.
- Backfill: `UPDATE memory_items SET workspace_id = c.workspace_id … FROM
  conversations c WHERE memory_items.source_conversation_id = c.id AND
  memory_items.scope = 'PERSONAL' AND memory_items.workspace_id IS NULL`
  (enum label casing as in DB).

**Core logic**

- Orphans remain NULL; injection excludes them (Unit 2).
- No content rewrite.

**Tests**

- Migration smoke / e2e: after create path, personal row has non-null
  `workspace_id` (Unit 2 covers create). Backfill correctness can be unit-tested
  against a fixture session if migration embeds SQL.

---

## Unit 2 — Service + repository + injection

**Files**

- `backend/cubeplex/services/memory.py` — personal create requires
  `self.workspace_id`; stamp fields; reject if missing.
- `backend/cubeplex/repositories/memory.py` — personal clause adds
  `workspace_id == self.workspace_id`; items with NULL workspace_id not readable
  in normal list/get for injection.
- `backend/cubeplex/middleware/memory.py` — no special case if repo is correct;
  verify list calls pass workspace context.
- API routes that construct `MemoryService` / `MemoryRepository` — ensure
  workspace from path/deps is always passed for workspace-scoped pages.

**Interfaces**

- `CreateMemoryInput` unchanged shape; behavior change on personal only.
- `MemoryPermissionError` or clear validation error when personal write lacks
  workspace context.
- `find_exact` / L0 later: same scope key including workspace for personal.

**Core logic**

```
create(personal):
  if not workspace_id: raise
  row.owner_user_id = user_id
  row.workspace_id = workspace_id
  row.org_id = org_id  # if available
```

```
list personal:
  scope=personal AND owner=user AND workspace_id=current_ws
```

**Tests**

- **unit:** create personal without workspace fails; with workspace stamps ids.
- **unit/e2e:** same user, two workspaces — list/inject in WS2 does not see WS1
  personal (business invariant: isolation). Prefer e2e if it hits real
  AsyncSession + app deps; unit with repo session is enough for filter math.

---

## Unit 3 — L0 normalize exact dedup

**Files**

- `backend/cubeplex/services/memory.py` (or small helper module
  `memory_normalize.py`) — `normalize_memory_content(str) -> str`.
- `backend/cubeplex/repositories/memory.py` — `find_exact` uses normalized
  comparison (or hash column if added in Unit 1).

**Interfaces**

- Normalize: strip + collapse internal whitespace; document case handling.
- On hit: return existing row after bump `updated_at` (current exact behavior).

**Core logic**

- Compare within same scope identity (personal includes workspace) and type.
- No similarity threshold.

**Tests**

- **unit:** `"  foo   bar "` and `"foo bar"` collide; `"raining"` vs `"not
  raining"` do **not** collide.

---

## Unit 4 — Reflection + consolidation + prompts

**Files**

- `backend/cubeplex/prompts/memory.py` — authoring block: personal = this WS
  about me; workspace for shared; search-first; drop “ALWAYS personal”.
- `backend/cubeplex/prompts/reflection_system.py` — same scope story; seed
  wording.
- `backend/cubeplex/services/reflection_runner.py` — seed label “this
  workspace”; load personal filtered by workspace (repo already).
- `backend/cubeplex/streams/run_manager.py` — only if reflection factory must
  pass workspace (already has `ctx.workspace_id`).
- `backend/cubeplex/services/memory_consolidation.py` — stop hard-coding
  extract → PERSONAL without workspace; apply_ops uses service create with
  correct scope; prompt + existing list include this-WS personal; extract
  personal still goes through service (stamps workspace). Prefer allowing
  extract with scope field in JSON **or** default extract to personal **with**
  service stamp — never NULL workspace_id.

**Interfaces**

- Consolidation op extract: either
  - `{"action":"extract","scope":"personal"|"workspace", "type", "content"}`
    validated allow-list, or
  - keep type-only extract but always `create(scope=PERSONAL)` via service that
    stamps workspace (minimum bar). Prefer explicit scope in ops if cheap.
- merge/archive: may target personal **or** workspace items the user can write;
  keep safety (no cross-user).

**Core logic**

- Reflection existing items: `list(scope=PERSONAL, …)` after Unit 2 is already
  workspace-scoped.
- Consolidation `apply_ops` extract must not write personal with NULL
  workspace_id.

**Tests**

- **unit:** `apply_ops` extract with service mock/fake stamps workspace (or
  rejects).
- **unit:** parse_ops accepts new shape if introduced.
- Prompt changes: no behavioral test required beyond string presence if
  useful; prefer not snapshot-flaking full prompts.

---

## Unit 5 — Site docs (same PR as behavior)

**Files**

- `docs/site/docs/guides/memory/overview.md`
- `docs/site/docs/guides/memory/using-memory.md`
- `docs/site/docs/guides/memory/managing-memory.md`
- zh-Hans mirrors if they duplicate the same claims

**Core logic**

- Personal: only you, **current workspace**.
- Remove “project fact without instruction → personal that follows you
  everywhere”.
- Default save guidance matches authoring prompt.

**Tests**

- None automated; review in PR.

---

## Unit 6 — Optional Phase 2 (separate PR)

- Pinned token budget ~1500 (`middleware/memory.py`).
- `touch_used` when items enter pinned/relevance snapshot.
- Soft active cap per (user, workspace).
- Reflection model → cheaper task preset.

Not required to close the isolation invariant.

---

## Spec coverage

| Spec requirement | Unit |
|------------------|------|
| personal `(user, workspace)` mount | 1, 2 |
| no cross-WS injection | 2 tests |
| L0 dedup | 3 |
| reflection/consolidation aligned | 4 |
| site docs | 5 |
| no L1 / no process-state scorer | explicit non-work |
| Phase 2 budgets | 6 optional |

## Suggested PR split

1. **This PR:** spec + plan only — closes [#419](https://github.com/cubeplexai/cubeplex/issues/419) as the design deliverable.
2. **Code PR A:** Units 1–2 (schema + service/repo isolation + tests).
3. **Code PR B:** Units 3–5 (L0 dedup, prompts/reflection/consolidation, site docs).
4. **Code PR C (optional):** Unit 6 Phase 2 injection budgets.
