# Frontend MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Perplexity Computer-style web frontend for the cubeplex AI agent product, with streaming SSE support, collapsible execution details, and dark/light theme switching.

**Architecture:** Monorepo with `packages/core` (pure TS, Zustand stores + SSE API client) and `packages/web` (Next.js App Router + shadcn/ui). Core layer is platform-agnostic for future mini-program reuse. Web layer provides UI and theme management.

**Tech Stack:** Next.js 15 App Router, shadcn/ui, Tailwind CSS, Zustand, next-themes, pnpm workspace, TypeScript, TDD.

---

## File Structure (Pre-Implementation)

```
cubeplex/frontend/
├── package.json                         (workspace root)
├── pnpm-workspace.yaml
├── packages/
│   ├── core/
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── src/
│   │       ├── api/
│   │       │   ├── client.ts           (HTTP client factory)
│   │       │   ├── conversations.ts    (conversation CRUD)
│   │       │   └── stream.ts           (SSE async generator)
│   │       ├── stores/
│   │       │   ├── conversationStore.ts
│   │       │   └── messageStore.ts
│   │       └── types/
│   │           ├── conversation.ts
│   │           ├── message.ts
│   │           └── events.ts
│   │
│   └── web/
│       ├── package.json
│       ├── next.config.ts
│       ├── tsconfig.json
│       ├── app/
│       │   ├── layout.tsx              (root layout + theme provider)
│       │   ├── page.tsx                (welcome page)
│       │   └── conversations/
│       │       └── [id]/
│       │           └── page.tsx        (chat page)
│       ├── components/
│       │   ├── layout/
│       │   │   ├── AppShell.tsx
│       │   │   ├── Sidebar.tsx
│       │   │   └── InputBar.tsx
│       │   ├── chat/
│       │   │   ├── MessageList.tsx
│       │   │   ├── UserMessage.tsx
│       │   │   ├── AssistantMessage.tsx
│       │   │   └── ExecutionDetails.tsx
│       │   └── ui/                     (shadcn auto-generated)
│       ├── hooks/
│       │   ├── useConversations.ts
│       │   └── useMessages.ts
│       └── lib/
│           └── theme.tsx               (theme store + toggle)
```

---

## Task Breakdown

### Task 1: Monorepo Setup

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/pnpm-workspace.yaml`

- [ ] **Step 1: Create root package.json**

```json
{
  "name": "cubeplex-frontend",
  "version": "0.0.1",
  "private": true,
  "packageManager": "pnpm@10.23.0",
  "scripts": {
    "dev": "pnpm -r run dev",
    "build": "pnpm -r run build",
    "type-check": "pnpm -r run type-check"
  },
  "devDependencies": {
    "typescript": "^5.3.3"
  }
}
```

- [ ] **Step 2: Create pnpm-workspace.yaml**

```yaml
packages:
  - 'packages/*'
```

- [ ] **Step 3: Initialize core package**

Create `packages/core/package.json`:
```json
{
  "name": "@cubeplex/core",
  "version": "0.0.1",
  "private": true,
  "type": "module",
  "exports": {
    ".": "./dist/index.js",
    "./api": "./dist/api/index.js",
    "./stores": "./dist/stores/index.js",
    "./types": "./dist/types/index.js"
  },
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "scripts": {
    "build": "tsc",
    "type-check": "tsc --noEmit"
  },
  "dependencies": {
    "zustand": "^4.4.2"
  },
  "devDependencies": {
    "@types/node": "^20.10.6",
    "typescript": "^5.3.3"
  }
}
```

- [ ] **Step 4: Create core tsconfig.json**

Create `packages/core/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020"],
    "module": "ESNext",
    "skipLibCheck": true,
    "declaration": true,
    "declarationMap": true,
    "sourceMap": true,
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noImplicitReturns": true,
    "noImplicitAny": true,
    "resolveJsonModule": true,
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "outDir": "./dist",
    "rootDir": "./src"
  },
  "include": ["src"]
}
```

- [ ] **Step 5: Commit monorepo setup**

```bash
cd /home/chris/cubeplex/frontend
git add package.json pnpm-workspace.yaml packages/core/package.json packages/core/tsconfig.json
git commit -m "feat: initialize monorepo structure with core package"
```

---

### Task 2: Core Types Definition

**Files:**
- Create: `packages/core/src/types/conversation.ts`
- Create: `packages/core/src/types/message.ts`
- Create: `packages/core/src/types/events.ts`
- Create: `packages/core/src/types/index.ts`

- [ ] **Step 1: Define Conversation type**

Create `packages/core/src/types/conversation.ts`:
```ts
export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}
```

- [ ] **Step 2: Define AgentEvent types**

Create `packages/core/src/types/events.ts`:
```ts
export type AgentEventType = 'chain_start' | 'llm_start' | 'llm_end' | 'tool_start' | 'tool_end' | 'chain_end' | 'error' | 'done'

export interface AgentEvent {
  type: AgentEventType
  timestamp: string
  data: Record<string, any>
}

export interface ChainStartEvent extends AgentEvent {
  type: 'chain_start'
  data: { input: string }
}

