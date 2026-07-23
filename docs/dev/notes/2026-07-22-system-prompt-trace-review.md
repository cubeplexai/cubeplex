# System prompt trace review — seed sample (local)

**Date:** 2026-07-23  
**Related:** #391 · design PR #412  
**Spec:** [docs/dev/specs/2026-07-22-system-prompt-trace-optimization-design.md](../specs/2026-07-22-system-prompt-trace-optimization-design.md)  
**Plan:** [docs/dev/plans/2026-07-22-system-prompt-trace-optimization.md](../plans/2026-07-22-system-prompt-trace-optimization.md)  
**Discipline:** [backend/docs/prompt-cache-discipline.md](../../../backend/docs/prompt-cache-discipline.md)  
**Trace skill:** `.agents/skills/cubepi-trace/SKILL.md`

**Status:** Phase A seed measurement + Phase B **partial** findings from one
real local conversation. **Not** a full stratified sample. Phase C (prompt
edits) still requires human design approval of this note and broader sample
coverage where claimed.

**Environment:** local main worktree (`backend/cubepi-traces/`), developer
machine, `record_content` on. **Do not treat as production telemetry.**
Raw JSONL stays on disk only; this note cites **trace ids** and redacted
sizes only—no user secrets, full skill bodies, or full memory text.

---

## 1. Sample set

| Field | Value |
| --- | --- |
| Conversation | `conv-1m1jE95wSfyYDi` |
| Focus run (latest) | `fd9890facb3e92805a7a775621000a41` |
| Prior run (same conv) | `1a078f8b74a8152fbaede423004d5a47` |
| Date shard | `cubepi-traces/2026-07-23/` |
| Primary model (latest) | `deepseek-v4-flash` |
| Prior model | `glm-5.2` (skill load + HITL) |
| Scenario class | Long multi-turn **sandbox coding + artifact**: PPTX investor deck after `ask_user` (format=pptx, theme=auto, content=refresh) |
| Outcome rubric | **success** (status ok, no ERROR spans; deck built + `save_artifact`) but **high cost / latency** |

### How to re-open

```bash
cd backend
uv run cubepi trace ls --meta conversation_id=conv-1m1jE95wSfyYDi --dir ./cubepi-traces
uv run cubepi trace view fd9890fa --dir ./cubepi-traces
uv run cubepi trace convert fd9890fa --dir ./cubepi-traces --span 0xa248bc9e  # first chat
uv run cubepi trace convert fd9890fa --dir ./cubepi-traces --span 0x2df20720  # last chat
uv run cubepi trace stats --dir ./cubepi-traces --meta conversation_id=conv-1m1jE95wSfyYDi
```

First chat span id (tree): `0xa248bc9e`. Last: `0x2df20720`.

---

## 2. Baselines (measured)

### 2.1 Run shape (latest: `fd9890…`)

| Metric | Value |
| --- | --- |
| Wall time | ~978 782 ms (~16.3 min) |
| `cubepi.turn` count | 38 |
| `chat deepseek-v4-flash` | 37 |
| Tools | `execute`×24, `write_file`×16, `edit_file`×4, `write_todos`×6, `ask_user`×1, `save_artifact`×1 |
| Errors | **0** |

### 2.2 Token usage (trace field convention)

Cubepi trace: `gen_ai.usage.input_tokens` is **inclusive total prompt**
(cache_read is a **subset**). Approximate hit fraction for a call:

```text
cache_read / input_tokens   # only when both present; not a billing formula
```

| Scope | Input tokens (sum) | Output | Cache read (sum) |
| --- | --- | --- | --- |
| Latest run only (37 chats) | **4 208 037** | **20 966** | **3 194 880** |
| Conversation (both models, stats CLI) | deepseek 4.2M in / 21k out; glm 422k in / 3.6k out | | deepseek cache_tok ~3.2M; glm ~330k |

Per-call **input_tokens** on latest run:

| Point | input_tokens | cache_read | cache_read/input (if defined) |
| --- | --- | --- | --- |
| First chat | 92 182 | 65 536 | ~71% |
| Mid | ~116 457 | 114 688 | ~98% |
| Last chat | **126 210** | 114 688 | ~91% |

**Cache cliffs:** multiple turns at **0%** cache_read (observed near ~96k,
~111k, ~122k, ~124k, ~125k input). Overall run still cache-heavy, but
cliffs re-bill large prefixes.

