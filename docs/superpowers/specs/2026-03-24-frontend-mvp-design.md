# Frontend MVP Design

**Date:** 2026-03-24
**Status:** Approved
**Scope:** Web frontend for cubebox AI agent product

---

## Overview

A Perplexity Computer-style agent product frontend. The core experience is a **hybrid** model: centered input box on the home page (like Perplexity), expanding into a full conversation view with step-by-step execution visibility when the agent runs.

---

## Product Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Primary experience | Hybrid (centered input → conversation) | Balances discoverability with task transparency |
| Execution display | Standard (tool calls + streaming LLM output) | Not too sparse, not overwhelming |
| Sidebar | Collapsible left, default expanded ~260px | Like ChatGPT/Claude.ai — familiar pattern |
| Execution details | Collapsible (default collapsed, auto-expands during streaming) | Answer always visible, details on demand |
| Cross-platform reuse | Monorepo with `packages/core` (pure TS) | Small-program support planned; core layer is framework-free |
| Theme system | shadcn/ui + next-themes (dark class strategy) | shadcn components work out-of-the-box, no overrides needed |

---

## Repository Structure

```
cubebox/
├── frontend/
│   ├── packages/
│   │   ├── core/                    # Pure TS, zero framework dependencies
│   │   │   ├── src/
│   │   │   │   ├── api/             # HTTP + SSE client
│   │   │   │   │   ├── client.ts    # Base fetch wrapper
│   │   │   │   │   ├── conversations.ts
│   │   │   │   │   └── stream.ts    # SSE async generator
│   │   │   │   ├── stores/          # Zustand stores
│   │   │   │   │   ├── conversationStore.ts
│   │   │   │   │   ├── messageStore.ts
│   │   │   │   │   └── themeStore.ts
│   │   │   │   └── types/           # Shared type definitions
│   │   │   │       ├── conversation.ts
│   │   │   │       ├── message.ts
│   │   │   │       └── events.ts    # AgentEvent types
│   │   │   ├── package.json         # name: @cubebox/core
│   │   │   └── tsconfig.json
│   │   └── web/                     # Next.js App Router
│   │       ├── app/
│   │       │   ├── layout.tsx       # Root: fonts, ThemeProvider
│   │       │   ├── page.tsx         # Welcome page: centered input
│   │       │   └── conversations/
│   │       │       └── [id]/
│   │       │           └── page.tsx # Chat page
│   │       ├── components/
│   │       │   ├── layout/
│   │       │   │   ├── AppShell.tsx
│   │       │   │   ├── Sidebar.tsx
│   │       │   │   └── InputBar.tsx
│   │       │   ├── chat/
│   │       │   │   ├── MessageList.tsx
│   │       │   │   ├── UserMessage.tsx
│   │       │   │   ├── AssistantMessage.tsx
│   │       │   │   └── ExecutionDetails.tsx
│   │       │   └── ui/              # shadcn/ui components (auto-generated)
│   │       ├── hooks/               # Thin React wrappers over core stores
│   │       │   ├── useConversations.ts
│   │       │   └── useMessages.ts
│   │       ├── package.json         # name: @cubebox/web
│   │       └── next.config.ts
│   ├── package.json                 # pnpm workspace root
│   └── pnpm-workspace.yaml
```

---

## Architecture Layers

### Layer 1: `core/api/` — Network

All HTTP and SSE communication with the backend.

```ts
// core/api/stream.ts
async function* streamMessages(
  conversationId: string,
  content: string
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`/api/v1/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  const reader = res.body!.getReader()
  for await (const line of readLines(reader)) {
    if (line.startsWith('data: ')) {
      yield JSON.parse(line.slice(6)) as AgentEvent
    }
  }
}
```

### Layer 2: `core/stores/` — State

Zustand stores with no React imports. Safe to use in any JS environment.

```ts
// core/stores/conversationStore.ts
interface ConversationStore {
  conversations: Conversation[]
  activeId: string | null
  fetchList(): Promise<void>
  create(title?: string): Promise<Conversation>
  remove(id: string): Promise<void>
  rename(id: string, title: string): Promise<void>
  setActive(id: string): void
}