export interface LlmStartEvent extends AgentEvent {
  type: 'llm_start'
  data: Record<string, any>
}

export interface LlmEndEvent extends AgentEvent {
  type: 'llm_end'
  data: {
    output: string
    usage?: { input_tokens: number; output_tokens: number }
  }
}

export interface ToolStartEvent extends AgentEvent {
  type: 'tool_start'
  data: { tool_name: string; input: Record<string, any> }
}

export interface ToolEndEvent extends AgentEvent {
  type: 'tool_end'
  data: { tool_name: string; output: string }
}

export interface ChainEndEvent extends AgentEvent {
  type: 'chain_end'
  data: Record<string, any>
}

export interface ErrorEvent extends AgentEvent {
  type: 'error'
  data: { error_code: string; message: string; details?: string }
}

export interface DoneEvent extends AgentEvent {
  type: 'done'
  data: Record<string, any>
}
```

- [ ] **Step 3: Define Message type**

Create `packages/core/src/types/message.ts`:
```ts
import type { AgentEvent } from './events'

export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'assistant'
  content: string | null
  events: AgentEvent[] | null
  created_at: string
}
```

- [ ] **Step 4: Create types index**

Create `packages/core/src/types/index.ts`:
```ts
export type * from './conversation'
export type * from './message'
export type * from './events'
```

- [ ] **Step 5: Create core index**

Create `packages/core/src/index.ts`:
```ts
export * from './types'
```

- [ ] **Step 6: Commit types**

```bash
cd /home/chris/cubeplex/frontend
git add packages/core/src/
git commit -m "feat: define core types (Conversation, Message, AgentEvent)"
```

---

### Task 3: Core API Client

**Files:**
- Create: `packages/core/src/api/client.ts`
- Create: `packages/core/src/api/conversations.ts`
- Create: `packages/core/src/api/stream.ts`
- Create: `packages/core/src/api/index.ts`

- [ ] **Step 1: Implement base API client**

Create `packages/core/src/api/client.ts`:
```ts
export interface ApiClient {
  baseUrl: string
  get(path: string): Promise<Response>
  post(path: string, body: unknown): Promise<Response>
}

export function createApiClient(baseUrl: string): ApiClient {
  return {
    baseUrl,
    get: (path: string) => fetch(`${baseUrl}${path}`),
    post: (path: string, body: unknown) =>
      fetch(`${baseUrl}${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
  }
}

export async function toApiError(res: Response): Promise<Error> {
  const contentType = res.headers.get('content-type')
  if (contentType?.includes('application/json')) {
    const data = await res.json()
    return new Error(data.message || `HTTP ${res.status}`)
  }
  return new Error(`HTTP ${res.status}: ${res.statusText}`)
}
```

- [ ] **Step 2: Implement conversation API**

Create `packages/core/src/api/conversations.ts`:
```ts
import type { Conversation, Message } from '../types'
import { toApiError, type ApiClient } from './client'

export async function createConversation(client: ApiClient, title?: string): Promise<Conversation> {
  const url = title ? `/api/v1/conversations?title=${encodeURIComponent(title)}` : '/api/v1/conversations'
  const res = await client.post(url, {})
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

export async function listConversations(client: ApiClient, limit = 50, offset = 0): Promise<Conversation[]> {
  const url = `/api/v1/conversations?limit=${limit}&offset=${offset}`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = await res.json()
  return data.conversations || []
}

export async function getConversation(client: ApiClient, id: string): Promise<Conversation> {
  const res = await client.get(`/api/v1/conversations/${id}`)
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

export async function deleteConversation(client: ApiClient, id: string): Promise<void> {
  const res = await client.post(`/api/v1/conversations/${id}?_method=DELETE`, {})
  if (!res.ok) throw await toApiError(res)
}

export async function renameConversation(client: ApiClient, id: string, title: string): Promise<Conversation> {
  const res = await client.post(`/api/v1/conversations/${id}?_method=PATCH`, { title })
  if (!res.ok) throw await toApiError(res)
  return res.json()
}

export async function listMessages(client: ApiClient, conversationId: string, limit = 50, offset = 0): Promise<Message[]> {
  const url = `/api/v1/conversations/${conversationId}/messages?limit=${limit}&offset=${offset}`
  const res = await client.get(url)
  if (!res.ok) throw await toApiError(res)
  const data = await res.json()
  return data.messages || []
}
```

- [ ] **Step 3: Implement SSE stream**

Create `packages/core/src/api/stream.ts`:
```ts
import type { AgentEvent } from '../types'

async function* readLines(reader: ReadableStreamDefaultReader<Uint8Array>): AsyncGenerator<string> {
  let buffer = ''
  const decoder = new TextDecoder()
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      yield line
    }
  }
  if (buffer) yield buffer
}

export async function* streamMessages(
  baseUrl: string,
  conversationId: string,
  content: string
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`${baseUrl}/api/v1/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })

  if (!res.ok) {
    const error = new Error(`HTTP ${res.status}`)
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: { message: error.message },
    } as AgentEvent
    return
  }

  const reader = res.body!.getReader()
  try {
    for await (const line of readLines(reader)) {
      if (line.startsWith('data: ')) {
        try {
          yield JSON.parse(line.slice(6)) as AgentEvent
        } catch {
          // skip malformed lines
        }
      }
    }
  } catch (err) {
    yield {
      type: 'error',
      timestamp: new Date().toISOString(),
      data: { message: 'Connection lost' },
    } as AgentEvent
  }
}
```

- [ ] **Step 4: Create API index**

Create `packages/core/src/api/index.ts`:
```ts
export { createApiClient, type ApiClient } from './client'
export * from './conversations'
export * from './stream'
```

- [ ] **Step 5: Commit API client**

```bash
cd /home/chris/cubeplex/frontend
git add packages/core/src/api/
git commit -m "feat: implement core API client (conversations, SSE streaming)"
```

---

### Task 4: Core Zustand Stores

**Files:**
- Create: `packages/core/src/stores/conversationStore.ts`
- Create: `packages/core/src/stores/messageStore.ts`
- Create: `packages/core/src/stores/index.ts`

- [ ] **Step 1: Implement conversation store**

Create `packages/core/src/stores/conversationStore.ts`:
```ts
import { create } from 'zustand'
import type { Conversation } from '../types'
import type { ApiClient } from '../api'
import { createConversation, listConversations, deleteConversation, renameConversation } from '../api'

