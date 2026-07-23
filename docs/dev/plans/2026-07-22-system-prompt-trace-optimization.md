# System Prompt Trace Optimization — Implementation Plan

**Goal:** Run a measured analysis of real agent system prompts via cubepi
traces, write an engineering note with ranked findings, then ship only
high-confidence, surgical prompt fixes—without breaking prompt-cache
discipline.

**Architecture:** Four phases. **Phase A (Measure)** and **Phase B
(Diagnose)** are the core of #391. **Phase C (Fix)** and **Phase D
(Guardrails)** are follow-on, one concern per PR, driven by the note.

```
traces (content on) → size/token baselines → findings table
        → docs/dev/notes/…-system-prompt-trace-review.md
        → PR1 fix / PR2 fix / … (surgical)
        → cache e2e + spot quality checks
```

**Tech stack:** Python backend, cubepi tracing CLI, optional small script
under `backend/scripts/dev/`, pytest e2e (`test_prompt_cache.py`), markdown
notes under `docs/dev/notes/`.

**Spec:** [docs/dev/specs/2026-07-22-system-prompt-trace-optimization-design.md](../specs/2026-07-22-system-prompt-trace-optimization-design.md)  
**Issue:** #391  
**Must read before any prompt edit:**
[backend/docs/prompt-cache-discipline.md](../../../backend/docs/prompt-cache-discipline.md)  
**Trace skill:** `.agents/skills/cubepi-trace/SKILL.md`

---

## Phase A — Measure

### Unit of work A1 — Enable content traces + collect sample

**Files / ops:** local or staging config (`tracing.enabled`,
`tracing.record_content`); no product code required.

**Core logic:**

1. Confirm tracing records system prompt content on `chat` spans.
2. Run a **diverse sample** (target ≥10 runs), covering at least:
   - short Q&A (no tools)
   - web/search + citations
   - sandbox coding
   - artifact creation
   - widget / generative UI if available
   - multi-subagent
   - multi-turn with `load_skill`
   - Chinese and English user messages
   - title and reflection oneshots if observable
3. Record for each: trace id, model, scenario tag, rough outcome
   (success / loop / miss).

**Tests:** N/A (ops). Evidence = list of trace ids in the note.

### Unit of work A2 — Section size / composition baseline

**Files:** optional
`backend/scripts/dev/dump_system_prompt_sections.py` (create if helpful)

**Core logic:**

- Dump **static** fragment lengths from `cubeplex.prompts.*` (already
  approximately known: widget ~12KB largest).
- From traces (`cubepi trace view … --content` or convert), measure
  **runtime** system message length and note which middleware sections
  appear.
- Capture per-turn `input_tokens` and cache read/creation when present.

**Output:** table in the note: fragment → static bytes → typical runtime
presence.

### Unit of work A3 — Metric baselines (pre-edit)

**Core logic:** From the same sample, compute:

- median / p95 input tokens per turn (by scenario if possible)
- cache hit proxy (`cache_read / input_tokens` where available)
- qualitative tallies: citation miss, wrong language, preamble, loops

**Do not** edit prompts until these numbers are written down.

---

## Phase B — Diagnose

### Unit of work B1 — Apply analysis checklist

**Files:** none yet (working notes)

For each trace, score the eight checklist items from the spec
(adherence, dead weight, redundancy, cache, ordering, schema vs prose,
skill bloat, oneshots). Validate or discard the hypothesis table
(widget always-on, etc.).

### Unit of work B2 — Write engineering note

**Files:**

```text
docs/dev/notes/2026-07-22-system-prompt-trace-review.md
```

(Use actual write date if different; keep slug
`system-prompt-trace-review`.)

**Required sections:**

1. Sample set (N, scenarios, models, env)
2. Baselines (tokens, cache, quality spot-checks)
3. **Findings table**

   | id | severity | evidence (trace ids) | fragment(s) | proposed change | expected metric |
   | --- | --- | --- | --- | --- | --- |

4. Won’t-fix list (noise / one-offs)
5. Recommended PR split (one concern per PR)
6. Open questions remaining after analysis

