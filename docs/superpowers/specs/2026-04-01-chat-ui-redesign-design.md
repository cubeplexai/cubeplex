# Chat UI Redesign — Design Spec

## Overview

Redesign the cubebox frontend chat interface to match the emerging dominant UI pattern for AI agent applications (split-screen: chat + tool detail panel), with specific reference to Kimi's interaction design. Key areas: tool call display, task progress, SubAgent visualization, and a right-side tool detail panel.

## Research Basis

- Kimi (kimi.com): collapsible tool steps, task progress bar, Agent Swarm, Artifact panel
- Manus (manus.im): grouped collapsible steps, computer status, step progress
- Perplexity Computer: split view, multi-agent orchestration
- Emerge Haus article: "The New Dominant UI Design for AI Agents" — left chat + right workspace

Full research: `research/agent-ui-research.md`

---

## 1. Layout Architecture

### 1.1 Three-Column Adaptive Layout

```
+----------+---------------------------+---------------------------+
| Sidebar  |     Chat Panel            |   Tool Detail Panel       |
| 224px    |     flex-1 (min 400px)    |   flex-1 (min 320px)      |
| (fixed)  |                           |   (on demand)             |
+----------+---------------------------+---------------------------+
```

- Use shadcn/ui `ResizablePanelGroup` + `ResizablePanel` + `ResizableHandle`
- **No tool detail open**: Panel hidden, Chat Panel fills remaining space (current behavior preserved)
- **Tool detail open**: Default 50/50 split, user-resizable via drag handle
- Sidebar stays at 224px (unchanged)
- Tool Detail Panel has close button (X), closing returns to single-column

### 1.2 AppShell Restructure

Current: `Sidebar | (Header + Main)`

New: `Sidebar | ResizablePanelGroup[ ChatPanel | ToolDetailPanel ]`

Header moves inside ChatPanel (Tool Detail Panel has its own header bar).

---

## 2. Tool Call Interaction Redesign

### 2.1 Collapsed State — Single-Line Summary

Replace current raw JSON display with Kimi-style collapsible entries:

```
┌──────────────────────────────────────────────────┐
│ [Terminal] execute  │  ls -la /workspace      ▸  │
└──────────────────────────────────────────────────┘
```

- Left: tool-type icon + tool name
- Center: smart parameter summary (not JSON dump)
  - `execute` → show `command` value
  - `web_search` → show `query` value
  - Other tools → first argument value, truncated
- Right: result count badge (if available) + chevron
- Entire row clickable to expand

### 2.2 Expanded State — Detail View

```
┌──────────────────────────────────────────────────┐
│ [Terminal] execute  │  ls -la /workspace      ▾  │
├──────────────────────────────────────────────────┤
│  [Clock] 1.2s                                    │
│                                                  │
│  Output:                                         │
│  ┌────────────────────────────────────────────┐  │
│  │ total 32                                   │  │
│  │ drwxr-xr-x 4 user user 4096 ...          │  │
│  └────────────────────────────────────────────┘  │
│                              [View in panel ->]  │
└──────────────────────────────────────────────────┘
```

- Show execution duration (frontend computed: tool_call.timestamp to tool_result.timestamp)
- Show tool_result content (currently stored but never rendered)
- Long output truncated + "View in panel" button → opens right-side panel
- Terminal/code output uses monospace + `bg-muted`

### 2.3 Consecutive Tool Call Grouping

Keep existing `groupBlocks` logic but render as vertically stacked collapsible entries with a connecting left border line:

```
┌──────────────────────────────────────────────────┐
│ [Terminal] execute  │  npm install      0.8s  ▸  │
│ [Terminal] execute  │  npm run build    2.3s  ▸  │
│ [Terminal] execute  │  npm test         1.1s  ▸  │
└──────────────────────────────────────────────────┘
```

### 2.4 Streaming Execution State

While tool is executing:

```
┌──────────────────────────────────────────────────┐
│ [Terminal] execute  │  npm run build  [Loader] Running... │
└──────────────────────────────────────────────────┘
```

- Pulse animation + live timer
- Frontend records `started_at` on `tool_call` event, computes duration on `tool_result`

---

## 3. Task Progress Bar (TodoListMiddleware)

### 3.1 Backend Integration

Add LangChain official `TodoListMiddleware` to agent graph:

```python
from langchain.agents.middleware import TodoListMiddleware
middleware.append(TodoListMiddleware())
```

Agent automatically gets `write_todos` tool for planning and tracking.

### 3.2 Frontend Data Extraction

The `write_todos` tool is called **per-item** (not batch). Each call:

```json
// tool_call arguments:
{"description": "Search papers", "status": "in_progress"}
// tool_result:
{"task_id": "task-123", "message": "Task 'Search papers' updated to in_progress."}
```

