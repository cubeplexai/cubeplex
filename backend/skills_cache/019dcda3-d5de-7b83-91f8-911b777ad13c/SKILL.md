---
name: deep-research
description: Use when a question needs current multi-source research, multi-angle investigation, or iterative verification before producing a report or detailed answer
version: 3.2.0
keywords:
  - research
  - multi-agent
  - subagent-orchestration
  - deep-investigation
  - report-generation
  - verification
---

# Deep Research

## Overview

Use a **supervisor-subagent loop** for serious research.
The main agent first grounds the request, then owns planning, todo state, review, and final synthesis.
Subagents do not manage the plan. They only execute narrowly scoped research tasks and return facts.

**Core principle:** never write the final answer from general knowledge when the task requires real research. Research coverage determines answer quality.

## Architecture

```text
User Query
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ MAIN AGENT (Supervisor)                                     │
│ • Do lightweight reconnaissance first                       │
│ • Clarify the request if needed                             │
│ • Classify the task                                         │
│ • Decompose into verification points                        │
│ • Create/update workflow-stage todos via `write_todos`      │
└──────────────────────────────────────────────────────────────┘
    │
    │ one active todo = one active research phase
    ▼
┌──────────────────────────────────────────────────────────────┐
│ ACTIVE TODO / RESEARCH PHASE                                │
│ Example: "Run current research round across required angles"│
└──────────────────────────────────────────────────────────────┘
    │
    │ supervisor chooses the angle decomposition for this phase
    │ and fan-outs parallel `subagent` calls inside this one todo
    ▼
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ Subagent A  │  │ Subagent B  │  │ Subagent C  │
│ facts       │  │ facts       │  │ facts       │
│ gaps        │  │ gaps        │  │ conflicts   │
└─────────────┘  └─────────────┘  └─────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│ MAIN AGENT REVIEW                                            │
│ • Are findings specific and source-backed?                   │
│ • Are there gaps or conflicts?                               │
│ • Is another round needed?                                   │
└──────────────────────────────────────────────────────────────┘
    │                              │
    │ more research needed         │ research sufficient
    ▼                              ▼
update current todo / add new      mark research todos complete
todos / dispatch more subagents    move report todo to in_progress
    │                              │
    └─────────────── loop ─────────┘
                   ▼
            Final synthesis/report
```

## Core Model

Treat the workflow as two separate layers:

- **Todo layer**: the main agent's deep-research workflow stages
- **Subagent layer**: angle decomposition and parallel execution inside the active stage

The key rule is:

> A single `in_progress` todo represents one active **research phase**, not one research angle and not one subagent. That phase may dispatch multiple subagents in parallel.

Do **not** create one todo per subagent or one todo per angle if that would leave multiple simultaneous active todos or force the model into artificial serial execution.

## Main Agent Responsibilities

The main agent must:

1. Run a lightweight grounding step before planning.
2. Clarify the user request when scope, time frame, or success criteria are ambiguous.
3. Classify the research task.
4. Decompose the topic into atomic verification points.
5. Use `write_todos` to create and maintain the workflow-stage plan.
6. Dispatch parallel `subagent` calls for the current phase.
7. Review subagent results for gaps, conflicts, and follow-ups.
8. Update the todo list after each review round.
9. Generate the final report only after research todos are complete.

## Subagent Responsibilities

Subagents must:

- work on one narrow, self-contained question
- search, extract, and return concrete facts
- report gaps, conflicts, and missing data explicitly
- avoid writing the final report
- avoid planning or updating todos

Subagents are **fact extractors**, not project managers.

## When to Use

Use this skill when:

- the user asks to research, investigate, verify, compare, or explain something in depth
- the answer needs current information from multiple sources
- one search is unlikely to be enough
- the task requires follow-up rounds after initial findings
- the output needs evidence-backed analysis or a report

Do not use this full workflow for:

- a single quick fact with one clear authoritative source
- purely conversational questions
- tasks that do not require external evidence

## Phase 0: Ground the Request Before Planning

**This phase is mandatory. Do NOT skip it. Do NOT call `write_todos` or `subagent` until Phase 0 is complete.**

