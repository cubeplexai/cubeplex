# Frontend Citation System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display inline citation references in agent responses with hover cards and side-panel highlighting, sourced from backend `citation` SSE events.

**Architecture:** Citations flow through a new `citationStore` (Zustand). During streaming, `citation` SSE events populate the store. For history, citation data is embedded in tool messages via `additional_kwargs["citations"]`. The text renderer parses `【N-M】` markers and replaces them with `CitationMarker` components that read from the store. Clicking a marker opens the existing ToolDetailPanel with the cited chunk highlighted.

**Tech Stack:** React 19, TypeScript, Zustand, shadcn Popover (existing `@base-ui/react`), ReactMarkdown, Tailwind CSS 4

**Spec:** `frontend/docs/superpowers/specs/2026-04-10-frontend-citation-design.md`

---

## File Structure

### New Files

| File                                     | Responsibility                                                                       |
| ---------------------------------------- | ------------------------------------------------------------------------------------ |
| `core/src/types/citation.ts`             | `CitationChunk`, `CitationMetadata`, `CitationData`, `CitationEvent` types           |
| `core/src/stores/citationStore.ts`       | Zustand store: add/load/get citations by conversationId + citationId                 |
| `web/components/chat/CitationMarker.tsx` | Inline pill component (`3.0` format) + HoverCard popover                             |
| `web/lib/citations.tsx`                  | `renderWithCitations()` — parse `【N-M】` in React children, insert `CitationMarker` |

### Modified Files

| File                                                 | Change                                                                     |
| ---------------------------------------------------- | -------------------------------------------------------------------------- |
| `core/src/types/events.ts`                           | Add `'citation'` to `AgentEventType`                                       |
| `core/src/types/message.ts`                          | Add `citations?: CitationData[]` to `Message`                              |
| `core/src/types/index.ts`                            | Re-export from `citation.ts`                                               |
| `core/src/stores/index.ts`                           | Export `citationStore`                                                     |
| `core/src/stores/messageStore.ts`                    | Handle `citation` event in `send()`; restore citations in `loadMessages()` |
| `web/components/chat/AssistantMessage.tsx`           | Use `renderWithCitations()` in text block rendering                        |
| `core/src/stores/panelStore.ts`                      | Add optional `highlightText` to tool view; add to `openTool`               |
| `core/src/stores/toolDetailStore.ts`                 | Pass `highlightText` through facade                                        |
| `web/hooks/useToolDetail.ts`                         | Expose `highlightText`                                                     |
| `web/components/panel/ToolDetailPanel.tsx`           | Pass `highlightText` to view components                                    |
| `web/components/panel/SearchResultView.tsx`          | Accept `highlightText`, scroll + highlight matching item                   |
| `web/components/panel/WebFetchView.tsx`              | Accept `highlightText`, scroll + highlight matching text                   |
| `web/components/panel/GenericToolView.tsx`           | Accept `highlightText`, scroll + highlight matching text                   |
| `backend/cubeplex/middleware/citations/middleware.py` | Store `citations` list in `additional_kwargs`                              |
| `backend/cubeplex/agents/convert.py`                  | Extract `citations` from ToolMessage for API response                      |

---

## Task 1: Citation Types

**Files:**

- Create: `frontend/packages/core/src/types/citation.ts`
- Modify: `frontend/packages/core/src/types/events.ts`
- Modify: `frontend/packages/core/src/types/message.ts`
- Modify: `frontend/packages/core/src/types/index.ts`

- [ ] **Step 1: Create citation type definitions**

Create `frontend/packages/core/src/types/citation.ts`:

```ts
export interface CitationChunk {
  chunk_index: number
  content: string
}

export interface CitationMetadata {
  source_type: string
  url?: string
  title?: string
  domain?: string
  published_at?: string
}

export interface CitationData {
  citation_id: number
  chunks: CitationChunk[]
  metadata: CitationMetadata
  tool_call_id: string
}
```

- [ ] **Step 2: Add `'citation'` to `AgentEventType` and add `CitationEvent`**

In `frontend/packages/core/src/types/events.ts`, add `'citation'` to the `AgentEventType` union:

```ts
export type AgentEventType =
  | 'text_delta'
  | 'reasoning'
  | 'tool_call'
  | 'tool_call_delta'
  | 'tool_result'
  | 'artifact'
  | 'error'
  | 'done'
  | 'status'
  | 'citation' // NEW
```

