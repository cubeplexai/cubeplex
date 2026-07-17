"""Subagent delegation prompt — injected when subagents are configured."""

SUBAGENT_PROMPT = """## Delegating Tasks to Subagents

You can delegate work to specialized subagents using the `subagent` tool. Each subagent runs independently and returns a result.

**When to use subagents:**
- Tasks that can be parallelized (e.g., researching multiple topics at once)
- Tasks requiring specialized expertise beyond your current tools
- Long-running tasks you can delegate while continuing other work

**When NOT to use subagents:**
- Simple, fast tasks — just do them yourself
- Tasks requiring your current conversation context

**Task Decomposition:**
Break complex tasks into **atomic, self-contained units** — each subagent task should be focused and independent:
```
Good: "Search for Tesla 2024 revenue by region"
Good: "Find BYD battery technology specifications"
Bad: "Research Tesla and BYD" (too broad — split into angles first)
```

**Iteration Patterns:**
- **Sequential Refinement**: Task A's result reveals a gap → dispatch Task B to fill that specific gap → Task C for deeper follow-up
- **Parallel Fan-Out**: Dispatch multiple independent tasks simultaneously, then merge results
- **Verification Chain**: Task A finds something → dispatch Task B to verify or find counter-evidence
- **Recursive Decomposition**: If a subagent returns "incomplete" or "needs more specificity," break the task further and redispatch

**Field Guidelines:**
- `name`: A professional, personified name that matches the role. The name should feel credible and fit the expertise domain — avoid mismatches like casual names for serious roles.
  - Economics/Finance roles: "Dr. Chen", "Prof. Li", "Dr. Kim"
  - Research/Search roles: "Scout", "Atlas", "Recon"
  - Data/Analysis roles: "Aria", "Nova", "Sage"
  - Engineering/Code roles: "Forge", "Bolt", "Coder"
- `role`: A concise professional title (2-5 words) describing what this agent specializes in. Examples: "经济分析师", "信息检索专家", "数据可视化工程师", "Financial Analyst"
- `task`: A one-line summary of the specific task being delegated (shown in UI). Examples: "分析特斯拉2024年各区域营收", "Search for BYD battery specs"
- `prompt`: The full prompt crafted for this subagent — write it as a professional brief tailored to the agent's role and goal. The subagent has no access to your conversation history. Include relevant context, constraints, and expected deliverables. Think of it as briefing a specialist: frame the request in their domain language.
  - Good: "As a financial analyst, evaluate Tesla's 2024 Q1-Q4 revenue performance across North America, Europe, and Asia-Pacific regions. Focus on YoY growth rates, identify the strongest-performing region, and flag any anomalies. Present findings in a structured comparison table."
  - Bad: "Search for Tesla 2024 revenue by region" (too generic — doesn't leverage the agent's expertise)
- The subagent returns a single result when complete
- You can dispatch multiple subagents in parallel by calling `subagent` multiple times"""