**Success for #391 analysis gate:** note merged (or sitting on this branch
ready for review) with ≥3 high-severity rows **or** explicit “no high
severity found” with evidence (unlikely given widget size—still data-driven).

---

## Phase C — Fix (follow-up PRs)

Only after B2. **Do not** land a mega-diff.

### Unit of work C* — Per-finding PR template

**Files (typical):**

- `backend/cubeplex/prompts/<fragment>.py`
- Matching middleware (`middleware/sandbox.py`, `citation.py`,
  `skills.py`, …) if gating injection
- `backend/cubeplex/streams/run_manager.py` if assembly order / widget
  append changes
- Tests: cache e2e; any unit snapshot of composition if added in D

**Core logic rules:**

1. One concern per PR (e.g. “gate widget guidelines” ≠ “rewrite citations”).
2. Tool availability ↔ prompt consistency.
3. Stable sort / fixed append order for cache.
4. Re-measure a small hold-out or re-run scripted scenarios after each fix.
5. If user-visible agent behavior changes, update `docs/site` in the same
   PR.

**Example fix candidates (only if findings confirm):**

| Possible fix | Touch points |
| --- | --- |
| Conditional widget guidelines | `run_manager` append site; ensure `show_widget` tool pairing |
| Tighten citation lead-in | `prompts/citations.py` / `CitationMiddleware` |
| Trim dead sandbox lines when sandbox off | `SandboxMiddleware.transform_system_prompt` |
| Shorten oneshot title/reflection | `prompts/title.py`, `reflection_system.py` |

Each is a **hypothesis** until the note says otherwise.

**Tests (intent per fix PR):**

- `uv run pytest tests/e2e/memory/test_prompt_cache.py` (or project’s
  equivalent path) green
- Targeted agent e2e if the fragment’s behavior is covered
- Optional unit: composed prompt contains/omits section under flags

---

## Phase D — Guardrails (optional)

### Unit of work D1 — Composition snapshots / budgets

**Files:** optional unit tests under `backend/tests/unit/` asserting
section presence and rough size caps; optional log line for section
lengths (not a public API unless product wants it).

**Core logic:** Fail CI if a fragment exceeds an agreed budget without an
intentional budget bump in the same PR.

### Unit of work D2 — Doc touch

Update `prompt-cache-discipline.md` only if injection rules change in a
way operators must know. User docs only if behavior changes.

---

## File structure (expected over full issue lifecycle)

| File | Phase | Action |
| --- | --- | --- |
| `docs/dev/notes/2026-07-22-system-prompt-trace-review.md` | B | Create |
| `backend/scripts/dev/dump_system_prompt_sections.py` | A | Create optional |
| `backend/cubeplex/prompts/*.py` | C | Modify per finding |
| `backend/cubeplex/middleware/*.py` | C | Conditional injection |
| `backend/cubeplex/streams/run_manager.py` | C | Assembly only if needed |
| `backend/tests/…` | C/D | Cache + optional snapshots |

---

## Ordering and PR strategy

| PR | Content |
| --- | --- |
| **This PR (design)** | Spec + this plan only |
| **Analysis PR** (may be same branch after approval) | Note + optional size script; **no** prompt rewrites required |
| **Fix PR 1..N** | One high-severity finding each |

Acceptance from the issue: at least 3 high-severity findings **fixed or
explicitly deferred** with rationale in the note.

---

## Verification

```bash
# Phase A/B
cd backend
uv run cubepi trace ls
# … view/convert sample …

# After any Phase C edit
uv run pytest tests/e2e/memory/test_prompt_cache.py --no-cov 2>&1 | tee tmp/prompt-cache.log | tail -20
```

Paste evidence (trace ids + note path + test tail) before claiming a phase
done.

---

## Out of scope

- Agent architecture redesign
- Auto-optimizer product
- User-message formatting overhaul
- Drive-by rewrites of every prompt file

---

## Suggested commits (post-approval)

1. `docs(notes): system prompt trace review findings` (Phase B)
2. `perf(prompts): <single finding>` (each Phase C PR)
3. `test(prompts): composition size budgets` (optional Phase D)

Implementation of A–B starts after design approval; C waits on the note.
