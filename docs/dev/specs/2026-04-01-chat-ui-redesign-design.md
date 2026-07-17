# Chat UI Redesign — Design Spec

## Overview

Redesign the cubeplex frontend chat interface to match the emerging dominant UI pattern for AI agent applications (split-screen: chat + tool detail panel), with specific reference to Kimi's interaction design. Key areas: tool call display, task progress, SubAgent visualization, and a right-side tool detail panel.

Goals:
- **Professional, polished UI** — clean visual hierarchy, not prototype-quality
- **Kimi as primary reference** — collapsible tool steps, task progress, Agent Swarm, side panel
- **Manus as secondary reference** — grouped steps, error transparency, step progress
- **Minimal backend changes** — leverage existing SSE events, add TodoListMiddleware

## Research Basis

- **Kimi** (kimi.com): collapsible tool steps with Request/Response detail, task progress bar (Phase N/M), Agent Swarm with sub-agent delegation, right-side Artifact panel (Excel/code preview), "Kimi's Computer" modal for tool detail inspection
- **Manus** (manus.im): grouped collapsible steps by phase with progress descriptions, "Manus's computer" panel with screen thumbnail + current tool status, task progress (8/8) with checkmark list, error recovery transparency (e.g., "Next.js failed, switching to React")
- **Perplexity Computer**: split view (`?view=split`), 19-model orchestration, task decomposition into subtasks with sub-agents, isolated compute environments
- **Emerge Haus** article "The New Dominant UI Design for AI Agents": left ~50% chat + right ~50% workspace is the converging standard; transparency builds trust; familiar interface lowers adoption friction

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

Header moves inside ChatPanel (Tool Detail Panel has its own header bar). Both headers share the same height (`h-11`) for visual alignment.

### 1.3 Chat Panel Internal Structure

```
┌─────────────────────────────────┐
│ Header (h-11, border-b)        │  ← conversation title + theme toggle
├─────────────────────────────────┤
│                                 │
│ MessageList (flex-1, scroll)    │  ← user messages + assistant messages
│                                 │
├─────────────────────────────────┤
│ TaskProgressBar (conditional)   │  ← only visible when todos exist
├─────────────────────────────────┤
│ InputBar                        │  ← text input + send button
└─────────────────────────────────┘
```

---

## 2. Tool Call Interaction Redesign

### 2.1 Collapsed State — Single-Line Summary

Replace current raw JSON display (`ToolCallList` component showing `tc.name + JSON.stringify(tc.arguments).slice(0, 100)`) with Kimi-style collapsible entries:

```
┌──────────────────────────────────────────────────┐
│ [Terminal] execute  │  ls -la /workspace      ▸  │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│ [Search] web_search │ AI agent UI...   12 results ▸ │
└──────────────────────────────────────────────────┘
```

**Design details**:
- Left: tool-type icon (see Section 6.2 icon table) + tool name in `text-sm font-medium`
- Center: smart parameter summary in `text-xs text-muted-foreground` (not JSON dump)
  - `execute` → show `command` argument value
  - `web_search` → show `query` argument value
  - `web_fetch` → show `url` argument value
  - `subagent` → handled separately by SubAgentCard (not ToolCallItem)
  - `write_todos` → **hidden entirely** from chat flow (shown only in TaskProgressBar)
  - Other tools → first argument value, truncated to ~60 chars
- Right: result count badge (if tool_result contains parseable count) + `ChevronRight` icon
- Entire row clickable to expand
- Background: `bg-card` with `border border-border rounded-lg`
- Hover: `hover:bg-muted/50 transition-colors`

### 2.2 Expanded State — Detail View

