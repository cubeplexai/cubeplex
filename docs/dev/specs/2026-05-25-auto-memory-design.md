# Auto-Memory (proactive save + background consolidation) — Design

**Date:** 2026-05-25
**Status:** Draft for review
**Author:** (agent-assisted)

## Problem

cubebox's agent has a memory subsystem, but it only **reads** automatically and
**writes** on explicit request:

- `MemoryMiddleware` (`backend/cubebox/middleware/memory.py`) has only read-side
  hooks — `transform_system_prompt` (inject pinned memory) and
  `transform_context` (prepend a relevance snapshot). No write/extract hook.
- The `memory_save` tool exists (`backend/cubebox/tools/builtin/memory.py`) but
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

cubebox is a multi-tenant server (not a single-user CLI), and **already
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
- **Per-type `when_to_save` triggers mapped to cubebox's real taxonomy** (the
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
- **Scope guidance:** default `personal`; the tool description already explains
  `workspace`/`org`.

This is mostly prompt authoring. Reliability is model-driven (not 100%) but is
exactly what Claude Code relies on for its proactive saves; Layer 2 backstops it.

### Layer 2 — Background consolidation pass

A periodic per-user pass that extracts what Layer 1 missed, dedups, and prunes —
decoupled from the live turn so it adds no latency.

**Scheduling — post-run self-check (decided).** At the end of each run,
`_execute_run` does a cheap check for that run's user: read `last_consolidated_at`
and the count of new runs since, both from Redis. Fire a background
consolidation task only when **both**: time since last ≥ `minHours` **and** new
runs since ≥ `minRuns`. Otherwise do nothing (a couple of Redis reads). No cron,
no new worker — active users naturally trigger it (mirrors Claude Code's
per-turn stat gate).

**Per-user lock (decided multi-instance-safe).** A Redis lock keyed per user
(holder id + TTL, stale reclaim) prevents piled-up runs and multiple instances
from consolidating the same user concurrently. The lock's value carries
`last_consolidated_at` semantics (set on successful completion).

**Consolidation runtime — OneShotLLM (decided).** A single LLM call via
`cubebox.llm.oneshot.OneShotLLM` (already used by compaction/title) with a cheap
model — not a forked agent. Inputs:
- the user's conversation history since `last_consolidated_at` (already in the
  checkpointer — free), and
- the user's existing active memory items (from `MemoryRepository`).

The call returns a structured set of operations, applied via `MemoryService`/
`MemoryRepository`:
- **extract** — salient durable facts Layer 1 missed (same taxonomy/types);
- **dedup/merge** — fold duplicates into existing items rather than adding
  contradictory ones;
- **prune/archive** — mark stale/superseded/low-value items `archived`.

**Scope — personal only, v1 (decided).** The pass reads/writes only the
`personal` tier for that user. `workspace`/`org` memory stays explicit
(`memory_save`) — auto-writing shared/sensitive scopes is deferred.

## Components / files

- **Layer 1:** `backend/cubebox/prompts/memory.py` (add the authoring block +
  per-type triggers) and `backend/cubebox/middleware/memory.py`
  (`transform_system_prompt` always injects the authoring block; pinned block
  stays conditional).
- **Layer 2 scheduling:** `backend/cubebox/streams/run_manager.py`
  (`_execute_run` post-run cheap check → fire background task).
- **Layer 2 service:** a new `backend/cubebox/services/memory_consolidation.py`
  — the gate read, the lock, the OneShotLLM pass, and applying ops via
  `MemoryService`. (New file = one clear responsibility; keeps `run_manager` thin.)
- **State:** Redis keys per user — `…:memcons:last:{user_id}` (last consolidated
  at), `…:memcons:runs:{user_id}` (new-run counter, incremented per run),
  `…:memcons:lock:{user_id}` (lock). Counter resets on successful consolidation.

## Data flow

1. Run completes → `_execute_run` increments the per-user run counter; reads
   `last` + counter; if `now-last ≥ minHours AND counter ≥ minRuns` → spawn a
   background consolidation task (fire-and-forget, like the existing drainers).
2. Task acquires the per-user lock (skip if held). 
3. Loads recent history (since `last`) + existing personal memory items.
4. OneShotLLM pass → ops (extract/merge/archive).
5. Apply ops via `MemoryService`; set `last = now`, reset counter; release lock.

## Failure modes

- **Consolidation LLM fails / times out:** best-effort — log, release lock, leave
  `last`/counter unchanged so it retries next eligible run. Never affects the
  live run (it's a detached background task).
- **Instance dies mid-consolidation:** lock TTL + stale reclaim lets the next
  eligible run retry; counter unchanged so nothing is lost.
- **Duplicate/contradictory writes:** the pass dedups against existing items and
  prefers merge/update; the prompt forbids creating contradictory items.
- **Cost runaway:** the time+run gate bounds frequency per user; OneShotLLM uses
  the cheap model; history window is bounded to "since last_consolidated_at".
- **Layer 1 over-saving (noise):** Layer 2's dedup/prune is the cleanup; the
  Layer 1 prompt also lists "what NOT to save".

## Testing strategy

- **Layer 1:** assert the authoring block (with per-type triggers) is present in
  the composed system prompt even with no pinned memory. A real-LLM E2E: a
  conversation that states a durable preference → assert a `memory_save` (or a
  resulting memory item) without an explicit "remember" ask.
- **Layer 2 gate (unit, fakeredis):** counter increments per run; fires only when
  both thresholds met; lock prevents concurrent runs; counter resets + `last`
  advances on success; unchanged on failure.
- **Layer 2 pass (unit):** given a fake history + existing items and a stubbed
  OneShotLLM returning ops, assert extract/merge/archive are applied via a fake
  `MemoryService`.
- **Real-LLM E2E (opt-in):** seed history with salient facts, force-run
  consolidation, assert personal memory items created + a duplicate merged.

## Non-goals (v1)

- Auto-writing `workspace`/`org` memory (explicit `memory_save` only).
- A forked consolidation agent with tools (OneShotLLM one-shot for v1).
- A cron/worker scheduler (post-run self-check only).
- Changing the read/relevance side (`compute_relevance_snapshot`, pinned block).

## Open questions

- Default thresholds (`minHours`, `minRuns`) — start conservative (e.g. minHours
  ≈ 6, minRuns ≈ 5) and tune; expose via config.
- History window cap for very active users (cap messages/tokens fed to the pass
  even within "since last").
