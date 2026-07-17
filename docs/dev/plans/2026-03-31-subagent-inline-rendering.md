# Subagent Inline Rendering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render subagent output inline within the assistant message, positioned after the tool_call block that dispatched it, with each subagent's output (text, tool calls, reasoning) grouped in a collapsible card.

**Architecture:** Backend renames the `task` tool to `subagent`, adds a `name` parameter for display, and uses `tool_call_id` as the subagent's `agent_id` so the frontend can correlate subagent streams with their originating tool_call blocks. Frontend renders `SubAgentCard` inline inside `AssistantMessage` when encountering a `tool_call` block with `name === "subagent"`, instead of rendering all subagents in a flat list above the message.

**Tech Stack:** Python (LangChain StructuredTool, InjectedToolCallId), TypeScript/React (Zustand, Next.js)

---

### Task 1: Backend — Rename tool and add name parameter

**Files:**
- Modify: `backend/cubeplex/middleware/subagents.py`

- [ ] **Step 1: Update `_TaskSchema` to `_SubAgentSchema` with `name` field**

```python
class _SubAgentSchema(BaseModel):
    name: str
    description: str
    subagent_type: str = "general-purpose"
```

- [ ] **Step 2: Add `InjectedToolCallId` to `_run_task` and use it for `sa_agent_id`**

Update imports:

```python
from typing import Annotated, Any, TypedDict
from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
```

Update `_run_task` signature and `sa_agent_id`:

```python
async def _run_task(
    name: str,
    description: str,
    subagent_type: str = "general-purpose",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    spec = subagent_map.get(subagent_type, subagent_map["general-purpose"])
    model = spec.get("model") or default_model
    if model is None:
        return f"[error: no model available for subagent '{subagent_type}']"

    tools: list[BaseTool] = list(spec.get("tools", []))
    middleware = list(spec.get("middleware", []))

    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=spec.get("system_prompt", ""),
        middleware=middleware,
    )

    queue = subagent_event_queue.get(None)

    try:
        if queue is not None:
            sa_agent_id = f"subagent:{tool_call_id}"
            last_ai_content: list[str] = []

            async for chunk in agent.astream(
                {"messages": [{"role": "user", "content": description}]},
                stream_mode="messages",
            ):
                await queue.put(("subagent", sa_agent_id, chunk))

                if isinstance(chunk, tuple) and len(chunk) >= 2:
                    msg = chunk[0]
                    c = getattr(msg, "content", "") or ""
                    msg_name = getattr(msg, "name", None)
                    if c and not msg_name:
                        last_ai_content.append(c)

            return "".join(last_ai_content) or "[subagent produced no output]"
        else:
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": description}]},
            )
            messages = result.get("messages", [])
            last = messages[-1] if messages else None
            if last and hasattr(last, "content"):
                content = last.content
                return content if isinstance(content, str) else str(content)
            return "[subagent produced no output]"
    except Exception as e:
        logger.error("Subagent '{}' failed: {}", subagent_type, e)
        return f"[error: {e}]"
```

- [ ] **Step 3: Update `StructuredTool.from_function` to use new name and schema**

```python
return StructuredTool.from_function(
    coroutine=_run_task,
    name="subagent",
    description=(
        f"Delegate a task to a subagent. Available subagent types: {available}. "
        "Provide a name (short label for display) and a self-contained description "
        "— the subagent has no conversation context."
    ),
    args_schema=_SubAgentSchema,
)
```

- [ ] **Step 4: Run backend linting and type check**

Run: `cd /home/chris/cubeplex/backend && make lint && make type-check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /home/chris/cubeplex/backend
git add cubeplex/middleware/subagents.py
git commit -m "refactor: rename task tool to subagent, add name param, use tool_call_id as agent_id"
```

---

### Task 2: Backend — Update subagent prompt to reflect new tool name

**Files:**
- Modify: `backend/cubeplex/prompts/subagents.py`

- [ ] **Step 1: Read the current subagent prompt**

Read `backend/cubeplex/prompts/subagents.py` fully before editing.

- [ ] **Step 2: Update all references from `task` tool to `subagent` tool**

Replace references to calling `task(...)` with `subagent(name=..., description=..., ...)` in the prompt text. Add guidance that `name` should be a short label (2-4 words) describing the subagent's role.

- [ ] **Step 3: Commit**

```bash
git add cubeplex/prompts/subagents.py
git commit -m "docs: update subagent prompt to reflect renamed tool and name parameter"
```

---

### Task 3: Backend — Update agent_id comment in schemas.py

**Files:**
- Modify: `backend/cubeplex/agents/schemas.py`

- [ ] **Step 1: Update the `agent_id` field description**

Change the description from `'task:xxx'` to `'subagent:<tool_call_id>'`:

```python
agent_id: str | None = Field(
    default=None,
    description="None for main agent, 'subagent:<tool_call_id>' for subagents",
)
```

- [ ] **Step 2: Commit**

```bash
git add cubeplex/agents/schemas.py
git commit -m "docs: update agent_id field description to match new subagent format"
```

---