Click to expand inline (using shadcn Collapsible):

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
│  │ -rw-r--r-- 1 user user 1234 ...          │  │
│  └────────────────────────────────────────────┘  │
│                              [View in panel ->]  │
└──────────────────────────────────────────────────┘
```

- **Execution duration**: frontend computed from `tool_call.timestamp` → `tool_result.timestamp` diff. Display with `Clock` icon in `text-xs text-muted-foreground`.
- **Tool result content**: read from `messageStore.toolResults` map by `tool_call_id`. This data is **already received and stored** in the frontend `AgentStream.toolResults` array but **currently never rendered** — this is the key fix.
- **Output area**: monospace font, `bg-muted rounded-md p-2`, max-height with overflow scroll
- **Long output truncation**: if content > 10 lines, show first 6 lines + "..." + "View in panel" button
- **"View in panel" button**: `text-xs text-primary cursor-pointer hover:underline`, opens right-side ToolDetailPanel with full content
- **Correlation**: `tool_call_id` is needed to match `tool_call` → `tool_result`. Currently `tool_result` SSE event lacks this field (see Section 7.1 backend changes).

### 2.3 Consecutive Tool Call Grouping

Keep existing `groupBlocks` logic in `AssistantMessage.tsx` (lines 225-241) but change rendering from a single card with all tools listed, to vertically stacked individual `ToolCallItem` components wrapped in a `ToolCallGroup`:

```
┌──────────────────────────────────────────────────┐
│ [Terminal] execute  │  npm install      0.8s  ▸  │
├──────────────────────────────────────────────────┤
│ [Terminal] execute  │  npm run build    2.3s  ▸  │
├──────────────────────────────────────────────────┤
│ [Terminal] execute  │  npm test         1.1s  ▸  │
└──────────────────────────────────────────────────┘
```

- Wrapped in a single `rounded-xl` container with shared border
- Items separated by thin `border-t border-border` dividers (not separate cards)
- Left 2px accent line connecting the group: `border-l-2 border-muted-foreground/20`
- Each item independently expandable

### 2.4 Streaming Execution State

While tool is executing (tool_call received but tool_result not yet):

```
┌──────────────────────────────────────────────────┐
│ [Terminal] execute  │  npm run build    ● 3s...  │
└──────────────────────────────────────────────────┘
```

- Small `Circle` icon with `animate-pulse` in `text-blue-500` + live elapsed timer
- Timer logic reuses the same pattern as `ReasoningBlock` (record `started_at = Date.now()` on tool_call event, setInterval tick every 1s)
- On tool_result arrival: animation stops, timer freezes to final duration, icon changes to static `CheckCircle2` in `text-emerald-500`

---

## 3. Task Progress Bar (TodoListMiddleware)

### 3.1 Backend Integration

Add LangChain official `TodoListMiddleware` to the middleware stack in `agents/graph.py`:

```python
from langchain.agents.middleware import TodoListMiddleware
middleware.append(TodoListMiddleware())
```

**How it works**:
- Middleware registers a `write_todos` tool available to the agent
- Middleware injects a system prompt guiding the agent to plan tasks for complex work
- Agent calls `write_todos` to create/update individual todo items
- Todo state is persisted in LangGraph agent state via the checkpointer — **no extra DB table needed**
- Historical conversations reload with todos already in state

**Custom prompt** (optional, via `TodoListMiddleware(system_prompt=...)`): can be tuned to encourage the agent to create todos at the start of multi-step tasks and update status as each step completes. Default LangChain prompt is a good starting point.

### 3.2 Frontend Data Extraction

The `write_todos` tool is called **per-item** (not batch). Each invocation:

```json
// tool_call event data:
{"name": "write_todos", "arguments": {"description": "Search papers", "status": "in_progress"}}

