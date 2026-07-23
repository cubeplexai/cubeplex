# System Prompt Trace Optimization — Design

**Status:** Draft  
**Date:** 2026-07-22  
**Related:** #391  
**Discipline doc:** [backend/docs/prompt-cache-discipline.md](../../../backend/docs/prompt-cache-discipline.md)

## 1. Goal

Improve real agent quality and cost by **analyzing production-like traces**
(not by rewriting prompts from theory alone). Produce an engineering note
with a findings table, then land **surgical** prompt / injection fixes for
the highest-impact issues—prefer conditional or smaller fragments over
unconditional growth—while preserving prompt-cache discipline.

This issue is **analysis-first**. Implementation of prompt edits is
follow-up work driven by measured evidence.

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
C–D specified so implementers do not improvise process. Phase C may land
in the same branch **only** after the note exists and top findings are
ranked; prefer separate PRs per concern when edits are large.

### 4.2 Prerequisites for measurement

- Tracing: `tracing.enabled: true`, `tracing.record_content: true`
- CLI: `uv run cubepi trace` from `backend/` (skill:
  `.agents/skills/cubepi-trace/SKILL.md`)
- Prefer diverse scenarios: short Q&A, tool-heavy research, sandbox coding,
  artifacts, citation-heavy web search, multi-subagent, CN vs EN users,
  long multi-turn, oneshot title/reflection

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

**Must contain:**

- Sample set description (N traces, scenarios, models, env)
- **Findings table:** id, severity, evidence (trace ids), fragment(s),
  proposed change, expected metric
- Explicit **won’t fix** list
- Recommended PR split (one concern per PR)
- Baseline metrics before any edit: median/p95 input tokens, cache hit
  proxy, quality spot-checks from the checklist

### 4.6 Fix principles (Phase C)

- Edit strings in `backend/cubeplex/prompts/*.py` and middleware
  conditionals—not hand-edited traces.
- Prefer **conditional / lazy injection** when evidence shows dead weight
  (e.g. gate widget text if tool not registered or task class never needs
  it)—but keep **tool availability and prompt consistent**.
- Preserve cache discipline: stable ordering, no timestamps, deterministic
  skill sort (already present).
- No mega-diff of all prompts at once.
- Re-run cache e2e and relevant agent e2e after each fix PR.
- User-visible behavior changes → update matching `docs/site` page in the
  same PR if any.

### 4.7 Hypotheses to validate (not conclusions)

| Hypothesis | Why it might matter |
| --- | --- |
| `WIDGET_GUIDELINES` always-on and oversized | ~12KB; many turns never build widgets |
| Citation rules long but under-followed | Adherence vs length |
| Sandbox rules on pure Q&A | Conditional on sandbox/tools |
| Base “be concise” vs long playbooks | Style conflict |
| Memory authoring over/under-save | vs reflection oneshot |
| Loaded skills bloat multi-turn | Token growth |
| Subagent style guidance high-token / low-impact | Aesthetic vs success |
| Title/reflection shorter without quality loss | Oneshot × frequency |

### 4.8 Success metrics

Baselines **before** edits, from the same sample methodology:

- Median / p95 **input tokens** per turn; cache read ratio where available
- Quality proxies: citation presence on search, wrong-language rate,
  unnecessary preamble, retry/loop, early stop
- Reflection oneshot: false-positive saves / missed prefs (spot-check)
- No regression on `tests/e2e/memory/test_prompt_cache.py` and relevant
  agent e2e

## 5. Out of scope

- Redesigning agent architecture or the whole tool set in this issue.
- Breaking prompt-cache discipline.
- A permanent auto-optimizer product.
- Optimizing user messages / tool result formatting except where they
  interact with system rules (note separately if found).
- Shipping a large unprompted rewrite without the analysis note.

## 6. Success criteria

1. Analysis note exists under `docs/dev/notes/` with methodology, findings
   table, prioritized recommendations.
2. At least **3 high-severity findings** fixed in follow-up PRs **or**
   explicitly deferred with rationale.
3. Any prompt change preserves cache discipline; cache e2e stays green.
4. Conditional/lazy injection preferred when evidence supports it.
5. Diffs are surgical (per-fragment / per-concern), not a full rewrite
   without evidence.

## 7. Open questions (guidance for the note)

| Question | Guidance |
| --- | --- |
| Minimum sample size? | Target ≥10 diverse traces; more if variance is high. Dev or staging with content recording is fine; note env. |
| Prompt composition debug endpoint? | Optional; offline traces + a small `backend/scripts/dev/` size dump is enough for #391. |
| Widget/citation/sandbox → skill-triggered? | Only if traces show dead weight **and** tool pairing stays consistent. |
| Per-model-family prompts? | Only if failure modes clearly differ; default is one prompt. |
