# Memory: workspace-bound personal scope — Design

**Date:** 2026-07-26  
**Status:** Draft for review  
**Issue:** [#419](https://github.com/cubeplexai/cubeplex/issues/419)  
**Branch:** `feat/2026-07-26-memory-workspace-personal`

## Goal

Make personal memory mean **private memory about me in the current workspace**
(not a cross-workspace global profile), and align the write path
(reflection, consolidation, agent tools, server dedup) so new rows stop
polluting other workspaces and unbounded personal dumps.

## Context

### What scope means today (implementation)

`scope` is an **access / mount / injection boundary**, not an ontology of
“about user vs about project”:

| scope | Mount | Who sees | When injected |
|-------|--------|----------|----------------|
| personal | `owner_user_id` only; `workspace_id` NULL | only that user | **every** workspace the user enters |
| workspace | `(org_id, workspace_id)` | workspace members | only that workspace |
| org | `org_id` | org members | workspaces in that org |

### Why this hurts

1. **Cross-workspace injection** — sandbox paths, skill versions, task IDs,
   project facts learned in one workspace ride along into every other workspace
   for the same user.
2. **Write path defaults** — authoring, reflection, and consolidation treat
   proactive saves as `scope=personal` (consolidation **hard-codes** PERSONAL
   on extract). Reflection runs after almost every successful run; consolidation
   after ~5 runs / 6h. Power users accumulate hundreds of auto-written rows
   (mostly REFLECTION + CONSOLIDATION).
3. **Dedup is weak** — only exact content match; no normalize. Near-duplicate
   preferences stack (e.g. many “prefers Chinese” variants).
4. **Prior design docs** described personal as org-independent and following
   the user everywhere. Site docs also default “even project facts → personal”.
   That product choice is what we reverse for v1 simplicity.

### What we are *not* solving with content classifiers

- No process-state regex score tables (overfit to one user’s dirty data).
- No L1 string-similarity dedup on write (false friends: “raining” vs “not
  raining”).
- Semantic merge stays agent-led (`memory_search` then update) or later
  consolidation LLM — not a sync write-path LLM call.

## Approaches considered

| Approach | Idea | Trade-off |
|----------|------|-----------|
| A. Prompt-only | Soften authoring/reflection text | Cheap; models skip; no mount fix |
| B. **Workspace-bound personal (chosen)** | Personal = `(user, workspace)` private slice; L0 server dedup; fix reflection/consolidation scope | Migration + invariant change; matches simple product story |
| C. Keep global personal; ban `project_fact` in personal | Type×scope matrix without remounting | Still injects private project notes everywhere; fights the mount model |

**Choice: B.** Scope answers *who sees / where it hangs / where it injects*.
“About me in this workspace” is enforced by **mount + filter**, not by guessing
sentence topics.

## Design

### 1. Product definition (frozen)

| Term | Meaning |
|------|---------|
| **personal** | Private memory **about me in the current workspace**. Only I can see it. **Does not cross workspaces.** |
| **workspace** | Shared memory for the current workspace. |
| **org** | Unchanged for this work. |
| **Cross-workspace personal / global user profile** | **Out of scope.** Future feature if needed. |

Type (`preference`, `project_fact`, …) stays a content shape for ranking/UI.
It is **not** an ACL. Personal may still hold a project_fact if it is *my*
private note **in this workspace** — it simply will not appear in other
workspaces.

### 2. Data model invariants

New rules for `memory_items`:

```
scope=personal  ⇒ owner_user_id set
                  workspace_id set   ← NEW (required)
                  org_id set to the workspace’s org (denormalized, same pattern
                  as other workspace-scoped rows when practical)

scope=workspace ⇒ workspace_id + org_id set; owner_user_id NULL
scope=org       ⇒ org_id set; workspace_id NULL; owner_user_id NULL
```

Indexes: replace or complement `ix_memory_personal (scope, owner_user_id)` with
one that includes `workspace_id`, e.g. `(scope, owner_user_id, workspace_id)`.

### 3. Write path

**`MemoryService.create` (personal):**

- Require a request `workspace_id` (and org). If missing → reject (clear error).
- Stamp `owner_user_id`, `workspace_id`, and org denormalize on the row.
- Do not accept “global personal” creates.

**L0 dedup (server, no LLM):**

1. `normalize(content)`: strip, collapse whitespace; case-fold is optional but
   should be consistent and documented (recommend case-fold for Latin letters
   only or full `casefold` — pick one and test).
2. Within the same scope key (`personal` ⇒ same owner + workspace; `workspace`
   ⇒ same workspace; `org` ⇒ same org) and same `type`, if an active row with
   the same normalized content exists → **do not insert**; bump `updated_at`
   (or return existing id). Prefer storing `content_hash` of the normalized
   form if that simplifies the query.

No L1 similarity. No process-state content scoring.

**Secrets (optional but recommended in Phase 1):** extend the existing shared
memory screen’s narrow secret patterns to personal writes as well. Do not grow
this into domain-specific “ops journal” detectors.

### 4. Read / injection path

**`MemoryRepository` personal filter:**

```
scope=personal AND owner_user_id = current_user
              AND workspace_id = current_workspace
```

Injection (`MemoryMiddleware` pinned + relevance snapshot) already lists via
the repo with request context — once the filter is correct, **other workspaces’
personal rows never enter the prompt**.

Memory Center / list APIs for personal must use the same filter (current
workspace from the route).

### 5. Background memory jobs (today vs target)

There are **two** post-run writers besides the main agent’s in-turn
`memory_save`. Both must be redesigned with the new personal mount; fixing only
the table shape without changing these jobs will keep flooding each workspace’s
personal slice.

```
Main agent run succeeds
        │
        ├─► Reflection     — almost every successful run (fire-and-forget)
        │
        └─► Consolidation  — only when Redis gate says due (default 5 runs + 6h)
```

#### 5.1 Reflection — current implementation

| Piece | Behavior today |
|-------|----------------|
| Trigger | `run_manager` after a **successful** main run; `asyncio.create_task`; never blocks the user path |
| Shape | Detached `cubepi.Agent` with **only** `memory_search` / `memory_save` / `memory_update` |
| Model | **Same model as the main conversation** (`provider.model(model_id)`) |
| Timeout | `ReflectionRunner` default **30s**; failures logged and swallowed |
| Idempotency | In-process `run_id` set (same process won’t reflect twice) |
| Seed input | Last-turn USER + ASSISTANT text; optional **tool_summaries** for the turn; up to **40** active personal items (recent order, content truncated to 200 chars) |
| System prompt | `REFLECTION_SYSTEM_PROMPT`: most turns save nothing; search before save; **“Scope: use personal unless the user explicitly said to share with the team.”** |
| Source stamp | `source_type=REFLECTION` via ContextVar |
| Side effect | If any save/update succeeded → `UserEventType.MEMORY_UPDATED` for the frontend |

**Problems today**

1. Default personal + old “global personal” mount → project/tool facts land in
   personal and follow the user everywhere.
2. Seed list is labeled “personal, active” with no workspace awareness; after
   remount, seed must be **this workspace only**.
3. No hard cap on creates per run (model can spam `memory_save`).
4. Search-before-save is prompt-only (server L0 exact is the hard fallback).
5. Using the main chat model is expensive and often verbose → more over-save.

#### 5.2 Reflection — target design

**Keep:** fire-and-forget, timeout/swallow, memory-only tools, MEMORY_UPDATED
event, per-run scheduling after success.

**Change:**

| Piece | Target |
|-------|--------|
| Service factory | Always bound to **current run’s** `user_id` / `org_id` / `workspace_id` so every personal write stamps that workspace |
| Existing-memory seed | List **this workspace’s** personal (limit still ~40, recent). Optionally append a short list of **this workspace’s** shared items (e.g. 10–20) so reflection can `update`/`skip` instead of re-extracting team facts as personal. Label seed blocks clearly: “personal (this workspace)” / “workspace (shared)” |
| System prompt | Rewrite scope rules (see §5.4). Keep “when in doubt, do not save” and search-before-save |
| Create policy (soft in prompt, hard via service) | Prefer `preference` / `correction` about the user into **personal**; project/team durable facts the user would share → **workspace** only when appropriate (or skip if unsure). Never invent org scope in reflection |
| Create budget (recommended this work) | Cap **successful `memory_save` creates** per reflection run (e.g. **≤ 2**). Updates unlimited. Enforce in tool wrapper or runner listener — not only in prose |
| Model (recommended this work if cheap) | Use a **task preset** (e.g. existing `summarize` or a dedicated `memory` preset) instead of the main conversation model when a preset is configured; fall back to main model if missing. Document in config |

**Does not change in this work:** reflection remains per-turn (not disabled).
No requirement that the tool trace prove `memory_search` ran before save.

#### 5.3 Consolidation — current implementation

| Piece | Behavior today |
|-------|----------------|
| Trigger | Every finished run calls `note_run`; `should_consolidate` requires **≥ min_runs (default 5)** and **≥ min_hours (default 6)** since last consolidate; Redis lock |
| Shape | Single `provider.generate` (no tools); model from task preset **`summarize`** |
| Input | Last **40** checkpoint messages + up to **200** active **personal** items |
| Output | JSON `{"ops":[...]}` — `extract` / `merge` / `archive`; max **20** ops; `max_output_tokens=1500` |
| extract | **`scope` hard-coded `MemoryScope.PERSONAL`** in `apply_ops` |
| merge/archive | Only if `repo.get(id)` is personal (ignores workspace targets) |
| Prompt | “distill a conversation into durable **PERSONAL** memory” |

**Problems today**

1. Extract always personal → same global dump as reflection, often **stacking** on top of per-turn reflection writes.
2. Prompt biases toward more extracts; merge/archive underused → growth not cleanup.
3. Existing list is personal-only and was global; after remount must be per-workspace and should include workspace items if we allow workspace extract/merge.

#### 5.4 Consolidation — target design

**Keep:** Redis gate defaults (5 runs / 6h), lock, oneshot/trace optional, JSON
ops envelope, apply via `MemoryService` (so L0 dedup + workspace stamp apply).

**Change:**

| Piece | Target |
|-------|--------|
| Existing memory input | Active **personal for this workspace** + active **workspace** items (caps e.g. 100 personal + 100 workspace, or 200 total with clear labels and ids) |
| extract | Ops include **`scope`: `personal` \| `workspace`** (validated). Personal extract goes through service with run’s workspace_id. Workspace extract requires workspace context. **Reject / skip** extract with missing or invalid scope. No more hard-code-only PERSONAL |
| Default when model omits scope | Prefer **skip** (or map: preference/correction → personal; project_fact/procedure/decision → workspace). Do not invent org in consolidation |
| merge / archive | Allowed for personal (owner + this WS) **or** workspace items the user can write; still reject foreign ids |
| Prompt | Distill **this workspace**: private facts about the user (personal) and shared durable project knowledge (workspace). **Prefer merge and archive over extract.** Cap extract ops softly in prompt (e.g. at most a few extracts per pass); most ops should be merge/archive when the list is large |
| Role vs reflection | Consolidation is the **session-level curator** (merge near-duplicates the model can see, archive clearly stale ids, fill rare misses). Reflection is the **per-turn catch** for clear new preferences/corrections. Both must share the same scope story so they do not double-write global personal |

**Gate timing:** leave min_runs / min_hours as config defaults; no need to change
unless product wants less frequent extract pressure after prompt changes.

#### 5.5 Shared prompt rules (authoring + reflection + consolidation)

One product story, three surfaces:

**Main agent — `MEMORY_AUTHORING_BLOCK`**

- Drop “proactive saves are ALWAYS `scope=personal`”.
- personal = durable facts about how *I* work **in this workspace**.
- workspace = shared project facts / procedures / decisions for the team.
- org only when the user explicitly asks to share with the organization.
- Before write: `memory_search`; then save / update / archive.
- Soft: no secrets, no one-off task state.

**Reflection — `REFLECTION_SYSTEM_PROMPT`**

- Same scope definitions as above.
- Default: **save nothing**.
- When saving: personal for user prefs/corrections; workspace only when the
  fact is clearly shared project knowledge worth the team; if unsure → skip.
- Always search (or use the seed list) before save; update existing id when
  related.
- Remove “use personal unless user said share” as the sole rule.

**Consolidation — `CONSOLIDATION_SYSTEM`**

- Output JSON ops only; scopes on extract.
- Prefer merge/archive; few extracts.
- Never secrets; never global personal (no null workspace).

#### 5.6 Dual-write risk and how we accept it

Reflection and consolidation can both write after the same conversation window.
Mitigations (no L1 similarity):

1. Both go through **L0 normalize exact** on create.
2. Reflection **create budget** (≤ N saves).
3. Consolidation **prefers merge** against the seeded id list.
4. Same workspace key so they at least do not cross-pollute other workspaces.

Semantic near-duplicates may still exist until a human archives them or a
future LLM merge pass improves — accepted for this design.

### 6. Migration / legacy rows

Existing personal rows have `workspace_id IS NULL`.

1. **Backfill** where `source_conversation_id` points at a conversation with a
   workspace: set `workspace_id` (and org if needed) from that conversation.
2. **Orphans** (no source, or conversation missing): leave `workspace_id` NULL
   temporarily; **exclude from injection and agent list** until fixed or
   archived. Memory Center may show an “unassigned” bucket later; not required
   for launch if orphans are rare.
3. No bulk content cleanup of dirty power-user histories in this work.

### 7. Site docs

Update `docs/site/docs/guides/memory/*` (and zh if mirrored) in the **same PR**
as behavior changes:

- Personal = only you, **in this workspace**.
- Remove “even project fact defaults to personal that follows you everywhere”
  language.
- Clarify sharing still requires explicit workspace/org save when the user wants
  teammates to see it.

## Out of scope

- Cross-workspace personal / global user profile.
- L1 similarity or write-path LLM/embedding dedup.
- Process-state content score tables.
- Bulk archive/rewrite of historical dirty content.
- Changing type enum membership.
- Requiring tool-trace proof of `memory_search` before every save.
- Disabling reflection entirely or redesigning the Redis consolidation gate
  schedule (defaults stay unless config already overrides).

Optional enhancements that may land in the **same PR** if small, else later
commits on the same branch: pinned ~1500 token budget, `touch_used` on inject,
soft active cap per (user, workspace).

## Success criteria

1. User writes personal memory in workspace A; starting a conversation in
   workspace B does **not** inject that item (e2e or integration with real
   session + repo list).
2. Every **new** personal row has `workspace_id` equal to the write context;
   create without workspace context fails.
3. Second `create` with the same scope key, type, and normalized content does
   not insert a second active row.
4. **Reflection:** seed lists this-workspace memory only; personal creates from
   reflection stamp the run’s workspace; prompt no longer says default global
   personal.
5. **Consolidation:** `apply_ops` extract no longer hard-codes global personal;
   extract either carries validated scope or stamps personal via service with
   workspace_id; existing-memory input is workspace-scoped; prompt prefers
   merge/archive.
6. Site memory guide matches the new personal definition when code ships.

## Delivery

All design and implementation work for this effort lands on **one branch / one
PR** ([#420](https://github.com/cubeplexai/cubeplex/pull/420)): iterative
commits (spec → schema → service → reflection/consolidation → docs/tests), not
a stack of follow-up PRs.
