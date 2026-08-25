---
name: deep-research
description: Use when a question needs current multi-source research, multi-angle investigation, or iterative verification before producing a report or detailed answer
version: 3.3.0
keywords:
  - research
  - multi-agent
  - subagent-orchestration
  - deep-investigation
  - report-generation
  - verification
---

# Deep Research

Use a supervisor-subagent loop when one question needs current evidence from
multiple sources, competing explanations, or iterative verification. The main
agent owns the question, verification map, dependencies, review, and synthesis.
Subagents answer narrow questions; they do not manage the plan or write the final
report.

Use direct search for a quick fact with one clear source. Use `wide-research`
when breadth is the hard part and the primary output is a comprehensive list or
dataset.

## Non-negotiable invariants

1. Ground the question before planning or fan-out.
2. Build a verification map and identify dependencies before dispatch.
3. Run only independent tasks in the same wave.
4. Review and consolidate one wave before designing the next.
5. Preserve claim-level evidence, counter-evidence, gaps, and conflicts.
6. Synthesize only from the reviewed evidence set; mark inference and uncertainty.

## 1. Ground the question

Use the smallest useful reconnaissance action to understand the topic, source
landscape, and ambiguity. If the request is time-sensitive, anchor the current
date and relevant period before research, then include that context in later
subagent briefs. Do not call the date tool for timeless conceptual questions.

Clarify only a missing choice that materially changes the research question,
comparison, time range, evidence standard, or deliverable. Otherwise state a
reasonable assumption and proceed.

Classify the task:

- **verification:** test one claim and seek counter-evidence
- **comparison:** evaluate shared criteria across named subjects
- **explanation:** establish mechanisms, chronology, and competing accounts
- **comprehensive investigation:** combine several dependent verification points

## 2. Build the verification and dependency map

Before substantial fan-out, record:

```text
Question and intended deliverable:
As-of date or time range:
Definitions and comparison criteria:
Verification points or claims:
Preferred source types and evidence threshold:
Known inputs and evidence already available:
Dependencies between verification points:
Stopping rule and budget:
```

For each proposed task, identify what inputs it needs, whether those inputs exist
now, what evidence it should produce, and which later tasks consume that output.

Different angles are not automatically independent. Tasks may run in the same
wave only when none consumes another task's future output and each can complete
correctly from inputs available before the wave starts.

Common dependency chains include:

- source identification → source-specific extraction
- candidate discovery → candidate verification
- evidence collection → conflict resolution
- findings → synthesis

Do not write later-wave prompts before their required inputs exist. Later tasks
should be shaped by the reviewed findings, gaps, and conflicts from earlier
waves, not guessed in advance.

Use workflow-stage todos for substantial research, with one active phase:
ground and map, collect evidence, review and verify gaps, then synthesize. Todos
track phases, not research angles or individual subagents.

## 3. Run dependency-ordered waves

Deep research normally follows this shape:

```text
Wave 1: independent reconnaissance or evidence discovery
Review: consolidate evidence, gaps, conflicts, and dependencies
Wave 2: targeted verification using reviewed Wave 1 outputs
Review: resolve material uncertainty and update confidence
Synthesis: answer only from the reviewed evidence set
```

This is a model, not a mandatory number of rounds. A simple verification may need
one collection wave; a difficult conflict may need several narrower waves.

Within a wave, assign exact, non-overlapping verification points or intentionally
orthogonal evidence lanes. Parallel workers may independently test the same
material claim only when independent corroboration or counter-evidence is the
purpose. Do not use duplicate vague prompts as a substitute for coverage.

Bad same-wave design:

```text
A: discover the relevant companies
B: verify whether each relevant company qualifies
```

Task B lacks its candidate input. Run discovery, consolidate the candidate set,
then dispatch qualification in the next wave.

## 4. Brief research subagents

The generic subagent instructions already define self-contained prompts and
parallel execution semantics. Add the research contract needed for this slice:

- the exact verification point and why it matters
- date anchor, definitions, and existing inputs
- preferred primary sources and useful independent sources
- what would support, weaken, or falsify the claim
- explicit exclusions and non-overlap with sibling tasks
- the required evidence structure and stopping boundary

Require a compact structured result:

```text
Findings:
- claim, value or event, time, and source URL or citation marker
Counter-evidence:
Gaps:
Conflicts:
Limitations:
Confidence: high | medium | low, with reason
```

A source must support the exact claim attached to it. A homepage or generic
article link is not evidence for unrelated fields. Missing evidence remains a
gap; subagents must not fill it from general knowledge.

## 5. Maintain and review the evidence map

For long or multi-wave work, persist the verification map and reviewed evidence
in a workspace file so context compaction does not become the source of truth.
The main agent owns this evidence map. Record each verification point as
`supported`, `contested`, `unknown`, or `failed`, with source provenance
and unresolved questions.

After every wave, the main agent must review:

1. Did each worker answer its assigned verification point?
2. Does every material fact have direct evidence?
3. Are sources primary enough, current enough, and methodologically comparable?
4. What evidence conflicts, and is the disagreement definitional or factual?
5. Which gaps block the requested conclusion?
6. Which next tasks now have all required inputs?

Merge duplicated findings without losing independent provenance. Update the
verification map, confidence, and todo phase before another wave. The next wave
must target named gaps, conflicts, weak sources, or counter-evidence and should
usually be narrower than the previous one.

Do not treat a subagent result as verified merely because it is detailed. Do not
dispatch synthesis while material verification points still depend on unfinished
research.

## 6. Stop and synthesize

Stop when the core verification points have reviewed evidence, material conflicts
are resolved or disclosed, remaining gaps are unlikely to change the answer
within the budget, and another wave has a clear lower expected value than
synthesis. Repeated failure is a limitation, not permission to infer an answer.

The final response or report should:

- lead with the supported answer to the user's question
- distinguish sourced findings from the main agent's inference
- preserve inline citations or source URLs for factual claims
- explain material conflicts and how they affect confidence
- state unresolved gaps, source limitations, and the as-of date
- avoid stronger certainty than the evidence supports

Save a substantial reusable report as an artifact when useful or requested;
otherwise answer directly in the format the user asked for.

Before finalizing, confirm that every key conclusion traces to reviewed evidence,
no dependent work was mistaken for independent fan-out, counter-evidence was not
silently dropped, and limitations are visible.