Add the event interface at the bottom (before the closing of the file), importing `CitationData`:

```ts
import type { CitationData } from './citation'

export interface CitationEvent extends AgentEvent {
  type: 'citation'
  data: CitationData & Record<string, unknown>
}
```

- [ ] **Step 3: Add `citations` field to `Message`**

In `frontend/packages/core/src/types/message.ts`, add import and field:

```ts
import type { CitationData } from './citation'

export interface Message {
  // ... existing fields ...
  citations?: CitationData[] | null // for tool messages: citation data from this tool result
}
```

- [ ] **Step 4: Re-export citation types**

In `frontend/packages/core/src/types/index.ts`, add:

```ts
export type * from './citation'
```

- [ ] **Step 5: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS (no errors related to new types — they're unused so far but valid)

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/core/src/types/
git commit -m "feat(citations): add citation type definitions"
```

---

## Task 2: Citation Store

**Files:**

- Create: `frontend/packages/core/src/stores/citationStore.ts`
- Modify: `frontend/packages/core/src/stores/index.ts`

- [ ] **Step 1: Create citation store**

Create `frontend/packages/core/src/stores/citationStore.ts`:

```ts
import { create } from 'zustand'
import type { CitationData } from '../types'

export interface CitationStore {
  /** conversationId → citationId → CitationData */
  citations: Record<string, Record<number, CitationData>>

  addCitation: (conversationId: string, data: CitationData) => void
  loadCitations: (conversationId: string, citations: CitationData[]) => void
  getCitation: (conversationId: string, citationId: number) => CitationData | undefined
  clearConversation: (conversationId: string) => void
}

export const useCitationStore = create<CitationStore>((set, get) => ({
  citations: {},

  addCitation(conversationId, data) {
    set((s) => ({
      citations: {
        ...s.citations,
        [conversationId]: {
          ...s.citations[conversationId],
          [data.citation_id]: data,
        },
      },
    }))
  },

  loadCitations(conversationId, citations) {
    const map: Record<number, CitationData> = {}
    for (const c of citations) {
      map[c.citation_id] = c
    }
    set((s) => ({
      citations: {
        ...s.citations,
        [conversationId]: {
          ...s.citations[conversationId],
          ...map,
        },
      },
    }))
  },

  getCitation(conversationId, citationId) {
    return get().citations[conversationId]?.[citationId]
  },

  clearConversation(conversationId) {
    set((s) => {
      const { [conversationId]: _, ...rest } = s.citations
      return { citations: rest }
    })
  },
}))
```

- [ ] **Step 2: Export from stores index**

In `frontend/packages/core/src/stores/index.ts`, add:

```ts
export { useCitationStore, type CitationStore } from './citationStore'
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/stores/citationStore.ts frontend/packages/core/src/stores/index.ts
git commit -m "feat(citations): add citation Zustand store"
```

---

## Task 3: Backend — Persist Citations in Checkpoint

**Files:**

- Modify: `backend/cubeplex/middleware/citations/middleware.py`
- Modify: `backend/cubeplex/agents/convert.py`

- [ ] **Step 1: Store citations in `additional_kwargs`**

In `backend/cubeplex/middleware/citations/middleware.py`, the `awrap_tool_call` method currently builds `citation_data` dicts and pushes them to the queue. Collect them in a list and also store in `additional_kwargs`.

After line 76 (`chunks_for_llm: list[str] = []`), add:

```python
        all_citations: list[dict[str, Any]] = []
```

After the line `await queue.put(("citation", None, citation_data))` (line 96), add:

```python
            all_citations.append(citation_data)
```

Change the block at lines 101-103 from:

```python
        if chunks_for_llm:
            result.additional_kwargs["original_content"] = raw_content
            result.content = "\n\n".join(chunks_for_llm)
```

To:

```python
        if chunks_for_llm:
            result.additional_kwargs["original_content"] = raw_content
            result.additional_kwargs["citations"] = all_citations
            result.content = "\n\n".join(chunks_for_llm)
```

- [ ] **Step 2: Extract citations in `convert.py`**

In `backend/cubeplex/agents/convert.py`, in the `elif isinstance(msg, ToolMessage):` block (around line 172), extract citations from `additional_kwargs` and add to the API response.

Change the ToolMessage result dict (lines 178-191) to include citations:

```python
        elif isinstance(msg, ToolMessage):
            raw_events = (msg.additional_kwargs or {}).get("subagent_events")
            subagent_events = _consolidate_subagent_events(raw_events) if raw_events else None
            citations = (msg.additional_kwargs or {}).get("citations")
            ts = _get_timestamp(msg)
            # Unwrap MCP content blocks: list[{"type": "text", "text": "..."}] -> text
            tool_content = _unwrap_mcp_content(msg.content)
            result.append(
                {
                    "id": getattr(msg, "id", None) or str(uuid.uuid4()),
                    "role": "tool",
                    "content": tool_content,
                    "tool_calls": None,
                    "reasoning": None,
                    "name": msg.name,
                    "tool_call_id": getattr(msg, "tool_call_id", None),
                    "started_at": (msg.response_metadata or {}).get("tool_started_at"),
                    "subagent_events": subagent_events,
                    "citations": citations,
                    "created_at": ts,
                }
            )
```

- [ ] **Step 3: Run backend checks**

Run: `cd backend && make check`
Expected: format, lint, type-check, tests all PASS

- [ ] **Step 4: Commit**

```bash
git add backend/cubeplex/middleware/citations/middleware.py backend/cubeplex/agents/convert.py
git commit -m "feat(citations): persist citation data in checkpoint and API response"
```

---

## Task 4: Wire Citation SSE Events into Message Store

**Files:**

- Modify: `frontend/packages/core/src/stores/messageStore.ts`

- [ ] **Step 1: Add `citation` event handling in `send()`**

In `frontend/packages/core/src/stores/messageStore.ts`, add import at top:

```ts
import type { CitationData } from '../types'
```

In the `send()` method's event processing loop, add a handler for `citation` events. After the `} else if (event.type === 'artifact') {` block (around line 388) and before the `} else if (event.type === 'status') {` block, add:

```ts
        } else if (event.type === 'citation') {
          const citationData = event.data as unknown as CitationData
          const { useCitationStore } = await import('./citationStore')
          useCitationStore.getState().addCitation(conversationId, citationData)
```

Note: uses dynamic import to match the existing pattern for `artifactStore` (line 383).

- [ ] **Step 2: Restore citations from history in `loadMessages()`**

In the `loadMessages()` method, after the todo restoration loop (after line 186 `let restoredTodos: TodoItem[] = []` block ends around line 196), add citation restoration:

```ts
// Restore citations from tool messages in history
const { useCitationStore } = await import('./citationStore')
for (const msg of messages) {
  if (msg.role === 'tool' && msg.citations?.length) {
    useCitationStore.getState().loadCitations(conversationId, msg.citations)
  }
}
```

Place this before the `set()` call at line 188.

- [ ] **Step 3: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/packages/core/src/stores/messageStore.ts
git commit -m "feat(citations): handle citation SSE events and restore from history"
```

---

## Task 5: Citation Text Parser (`renderWithCitations`)

**Files:**

- Create: `frontend/packages/web/lib/citations.tsx`

- [ ] **Step 1: Create the citation text parser**

Create `frontend/packages/web/lib/citations.tsx`:

```tsx
import React, { type ReactNode } from 'react'

/** Regex matching 【N-M】 citation markers in text */
const CITATION_RE = /【(\d+)-(\d+)】/g

export interface CitationRef {
  citationId: number
  chunkIndex: number
}

/**
 * Parse a string for 【N-M】 markers and return an array of alternating
 * text strings and CitationRef objects.
 */
export function parseCitationMarkers(text: string): (string | CitationRef)[] {
  const parts: (string | CitationRef)[] = []
  let lastIndex = 0

  for (const match of text.matchAll(CITATION_RE)) {
    const before = text.slice(lastIndex, match.index)
    if (before) parts.push(before)
    parts.push({
      citationId: parseInt(match[1], 10),
      chunkIndex: parseInt(match[2], 10),
    })
    lastIndex = match.index + match[0].length
  }

  const tail = text.slice(lastIndex)
  if (tail) parts.push(tail)
  return parts
}

/**
 * Walk React children, find string nodes containing 【N-M】, and replace
 * markers with CitationMarker components. Non-string children pass through.
 *
 * @param children - React children from a markdown element (p, li, etc.)
 * @param conversationId - current conversation ID for store lookups
 * @param MarkerComponent - the CitationMarker component to render
 */
export function renderWithCitations(
  children: ReactNode,
  conversationId: string,
  MarkerComponent: React.ComponentType<{
    citationId: number
    chunkIndex: number
    conversationId: string
  }>,
): ReactNode {
  return React.Children.map(children, (child) => {
    if (typeof child === 'string') {
      const parts = parseCitationMarkers(child)
      if (parts.length === 1 && typeof parts[0] === 'string') {
        return child // no markers found, return original string
      }
      return parts.map((part, i) => {
        if (typeof part === 'string') return part
        return (
          <MarkerComponent
            key={`cite-${part.citationId}-${part.chunkIndex}-${i}`}
            citationId={part.citationId}
            chunkIndex={part.chunkIndex}
            conversationId={conversationId}
          />
        )
      })
    }
    // Recursively handle nested elements (e.g., <strong>, <em> inside <p>)
    if (React.isValidElement(child) && child.props.children) {
      return React.cloneElement(
        child,
        undefined,
        renderWithCitations(child.props.children, conversationId, MarkerComponent),
      )
    }
    return child
  })
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/lib/citations.tsx
git commit -m "feat(citations): add citation marker parser and renderWithCitations utility"
```

---

## Task 6: CitationMarker Component with Hover Card

**Files:**

- Create: `frontend/packages/web/components/chat/CitationMarker.tsx`

- [ ] **Step 1: Create the CitationMarker component**

Create `frontend/packages/web/components/chat/CitationMarker.tsx`:

```tsx
'use client'

import { useState, useCallback } from 'react'
import { Globe, ExternalLink, Calendar } from 'lucide-react'
import { useCitationStore, usePanelStore } from '@cubeplex/core'
import type { CitationData } from '@cubeplex/core'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'

interface CitationMarkerProps {
  citationId: number
  chunkIndex: number
  conversationId: string
}

function getFaviconUrl(domain: string): string {
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=16`
}