export interface ConversationStore {
  conversations: Conversation[]
  activeId: string | null
  isLoading: boolean
  error: string | null
  fetchList(client: ApiClient): Promise<void>
  create(client: ApiClient, title?: string): Promise<Conversation>
  remove(client: ApiClient, id: string): Promise<void>
  rename(client: ApiClient, id: string, title: string): Promise<void>
  setActive(id: string | null): void
}

export const useConversationStore = create<ConversationStore>((set) => ({
  conversations: [],
  activeId: null,
  isLoading: false,
  error: null,

  async fetchList(client: ApiClient) {
    set({ isLoading: true, error: null })
    try {
      const conversations = await listConversations(client)
      set({ conversations })
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isLoading: false })
    }
  },

  async create(client: ApiClient, title?: string) {
    set({ isLoading: true, error: null })
    try {
      const convo = await createConversation(client, title)
      set((s) => ({ conversations: [convo, ...s.conversations] }))
      return convo
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    } finally {
      set({ isLoading: false })
    }
  },

  async remove(client: ApiClient, id: string) {
    try {
      await deleteConversation(client, id)
      set((s) => ({
        conversations: s.conversations.filter((c) => c.id !== id),
        activeId: s.activeId === id ? null : s.activeId,
      }))
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    }
  },

  async rename(client: ApiClient, id: string, title: string) {
    try {
      const updated = await renameConversation(client, id, title)
      set((s) => ({
        conversations: s.conversations.map((c) => (c.id === id ? updated : c)),
      }))
    } catch (err) {
      set({ error: (err as Error).message })
      throw err
    }
  },

  setActive(id: string | null) {
    set({ activeId: id })
  },
}))
```

- [ ] **Step 2: Implement message store**

Create `packages/core/src/stores/messageStore.ts`:
```ts
import { create } from 'zustand'
import type { Message, AgentEvent } from '../types'
import type { ApiClient } from '../api'
import { listMessages, streamMessages } from '../api'

export interface MessageStore {
  messages: Record<string, Message[]>
  streamingEvents: AgentEvent[]
  isStreaming: boolean
  error: string | null
  fetchHistory(client: ApiClient, conversationId: string): Promise<void>
  sendMessage(client: ApiClient, conversationId: string, content: string): Promise<void>
  clearStreaming(): void
}

export const useMessageStore = create<MessageStore>((set) => ({
  messages: {},
  streamingEvents: [],
  isStreaming: false,
  error: null,

  async fetchHistory(client: ApiClient, conversationId: string) {
    try {
      const messages = await listMessages(client, conversationId)
      set((s) => ({
        messages: { ...s.messages, [conversationId]: messages },
      }))
    } catch (err) {
      set({ error: (err as Error).message })
    }
  },

  async sendMessage(client: ApiClient, conversationId: string, content: string) {
    set({ isStreaming: true, streamingEvents: [], error: null })
    try {
      for await (const event of streamMessages(client.baseUrl, conversationId, content)) {
        set((s) => ({ streamingEvents: [...s.streamingEvents, event] }))
        if (event.type === 'done') break
      }
    } catch (err) {
      set({ error: (err as Error).message })
    } finally {
      set({ isStreaming: false })
    }
  },

  clearStreaming() {
    set({ streamingEvents: [], isStreaming: false })
  },
}))
```

- [ ] **Step 3: Create stores index**

Create `packages/core/src/stores/index.ts`:
```ts
export { useConversationStore, type ConversationStore } from './conversationStore'
export { useMessageStore, type MessageStore } from './messageStore'
```

- [ ] **Step 4: Update core index.ts**

Update `packages/core/src/index.ts` to export all stores and API:
```ts
export * from './types'
export * from './api'
export * from './stores'
```

- [ ] **Step 5: Commit stores and update index**

```bash
cd /home/chris/cubeplex/frontend
git add packages/core/src/stores/ packages/core/src/index.ts
git commit -m "feat: implement Zustand stores (conversation, message)"
```

---

### Task 5: Next.js Web Project Setup

**Files:**
- Create: `packages/web/package.json`
- Create: `packages/web/tsconfig.json`
- Create: `packages/web/next.config.ts`
- Create: `packages/web/.env.local` (optional, git-ignored)

- [ ] **Step 1: Initialize Next.js project with shadcn**

```bash
cd /home/chris/cubeplex/frontend/packages
rm -rf web 2>/dev/null || true
pnpm dlx create-next-app@latest web \
  --typescript \
  --tailwind \
  --app \
  --no-eslint \
  --no-git \
  --src-dir=false
