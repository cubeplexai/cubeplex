# Auto-Memory (proactive save + background consolidation) — Design

**Date:** 2026-05-25
**Status:** Draft for review
**Author:** (agent-assisted)

## Problem

cubeplex's agent has a memory subsystem, but it only **reads** automatically and
**writes** on explicit request:

- `MemoryMiddleware` (`backend/cubeplex/middleware/memory.py`) has only read-side
  hooks — `transform_system_prompt` (inject pinned memory) and
  `transform_context` (prepend a relevance snapshot). No write/extract hook.
- The `memory_save` tool exists (`backend/cubeplex/tools/builtin/memory.py`) but
  the system prompt contains **no guidance on when to save**. The only memory
  text (`MEMORY_PROMPT_HEADER`, `prompts/memory.py`) is read-side descriptive and
  is injected **only when pinned memory already exists**.

Result: the agent saves memory only when the user explicitly says "remember
this" (confirmed in trace `652d5f7e` — 4 `memory_save` calls in the one
conversation where the user asked; zero in task-only runs). Nothing is captured
automatically during a normal run.

## Reference: how Claude Code does it

Studied `~/claude-code/src/memdir` + `src/services/autoDream`. Two layers:

1. **Proactive inline save (prompt-driven).** No dedicated "save" tool — the
   agent writes memory files with its normal Write tool, driven by detailed
   system-prompt instructions. The proactive behavior comes from per-type
   `<when_to_save>` triggers (`memoryTypes.ts`), e.g. *"Any time the user
   corrects your approach … OR confirms a non-obvious approach worked"*, plus
   *"build up this memory system over time"* and *"if the user explicitly asks …
   save immediately"*.
2. **Background consolidation ("dream").** A separate forked agent
   (`autoDream.ts`) runs periodically — *"a separate nightly process distills
   these logs into MEMORY.md and topic files"* — doing a 4-stage
   orient→gather→consolidate→prune pass. **Trigger is cheap scheduling, not a
   per-turn LLM gate:** time since last consolidation (a lock-file `stat`, the
   lock mtime = `lastConsolidatedAt`) ≥ `minHours` **and** new-session count ≥
   `minSessions` (scanned at most every 10 min). A per-holder lock prevents
   piled-up concurrent consolidations.

cubeplex is a multi-tenant server (not a single-user CLI), and **already
persists every run's full conversation to the Postgres checkpointer
(`cubepi_messages`)** — so the "capture" raw material is free; no daily-log
needed. We adopt the same two-layer shape.

## Design — hybrid, two layers

### Layer 1 — Proactive inline save (prompt-driven)

Make the **existing** `memory_save` tool fire proactively by adding a standing
"how + when to save" block to the agent system prompt.

- **Always inject** a memory authoring block (independent of whether pinned
  memory exists). Today `MemoryMiddleware.transform_system_prompt` returns early
  when there's no pinned memory, so no authoring guidance is ever shown. Split
  the concern: the *pinned-memory block* stays conditional, but a *"how/when to
  save" block* is always present.
- **Per-type `when_to_save` triggers mapped to cubeplex's real taxonomy** (the
  `memory_save` tool's `type`: `preference`, `correction`, `procedure`,
  `project_fact`, `decision`, `org_policy`) — NOT Claude Code's
  user/feedback/project/reference. Each type gets a concrete trigger:
  - `preference` — when you learn the user's style / collaboration preferences.
  - `correction` — when the user corrects you ("don't do X"), **or** confirms a
    non-obvious approach worked ("yes, exactly"); record *why*.
  - `project_fact` / `decision` — when you learn who is doing what / why / by
    when, or a settled decision; convert relative dates to absolute.
  - `procedure` — when you learn a reusable workflow.
  - `org_policy` — when you learn an org-level rule/policy.
- **General rules:** build memory up over time; if the user explicitly asks to
  remember, save immediately; don't save what's trivially derivable from
  code/history; prefer updating an existing item (via `memory_update`) over
  creating a contradictory new one.
- **Scope guidance:** *proactive* saves go to `personal` ONLY. The model may use
  `workspace`/`org` scope solely when the user explicitly asks to share — the
  prompt must say this directly (a bare "default personal" is too weak; see B3 in
  the Layer 2 scope discussion). Layer 2 enforces personal server-side; Layer 1's
  shared-scope restriction is prompt-level (the tool stays capable for explicit
  user requests).

This is mostly prompt authoring. Reliability is model-driven (not 100%) but is
exactly what Claude Code relies on for its proactive saves; Layer 2 backstops it.