Before writing todos or dispatching subagents, do a **lightweight reconnaissance pass** to understand what the user is actually asking.

### Step 0a: Temporal Grounding (check FIRST)

Scan the user's request for **any** time reference — explicit or implied:

- Explicit: dates, years, quarters, "2024", "Q1", "last month", "recent"
- Implicit: "current status", "latest", "now", "market share" (implies recency), "trend", "forecast", news events, pricing, rankings, competitive landscape

**If the request contains ANY time signal (explicit or implicit):**

1. Call the `datetime` tool immediately — before any other action.
2. Use the result to anchor all subsequent planning: determine the correct year, quarter, and date range.
3. Include the anchored time context in every subagent prompt you later dispatch (e.g., "Today is 2026-04-14. Research data from 2025-2026.").

**If the request is purely conceptual** (e.g., "explain how transformers work", "compare REST vs GraphQL architectures") — no temporal grounding is needed.

When in doubt, call `datetime`. The cost is one lightweight tool call; the cost of getting the time wrong propagates to every subagent and corrupts the entire research.

### Step 0b: Topic Reconnaissance

Use the smallest useful tool action to understand the research landscape:

- a simple web search to identify the topic, likely source landscape, or whether the question is temporal
- a quick direct tool lookup when the request names a specific entity, event, metric, or date range

The goal of this pass is not to complete the research. The goal is to reduce ambiguity before planning.

### Step 0c: Scope Assessment

During this pass, determine:

