---
name: deep-research
description: Conduct comprehensive deep research using multi-agent orchestration. Use when questions require web research, multi-angle investigation, or content generation based on real-world information. Provides supervisor-subagent architecture for parallel research tasks.
version: 2.0.0
keywords:
  - research
  - multi-agent
  - subagent-orchestration
  - swarm-intelligence
  - deep-investigation
  - report-generation
---

# Deep Research Skill (v2)

## Overview

This skill provides a **supervisor-based multi-agent orchestration** methodology for conducting thorough research. The main agent acts as **Chief Research Strategist**, delegating atomic research tasks to specialized subagents via the `task` tool, then synthesizing results into comprehensive reports.

**Core Principle**: Never generate content from general knowledge alone. Research quality determines output quality.

---

## Architecture: Supervisor-Subagent Pattern

```
User Query
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  MAIN AGENT (Chief Research Strategist / Supervisor)    │
│  • Decomposes research into atomic verification points  │
│  • Assigns tasks to subagents via `task` tool          │
│  • Synthesizes findings from all subagents             │
│  • Validates completeness before reporting             │
└─────────────────────────────────────────────────────────┘
    │  task tool calls (parallel when independent)
    ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│ Subagent │  │ Subagent │  │ Subagent │
│ Research │  │ Research │  │ Research │
│ Angle A  │  │ Angle B  │  │ Angle C  │
└──────────┘  └──────────┘  └──────────┘
    │  results
    ▼
┌─────────────────────────────────────────────────────────┐
│  SYNTHESIS                                              │
│  • Merge findings from all subagents                   │
│  • Resolve conflicts (prioritize authoritative sources) │
│  • Fill gaps with additional targeted research          │
│  • Generate comprehensive report                        │
└─────────────────────────────────────────────────────────┘
```

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
| **Temporal** | Time-sensitive (prices, events, news) | Priority on T1 sources, timezone awareness |

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

### Step 1C: Source Hierarchy Planning

For each angle, plan which source tier to prioritize:

| Tier | Source | Priority | Use Case |
|------|--------|----------|----------|
| **T0** | User-uploaded files | Highest | Check first — user provided this |
| **T1** | Official/authoritative | High | Facts, data, official statements |
| **T2** | Established media/analyst reports | Medium | Context, trends, expert opinions |
| **T3** | Community/forums/blogs | Low | Leads, hints (verify before trusting) |

---

## Phase 2: Subagent Orchestration

### Parallel Task Dispatch

Dispatch **independent** research angles in parallel using multiple `task` calls:

```
Subagent 1 (Angle 1): "Search for Tesla market share data 2024-2026"
Subagent 2 (Angle 2): "Research Tesla vs BYD battery technology comparisons"
Subagent 3 (Angle 3): "Find Tesla and BYD financial performance metrics 2024"
```

**Rule**: Always dispatch independent angles in parallel. Research time scales inversely with parallelism.

### The `task` Tool

```python
task(
    description="Your research question — be specific and self-contained",
    subagent_type="general-purpose"  # Use for web research tasks
)
```

**Writing effective task descriptions:**
- Include time range: "2024-2025 revenue data" not just "revenue"
- Specify source tier if known: "Search official company filings"
- Include verification requirement: "Verify with at least 2 authoritative sources"
- State the goal: "Extract specific numbers, not just trends"

### Track System for Each Task

Based on cubemanus supervisor methodology:

| Track | Trigger | Strategy |
|-------|---------|----------|
| **Track A (Fast)** | Gap_Retry_Count=0, clear facts | T0→T1 direct search, methodology first if unfamiliar domain |
| **Track B (Lateral)** | 0 < Gap_Retry_Count < 3 | Red-teaming + proxy search when direct fails |
| **Track C (Circuit)** | Gap_Retry_Count ≥ 3 | Mark `[不可得]`, rotate to next angle |

### Source Selection Priority

Per cubemanus methodology:

1. **Methodology First** (for unfamiliar domains): Search "industry analysis framework" before raw data
2. **Draft-Driven**: Only search to fill specific gaps, never blind searching
3. **Red Teaming**: Assume conclusions are wrong, search counter-evidence
4. **Proxy Logic** (when direct unavailable):
   - Can't find company data → search components/events (suppliers, lawsuits, IPO)
   - Can't find official site → search regulatory filings, court records

---

## Phase 3: Result Synthesis

### Merging Strategy

1. **Conflict Resolution**: When subagents report conflicting data:
   - Prioritize T0 > T1 > T2 > T3
   - Look for root cause (different time periods, definitions, regions)
   - If unresolvable, present both with `[Conflict: Source A vs Source B]`

2. **Completeness Check**:
   - Did each atomic verification point get answered?
   - Any gaps remain? Dispatch targeted subagent for gaps
   - Is evidence sufficient to support conclusions?

3. **Confidence标记**:
   - `[Confirmed]` — Multiple authoritative sources agree
   - `[Partial]` — Some evidence but incomplete
   - `[Unverified]` — Single source or unverified claim
   - `[Unavailable]` — After Track C exhaustion

### Red Team Validation

Before finalizing, consider:
- What would **disprove** my conclusions?
- Search for counter-evidence (negative reports, regulatory issues)
- If red team finds nothing, confidence increases
- If red team finds something, update conclusions accordingly

---

## Phase 4: Report Generation

Output should include:

1. **Executive Summary** (2-3 sentences)
2. **Key Findings** (specific data points, not vague statements)
3. **Analysis by Angle** (corresponding to atomic decomposition)
4. **Source Attribution** (for credibility)
5. **Confidence & Limitations** (honest assessment)
6. **Outstanding Gaps** (what couldn't be verified)

**Quality Bar**: A reader should be able to answer "So what?" and "How do you know?" from your report.

---

## Quality Checklist

Before completing research:

- [ ] Have I covered at least 3-5 different research angles?
- [ ] Have I fetched full content from authoritative sources, not just snippets?
- [ ] Do I have specific data points, not just vague trends?
- [ ] Have I searched counter-evidence (red team)?
- [ ] Have I addressed conflicts between sources?
- [ ] Is my information current? (check timestamps)
- [ ] Have I marked confidence levels honestly?

**If any answer is NO, continue researching before generating content.**

---

## Common Mistakes to Avoid

- ❌ Stopping after 1-2 searches (insufficient for "deep" research)
- ❌ Relying on snippets without reading full sources
- ❌ Searching only one angle of a multi-faceted topic
- ❌ Ignoring contradicting evidence (red team failure)
- ❌ Using outdated information when current data exists
- ❌ Starting content generation before research is complete
- ❌ Dispatching dependent tasks in parallel (waste of subagents)

---

## Output

After completing research, you should have:
1. Comprehensive coverage of all atomic verification points
2. Specific facts, data points, and statistics with source attribution
3. Real-world examples and case studies
4. Expert perspectives and authoritative sources
5. Honest confidence assessment and known gaps

**Only then proceed to content generation.**