```

- [ ] **Step 2: Add @cubeplex/core as workspace dependency**

Update `packages/web/package.json`:
```json
{
  "dependencies": {
    "@cubeplex/core": "workspace:*",
    "next": "latest",
    "react": "latest",
    "react-dom": "latest"
  }
}
```

- [ ] **Step 3: Add shadcn and next-themes to web**

```bash
cd /home/chris/cubeplex/frontend/packages/web
pnpm add next-themes zustand lucide-react
pnpm dlx shadcn@latest init --defaults
```

- [ ] **Step 4: Install shadcn components for MVP**

```bash
cd /home/chris/cubeplex/frontend/packages/web
pnpm dlx shadcn@latest add button input textarea tooltip scroll-area separator badge collapsible
```

- [ ] **Step 5: Create next.config.ts with API proxy**

Create `packages/web/next.config.ts`:
```ts
import type { NextConfig } from 'next'

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.CUBEPLEX_API_URL ?? 'http://localhost:8000'}/api/:path*`,
      },
    ]
  },
}

export default nextConfig
```

- [ ] **Step 6: Commit web setup**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/
git commit -m "feat: initialize Next.js web project with shadcn/ui"
```

---

### Task 6: Root Layout & Theme Provider

**Files:**
- Create: `packages/web/lib/theme.tsx`
- Modify: `packages/web/app/layout.tsx`
- Modify: `packages/web/app/globals.css`

- [ ] **Step 1: Create theme store (web-only)**

Create `packages/web/lib/theme.tsx`:
```ts
'use client'

import { useEffect, useState } from 'react'
import { create } from 'zustand'

interface ThemeStore {
  theme: 'dark' | 'light'
  toggle(): void
}

export const useThemeStore = create<ThemeStore>((set) => ({
  theme: 'dark',
  toggle() {
    set((s) => {
      const newTheme = s.theme === 'dark' ? 'light' : 'dark'
      if (typeof document !== 'undefined') {
        document.documentElement.classList.toggle('light', newTheme === 'light')
        localStorage.setItem('theme', newTheme)
      }
      return { theme: newTheme }
    })
  },
}))

export function useThemeInitializer() {
  const [mounted, setMounted] = useState(false)
  const { theme, toggle } = useThemeStore()

  useEffect(() => {
    const stored = localStorage.getItem('theme') as 'dark' | 'light' | null
    const initial = stored || 'dark'
    if (initial !== theme) {
      toggle()
    }
    setMounted(true)
  }, [])

  return mounted
}
```

- [ ] **Step 2: Update root layout**

Update `packages/web/app/layout.tsx`:
```tsx
import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import { ThemeProvider } from 'next-themes'
import './globals.css'

const inter = Inter({ subsets: ['latin'] })

export const metadata: Metadata = {
  title: 'cubeplex',
  description: 'AI Agent System',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html suppressHydrationWarning>
      <body className={inter.className}>
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
          {children}
        </ThemeProvider>
      </body>
    </html>
  )
}
```

- [ ] **Step 3: Customize theme colors in globals.css**

Update `packages/web/app/globals.css` (extend the shadcn CSS variables with brand colors):
```css
@layer base {
  :root {
    --background: 220 13% 9%;
    --foreground: 220 9% 95%;
    --card: 220 13% 11%;
    --card-foreground: 220 9% 95%;
    --primary: 210 100% 50%;
    --primary-foreground: 0 0% 100%;
    --secondary: 220 13% 13%;
    --secondary-foreground: 220 9% 95%;
    --muted: 220 13% 13%;
    --muted-foreground: 220 9% 65%;
    --accent: 220 13% 15%;
    --accent-foreground: 220 9% 95%;
    --border: 220 13% 15%;
    --input: 220 13% 15%;
    --ring: 210 100% 50%;
    --radius: 0.5rem;
  }

  .light {
    --background: 0 0% 100%;
    --foreground: 240 10% 3.9%;
    --card: 0 0% 100%;
    --card-foreground: 240 10% 3.9%;
    --primary: 210 100% 50%;
    --primary-foreground: 0 0% 100%;
    --secondary: 240 4.8% 95.9%;
    --secondary-foreground: 240 5.9% 10%;
    --muted: 240 4.8% 95.9%;
    --muted-foreground: 240 3.8% 46.1%;
    --accent: 240 4.8% 95.9%;
    --accent-foreground: 240 10% 3.9%;
    --border: 240 5.9% 90%;
    --input: 240 5.9% 90%;
    --ring: 210 100% 50%;
  }
}
```

- [ ] **Step 4: Commit theme setup**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/lib/ packages/web/app/
git commit -m "feat: add theme provider and dark/light mode support"
```

