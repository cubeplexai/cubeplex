# Subagent Card Redesign

## Goal

Redesign the SubAgentCard UI to show richer agent identity (personified name, role, avatar), task context, progress, and a collapsed-by-default output view — inspired by Kimi's agent card design.

## Backend Changes

### Schema: `_SubAgentSchema` (middleware/subagents.py)

Add `role` and `task` fields to the tool call schema:

```python
class _SubAgentSchema(BaseModel):
    name: str          # Personified name matching the role (e.g., "Dr. Chen", "Scout")
    role: str          # Professional role (e.g., "经济分析师", "信息检索专家")
    task: str          # Short task summary for display (e.g., "分析特斯拉2024年财务数据")
    prompt: str        # Full prompt crafted for the subagent's expertise and goal (not displayed in UI)
    subagent_type: str = "general-purpose"
```

### Prompt: `SUBAGENT_PROMPT` (prompts/subagents.py)

Update the subagent delegation prompt to instruct the main model:
- `name`: a professional, personified name that fits the role (not a task description). Examples: "Dr. Chen" for an economist, "Scout" for a search specialist, "Aria" for a data analyst. Must feel credible — no mismatched casual names for serious roles.
- `role`: a concise professional title describing what this agent specializes in.
- `task`: a one-line summary of the specific task being delegated.
- `prompt`: the full prompt crafted for the subagent — should be tailored to the agent's expertise and goal, not just a task description. The main agent should write this as a professional brief that helps the subagent perform at its best.

### Subagent event consolidation (agents/convert.py)

Add `role` and `task` to `SubagentSummary` so historical messages can display them. Extract from the tool_call arguments that triggered the subagent.

## Frontend Changes

### 1. Install DiceBear (packages/web)

```bash
pnpm --filter web add @dicebear/core @dicebear/collection
```

Use **bottts** style, seed from agent `name` for deterministic avatars.

### 2. SubAgentCluster component (new)

Renders above the subagent card group when 2+ subagents are active:

```
⚡ Agent 集群 · 3 个并行任务
```

- Count active subagent streams from `subAgentStreams`
- Hide when only 1 subagent or all complete

### 3. SubAgentCard redesign

**Header layout:**
```
┌─────────────────────────────────────────────┐
│  [avatar]  Dr. Chen          经济分析师  01  │
│            分析特斯拉2024年财务数据           │
├─────────────────────────────────────────────┤
│  ┌─ fixed-height streaming window ────────┐ │
│  │  ✓ web_search "tesla revenue 2024"     │ │
│  │  ● read_file "report.csv"              │ │
│  │  ··· (gradient mask)                   │ │
│  └────────────────────────────────────────┘ │
│  ●● ●●● ◉          running indicator · 8s   │
└─────────────────────────────────────────────┘
```

**Props additions:**
- `role: string` — from `block.arguments.role`
- `task: string` — from `block.arguments.task`
- `index: number` — sequential number (01, 02, ...)

**Avatar:** DiceBear bottts SVG generated from `name` seed, rendered inline. Size ~32px, rounded.

**Output area (like ReasoningBlock pattern):**
- **Running:** Fixed height (~3 lines) scrolling viewport with gradient mask. Shows tool calls streaming through.
- **Completed:** Collapses to summary line: "完成 · 5 个工具调用 · 12s". Click to expand full content.
- Agent text output is NOT shown by default (collapsed like reasoning).

**Activity dots (footer area):**
Tool calls are generated incrementally — total count is unknown at runtime. Instead of a progress bar, use dot indicators:
- Each completed tool call → small green filled dot (●)
- Currently running tool call → pulsing/blinking dot (◉)
- Overall running state → a subtle pulse animation + elapsed time on the right
- When agent is outputting text (not in a tool call), the running indicator still pulses to show activity
- **Completed state:** dots remain as a summary row: "●●●●● · 12s"

**Step type icons in ToolCallItem:**
Map tool names to semantic icons:
- `web_search`, `tavily_search` → Search icon
- `read_file`, `write_file` → File icon
- `code_execute`, `python_repl` → Code icon
- `web_fetch` → Globe icon
- default → Wrench icon

### 4. AssistantMessage changes

- Extract `role`, `task` from `block.arguments` and pass to SubAgentCard
- Compute subagent index from block order
- Render SubAgentCluster bar when multiple subagents present

### 5. Type changes (core package)

Update `SubagentSummary` to include `role` and `task` fields for historical rendering.

## Files to modify

**Backend:**
1. `backend/cubeplex/middleware/subagents.py` — add `role`, `task` to `_SubAgentSchema`
2. `backend/cubeplex/prompts/subagents.py` — update prompt with field guidelines
3. `backend/cubeplex/agents/convert.py` — include `role`, `task` in SubagentSummary

**Frontend:**
4. `frontend/packages/web/package.json` — add @dicebear/core, @dicebear/collection
5. `frontend/packages/web/components/chat/SubAgentCard.tsx` — full redesign
6. `frontend/packages/web/components/chat/SubAgentCluster.tsx` — new component
7. `frontend/packages/web/components/chat/AssistantMessage.tsx` — pass new props, render cluster
8. `frontend/packages/web/components/chat/ToolCallItem.tsx` — add semantic tool icons
9. `frontend/packages/core/src/types/message.ts` — update SubagentSummary type
