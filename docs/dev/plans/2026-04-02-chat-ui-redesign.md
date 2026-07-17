# Chat UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the cubeplex chat interface to a split-screen agent UI with professional tool call display, task progress bar, enhanced SubAgent cards, and a right-side tool detail panel.

**Architecture:** Three-column adaptive layout (Sidebar | ChatPanel | ToolDetailPanel) using shadcn/ui ResizablePanelGroup. New Zustand stores for tool detail panel state and todo tracking. Two small backend changes (add TodoListMiddleware + add `tool_call_id` to `tool_result` events). All new components are small, focused files under `components/chat/` and `components/panel/`.

**Tech Stack:** Next.js 16, React 19, TypeScript 5, Tailwind CSS 4, Zustand 4, shadcn/ui, lucide-react, react-markdown

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `frontend/packages/web/components/ui/resizable.tsx` | shadcn/ui ResizablePanel components (install via CLI) |
| `frontend/packages/core/src/stores/toolDetailStore.ts` | Zustand store for right-side panel open/close/content state |
| `frontend/packages/web/hooks/useToolDetail.ts` | Thin hook wrapping toolDetailStore |
| `frontend/packages/web/components/chat/ToolCallItem.tsx` | Single tool call collapsed/expanded row |
| `frontend/packages/web/components/chat/ToolCallGroup.tsx` | Container grouping consecutive ToolCallItems |
| `frontend/packages/web/components/chat/TaskProgressBar.tsx` | Collapsible task progress indicator |
| `frontend/packages/web/components/panel/ToolDetailPanel.tsx` | Right-side panel container with header + content router |
| `frontend/packages/web/components/panel/SearchResultView.tsx` | Search tool detail view |
| `frontend/packages/web/components/panel/TerminalView.tsx` | Shell execute detail view |
| `frontend/packages/web/components/panel/WebFetchView.tsx` | Web fetch detail view |
| `frontend/packages/web/components/panel/GenericToolView.tsx` | Generic request/response JSON view |
| `frontend/packages/web/components/panel/PanelHeader.tsx` | Shared panel header with icon + title + copy + close |
| `frontend/packages/web/lib/toolIcons.ts` | Tool name → lucide icon mapping + param summary extraction |

### Modified Files

| File | Changes |
|------|---------|
| `backend/cubeplex/agents/graph.py` | Add `TodoListMiddleware()` to middleware stack |
| `backend/cubeplex/agents/stream.py` | Add `tool_call_id` to tool_result events |
| `frontend/packages/core/src/types/events.ts` | Add `TodoItem`, `PanelContentType`, extend `ToolResultEvent` with `tool_call_id` |
| `frontend/packages/core/src/stores/messageStore.ts` | Add `todos[]`, `toolResultMap`, process `write_todos`, store results by `tool_call_id` |
| `frontend/packages/core/src/stores/index.ts` | Export `toolDetailStore` |
| `frontend/packages/core/src/index.ts` | No change needed (already re-exports all) |
| `frontend/packages/web/hooks/useMessages.ts` | Expose `todos` and `toolResultMap` |
| `frontend/packages/web/components/layout/AppShell.tsx` | Restructure to ResizablePanelGroup with ChatPanel + ToolDetailPanel |
| `frontend/packages/web/components/chat/AssistantMessage.tsx` | Replace `ToolCallList` with `ToolCallItem`/`ToolCallGroup`, filter `write_todos`, pass `toolResultMap` |
| `frontend/packages/web/components/chat/SubAgentCard.tsx` | Reuse `ToolCallItem`, Markdown output, duration display, refined animations |
| `frontend/packages/web/components/chat/MessageList.tsx` | Pass `todos` to `TaskProgressBar`, pass `toolResultMap` through |
| `frontend/packages/web/app/conversations/[id]/page.tsx` | Move InputBar + TaskProgressBar inside AppShell ChatPanel |

### Deleted Code

| Location | What |
|----------|------|
| `AssistantMessage.tsx:88-100` | `ToolCallList` component (replaced by `ToolCallItem`) |

---

## Task 1: Backend — Add `tool_call_id` to `tool_result` events

**Files:**
- Modify: `backend/cubeplex/agents/stream.py:73-86`

- [ ] **Step 1: Add `tool_call_id` extraction to tool_result event**

In `backend/cubeplex/agents/stream.py`, find the tool result section (around line 73) and add `tool_call_id` extraction:

```python
    # Tool result (ToolMessage: has name and content)
    if tool_name and content:
        tool_call_id = (
            msg.get("tool_call_id", "") if isinstance(msg, dict)
            else getattr(msg, "tool_call_id", "")
        )
        events.append(
            {
                "type": "tool_result",
                "timestamp": timestamp,
                "data": {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": (
                        content if isinstance(content, str)
                        else str(content)
                    ),
                },
                "agent_id": agent_id,
            }
        )
        return events
```

The old code to replace is:

```python
    # Tool result (ToolMessage: has name and content)
    if tool_name and content:
        events.append(
            {
                "type": "tool_result",
                "timestamp": timestamp,
                "data": {
                    "tool_name": tool_name,
                    "content": content if isinstance(content, str) else str(content),
                },
                "agent_id": agent_id,
            }
        )
        return events
```

- [ ] **Step 2: Run backend checks**

Run: `cd backend && make check`
Expected: All format, lint, type-check, and tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/agents/stream.py
git commit -m "feat: add tool_call_id to tool_result SSE events"
```

---

## Task 2: Backend — Add TodoListMiddleware

**Files:**
- Modify: `backend/cubeplex/agents/graph.py:40-50`

- [ ] **Step 1: Add TodoListMiddleware import and registration**

In `backend/cubeplex/agents/graph.py`, add the import at the top with the other middleware imports:

```python
from langchain.agents.middleware.todo import TodoListMiddleware
```

Then in `create_cubeplex_agent()`, add it to the middleware stack after `SkillsMiddleware` and before `SubAgentMiddleware`:

```python
    _skills = skills or []
    middleware.append(SkillsMiddleware(skills=_skills))
    middleware.append(TodoListMiddleware())
    middleware.append(
        SubAgentMiddleware(
            subagents=subagents or [],
            default_model=llm,
            shared_tools=tools,
            shared_skills=_skills,
        )
    )
```

- [ ] **Step 2: Run backend checks**

Run: `cd backend && make check`
Expected: All checks pass. If `TodoListMiddleware` import path differs, check with: `cd backend && python -c "from langchain.agents.middleware.todo import TodoListMiddleware; print('OK')"`

If the import path is wrong, search for the correct path:
```bash
cd backend && python -c "import langchain.agents.middleware; print(dir(langchain.agents.middleware))"
```

- [ ] **Step 3: Commit**

```bash
git add backend/cubeplex/agents/graph.py
git commit -m "feat: add TodoListMiddleware for agent task planning"
```

---

## Task 3: Frontend Types — Extend events.ts with TodoItem, PanelContentType, tool_call_id

**Files:**
- Modify: `frontend/packages/core/src/types/events.ts`

- [ ] **Step 1: Add TodoItem and PanelContentType types, extend ToolResultEvent**

Replace the full content of `frontend/packages/core/src/types/events.ts`:

```typescript
// frontend/packages/core/src/types/events.ts
export type ContentBlock =
  | {
      type: 'reasoning'
      content: string
      started_at?: number
      duration_ms?: number
    }
  | { type: 'text'; content: string }
  | {
      type: 'tool_call'
      name: string
      arguments: Record<string, unknown>
      tool_call_id: string
    }

export interface TodoItem {
  id: string | null
  description: string
  status: 'pending' | 'in_progress' | 'completed'
}

export type PanelContentType =
  | 'search'
  | 'code_execute'
  | 'web_fetch'
  | 'terminal'
  | 'generic'
  | 'artifact'

export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_result'
  | 'error'
  | 'done'
  | 'status'

export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, unknown>
  agent_id: string | null
  agent_name: string | null
}

export interface TextDeltaEvent extends AgentEvent {
  type: 'text_delta'
  data: {
    content: string
    usage?: { input_tokens: number; output_tokens: number }
  }
}