---

### Task 7: Welcome Page

**Files:**
- Create: `packages/web/app/page.tsx`
- Create: `packages/web/components/layout/InputBar.tsx`

- [ ] **Step 1: Create InputBar component**

Create `packages/web/components/layout/InputBar.tsx`:
```tsx
'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ArrowUp } from 'lucide-react'

interface InputBarProps {
  onSubmit: (content: string) => void
  isLoading?: boolean
}

export function InputBar({ onSubmit, isLoading = false }: InputBarProps) {
  const [content, setContent] = useState('')

  const handleSubmit = () => {
    if (!content.trim()) return
    onSubmit(content)
    setContent('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      handleSubmit()
    }
  }

  return (
    <div className="w-full max-w-2xl mx-auto px-4 pb-8">
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <Textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="有什么可以帮你的？"
          className="resize-none min-h-24"
          disabled={isLoading}
        />
        <div className="flex justify-end">
          <Button
            onClick={handleSubmit}
            disabled={!content.trim() || isLoading}
            size="sm"
            data-icon="inline-end"
          >
            <ArrowUp className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create welcome page**

Update `packages/web/app/page.tsx`:
```tsx
'use client'

import { useRouter } from 'next/navigation'
import { useConversationStore } from '@cubeplex/core'
import { createApiClient } from '@cubeplex/core'
import { InputBar } from '@/components/layout/InputBar'

export default function WelcomePage() {
  const router = useRouter()
  const { create: createConversation } = useConversationStore()

  const handleSubmit = async (content: string) => {
    const client = createApiClient('')
    try {
      const convo = await createConversation(client, content.slice(0, 30))
      useConversationStore.setState({ activeId: convo.id })
      router.push(`/conversations/${convo.id}`)
      // Send message after navigation
      setTimeout(() => {
        const store = useConversationStore.getState()
        // Message sending will happen on the chat page
      }, 100)
    } catch (err) {
      console.error('Failed to create conversation:', err)
    }
  }

  return (
    <div className="h-screen flex flex-col items-center justify-center bg-background text-foreground">
      <div className="text-center mb-12">
        <h1 className="text-4xl font-bold mb-2">cubeplex</h1>
        <p className="text-muted-foreground">AI 智能体系统</p>
      </div>
      <InputBar onSubmit={handleSubmit} />
    </div>
  )
}
```

- [ ] **Step 3: Commit welcome page**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/app/page.tsx packages/web/components/layout/InputBar.tsx
git commit -m "feat: add welcome page with centered input"
```

---

### Task 8: Chat Page Layout Components

**Files:**
- Create: `packages/web/components/layout/AppShell.tsx`
- Create: `packages/web/components/layout/Sidebar.tsx`
- Modify: `packages/web/app/conversations/[id]/page.tsx`

- [ ] **Step 1: Create Sidebar component**

Create `packages/web/components/layout/Sidebar.tsx`:
```tsx
'use client'

import { useConversationStore } from '@cubeplex/core'
import { createApiClient } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import Link from 'next/link'
import { Plus, Trash2 } from 'lucide-react'

export function Sidebar() {
  const { conversations, activeId, fetchList, remove, setActive } = useConversationStore()

  const handleDeleteClick = async (e: React.MouseEvent, id: string) => {
    e.preventDefault()
    const client = createApiClient('')
    try {
      await remove(client, id)
    } catch (err) {
      console.error('Failed to delete conversation:', err)
    }
  }

  return (
    <div className="w-64 bg-card border-r border-border flex flex-col h-screen">
      <div className="p-4 border-b border-border">
        <Button className="w-full" size="sm">
          <Plus className="size-4" data-icon="inline-start" />
          新建对话
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <div className="p-2 space-y-1">
          {conversations.map((convo) => (
            <Link
              key={convo.id}
              href={`/conversations/${convo.id}`}
              onClick={() => setActive(convo.id)}
              className={`block p-3 rounded-lg text-sm transition-colors truncate ${ activeId === convo.id ? 'bg-primary/10 text-primary' : 'hover:bg-accent/30 text-muted-foreground'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex-1 truncate">{convo.title || '新对话'}</span>
                <button
                  onClick={(e) => handleDeleteClick(e, convo.id)}
                  className="opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Trash2 className="size-3" />
                </button>
              </div>
            </Link>
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}
```

- [ ] **Step 2: Create AppShell component**

Create `packages/web/components/layout/AppShell.tsx`:
```tsx
'use client'

import { ReactNode } from 'react'
import { Sidebar } from './Sidebar'

interface AppShellProps {
  children: ReactNode
}

