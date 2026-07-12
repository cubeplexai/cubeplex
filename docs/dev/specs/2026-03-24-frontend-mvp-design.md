# Frontend MVP Design

**Date:** 2026-03-24
**Status:** Approved
**Scope:** Web frontend for cubeplex AI agent product

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
cubeplex/
├── frontend/
│   ├── packages/
│   │   ├── core/                    # Pure TS, zero framework dependencies
│   │   │   ├── src/
│   │   │   │   ├── api/             # HTTP + SSE client
│   │   │   │   │   ├── client.ts    # Base fetch wrapper (accepts injected baseUrl)
│   │   │   │   │   ├── conversations.ts
│   │   │   │   │   └── stream.ts    # SSE async generator
│   │   │   │   ├── stores/          # Zustand stores (conversationStore, messageStore)
│   │   │   │   └── types/           # Shared type definitions
│   │   │   │       ├── conversation.ts
│   │   │   │       ├── message.ts
│   │   │   │       └── events.ts    # AgentEvent types
│   │   │   ├── package.json         # name: @cubeplex/core
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
│   │       ├── lib/
│   │       │   └── theme.tsx        # ThemeStore + ThemeToggle (web-only)
│   │       ├── package.json         # name: @cubeplex/web
│   │       └── next.config.ts       # API proxy rewrites + env vars
│   ├── package.json                 # pnpm workspace root
│   └── pnpm-workspace.yaml
```

---

## Architecture Layers

### Layer 1: `core/api/` — Network

All HTTP and SSE communication with the backend. `client.ts` accepts a `baseUrl` injected at initialization — never hardcodes a path. This makes `core/` portable to any platform.

```ts
// core/api/client.ts
export function createApiClient(baseUrl: string) {
  return {
    get: (path: string) => fetch(`${baseUrl}${path}`),
    post: (path: string, body: unknown) =>
      fetch(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
  }
}
```

**Create conversation** uses a query param (matching backend signature):
```ts
// core/api/conversations.ts
async function createConversation(client: ApiClient, title?: string) {
  const url = title ? `/api/v1/conversations?title=${encodeURIComponent(title)}` : '/api/v1/conversations'
  const res = await client.post(url, {})
  if (!res.ok) throw await toApiError(res)
  return res.json() as Promise<Conversation>
}
```

**SSE streaming** with error handling:
```ts
// core/api/stream.ts
async function* streamMessages(
  baseUrl: string,
  conversationId: string,
  content: string
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`${baseUrl}/api/v1/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })

  // HTTP-level error before SSE starts
  if (!res.ok) throw await toApiError(res)

  const reader = res.body!.getReader()
  try {
    for await (const line of readLines(reader)) {
      if (line.startsWith('data: ')) {
        yield JSON.parse(line.slice(6)) as AgentEvent
      }
    }
  } catch (e) {
    // Connection dropped mid-stream — yield a synthetic error event
    // so the caller can handle it uniformly
    yield { type: 'error', data: { message: 'Connection lost' }, timestamp: new Date().toISOString() }
  }
}
```

**`done` event is the front-end state machine's termination signal.** `messageStore.sendMessage` sets `isStreaming = false` upon receiving a `done` event, or in the `finally` block if the generator exits for any reason.

### Layer 2: `core/stores/` — State

Zustand stores. No React imports. Note: Zustand is a runtime dependency — `core/` is "pure TS" in the sense of no framework-specific APIs, but it does depend on Zustand. WeChat mini-program compatibility should be verified before `packages/miniapp` is started.

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

`sendMessage` guarantees `isStreaming = false` on completion or error. Note: the `done` event is pushed into `streamingEvents` before the break — `ExecutionDetails` must filter out `type === 'done'` events when rendering.
```ts
async sendMessage(conversationId, content) {
  set({ isStreaming: true, streamingEvents: [] })
  try {
    for await (const event of streamMessages(baseUrl, conversationId, content)) {
      set((s) => ({ streamingEvents: [...s.streamingEvents, event] }))
      if (event.type === 'done') break
    }
  } finally {
    set({ isStreaming: false })
  }
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

### Layer 5: `web/lib/theme.tsx` — Web-only Theme

Theme management lives in `web/`, not `core/`. Uses `next-themes` + shadcn/ui CSS variables.

---

## Page Structure

### Welcome Page (`/`)

Shown when no conversation is active. Centered layout:

```
┌─────────────────────────────────────────────┐
│  [Sidebar]  │                               │
│             │         cubeplex               │
│             │    ┌─────────────────────┐    │
│             │    │  有什么可以帮你的？    │    │
│             │    └─────────────────────┘    │
│             │         [Send]                │
└─────────────────────────────────────────────┘
```

On submit: `createConversation(firstMessage)` → navigate to `/conversations/[id]` → `sendMessage(content)`.

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
| `done` | *(state machine termination signal — filtered out, not rendered)* |

---

## Assistant Message Rendering

The backend saves assistant messages with `content = null` and `events = [...]` (the full SSE event array). There is no pre-extracted text field.

**During streaming:** `AssistantMessage` reads from `streamingEvents` in the store.

**From history** (`fetchHistory`): The backend `GET /api/v1/conversations/{id}/messages` response includes the `events` field for each message (confirmed). `fetchHistory` stores the full `Message` objects including `events` in the store. `AssistantMessage` receives the stored `events` array and:
1. Extracts the final text from `llm_end.data.output` (last `llm_end` event)
2. Passes the full `events` array to `ExecutionDetails` for the collapsible panel
3. `ExecutionDetails` defaults to collapsed for historical messages

```ts
// core/types/message.ts
interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string | null      // null for assistant messages
  events: AgentEvent[] | null // null for user messages
  created_at: string
}

// Utility to extract final text from events
function extractAssistantText(events: AgentEvent[]): string {
  const lastLlmEnd = [...events].reverse().find((e) => e.type === 'llm_end')
  return lastLlmEnd?.data?.output ?? ''
}
```

---

## API Proxy Configuration

The `web` package proxies API calls to the backend via `next.config.ts`. This avoids CORS issues in development and keeps `core/api/client.ts` using relative paths on web.

```ts
// web/next.config.ts
const nextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'}/api/:path*`,
      },
    ]
  },
}
```

`CUBEPLEX_API_URL` defaults to `http://localhost:8000`. In production, set via environment variable.

