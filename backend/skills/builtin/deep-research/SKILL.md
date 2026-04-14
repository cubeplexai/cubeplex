---
name: deep-research
description: Use when a question needs current multi-source research, multi-angle investigation, or iterative verification before producing a report or detailed answer
version: 3.1.0
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

Before writing todos or dispatching subagents, do a **lightweight reconnaissance pass** to understand what the user is actually asking.

Use the smallest useful tool action first, for example:

- a simple web search to identify the topic, likely source landscape, or whether the question is temporal
- a date/time tool to anchor "today", "this year", quarter references, or deadline-sensitive requests
- a quick direct tool lookup when the request names a specific entity, event, metric, or date range

The goal of this pass is not to complete the research. The goal is to reduce ambiguity before planning.

During this pass, determine:

- what the actual research object is
- whether the request is time-sensitive
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

Use todos to track **workflow stages**, not individual subagents and not individual research angles.

Good todo list:

```json
{
  "todos": [
    {"content": "Clarify scope, success criteria, and research angles", "status": "completed"},
    {"content": "Run current research round across required angles", "status": "in_progress"},
    {"content": "Resolve conflicts and fill critical gaps", "status": "pending"},
    {"content": "Synthesize findings into final report", "status": "pending"}
  ]
}
```

Bad todo list:

- one todo per subagent if several would need to be `in_progress`
- one todo per research angle if that forces serial execution
- vague items like "Research topic"
- mixing execution detail and reporting in the same step

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

Example:

```python
subagent(
    name="Dr. Chen",
    role="Auto Market Analyst",
    task="Find global EV market share data for 2024-2026",
    prompt="As an auto market analyst, find global EV market share data for Tesla and BYD for 2024-2026. Return concrete numbers, dates, source names, and note any gaps."
)
subagent(
    name="Atlas",
    role="China Market Researcher",
    task="Find China market share evidence for Tesla and BYD",
    prompt="As a China auto market researcher, find 2024-2026 China EV market share data for Tesla and BYD. Return only specific figures and source-backed statements."
)
subagent(
    name="Scout",
    role="Industry Report Verifier",
    task="Check third-party industry reports for market share methodology",
    prompt="As an industry report verifier, identify how major reports define market share for Tesla and BYD, and note any methodology differences that could create conflicting numbers."
)
```

All three subagents may belong to the same todo:

- `Run current research round across required angles`

This is the core coordination rule:

> One active todo can contain many parallel subagent runs. The todo tracks the research phase. The subagents are the execution units inside it.

## Phase 4: Require Structured Subagent Output

Prompt subagents to return structured findings.
At minimum, ask for:

1. **Facts**: atomic, source-backed statements with numbers and dates
2. **Gaps**: what they could not confirm
3. **Conflicts**: where sources disagree
4. **Limitations**: any source quality or scope issues

Preferred fact format:

```text
[Subject] + [Time] + [Metric] + [Value] + [Source]
```

Good:

- "Tesla global BEV share in 2024 was X% according to source Y."

Bad:

- "Tesla performed well."

### Prompt Rules for Subagents

- give a specialist role
- include a clear time range
- ask for specific numbers, dates, and source context
- instruct the subagent to say "not found" when evidence is missing
- tell the subagent not to write a polished report

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

The final output should include:

1. Executive summary
2. Key findings
3. Analysis by research angle
4. Conflicts and uncertainty
5. Confidence and limitations

The reader should be able to answer both:

- "What did you conclude?"
- "What evidence supports that?"

## Quality Checklist

Before finalizing:

- [ ] Did I cover the key research angles?
- [ ] Do I have specific numbers, dates, or source-backed facts where appropriate?
- [ ] Did I review subagent findings instead of accepting them blindly?
- [ ] Did I update the todo plan as the research evolved?
- [ ] Did I handle conflicts or explicitly document them?
- [ ] Did I mark what could not be verified?
- [ ] Did I avoid writing the final report before research completion?

If any answer is no, continue the loop or mark the limitation explicitly.

## Common Mistakes

- Treating each subagent as its own active todo
- Letting multiple todos appear active at once
- Never calling `write_todos` after the research plan changes
- Accepting vague subagent output
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
