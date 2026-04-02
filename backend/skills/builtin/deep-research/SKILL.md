---
name: deep-research
description: Conduct comprehensive deep research using multi-agent orchestration. Use when questions require web research, multi-angle investigation, or content generation based on real-world information. Provides supervisor-subagent architecture for parallel research tasks.
version: 3.0.0
keywords:
  - research
  - multi-agent
  - subagent-orchestration
  - swarm-intelligence
  - deep-investigation
  - report-generation
---

# Deep Research Skill (v3)

## Overview

This skill provides a **supervisor-based multi-agent orchestration** methodology for conducting thorough research. The main agent acts as **Chief Research Strategist**, delegating atomic research tasks to specialized subagents via the `subagent` tool, reviewing their findings, and iterating until research is complete.

**Core Principle**: Never generate content from general knowledge alone. Research quality determines output quality.

---

## Architecture: Iterative Supervisor-Subagent Loop

```
User Query
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  MAIN AGENT (Chief Research Strategist / Supervisor)    │
│  • Decomposes research into atomic verification points  │
│  • Assigns tasks to subagents via `subagent` tool       │
└─────────────────────────────────────────────────────────┘
    │  subagent tool calls (parallel when independent)
    ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Subagent │  │ Subagent │  │ Subagent │
│ Extract  │  │ Extract  │  │ Extract  │
│ Facts A  │  │ Facts B  │  │ Facts C  │
└──────────┘  └──────────┘  └──────────┘
    │  key facts returned
    ▼
┌─────────────────────────────────────────────────────────┐
│  REVIEW (Main Agent)                                    │
│  • Are the facts clear and specific?                    │
│  • Are there gaps or unanswered questions?              │
│  • Do any findings conflict or need verification?       │
│  • Is any angle insufficiently covered?                 │
└─────────────────────────────────────────────────────────┘
    │                           │
    │ Gaps found                │ Research sufficient
    │ ▼                         │ ▼
    │ Loop back: spawn new      │ ┌──────────────────────┐
    │ subagents for gaps        │ │  SYNTHESIS            │
    │ (return to dispatch)      │ │  • Merge all facts    │
    └───────────────────────────│ │  • Resolve conflicts  │
                                │ │  • Generate report    │
                                │ └──────────────────────┘
```

**Key difference from v2**: This is a **loop**, not a pipeline. The supervisor reviews results after each round and spawns additional subagents for gaps, follow-ups, or verification — repeating until coverage is sufficient.

---

## When to Use This Skill

**Load this skill when:**
- User asks "research X", "investigate X", "explain X in depth"
- Questions require current, comprehensive information from multiple sources
- A single search would be insufficient
- Creating reports, articles, or content requiring real-world data
- Complex comparisons or competitive analysis

---

## Phase 1: Research Decomposition

### Step 1A: Intent Classification

Before starting, classify the research type:

| Type | Characteristics | Approach |
|------|----------------|----------|
| **Quick Fact** | Single data point, clear answer | Direct search, skip to Phase 4 |
| **Verification** | User provides claim, needs confirmation | Red-team style, search counter-evidence |
| **Comprehensive** | Multi-dimensional topic | Full orchestration with subagents |
| **Temporal** | Time-sensitive (prices, events, news) | Priority on official/authoritative sources |

### Step 1B: Atomic Decomposition

Break the research into **irreducible verification points** — each subagent task should be a single, focused question.

**Good decomposition:**
```
Topic: "Tesla competitive position vs BYD"
├── Angle 1: Market share data (2024-2026)
├── Angle 2: Technology comparison (battery, autopilot)
├── Angle 3: Financial performance (revenue, margins)
├── Angle 4: Production capacity and growth
└── Angle 5: Regulatory environment per market
```

**Bad decomposition:**
- "Research Tesla and BYD" (too broad, single subagent would be overwhelmed)
- "Compare everything" (interleaves multiple angles)

---

## Phase 2: Subagent Orchestration

### Parallel Task Dispatch

Dispatch **independent** research angles in parallel using multiple `subagent` calls:

```
subagent(
    name="Dr. Chen",
    role="汽车行业市场分析师",
    task="调研特斯拉与比亚迪2024-2026年市场份额数据",
    prompt="作为汽车行业市场分析师，请调研并对比特斯拉与比亚迪在2024-2026年的全球市场份额...",
)
subagent(
    name="Forge",
    role="电池技术研究员",
    task="对比特斯拉与比亚迪电池技术路线",
    prompt="作为电池技术研究员，请深入对比特斯拉与比亚迪的电池技术策略...",
)
subagent(
    name="Prof. Li",
    role="财务绩效分析师",
    task="分析特斯拉与比亚迪2024年财务表现",
    prompt="作为财务绩效分析师，请查找并分析特斯拉与比亚迪2024年的关键财务指标...",
)
```

**Rule**: Always dispatch independent angles in parallel. Research time scales inversely with parallelism.

### The `subagent` Tool