export function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex h-screen bg-background text-foreground">
      <Sidebar />
      <main className="flex-1 flex flex-col overflow-hidden">{children}</main>
    </div>
  )
}
```

- [ ] **Step 3: Create chat page**

Create `packages/web/app/conversations/[id]/page.tsx`:
```tsx
'use client'

import { useParams } from 'next/navigation'
import { useEffect } from 'react'
import { useConversationStore, createApiClient } from '@cubeplex/core'
import { AppShell } from '@/components/layout/AppShell'
import { MessageList } from '@/components/chat/MessageList'
import { InputBar } from '@/components/layout/InputBar'

export default function ChatPage() {
  const params = useParams()
  const conversationId = params.id as string
  const { setActive, fetchList } = useConversationStore()

  useEffect(() => {
    setActive(conversationId)
    const client = createApiClient('')
    fetchList(client)
  }, [conversationId, setActive, fetchList])

  return (
    <AppShell>
      <MessageList conversationId={conversationId} />
      <div className="border-t border-border p-4 bg-background">
        <InputBar conversationId={conversationId} />
      </div>
    </AppShell>
  )
}
```

- [ ] **Step 4: Commit layout components**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/components/layout/ packages/web/app/conversations/
git commit -m "feat: add app shell, sidebar, and chat page layout"
```

---

### Task 9: Message Display Components

**Files:**
- Create: `packages/web/components/chat/MessageList.tsx`
- Create: `packages/web/components/chat/UserMessage.tsx`
- Create: `packages/web/components/chat/AssistantMessage.tsx`
- Create: `packages/web/components/chat/ExecutionDetails.tsx`
- Create: `packages/web/hooks/useMessages.ts`

- [ ] **Step 1: Create message hooks**

Create `packages/web/hooks/useMessages.ts`:
```ts
'use client'

import { useMessageStore } from '@cubeplex/core'

export function useMessages(conversationId: string) {
  const messages = useMessageStore((s) => s.messages[conversationId] ?? [])
  const streamingEvents = useMessageStore((s) => s.streamingEvents)
  const isStreaming = useMessageStore((s) => s.isStreaming)

  return { messages, streamingEvents, isStreaming }
}
```

- [ ] **Step 2: Create ExecutionDetails component**

Create `packages/web/components/chat/ExecutionDetails.tsx`:
```tsx
'use client'

import { useState } from 'react'
import type { AgentEvent } from '@cubeplex/core'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { CheckCircle2, AlertCircle, Loader2 } from 'lucide-react'

interface ExecutionDetailsProps {
  events: AgentEvent[]
  isStreaming?: boolean
}

function getEventDisplay(event: AgentEvent) {
  switch (event.type) {
    case 'chain_start':
      return { icon: '🚀', label: '开始执行' }
    case 'llm_start':
      return { icon: '🧠', label: '思考中...' }
    case 'llm_end':
      return { icon: '✓', label: '生成完成' }
    case 'tool_start':
      return {
        icon: '⚙',
        label: `${event.data?.tool_name || '工具'} · 输入: ${JSON.stringify(event.data?.input).slice(0, 50)}...`,
      }
    case 'tool_end':
      return {
        icon: '✓',
        label: `结果: ${JSON.stringify(event.data?.output).slice(0, 50)}...`,
      }
    case 'chain_end':
      return { icon: '✓', label: '完成' }
    case 'error':
      return {
        icon: '✗',
        label: `错误: ${event.data?.message || 'Unknown error'}`,
      }
    default:
      return { icon: '•', label: event.type }
  }
}

function summarizeEvents(events: AgentEvent[]): string {
  const toolCount = events.filter((e) => e.type === 'tool_start').length
  const duration = events.length > 0
    ? new Date(events[events.length - 1].timestamp).getTime() - new Date(events[0].timestamp).getTime()
    : 0
  return `已完成 · ${toolCount} 个工具调用 · ${(duration / 1000).toFixed(1)}s`
}

export function ExecutionDetails({ events, isStreaming = false }: ExecutionDetailsProps) {
  const [isOpen, setIsOpen] = useState(isStreaming)
  const displayEvents = events.filter((e) => e.type !== 'done')

  if (displayEvents.length === 0) return null

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <CollapsibleTrigger className="text-xs text-muted-foreground hover:text-foreground transition-colors">
        {isOpen ? '▼' : '▶'} {summarizeEvents(displayEvents)}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2 text-xs">
        {displayEvents.map((event, idx) => {
          const { icon, label } = getEventDisplay(event)
          return (
            <div key={idx} className="flex items-center gap-2">
              <span>{icon}</span>
              <span className="text-muted-foreground">{label}</span>
            </div>
          )
        })}
      </CollapsibleContent>
    </Collapsible>
  )
}
```

- [ ] **Step 3: Create UserMessage component**

Create `packages/web/components/chat/UserMessage.tsx`:
```tsx
interface UserMessageProps {
  content: string
}

export function UserMessage({ content }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <div className="bg-primary/10 border border-primary/30 text-foreground rounded-lg px-4 py-2 max-w-xs">
        {content}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Create AssistantMessage component**

Create `packages/web/components/chat/AssistantMessage.tsx`:
```tsx
'use client'

