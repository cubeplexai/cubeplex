# Subagent Card Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign SubAgentCard with richer agent identity (personified name, role, DiceBear avatar), task context, activity dots, and collapsed-by-default output — inspired by Kimi's agent card UI.

**Architecture:** Backend adds `role`, `task` fields and renames `description` → `prompt` in the subagent tool schema, updates the delegation prompt to guide name/role/prompt generation. Frontend installs DiceBear bottts, redesigns SubAgentCard with avatar/role/task header, collapsed streaming output area, activity dot indicators, and adds a SubAgentCluster overview bar.

**Tech Stack:** Python/Pydantic (backend schema), Next.js/React/Tailwind CSS (frontend), @dicebear/core + @dicebear/collection (avatars)

---

## File Structure

**Backend (3 files):**
- Modify: `backend/cubeplex/middleware/subagents.py` — add `role`, `task` and rename `description` → `prompt` in `_SubAgentSchema`
- Modify: `backend/cubeplex/prompts/subagents.py` — update prompt with field guidelines
- Modify: `backend/cubeplex/agents/convert.py` — include `role`, `task` in subagent summary

**Frontend (7 files):**
- Modify: `frontend/packages/web/package.json` — add DiceBear dependencies
- Create: `frontend/packages/web/components/chat/AgentAvatar.tsx` — DiceBear bottts avatar component
- Create: `frontend/packages/web/components/chat/SubAgentCluster.tsx` — parallel agent overview bar
- Modify: `frontend/packages/web/components/chat/SubAgentCard.tsx` — full redesign
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx` — pass new props, render cluster
- Modify: `frontend/packages/core/src/types/message.ts` — update SubagentSummary type
- Modify: `frontend/packages/core/src/types/index.ts` — re-export if needed

---

### Task 1: Backend — Update subagent schema (add `role`, `task`; rename `description` → `prompt`)

**Files:**
- Modify: `backend/cubeplex/middleware/subagents.py:46-49, 75-80, 109-110`

- [ ] **Step 1: Update `_SubAgentSchema`**

In `backend/cubeplex/middleware/subagents.py`, replace the schema class:

```python
class _SubAgentSchema(BaseModel):
    name: str
    role: str
    task: str
    prompt: str
    subagent_type: str = "general-purpose"