The web package initializes `core` with `baseUrl = ''` (empty string), so all API calls use the Next.js proxy. Non-web platforms inject their own `baseUrl` directly.

---

## Theme System

- **Library**: `next-themes` + shadcn/ui CSS variables
- **Location**: `web/lib/theme.tsx` (web-only, not in `core/`)
- **Strategy**: `attribute="class"`, adds `dark` class to `<html>` for dark mode
- **Default**: `dark`
- **Persistence**: `localStorage` via next-themes
- **Brand colors**: mapped into shadcn's CSS variables in `globals.css`

Key color tokens (from cubetrace STYLE_GUIDE):

```css
/* globals.css — dark theme */
:root {
  --background: 220 13% 9%;
  --foreground: 220 9% 95%;
  --card: 220 13% 11%;
  --primary: 210 100% 50%;        /* #0080FF brand blue */
  --primary-foreground: 0 0% 100%;
  --muted: 220 13% 13%;
  --muted-foreground: 220 9% 65%;
  --border: 220 13% 15%;
  --radius: 0.5rem;
}
.dark { /* same as :root — dark is default */ }
.light {
  --background: 0 0% 100%;
  --foreground: 240 10% 3.9%;
  --card: 0 0% 100%;
  --primary: 210 100% 50%;        /* brand blue unchanged */
  --border: 240 5.9% 90%;
  --muted: 240 4.8% 95.9%;
  --muted-foreground: 240 3.8% 46.1%;
}
```

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
| Theme | next-themes (web-only) |
| State | Zustand (in `core/`) |
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
- Message history
- Send message with SSE streaming
- ExecutionDetails component (collapsible, real-time)
- Dark / light theme toggle
- Code block syntax highlighting in messages
- Desktop layout (mobile is post-MVP)

Out of scope (post-MVP):
- Mobile responsive layout
- File uploads
- Multi-modal input
- WeChat mini-program (`packages/miniapp`)
- User authentication
- Settings page

---

## Cross-Platform Strategy

`packages/core` contains all business logic. When building the WeChat mini-program:

1. Add `packages/miniapp/` to the workspace
2. Reference `@cubeplex/core` via `workspace:*`
3. Implement UI layer using mini-program components
4. Reuse all stores and API clients — inject mini-program's `baseUrl` and verify Zustand compatibility

The only constraint: `core/` must never import from `react`, `next`, or any browser-only global. Environment-specific concerns (storage, base URL) are injected at initialization.