Frontend intercepts `tool_call` events where `name === "write_todos"`:
- On `tool_call`: upsert into `messageStore.todos[]` using description as temporary key
- On `tool_result`: update with server-assigned `task_id`
- Multiple calls accumulate into the full todo list over time

### 3.3 Progress Bar UI

Fixed between chat messages area and input bar:

**Collapsed (default)**:
```
┌──────────────────────────────────────────────────────┐
│ [ListChecks] Task Progress 2/4  [Circle] Data analysis...  [ChevronUp] │
└──────────────────────────────────────────────────────────┘
```

**Expanded**:
```
┌──────────────────────────────────────────────────────┐
│ [ListChecks] Task Progress 2/4                  [ChevronDown] │
│──────────────────────────────────────────────────────│
│  [CheckCircle2 green] Search papers                  │
│  [Circle blue+pulse]  Data analysis - Extract...     │
│  [Circle gray]        Generate visualizations        │
│  [Circle gray]        Output report                  │
└──────────────────────────────────────────────────────┘
```

**Status icons** (lucide-react, not emoji):
- Completed: `CheckCircle2` (`text-emerald-500`)
- In progress: `Circle` (`text-blue-500` + pulse animation)
- Pending: `Circle` (`text-muted-foreground/30`)

**Behavior**:
- Hidden when Agent hasn't used `write_todos`
- Auto-expands on first appearance, user can toggle
- Brief highlight animation when a todo transitions to new status
- `write_todos` tool_call **hidden from chat message flow** — only shown via progress bar

---

## 4. SubAgent Display Enhancement

### 4.1 Visual Upgrade

```
┌──────────────────────────────────────────────────────┐
│ [Bot] Research Agent                    Running ...   │
├──────────────────────────────────────────────────────┤
│  [Search] web_search │ AI agent patterns    8 results │
│  [Search] web_search │ Kimi interface      12 results │
│  [Terminal] execute  │ python analyze.py      1.3s   │
│                                                      │
│  Analysis complete, found 3 major design patterns... │
└──────────────────────────────────────────────────────┘
```

### 4.2 Design Details

- Left 2px colored border: `border-l-2 border-primary/40`
- Background: `bg-muted/10` to differentiate from main chat
- Internal tool calls reuse `ToolCallItem` component (same design as main agent)
- Header right side:
  - Running: pulse animation dots
  - Completed: `CheckCircle2` icon + duration text
- Text output rendered as Markdown (currently plain text)

### 4.3 SubAgent Todo Handling

If SubAgent uses `write_todos`, its todos display as a mini progress indicator inside the SubAgent card (completed/total), NOT in the main progress bar.

---

## 5. Right-Side Tool Detail Panel

### 5.1 Purpose

A **tool execution detail viewer** that opens when clicking a tool call entry. Renders different structured views based on tool type.

### 5.2 Panel Structure

```
┌──────────────────────────────────────────────┐
│  [icon] tool_name  "param summary"  [Copy][X]│
├──────────────────────────────────────────────┤
│  (content varies by tool type)               │
└──────────────────────────────────────────────┘
```

Header bar: tool icon + name + param summary + action buttons. Height matches Chat Panel header (h-11).

### 5.3 Tool-Type Views

**Search tools (`web_search` etc.)**:
- Result count header
- Card list: title + source domain + snippet per result
- Cards have hover highlight

**Code executor (future `code_execute`, reserved)**:
- Top: code with syntax highlighting (from tool_call arguments)
- Bottom: execution output (from tool_result)

**Web fetch (`web_fetch`)**:
- Full URL display (clickable)
- Content: Markdown prose rendering of fetched content

**Shell execution (`execute`)**:
- Command display: `$ npm run build`
- Output: monospace scrollable area
- Bottom: exit code

**Generic tools (default)**:
- Request section: formatted JSON from tool_call.arguments
- Response section: tool_result.content (try JSON parse + format, fallback to text)
- Each section has copy button

### 5.4 Theme Adaptation

All content blocks use Tailwind semantic colors (`bg-muted`, `bg-card`, `text-foreground`). Automatically follows light/dark theme. No hardcoded colors.

### 5.5 Artifact Reservation

Panel state type reserves `artifact` mode for future use:

```typescript
type PanelContentType = 'search' | 'code_execute' | 'web_fetch' | 'terminal' | 'generic' | 'artifact'
```

---

## 6. Visual Specifications

### 6.1 Design Principles

- Border radius: containers `rounded-xl` (12px), small elements `rounded-lg` (8px)
- Borders: `border-border` uniformly
- Spacing: `space-y-3` between components, `p-3` inside
- Typography: tool name `text-sm font-medium`, params `text-xs text-muted-foreground`, body `text-sm`
- Animation: only on state changes, 150-200ms duration, `transition-colors` / `transition-opacity`

### 6.2 Icon System (lucide-react)

All icons `size-3.5` (14px), color `text-muted-foreground`:

| Purpose | Icon |
|---------|------|
| Shell execution (`execute`) | `Terminal` |
| Search tools | `Search` |
| Web fetch | `Globe` |
| Code execution | `Code` |
| SubAgent | `Bot` |
| Generic tool | `Wrench` |
| Task progress | `ListChecks` |
| Completed | `CheckCircle2` (`text-emerald-500`) |
| In progress | `Circle` (`text-blue-500` + pulse) |
| Pending | `Circle` (`text-muted-foreground/30`) |
| Thinking | `Brain` |
| Expand/collapse | `ChevronDown` / `ChevronRight` |
| Close panel | `X` |
| Copy | `Copy` / `Check` (toggle on success) |

### 6.3 Colors

No new colors. All Tailwind semantic + limited status colors:
- Status green: `text-emerald-500` (completed)
- Status blue: `text-blue-500` (in_progress)
- Primary accent: `border-primary/40` (SubAgent card left border)
- All backgrounds: `bg-card`, `bg-muted`, `bg-muted/40`

---

## 7. Data Flow & State Management

### 7.1 Backend Changes

| File | Change | Detail |
|------|--------|--------|
| `agents/graph.py` | Add `TodoListMiddleware()` | 1 line |
| `agents/stream.py` | Add `tool_call_id` to `tool_result` events | Extract from ToolMessage for frontend correlation |

Tool execution duration computed frontend-side from event timestamps. No new event types needed.

### 7.2 Frontend Type Extensions

```typescript
// types/events.ts
interface TodoItem {
  id: string | null       // null until tool_result returns server-assigned task_id
  description: string     // primary field from write_todos arguments
  status: 'pending' | 'in_progress' | 'completed'
}

// tool_result event data
interface ToolResultData {
  tool_name: string
  tool_call_id: string  // NEW
  content: string
}
```

### 7.3 Store Changes

**messageStore extensions**:
```typescript
todos: TodoItem[]
toolResults: Map<string, { content: string; receivedAt: number }>
```

Processing:
- `tool_call(name=write_todos)` → parse arguments → update `todos`
- `tool_result` → store by `tool_call_id` in `toolResults` map

**New toolDetailStore**:
```typescript
interface ToolDetailStore {
  isOpen: boolean
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  contentType: PanelContentType
  open: (toolName, toolArgs, toolResult) => void
  close: () => void
}
```

### 7.4 Hook Changes

`useMessages` returns new: `todos`, `toolResults`

New `useToolDetail` hook for panel state.

### 7.5 Data Flow

```
SSE events
  ├─ tool_call(write_todos) → messageStore.todos → TaskProgressBar
  ├─ tool_call(other)       → messageStore.blocks → ToolCallItem (chat)
  ├─ tool_result            → messageStore.toolResults ←── ToolCallItem reads
  └─ user clicks item       → toolDetailStore → ToolDetailPanel (right side)
```

---

## 8. Component Inventory

### 8.1 New Components

```
components/
├── chat/
│   ├── ToolCallItem.tsx        NEW: single tool call collapsible entry
│   ├── ToolCallGroup.tsx       NEW: consecutive tool calls container
│   └── TaskProgressBar.tsx     NEW: bottom progress bar
├── panel/
│   ├── ToolDetailPanel.tsx     NEW: right panel container
│   ├── PanelHeader.tsx         NEW: panel header bar
│   ├── SearchResultView.tsx    NEW: search results renderer
│   ├── TerminalView.tsx        NEW: terminal output renderer
│   ├── WebFetchView.tsx        NEW: web content renderer
│   ├── CodeExecuteView.tsx     NEW: code+output renderer (reserved)
│   └── GenericToolView.tsx     NEW: generic Request/Response JSON
└── ui/
    └── resizable.tsx           NEW: shadcn resizable component
```

### 8.2 Refactored Components

| Component | Change |
|-----------|--------|
| `AppShell.tsx` | Restructure with ResizablePanelGroup |
| `AssistantMessage.tsx` | Extract tool call rendering to ToolCallItem, filter write_todos |
| `SubAgentCard.tsx` | Visual upgrade, reuse ToolCallItem internally |
| `MessageList.tsx` | Pass toolResults data |

### 8.3 Core Package Changes

```
packages/core/src/
├── types/events.ts          EXTEND: TodoItem, ToolResultData
├── stores/messageStore.ts   EXTEND: todos[], toolResults Map
├── stores/toolDetailStore.ts NEW: panel state
└── index.ts                 EXPORT: new types and store
```

### 8.4 Backend Changes

```
backend/cubebox/
├── agents/graph.py          ADD: TodoListMiddleware
└── agents/stream.py         ADD: tool_call_id in tool_result events
```

---

## Scope Exclusions

- Sidebar collapse/expand — not in this iteration
- Sandbox screenshots or file system browsing
- Artifact auto-detection from tool results
- Replay/share mode
- Mobile responsive layout