import type { Message, AgentEvent } from '@cubeplex/core'
import { ExecutionDetails } from './ExecutionDetails'

function extractFinalText(events: AgentEvent[] | null): string {
  if (!events) return ''
  const lastLlmEnd = [...(events || [])].reverse().find((e) => e.type === 'llm_end')
  return lastLlmEnd?.data?.output ?? ''
}

interface AssistantMessageProps {
  message?: Message
  streamingEvents?: AgentEvent[]
  isStreaming?: boolean
}

export function AssistantMessage({
  message,
  streamingEvents = [],
  isStreaming = false,
}: AssistantMessageProps) {
  const events = message?.events ?? streamingEvents
  const finalText = extractFinalText(events)

  return (
    <div className="flex justify-start">
      <div className="bg-card border border-border rounded-lg px-4 py-2 max-w-md space-y-2">
        {events && <ExecutionDetails events={events} isStreaming={isStreaming} />}
        {finalText && <div className="text-foreground whitespace-pre-wrap">{finalText}</div>}
        {isStreaming && !finalText && <div className="text-muted-foreground text-sm animate-pulse">生成中...</div>}
      </div>
    </div>
  )
}
```

- [ ] **Step 5: Create MessageList component**

Create `packages/web/components/chat/MessageList.tsx`:
```tsx
'use client'

import { useEffect } from 'react'
import { useMessageStore, useConversationStore, createApiClient } from '@cubeplex/core'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useMessages } from '@/hooks/useMessages'

interface MessageListProps {
  conversationId: string
}

export function MessageList({ conversationId }: MessageListProps) {
  const { messages, streamingEvents, isStreaming } = useMessages(conversationId)
  const { fetchHistory } = useMessageStore()

  useEffect(() => {
    const client = createApiClient('')
    fetchHistory(client, conversationId)
  }, [conversationId, fetchHistory])

  return (
    <ScrollArea className="flex-1 p-4">
      <div className="space-y-4 max-w-2xl mx-auto">
        {messages.map((msg) => (
          <div key={msg.id}>
            {msg.role === 'user' && <UserMessage content={msg.content ?? ''} />}
            {msg.role === 'assistant' && <AssistantMessage message={msg} />}
          </div>
        ))}
        {isStreaming && (
          <AssistantMessage streamingEvents={streamingEvents} isStreaming={true} />
        )}
      </div>
    </ScrollArea>
  )
}
```

- [ ] **Step 6: Commit message components**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/components/chat/ packages/web/hooks/
git commit -m "feat: add message display components with execution details"
```

---

### Task 10: Message Sending Integration

**Files:**
- Modify: `packages/web/app/conversations/[id]/page.tsx`
- Modify: `packages/web/components/layout/InputBar.tsx`
- Create: `packages/web/hooks/useConversations.ts`

- [ ] **Step 1: Create conversation hooks**

Create `packages/web/hooks/useConversations.ts`:
```ts
'use client'

import { useConversationStore } from '@cubeplex/core'

export function useConversations() {
  const conversations = useConversationStore((s) => s.conversations)
  const activeId = useConversationStore((s) => s.activeId)
  const fetchList = useConversationStore((s) => s.fetchList)

  return { conversations, activeId, fetchList }
}
```

- [ ] **Step 2: Update InputBar with send functionality**

Update `packages/web/components/layout/InputBar.tsx`:
```tsx
'use client'

import { useState } from 'react'
import { useMessageStore, createApiClient } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ArrowUp, Loader2 } from 'lucide-react'

interface InputBarProps {
  conversationId?: string
  onSubmit?: (content: string) => void
  isLoading?: boolean
}

export function InputBar({ conversationId, onSubmit, isLoading = false }: InputBarProps) {
  const [content, setContent] = useState('')
  const { sendMessage } = useMessageStore()
  const messageIsStreaming = useMessageStore((s) => s.isStreaming)

  const handleSubmit = async () => {
    if (!content.trim()) return
    if (!conversationId) {
      onSubmit?.(content)
      return
    }

    const client = createApiClient('')
    try {
      await sendMessage(client, conversationId, content)
      setContent('')
    } catch (err) {
      console.error('Failed to send message:', err)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      handleSubmit()
    }
  }

  const isSubmitting = isLoading || messageIsStreaming

  return (
    <div className="w-full max-w-2xl mx-auto px-4">
      <div className="bg-card border border-border rounded-lg p-4 space-y-3">
        <Textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="有什么可以帮你的？"
          className="resize-none min-h-24"
          disabled={isSubmitting}
        />
        <div className="flex justify-end">
          <Button
            onClick={handleSubmit}
            disabled={!content.trim() || isSubmitting}
            size="sm"
            data-icon="inline-end"
          >
            {isSubmitting ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
          </Button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Update chat page to pass conversationId**

Update `packages/web/app/conversations/[id]/page.tsx`:
```tsx
'use client'