### Layer 2 — Background consolidation pass

A periodic per-user pass that extracts what Layer 1 missed, dedups, and prunes —
decoupled from the live turn so it adds no latency.

**Granularity — per CONVERSATION, not per user.** The checkpointer is keyed by
conversation (`cp.load(conversation_id)`); there is no per-user "history since T"
query, and a user has many conversations. So consolidation is **scoped to the
conversation that just ran**: it loads *that* conversation's recent history (the
API we already have) and distills into the user's `personal` memory. Dedup still
runs against the user's *entire* personal memory (read via `MemoryRepository`),
so source granularity doesn't fragment dedup. Cross-conversation aggregation is a
future enhancement, explicitly out of scope for v1.

**Scheduling — post-run self-check (decided).** At the end of each run,
`_execute_run` does a cheap per-conversation check: read this conversation's
`last_consolidated_at` and new-run counter from Redis. Fire a background
consolidation task only when **both**: `now − last ≥ minHours` **and**
`counter ≥ minRuns`. Otherwise do nothing (a couple of Redis reads). No cron, no
new worker.

**Per-conversation lock + atomic high-water-mark (multi-instance-safe).**
- A Redis lock keyed per conversation (holder id + TTL, stale reclaim) prevents
  piled-up runs and multiple instances consolidating the same conversation
  concurrently. The lock value is **holder identity only** — it is NOT the source
  of `last_consolidated_at`.
- `last_consolidated_at` is its own durable Redis key, the canonical truth.
- **Avoid losing runs that finish during consolidation** (the reset race): at the
  start, atomically capture `cutoff = now` and the current counter value `N`
  (Redis `GET`+`DECRBY` / Lua). Load history with `started_at ≤ cutoff`. On
  success, set `last = cutoff` and `DECRBY counter N` (not reset-to-0). Runs that
  arrive during consolidation increment the counter and have `started_at > cutoff`
  → counted next time, never erased. On failure, re-`INCRBY N` (restore) and leave
  `last` unchanged so it retries.

**Consolidation runtime — OneShotLLM (decided).** A single LLM call via
`cubeplex.llm.oneshot.OneShotLLM` (cheap model). Inputs: this conversation's
history up to `cutoff` (**bounded** — see window cap) + the user's existing active
personal memory items.

**Structured ops — explicit schema, validated, capped.** `OneShotLLM` returns raw
text, so the pass MUST: instruct a strict JSON op envelope, parse + schema-validate
it, reject malformed output (log + no-op), and enforce a server-side **max op
count** before applying anything. Op kinds:
- **extract** — a new durable fact Layer 1 missed (same taxonomy: `preference`,
  `correction`, `procedure`, `project_fact`, `decision`, `org_policy`);
- **merge/update** — fold into an existing item id (dedup) instead of adding a
  contradictory one;
- **archive** — mark an existing item id stale/superseded.
The op schema **omits `scope`** entirely; the service hard-codes
`MemoryScope.PERSONAL` when applying — model output cannot escalate to
`workspace`/`org`. Each written/updated item is stamped with source attribution
(`source_type=consolidation`, `source_conversation_id`, and the consolidation
window/run metadata) using the existing `source_*` fields on the memory model.

**History window cap (required, not optional).** Even within "since `last`," a
busy conversation can blow context/cost. The pass caps input by a configured
message/token budget, keeping the **most recent** messages (truncate older);
`cutoff` still advances to `now` so skipped-old content isn't re-scanned next time.

**Scope — personal only, v1 (decided + server-enforced).** Reads and writes only
the `personal` tier for that user, enforced in the service (not by prompt). For
**Layer 1**, the authoring prompt explicitly restricts *proactive* saves to
`personal`; `workspace`/`org` saves happen only when the user explicitly asks
(the `memory_save` tool stays capable for that deliberate case — we do not hard-
disable it, which would break explicit shared saves).

## Components / files

- **Layer 1:** `backend/cubeplex/prompts/memory.py` (authoring block + per-type
  triggers + "proactive saves are personal-only") and
  `backend/cubeplex/middleware/memory.py` (`transform_system_prompt` always injects
  the **static** authoring block; the variable pinned block stays conditional —
  see cache note).
- **Layer 2 scheduling:** `backend/cubeplex/streams/run_manager.py` — `_execute_run`
  per-conversation cheap check → spawn a background task tracked in an app-level
  registry (see drain-safety) with `conversation_id` + `user_id`.
