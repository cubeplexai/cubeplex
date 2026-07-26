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

### 5. Reflection (post-run, almost every success)

Keep the fire-and-forget agent shape. Change:

| Item | Today | Target |
|------|--------|--------|
| Existing memory seed | personal only, “global” wording | **This workspace’s** personal (and optionally a short workspace list for awareness) |
| Prompt scope line | “use personal unless user asked to share” | personal = **about me in this workspace**; shared project/team facts → `workspace` when appropriate; default skip |
| Create stamp | via MemoryService (will gain workspace_id) | same service factory with **current run’s workspace** |

Still soft: search before save/update. Optional later: max N creates per run;
cheaper model preset — not required for the first correctness PR.

### 6. Consolidation (gated: default 5 runs + 6h)

Keep Redis gate and JSON ops. Change:

| Item | Today | Target |
|------|--------|--------|
| Existing list | personal only (limit 200) | this WS personal (+ workspace items for merge context) |
| extract scope | **hard-coded PERSONAL** | pass through service with correct scope from op **or** map types to default scope; **never** write global personal |
| Prompt | “durable PERSONAL memory” | distill **this workspace’s** private (personal) and optionally shared (workspace) durable knowledge; prefer merge/archive over extract |

Ops still go through `MemoryService` so L0 dedup and personal workspace stamping apply.

### 7. Agent authoring prompt

Replace “proactive saves are ALWAYS personal” with:

- **personal** — durable facts about how *I* work **in this workspace**
  (preferences, corrections about me).
- **workspace** — shared project facts, procedures, decisions for the team.
- Before save: `memory_search`; then save / update / archive.
- Do not save secrets or one-off task state (soft guidance only).

### 8. Migration / legacy rows

Existing personal rows have `workspace_id IS NULL`.

1. **Backfill** where `source_conversation_id` points at a conversation with a
   workspace: set `workspace_id` (and org if needed) from that conversation.
2. **Orphans** (no source, or conversation missing): leave `workspace_id` NULL
   temporarily; **exclude from injection and agent list** until fixed or
   archived. Memory Center may show an “unassigned” bucket later; not required
   for launch if orphans are rare.
3. No bulk content cleanup of dirty power-user histories in this work.

### 9. Site docs

Update `docs/site/docs/guides/memory/*` (and zh if mirrored) in the same
implementation PR as behavior changes:

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
- Pinned token budget / `touch_used` / soft caps (Phase 2 follow-up; may land
  in a later PR after scope correctness).
- Requiring tool-trace proof of `memory_search` before every save.

## Success criteria

1. User writes personal memory in workspace A; starting a conversation in
   workspace B does **not** inject that item (e2e or integration with real
   session + repo list).
2. Every **new** personal row has `workspace_id` equal to the write context;
   create without workspace context fails.
3. Second `create` with the same scope key, type, and normalized content does
   not insert a second active row.
4. Reflection seed text and consolidation extract path do not assume global
   personal; consolidation extract no longer hard-codes only PERSONAL without
   workspace stamp.
5. Site memory guide matches the new personal definition when code ships.

## Phasing (implementation PRs after this design PR)

| Phase | Deliverable |
|-------|-------------|
| **0** | Invariant + migration/backfill + service/repo/inject + tests + site docs |
| **1** | L0 normalize dedup + authoring/reflection/consolidation prompts + consolidation scope fix |
| **2** (optional) | Pinned ~1500 token budget, `touch_used`, soft active caps, cheaper reflection model |

This design PR ships the spec (+ plan). Code follows in separate PR(s).