```

- [ ] **Step 1b: Update `_run_subagent` function signature and usage**

The `_run_subagent` function currently takes `description: str` as a parameter and uses it as the user message sent to the subagent. Update the parameter name from `description` to `prompt`:

In the function signature (around line 75-80), change:

```python
    async def _run_subagent(
        name: str,
        description: str,
        subagent_type: str = "general-purpose",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str | ToolMessage:
```

to:

```python
    async def _run_subagent(
        name: str,
        role: str,
        task: str,
        prompt: str,
        subagent_type: str = "general-purpose",
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
    ) -> str | ToolMessage:
```

Then update the two places where `description` is used as the subagent's input message:

Line ~110 (stream mode):
```python
                async for chunk in agent.astream(
                    {"messages": [{"role": "user", "content": prompt}]},
```

Line ~129 (invoke mode):
```python
                result = await agent.ainvoke(
                    {"messages": [{"role": "user", "content": prompt}]},
```

- [ ] **Step 2: Run backend checks**

Run: `cd backend && make check`
Expected: All checks pass (format, lint, type-check, test)

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/middleware/subagents.py
git commit -m "feat(backend): add role, task and rename description to prompt in subagent schema"
```

---

### Task 2: Backend — Update subagent prompt

**Files:**
- Modify: `backend/cubeplex/prompts/subagents.py`

- [ ] **Step 1: Replace `SUBAGENT_PROMPT`**

Replace the entire content of `backend/cubeplex/prompts/subagents.py`:

```python
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
```

- [ ] **Step 2: Run backend checks**

Run: `cd backend && make check`
Expected: All checks pass

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/prompts/subagents.py
git commit -m "feat(backend): update subagent prompt with role/task field guidelines"
```

---

### Task 3: Backend — Include `role` and `task` in subagent summary

**Files:**
- Modify: `backend/cubeplex/agents/convert.py:27-58`

- [ ] **Step 1: Update `_consolidate_subagent_events` return type**

The function already returns `{"text", "tool_calls", "reasoning"}`. We need to thread through `role` and `task` from the tool_call arguments that spawned the subagent. However, `_consolidate_subagent_events` only sees the subagent's internal events — it doesn't have access to the parent tool_call arguments.

The `role` and `task` are already available on the frontend via `block.arguments.role` and `block.arguments.task` (from the parent AI message's tool_call). For historical messages, these are in `msg.tool_calls[].arguments`. So **no change needed to convert.py** — the frontend already has these fields from the tool call arguments.

Skip this task — no backend change needed.

---

### Task 4: Frontend — Update SubagentSummary type

**Files:**
- Modify: `frontend/packages/core/src/types/message.ts:4-8`

- [ ] **Step 1: Add `role` and `task` to SubagentSummary**

In `frontend/packages/core/src/types/message.ts`, update the interface:

```typescript
export interface SubagentSummary {
  text: string
  tool_calls: { name: string; arguments: Record<string, unknown> }[]
  reasoning: string
  role?: string
  task?: string
}
```

Note: For historical messages, `role` and `task` come from the parent tool_call's `arguments` field, not from SubagentSummary. These optional fields are for future backend enrichment. The primary source is `block.arguments.role` / `block.arguments.task`.

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/types/message.ts
git commit -m "feat(core): add optional role and task to SubagentSummary"
```

---

### Task 5: Frontend — Install DiceBear and create AgentAvatar

**Files:**
- Modify: `frontend/packages/web/package.json`
- Create: `frontend/packages/web/components/chat/AgentAvatar.tsx`

- [ ] **Step 1: Install DiceBear**

Run: `cd frontend && pnpm --filter web add @dicebear/core @dicebear/collection`

- [ ] **Step 2: Create AgentAvatar component**

Create `frontend/packages/web/components/chat/AgentAvatar.tsx`:

```tsx
'use client'

import { useMemo } from 'react'
import { createAvatar } from '@dicebear/core'
import { bottts } from '@dicebear/collection'

interface AgentAvatarProps {
  seed: string
  size?: number
  className?: string
}

export function AgentAvatar({ seed, size = 32, className }: AgentAvatarProps) {
  const svgDataUri = useMemo(() => {
    const avatar = createAvatar(bottts, {
      seed,
      size,
    })
    return avatar.toDataUri()
  }, [seed, size])

  return (
    <img
      src={svgDataUri}
      alt=""
      width={size}
      height={size}
      className={className}
    />
  )
}
```

- [ ] **Step 3: Verify build**

Run: `cd frontend && pnpm build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/package.json frontend/pnpm-lock.yaml frontend/packages/web/components/chat/AgentAvatar.tsx
git commit -m "feat(web): add DiceBear bottts AgentAvatar component"
```

---

### Task 6: Frontend — Create SubAgentCluster overview bar

**Files:**
- Create: `frontend/packages/web/components/chat/SubAgentCluster.tsx`

- [ ] **Step 1: Create SubAgentCluster component**

Create `frontend/packages/web/components/chat/SubAgentCluster.tsx`:

```tsx
'use client'

import { Zap } from 'lucide-react'

interface SubAgentClusterProps {
  activeCount: number
  totalCount: number
}

export function SubAgentCluster({ activeCount, totalCount }: SubAgentClusterProps) {
  if (totalCount < 2) return null

  const allDone = activeCount === 0

  return (
    <div className="flex items-center gap-1.5 px-2 py-1 text-xs text-muted-foreground">
      <Zap className={`size-3 ${allDone ? 'text-emerald-500' : 'text-primary animate-pulse'}`} />
      <span>
        Agent 集群
        <span className="mx-1 text-muted-foreground/40">·</span>
        {allDone
          ? `${totalCount} 个任务已完成`
          : `${activeCount} 个并行任务`}
      </span>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/packages/web/components/chat/SubAgentCluster.tsx
git commit -m "feat(web): add SubAgentCluster parallel agent overview bar"
```

---

### Task 7: Frontend — Redesign SubAgentCard

**Files:**
- Modify: `frontend/packages/web/components/chat/SubAgentCard.tsx`

This is the main task. The card gets avatar, role, task, collapsed output area, and activity dots.

- [ ] **Step 1: Rewrite SubAgentCard**

Replace the entire content of `frontend/packages/web/components/chat/SubAgentCard.tsx`:

```tsx
'use client'

import { useState, useEffect, useRef, memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CheckCircle2, ChevronDown, ChevronRight } from 'lucide-react'
import type { AgentStream } from '@cubeplex/core'
import { ToolCallItem } from './ToolCallItem'
import { AgentAvatar } from './AgentAvatar'
import { proseClasses } from '@/lib/utils'

interface Props {
  name: string
  role: string
  task: string
  index: number
  stream?: AgentStream
  isRunning: boolean
  toolResultMap: Record<string, { content: string; receivedAt: number }>
}

function formatDuration(ms: number): string {
  if (ms < 0) return '0s'
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

export const SubAgentCard = memo(function SubAgentCard({
  name,
  role,
  task,
  index,
  stream,
  isRunning,
  toolResultMap,
}: Props) {
  const [expanded, setExpanded] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const startedAt = useRef(Date.now())
  const scrollRef = useRef<HTMLDivElement>(null)

  // Reset start time when component mounts (new agent run)
  useEffect(() => {
    startedAt.current = Date.now()
  }, [])

  // Live elapsed timer
  useEffect(() => {
    if (!isRunning) return
    const tick = () => setElapsed(Date.now() - startedAt.current)
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [isRunning])

  // Auto-scroll streaming content
  useEffect(() => {
    if (isRunning && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [stream?.toolCalls.length, stream?.text, isRunning])

  const toolCalls = stream?.toolCalls ?? []
  const completedCount = toolCalls.filter(
    (tc) => toolResultMap[tc.data.tool_call_id],
  ).length
  const pendingTc = toolCalls.find(
    (tc) => !toolResultMap[tc.data.tool_call_id],
  )
  const hasContent = stream && (toolCalls.length > 0 || stream.text)
  const displayTime = isRunning ? elapsed : (hasContent ? elapsed : 0)

  return (
    <div className="border border-border rounded-xl overflow-hidden bg-muted/10 border-l-2
      border-l-primary/40">
      {/* Header */}
      <div className="flex items-start gap-2.5 px-3 py-2.5">
        <AgentAvatar seed={name} size={32} className="rounded-md shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm text-foreground">{name}</span>
            <span className="text-xs text-muted-foreground bg-muted/50 px-1.5 py-0.5
              rounded-md">{role}</span>
            <span className="ml-auto text-xs text-muted-foreground/50 font-mono tabular-nums">
              {String(index).padStart(2, '0')}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-0.5 truncate">{task}</p>
        </div>
      </div>

      {/* Content area — collapsed by default, expandable */}
      {hasContent && (
        <div className="border-t border-border">
          {/* Streaming viewport (always visible when running, shows last few items) */}
          {!expanded && (
            <div
              ref={scrollRef}
              className="overflow-hidden"
              style={{
                maxHeight: 'calc(2.5rem * 3)',
                maskImage: isRunning
                  ? 'linear-gradient(to bottom, transparent 0%, black 20%, black 80%, transparent 100%)'
                  : 'linear-gradient(to bottom, black 0%, black 80%, transparent 100%)',
                WebkitMaskImage: isRunning
                  ? 'linear-gradient(to bottom, transparent 0%, black 20%, black 80%, transparent 100%)'
                  : 'linear-gradient(to bottom, black 0%, black 80%, transparent 100%)',
              }}
            >
              {toolCalls.map((tc, i) => {
                const result = toolResultMap[tc.data.tool_call_id] ?? null
                return (
                  <ToolCallItem
                    key={tc.data.tool_call_id || i}
                    name={tc.data.name}
                    arguments={tc.data.arguments}
                    toolCallId={tc.data.tool_call_id}
                    toolResult={result}
                    timestamp={tc.timestamp}
                    isPending={isRunning && !result}
                  />
                )
              })}
            </div>
          )}

          {/* Expanded full content */}
          {expanded && (
            <div className="max-h-80 overflow-y-auto">
              {toolCalls.map((tc, i) => {
                const result = toolResultMap[tc.data.tool_call_id] ?? null
                return (
                  <ToolCallItem
                    key={tc.data.tool_call_id || i}
                    name={tc.data.name}
                    arguments={tc.data.arguments}
                    toolCallId={tc.data.tool_call_id}
                    toolResult={result}
                    timestamp={tc.timestamp}
                    isPending={isRunning && !result}
                    showDivider={i > 0}
                  />
                )
              })}
              {stream?.text && (
                <div className={`px-3 py-2 border-t border-border ${proseClasses}`}>
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{stream.text}</ReactMarkdown>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Footer: activity dots + expand toggle + elapsed time */}
      <div className="flex items-center gap-1.5 px-3 py-1.5 border-t border-border">
        {/* Activity dots */}
        <div className="flex items-center gap-1">
          {Array.from({ length: completedCount }, (_, i) => (
            <span key={`done-${i}`} className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          ))}
          {isRunning && pendingTc && (
            <span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
          )}
          {isRunning && !pendingTc && (
            <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
          )}
        </div>

        {/* Expand/collapse toggle */}
        {hasContent && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="ml-1 flex items-center gap-0.5 text-xs text-muted-foreground
              hover:text-foreground transition-colors"
          >
            {expanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
            <span>{expanded ? '收起' : '展开'}</span>
          </button>
        )}

        {/* Running indicator + elapsed time */}
        <span className="ml-auto flex items-center gap-1.5 text-xs text-muted-foreground">
          {isRunning && (
            <span className="flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="w-1 h-1 rounded-full bg-muted-foreground animate-pulse"
                  style={{ animationDelay: `${i * 200}ms` }}
                />
              ))}
            </span>
          )}
          {!isRunning && hasContent && <CheckCircle2 className="size-3 text-emerald-500" />}
          {displayTime >= 1000 && <span>{formatDuration(displayTime)}</span>}
        </span>
      </div>

      {/* Empty running state */}
      {!hasContent && isRunning && (
        <div className="px-3 pb-2.5">
          <span className="text-xs text-muted-foreground animate-pulse">正在执行...</span>
        </div>
      )}
    </div>
  )
})
```

- [ ] **Step 2: Verify types compile**

Run: `cd frontend && pnpm type-check`
Expected: No type errors

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/SubAgentCard.tsx
git commit -m "feat(web): redesign SubAgentCard with avatar, role, task, activity dots"
```

---

### Task 8: Frontend — Update AssistantMessage to pass new props and render cluster

**Files:**
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`

- [ ] **Step 1: Add SubAgentCluster import and subagent index tracking**

At the top of `AssistantMessage.tsx`, add the import:

```typescript
import { SubAgentCluster } from './SubAgentCluster'
```

- [ ] **Step 2: Update ContentBlockRenderer to pass new props**

In `ContentBlockRenderer`, update the subagent rendering block (around line 216-232). Replace:

```typescript
  if (block.type === 'tool_call' && block.name === 'subagent') {
    const agentKey = `subagent:${block.tool_call_id}`
    const stream = subAgentStreams?.[agentKey]
    // For historical messages, construct stream from consolidated data
    const historicalStream = !stream && subagentDataMap?.[agentKey]
      ? subagentSummaryToStream(subagentDataMap[agentKey])
      : undefined
    const displayName =
      (block.arguments as { name?: string }).name ?? 'Subagent'
    return (
      <SubAgentCard
        name={displayName}
        stream={stream ?? historicalStream}
        isRunning={isStreaming && !!stream}
        toolResultMap={toolResultMap}
      />
    )
  }
```

With:

```typescript
  if (block.type === 'tool_call' && block.name === 'subagent') {
    const agentKey = `subagent:${block.tool_call_id}`
    const stream = subAgentStreams?.[agentKey]
    const historicalStream = !stream && subagentDataMap?.[agentKey]
      ? subagentSummaryToStream(subagentDataMap[agentKey])
      : undefined
    const args = block.arguments as {
      name?: string; role?: string; task?: string
    }
    return (
      <SubAgentCard
        name={args.name ?? 'Subagent'}
        role={args.role ?? ''}
        task={args.task ?? ''}
        index={subagentIndex ?? 1}
        stream={stream ?? historicalStream}
        isRunning={isStreaming && !!stream}
        toolResultMap={toolResultMap}
      />
    )
  }
```

- [ ] **Step 3: Add `subagentIndex` prop to ContentBlockRenderer**

Update `ContentBlockRenderer` signature to accept `subagentIndex`:

```typescript
function ContentBlockRenderer(
  { block, index, isLast, isStreaming, subAgentStreams, subagentDataMap, toolResultMap,
    messageCreatedAt, subagentIndex }: {
    block: ContentBlock; index: number; isLast: boolean; isStreaming: boolean
    subAgentStreams?: Record<string, AgentStream>
    subagentDataMap?: Record<string, SubagentSummary>
    toolResultMap: Record<string, { content: string; receivedAt: number }>
    messageCreatedAt?: string
    subagentIndex?: number
  },
) {
```

- [ ] **Step 4: Add cluster bar and index computation in AssistantMessage**

In the `AssistantMessage` component body, after `const grouped = groupBlocks(blocks)`, add subagent index tracking and cluster info:

```typescript
  // Count subagent blocks for index assignment and cluster display
  let subagentCounter = 0
  const subagentIndexMap = new Map<number, number>()
  for (let i = 0; i < grouped.length; i++) {
    const item = grouped[i]
    if (!Array.isArray(item) && item.type === 'tool_call' && item.name === 'subagent') {
      subagentCounter++
      subagentIndexMap.set(i, subagentCounter)
    }
  }
  const totalSubagents = subagentCounter

  // Count active subagents (streaming)
  const activeSubagentCount = subAgentStreams
    ? Object.keys(subAgentStreams).length
    : 0
```

Then in the JSX, render the cluster bar before the grouped blocks (inside the `<div className="flex-1 max-w-[75%] space-y-2">`):

```tsx
        {totalSubagents >= 2 && (
          <SubAgentCluster
            activeCount={isStreaming === true ? activeSubagentCount : 0}
            totalCount={totalSubagents}
          />
        )}
```

- [ ] **Step 5: Pass subagentIndex when rendering ContentBlockRenderer**

In the `.map()` over `grouped`, when rendering a single block, pass the index:

```tsx
            <ContentBlockRenderer
              key={i}
              block={item}
              index={i}
              isLast={i === grouped.length - 1}
              isStreaming={isStreaming === true}
              subAgentStreams={subAgentStreams}
              subagentDataMap={subagentDataMap}
              toolResultMap={toolResultMap}
              messageCreatedAt={msgCreatedAt}
              subagentIndex={subagentIndexMap.get(i)}
            />
```

- [ ] **Step 6: Run type check and build**

Run: `cd frontend && pnpm type-check && pnpm build`
Expected: Both succeed

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/components/chat/AssistantMessage.tsx
git commit -m "feat(web): wire SubAgentCard new props and SubAgentCluster bar"
```

---

### Task 9: Visual QA and final adjustments

- [ ] **Step 1: Run dev server**

Run: `cd frontend && pnpm dev`

- [ ] **Step 2: Test with a real subagent interaction**

Send a message that triggers subagent delegation (e.g., a research task). Verify:
- Avatar renders from DiceBear bottts
- Name is personified (not a task description)
- Role badge shows
- Task summary shows below name
- Collapsed streaming viewport with gradient mask works
- Activity dots: green for completed tools, pulsing for running
- Expand/collapse toggle works
- Elapsed time displays
- SubAgentCluster bar appears with 2+ subagents
- Historical messages (after page refresh) render correctly

- [ ] **Step 3: Fix any visual issues found during QA**

Adjust spacing, colors, alignment as needed.

- [ ] **Step 4: Run full checks**

Run: `cd backend && make check`
Run: `cd frontend && pnpm type-check && pnpm build`
Expected: All pass

- [ ] **Step 5: Final commit if adjustments were made**

```bash
git add -A
git commit -m "fix(web): visual adjustments for SubAgentCard redesign"
```
