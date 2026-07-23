# System Prompt Trace Optimization — Design

**Status:** Draft (seed analysis landed; Phase C still gated)  
**Date:** 2026-07-22 · seed note 2026-07-23  
**Related:** #391 · design PR #412  
**Discipline doc:** [backend/docs/prompt-cache-discipline.md](../../../backend/docs/prompt-cache-discipline.md)  
**Seed analysis note:** [docs/dev/notes/2026-07-22-system-prompt-trace-review.md](../notes/2026-07-22-system-prompt-trace-review.md)

## 1. Goal

Improve real agent quality and cost by **analyzing production-like traces**
(not by rewriting prompts from theory alone). Produce an engineering note
with a findings table, then land **surgical** prompt / injection fixes for
the highest-impact issues—prefer conditional or smaller fragments over
unconditional growth—while preserving prompt-cache discipline.

This issue is **analysis-first**. Implementation of prompt edits is
follow-up work driven by measured evidence.

### 1.1 Seed evidence (2026-07-23)

A **local** conversation was measured end-to-end and written up as the
first Phase A/B artifact (not a full stratified sample):

| | |
| --- | --- |
| Conversation | `conv-1m1jE95wSfyYDi` |
| Latest run (focus) | `fd9890facb3e92805a7a775621000a41` |
| Prior run | `1a078f8b74a8152fbaede423004d5a47` |
| Class | Long multi-turn sandbox + skill + PPTX artifact (HITL → build) |
| Write-up | [2026-07-22-system-prompt-trace-review.md](../notes/2026-07-22-system-prompt-trace-review.md) |

**Headline numbers (latest run only):** ~16 min wall; **37** `chat` calls;
input tokens **~92k → ~126k** per call; **~4.2M** input tokens summed over
the run; system message **~37k chars** (widget block alone **~11k**); tool
schemas **~25k chars**; tool-role history **~145k → ~184k chars** driven by
`load_skill` + full skill reference `file_read`s. Run **succeeded** (no
ERROR)—primary issue is **cost/latency context design**, not crash.

Finding ids in the note (**F1–F8**) are the authoritative list; this spec
only summarizes and links them.

## 2. Context

### Where system prompts are assembled today

| Source | Path / hook | Role |
| --- | --- | --- |
| Base | `backend/cubeplex/prompts/system.py` → `BASE_SYSTEM_PROMPT` | Core behavior, language, datetime tool, attachments |
| Skills catalog | `prompts/skills.py` via `run_manager` | Available skills list |
| Widget | `prompts/widget.py` (`WIDGET_GUIDELINES`) | UI widget authoring (~12KB static; **always appended** in `run_manager`) |
| Sandbox | `prompts/sandbox.py` via `SandboxMiddleware.transform_system_prompt` | Shell / workdir layout |
| Memory | `prompts/memory.py` via `MemoryMiddleware` | Pinned memory + authoring rules |
| Citations | `prompts/citations.py` via `CitationMiddleware` | Inline citation rules |
| Artifacts | `prompts/artifacts.py` via `ArtifactMiddleware` | `save_artifact` workflow |
| Subagents | `prompts/subagents.py` | Delegation patterns |
| Reflection | `prompts/reflection_system.py` | Detached memory curation oneshot |
| Title | `prompts/title.py` | Title generation oneshot |
| Agent overlay | `agent_cfg.system_prompt` in `run_manager` | Per-agent extra |
| Loaded skills | `SkillsMiddleware.transform_system_prompt` | Skill bodies after `load_skill` |

Rough static source sizes (bytes on disk for the module files; runtime also
adds memory/artifact lists and tool schemas):

| Fragment file | ~bytes |
| --- | --- |
| `widget.py` | ~12.0K |
| `sandbox.py` | ~4.5K |
| `subagents.py` | ~3.1K |
| `citations.py` | ~3.1K |
| `system.py` | ~2.7K |
| `title.py` | ~2.8K |
| `memory.py` | ~2.2K |
| `artifacts.py` | ~1.9K |
| `reflection_system.py` | ~1.6K |
| `skills.py` | ~0.5K |