export interface ReasoningEvent extends AgentEvent {
  type: 'reasoning'
  data: { content: string }
}

export interface ToolCallEvent extends AgentEvent {
  type: 'tool_call'
  data: {
    tool_call_id: string
    name: string
    arguments: Record<string, unknown>
  }
}

export interface ToolResultEvent extends AgentEvent {
  type: 'tool_result'
  data: {
    tool_name: string
    tool_call_id: string
    content: string
  }
}

export interface ErrorEvent extends AgentEvent {
  type: 'error'
  data: {
    error_code: string
    message: string
    details?: string
  }
}

export interface DoneEvent extends AgentEvent {
  type: 'done'
  data: Record<string, unknown>
}

export type StatusPhase =
  | 'sandbox_creating'
  | 'sandbox_ready'
  | 'sandbox_failed'

export interface StatusEvent extends AgentEvent {
  type: 'status'
  data: { phase: StatusPhase; detail?: string }
}
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass (types are only definitions, no runtime changes yet).

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/types/events.ts
git commit -m "feat: add TodoItem, PanelContentType types and tool_call_id to ToolResultEvent"
```

---

## Task 4: Frontend Store — Create toolDetailStore

**Files:**
- Create: `frontend/packages/core/src/stores/toolDetailStore.ts`
- Modify: `frontend/packages/core/src/stores/index.ts`

- [ ] **Step 1: Create toolDetailStore**

Create `frontend/packages/core/src/stores/toolDetailStore.ts`:

```typescript
// frontend/packages/core/src/stores/toolDetailStore.ts
import { create } from 'zustand'
import type { PanelContentType } from '../types'

export interface ToolDetailStore {
  isOpen: boolean
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  contentType: PanelContentType

  open: (
    toolName: string,
    toolArgs: Record<string, unknown>,
    toolResult: string | null,
  ) => void
  close: () => void
}

function detectContentType(toolName: string): PanelContentType {
  if (toolName === 'execute') return 'terminal'
  if (toolName === 'web_search' || toolName === 'search') {
    return 'search'
  }
  if (toolName === 'web_fetch' || toolName === 'fetch') {
    return 'web_fetch'
  }
  if (toolName === 'code_execute' || toolName === 'python') {
    return 'code_execute'
  }
  return 'generic'
}

export const useToolDetailStore = create<ToolDetailStore>(
  (set) => ({
    isOpen: false,
    toolName: '',
    toolArgs: {},
    toolResult: null,
    contentType: 'generic',

    open: (toolName, toolArgs, toolResult) =>
      set({
        isOpen: true,
        toolName,
        toolArgs,
        toolResult,
        contentType: detectContentType(toolName),
      }),

    close: () => set({ isOpen: false }),
  }),
)
```

- [ ] **Step 2: Export from stores/index.ts**

In `frontend/packages/core/src/stores/index.ts`, add the export:

```typescript
export {
  useConversationStore,
  type ConversationStore,
} from './conversationStore'
export {
  useMessageStore,
  type MessageStore,
  type AgentStream,
} from './messageStore'
export {
  useToolDetailStore,
  type ToolDetailStore,
} from './toolDetailStore'
```

- [ ] **Step 3: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/stores/toolDetailStore.ts \
       frontend/packages/core/src/stores/index.ts
git commit -m "feat: add toolDetailStore for right-side panel state"
```

---

## Task 5: Frontend Store — Extend messageStore with todos and toolResultMap

**Files:**
- Modify: `frontend/packages/core/src/stores/messageStore.ts`

- [ ] **Step 1: Add TodoItem import, new state fields, and processing logic**

In `frontend/packages/core/src/stores/messageStore.ts`:

1. Add `TodoItem` to the import from `'../types'`:

```typescript
import type {
  ContentBlock, TodoItem,
  Message, TextDeltaEvent, ToolCallEvent,
  ToolResultEvent, ReasoningEvent,
} from '../types'
```

2. Add new fields to the `AgentStream` interface:

```typescript
export interface AgentStream {
  text: string
  toolCalls: ToolCallEvent[]
  toolResults: ToolResultEvent[]
  reasoning: string
  blocks: ContentBlock[]
  name: string | null
}
```

3. Add new fields to the `MessageStore` interface, after `error`:

```typescript
export interface MessageStore {
  messages: Record<string, Message[]>
  streamAgents: Record<string, AgentStream>
  isStreaming: boolean
  statusPhase: string | null
  error: string | null
  todos: TodoItem[]
  toolResultMap: Record<string, { content: string; receivedAt: number }>

  loadMessages(
    client: ApiClient,
    conversationId: string,
  ): Promise<void>
  send(
    client: ApiClient,
    conversationId: string,
    content: string,
  ): Promise<void>
  clearStream(): void
}
```

4. Add initial state in the `create<MessageStore>` call:

```typescript
  todos: [],
  toolResultMap: {},
```

5. In the `send` method's initial `set()`, reset todos and toolResultMap:

```typescript
    set((s) => ({
      messages: {
        ...s.messages,
        [conversationId]: [
          ...(s.messages[conversationId] ?? []),
          userMessage,
        ],
      },
      streamAgents: { [MAIN_AGENT_KEY]: emptyStream() },
      isStreaming: true,
      statusPhase: null,
      error: null,
      todos: [],
      toolResultMap: {},
    }))
```

6. In the `tool_call` handler, add special handling for `write_todos` — upsert into `todos[]`:

```typescript
        } else if (event.type === 'tool_call') {
          const e = event as ToolCallEvent
          set((s) => {
            const prev =
              s.streamAgents[agentKey] ?? emptyStream(event.agent_name)

            // Upsert write_todos into todos list
            let nextTodos = s.todos
            if (e.data.name === 'write_todos') {
              const desc = String(
                (e.data.arguments as { description?: string })
                  .description ?? '',
              )
              const status = (
                (e.data.arguments as { status?: string }).status
                  ?? 'pending'
              ) as TodoItem['status']
              const existing = s.todos.findIndex(
                (t) => t.description === desc,
              )
              if (existing >= 0) {
                nextTodos = [...s.todos]
                nextTodos[existing] = {
                  ...nextTodos[existing],
                  status,
                }
              } else {
                nextTodos = [
                  ...s.todos,
                  { id: null, description: desc, status },
                ]
              }
            }

            return {
              todos: nextTodos,
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: {
                  ...prev,
                  toolCalls: [...prev.toolCalls, e],
                  blocks: appendToolCallBlock(
                    prev.blocks,
                    e.data.name,
                    e.data.arguments,
                    e.data.tool_call_id,
                  ),
                },
              },
            }
          })
```

7. In the `tool_result` handler, store into `toolResultMap` keyed by `tool_call_id`, and update todo `id` for `write_todos`:

```typescript
        } else if (event.type === 'tool_result') {
          const e = event as ToolResultEvent
          const toolCallId =
            (e.data as { tool_call_id?: string }).tool_call_id
              ?? ''
          set((s) => {
            const newMap = { ...s.toolResultMap }
            if (toolCallId) {
              newMap[toolCallId] = {
                content: e.data.content,
                receivedAt: Date.now(),
              }
            }

            // Update todo id from write_todos result
            let nextTodos = s.todos
            if (e.data.tool_name === 'write_todos' && toolCallId) {
              try {
                const parsed = JSON.parse(e.data.content)
                const taskId = parsed.task_id as string | undefined
                if (taskId) {
                  // Find the most recent todo without an id
                  const idx = s.todos.findIndex(
                    (t) => t.id === null,
                  )
                  if (idx >= 0) {
                    nextTodos = [...s.todos]
                    nextTodos[idx] = {
                      ...nextTodos[idx],
                      id: taskId,
                    }
                  }
                }
              } catch {
                // Ignore parse errors
              }
            }

            return {
              todos: nextTodos,
              toolResultMap: newMap,
              streamAgents: {
                ...s.streamAgents,
                [agentKey]: {
                  ...s.streamAgents[agentKey]
                    ?? emptyStream(event.agent_name),
                  toolResults: [
                    ...(s.streamAgents[agentKey]?.toolResults
                      ?? []),
                    e,
                  ],
                },
              },
            }
          })
```