- what the actual research object is
- whether the request is time-sensitive (if you haven't called `datetime` yet, reconsider)
- whether the user is asking for explanation, verification, comparison, or report generation
- what obvious ambiguities or missing constraints exist

### Clarification Rule

If the user's request is still ambiguous after lightweight reconnaissance, ask a clarifying question before creating todos or spawning subagents.

Examples:

- unclear scope
- unclear time range
- unclear comparison target
- unclear output format
- unclear standard for success

Do **not** jump straight from the raw user message into `write_todos` if you do not yet understand the request well enough to plan intelligently.

## Phase 1: Classify and Decompose

Before researching, classify the request:

| Type | Characteristics | Approach |
|------|----------------|----------|
| **Quick Fact** | One specific answer | Search directly, skip heavy orchestration |
| **Verification** | A claim needs confirmation or challenge | Search for evidence and counter-evidence |
| **Comprehensive** | Multi-dimensional topic | Full supervisor + todo + subagent loop |
| **Temporal** | News, prices, events, recent changes | Prefer official and up-to-date sources |

Then break the topic into **atomic verification points**.
Each point should represent one dimension that can be researched independently.

Good decomposition:

```text
Topic: Tesla vs BYD competitive position
- Market share, 2024-2026
- Battery technology differences
- Financial performance
- Production capacity
- Regulatory environment
```

Bad decomposition:

- "Research Tesla and BYD"
- "Compare everything"

## Phase 2: Create the Todo Plan

Before dispatching substantial research, the main agent should call `write_todos`.

Use todos to track **workflow stages** — they describe what phase of the research process you are in, not what topics you are researching. The angle decomposition happens inside the active todo when you dispatch subagents; it does not appear in the todo list itself.

**The litmus test:** if a todo item reads like a research question or topic heading, it belongs in a subagent prompt, not in the todo list. Todos should answer "what step of the process am I on?" not "what do I need to find out?"

Good todo list:

```json
{
  "todos": [
    {"content": "Ground the request: clarify scope, time range, and success criteria", "status": "completed"},
    {"content": "Round 1: broad research across all identified angles", "status": "in_progress"},
    {"content": "Review round 1, resolve conflicts and fill critical gaps", "status": "pending"},
    {"content": "Write and save final report as artifact", "status": "pending"}
  ]
}
```

Bad todo list (DO NOT do this — these are research topics disguised as todos):

```json
{
  "todos": [
    {"content": "Research Tesla market share data", "status": "in_progress"},
    {"content": "Research BYD market share data", "status": "in_progress"},
    {"content": "Compare battery technology", "status": "pending"},
    {"content": "Analyze financial performance", "status": "pending"},
    {"content": "Check regulatory environment", "status": "pending"}
  ]
}
```

Why this is wrong:
- Each item is a research angle, not a workflow stage
- Multiple items would need to be `in_progress` simultaneously
- It forces serial execution of things that should run in parallel as subagents
- The todo list becomes a table of contents for the report, not a process tracker

### Todo Rules

- The main agent owns all `write_todos` calls.
- Unless everything is done, there must be exactly one `in_progress` todo.
- A todo may be revised, split, expanded, or replaced after review.
- If new gaps appear, add or rewrite future todos.
- The todo list should describe the deep-research flow, not the full internal decomposition of angles.
- The supervisor should decide angle decomposition separately from todo writing.
- If the current phase narrows after one round, keep it `in_progress` and rewrite it more specifically.
- Final report generation should usually be its own todo.

## Phase 3: Fan Out Subagents Inside the Active Todo

Once one research phase is `in_progress`, the main agent may dispatch multiple subagents inside that phase.

**Time context in subagent prompts:** If Phase 0 established a temporal anchor, you MUST include it at the start of every subagent prompt. Subagents have no conversation context — if you don't tell them the date, they will guess from training data and return stale results. Format: `"Today is YYYY-MM-DD. [rest of prompt]"`

### Subagent Prompt Template

Every subagent prompt must include **all five sections** below. A vague one-liner prompt produces vague output. The prompt is the only contract between the supervisor and the subagent — everything you need from the subagent must be spelled out in it.

```
1. CONTEXT    — date anchor, background the subagent needs, why this matters
2. TASK       — the specific, narrow research question (one question per subagent)
3. METHOD     — what to search for, what sources to prefer, what to avoid
4. OUTPUT     — required structure for the response (see below)
5. BOUNDARIES — what NOT to do (no report writing, no speculation, no planning)
```

### Required Output Structure

Instruct every subagent to return findings in this exact structure:

```
## Facts
- [Subject] [Time] [Metric]: [Value] (Source: [name/url])
- ...

## Gaps
- What could not be confirmed and why

## Conflicts
- Where sources disagree and what the disagreement is

## Limitations
- Source quality issues, paywalled data, methodology concerns
```

Every fact must include a **source name or URL**. Subagent results carry citation metadata — the source information you include in the prompt output requirements is what enables citations in the final report.

### Full Example

```python
subagent(
    name="Dr. Chen",
    role="Auto Market Analyst",
    task="Find global EV market share data for 2024-2026",
    prompt="""Today is 2026-04-14.

CONTEXT:
We are researching the competitive position of Tesla vs BYD in the global EV market.
Your focus is global market share data. Other subagents are covering China-specific data
and battery technology separately — do not duplicate their scope.

TASK:
Find global BEV (battery electric vehicle) market share figures for Tesla and BYD
for calendar years 2024 and 2025, plus any available 2026 partial-year data.

METHOD:
- Search for industry reports from CleanTechnica, EV-Volumes, Counterpoint, SNE Research
- Prefer quarterly or annual share percentages over unit sales alone
- Cross-reference at least two independent sources per data point
- If a figure appears in only one source, flag it in Limitations

OUTPUT:
Return your findings in this structure:

## Facts
- [Company] [Period] [Metric]: [Value] (Source: [name/url])
  Example: Tesla Q3 2025 global BEV share: 14.2% (Source: Counterpoint Research)

## Gaps
- List what you could not find (e.g., "2026 Q1 data not yet published by any source")

## Conflicts
- Where two sources give different numbers for the same metric

## Limitations
- Source quality or methodology issues

BOUNDARIES:
- Do NOT write a summary, analysis, or report
- Do NOT speculate on reasons behind the numbers
- Do NOT plan further research steps
- Say "not found" explicitly when evidence is missing — do not fill gaps with general knowledge
"""
)
subagent(
    name="Atlas",
    role="China Market Researcher",
    task="Find China-specific EV market share for Tesla and BYD",
    prompt="""Today is 2026-04-14.

CONTEXT:
We are comparing Tesla vs BYD competitive position. Your scope is China domestic market only.
Another subagent covers global data — focus exclusively on China registrations and market share.

TASK:
Find China domestic BEV/PHEV market share and unit sales for Tesla and BYD for 2024-2025,
plus any 2026 partial data. Include both BEV-only and total NEV (BEV+PHEV) if available,
since BYD's PHEV share significantly affects the comparison.

METHOD:
- Search CPCA (China Passenger Car Association) monthly reports
- Check CAAM (China Association of Automobile Manufacturers) data
- Cross-reference with local media (CnEVPost, Gasgoo, 36kr)
- Distinguish between wholesale and retail figures

OUTPUT:
## Facts
- [Company] [Period] [Market: China] [Metric]: [Value] (Source: [name/url])

## Gaps
- What could not be confirmed

## Conflicts
- Where sources disagree (e.g., wholesale vs retail discrepancies)

## Limitations
- Methodology notes (BEV-only vs NEV, wholesale vs retail)

BOUNDARIES:
- Do NOT write analysis or draw conclusions
- Do NOT cover markets outside China
- Say "not found" when evidence is missing
"""
)
```

All subagents dispatched in one round belong to the same active todo (e.g., `Round 1: broad research across all identified angles`). This is the core coordination rule:

> One active todo can contain many parallel subagent runs. The todo tracks the research phase. The subagents are the execution units inside it.

## Phase 4: Subagent Output Review and Citation Integrity

When subagent results come back, the main agent must check both **content quality** and **citation integrity** before proceeding.

### Content quality check

For each subagent result, verify:

1. **Specificity**: are there concrete facts with numbers/dates, or vague summaries?
2. **Source attribution**: does every fact include a source name or URL?
3. **Coverage**: did the subagent answer the assigned question?
4. **Gaps**: what remains unanswered?
5. **Conflicts**: do results disagree across subagents?

### Citation integrity

Subagent results carry citation metadata (URLs, titles) from their search and browsing tools. When the main agent synthesizes the final report:

- **Preserve source URLs** from subagent findings — do not strip or summarize away the source references
- **Cite inline** in the report body: link facts to their sources so the reader can verify
- **Do not fabricate sources** — if a subagent reported a fact without a source, mark it as unverified rather than inventing a citation

Bad:

- "Tesla performed well." (no specifics, no source)
- "According to industry reports, BYD leads in China." (which reports?)

Good:

- "Tesla global BEV share in 2024 was 17.1% (Source: Counterpoint Research Q4 2024 report)."

## Phase 5: Supervisor Review Loop

After subagents return, the main agent must review before continuing.

Review each result for:

1. **Specificity**: are there concrete facts or vague summaries?
2. **Coverage**: did the subagent answer the assigned question?
3. **Gaps**: what remains unanswered?
4. **Conflicts**: do results disagree?
5. **Follow-ups**: what new questions emerged?

### Review Outcomes

| Situation | Main agent action |
|-----------|-------------------|
| Results are strong and complete | Mark current todo `completed`, move next todo to `in_progress` |
| Current phase needs another pass | Keep current todo `in_progress`, rewrite it more narrowly |
| New gaps appear | Add new pending todos |
| Sources conflict | Update todos first, usually by moving `Resolve conflicts and fill critical gaps` to `in_progress`, then dispatch verifier subagents |
| The final report is now justified | Move report todo to `in_progress` |

### Required Todo Update After Review

After a meaningful review round, the main agent should update the todo list to reflect the new state.

Typical update patterns:

- current todo becomes `completed`, next todo becomes `in_progress`
- current todo stays `in_progress` but is rewritten to a narrower question
- one or more new pending todos are added for gaps or conflict resolution

If review reveals **material conflicts** or **missing evidence that blocks synthesis**, update `write_todos` before dispatching the next round.
In most cases this means:

- mark the current research-round todo `completed` or narrow it explicitly
- set `Resolve conflicts and fill critical gaps` to `in_progress`
- keep `Synthesize findings into final report` as `pending`

Do not continue into another subagent round for conflict resolution while the todo list still implies you are in the previous phase.

Do not leave the todo list stale while continuing research rounds.

## Loop Discipline

- Cap the deep research loop at **3 rounds** unless the task clearly justifies more.
- Each round should become narrower and more targeted.
- If data is genuinely unavailable after repeated attempts, mark that as a limitation and move on.
- Avoid redispatching the same vague prompt.

## Phase 6: Synthesis and Report

Only synthesize after the research plan is complete enough.
Usually this means:

- core research todos are `completed`
- open conflicts are resolved or explicitly documented
- known gaps are clearly marked
- the final report todo is the only active step

### Output Format

**Default behavior: save the report as an artifact file** using the `save_artifact` tool (or write to a file if `save_artifact` is unavailable). Only output the report directly in the chat if the user explicitly asks for inline output.

When saving as artifact:
- Use a descriptive filename (e.g., `tesla-vs-byd-competitive-analysis.md`)
- After saving, reply to the user with a brief summary (3-5 sentences) of the key conclusions and mention that the full report has been saved

### Report Structure

The report should include:

1. **Executive summary** — key conclusions in 3-5 sentences
2. **Key findings** — the most important facts, each with inline source citations
3. **Analysis by research angle** — detailed findings organized by the angles researched, with source links
4. **Conflicts and uncertainty** — where sources disagreed and how conflicts were resolved (or not)
5. **Confidence and limitations** — what could not be verified, source quality issues

### Citation Requirements in the Report

Every factual claim in the report must trace back to a source from the subagent research. Use inline citations:

- Link to source URLs where available: `[Counterpoint Research](url)`
- Name the source when URL is unavailable: `(Source: CPCA monthly report, March 2026)`
- Mark unverified claims explicitly: `(unverified — single source only)`

Do NOT strip source information during synthesis. The reader should be able to answer both:

- "What did you conclude?"
- "What evidence supports that, and where can I verify it?"

## Quality Checklist

Before finalizing:

- [ ] Did I cover the key research angles?
- [ ] Do I have specific numbers, dates, or source-backed facts where appropriate?
- [ ] Did I review subagent findings instead of accepting them blindly?
- [ ] Did I update the todo plan as the research evolved?
- [ ] Did I handle conflicts or explicitly document them?
- [ ] Did I mark what could not be verified?
- [ ] Did I avoid writing the final report before research completion?
- [ ] Does every fact in the report include an inline source citation?
- [ ] Did I save the report as an artifact (not just output inline)?
- [ ] Are my todos tracking workflow stages, not research topics?

If any answer is no, continue the loop or mark the limitation explicitly.

## Common Mistakes

- **Skipping Phase 0 temporal grounding** — dispatching subagents without calling `datetime` first on time-sensitive topics, causing all subagents to operate with wrong time assumptions
- **Omitting date context from subagent prompts** — even if you called `datetime`, subagents have no memory of it; you must include "Today is YYYY-MM-DD" in each prompt
- **Writing research topics as todos** — todos like "Research market share" or "Analyze battery tech" are research angles, not workflow stages; they belong in subagent prompts, not in the todo list
- **One-liner subagent prompts** — a prompt like "Find Tesla market share data" is too vague; subagent prompts must include all five sections (Context, Task, Method, Output, Boundaries)
- **Stripping citations during synthesis** — subagent results carry source URLs and names; the final report must preserve them as inline citations, not summarize them away
- **Outputting report inline instead of saving** — unless the user asks for inline output, always save the report as an artifact file
- Treating each subagent as its own active todo
- Letting multiple todos appear active at once
- Never calling `write_todos` after the research plan changes
- Accepting vague subagent output without source attribution
- Dispatching dependent tasks in parallel
- Starting synthesis after one shallow round
- Hiding unresolved conflicts
- Using outdated information on temporal questions

## Output Standard

A successful deep research run should produce:

1. Coverage of the main verification points
2. Concrete facts gathered by specialized subagents
3. Explicit gaps and limitations
4. Conflict handling or conflict disclosure
5. A final synthesis produced only after the research work packages are complete
