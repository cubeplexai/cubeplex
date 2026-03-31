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

**Usage:**
- Provide a clear, self-contained `description` — the subagent has no access to your conversation history
- The subagent returns a single result when complete
- You can dispatch multiple subagents in parallel by calling `task` multiple times"""