// tool_result event data:
{"tool_name": "write_todos", "content": "{\"task_id\": \"task-123\", \"message\": \"Task updated.\"}"}
```

**Frontend processing in messageStore**:
1. On `tool_call(name=write_todos)`: extract `description` and `status` from arguments, upsert into `todos[]` using `description` as temporary key (since `task_id` not yet known)
2. On `tool_result(tool_name=write_todos)`: parse content JSON, extract `task_id`, update the matching todo item with server-assigned id
3. Multiple calls accumulate the full todo list progressively as the agent works

**Filtering**: `write_todos` tool_call blocks are **excluded** from `ContentBlockRenderer` — they do not appear as tool call cards in the chat. Data is only surfaced through the TaskProgressBar.

### 3.3 Progress Bar UI

Fixed between MessageList and InputBar:

**Collapsed (default after first interaction)**:
```
┌──────────────────────────────────────────────────────────┐
│ [ListChecks] Task Progress 2/4  ● Data analysis...  [^] │
└──────────────────────────────────────────────────────────┘
```

- Left: `ListChecks` icon + "Task Progress N/M" where N = completed count, M = total
- Center: current in_progress task description (truncated)
- Right: `ChevronUp`/`ChevronDown` toggle

**Expanded**:
```
┌──────────────────────────────────────────────────────────┐
│ [ListChecks] Task Progress 2/4                      [v] │
├──────────────────────────────────────────────────────────┤
│  [CheckCircle2] Search papers                            │
│  [CheckCircle2] Extract key data                         │
│  [Circle ●]     Data analysis - Analyze trends...        │
│  [Circle ○]     Generate report                          │
└──────────────────────────────────────────────────────────┘
```

**Status icons** (lucide-react only, no emoji):
- **Completed**: `CheckCircle2` icon in `text-emerald-500`
- **In progress**: `Circle` icon in `text-blue-500` with `animate-pulse` (subtle)
- **Pending**: `Circle` icon in `text-muted-foreground/30` (hollow appearance via opacity)

**Behavior**:
- **Hidden** when agent hasn't used `write_todos` (component returns null, no space occupied)
- **Auto-expands** on first `write_todos` call to show the user that planning has started
- User can manually toggle collapse/expand, preference persists within session
- **Status transition highlight**: when a todo changes status (e.g., pending → in_progress), brief `bg-primary/5` highlight on that row, fading over 500ms
- **Styling**: `bg-card border-t border-border` to visually separate from MessageList above and InputBar below. Inner padding `px-4 py-2`.

---

## 4. SubAgent Display Enhancement

### 4.1 Current Problems

`SubAgentCard.tsx` is a simple collapsible card that only shows:
- Name + bounce dots when running
- Flat list of tool_call names + truncated JSON args
- Plain text output (no Markdown rendering)

Missing: visual hierarchy, phase awareness, progress indication, duration.

### 4.2 New Design

```
┌──────────────────────────────────────────────────────┐
│ [Bot] Research Agent                    Running ●●●  │
├──────────────────────────────────────────────────────┤
│                                                      │
│  ▸ [Search] web_search │ AI agent patterns  8 results│
│  ▸ [Search] web_search │ Kimi interface    12 results│
│  ▸ [Terminal] execute  │ python analyze.py    1.3s   │
│                                                      │
│  Analysis complete, found 3 major design patterns... │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 4.3 Design Details

**Card container**:
- Left 2px colored border: `border-l-2 border-primary/40` — primary visual differentiator from regular tool cards
- Background: `bg-muted/10` — subtle differentiation from main chat area
- Rounded: `rounded-xl` to match other containers
- Overall: slightly indented relative to main message flow via padding

**Header row**:
- Left: `Bot` icon + SubAgent name in `font-medium`
- Right status:
  - Running: three dots with staggered `animate-pulse` (more refined than current `animate-bounce`)
  - Completed: `CheckCircle2` in `text-emerald-500` + total duration text (e.g., "12.3s")
- Duration: computed from first tool_call timestamp to last tool_result timestamp of this subagent's stream

**Internal tool calls**:
- **Reuse `ToolCallItem` component** — same collapsed/expanded behavior as main agent tool calls
- This ensures visual consistency and reduces code duplication
- Tool calls within SubAgent are also clickable to open right-side panel

**Text output**:
- Currently rendered as plain text (`<p>` tag)
- Change to **Markdown rendering** using `ReactMarkdown` + `remarkGfm` with same `proseClasses` as main AssistantMessage
- This allows SubAgent output with headers, lists, code blocks to render properly

### 4.4 SubAgent Todo Handling

