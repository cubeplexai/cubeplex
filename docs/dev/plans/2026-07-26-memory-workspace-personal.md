# Plan: Memory workspace-bound personal scope

**Goal:** Ship workspace-bound personal memory and aligned write paths so
personal no longer follows users across workspaces.

**Architecture:** Personal rows mount on `(owner_user_id, workspace_id)`. All
reads/injection filter by current workspace. Writes stamp the run’s workspace.
Reflection (per-run) and consolidation (gated) use the same `MemoryService`
path with rewritten scope prompts; consolidation extract is no longer
hard-coded global personal. Server dedup is L0 normalize+exact only.

**Tech stack:** Postgres + Alembic, `MemoryService` / `MemoryRepository`,
`reflection_runner` + `run_manager` hook, `memory_consolidation` Redis gate,
site docs under `docs/site/docs/guides/memory/`.

**Spec:** [2026-07-26-memory-workspace-personal-design.md](../specs/2026-07-26-memory-workspace-personal-design.md)  
**Issue:** [#419](https://github.com/cubeplexai/cubeplex/issues/419)  
**PR:** [#420](https://github.com/cubeplexai/cubeplex/pull/420) — **all commits for this work go on this PR**

---

## Unit 1 — Schema and migration

**Files**

- `backend/cubeplex/models/memory.py` — personal carries `workspace_id` (and org when set).
- `backend/alembic/versions/<rev>_…py` — index + backfill from source conversation.

**Interfaces**

- New writes: personal always has `workspace_id`.
- Index `(scope, owner_user_id, workspace_id)` (or equivalent).
- Backfill from `source_conversation_id → conversations.workspace_id`.

**Core logic**

- Orphans (`workspace_id` NULL) remain; excluded from injection (Unit 2).

**Tests**

- Create path stamps non-null `workspace_id` (Unit 2). Backfill covered if SQL
  lives in migration/helpers with a focused test.

---

## Unit 2 — Service + repository + injection

**Files**

- `backend/cubeplex/services/memory.py`
- `backend/cubeplex/repositories/memory.py`
- `backend/cubeplex/middleware/memory.py` (verify only if needed)
- Memory API deps that construct service/repo

**Interfaces**

- Personal create without workspace → error.
- List/get personal: `owner == user AND workspace_id == current`.

**Tests**

- unit: stamp / reject.
- unit or e2e: same user, WS1 vs WS2 isolation on list/inject.

---

## Unit 3 — L0 normalize exact dedup

**Files**

- normalize helper + `MemoryService.create` / `find_exact`

**Interfaces**

- strip + collapse whitespace; documented case handling.
- Hit → bump existing, no second row.

**Tests**

- unit: whitespace collision; `"raining"` vs `"not raining"` do not collide.

---

## Unit 4 — Reflection

**Files**

- `backend/cubeplex/prompts/reflection_system.py`
- `backend/cubeplex/services/reflection_runner.py` (seed labels, optional create cap)
- `backend/cubeplex/streams/run_manager.py` (factory already has workspace; wire
  create budget / model preset if implemented here)
- `backend/cubeplex/tools/builtin/memory.py` if create cap is tool-side for
  reflection only

**Interfaces**

- Seed: “personal (this workspace)” list via repo (post Unit 2); optional short
  workspace shared list.
- Prompt: scope rules per spec §5.4–5.5; remove global-personal default.
- Create budget: ≤ N `memory_save` successes per reflection run (recommend 2).
- Model: prefer task preset when configured (optional same PR).

**Core logic**

- Reflection does not bypass `MemoryService`; personal always stamped.
- Failures still fire-and-forget.

**Tests**

- unit: seed builder labels / only includes current-ws items when given fixtures.
- unit: create budget stops further saves after N (if implemented).
- unit: prompt string contains this-workspace scope language (light).

---

## Unit 5 — Consolidation

**Files**

- `backend/cubeplex/services/memory_consolidation.py`
  (`CONSOLIDATION_SYSTEM`, `parse_ops`, `apply_ops`, existing list load)

**Interfaces**

- extract op: `scope` ∈ {`personal`,`workspace`} **or** documented default
  mapping; never write personal with NULL `workspace_id`.
- Existing memory prompt input: this-WS personal + workspace items with ids.
- merge/archive: personal (this user+ws) or writable workspace items.
- Prompt: prefer merge/archive; few extracts; this-workspace framing.

**Core logic**

```
extract:
  resolve scope (explicit or mapped)
  service.create(scope=..., ...)  # personal stamps workspace_id
merge/archive:
  get(id); allow personal|workspace under write rules
```

**Tests**

- unit: `parse_ops` with scope field.
- unit: `apply_ops` extract personal uses service with workspace (mock/fake);
  no hard-coded path that nulls workspace_id.
- unit: merge/archive rejects foreign / wrong-scope ids as today.

---

## Unit 6 — Main-agent authoring prompt + site docs

**Files**

- `backend/cubeplex/prompts/memory.py` — `MEMORY_AUTHORING_BLOCK`
- `docs/site/docs/guides/memory/overview.md`
- `docs/site/docs/guides/memory/using-memory.md`
- `docs/site/docs/guides/memory/managing-memory.md`
- zh-Hans mirrors if they copy the old global-personal claims

**Core logic**

- Align with spec §5.5 and product definition.
- No “project fact without instruction → global personal”.

**Tests**

- None automated for docs; prompt presence checks optional.

---

## Unit 7 — Optional same-PR enhancements

- Pinned ~1500 token budget (`middleware/memory.py`).
- `touch_used` when items enter pinned/relevance snapshot.
- Soft active cap per (user, workspace).

Ship only if they stay small; otherwise leave for a later commit on **#420**.

---

## Spec coverage

| Spec requirement | Unit |
|------------------|------|
| personal `(user, workspace)` mount | 1, 2 |
| no cross-WS injection | 2 |
| L0 dedup | 3 |
| reflection redesign | 4 |
| consolidation redesign | 5 |
| authoring + site docs | 6 |
| optional inject budgets | 7 |

## Delivery (single PR)

All work is committed to branch
`feat/2026-07-26-memory-workspace-personal` and PR **#420**.

Suggested commit order (not separate PRs):

1. Spec/plan (done + updates)
2. Units 1–2 (schema + isolation)
3. Unit 3 (L0 dedup)
4. Units 4–5 (reflection + consolidation)
5. Unit 6 (authoring + site docs)
6. Unit 7 if cheap
7. Tests green; PR ready for review