### 2.3 First request composition (converted OpenAI-shaped body)

Source: `cubepi trace convert fd9890fa --span 0xa248bc9e`.

| Bucket | Chars (approx) | Notes |
| --- | --- | --- |
| **system** | **37 449** | Single system message |
| **tools** (schemas) | **~25 623** | 21 tools always registered |
| **tool** role (history) | **~145 196** | Dominated by skill load + file_read of skill refs |
| **assistant** history | ~1.2k (first) → ~12k (last) | Grows with loop |
| **user** | small | HITL resume + brief |
| **Total request JSON** | ~312k chars first chat | |

**Last request** (`0x2df20720`): 119 messages; tool-role history **~184k**
chars; system still ~37.5k; same ~21 tools.

### 2.4 System message section sizes (first chat)

Split on `## ` headers (and top-level `#` blocks). Approximate character
counts of the **assembled** system string:

| Section / block | ~chars | Observation |
| --- | --- | --- |
| `## Rendering interactive widgets (show_widget)` | **~11 030** | Always present; task is **PPTX**, not widget |
| `## Memory` (+ pinned items) | **~7 139** | Large personal corrections (ops / social); low relevance to deck |
| `# Available skills` catalog | **~6 435** | Long skill descriptions (incl. namespaced uploaded skill) |
| `## File Attachments` + persona tail | **~7 131** | Attachment rules + agent persona overlay |
| `## Artifacts` | ~1 827 | Relevant near end of run |
| `## Shell Execution` + workspace org | ~1.5–1.6k each | Useful for sandbox task |
| `## Saving memory` / todos notes | ~1–1.7k | Always-on |
| Base core / doing tasks / etc. | smaller | |

Persona overlay observed (redacted intent only): named assistant + “Always
talk to me with English.”

### 2.5 History bloat drivers (tool results, not system)

Largest tool payloads retained in the transcript (first chat already):

| Kind | ~chars | Example |
| --- | --- | --- |
| `load_skill` body | ~33k | `gxf-beta-s-org:huashu-design` full SKILL.md |
| `file_read` skill reference | ~42k | `…/references/slide-decks.md` (full file, not truncated) |
| Other skill refs | 10–17k each | `editable-pptx.md`, `design-styles.md`, … |
| App source read | ~17k | e.g. frontend `globals.css` pulled into tool result |

These remain as **tool** messages for **all subsequent turns** of the run
(no compaction observed).

### 2.6 Agent loop pattern

- One turn produced **out≈7869** tokens then **11× `write_file`** in the
  same turn (~223 s wall for that turn).
- Many small `execute` cycles (fonts, sharp, build) after large skill-driven
  planning.
- Ends with `save_artifact` + short final reply.

---

## 3. Findings table

Severity is for **this sample class** (long skill + sandbox artifact). Do
not generalize to all product surfaces without more stratified runs.

| id | severity | evidence | fragment(s) / layer | proposed change (direction only) | expected metric |
| --- | --- | --- | --- | --- | --- |
| **F1** | **high** | `fd9890…`: system always includes ~11k widget block while tools/path are PPTX + sandbox | `WIDGET_GUIDELINES` / `run_manager` always-on append | Conversation-stable gate: inject widget prose only when `show_widget` is in the tool set **for that agent config**, or move bulk to on-demand skill; **never** per-turn task classify mid-conversation | Lower median system chars / first-turn input on non-widget runs; cache e2e still green |
| **F2** | **high** | First chat tool-role ~145k; skill body + multi-file references stay for 37 turns; input 92k→126k | `load_skill` + `file_read` + history replay; skills middleware | Cap or summarize skill bodies in history; truncate large reference reads; optional “skill unload”; long-run **compaction** | p95 input tokens on multi-turn skill runs; same task success rate |
| **F3** | **high** | Skills list ~6.4k with long uploaded skill blurb; name `gxf-beta-s-org:huashu-design` | `SKILLS_PROMPT_TEMPLATE` / catalog formatting | Short catalog lines (name + one-line trigger); full text only after `load_skill`; UI/display: #399 | Catalog chars; load_skill still discoverable |
| **F4** | **medium** | Memory block ~7k, mostly off-task personal corrections | `MemoryMiddleware` + pinned memory render | Tighter ranking / size budget for system memory block; keep authoring rules short | System memory section chars; no regression on in-scope corrections |
| **F5** | **medium** | 21 tool schemas ~25k always (write_todos, file_read, schedule, …) | Tool registration / deferred groups | Expand deferred tool groups; shorten schema descriptions | tools JSON chars; task success |
| **F6** | **medium** | Several 0% cache_read turns despite high average cache | Stable prefix + history growth | Keep system/tool prefix stable; avoid mid-run skill **re**-injection into system; measure cliffs after F1–F2 | Count of 0% cache turns / run |
| **F7** | **low–medium** | 37 turns, 16 write_file, 24 execute for one deck | Skill guidance + agent behavior (not only base system) | Skill-side: fewer full-file dumps; prefer edit over rewrite; optional model/effort policy | Turns per successful artifact; wall time |
| **F8** | **info** | Run **succeeded** with no ERROR | — | Do not “fix success”; optimize cost path | — |