If a SubAgent also uses `write_todos`:
- Its todos are **NOT** merged into the main TaskProgressBar (would be confusing)
- Instead, show a **mini progress indicator** in the SubAgent card header: e.g., `[2/5]` next to the name
- Implementation: filter `todos` by `agent_id` prefix matching

### 4.5 Historical SubAgent Rendering

Current `subagentSummaryToStream()` function constructs a fake `AgentStream` from consolidated `SubagentSummary` data. This should continue to work — the new `ToolCallItem` will render from the same `toolCalls` array, just with improved visuals.

---

## 5. Right-Side Tool Detail Panel

### 5.1 Purpose

A **tool execution detail viewer** — not an artifact preview. Opens when user clicks a tool call entry in the chat. Renders different structured views based on tool type. Background color follows theme (light/dark), not hardcoded dark.

### 5.2 Panel Structure

```
┌──────────────────────────────────────────────┐
│  [icon] tool_name  "param summary"  [Copy][X]│
├──────────────────────────────────────────────┤
│                                              │
│  (content varies by tool type — see 5.3)     │
│                                              │
└──────────────────────────────────────────────┘
```

**Header bar (PanelHeader)**:
- Height: `h-11` matching ChatPanel header for visual alignment
- Left: tool-type icon + tool name + param summary
- Right: copy button (`Copy` icon, click → `Check` icon for 2s) + close button (`X` icon)
- Border: `border-b border-border`
- Background: inherits from theme (`bg-card` or `bg-background`)

**Content area**:
- `flex-1 overflow-auto` with `p-4` padding
- ScrollArea for long content

### 5.3 Tool-Type Views

**1) Search tools (`web_search` etc.) — `SearchResultView`**:

```
┌──────────────────────────────────────────────┐
│  [Search] web_search  "AI agent UI"    [X]   │
├──────────────────────────────────────────────┤
│  12 results                                  │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │ The New Dominant UI Design for AI...   │  │
│  │ emerge.haus                            │  │
│  │ AI agents are the next step beyond...  │  │
│  └────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────┐  │
│  │ Perplexity Computer: Full Guide...     │  │
│  │ thesys.dev                             │  │
│  │ The AI Agent Built to Run Entire...    │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

- Parse tool_result content to extract result items (title, url, snippet)
- Result count header: `text-sm text-muted-foreground`
- Each result card: `bg-muted/30 rounded-lg p-3`, title in `font-medium`, domain in `text-xs text-muted-foreground`, snippet in `text-sm`
- Hover: `hover:bg-muted/50 transition-colors`

**2) Code executor (future, reserved) — `CodeExecuteView`**:

```
┌──────────────────────────────────────────────┐
│  [Code] code_execute  "analyze.py"     [X]   │
├──────────────────────────────────────────────┤
│  Code                                        │
│  ┌────────────────────────────────────────┐  │
│  │ import pandas as pd                    │  │
│  │ df = pd.read_csv("data.csv")          │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  Output                                      │
│  ┌────────────────────────────────────────┐  │
│  │       count  mean   std               │  │
│  │ age   100    34.5   12.3              │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

- Top section: code from `tool_call.arguments` with syntax highlighting
- Bottom section: result from `tool_result.content` in monospace
- Both sections: `bg-muted rounded-lg p-3`
- **Reserved**: implement component shell with placeholder, actual rendering when code_execute tool exists

**3) Web fetch (`web_fetch`) — `WebFetchView`**:

```
┌──────────────────────────────────────────────┐
│  [Globe] web_fetch  "emerge.haus/..."  [X]   │
├──────────────────────────────────────────────┤
│  https://emerge.haus/blog/the-new-dom...     │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │ (Markdown prose rendered content)      │  │
│  │                                        │  │
│  │ # The New Dominant UI Design           │  │
│  │ There's a pattern to how technology... │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

- Full URL display at top: `text-xs text-primary hover:underline` (clickable, opens in new tab)
- Content area: Markdown prose rendering with same `proseClasses` from AssistantMessage
- If content is too long, ScrollArea handles it

**4) Shell execution (`execute`) — `TerminalView`**:

```
┌──────────────────────────────────────────────┐
│  [Terminal] execute  "npm run build"   [X]   │
├──────────────────────────────────────────────┤
│  $ npm run build                             │
│  ┌────────────────────────────────────────┐  │
│  │ > cubeplex@1.0.0 build                 │  │
│  │ > next build                          │  │
│  │                                       │  │
│  │ ✓ Compiled successfully               │  │
│  │ ✓ Collecting page data                │  │
│  └────────────────────────────────────────┘  │
│                                   exit: 0    │
└──────────────────────────────────────────────┘
```

- Command display: `$ {command}` in `font-mono text-sm font-medium`
- Output: `font-mono text-sm` in `bg-muted rounded-lg p-3`, full scrollable
- Exit code footer: `text-xs text-muted-foreground` aligned right
- Exit code parsed from tool_result content if available (current `execute` tool appends `\n[exit: N]`)

**5) Generic tools (default fallback) — `GenericToolView`**:

```
┌──────────────────────────────────────────────┐
│  [Wrench] some_tool                    [X]   │
├──────────────────────────────────────────────┤
│  Request                              [Copy] │
│  ┌────────────────────────────────────────┐  │
│  │ {                                     │  │
│  │   "param1": "value1",                │  │
│  │   "param2": 42                       │  │
│  │ }                                     │  │
│  └────────────────────────────────────────┘  │
│                                              │
│  Response                             [Copy] │
│  ┌────────────────────────────────────────┐  │
│  │ {                                     │  │
│  │   "result": "success",               │  │
│  │   "data": [...]                      │  │
│  │ }                                     │  │
│  └────────────────────────────────────────┘  │
└──────────────────────────────────────────────┘
```

- **Request**: `JSON.stringify(tool_call.arguments, null, 2)` with `font-mono text-sm`
- **Response**: try `JSON.parse(tool_result.content)` then pretty-print; fallback to raw text
- Each section has independent copy button
- Section labels: `text-xs font-medium text-muted-foreground uppercase tracking-wider`
- Both blocks: `bg-muted rounded-lg p-3`

### 5.4 Theme Adaptation

**Critical**: all views must follow the current theme (light or dark). Rules:
- All backgrounds use Tailwind semantic tokens: `bg-card`, `bg-muted`, `bg-muted/40`
- All text uses: `text-foreground`, `text-muted-foreground`
- No hardcoded hex colors (e.g., no `#1e1e1e` for "dark terminal")
- Code/terminal blocks use `bg-muted` which adapts automatically
- Borders: `border-border` everywhere

### 5.5 Artifact Reservation

Panel state type reserves `artifact` mode for future use:

```typescript
type PanelContentType = 'search' | 'code_execute' | 'web_fetch' | 'terminal' | 'generic' | 'artifact'
```

When `contentType === 'artifact'`: not implemented now, will render a placeholder or fall through to `generic`. No artifact auto-detection logic in this iteration.

### 5.6 Opening the Panel

Three trigger methods:

1. **Click "View in panel" button** in an expanded ToolCallItem — primary trigger
2. **Click a code block** in assistant message text — sends code content to panel for larger view
3. **Future**: Agent outputs structured artifact → auto-opens panel

When the panel opens:
- `toolDetailStore.open(toolName, toolArgs, toolResult)` is called
- `contentType` is auto-detected from `toolName`:
  - `execute` → `'terminal'`
  - `web_search` / `search` → `'search'`
  - `web_fetch` / `fetch` → `'web_fetch'`
  - `code_execute` / `python` → `'code_execute'`
  - everything else → `'generic'`
- If panel was already open with different content, content is replaced (not stacked)

---

## 6. Visual Specifications

### 6.1 Design Principles