8. In the `clearStream` method, also clear todos and toolResultMap:

```typescript
  clearStream() {
    set({
      streamAgents: {},
      isStreaming: false,
      statusPhase: null,
      todos: [],
      toolResultMap: {},
    })
  },
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts
git commit -m "feat: add todos and toolResultMap to messageStore"
```

---

## Task 6: Frontend Hooks — Extend useMessages, create useToolDetail

**Files:**
- Modify: `frontend/packages/web/hooks/useMessages.ts`
- Create: `frontend/packages/web/hooks/useToolDetail.ts`

- [ ] **Step 1: Extend useMessages to expose todos and toolResultMap**

Replace `frontend/packages/web/hooks/useMessages.ts`:

```typescript
'use client'

import { useMessageStore } from '@cubeplex/core'

export function useMessages(conversationId: string) {
  const messagesMap = useMessageStore((s) => s.messages) ?? {}
  const messages = messagesMap[conversationId] ?? []
  const isStreaming =
    useMessageStore((s) => s.isStreaming) ?? false
  const statusPhase = useMessageStore((s) => s.statusPhase)
  const streamAgents = useMessageStore((s) => s.streamAgents)
  const todos = useMessageStore((s) => s.todos)
  const toolResultMap =
    useMessageStore((s) => s.toolResultMap)

  const agents = streamAgents ?? {}
  const mainStream = agents['main'] ?? null
  const subAgentStreams = Object.entries(agents).filter(
    ([key]) => key !== 'main',
  )

  return {
    messages,
    isStreaming,
    statusPhase,
    mainStream,
    subAgentStreams,
    todos,
    toolResultMap,
  }
}
```

- [ ] **Step 2: Create useToolDetail hook**

Create `frontend/packages/web/hooks/useToolDetail.ts`:

```typescript
'use client'

import { useToolDetailStore } from '@cubeplex/core'

export function useToolDetail() {
  const isOpen = useToolDetailStore((s) => s.isOpen)
  const toolName = useToolDetailStore((s) => s.toolName)
  const toolArgs = useToolDetailStore((s) => s.toolArgs)
  const toolResult = useToolDetailStore((s) => s.toolResult)
  const contentType =
    useToolDetailStore((s) => s.contentType)
  const open = useToolDetailStore((s) => s.open)
  const close = useToolDetailStore((s) => s.close)

  return {
    isOpen,
    toolName,
    toolArgs,
    toolResult,
    contentType,
    open,
    close,
  }
}
```

- [ ] **Step 3: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/hooks/useMessages.ts \
       frontend/packages/web/hooks/useToolDetail.ts
git commit -m "feat: extend useMessages with todos/toolResultMap, add useToolDetail hook"
```

---

## Task 7: Utility — Create toolIcons helper

**Files:**
- Create: `frontend/packages/web/lib/toolIcons.ts`

- [ ] **Step 1: Create the tool icon mapping and param summary helper**

Create `frontend/packages/web/lib/toolIcons.ts`:

```typescript
import {
  Terminal,
  Search,
  Globe,
  Code,
  Bot,
  Wrench,
  type LucideIcon,
} from 'lucide-react'

const iconMap: Record<string, LucideIcon> = {
  execute: Terminal,
  web_search: Search,
  search: Search,
  web_fetch: Globe,
  fetch: Globe,
  code_execute: Code,
  python: Code,
  subagent: Bot,
}

export function getToolIcon(toolName: string): LucideIcon {
  return iconMap[toolName] ?? Wrench
}

/**
 * Extract a human-readable summary from tool arguments.
 * Returns the most meaningful parameter value, truncated.
 */
export function getParamSummary(
  toolName: string,
  args: Record<string, unknown>,
  maxLen = 60,
): string {
  let value = ''
  if (toolName === 'execute') {
    value = String(args.command ?? args.cmd ?? '')
  } else if (
    toolName === 'web_search' || toolName === 'search'
  ) {
    value = String(args.query ?? args.q ?? '')
  } else if (
    toolName === 'web_fetch' || toolName === 'fetch'
  ) {
    value = String(args.url ?? '')
  } else {
    // Use first string argument value
    const firstVal = Object.values(args).find(
      (v) => typeof v === 'string',
    )
    value = firstVal ? String(firstVal) : ''
  }
  if (value.length > maxLen) {
    return value.slice(0, maxLen) + '...'
  }
  return value
}
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/lib/toolIcons.ts
git commit -m "feat: add tool icon mapping and param summary helper"
```

---

## Task 8: Install shadcn/ui Resizable component

**Files:**
- Create: `frontend/packages/web/components/ui/resizable.tsx`

- [ ] **Step 1: Install the resizable component**

Run: `cd frontend/packages/web && npx shadcn@latest add resizable`

If the CLI doesn't work or the project isn't configured for shadcn CLI, manually install the dependency and create the component:

```bash
cd frontend && pnpm --filter web add react-resizable-panels
```

Then create `frontend/packages/web/components/ui/resizable.tsx`:

```tsx
"use client"

import { GripVertical } from "lucide-react"
import * as ResizablePrimitive from "react-resizable-panels"
import { cn } from "@/lib/utils"

function ResizablePanelGroup({
  className,
  ...props
}: React.ComponentProps<typeof ResizablePrimitive.PanelGroup>) {
  return (
    <ResizablePrimitive.PanelGroup
      data-slot="resizable-panel-group"
      className={cn(
        "flex h-full w-full",
        "data-[panel-group-direction=vertical]:flex-col",
        className,
      )}
      {...props}
    />
  )
}

function ResizablePanel({
  ...props
}: React.ComponentProps<typeof ResizablePrimitive.Panel>) {
  return (
    <ResizablePrimitive.Panel
      data-slot="resizable-panel"
      {...props}
    />
  )
}

function ResizableHandle({
  withHandle,
  className,
  ...props
}: React.ComponentProps<
  typeof ResizablePrimitive.PanelResizeHandle
> & { withHandle?: boolean }) {
  return (
    <ResizablePrimitive.PanelResizeHandle
      data-slot="resizable-handle"
      className={cn(
        "bg-border focus-visible:ring-ring relative flex"
          + " w-px items-center justify-center"
          + " after:absolute after:inset-y-0 after:-left-2"
          + " after:-right-2 focus-visible:ring-1"
          + " focus-visible:ring-offset-1"
          + " focus-visible:outline-hidden"
          + " data-[panel-group-direction=vertical]:h-px"
          + " data-[panel-group-direction=vertical]:w-full"
          + " data-[panel-group-direction=vertical]:"
          + "after:left-0"
          + " data-[panel-group-direction=vertical]:"
          + "after:h-4"
          + " data-[panel-group-direction=vertical]:"
          + "after:-top-2"
          + " data-[panel-group-direction=vertical]:"
          + "after:-bottom-2"
          + " data-[panel-group-direction=vertical]:"
          + "after:w-full"
          + " [&[data-panel-group-direction=vertical]>div]"
          + ":rotate-90",
        className,
      )}
      {...props}
    >
      {withHandle && (
        <div className="bg-border z-10 flex h-4 w-3
          items-center justify-center rounded-xs border">
          <GripVertical className="size-2.5" />
        </div>
      )}
    </ResizablePrimitive.PanelResizeHandle>
  )
}

export { ResizablePanelGroup, ResizablePanel, ResizableHandle }
```

**Note:** Check if `@/lib/utils` with a `cn` function exists. If not, create it:

```bash
ls frontend/packages/web/lib/utils.ts 2>/dev/null || echo "MISSING"
```

If missing, create `frontend/packages/web/lib/utils.ts`:

```typescript
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
```

And install deps: `cd frontend && pnpm --filter web add clsx tailwind-merge`

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/ui/resizable.tsx \
       frontend/packages/web/lib/utils.ts \
       frontend/packages/web/package.json \
       frontend/pnpm-lock.yaml
git commit -m "feat: install shadcn/ui resizable panel components"
```

---

## Task 9: Component — ToolCallItem (collapsed/expanded tool call row)

**Files:**
- Create: `frontend/packages/web/components/chat/ToolCallItem.tsx`

- [ ] **Step 1: Create ToolCallItem component**