### Task 4: Frontend — Pass `subAgentStreams` into `AssistantMessage`

**Files:**
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`

- [ ] **Step 1: Update `MessageList.tsx` — remove top-level SubAgentCard rendering, pass streams to AssistantMessage**

```tsx
'use client'

import { useEffect } from 'react'
import { useMessageStore, createApiClient } from '@cubeplex/core'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'

interface MessageListProps {
  conversationId: string
}

export function MessageList({ conversationId }: MessageListProps) {
  const { messages, isStreaming, statusPhase, mainStream, subAgentStreams } =
    useMessages(conversationId)
  const loadMessages = useMessageStore((s) => s.loadMessages)

  useEffect(() => {
    const client = createApiClient('')
    loadMessages(client, conversationId)
  }, [conversationId, loadMessages])

  const subAgentMap = Object.fromEntries(subAgentStreams)

  return (
    <ScrollArea className="flex-1 p-4">
      <div className="space-y-4 max-w-2xl mx-auto">
        {(messages ?? []).map((msg) => (
          <div key={msg.id}>
            {msg.role === 'user' && <UserMessage content={msg.content ?? ''} />}
            {msg.role === 'assistant' && <AssistantMessage message={msg} />}
          </div>
        ))}

        {isStreaming && mainStream && (
          <AssistantMessage
            stream={mainStream}
            isStreaming
            statusPhase={statusPhase}
            subAgentStreams={subAgentMap}
          />
        )}
      </div>
    </ScrollArea>
  )
}
```

Key change: removed the `SubAgentCard` map and `<>` wrapper. The `SubAgentCard` import is removed. `subAgentStreams` is converted to a `Record<string, AgentStream>` and passed as a prop.

- [ ] **Step 2: Update `AssistantMessage` props to accept `subAgentStreams`**

In `AssistantMessage.tsx`, update the `StreamingProps` interface:

```typescript
interface StreamingProps {
  message?: never
  stream: AgentStream
  isStreaming: true
  statusPhase?: string | null
  subAgentStreams?: Record<string, AgentStream>
}
```

And update `AssistantMessageProps` so `subAgentStreams` is accessible:

```typescript
type AssistantMessageProps = HistoryProps | StreamingProps
```

Update the component destructure:

```typescript
export function AssistantMessage(
  { message, stream, isStreaming, statusPhase, subAgentStreams }: AssistantMessageProps,
) {
```

No behavioral change yet — just plumbing.

- [ ] **Step 3: Run type check**

Run: `cd /home/chris/cubeplex/frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/components/chat/MessageList.tsx packages/web/components/chat/AssistantMessage.tsx
git commit -m "refactor: pass subAgentStreams into AssistantMessage instead of rendering at top level"
```

---

### Task 5: Frontend — Render SubAgentCard inline for subagent tool_call blocks

**Files:**
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`
- Modify: `frontend/packages/web/components/chat/SubAgentCard.tsx`

- [ ] **Step 1: Update `SubAgentCard` to accept an optional `subAgentStreams` prop for live streaming**

The card needs to show live output when streaming AND static content from historical messages. Update `SubAgentCard.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { ChevronDown, ChevronRight, Bot } from 'lucide-react'
import type { AgentStream } from '@cubeplex/core'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'

interface Props {
  name: string
  stream?: AgentStream
  isRunning: boolean
}

export function SubAgentCard({ name, stream, isRunning }: Props) {
  const [open, setOpen] = useState(true)

  const hasContent = stream && (stream.toolCalls.length > 0 || stream.text)

  return (
    <div className="border border-border rounded-lg overflow-hidden bg-muted/20">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger className="flex w-full items-center gap-2 px-3 py-2 text-sm
          text-muted-foreground hover:bg-muted/30 transition-colors">
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          <Bot className="h-3 w-3" />
          <span className="font-medium">{name}</span>
          {isRunning && (
            <span className="ml-auto flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="w-1 h-1 rounded-full bg-muted-foreground animate-bounce"
                  style={{ animationDelay: `${i * 150}ms` }}
                />
              ))}
            </span>
          )}
        </CollapsibleTrigger>

        <CollapsibleContent>
          {hasContent && (
            <div className="px-3 pb-3 pt-1 space-y-1">
              {stream.toolCalls.map((tc, i) => (
                <div key={i} className="text-xs font-mono text-muted-foreground truncate">
                  <span className="text-foreground/60">{tc.data.name}</span>
                  {' '}
                  <span className="opacity-60">
                    {JSON.stringify(tc.data.arguments).slice(0, 80)}
                  </span>
                </div>
              ))}
              {stream.text && (
                <p className="text-sm text-foreground/80 whitespace-pre-wrap">{stream.text}</p>
              )}
            </div>
          )}
          {!hasContent && isRunning && (
            <div className="px-3 pb-3 pt-1">
              <span className="text-xs text-muted-foreground animate-pulse">正在执行...</span>
            </div>
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
```

Key changes:
- Props simplified: `name` replaces `agentId`, `stream` is optional (for future historical rendering)
- Uses `Collapsible` from shadcn/ui for consistency with ReasoningBlock
- Shows "正在执行..." placeholder when running but no content yet

- [ ] **Step 2: Update `ContentBlockRenderer` to render subagent tool_call blocks as `SubAgentCard`**

In `AssistantMessage.tsx`, add the SubAgentCard import:

```typescript
import { SubAgentCard } from './SubAgentCard'
```

Update `ContentBlockRenderer` to accept `subAgentStreams`:

```typescript
function ContentBlockRenderer(
  { block, index, isLast, isStreaming, subAgentStreams }: {
    block: ContentBlock; index: number; isLast: boolean; isStreaming: boolean
    subAgentStreams?: Record<string, AgentStream>
  },
) {
  if (block.type === 'reasoning') {
    return (
      <div className="bg-card border border-border rounded-xl px-3 py-2.5">
        <ReasoningBlock
          reasoning={block.content}
          isStreaming={isStreaming && isLast}
          startedAt={block.started_at}
          durationMs={block.duration_ms}
        />
      </div>
    )
  }
  if (block.type === 'tool_call' && block.name === 'subagent') {
    const agentKey = `subagent:${block.tool_call_id}`
    const stream = subAgentStreams?.[agentKey]
    const displayName = (block.arguments as { name?: string }).name ?? 'Subagent'
    return (
      <SubAgentCard
        name={displayName}
        stream={stream}
        isRunning={isStreaming && !!stream}
      />
    )
  }
  if (block.type === 'tool_call') {
    return (
      <div className="bg-card border border-border rounded-xl px-3 py-2.5">
        <ToolCallList toolCalls={[{ name: block.name, arguments: block.arguments }]} />
      </div>
    )
  }
  // text block
  return (
    <div className={proseClasses}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content}</ReactMarkdown>
    </div>
  )
}
```

- [ ] **Step 3: Update `groupBlocks` to NOT group subagent tool_call blocks**

Subagent tool_calls should render individually (each one gets its own `SubAgentCard`), not be grouped with other tool_calls:

```typescript
function groupBlocks(blocks: ContentBlock[]): (ContentBlock | ContentBlock[])[] {
  const result: (ContentBlock | ContentBlock[])[] = []
  for (const block of blocks) {
    if (block.type === 'tool_call' && block.name !== 'subagent') {
      const last = result[result.length - 1]
      if (Array.isArray(last) && last[0].type === 'tool_call'
        && (last[0] as ContentBlock & { name: string }).name !== 'subagent') {
        last.push(block)
      } else {
        result.push([block])
      }
    } else {
      result.push(block)
    }
  }
  return result
}
```

- [ ] **Step 4: Pass `subAgentStreams` through the render chain**

In the `AssistantMessage` component body, pass `subAgentStreams` to `ContentBlockRenderer`:

```tsx
{grouped.map((item, i) => {
  if (Array.isArray(item)) {
    const toolCalls = item.map((b) => {
      const tc = b as ContentBlock & { type: 'tool_call' }
      return { name: tc.name, arguments: tc.arguments }
    })
    return (
      <div key={i} className="bg-card border border-border rounded-xl px-3 py-2.5">
        <ToolCallList toolCalls={toolCalls} />
      </div>
    )
  }
  return (
    <ContentBlockRenderer
      key={i}
      block={item}
      index={i}
      isLast={i === grouped.length - 1}
      isStreaming={isStreaming === true}
      subAgentStreams={subAgentStreams}
    />
  )
})}
```

- [ ] **Step 5: Run type check**

Run: `cd /home/chris/cubeplex/frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/components/chat/AssistantMessage.tsx packages/web/components/chat/SubAgentCard.tsx
git commit -m "feat: render subagent output inline within assistant message blocks"
```

---

### Task 6: Frontend — Update event type comment and clean up unused imports

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts`

- [ ] **Step 1: Update the `agent_id` comment**

```typescript
export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, unknown>
  agent_id: string | null    // null = main agent, "subagent:<tool_call_id>" = subagent
  agent_name: string | null  // subagent description
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/chris/cubeplex/frontend
git add packages/core/src/types/events.ts
git commit -m "docs: update agent_id comment to reflect new subagent format"
```

---

### Task 7: Verify end-to-end

- [ ] **Step 1: Run backend checks**

Run: `cd /home/chris/cubeplex/backend && make check`
Expected: All pass (format, lint, type-check, tests)

- [ ] **Step 2: Run frontend type check**

Run: `cd /home/chris/cubeplex/frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 3: Manual smoke test**

Start backend and frontend:
```bash
cd /home/chris/cubeplex/backend && python main.py &
cd /home/chris/cubeplex/frontend && pnpm dev &
```

Send a message that triggers subagent dispatch (e.g., a research question). Verify:
1. SubAgentCard appears inline within the assistant message, after the tool_call that dispatched it
2. Multiple parallel subagents each get their own card at the correct position
3. Subagent tool calls and text appear inside the card
4. Cards are collapsible
5. Loading animation shows while subagent is running

- [ ] **Step 4: Final commit if any fixes needed**