Overall aesthetic: **clean, professional, information-dense but not cluttered** (reference: Kimi's restraint).

- **Border radius**: large containers `rounded-xl` (12px), small elements/inner blocks `rounded-lg` (8px)
- **Borders**: `border-border` uniformly, no mixed border styles
- **Spacing**: `space-y-3` between sibling components, `p-3` inside containers, `gap-2` for inline items
- **Typography hierarchy**:
  - Tool name: `text-sm font-medium text-foreground`
  - Parameter summary: `text-xs text-muted-foreground`
  - Body text: `text-sm text-foreground`
  - Labels/captions: `text-xs text-muted-foreground/70`
  - Monospace (code/terminal): `font-mono text-sm`
- **Animations**: only on state changes, 150-200ms duration, using `transition-colors` / `transition-opacity`. Avoid gratuitous motion. Allowed animations:
  - `animate-pulse` for in-progress status indicators (subtle opacity pulse, not scale)
  - `transition-colors duration-150` for hover states
  - `transition-all duration-200` for collapsible open/close
  - Brief `bg-primary/5` highlight (500ms fade) for todo status transitions
- **Hover states**: all interactive elements have visible hover feedback via `hover:bg-muted/50` or similar

### 6.2 Icon System (lucide-react)

All icons use `size-3.5` (14px) consistently. Default color `text-muted-foreground` unless specified:

| Purpose | Icon Name | Color Override |
|---------|-----------|----------------|
| Shell execution (`execute`) | `Terminal` | — |
| Search tools | `Search` | — |
| Web fetch | `Globe` | — |
| Code execution | `Code` | — |
| SubAgent | `Bot` | — |
| Generic tool | `Wrench` | — |
| Task progress header | `ListChecks` | — |
| Todo completed | `CheckCircle2` | `text-emerald-500` |
| Todo in progress | `Circle` | `text-blue-500` + `animate-pulse` |
| Todo pending | `Circle` | `text-muted-foreground/30` |
| Thinking/reasoning | `Brain` | — |
| Expand | `ChevronRight` | — |
| Collapse | `ChevronDown` | — |
| Close panel | `X` | — |
| Copy | `Copy` → `Check` on success | `Check` in `text-emerald-500` for 2s |
| Duration/time | `Clock` | — |
| View in panel | `ExternalLink` or `PanelRight` | `text-primary` |

### 6.3 Colors

No new design tokens. All Tailwind semantic colors + limited status overrides:

- **Status green**: `text-emerald-500` — completed tasks, successful operations
- **Status blue**: `text-blue-500` — in-progress indicators
- **Primary accent**: `border-primary/40` — SubAgent card left border, active elements
- **Backgrounds**: `bg-card`, `bg-muted`, `bg-muted/40`, `bg-muted/10` — layered depth
- **Hover**: `hover:bg-muted/50` — universal interactive feedback
- **Highlight flash**: `bg-primary/5` — brief status transition indication

All colors automatically adapt to light/dark theme through Tailwind's CSS variable system.

### 6.4 `write_todos` Filtering

In the chat message flow, `write_todos` tool calls are **invisible**:
- `ContentBlockRenderer`: skip blocks where `block.type === 'tool_call' && block.name === 'write_todos'`
- `groupBlocks`: exclude `write_todos` from consecutive tool call grouping
- The data is consumed only by `TaskProgressBar` via `messageStore.todos`

---

## 7. Data Flow & State Management

### 7.1 Backend Changes

Only 2 files need modification:

**1) `backend/cubeplex/agents/graph.py`** — Add TodoListMiddleware:

```python
from langchain.agents.middleware import TodoListMiddleware

# In create_cubeplex_agent(), add to middleware stack:
middleware.append(TodoListMiddleware())
```

Position in stack: after `SkillsMiddleware`, before `SubAgentMiddleware`. This ensures the todo prompt is injected and the `write_todos` tool is available to both main agent and (if desired) subagents.

**2) `backend/cubeplex/agents/stream.py`** — Add `tool_call_id` to tool_result events:

Current `tool_result` event (line 73-86) only includes `tool_name` and `content`. The `tool_call_id` field is available on ToolMessage objects but not extracted.

Change: extract `tool_call_id` from the ToolMessage and include it in the event:

```python
# In the tool result section of convert_chunk_to_events():
tool_call_id = (
    msg.get("tool_call_id", "") if isinstance(msg, dict)
    else getattr(msg, "tool_call_id", "")
)

events.append({
    "type": "tool_result",
    "timestamp": timestamp,
    "data": {
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,  # NEW
        "content": content if isinstance(content, str) else str(content),
    },
    "agent_id": agent_id,
})
```

**Why this matters**: without `tool_call_id`, the frontend cannot precisely match which `tool_result` belongs to which `tool_call`. Currently the frontend stores `toolResults` in an array but never renders them — partly because there's no reliable way to correlate them. Adding this field enables the `ToolCallItem` expanded view to show the correct result.

**No other backend changes needed**: no new event types, no new API endpoints, no new middleware beyond TodoListMiddleware.

### 7.2 Existing Data That Is Available But Not Used

Before this redesign, the following data is already flowing through but wasted:

| Data | Where it exists | Why unused | Fix |
|------|-----------------|------------|-----|
| `tool_result` content | `AgentStream.toolResults[]` in messageStore | Components never read from this array | `ToolCallItem` reads it by matching `tool_call_id` |
| Token usage (`input_tokens`, `output_tokens`) | `text_delta` event `data.usage` | Received but not persisted | Out of scope for this redesign, but noted |
| Error details | `ErrorEvent.data.details` | Not extracted in error handler | Out of scope, but noted |
| Status detail | `StatusEvent.data.detail` | Ignored | Out of scope, but noted |

### 7.3 Frontend Type Extensions

```typescript
// packages/core/src/types/events.ts — NEW types

interface TodoItem {
  id: string | null       // null until tool_result returns server-assigned task_id
  description: string     // primary field from write_todos arguments
  status: 'pending' | 'in_progress' | 'completed'
}

// tool_result event data — EXTENDED
interface ToolResultData {
  tool_name: string
  tool_call_id: string  // NEW: correlates with ToolCallEvent.data.tool_call_id
  content: string
}

// Panel content type — NEW
type PanelContentType = 'search' | 'code_execute' | 'web_fetch' | 'terminal' | 'generic' | 'artifact'
```

### 7.4 Store Changes

**messageStore extensions** (in `packages/core/src/stores/messageStore.ts`):

```typescript
// New state fields:
todos: TodoItem[]
toolResults: Map<string, { content: string; receivedAt: number }>
// key = tool_call_id, receivedAt = Date.now() for duration calculation

// New processing in the event handler:
// 1. On tool_call where name === 'write_todos':
//    - Extract description, status from arguments
//    - Upsert into todos[] (match by description as temp key)
//
// 2. On tool_result:
//    - Store in toolResults map keyed by tool_call_id
//    - If tool_name === 'write_todos': parse content JSON, extract task_id, update matching todo
```

**New toolDetailStore** (in `packages/core/src/stores/toolDetailStore.ts`):

```typescript
interface ToolDetailStore {
  isOpen: boolean
  toolName: string
  toolArgs: Record<string, unknown>
  toolResult: string | null
  contentType: PanelContentType

  open: (toolName: string, toolArgs: Record<string, unknown>, toolResult: string | null) => void
  close: () => void
}

// contentType auto-detection in open():
// 'execute' → 'terminal'
// 'web_search' | 'search' → 'search'
// 'web_fetch' | 'fetch' → 'web_fetch'
// 'code_execute' | 'python' → 'code_execute'
// * → 'generic'
```

### 7.5 Hook Changes

**`useMessages` hook** (in `packages/web/hooks/useMessages.ts`):
- Returns new fields: `todos: TodoItem[]`, `toolResults: Map<string, ToolResultData>`
- These are read from `messageStore`

**New `useToolDetail` hook**:
- Thin wrapper around `toolDetailStore` using Zustand `useStore`
- Returns: `isOpen`, `toolName`, `toolArgs`, `toolResult`, `contentType`, `open()`, `close()`

### 7.6 Data Flow Diagram