Assembly: `run_manager` builds `effective_system_prompt` from base (+ agent
overlay + skills template + **unconditional** `WIDGET_GUIDELINES`);
middleware then appends via `transform_system_prompt`.

Cache constraints (non-negotiable): stable prefix byte-identity, no
timestamps in system prompt, deterministic ordering — see
`prompt-cache-discipline.md`. Existing gate:
`tests/e2e/memory/test_prompt_cache.py`.

### Why this work

System prompt is a large, always-on cost and a frequent root cause of
instruction misses (citations, language, verbosity). Unvalidated edits risk
cache misses and regressions. Trace-backed prioritization keeps changes
small and justified.

## 3. Approaches considered

| Approach | Pros | Cons |
| --- | --- | --- |
| **A. Measure → diagnose → fix from traces** (recommended) | Evidence-based; surgical PRs; respects cache discipline | Needs diverse sample + tracing with content |
| **B. Immediate rewrite of largest fragments (e.g. widget)** | Fast token win possible | May break widget quality; may violate “always-on tool ↔ prompt” pairing without analysis |
| **C. Auto-optimizer / permanent prompt product** | Ongoing | Out of scope; high risk; not this issue |

**Recommendation: A**, matching the issue. Hypotheses (widget always-on,
citation under-follow, sandbox on pure Q&A, etc.) are **starting hunches
only** until traces confirm.

## 4. Design

### 4.1 Phased workflow

| Phase | Name | Output |
| --- | --- | --- |
| **A** | **Measure** | Sample set of traces + section size baselines + token/cache baselines |
| **B** | **Diagnose** | Engineering note with findings table + won’t-fix + PR split |
| **C** | **Fix** | One concern per follow-up PR; only high-confidence items |
| **D** | **Guardrails** | Optional snapshots / size budgets; cache e2e green |

This design + plan cover **A–B as the primary deliverable of #391**, with
C–D specified so implementers do not improvise process.

**Hard gate for Phase C (resolved):** no prompt-code PR (and no prompt
edits on the analysis branch) until the findings note is **merged or
explicitly design-approved** (human review). Each fix PR must reference a
specific finding `id` from that note and include: evidence (trace ids),
expected metric, and post-change measurement notes. “Note file exists on
the branch” is **not** sufficient to start rewrites.

### 4.2 Prerequisites for measurement

- Tracing: `tracing.enabled: true`, `tracing.record_content: true`
- CLI: `uv run cubepi trace` from `backend/` (skill:
  `.agents/skills/cubepi-trace/SKILL.md`)
- Prefer diverse scenarios: short Q&A, tool-heavy research, sandbox coding,
  artifacts, citation-heavy web search, multi-subagent, CN vs EN users,
  long multi-turn, oneshot title/reflection

**Privacy / data boundary (required):**

- Prefer **synthetic local** or **explicitly sanitized staging** traffic.
- Do **not** enable `record_content` against production by default for this
  workstream.
- Do not commit raw traces, user prompts, tool args/results, or secrets into
  the repo. The engineering note may cite **trace ids** and redacted
  snippets only.
- After analysis, delete or retain local JSONL per team retention norms;
  document env used in the note (`local` / `staging`).

### 4.3 How to inspect

```bash
cd backend
uv run cubepi trace ls
uv run cubepi trace view <trace_id_prefix> --content
uv run cubepi trace convert <trace_id> --span 0x…
uv run cubepi trace stats --by model
```

Focus on `chat <model>` spans under `cubepi.turn`:

- System content in `gen_ai.input.messages`
- `gen_ai.usage.input_tokens`, cache read/creation
- Tool patterns vs instructions; errors; loops

Admin Trace UI (`/admin/traces`) may complement local JSONL.