import { useParams } from 'next/navigation'
import { useEffect } from 'react'
import { useConversationStore, createApiClient } from '@cubeplex/core'
import { AppShell } from '@/components/layout/AppShell'
import { MessageList } from '@/components/chat/MessageList'
import { InputBar } from '@/components/layout/InputBar'

export default function ChatPage() {
  const params = useParams()
  const conversationId = params.id as string
  const { setActive, fetchList } = useConversationStore()

  useEffect(() => {
    setActive(conversationId)
    const client = createApiClient('')
    fetchList(client)
  }, [conversationId, setActive, fetchList])

  return (
    <AppShell>
      <MessageList conversationId={conversationId} />
      <div className="border-t border-border p-4 bg-background">
        <InputBar conversationId={conversationId} />
      </div>
    </AppShell>
  )
}
```

- [ ] **Step 4: Commit message sending**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/hooks/ packages/web/components/ packages/web/app/
git commit -m "feat: integrate message sending with SSE streaming"
```

---

### Task 11: Theme Toggle Component

**Files:**
- Modify: `packages/web/app/layout.tsx`

- [ ] **Step 1: Create ThemeToggle component**

Create `packages/web/components/ui/theme-toggle.tsx`:
```tsx
'use client'

import { useTheme } from 'next-themes'
import { Button } from './button'
import { Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  if (!mounted) return null

  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
    >
      {theme === 'dark' ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  )
}
```

- [ ] **Step 2: Update Sidebar with theme toggle**

Update `packages/web/components/layout/Sidebar.tsx` to include theme toggle at the bottom:
```tsx
'use client'

import { useConversationStore } from '@cubeplex/core'
import { createApiClient } from '@cubeplex/core'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { ThemeToggle } from '@/components/ui/theme-toggle'
import Link from 'next/link'
import { Plus, Trash2 } from 'lucide-react'

export function Sidebar() {
  const { conversations, activeId, fetchList, remove, setActive } = useConversationStore()

  const handleDeleteClick = async (e: React.MouseEvent, id: string) => {
    e.preventDefault()
    const client = createApiClient('')
    try {
      await remove(client, id)
    } catch (err) {
      console.error('Failed to delete conversation:', err)
    }
  }

  return (
    <div className="w-64 bg-card border-r border-border flex flex-col h-screen">
      <div className="p-4 border-b border-border">
        <Button className="w-full" size="sm">
          <Plus className="size-4" data-icon="inline-start" />
          新建对话
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <div className="p-2 space-y-1">
          {conversations.map((convo) => (
            <Link
              key={convo.id}
              href={`/conversations/${convo.id}`}
              onClick={() => setActive(convo.id)}
              className={`block p-3 rounded-lg text-sm transition-colors truncate ${activeId === convo.id ? 'bg-primary/10 text-primary' : 'hover:bg-accent/30 text-muted-foreground'}`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex-1 truncate">{convo.title || '新对话'}</span>
                <button
                  onClick={(e) => handleDeleteClick(e, convo.id)}
                  className="opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <Trash2 className="size-3" />
                </button>
              </div>
            </Link>
          ))}
        </div>
      </ScrollArea>
      <div className="border-t border-border p-4 mt-auto">
        <ThemeToggle />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit theme toggle**

```bash
cd /home/chris/cubeplex/frontend
git add packages/web/components/ui/theme-toggle.tsx packages/web/components/layout/Sidebar.tsx
git commit -m "feat: add theme toggle button"
```

---

### Task 12: Testing & Verification

**Files:**
- Test all components manually

- [ ] **Step 1: Install dependencies**

```bash
cd /home/chris/cubeplex/frontend
pnpm install
```

- [ ] **Step 2: Build core package**

```bash
cd /home/chris/cubeplex/frontend/packages/core
pnpm build
```

- [ ] **Step 3: Start development server**

```bash
cd /home/chris/cubeplex/frontend/packages/web
CUBEPLEX_API_URL=http://localhost:8000 pnpm dev
```

Expected: Next.js server starts on http://localhost:3000

- [ ] **Step 4: Verify welcome page**

Open http://localhost:3000, verify:
- Centered title "cubeplex"
- Centered input area with send button
- Dark theme applied (deep blue background)
- Theme toggle button visible in sidebar (after creating a conversation)

- [ ] **Step 5: Test conversation creation**

Enter "test message" and submit, verify:
- Navigates to `/conversations/[id]`
- Sidebar visible with conversation list
- Message list area displays the sent user message
- Input bar at bottom is ready for next message

- [ ] **Step 6: Test theme toggle**

Click theme toggle button, verify:
- Light theme applies (white background, dark text)
- Page remains functional
- Toggling back restores dark theme

- [ ] **Step 7: Commit final build**

```bash
cd /home/chris/cubeplex/frontend
git add -A
git commit -m "feat: complete frontend MVP implementation"
```

---

## Execution Notes

- **Each task** should be completed in order; some depend on previous tasks.
- **Frequent commits** keep changes small and reviewable.
- **Tests run locally** during development; no automated test suite in MVP.
- **Environment variable:** Set `CUBEPLEX_API_URL=http://localhost:8000` for local development.
- **Package manager:** Always use `pnpm` commands for workspace consistency.