// core/stores/messageStore.ts
interface MessageStore {
  messages: Record<string, Message[]>  // keyed by conversationId
  streamingEvents: AgentEvent[]         // live buffer during streaming
  isStreaming: boolean
  sendMessage(conversationId: string, content: string): Promise<void>
  fetchHistory(conversationId: string): Promise<void>
}
```

### Layer 3: `web/hooks/` — React Bridge

Thin hooks that subscribe to Zustand stores. No business logic here.

```ts
// web/hooks/useMessages.ts
export function useMessages(conversationId: string) {
  return useMessageStore((s) => ({
    messages: s.messages[conversationId] ?? [],
    streamingEvents: s.streamingEvents,
    isStreaming: s.isStreaming,
    send: (content: string) => s.sendMessage(conversationId, content),
  }))
}
```

### Layer 4: `web/components/` — UI

Pure rendering. Receives data from hooks, emits events via callbacks.

---

## Page Structure

### Welcome Page (`/`)

Shown when no conversation is active. Centered layout:

```
┌─────────────────────────────────────────────┐
│  [Sidebar]  │                               │
│             │         cubebox               │
│             │    ┌─────────────────────┐    │
│             │    │  有什么可以帮你的？    │    │
│             │    └─────────────────────┘    │
│             │         [Send]                │
└─────────────────────────────────────────────┘
```

On submit: create conversation → navigate to `/conversations/[id]` → send message.

### Chat Page (`/conversations/[id]`)

```
┌─────────────────────────────────────────────┐
│  Sidebar    │  MessageList (scrollable)      │
│  ─────────  │  ────────────────────────────  │
│  + New Chat │  [UserMessage]                 │
│             │  [AssistantMessage]            │
│  Today      │    └─ ExecutionDetails         │
│  > Chat 1   │    └─ Streaming text           │
│             │                                │
│  Yesterday  │  ────────────────────────────  │
│  > Chat 2   │  [InputBar - fixed bottom]     │
└─────────────────────────────────────────────┘
```

---

## Key Component: ExecutionDetails

Behavior:
- **During streaming**: auto-expands, appends events in real time
- **After completion**: auto-collapses to summary line
- **User can toggle**: click to expand/collapse at any time

Summary line format: `「已完成 · 2 个工具调用 · 1.3s」`

Expanded view maps SSE events to rows:

| Event type | Display |
|---|---|
| `chain_start` | 「开始执行」 |
| `llm_start` | 「思考中...」(spinner) |
| `llm_end` | 「生成完成」 |
| `tool_start` | 「⚙ {tool_name} · 输入: {input}」 |
| `tool_end` | 「✓ 结果: {output}」 |
| `chain_end` | 「完成」 |
| `error` | 「✗ 错误: {message}」(destructive color) |

---

## Theme System

- **Library**: `next-themes` + shadcn/ui CSS variables
- **Strategy**: `attribute="class"`, adds `dark` class to `<html>` for dark mode
- **Default**: `dark` (dark-first, per STYLE_GUIDE.md)
- **Persistence**: `localStorage` via next-themes
- **Brand colors**: STYLE_GUIDE.md values mapped into shadcn's `--primary`, `--background`, etc.

```tsx
// app/layout.tsx
<ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
  {children}
</ThemeProvider>
```

---

## Technology Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 15 App Router |
| UI Components | shadcn/ui (installed per-component) |
| Styling | Tailwind CSS v3 |
| Theme | next-themes |
| State | Zustand (in `core/`, no React dependency) |
| Package Manager | pnpm workspace |
| Icons | lucide-react |
| Code Highlighting | shiki |
| Build | Turbopack (Next.js built-in) |

### shadcn/ui components for MVP

`button` `input` `textarea` `tooltip` `scroll-area` `separator` `skeleton` `badge` `collapsible`

---

## MVP Scope

In scope:
- Conversation list (sidebar) with create / delete / rename
- Message history with pagination
- Send message with SSE streaming
- ExecutionDetails component (collapsible, real-time)
- Dark / light theme toggle
- Code block syntax highlighting in messages
- Responsive layout (desktop + mobile)

Out of scope (post-MVP):
- File uploads
- Multi-modal input
- WeChat mini-program (`packages/miniapp`)
- User authentication
- Settings page

---

## Cross-Platform Strategy

`packages/core` contains all business logic with zero framework dependencies. When building the WeChat mini-program:

1. Add `packages/miniapp/` to the workspace
2. `npm install @cubebox/core` (workspace reference: `workspace:*`)
3. Implement UI layer using mini-program components
4. Reuse all stores and API clients unchanged

The only constraint: `core/` must never import from `react`, `next`, or any browser-only API. Use dependency injection for environment-specific behavior (e.g., storage adapters).