### 4.4 Analysis checklist (“worth optimizing”)

For each sampled run, score:

1. **Instruction adherence gaps** — model violates explicit rules
   (citations, language match, no preamble, `ask_user`, datetime tool,
   artifact versioning, sandbox paths).
2. **Dead weight** — large irrelevant sections (e.g. full widget guidelines
   on pure research).
3. **Redundancy / conflict** — same rule twice; concision vs long playbooks.
4. **Cache / cost structure** — unstable prefix; sections that change every
   turn; cache hit rate.
5. **Ordering / primacy** — critical rules buried under capability docs.
6. **Tool schema vs prose duplication** — waste or missing structural
   enforcement.
7. **Subagent / skill load cost** — skill body growth after load.
8. **Oneshot agents** — reflection/title precision vs recall.

### 4.5 Required analysis artifact

Path (repo convention):

```text
docs/dev/notes/2026-07-22-system-prompt-trace-review.md
```

(If the note is written on a later day, use that day’s date in the
filename; keep the slug `system-prompt-trace-review`.)

**Seed revision (2026-07-23):** the file above **exists** and holds the
measured baselines + findings table for `conv-1m1jE95wSfyYDi` /
`fd9890…`. Treat it as **Phase A seed + Phase B draft**. Expand the same
file (or a dated successor that links back) when the stratified sample
grows; do not start a second unlinked note.

**Must contain:**

- Sample set description (N traces, scenarios, models, env)
- **Findings table:** id, severity, evidence (trace ids), fragment(s),
  proposed change, expected metric
- Explicit **won’t fix** list
- Recommended PR split (one concern per PR)
- Baseline metrics before any edit: median/p95 input tokens, cache hit
  proxy, quality spot-checks from the checklist

**Seed findings (summary — full table in the note):**

| id | severity | One-line |
| --- | --- | --- |
| F1 | high | Always-on **widget** system prose (~11k) on a non-widget PPTX run |
| F2 | high | **Skill body + full reference files** stuck in tool history for 37 turns |
| F3 | high | **Skills catalog** descriptions too long; long org-prefixed names |
| F4 | medium | **Memory** system block large and off-task for this run |
| F5 | medium | **Tool schemas** ~25k always-on |
| F6 | medium | **Cache cliffs** (0% cache_read) on some turns |
| F7 | low–med | Agent loop inefficiency (many write/execute turns) partly skill-side |
| F8 | info | Run succeeded — optimize cost path, not “fix crash” |

### 4.6 Fix principles (Phase C)

- Edit strings in `backend/cubeplex/prompts/*.py` and middleware
  conditionals—not hand-edited traces.
- Prefer **conditional / lazy injection** when evidence shows dead weight
  (e.g. gate widget text if the **tool is not registered for that agent
  config**)—but keep **tool availability and prompt consistent**.
- **Cache-safe gating (non-negotiable):** capability sections that sit in
  the stable system prefix must be **fixed for the lifetime of a
  conversation** and decided **before the first model call** of that
  conversation (or when agent/tool config is fixed). **Forbidden:**
  per-turn / per-user-message task classification that adds or removes
  system-prefix fragments mid-conversation (busts byte-identical prefix —
  see `prompt-cache-discipline.md`). Turn-varying guidance belongs after
  the cache breakpoint or in a new conversation.
- Preserve cache discipline: stable ordering, no timestamps, deterministic
  skill sort (already present). After any injection change, require
  byte-level prompt comparison across ≥2 turns of the same conversation
  (or the existing cache e2e) to prove prefix stability.
- No mega-diff of all prompts at once.
- Re-run cache e2e and relevant agent e2e after each fix PR.
- User-visible behavior changes → update matching `docs/site` page in the
  same PR if any.

### 4.7 Hypotheses to validate (not conclusions)