```
Backend SSE Stream
  │
  ├─ tool_call(name=write_todos)
  │     └─→ messageStore.todos[] (upsert)
  │           └─→ TaskProgressBar component
  │
  ├─ tool_call(name=execute/search/...)
  │     └─→ messageStore.streamAgents[agentKey].blocks[]
  │           └─→ AssistantMessage → ToolCallItem (collapsed)
  │
  ├─ tool_result
  │     └─→ messageStore.toolResults Map (by tool_call_id)
  │           └─→ ToolCallItem reads result for expanded view + duration calc
  │
  └─ User clicks ToolCallItem "View in panel"
        └─→ toolDetailStore.open(name, args, result)
              └─→ AppShell shows ToolDetailPanel (right side)
                    └─→ Routes to SearchResultView / TerminalView / etc.
```

---

## 8. Component Inventory

### 8.1 New Components

```
frontend/packages/web/components/
├── chat/
│   ├── ToolCallItem.tsx        NEW: single tool call collapsible entry
│   ├── ToolCallGroup.tsx       NEW: consecutive tool calls wrapper with connecting line
│   └── TaskProgressBar.tsx     NEW: bottom todo progress bar
├── panel/
│   ├── ToolDetailPanel.tsx     NEW: right panel container + routing by contentType
│   ├── PanelHeader.tsx         NEW: panel header bar (title + copy + close)
│   ├── SearchResultView.tsx    NEW: search results card list
│   ├── TerminalView.tsx        NEW: command + terminal output
│   ├── WebFetchView.tsx        NEW: URL + Markdown prose content
│   ├── CodeExecuteView.tsx     NEW: code + output split (reserved/placeholder)
│   └── GenericToolView.tsx     NEW: Request JSON + Response JSON
└── ui/
    └── resizable.tsx           NEW: shadcn/ui resizable panel components
```

### 8.2 Refactored Components

| Component | What Changes | Detail |
|-----------|-------------|--------|
| `AppShell.tsx` | Major restructure | Wrap main area in `ResizablePanelGroup`, conditionally show `ToolDetailPanel` based on `toolDetailStore.isOpen` |
| `AssistantMessage.tsx` | Extract + filter | Remove `ToolCallList` component, delegate to `ToolCallItem`/`ToolCallGroup`. Filter out `write_todos` blocks in rendering. Keep `ReasoningBlock`, `ContentBlockRenderer`, `proseClasses`. |
| `SubAgentCard.tsx` | Visual upgrade | Add left border accent, use `ToolCallItem` for internal tool calls, Markdown rendering for text output, refined pulse animation, duration display |
| `MessageList.tsx` | Pass new data | Thread `toolResults` map and `todos` down to child components |

### 8.3 Deleted/Replaced Code

| Code | Action |
|------|--------|
| `ToolCallList` component in AssistantMessage.tsx (lines 88-99) | **Delete** — replaced by `ToolCallItem` |
| Grouped tool call card rendering in AssistantMessage.tsx (lines 262-272) | **Replace** with `ToolCallGroup` component |

### 8.4 Core Package Changes

```
frontend/packages/core/src/
├── types/events.ts               EXTEND: add TodoItem, ToolResultData, PanelContentType
├── stores/messageStore.ts        EXTEND: add todos[], toolResults Map, processing logic
├── stores/toolDetailStore.ts     NEW: panel open/close state
└── index.ts                      EXTEND: export new types and toolDetailStore
```

### 8.5 Backend Changes

```
backend/cubeplex/
├── agents/graph.py               MODIFY: add TodoListMiddleware() to middleware stack
└── agents/stream.py              MODIFY: add tool_call_id field to tool_result events
```

---

## 9. Scope Exclusions

Explicitly **not** in this iteration:

- Sidebar collapse/expand functionality
- Sandbox screenshots or file system browsing
- Artifact auto-detection from tool results (panel type reserved but not implemented)
- Replay/share mode (Kimi and Manus both have this)
- Mobile responsive layout
- Token usage display
- Error detail display enhancement
- `tool_started` event (proposed in research but not needed for this iteration)
- `subagent_spawned` / `subagent_completed` explicit events
- Agent status machine events (`thinking`, `calling_tool`, etc.)