```python
subagent(
    name="Dr. Chen",           # Personified name matching the role
    role="经济分析师",           # Professional title (2-5 words)
    task="分析特斯拉2024年营收", # One-line task summary (shown in UI)
    prompt="...",              # Full professional brief for the subagent
    subagent_type="general-purpose"
)
```

### Subagent Goal: Extract Key Facts, Not Write Reports

Subagents are **fact extractors**, not report writers. Their prompt should instruct them to return:

1. **Atomic facts** — specific, verifiable statements with concrete data points
   - Good: "Tesla 2024 Q3 revenue was $25.2B, up 8% YoY"
   - Bad: "Tesla had strong revenue growth" (vague, no numbers)

2. **Structured output** — facts organized by dimension/topic, not free-form prose

3. **Limitations** — what they searched for but couldn't find, or conflicting data they encountered

**Key principles for subagent fact extraction:**
- **Zero hallucination**: Only report what was found in sources. If a data point wasn't found, say so explicitly.
- **Numeric integrity**: Preserve original numbers and units exactly as found. No unit conversions or rounding.
- **Atomic statements**: Each fact should be one specific claim: `[Subject] + [Time] + [Metric] + [Value]`
- **Conflict marking**: When sources disagree, report both values rather than picking one.

### Writing Effective Prompts

- Frame the request in the agent's domain language — brief them like a specialist
- Include time range: "2024-2025 revenue data" not just "revenue"
- State deliverables: "Return a list of specific data points with numbers, not general trends"
- Instruct fact extraction: "For each finding, provide the specific number, time period, and where you found it"
- Bad: "Search for Tesla revenue" (too generic, doesn't leverage agent expertise)
- Good: "As a financial analyst, find Tesla's Q1-Q4 2024 revenue broken down by region. Return specific numbers for each region and quarter. Note any data you searched for but couldn't find."

---

## Phase 3: Review Loop

**This is the critical phase that distinguishes deep research from shallow search.**

After subagents return their findings, the supervisor must review before proceeding:

### Review Checklist

For each subagent's results, ask:

1. **Fact clarity**: Are the facts specific with concrete numbers/dates? Or vague and hand-wavy?
2. **Completeness**: Did the subagent cover all aspects of its assigned angle?
3. **Gaps**: What questions remain unanswered? What data was "not found"?
4. **Conflicts**: Do any findings contradict other subagents' results?
5. **Follow-ups**: Did any finding reveal a new angle worth investigating?

### Decision: Loop or Proceed

| Situation | Action |
|-----------|--------|
| Major gaps in core angles | Spawn new subagents targeting specific gaps |
| Conflicting data between subagents | Spawn a verification subagent to resolve |
| A finding reveals an important new angle | Spawn a subagent for the new angle |
| Vague results without specific data | Re-dispatch with more specific prompt |
| All angles well-covered with concrete facts | Proceed to synthesis |

### Loop Discipline

- **Max 3 rounds** of iteration to avoid infinite loops
- Each round should have a **narrower, more specific** focus than the previous
- If data is genuinely unavailable after 2 attempts, mark it as a gap and move on
- Track what's been tried to avoid repeating the same searches

---

## Phase 4: Synthesis & Report

Once the review loop determines research is sufficient:

### Merging Strategy

1. **Conflict Resolution**: When subagents report conflicting data:
   - Look for root cause (different time periods, definitions, regions)
   - If unresolvable, present both values with context

2. **Completeness Check**:
   - Did each atomic verification point get answered?
   - Is evidence sufficient to support conclusions?

3. **Confidence Assessment**:
   - Mark well-supported findings vs. findings with limited data
   - Be honest about what couldn't be verified

### Report Structure

Output should include:

1. **Executive Summary** (2-3 sentences)
2. **Key Findings** (specific data points, not vague statements)
3. **Analysis by Angle** (corresponding to atomic decomposition)
4. **Confidence & Limitations** (honest assessment of gaps)

**Quality Bar**: A reader should be able to answer "So what?" and "How do you know?" from your report.

---

## Quality Checklist

Before completing research:

- [ ] Have I covered at least 3-5 different research angles?
- [ ] Do I have specific data points (numbers, dates), not just vague trends?
- [ ] Have I reviewed subagent results and followed up on gaps?
- [ ] Have I addressed conflicts between findings?
- [ ] Is my information current? (check time periods)
- [ ] Have I honestly marked what couldn't be found?

**If any answer is NO, continue researching before generating content.**

---

## Common Mistakes to Avoid

- Stopping after 1 round of subagents without reviewing results
- Accepting vague subagent output ("Tesla did well") without demanding specifics
- Searching only one angle of a multi-faceted topic
- Ignoring contradicting evidence
- Using outdated information when current data exists
- Starting report generation before research is complete
- Dispatching dependent tasks in parallel (waste of subagents)
- Letting the review loop run endlessly — cap at 3 rounds

---

## Output

After completing research, you should have:
1. Comprehensive coverage of all atomic verification points
2. Specific facts, data points, and statistics from subagent extractions
3. Honest confidence assessment and known gaps
4. Conflicts identified and (where possible) resolved

**Only then proceed to report generation.**