| Hypothesis | Why it might matter | Seed sample (`fd9890…`) |
| --- | --- | --- |
| `WIDGET_GUIDELINES` always-on and oversized | ~12KB; many turns never build widgets | **Supported** — see note **F1** |
| Citation rules long but under-followed | Adherence vs length | **Not exercised** |
| Sandbox rules on pure Q&A | Conditional on sandbox/tools | **Not exercised** (sandbox needed) |
| Base “be concise” vs long playbooks | Style conflict | Possible via skill; not primary |
| Memory authoring over/under-save | vs reflection oneshot | **Partial** — large off-task block **F4** |
| Loaded skills bloat multi-turn | Token growth | **Supported** — **F2**, **F3** |
| Subagent style guidance high-token / low-impact | Aesthetic vs success | **Not exercised** |
| Title/reflection shorter without quality loss | Oneshot × frequency | **Not exercised** |

Update the note’s hypothesis table when new scenario classes are measured.

### 4.8 Success metrics

Baselines **before** edits, from the same sample methodology. Report
fields **separately** (do not invent a single ratio without defining
denominators against provider/cubepi fields):

| Metric | How to record |
| --- | --- |
| Input / prompt tokens | `gen_ai.usage.input_tokens` (or provider equivalent) per turn |
| Cache read tokens | provider `cache_read` / `cache_read_input_tokens` when present |
| Cache creation tokens | provider `cache_creation` / write tokens when present |
| Uncached input | derived only with an explicit formula that matches billing semantics used in `test_prompt_cache` / provider docs — never assume `cache_read / input_tokens` is a “hit rate” without checking whether `input_tokens` is total or uncached-only |
| Quality proxies | citation presence on search, wrong-language, preamble, retry/loop, early stop — tallied with a **fixed rubric** |
| Reflection oneshot | false-positive saves / missed prefs (spot-check) |
| Guardrail | `tests/e2e/memory/test_prompt_cache.py` + relevant agent e2e green |

Document the exact field names observed in cubepi spans in the note so
formulas are reproducible.

## 5. Out of scope

- Redesigning agent architecture or the whole tool set in this issue.
- Breaking prompt-cache discipline.
- A permanent auto-optimizer product.
- Optimizing user messages / tool result formatting except where they
  interact with system rules (note separately if found).
- Shipping a large unprompted rewrite without the analysis note.

## 6. Success criteria

1. Analysis note exists under `docs/dev/notes/` with methodology, findings
   table, prioritized recommendations — and is **reviewed/approved** before
   Phase C. **Seed note present** ([link](../notes/2026-07-22-system-prompt-trace-review.md));
   approval + stratified expansion still required.
2. Every finding that meets predeclared impact/confidence thresholds is
   either fixed in a follow-up PR or **explicitly deferred** with rationale.
   There is **no quota** to invent N high-severity rows; zero or one real
   high-severity finding is acceptable if the evidence says so.
3. Any prompt change preserves cache discipline; cache e2e stays green;
   mid-conversation system-prefix gating is not introduced.
4. Conditional/lazy injection preferred when evidence supports it **and**
   gating is conversation-stable (or config-stable).
5. Diffs are surgical (per-fragment / per-concern), not a full rewrite
   without evidence.

## 7. Open questions (guidance for the note)

| Question | Guidance |
| --- | --- |
| Minimum sample size? | Stratified sample: cover the scenario classes in §4.2 with **≥2 traces per primary class** when claiming a class-level finding (not one anecdote per class). Overall target often ≥10–20 runs; state N and uncertainty in the note. Keep a small **holdout** for post-fix comparison. |
| Prompt composition debug endpoint? | Optional; offline traces + a small `backend/scripts/dev/` size dump is enough for #391. |
| Widget/citation/sandbox → skill-triggered? | Only if traces show dead weight **and** tool pairing stays consistent **and** injection is conversation-stable. |
| Per-model-family prompts? | Only if failure modes clearly differ; default is one prompt. |
| Production `record_content`? | Default no; synthetic/staging + redaction only. |