Create `frontend/packages/web/components/chat/ToolCallItem.tsx`:

```tsx
'use client'

import { useState, useEffect, useRef } from 'react'
import {
  ChevronRight,
  ChevronDown,
  Clock,
  CheckCircle2,
  Circle,
  PanelRight,
} from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { getToolIcon, getParamSummary } from '@/lib/toolIcons'
import { useToolDetailStore } from '@cubeplex/core'

interface ToolCallItemProps {
  name: string
  arguments: Record<string, unknown>
  toolCallId: string
  toolResult?: { content: string; receivedAt: number } | null
  timestamp?: string
  /** True while this tool is still executing */
  isPending: boolean
  /** Show border-top separator (not first in group) */
  showDivider?: boolean
}

function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}s`
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return s > 0 ? `${m}m${s}s` : `${m}m`
}

export function ToolCallItem({
  name,
  arguments: args,
  toolCallId,
  toolResult,
  timestamp,
  isPending,
  showDivider,
}: ToolCallItemProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const startedAt = useRef(Date.now())
  const openPanel = useToolDetailStore((s) => s.open)

  // Record start time from timestamp or now
  useEffect(() => {
    if (timestamp) {
      startedAt.current = new Date(timestamp).getTime()
    }
  }, [timestamp])

  // Live timer while pending
  useEffect(() => {
    if (!isPending) return
    const tick = () =>
      setElapsed(Date.now() - startedAt.current)
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [isPending])

  const duration = toolResult
    ? toolResult.receivedAt - startedAt.current
    : elapsed

  const Icon = getToolIcon(name)
  const summary = getParamSummary(name, args)

  const handleViewInPanel = () => {
    openPanel(name, args, toolResult?.content ?? null)
  }

  // Truncate result for inline preview
  const resultLines = toolResult?.content.split('\n') ?? []
  const showTruncated = resultLines.length > 10
  const previewText = showTruncated
    ? resultLines.slice(0, 6).join('\n') + '\n...'
    : toolResult?.content ?? ''

  return (
    <div className={showDivider ? 'border-t border-border' : ''}>
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        <CollapsibleTrigger
          className="flex w-full items-center gap-2 px-3 py-2
            text-sm hover:bg-muted/50 transition-colors
            cursor-pointer"
        >
          <Icon className="size-3.5 text-muted-foreground
            shrink-0" />
          <span className="font-medium text-foreground
            shrink-0">
            {name}
          </span>
          {summary && (
            <>
              <span className="text-muted-foreground/40
                shrink-0">
                |
              </span>
              <span className="text-xs text-muted-foreground
                truncate">
                {summary}
              </span>
            </>
          )}
          <span className="ml-auto flex items-center gap-1.5
            shrink-0">
            {isPending ? (
              <>
                <Circle className="size-2.5 text-blue-500
                  animate-pulse" />
                <span className="text-xs
                  text-muted-foreground">
                  {formatDuration(elapsed)}
                </span>
              </>
            ) : toolResult ? (
              <>
                <CheckCircle2 className="size-3
                  text-emerald-500" />
                <span className="text-xs
                  text-muted-foreground">
                  {formatDuration(duration)}
                </span>
              </>
            ) : null}
            {isOpen ? (
              <ChevronDown className="size-3.5
                text-muted-foreground" />
            ) : (
              <ChevronRight className="size-3.5
                text-muted-foreground" />
            )}
          </span>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="px-3 pb-3 space-y-2">
            {toolResult && (
              <>
                <div className="flex items-center gap-1.5
                  text-xs text-muted-foreground">
                  <Clock className="size-3" />
                  <span>{formatDuration(duration)}</span>
                </div>
                <div className="bg-muted rounded-md p-2
                  max-h-48 overflow-auto">
                  <pre className="font-mono text-xs
                    text-foreground whitespace-pre-wrap
                    break-all">
                    {previewText}
                  </pre>
                </div>
                {showTruncated && (
                  <button
                    onClick={handleViewInPanel}
                    className="flex items-center gap-1
                      text-xs text-primary
                      hover:underline cursor-pointer"
                  >
                    <PanelRight className="size-3" />
                    View in panel
                  </button>
                )}
              </>
            )}
            {!toolResult && isPending && (
              <span className="text-xs text-muted-foreground
                animate-pulse">
                Executing...
              </span>
            )}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/ToolCallItem.tsx
git commit -m "feat: add ToolCallItem component with collapsed/expanded states"
```

---

## Task 10: Component — ToolCallGroup (consecutive tool call container)

**Files:**
- Create: `frontend/packages/web/components/chat/ToolCallGroup.tsx`

- [ ] **Step 1: Create ToolCallGroup component**

Create `frontend/packages/web/components/chat/ToolCallGroup.tsx`:

```tsx
'use client'

import type { ContentBlock } from '@cubeplex/core'
import { ToolCallItem } from './ToolCallItem'

interface ToolCallGroupProps {
  blocks: (ContentBlock & { type: 'tool_call' })[]
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number }
  >
  isStreaming: boolean
}

export function ToolCallGroup({
  blocks,
  toolResultMap,
  isStreaming,
}: ToolCallGroupProps) {
  return (
    <div className="bg-card border border-border rounded-xl
      overflow-hidden border-l-2
      border-l-muted-foreground/20">
      {blocks.map((block, i) => {
        const result =
          toolResultMap[block.tool_call_id] ?? null
        const isPending = isStreaming && !result
        return (
          <ToolCallItem
            key={block.tool_call_id || i}
            name={block.name}
            arguments={block.arguments}
            toolCallId={block.tool_call_id}
            toolResult={result}
            isPending={isPending}
            showDivider={i > 0}
          />
        )
      })}
    </div>
  )
}
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/ToolCallGroup.tsx
git commit -m "feat: add ToolCallGroup container for consecutive tool calls"
```

---

## Task 11: Component — TaskProgressBar

**Files:**
- Create: `frontend/packages/web/components/chat/TaskProgressBar.tsx`

- [ ] **Step 1: Create TaskProgressBar component**

Create `frontend/packages/web/components/chat/TaskProgressBar.tsx`:

```tsx
'use client'

import { useState, useEffect, useRef } from 'react'
import {
  ListChecks,
  CheckCircle2,
  Circle,
  ChevronUp,
  ChevronDown,
} from 'lucide-react'
import type { TodoItem } from '@cubeplex/core'

interface TaskProgressBarProps {
  todos: TodoItem[]
}

export function TaskProgressBar({ todos }: TaskProgressBarProps) {
  const [isExpanded, setIsExpanded] = useState(false)
  const prevCount = useRef(0)

  // Auto-expand on first todo arrival
  useEffect(() => {
    if (prevCount.current === 0 && todos.length > 0) {
      setIsExpanded(true)
    }
    prevCount.current = todos.length
  }, [todos.length])

  if (todos.length === 0) return null

  const completed = todos.filter(
    (t) => t.status === 'completed',
  ).length
  const inProgress = todos.find(
    (t) => t.status === 'in_progress',
  )

  return (
    <div className="bg-card border-t border-border">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center gap-2 px-4 py-2
          text-sm hover:bg-muted/30 transition-colors"
      >
        <ListChecks className="size-3.5
          text-muted-foreground shrink-0" />
        <span className="font-medium text-foreground">
          Task Progress {completed}/{todos.length}
        </span>
        {inProgress && !isExpanded && (
          <span className="text-xs text-muted-foreground
            truncate ml-2">
            <Circle className="size-2.5 text-blue-500
              animate-pulse inline mr-1" />
            {inProgress.description}
          </span>
        )}
        <span className="ml-auto shrink-0">
          {isExpanded ? (
            <ChevronDown className="size-3.5
              text-muted-foreground" />
          ) : (
            <ChevronUp className="size-3.5
              text-muted-foreground" />
          )}
        </span>
      </button>

      {isExpanded && (
        <div className="px-4 pb-2 space-y-1">
          {todos.map((todo, i) => (
            <div
              key={todo.id ?? i}
              className="flex items-center gap-2 py-0.5 text-sm"
            >
              {todo.status === 'completed' ? (
                <CheckCircle2 className="size-3.5
                  text-emerald-500 shrink-0" />
              ) : todo.status === 'in_progress' ? (
                <Circle className="size-3.5 text-blue-500
                  animate-pulse shrink-0" />
              ) : (
                <Circle className="size-3.5
                  text-muted-foreground/30 shrink-0" />
              )}
              <span
                className={
                  todo.status === 'completed'
                    ? 'text-muted-foreground'
                    : 'text-foreground'
                }
              >
                {todo.description}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/TaskProgressBar.tsx
git commit -m "feat: add TaskProgressBar component for todo progress display"
```

---

## Task 12: Panel Components — PanelHeader, TerminalView, SearchResultView, WebFetchView, GenericToolView

**Files:**
- Create: `frontend/packages/web/components/panel/PanelHeader.tsx`
- Create: `frontend/packages/web/components/panel/TerminalView.tsx`
- Create: `frontend/packages/web/components/panel/SearchResultView.tsx`
- Create: `frontend/packages/web/components/panel/WebFetchView.tsx`
- Create: `frontend/packages/web/components/panel/GenericToolView.tsx`

- [ ] **Step 1: Create PanelHeader**

Create `frontend/packages/web/components/panel/PanelHeader.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { X, Copy, Check } from 'lucide-react'
import { getToolIcon, getParamSummary } from '@/lib/toolIcons'

interface PanelHeaderProps {
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  onClose: () => void
}

export function PanelHeader({
  toolName,
  toolArgs,
  toolResult,
  onClose,
}: PanelHeaderProps) {
  const [copied, setCopied] = useState(false)
  const Icon = getToolIcon(toolName)
  const summary = getParamSummary(toolName, toolArgs, 40)

  const handleCopy = async () => {
    const text = toolResult ?? JSON.stringify(toolArgs, null, 2)
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <header className="h-11 border-b border-border flex
      items-center gap-2 px-4 shrink-0 bg-card">
      <Icon className="size-3.5 text-muted-foreground
        shrink-0" />
      <span className="text-sm font-medium text-foreground
        shrink-0">
        {toolName}
      </span>
      {summary && (
        <span className="text-xs text-muted-foreground
          truncate">
          {summary}
        </span>
      )}
      <span className="ml-auto flex items-center gap-1">
        <button
          onClick={handleCopy}
          className="p-1 rounded hover:bg-muted/50
            transition-colors"
          title="Copy"
        >
          {copied ? (
            <Check className="size-3.5 text-emerald-500" />
          ) : (
            <Copy className="size-3.5
              text-muted-foreground" />
          )}
        </button>
        <button
          onClick={onClose}
          className="p-1 rounded hover:bg-muted/50
            transition-colors"
          title="Close"
        >
          <X className="size-3.5 text-muted-foreground" />
        </button>
      </span>
    </header>
  )
}
```

- [ ] **Step 2: Create TerminalView**

Create `frontend/packages/web/components/panel/TerminalView.tsx`:

```tsx
interface TerminalViewProps {
  args: Record<string, unknown>
  result: string | null
}

export function TerminalView({
  args,
  result,
}: TerminalViewProps) {
  const command = String(args.command ?? args.cmd ?? '')

  // Parse exit code from result if present
  const exitMatch = result?.match(/\[exit:\s*(\d+)\]\s*$/)
  const exitCode = exitMatch ? exitMatch[1] : null
  const output = exitMatch
    ? result!.slice(0, exitMatch.index).trimEnd()
    : result

  return (
    <div className="p-4 space-y-3">
      {command && (
        <div className="font-mono text-sm font-medium
          text-foreground">
          $ {command}
        </div>
      )}
      {output && (
        <div className="bg-muted rounded-lg p-3">
          <pre className="font-mono text-sm text-foreground
            whitespace-pre-wrap break-all">
            {output}
          </pre>
        </div>
      )}
      {exitCode !== null && (
        <div className="text-xs text-muted-foreground
          text-right">
          exit: {exitCode}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Create SearchResultView**

Create `frontend/packages/web/components/panel/SearchResultView.tsx`:

```tsx
interface SearchItem {
  title: string
  url: string
  snippet: string
}

interface SearchResultViewProps {
  result: string | null
}

function parseSearchResults(raw: string): SearchItem[] {
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      return parsed.map((item: Record<string, unknown>) => ({
        title: String(item.title ?? ''),
        url: String(item.url ?? item.link ?? ''),
        snippet: String(
          item.snippet ?? item.description ?? '',
        ),
      }))
    }
    if (parsed.results && Array.isArray(parsed.results)) {
      return parseSearchResults(
        JSON.stringify(parsed.results),
      )
    }
  } catch {
    // Not JSON — fall through
  }
  return []
}

function getDomain(url: string): string {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}

export function SearchResultView({
  result,
}: SearchResultViewProps) {
  const items = result ? parseSearchResults(result) : []

  if (items.length === 0 && result) {
    // Fallback: show raw text
    return (
      <div className="p-4">
        <pre className="font-mono text-sm text-foreground
          whitespace-pre-wrap">
          {result}
        </pre>
      </div>
    )
  }

  return (
    <div className="p-4 space-y-3">
      <div className="text-sm text-muted-foreground">
        {items.length} results
      </div>
      {items.map((item, i) => (
        <a
          key={i}
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block bg-muted/30 rounded-lg p-3
            hover:bg-muted/50 transition-colors"
        >
          <div className="font-medium text-sm
            text-foreground">
            {item.title}
          </div>
          {item.url && (
            <div className="text-xs text-muted-foreground
              mt-0.5">
              {getDomain(item.url)}
            </div>
          )}
          {item.snippet && (
            <div className="text-sm text-foreground/80
              mt-1 line-clamp-2">
              {item.snippet}
            </div>
          )}
        </a>
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Create WebFetchView**

Create `frontend/packages/web/components/panel/WebFetchView.tsx`:

```tsx
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface WebFetchViewProps {
  args: Record<string, unknown>
  result: string | null
}

const proseClasses = `prose prose-sm dark:prose-invert max-w-none
  prose-p:leading-relaxed prose-p:my-1
  prose-headings:font-semibold prose-headings:mt-3
  prose-headings:mb-1 prose-headings:text-foreground
  prose-p:text-foreground prose-li:text-foreground
  prose-strong:text-foreground
  prose-code:text-foreground prose-code:text-[0.8em]
  prose-code:bg-muted prose-code:px-1 prose-code:py-0.5
  prose-code:rounded
  prose-code:before:content-none
  prose-code:after:content-none
  prose-pre:bg-muted prose-pre:border prose-pre:border-border
  prose-pre:rounded-lg prose-pre:text-[0.8em]
  prose-ul:my-1 prose-ol:my-1 prose-li:my-0
  prose-a:text-primary`

export function WebFetchView({
  args,
  result,
}: WebFetchViewProps) {
  const url = String(args.url ?? '')

  return (
    <div className="p-4 space-y-3">
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-primary hover:underline
            break-all"
        >
          {url}
        </a>
      )}
      {result && (
        <div className={proseClasses}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {result}
          </ReactMarkdown>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Create GenericToolView**

Create `frontend/packages/web/components/panel/GenericToolView.tsx`:

```tsx
'use client'

import { useState } from 'react'
import { Copy, Check } from 'lucide-react'

interface GenericToolViewProps {
  args: Record<string, unknown>
  result: string | null
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      onClick={handleCopy}
      className="p-1 rounded hover:bg-muted/50
        transition-colors"
      title="Copy"
    >
      {copied ? (
        <Check className="size-3 text-emerald-500" />
      ) : (
        <Copy className="size-3 text-muted-foreground" />
      )}
    </button>
  )
}

function formatContent(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

export function GenericToolView({
  args,
  result,
}: GenericToolViewProps) {
  const requestText = JSON.stringify(args, null, 2)
  const responseText = result ? formatContent(result) : null

  return (
    <div className="p-4 space-y-4">
      <div>
        <div className="flex items-center justify-between
          mb-2">
          <span className="text-xs font-medium
            text-muted-foreground uppercase tracking-wider">
            Request
          </span>
          <CopyButton text={requestText} />
        </div>
        <div className="bg-muted rounded-lg p-3">
          <pre className="font-mono text-sm text-foreground
            whitespace-pre-wrap break-all">
            {requestText}
          </pre>
        </div>
      </div>
      {responseText && (
        <div>
          <div className="flex items-center justify-between
            mb-2">
            <span className="text-xs font-medium
              text-muted-foreground uppercase tracking-wider">
              Response
            </span>
            <CopyButton text={responseText} />
          </div>
          <div className="bg-muted rounded-lg p-3">
            <pre className="font-mono text-sm text-foreground
              whitespace-pre-wrap break-all">
              {responseText}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 6: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/packages/web/components/panel/
git commit -m "feat: add panel components — PanelHeader, TerminalView, SearchResultView, WebFetchView, GenericToolView"
```

---

## Task 13: Component — ToolDetailPanel (right-side container)

**Files:**
- Create: `frontend/packages/web/components/panel/ToolDetailPanel.tsx`

- [ ] **Step 1: Create ToolDetailPanel**

Create `frontend/packages/web/components/panel/ToolDetailPanel.tsx`:

```tsx
'use client'

import { useToolDetail } from '@/hooks/useToolDetail'
import { ScrollArea } from '@/components/ui/scroll-area'
import { PanelHeader } from './PanelHeader'
import { TerminalView } from './TerminalView'
import { SearchResultView } from './SearchResultView'
import { WebFetchView } from './WebFetchView'
import { GenericToolView } from './GenericToolView'

export function ToolDetailPanel() {
  const {
    toolName,
    toolArgs,
    toolResult,
    contentType,
    close,
  } = useToolDetail()

  return (
    <div className="flex flex-col h-full bg-background">
      <PanelHeader
        toolName={toolName}
        toolArgs={toolArgs}
        toolResult={toolResult}
        onClose={close}
      />
      <ScrollArea className="flex-1">
        {contentType === 'terminal' && (
          <TerminalView args={toolArgs} result={toolResult} />
        )}
        {contentType === 'search' && (
          <SearchResultView result={toolResult} />
        )}
        {contentType === 'web_fetch' && (
          <WebFetchView args={toolArgs} result={toolResult} />
        )}
        {contentType === 'generic' && (
          <GenericToolView args={toolArgs} result={toolResult} />
        )}
        {contentType === 'code_execute' && (
          <GenericToolView args={toolArgs} result={toolResult} />
        )}
        {contentType === 'artifact' && (
          <GenericToolView args={toolArgs} result={toolResult} />
        )}
      </ScrollArea>
    </div>
  )
}
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/panel/ToolDetailPanel.tsx
git commit -m "feat: add ToolDetailPanel with content type routing"
```

---

## Task 14: Layout — Restructure AppShell to split-screen with ResizablePanelGroup

**Files:**
- Modify: `frontend/packages/web/components/layout/AppShell.tsx`
- Modify: `frontend/packages/web/app/conversations/[id]/page.tsx`

- [ ] **Step 1: Rewrite AppShell with ResizablePanelGroup**

Replace `frontend/packages/web/components/layout/AppShell.tsx`:

```tsx
'use client'

import { ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from '@/components/ui/resizable'
import { ToolDetailPanel } from '@/components/panel/ToolDetailPanel'
import { useToolDetail } from '@/hooks/useToolDetail'

interface AppShellProps {
  children: ReactNode
  headerTitle?: string
}

export function AppShell({
  children,
  headerTitle,
}: AppShellProps) {
  const { isOpen } = useToolDetail()

  return (
    <div className="flex h-screen bg-background
      text-foreground">
      <Sidebar />
      <ResizablePanelGroup direction="horizontal">
        <ResizablePanel
          defaultSize={isOpen ? 50 : 100}
          minSize={30}
        >
          <div className="flex flex-col h-full
            overflow-hidden">
            <header className="h-11 border-b border-border
              flex items-center px-4 shrink-0">
              <span className="text-sm text-muted-foreground
                truncate flex-1">
                {headerTitle || ''}
              </span>
              <ThemeToggle />
            </header>
            <main className="flex-1 flex flex-col
              overflow-hidden">
              {children}
            </main>
          </div>
        </ResizablePanel>

        {isOpen && (
          <>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={50} minSize={25}>
              <ToolDetailPanel />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
    </div>
  )
}
```

- [ ] **Step 2: Update chat page to move TaskProgressBar into content flow**

Replace `frontend/packages/web/app/conversations/[id]/page.tsx`:

```tsx
'use client'

import { useParams } from 'next/navigation'
import { useEffect } from 'react'
import {
  useConversationStore,
  createApiClient,
} from '@cubeplex/core'
import { AppShell } from '@/components/layout/AppShell'
import { MessageList } from '@/components/chat/MessageList'
import { InputBar } from '@/components/layout/InputBar'
import { TaskProgressBar } from '@/components/chat/TaskProgressBar'
import { useMessages } from '@/hooks/useMessages'

export default function ChatPage() {
  const params = useParams()
  const conversationId = params.id as string
  const { setActive, fetchList, conversations } =
    useConversationStore()
  const { todos } = useMessages(conversationId)

  useEffect(() => {
    setActive(conversationId)
    const client = createApiClient('')
    fetchList(client)
  }, [conversationId, setActive, fetchList])

  const currentConvo = conversations.find(
    (c) => c.id === conversationId,
  )

  return (
    <AppShell headerTitle={currentConvo?.title}>
      <MessageList conversationId={conversationId} />
      <TaskProgressBar todos={todos} />
      <div className="border-t border-border px-4 py-3
        bg-background">
        <InputBar conversationId={conversationId} />
      </div>
    </AppShell>
  )
}
```

- [ ] **Step 3: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/layout/AppShell.tsx \
       frontend/packages/web/app/conversations/[id]/page.tsx
git commit -m "feat: restructure AppShell to split-screen layout with ResizablePanelGroup"
```

---

## Task 15: Refactor AssistantMessage — Replace ToolCallList, filter write_todos, pass toolResultMap

**Files:**
- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`
- Modify: `frontend/packages/web/components/chat/MessageList.tsx`

- [ ] **Step 1: Update MessageList to pass toolResultMap**

In `frontend/packages/web/components/chat/MessageList.tsx`, update the component to pass `toolResultMap` to `AssistantMessage`:

```tsx
'use client'

import { useEffect, useMemo } from 'react'
import { useMessageStore, createApiClient } from '@cubeplex/core'
import type { Message, SubagentSummary } from '@cubeplex/core'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'

interface MessageListProps {
  conversationId: string
}

/**
 * Build a map from tool_call_id -> SubagentSummary
 * by scanning tool messages.
 */
function buildSubagentDataMap(
  messages: Message[],
): Record<string, SubagentSummary> {
  const map: Record<string, SubagentSummary> = {}
  for (const msg of messages) {
    if (
      msg.role === 'tool' &&
      msg.name === 'subagent' &&
      msg.tool_call_id &&
      msg.subagent_events
    ) {
      map[`subagent:${msg.tool_call_id}`] =
        msg.subagent_events
    }
  }
  return map
}

export function MessageList({
  conversationId,
}: MessageListProps) {
  const {
    messages,
    isStreaming,
    statusPhase,
    mainStream,
    subAgentStreams,
    toolResultMap,
  } = useMessages(conversationId)
  const loadMessages = useMessageStore((s) => s.loadMessages)

  useEffect(() => {
    const client = createApiClient('')
    loadMessages(client, conversationId)
  }, [conversationId, loadMessages])

  const subagentDataMap = useMemo(
    () => buildSubagentDataMap(messages ?? []),
    [messages],
  )

  return (
    <ScrollArea className="flex-1 p-4">
      <div className="space-y-4 max-w-2xl mx-auto">
        {(messages ?? []).map((msg) => (
          <div key={msg.id}>
            {msg.role === 'user' && (
              <UserMessage content={msg.content ?? ''} />
            )}
            {msg.role === 'assistant' && (
              <AssistantMessage
                message={msg}
                subagentDataMap={subagentDataMap}
                toolResultMap={toolResultMap}
              />
            )}
          </div>
        ))}

        {isStreaming && mainStream && (
          <AssistantMessage
            stream={mainStream}
            isStreaming
            statusPhase={statusPhase}
            subAgentStreams={
              Object.fromEntries(subAgentStreams)
            }
            toolResultMap={toolResultMap}
          />
        )}
      </div>
    </ScrollArea>
  )
}
```

- [ ] **Step 2: Refactor AssistantMessage to use ToolCallGroup, filter write_todos**

Replace `frontend/packages/web/components/chat/AssistantMessage.tsx` completely. Key changes:
- Delete `ToolCallList` function (lines 88-100)
- Import `ToolCallGroup` instead
- Add `toolResultMap` to props
- Filter `write_todos` blocks from `groupBlocks`
- Use `ToolCallGroup` for grouped tool call rendering
- Pass single tool calls to `ToolCallGroup` too (group of 1)

```tsx
'use client'

import { useState, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type {
  Message,
  ContentBlock,
  SubagentSummary,
  AgentStream,
} from '@cubeplex/core'
import { Bot, ChevronDown, ChevronRight, Brain } from 'lucide-react'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { SubAgentCard } from './SubAgentCard'
import { ToolCallGroup } from './ToolCallGroup'

interface ReasoningBlockProps {
  reasoning: string
  isStreaming: boolean
  startedAt?: number
  durationMs?: number
}

function formatDuration(ms: number): string {
  const seconds = Math.round(ms / 1000)
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const remainSeconds = seconds % 60
  return remainSeconds > 0
    ? `${minutes}分${remainSeconds}秒`
    : `${minutes}分`
}

function ReasoningBlock({
  reasoning,
  isStreaming,
  startedAt,
  durationMs,
}: ReasoningBlockProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const prevStreamingRef = useRef(isStreaming)

  useEffect(() => {
    if (isStreaming && !prevStreamingRef.current) {
      setIsOpen(true)
    } else if (!isStreaming && prevStreamingRef.current) {
      setIsOpen(false)
    }
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  useEffect(() => {
    if (isStreaming) setIsOpen(true)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!isStreaming || !startedAt) return
    const tick = () => setElapsed(Date.now() - startedAt)
    tick()
    const interval = setInterval(tick, 1000)
    return () => clearInterval(interval)
  }, [isStreaming, startedAt])

  const displayTime =
    durationMs ?? (isStreaming && startedAt ? elapsed : null)

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger
        className="flex items-center gap-1.5 text-xs
          text-muted-foreground hover:text-foreground
          transition-colors group"
      >
        <span className="text-muted-foreground/60
          group-hover:text-muted-foreground
          transition-colors">
          {isOpen ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
        </span>
        <Brain className="size-3 text-muted-foreground/70" />
        <span>
          {isStreaming ? '思考中...' : '思考过程'}
        </span>
        {displayTime != null && displayTime >= 1000 && (
          <span className="text-muted-foreground/50 ml-0.5">
            {formatDuration(displayTime)}
          </span>
        )}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-1.5">
        <div className="pl-4 border-l-2 border-border/50">
          <p className="text-xs text-muted-foreground/70
            leading-relaxed whitespace-pre-wrap italic">
            {reasoning}
          </p>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

interface HistoryProps {
  message: Message
  subagentDataMap?: Record<string, SubagentSummary>
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number }
  >
  stream?: never
  isStreaming?: never
  statusPhase?: never
  subAgentStreams?: never
}

interface StreamingProps {
  message?: never
  subagentDataMap?: never
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number }
  >
  stream: AgentStream
  isStreaming: true
  statusPhase?: string | null
  subAgentStreams?: Record<string, AgentStream>
}

type AssistantMessageProps = HistoryProps | StreamingProps

const proseClasses = `prose prose-sm dark:prose-invert max-w-none
  prose-p:leading-relaxed prose-p:my-1
  prose-headings:font-semibold prose-headings:mt-3
  prose-headings:mb-1 prose-headings:text-foreground
  prose-p:text-foreground prose-li:text-foreground
  prose-strong:text-foreground
  prose-code:text-foreground prose-code:text-[0.8em]
  prose-code:bg-muted prose-code:px-1 prose-code:py-0.5
  prose-code:rounded
  prose-code:before:content-none
  prose-code:after:content-none
  prose-pre:bg-muted prose-pre:border
  prose-pre:border-border prose-pre:rounded-lg
  prose-pre:text-[0.8em]
  prose-ul:my-1 prose-ol:my-1 prose-li:my-0
  prose-blockquote:border-l-primary/40
  prose-blockquote:text-muted-foreground
  prose-hr:border-border prose-a:text-primary
  prose-strong:font-semibold
  prose-table:text-foreground prose-th:text-foreground
  prose-td:text-foreground`

function blocksFromMessage(msg: Message): ContentBlock[] {
  const result: ContentBlock[] = []
  if (msg.reasoning) {
    result.push({ type: 'reasoning', content: msg.reasoning })
  }
  if (msg.tool_calls) {
    for (const tc of msg.tool_calls) {
      result.push({
        type: 'tool_call',
        name: tc.name,
        arguments: tc.arguments,
        tool_call_id: tc.tool_call_id ?? '',
      })
    }
  }
  if (msg.content) {
    result.push({ type: 'text', content: msg.content })
  }
  return result
}

function subagentSummaryToStream(
  summary: SubagentSummary,
): AgentStream {
  return {
    text: summary.text,
    toolCalls: summary.tool_calls.map((tc, i) => ({
      type: 'tool_call' as const,
      timestamp: '',
      data: {
        tool_call_id: `hist-${i}`,
        name: tc.name,
        arguments: tc.arguments,
      },
      agent_id: null,
      agent_name: null,
    })),
    toolResults: [],
    reasoning: summary.reasoning,
    blocks: [],
    name: null,
  }
}

function ContentBlockRenderer({
  block,
  isLast,
  isStreaming,
  toolResultMap,
  subAgentStreams,
  subagentDataMap,
}: {
  block: ContentBlock
  isLast: boolean
  isStreaming: boolean
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number }
  >
  subAgentStreams?: Record<string, AgentStream>
  subagentDataMap?: Record<string, SubagentSummary>
}) {
  if (block.type === 'reasoning') {
    return (
      <div className="bg-card border border-border
        rounded-xl px-3 py-2.5">
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
    const historicalStream =
      !stream && subagentDataMap?.[agentKey]
        ? subagentSummaryToStream(subagentDataMap[agentKey])
        : undefined
    const displayName =
      (block.arguments as { name?: string }).name
        ?? 'Subagent'
    return (
      <SubAgentCard
        name={displayName}
        stream={stream ?? historicalStream}
        isRunning={isStreaming && !!stream}
        toolResultMap={toolResultMap}
      />
    )
  }
  if (block.type === 'tool_call') {
    // Single non-grouped tool call — rendered as group of 1
    return (
      <ToolCallGroup
        blocks={[block as ContentBlock & { type: 'tool_call' }]}
        toolResultMap={toolResultMap}
        isStreaming={isStreaming}
      />
    )
  }
  return (
    <div className={proseClasses}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {block.content}
      </ReactMarkdown>
    </div>
  )
}

/**
 * Group consecutive non-subagent, non-write_todos
 * tool_call blocks for compact rendering.
 */
function groupBlocks(
  blocks: ContentBlock[],
): (ContentBlock | ContentBlock[])[] {
  const result: (ContentBlock | ContentBlock[])[] = []
  for (const block of blocks) {
    if (
      block.type === 'tool_call' &&
      block.name !== 'subagent' &&
      block.name !== 'write_todos'
    ) {
      const last = result[result.length - 1]
      if (
        Array.isArray(last) &&
        last[0].type === 'tool_call'
      ) {
        last.push(block)
      } else {
        result.push([block])
      }
    } else if (
      block.type === 'tool_call' &&
      block.name === 'write_todos'
    ) {
      // Filtered out — shown only in TaskProgressBar
      continue
    } else {
      result.push(block)
    }
  }
  return result
}

export function AssistantMessage({
  message,
  stream,
  isStreaming,
  statusPhase,
  subAgentStreams,
  subagentDataMap,
  toolResultMap,
}: AssistantMessageProps) {
  const blocks: ContentBlock[] = isStreaming
    ? stream.blocks
    : (message.blocks ?? blocksFromMessage(message))

  const hasContent = blocks.length > 0
  const grouped = groupBlocks(blocks)

  return (
    <div
      data-role="assistant"
      className="flex justify-start gap-2.5"
    >
      <div className="shrink-0 w-6 h-6 rounded-md border
        border-border bg-card flex items-center
        justify-center mt-0.5">
        <Bot className="size-3.5 text-primary/70" />
      </div>
      <div className="flex-1 max-w-[75%] space-y-2">
        {grouped.map((item, i) => {
          if (Array.isArray(item)) {
            const tcBlocks = item as (ContentBlock & {
              type: 'tool_call'
            })[]
            return (
              <ToolCallGroup
                key={i}
                blocks={tcBlocks}
                toolResultMap={toolResultMap}
                isStreaming={isStreaming === true}
              />
            )
          }
          return (
            <ContentBlockRenderer
              key={i}
              block={item}
              isLast={i === grouped.length - 1}
              isStreaming={isStreaming === true}
              toolResultMap={toolResultMap}
              subAgentStreams={subAgentStreams}
              subagentDataMap={subagentDataMap}
            />
          )
        })}
        {!hasContent && isStreaming && (
          <div
            data-testid="loading-indicator"
            className="flex items-center gap-1 pl-1"
          >
            {statusPhase === 'sandbox_creating' ? (
              <span className="text-xs text-muted-foreground
                animate-pulse">
                正在准备沙箱环境...
              </span>
            ) : statusPhase === 'sandbox_failed' ? (
              <span className="text-xs text-destructive">
                沙箱环境创建失败，将在无沙箱模式下继续
              </span>
            ) : (
              <>
                <span className="w-1.5 h-1.5 rounded-full
                  bg-primary animate-bounce
                  [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 rounded-full
                  bg-primary animate-bounce
                  [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 rounded-full
                  bg-primary animate-bounce
                  [animation-delay:300ms]" />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/web/components/chat/AssistantMessage.tsx \
       frontend/packages/web/components/chat/MessageList.tsx
git commit -m "feat: replace ToolCallList with ToolCallGroup, filter write_todos from chat"
```

---

## Task 16: Refactor SubAgentCard — Reuse ToolCallItem, Markdown output, refined animations

**Files:**
- Modify: `frontend/packages/web/components/chat/SubAgentCard.tsx`

- [ ] **Step 1: Rewrite SubAgentCard**

Replace `frontend/packages/web/components/chat/SubAgentCard.tsx`:

```tsx
'use client'

import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ChevronDown,
  ChevronRight,
  Bot,
  CheckCircle2,
} from 'lucide-react'
import type { AgentStream } from '@cubeplex/core'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { ToolCallItem } from './ToolCallItem'

interface Props {
  name: string
  stream?: AgentStream
  isRunning: boolean
  toolResultMap: Record<
    string,
    { content: string; receivedAt: number }
  >
}

const proseClasses = `prose prose-sm dark:prose-invert max-w-none
  prose-p:leading-relaxed prose-p:my-1
  prose-headings:font-semibold prose-headings:mt-3
  prose-headings:mb-1 prose-headings:text-foreground
  prose-p:text-foreground prose-li:text-foreground
  prose-strong:text-foreground
  prose-code:text-foreground prose-code:text-[0.8em]
  prose-code:bg-muted prose-code:px-1 prose-code:py-0.5
  prose-code:rounded
  prose-code:before:content-none
  prose-code:after:content-none
  prose-pre:bg-muted prose-pre:border
  prose-pre:border-border prose-pre:rounded-lg
  prose-pre:text-[0.8em]
  prose-ul:my-1 prose-ol:my-1 prose-li:my-0
  prose-a:text-primary`

export function SubAgentCard({
  name,
  stream,
  isRunning,
  toolResultMap,
}: Props) {
  const [open, setOpen] = useState(true)

  const hasContent =
    stream && (stream.toolCalls.length > 0 || stream.text)

  return (
    <div className="border border-border rounded-xl
      overflow-hidden bg-muted/10 border-l-2
      border-l-primary/40">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger
          className="flex w-full items-center gap-2 px-3
            py-2 text-sm text-muted-foreground
            hover:bg-muted/30 transition-colors"
        >
          {open ? (
            <ChevronDown className="size-3" />
          ) : (
            <ChevronRight className="size-3" />
          )}
          <Bot className="size-3.5" />
          <span className="font-medium text-foreground">
            {name}
          </span>
          {isRunning ? (
            <span className="ml-auto flex gap-0.5">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className="w-1 h-1 rounded-full
                    bg-muted-foreground animate-pulse"
                  style={{
                    animationDelay: `${i * 200}ms`,
                  }}
                />
              ))}
            </span>
          ) : hasContent ? (
            <CheckCircle2 className="ml-auto size-3.5
              text-emerald-500" />
          ) : null}
        </CollapsibleTrigger>

        <CollapsibleContent>
          {hasContent && (
            <div className="px-1 pb-2 space-y-1">
              {stream.toolCalls.map((tc, i) => {
                const result =
                  toolResultMap[tc.data.tool_call_id] ?? null
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
              {stream.text && (
                <div className={`px-3 pt-1 ${proseClasses}`}>
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                  >
                    {stream.text}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          )}
          {!hasContent && isRunning && (
            <div className="px-3 pb-3 pt-1">
              <span className="text-xs text-muted-foreground
                animate-pulse">
                正在执行...
              </span>
            </div>
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
```

- [ ] **Step 2: Run type check**

Run: `cd frontend && pnpm type-check`
Expected: Pass.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/SubAgentCard.tsx
git commit -m "feat: enhance SubAgentCard with ToolCallItem reuse, Markdown output, refined animations"
```

---

## Task 17: Build verification and visual smoke test

**Files:** None (verification only)

- [ ] **Step 1: Run full type check**

Run: `cd frontend && pnpm type-check`
Expected: All packages pass with no errors.

- [ ] **Step 2: Run build**

Run: `cd frontend && pnpm build`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Run backend checks**

Run: `cd backend && make check`
Expected: All format, lint, type-check, test pass.

- [ ] **Step 4: Visual smoke test**

Start the dev server:
```bash
cd frontend && pnpm dev
```

Open `http://localhost:3000` in browser and verify:
1. Chat page loads normally
2. Send a message — tool calls render with new ToolCallItem design (icon + name + param summary)
3. Click a tool call — it expands to show result inline
4. Click "View in panel" — right panel opens with split-screen layout
5. Drag the resize handle — panels resize properly
6. Close panel via X button — returns to single-column
7. If agent uses `write_todos` — TaskProgressBar appears above InputBar
8. SubAgent cards show ToolCallItem components internally
9. Light/dark theme toggle — all components adapt correctly

- [ ] **Step 5: Fix any issues found**

Address type errors, layout bugs, or visual issues discovered during smoke testing.

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix: address smoke test issues in chat UI redesign"
```

---

## Task 18: E2E test for new UI components

**Files:**
- Modify or create: `frontend/packages/web/tests/e2e/chat-ui.spec.ts` (or equivalent test file location)

- [ ] **Step 1: Check existing E2E test structure**

```bash
ls frontend/packages/web/tests/ 2>/dev/null || ls frontend/tests/ 2>/dev/null || echo "Check test locations"
find frontend -name "*.spec.ts" -o -name "*.test.ts" | head -20
```

Understand the existing test patterns and Playwright config.

- [ ] **Step 2: Write E2E test for tool call interaction**

Create or extend an E2E test that covers:
1. Send a message that triggers tool calls
2. Verify tool call items render with icon + name (not raw JSON)
3. Click a tool call item to expand it
4. Verify expanded state shows result content
5. Click "View in panel" if visible
6. Verify right panel opens

The exact test code depends on the existing Playwright setup and test patterns found in step 1. Follow the conventions already in place.

- [ ] **Step 3: Run E2E tests**

```bash
cd frontend && pnpm test:e2e
```

Expected: New tests pass (may need a running backend or mocked API).

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "test: add E2E tests for chat UI redesign components"
```