- **Layer 2 service:** new `backend/cubeplex/services/memory_consolidation.py` —
  gate read, lock + atomic high-water-mark, OneShotLLM call, op
  parse/validate/cap, and applying ops via `MemoryService` (scope hard-coded
  PERSONAL, source stamped).
- **State (Redis, per conversation):** `…:memcons:last:{conversation_id}`
  (durable last-consolidated timestamp — canonical), `…:memcons:runs:{conversation_id}`
  (new-run counter, `INCR` per run, `DECRBY N` on success), `…:memcons:lock:{conversation_id}`
  (holder id + TTL).

## Data flow

1. Run completes → `_execute_run` `INCR`s the per-conversation counter; reads
   `last` + counter; if `now−last ≥ minHours AND counter ≥ minRuns` → spawn a
   tracked background consolidation task.
2. Task tries the per-conversation lock (skip if held by a live holder).
3. Atomically capture `cutoff = now` and counter value `N`.
4. Load this conversation's history up to `cutoff` (window-capped) + the user's
   existing personal memory items.
5. OneShotLLM → JSON ops; parse, validate, cap.
6. Apply ops via `MemoryService` (scope=PERSONAL, source stamped).
7. On success: `last = cutoff`, `DECRBY counter N`, release lock. On failure:
   restore counter, leave `last`, release lock.

## Failure modes

- **Consolidation LLM fails / times out / malformed JSON:** best-effort — log,
  restore counter, leave `last`, release lock; retries next eligible run. Never
  affects the live run (detached, tracked task).
- **Instance dies mid-consolidation:** lock TTL + stale reclaim lets the next
  eligible run retry; counter was only decremented on success, so nothing is lost.
- **Runs finishing during consolidation:** handled by the captured-`cutoff` +
  `DECRBY N` high-water-mark (not reset-to-0) — those runs stay counted.
- **Shutdown:** the consolidation task is tracked in an app-level registry and
  cancelled (best-effort) on shutdown — not silently abandoned like a bare
  fire-and-forget task.
- **Duplicate/contradictory/scope-escalating output:** dedup/merge against
  existing items; op schema omits `scope` and the service hard-codes PERSONAL;
  op-count cap bounds blast radius; malformed ops rejected.
- **Cost runaway:** time+run gate bounds frequency; cheap model; required window
  cap bounds input size per pass.
- **Layer 1 over-saving (noise):** Layer 2 dedup/prune is the cleanup; the Layer 1
  prompt also lists "what NOT to save."

## Testing strategy

- **Layer 1:** assert the authoring block (with per-type triggers) is present in
  the composed system prompt even with no pinned memory. A real-LLM E2E: a
  conversation that states a durable preference → assert a `memory_save` (or a
  resulting memory item) without an explicit "remember" ask.
- **Layer 2 gate (unit, fakeredis):** per-conversation counter increments per run;
  fires only when both thresholds met; lock prevents concurrent runs; the
  captured-`cutoff` + `DECRBY N` high-water-mark keeps runs that arrive during
  consolidation counted (assert a run added mid-pass is not lost); `last`
  advances only on success; counter restored on failure.
- **Layer 2 pass (unit):** given a fake conversation history + existing items and a
  stubbed OneShotLLM, assert: malformed/over-cap output is rejected (no writes);
  valid ops apply via a fake `MemoryService`; every write is scope=PERSONAL with
  source attribution stamped (model cannot set scope).
- **Real-LLM E2E (opt-in):** seed history with salient facts, force-run
  consolidation, assert personal memory items created + a duplicate merged.

## Non-goals (v1)

- Auto-writing `workspace`/`org` memory (explicit `memory_save` only).
- A forked consolidation agent with tools (OneShotLLM one-shot for v1).
- A cron/worker scheduler (post-run self-check only).
- Changing the read/relevance side (`compute_relevance_snapshot`, pinned block).
- Cross-conversation per-user aggregation (v1 consolidates per conversation).

## Prompt-cache note

The Layer 1 authoring block is **static** — no timestamps, per-user counts, or
thresholds — so always-injecting it keeps the cache-eligible system-prompt prefix
byte-stable. The variable pinned-memory block remains the only memory-driven
suffix (unchanged from today). See `backend/docs/prompt-cache-discipline.md`.

## Open questions

- Default thresholds (`minHours`, `minRuns`) — start conservative (e.g. minHours
  ≈ 6, minRuns ≈ 5) and tune; expose via config.
- Window-cap budget (message count vs token budget) and exact value — required
  parameter (see Layer 2); default needs picking (e.g. last ~40 messages or a
  token budget aligned with the cheap model's context).
