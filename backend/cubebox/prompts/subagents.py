"""Subagent delegation prompt — injected when subagents are configured."""

SUBAGENT_PROMPT = """## Delegating Tasks to Subagents

You can delegate work to specialized subagents using the `task` tool. Each subagent runs independently and returns a result.

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

**Usage:**
- Provide a clear, self-contained `description` — the subagent has no access to your conversation history
- Include relevant context (time ranges, source preferences, specific constraints) in the description
- The subagent returns a single result when complete
- You can dispatch multiple subagents in parallel by calling `task` multiple times"""