### Hypotheses from the design doc — status on this sample

| Hypothesis (spec §4.7) | This sample |
| --- | --- |
| Widget always-on and oversized | **Confirmed** for this non-widget run (F1) |
| Loaded skills bloat multi-turn | **Confirmed** strongly (F2) |
| Memory authoring / block size | **Partially confirmed** as dead weight for this task (F4) |
| Citation under-follow | **Not exercised** (no web_search path) |
| Sandbox on pure Q&A | **Not exercised** (sandbox was needed) |
| Subagent style guidance | **Not exercised** |

---

## 4. Won’t fix (from this sample alone)

| Item | Why |
| --- | --- |
| Rewrite base “be concise” without more samples | Success path; style conflict not proven as failure mode here |
| Delete sandbox workspace-org rules | Task used sandbox; dead-weight claim invalid here |
| Change model / provider | Out of scope for prompt optimization |
| Blame deepseek alone for 37 turns | Skill + history design dominate measurable tokens |
| Commit raw JSONL or full memory/skill text | Privacy / repo hygiene |

---

## 5. Recommended PR split (after note approval + broader sample)

One concern per PR; each cites a finding id and re-measures.

| Order | PR concern | Findings | Notes |
| --- | --- | --- | --- |
| 1 | Gate or slim **widget** system prose (conversation-stable) | F1 | Pair with `show_widget` tool presence |
| 2 | **Skill catalog** line budget + short descriptions | F3 | Coordinates with #399 display only |
| 3 | **History** policy for large tool results / skill refs | F2 | May touch cubepi/middleware more than `prompts/*.py` |
| 4 | Memory **system block** size budget / ranking | F4 | Preserve corrections quality |
| 5 | Deferred tools / schema trim | F5 | Optional after 1–3 |

**Do not** combine 1–5 into one mega-diff.

---

## 6. Open questions

1. Is widget gating allowed when `show_widget` remains registered but unused
   (tool always on in default agent)? If yes, need another conversation-stable
   signal (agent profile / capability flag), not per-user-message NLP.
2. Should large `file_read` of skill `references/*` auto-truncate with
   “open path in sandbox” guidance?
3. Compaction: product feature vs prompt-only? Spec currently notes tool
   result formatting as out of scope unless it interacts with system rules—
   F2 may force an explicit scope expansion in a follow-up design.
4. Expand sample: short Q&A, citation search, pure memory chat, multi-subagent
   (still required before org-wide claims).

---

## 7. Analysis checklist scores (latest run only)

| Checklist item (spec §4.4) | Score (this run) |
| --- | --- |
| 1 Instruction adherence | Not primary failure; language/persona followed |
| 2 Dead weight | **High** — widget + off-task memory + long catalog |
| 3 Redundancy / conflict | Medium — long skill vs base concise |
| 4 Cache / cost | **High** volume; cliffs present |
| 5 Ordering / primacy | Widget/memory before task-specific needs |
| 6 Tool schema vs prose | Medium — large schemas + long prose for same tools |
| 7 Subagent / skill load | **High** — skill + refs dominate history |
| 8 Oneshots | Not the focus of this run |

---

## 8. Gate for Phase C

Per design PR #412:

- [x] Seed note written with methodology, baselines, findings, won’t-fix, PR split  
- [ ] Stratified sample (≥2 per primary class) for class-level claims  
- [ ] Human design approval of this note (or successor revision)  
- [ ] Holdout set reserved for post-fix comparison  

Until the second and third boxes are checked, treat **F1–F3 as high-priority
hypotheses with strong local evidence**, not as a blank check to rewrite all
prompts on main.