function CitationHoverContent({
  citation,
  chunkIndex,
  onOpenPanel,
}: {
  citation: CitationData
  chunkIndex: number
  onOpenPanel: () => void
}) {
  const { metadata, chunks } = citation
  const chunk = chunks.find((c) => c.chunk_index === chunkIndex)
  const [faviconError, setFaviconError] = useState(false)
  const isWeb = metadata.source_type === 'web'

  return (
    <div className="flex flex-col gap-2">
      {/* Header: favicon + domain + source type */}
      <div className="flex items-center gap-1.5">
        {isWeb && metadata.domain && !faviconError ? (
          <img
            src={getFaviconUrl(metadata.domain)}
            alt=""
            className="size-4 rounded-sm shrink-0"
            onError={() => setFaviconError(true)}
          />
        ) : (
          <Globe className="size-4 text-muted-foreground shrink-0" />
        )}
        <span className="text-xs text-muted-foreground truncate">
          {metadata.domain || metadata.source_type}
        </span>
        <span
          className="ml-auto text-[10px] font-medium text-muted-foreground/60
          bg-muted px-1.5 py-0.5 rounded shrink-0"
        >
          {metadata.source_type}
        </span>
      </div>

      {/* Title */}
      {metadata.title && (
        <button
          type="button"
          onClick={onOpenPanel}
          className="text-sm font-medium text-foreground hover:text-primary
            transition-colors text-left line-clamp-2 cursor-pointer"
        >
          {metadata.title}
        </button>
      )}

      {/* Chunk snippet */}
      {chunk && (
        <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">
          {chunk.content}
        </p>
      )}

      {/* Footer: date + URL */}
      <div className="flex items-center gap-2 text-[10px] text-muted-foreground/60">
        {metadata.published_at && (
          <span className="flex items-center gap-1">
            <Calendar className="size-2.5" />
            {metadata.published_at}
          </span>
        )}
        {isWeb && metadata.url && (
          <a
            href={metadata.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-primary hover:underline truncate ml-auto"
          >
            <ExternalLink className="size-2.5 shrink-0" />
            <span className="truncate">{metadata.url}</span>
          </a>
        )}
      </div>
    </div>
  )
}

export function CitationMarker({ citationId, chunkIndex, conversationId }: CitationMarkerProps) {
  const citation = useCitationStore((s) => s.citations[conversationId]?.[citationId])
  const openTool = usePanelStore((s) => s.openTool)

  const handleOpenPanel = useCallback(() => {
    if (!citation) return
    const chunk = citation.chunks.find((c) => c.chunk_index === chunkIndex)
    // Find the tool result from the message store to populate the panel
    const { useMessageStore } = require('@cubeplex/core')
    const toolResultMap = useMessageStore.getState().toolResultMap
    const result = toolResultMap[citation.tool_call_id]

    if (result) {
      openTool(
        result.contentType === 'json' ? 'web_search' : 'web_fetch',
        {},
        result.content,
        result.contentType,
        undefined,
        chunk?.content ?? undefined,
      )
    }
  }, [citation, chunkIndex, openTool])

  // No citation data available — render marker as plain text
  if (!citation) {
    return (
      <span className="text-muted-foreground/50 text-xs">
        【{citationId}-{chunkIndex}】
      </span>
    )
  }

  return (
    <Popover openOnHover>
      <PopoverTrigger
        onClick={handleOpenPanel}
        className="inline-flex items-center justify-center min-w-[1.5em] h-[1.2em]
          px-1 mx-0.5 text-[10px] font-mono font-medium leading-none
          bg-primary/10 text-primary hover:bg-primary/20 rounded-full
          cursor-pointer transition-colors align-super relative -top-[1px]"
      >
        {citationId}.{chunkIndex}
      </PopoverTrigger>
      <PopoverContent side="top" sideOffset={4} className="w-80 p-3">
        <CitationHoverContent
          citation={citation}
          chunkIndex={chunkIndex}
          onOpenPanel={handleOpenPanel}
        />
      </PopoverContent>
    </Popover>
  )
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: May fail because `openTool` doesn't accept `highlightText` yet — that's fine, we'll fix in Task 8.

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/CitationMarker.tsx
git commit -m "feat(citations): add CitationMarker component with hover card"
```

---

## Task 7: Integrate Citations into Text Block Rendering

**Files:**

- Modify: `frontend/packages/web/components/chat/AssistantMessage.tsx`

- [ ] **Step 1: Add citation rendering to text blocks**

In `frontend/packages/web/components/chat/AssistantMessage.tsx`:

Add imports at the top:

```ts
import { renderWithCitations } from '@/lib/citations'
import { CitationMarker } from './CitationMarker'
import { useConversationStore } from '@cubeplex/core'
```

In the `ContentBlockRenderer` function, find the text block rendering (around line 328):

```tsx
if (block.type === 'text') {
  return (
    <div className={proseClasses}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content}</ReactMarkdown>
    </div>
  )
}
```

Replace it with:

```tsx
if (block.type === 'text') {
  const hasCitations = /【\d+-\d+】/.test(block.content)
  if (!hasCitations) {
    return (
      <div className={proseClasses}>
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.content}</ReactMarkdown>
      </div>
    )
  }
  return (
    <div className={proseClasses}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <p>{renderWithCitations(children, conversationId, CitationMarker)}</p>
          ),
          li: ({ children }) => (
            <li>{renderWithCitations(children, conversationId, CitationMarker)}</li>
          ),
          td: ({ children }) => (
            <td>{renderWithCitations(children, conversationId, CitationMarker)}</td>
          ),
        }}
      >
        {block.content}
      </ReactMarkdown>
    </div>
  )
}
```

The `ContentBlockRenderer` function needs access to `conversationId`. Add it as a prop:

In the function signature (around line 199), add `conversationId: string` to the props:

```tsx
function ContentBlockRenderer(
  { block, index, isLast, isStreaming, subAgentStreams, subagentDataMap, toolResultMap,
    messageCreatedAt, subagentIndex, agentId, conversationId }: {
    block: ContentBlock; index: number; isLast: boolean; isStreaming: boolean
    subAgentStreams?: Record<string, AgentStream>
    subagentDataMap?: Record<string, SubagentSummary>
    toolResultMap: Record<string, { content: string; receivedAt: number }>
    messageCreatedAt?: string
    subagentIndex?: number
    agentId?: string | null
    conversationId: string
  },
) {
```

In `AssistantMessage`, pass `conversationId` to `ContentBlockRenderer`. The component needs the active conversation ID. Add a prop or derive it:

In the `AssistantMessage` function, get the active conversation ID from the conversation store:

```tsx
export function AssistantMessage(
  { message, stream, isStreaming, statusPhase, subAgentStreams, subagentDataMap, toolResultMap }:
  AssistantMessageProps,
) {
  const activeConversationId = useConversationStore((s) => s.activeId) ?? ''
```

Then pass it to `ContentBlockRenderer`:

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
  agentId={streamAgentId}
  conversationId={activeConversationId}
/>
```

Also pass it in the `ToolCallGroup` branches — but `ToolCallGroup` doesn't need it, only `ContentBlockRenderer` does. Make sure all `ContentBlockRenderer` calls include `conversationId={activeConversationId}`.

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS (or minor issues to fix inline)

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/AssistantMessage.tsx
git commit -m "feat(citations): integrate citation markers into text block rendering"
```

---

## Task 8: Panel Store — Add `highlightText` Support

**Files:**

- Modify: `frontend/packages/core/src/stores/panelStore.ts`
- Modify: `frontend/packages/core/src/stores/toolDetailStore.ts`
- Modify: `frontend/packages/web/hooks/useToolDetail.ts`

- [ ] **Step 1: Add `highlightText` to panel store**

In `frontend/packages/core/src/stores/panelStore.ts`:

Add `highlightText` to the tool view type (around line 32):

```ts
  | {
      type: 'tool'
      toolName: string
      toolArgs: Record<string, unknown>
      toolResult: string | null
      contentType: PanelContentType
      toolRef: ToolCallRef | null
      highlightText: string | null
    }
```

Update the `openTool` signature in `PanelStore` (around line 48):

```ts
  openTool: (
    toolName: string,
    toolArgs: Record<string, unknown>,
    toolResult: string | null,
    contentType?: string,
    toolRef?: ToolCallRef,
    highlightText?: string,
  ) => void
```

Update the `openTool` implementation (around line 62):

```ts
  openTool: (toolName, toolArgs, toolResult, contentType, toolRef, highlightText) =>
    set({
      view: {
        type: 'tool',
        toolName,
        toolArgs,
        toolResult,
        contentType: mapContentType(toolName, contentType),
        toolRef: toolRef ?? null,
        highlightText: highlightText ?? null,
      },
    }),
```

- [ ] **Step 2: Update toolDetailStore facade**

In `frontend/packages/core/src/stores/toolDetailStore.ts`:

Add `highlightText` to `ToolDetailStore` interface:

```ts
export interface ToolDetailStore {
  isOpen: boolean
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  contentType: PanelContentType
  toolRef: ToolCallRef | null
  highlightText: string | null

  open: (
    toolName: string,
    toolArgs: Record<string, unknown>,
    toolResult: string | null,
    contentType?: string,
    toolRef?: ToolCallRef,
    highlightText?: string,
  ) => void
  close: () => void
}
```

In the facade builder (around line 28), add `highlightText`:

```ts
const facade: ToolDetailStore =
  v.type === 'tool'
    ? {
        isOpen: true,
        toolName: v.toolName,
        toolArgs: v.toolArgs,
        toolResult: v.toolResult,
        contentType: v.contentType,
        toolRef: v.toolRef,
        highlightText: v.highlightText,
        open: panel.openTool,
        close: panel.close,
      }
    : {
        isOpen: false,
        toolName: '',
        toolArgs: {},
        toolResult: null,
        contentType: 'generic',
        toolRef: null,
        highlightText: null,
        open: panel.openTool,
        close: panel.close,
      }
```

Same for the `getState()` version (around line 54):

```ts
if (v.type === 'tool') {
  return {
    isOpen: true,
    toolName: v.toolName,
    toolArgs: v.toolArgs,
    toolResult: v.toolResult,
    contentType: v.contentType,
    toolRef: v.toolRef,
    highlightText: v.highlightText,
    open: panel.openTool,
    close: panel.close,
  }
}
return {
  isOpen: false,
  toolName: '',
  toolArgs: {},
  toolResult: null,
  contentType: 'generic',
  toolRef: null,
  highlightText: null,
  open: panel.openTool,
  close: panel.close,
}
```

- [ ] **Step 3: Update `useToolDetail` hook**

In `frontend/packages/web/hooks/useToolDetail.ts`, add:

```ts
const highlightText = useToolDetailStore((s) => s.highlightText)
```

And include in the return:

```ts
return {
  isOpen,
  toolName,
  toolArgs,
  toolResult,
  contentType,
  toolRef,
  highlightText,
  open,
  close,
}
```

- [ ] **Step 4: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/packages/core/src/stores/panelStore.ts frontend/packages/core/src/stores/toolDetailStore.ts frontend/packages/web/hooks/useToolDetail.ts
git commit -m "feat(citations): add highlightText support to panel store"
```

---

## Task 9: Panel Views — Pass and Render Highlight

**Files:**

- Modify: `frontend/packages/web/components/panel/ToolDetailPanel.tsx`
- Modify: `frontend/packages/web/components/panel/SearchResultView.tsx`
- Modify: `frontend/packages/web/components/panel/WebFetchView.tsx`
- Modify: `frontend/packages/web/components/panel/GenericToolView.tsx`

- [ ] **Step 1: Pass `highlightText` through ToolDetailPanel**

In `frontend/packages/web/components/panel/ToolDetailPanel.tsx`, destructure `highlightText` from `useToolDetail()`:

```tsx
const { toolName, toolArgs, toolResult, contentType, toolRef, highlightText, close } =
  useToolDetail()
```

Pass it to the view components that support it:

```tsx
{
  contentType === 'search' && (
    <SearchResultView result={toolResult} args={toolArgs} highlightText={highlightText} />
  )
}
{
  contentType === 'web_fetch' && (
    <WebFetchView args={toolArgs} result={toolResult} highlightText={highlightText} />
  )
}
{
  ;(contentType === 'generic' || contentType === 'code_execute' || contentType === 'artifact') && (
    <GenericToolView args={toolArgs} result={toolResult} highlightText={highlightText} />
  )
}
```

- [ ] **Step 2: Add highlight to SearchResultView**

In `frontend/packages/web/components/panel/SearchResultView.tsx`:

Add `highlightText` prop:

```tsx
interface SearchResultViewProps {
  result: string | null
  args?: Record<string, unknown>
  highlightText?: string | null
}
```

Add `useEffect` and `useRef` imports. In the component, add scroll-to-highlight logic:

```tsx
import { useEffect, useRef } from 'react'

export function SearchResultView({
  result,
  args,
  highlightText,
}: SearchResultViewProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Scroll to highlighted result when highlightText changes
  useEffect(() => {
    if (!highlightText || !containerRef.current) return
    const items = containerRef.current.querySelectorAll('[data-result-item]')
    for (const item of items) {
      if (item.textContent?.includes(highlightText.slice(0, 50))) {
        item.classList.add('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
        item.scrollIntoView({ behavior: 'smooth', block: 'center' })
        // Fade out after 2s
        setTimeout(() => {
          item.classList.remove('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
        }, 2000)
        break
      }
    }
  }, [highlightText])
```

Add `ref={containerRef}` to the outer container div (the one wrapping the results list):

```tsx
      <div ref={containerRef} className="p-3 space-y-1.5">
```

Add `data-result-item` attribute to each result item:

```tsx
          <a
            key={i}
            data-result-item
            href={item.url}
```

- [ ] **Step 3: Add highlight to WebFetchView**

In `frontend/packages/web/components/panel/WebFetchView.tsx`:

Add prop and highlight logic:

```tsx
import { useEffect, useRef } from 'react'

interface WebFetchViewProps {
  args: Record<string, unknown>
  result: string | null
  highlightText?: string | null
}

export function WebFetchView({ args, result, highlightText }: WebFetchViewProps) {
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!highlightText || !contentRef.current) return
    // Use TreeWalker to find text nodes containing the highlight text
    const walker = document.createTreeWalker(contentRef.current, NodeFilter.SHOW_TEXT)
    const searchText = highlightText.slice(0, 50)
    while (walker.nextNode()) {
      const node = walker.currentNode
      if (node.textContent?.includes(searchText)) {
        const parent = node.parentElement
        if (parent) {
          parent.classList.add('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
          parent.scrollIntoView({ behavior: 'smooth', block: 'center' })
          setTimeout(() => {
            parent.classList.remove('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
          }, 2000)
        }
        break
      }
    }
  }, [highlightText])

  const url = String(args.url ?? '')

  return (
    <div className="p-4 space-y-3">
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-primary hover:underline break-all"
        >
          {url}
        </a>
      )}
      {result && (
        <div ref={contentRef} className={proseClasses}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{result}</ReactMarkdown>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Add highlight to GenericToolView**

In `frontend/packages/web/components/panel/GenericToolView.tsx`:

Add prop and highlight logic:

```tsx
interface GenericToolViewProps {
  args: Record<string, unknown>
  result: string | null
  highlightText?: string | null
}

export function GenericToolView({
  args,
  result,
  highlightText,
}: GenericToolViewProps) {
  const responseRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (!highlightText || !responseRef.current) return
    const text = responseRef.current.textContent ?? ''
    const searchText = highlightText.slice(0, 50)
    if (text.includes(searchText)) {
      responseRef.current.classList.add('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
      responseRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setTimeout(() => {
        responseRef.current?.classList.remove('ring-2', 'ring-yellow-400/50', 'bg-yellow-50/10')
      }, 2000)
    }
  }, [highlightText])
```

Add `import { useState, useEffect, useRef } from 'react'` at top, and `ref={responseRef}` to the response `<pre>`:

```tsx
<pre ref={responseRef} className="font-mono text-sm text-foreground whitespace-pre-wrap break-all">
  {responseText}
</pre>
```

- [ ] **Step 5: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/packages/web/components/panel/ToolDetailPanel.tsx frontend/packages/web/components/panel/SearchResultView.tsx frontend/packages/web/components/panel/WebFetchView.tsx frontend/packages/web/components/panel/GenericToolView.tsx
git commit -m "feat(citations): add highlight text support to panel views"
```

---

## Task 10: Fix CitationMarker Panel Integration

**Files:**

- Modify: `frontend/packages/web/components/chat/CitationMarker.tsx`

Now that `openTool` accepts `highlightText`, update the `CitationMarker` component to use the proper import pattern (avoid `require`) and pass `highlightText`.

- [ ] **Step 1: Update handleOpenPanel to use proper imports**

Replace the `handleOpenPanel` callback in `CitationMarker.tsx`:

```tsx
const handleOpenPanel = useCallback(() => {
  if (!citation) return
  const chunk = citation.chunks.find((c) => c.chunk_index === chunkIndex)
  const toolResultMap = useMessageStore.getState().toolResultMap
  const result = toolResultMap[citation.tool_call_id]

  if (result) {
    openTool(
      'web_search', // toolName — panel will route based on contentType
      {},
      result.content,
      result.contentType,
      undefined,
      chunk?.content,
    )
  }
}, [citation, chunkIndex, openTool])
```

Add import at top:

```ts
import { useCitationStore, usePanelStore, useMessageStore } from '@cubeplex/core'
```

(Replace the separate `useCitationStore` and `usePanelStore` imports with a combined one, and add `useMessageStore`.)

- [ ] **Step 2: Type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add frontend/packages/web/components/chat/CitationMarker.tsx
git commit -m "feat(citations): wire CitationMarker panel opening with highlightText"
```

---

## Task 11: Manual Integration Test

- [ ] **Step 1: Build frontend**

Run: `cd frontend && pnpm build`
Expected: Build succeeds with no errors

- [ ] **Step 2: Run type-check**

Run: `cd frontend && pnpm type-check`
Expected: PASS

- [ ] **Step 3: Run backend checks**

Run: `cd backend && make check`
Expected: All checks pass

- [ ] **Step 4: Manual test with dev server**

Start both servers:

```bash
cd backend && python main.py &
cd frontend && pnpm dev &
```

Test scenarios:

1. Send a message that triggers a web_search tool (e.g., "搜索一下最近的AI新闻")
2. Verify `【N-M】` markers in the response text are replaced with clickable pills showing `N.M`
3. Hover over a pill — verify the popover card appears with title, domain, favicon, snippet
4. Click the pill — verify the side panel opens with the search results, and the cited item is highlighted
5. Reload the page — verify citation pills still render correctly from history (citations loaded from tool message metadata)

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix(citations): integration test fixes"
```
